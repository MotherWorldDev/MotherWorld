#!/usr/bin/env python
from __future__ import annotations

import argparse
import calendar
import json
import shutil
from pathlib import Path

import cdsapi
import numpy as np

from temperature_common import (
    antimeridian_safe_parts,
    accumulate_window,
    daily_offsets,
    processing_fingerprint,
    canonicalize_grid,
    detect_temperature_variable,
    detect_time_name,
    fractional_weight_window,
    iter_polygon_parts,
    load_index,
    load_lake_geometries,
    load_land_geometries,
    make_bin_edges,
    open_downloaded_dataset,
    parse_year_range,
    scalar_stats,
    temperature_to_c,
    to_percent,
    update_climate_index,
    utc_now_iso,
    weighted_stats_from_hist,
    write_region_payload,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build land/lake empirical temperature distributions from ERA5/ERA5-Land daily means.")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--years", default="1991:2020")
    p.add_argument("--source", choices=("era5-land", "era5"), default="era5-land")
    p.add_argument("--region", action="append", default=[], help="Region id; repeat to restrict. Defaults to all land + lakes.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--supersample", type=int, default=4)
    p.add_argument("--bin-min", type=float, default=-100.0)
    p.add_argument("--bin-max", type=float, default=65.0)
    p.add_argument("--bin-width", type=float, default=0.5)
    p.add_argument("--cache-dir", type=Path, default=Path(".cache/motherworld/climate/raw/era5"))
    p.add_argument("--chunk-days", type=int, default=31)
    p.add_argument("--keep-raw", action="store_true")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def cds_dataset(source: str) -> tuple[str, dict]:
    if source == "era5-land":
        return "derived-era5-land-daily-statistics", {}
    return "derived-era5-single-levels-daily-statistics", {"product_type": "reanalysis"}


def download_year_bbox(client, source: str, year: int, bbox: tuple[float, float, float, float], target: Path) -> Path:
    if target.exists() and target.stat().st_size > 1024:
        return target
    dataset, extra = cds_dataset(source)
    minx, miny, maxx, maxy = bbox
    request = {
        **extra,
        "variable": ["2m_temperature"],
        "year": str(year),
        "month": [f"{m:02d}" for m in range(1, 13)],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00",
        "frequency": "1_hourly",
        "area": [min(90.0, maxy), max(-180.0, minx), max(-90.0, miny), min(180.0, maxx)],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"CDS {source}: {year} bbox={request['area']} -> {target}")
    partial = target.with_suffix(target.suffix + ".part")
    client.retrieve(dataset, request, str(partial))
    partial.replace(target)
    return target


def choose_request_geometries(geom) -> list:
    parts = antimeridian_safe_parts(geom)
    if not parts:
        return []
    minx, miny, maxx, maxy = geom.bounds
    # A compact multipart region is cheaper as one request. Widely separated islands are split.
    if maxx - minx <= 35 and maxy - miny <= 35:
        return [geom]
    return parts


process_window = accumulate_window


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    start_year, end_year = parse_year_range(args.years)
    edges = make_bin_edges(args.bin_min, args.bin_max, args.bin_width)
    if args.chunk_days < 1 or args.supersample < 1: raise ValueError("Chunk days and supersample must be positive")
    land_index = load_index(repo / "frontend/public/data/regions.index.json")
    lake_index = load_index(repo / "frontend/public/data/lakes.index.json")
    land_geoms = load_land_geometries(repo)
    lake_geoms = load_lake_geometries(repo)

    regions: list[tuple[str, str, dict, object]] = []
    for rid, meta in land_index.get("regions", {}).items():
        if rid in land_geoms:
            regions.append((rid, "land", meta, land_geoms[rid]))
    for rid, meta in lake_index.get("regions", {}).items():
        if rid in lake_geoms:
            regions.append((rid, "lakes", meta, lake_geoms[rid]))
    if args.region:
        selected = set(args.region)
        regions = [r for r in regions if r[0] in selected]
    if args.limit:
        regions = regions[: args.limit]

    cache_root = (repo / args.cache_dir).resolve() if not args.cache_dir.is_absolute() else args.cache_dir
    # Fail before any retrieval if credentials are not configured. Never print them.
    client = cdsapi.Client()
    entries: dict[str, dict] = {}

    for idx, (rid, kind, meta, geom) in enumerate(regions, 1):
        out = repo / "frontend/public/data/climate" / kind / f"{rid}.temperature.json"
        signature = processing_fingerprint({"version": 3, "source": args.source, "years": [start_year, end_year], "bins": edges.tolist(), "supersample": args.supersample}, {rid: geom})
        previous = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        if previous.get("queryFingerprint") == signature and not args.force:
            print(f"[{idx}/{len(regions)}] {rid}: exists, skipping")
            entries[rid] = {"url": f"{kind}/{rid}.temperature.json", "kind": kind, "source": args.source, "generatedAt": previous["generatedAt"], "queryFingerprint": signature}
            update_climate_index(repo, {rid: entries[rid]}, {})
            continue
        print(f"[{idx}/{len(regions)}] {rid} {meta.get('name', '')}")
        request_geoms = choose_request_geometries(geom)
        if not request_geoms:
            print("  no polygon geometry; skipping")
            continue

        daily_means: list[float] = []
        space_hist = np.zeros(len(edges) - 1, dtype=np.float64)
        weighted_sum = 0.0
        weighted_sq_sum = 0.0
        total_valid_area_time = 0.0
        coverage_area_m2 = 0.0
        cell_count = 0

        for year in range(start_year, end_year + 1):
            expected_days = 366 if calendar.isleap(year) else 365
            year_num = np.zeros(expected_days, dtype=np.float64)
            year_den = np.zeros(expected_days, dtype=np.float64)
            checkpoint = cache_root.parent.parent / "processed-land" / rid / signature / f"{year}.npz"
            if checkpoint.exists() and not args.force:
                with np.load(checkpoint, allow_pickle=False) as state:
                    daily_means.extend(state["daily"].tolist())
                    space_hist += state["hist"]
                    weighted_sum += float(state["weighted_sum"])
                    weighted_sq_sum += float(state["weighted_sq_sum"])
                    total_valid_area_time += float(state["valid_area_time"])
                    if year == start_year:
                        coverage_area_m2 += float(state["covered_area"]); cell_count += int(state["cell_count"])
                print(f"  {year}: resumed processed year", flush=True)
                continue
            year_hist = np.zeros(len(edges) - 1)
            year_sum = year_sq_sum = year_area = 0.0
            year_cells = 0

            for part_i, part in enumerate(request_geoms):
                minx, miny, maxx, maxy = part.bounds
                pad = 0.3 if args.source == "era5-land" else 0.6
                bbox = (minx - pad, miny - pad, maxx + pad, maxy + pad)
                raw_key = processing_fingerprint({"source": args.source, "year": year, "bbox": bbox}, {})[:16]
                target = cache_root / args.source / rid / f"{year}.{raw_key}.zip"
                download_year_bbox(client, args.source, year, bbox, target)
                opened_ds, tmp = open_downloaded_dataset(target)
                processed = False
                try:
                    ds, lat_name, lon_name = canonicalize_grid(opened_ds)
                    da = ds[detect_temperature_variable(ds, args.source)]
                    time_name = detect_time_name(da)
                    for dim in list(da.dims):
                        if dim not in (time_name, lat_name, lon_name):
                            if da.sizes[dim] != 1: raise ValueError(f"Unexpected temperature dimension: {dim}")
                            da = da.isel({dim: 0})
                    da = da.transpose(time_name, lat_name, lon_name)
                    offsets = daily_offsets(da[time_name].values, year)
                    ww = fractional_weight_window(part, ds[lat_name].values, ds[lon_name].values, args.supersample)
                    if ww is not None:
                        subset = da.isel({lat_name: slice(ww.row0, ww.row1), lon_name: slice(ww.col0, ww.col1)})
                        for t0 in range(0, len(offsets), args.chunk_days):
                            t1 = min(len(offsets), t0 + args.chunk_days)
                            values = temperature_to_c(subset.isel({time_name: slice(t0, t1)}).values, da.attrs.get("units"))
                            num, den, hist, wsum, wsq = process_window(values, ww.weights, edges)
                            year_num[offsets[t0:t1]] += num
                            year_den[offsets[t0:t1]] += den
                            year_hist += hist; year_sum += wsum; year_sq_sum += wsq
                        year_area += float(ww.weights.sum()); year_cells += int(np.count_nonzero(ww.weights))
                    processed = True
                finally:
                    opened_ds.close()
                    if tmp is not None: tmp.cleanup()
                    if processed and not args.keep_raw: target.unlink(missing_ok=True)

            values = np.divide(year_num, year_den, out=np.full(expected_days, np.nan), where=year_den > 0)
            values = values[np.isfinite(values)]
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            with checkpoint.with_suffix(".npz.tmp").open("wb") as stream:
                np.savez_compressed(stream, daily=values, hist=year_hist, weighted_sum=year_sum, weighted_sq_sum=year_sq_sum,
                                    valid_area_time=float(year_den.sum()), covered_area=year_area, cell_count=year_cells)
            checkpoint.with_suffix(".npz.tmp").replace(checkpoint)
            daily_means.extend(values.tolist()); space_hist += year_hist
            weighted_sum += year_sum; weighted_sq_sum += year_sq_sum; total_valid_area_time += float(year_den.sum())
            if year == start_year: coverage_area_m2 += year_area; cell_count += year_cells
            print(f"  {year}: {len(values)} valid daily means", flush=True)

        if not daily_means or not space_hist.sum():
            print(f"  {rid}: no valid covered climate cells; no inventory published")
            continue
        temporal_hist = np.histogram(np.asarray(daily_means), bins=edges)[0].astype(np.float64)
        rel = write_region_payload(
            repo,
            kind,
            rid,
            {
                "schemaVersion": 1,
                "queryFingerprint": signature,
                "regionId": rid,
                "regionName": meta.get("name"),
                "regionKind": kind,
                "generatedAt": utc_now_iso(),
                "variable": "2m_air_temperature",
                "unit": "degC",
                "baseline": {"startYear": start_year, "endYear": end_year, "dailyAggregation": "daily_mean", "timeZone": "UTC"},
                "bins": {"edgesC": edges.tolist(), "widthC": args.bin_width},
                "distributions": {
                    "regionalDailyMean": {
                        "label": "Regional mean over time",
                        "definition": "Area-weighted regional mean temperature is computed for each day; the histogram is the percentage of valid days in each temperature bin.",
                        "percent": to_percent(temporal_hist),
                        "sampleCountDays": len(daily_means),
                        "stats": scalar_stats(daily_means),
                    },
                    "spaceTime": {
                        "label": "Space × time",
                        "definition": "Every grid-cell day contributes according to the fraction of the cell covered by the region and the cell's surface area.",
                        "percent": to_percent(space_hist),
                        "weightedAreaTimeM2Days": total_valid_area_time,
                        "stats": weighted_stats_from_hist(space_hist, edges, weighted_sum, weighted_sq_sum),
                    },
                },
                "quality": {
                    "boundaryWeighting": f"{args.supersample}x{args.supersample} sub-cell fractional rasterization",
                    "weightedGridCellCount": cell_count,
                    "approxCoveredAreaKm2": coverage_area_m2 / 1_000_000.0,
                },
                "source": {
                    "id": args.source,
                    "label": "ERA5-Land daily statistics" if args.source == "era5-land" else "ERA5 daily statistics",
                    "dataset": "derived-era5-land-daily-statistics" if args.source == "era5-land" else "derived-era5-single-levels-daily-statistics",
                    "variable": "2m_temperature",
                    "spatialResolution": "0.1°" if args.source == "era5-land" else "0.25°",
                    "license": "Copernicus Climate Data Store licence / CC-BY catalogue metadata",
                    "doi": "10.24381/cds.e9c9c792" if args.source == "era5-land" else "10.24381/cds.4991cf48",
                },
            },
        )
        entries[rid] = {"url": rel, "kind": kind, "source": args.source, "queryFingerprint": signature, "generatedAt": utc_now_iso()}
        update_climate_index(
            repo,
            entries,
            {
                args.source: {
                    "label": "ERA5-Land post-processed daily statistics" if args.source == "era5-land" else "ERA5 post-processed daily statistics",
                    "doi": "10.24381/cds.e9c9c792" if args.source == "era5-land" else "10.24381/cds.4991cf48",
                }
            },
        )

    print(f"Done. Generated/registered {len(entries)} land/lake climate files.")


if __name__ == "__main__":
    main()
