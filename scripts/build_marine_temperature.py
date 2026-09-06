#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import requests
import xarray as xr

from temperature_common import (
    accumulate_window,
    daily_offsets,
    grid_cell_area_rows,
    processing_fingerprint,
    regular_step,
    temperature_to_c,
    antimeridian_safe_parts,
    canonicalize_grid,
    detect_temperature_variable,
    detect_time_name,
    fractional_weight_window,
    load_index,
    load_marine_geometries,
    make_bin_edges,
    parse_year_range,
    scalar_stats,
    to_percent,
    update_climate_index,
    utc_now_iso,
    weighted_stats_from_hist,
    write_region_payload,
)

PSL_BASE = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build marine empirical SST distributions from NOAA OISST v2.1 daily data.")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--years", default="1991:2020")
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--supersample", type=int, default=4)
    p.add_argument("--bin-min", type=float, default=-5.0)
    p.add_argument("--bin-max", type=float, default=45.0)
    p.add_argument("--bin-width", type=float, default=0.25)
    p.add_argument("--chunk-days", type=int, default=7)
    p.add_argument("--cache-dir", type=Path, default=Path(".cache/motherworld/climate/raw/oisst"))
    p.add_argument("--keep-raw", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-open-ocean", action="store_true")
    return p.parse_args()


def download_year(year: int, cache_dir: Path) -> Path:
    path = cache_dir / f"sst.day.mean.{year}.nc"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    url = f"{PSL_BASE}/sst.day.mean.{year}.nc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    for attempt in range(4):
        offset = partial.stat().st_size if partial.exists() else 0
        print(f"Downloading NOAA {year}: resume at {offset / 1048576:.1f} MiB", flush=True)
        try:
            with requests.get(url, headers={"Range": f"bytes={offset}-"} if offset else {}, stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                append = response.status_code == 206 and offset > 0
                if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("NOAA returned an unexpected partial response")
                expected = int(response.headers.get("Content-Length", 0)) + (offset if append else 0)
                with partial.open("ab" if append else "wb") as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk: stream.write(chunk)
                if expected and partial.stat().st_size != expected:
                    raise IOError("Incomplete NOAA download")
            with xr.open_dataset(partial) as dataset:
                detect_temperature_variable(dataset, "oisst")
            partial.replace(path)
            return path
        except (requests.RequestException, OSError):
            if attempt == 3: raise
            time.sleep(2 ** attempt)
    raise RuntimeError("NOAA download failed")


def build_weight_table(geoms: dict[str, object], lat: np.ndarray, lon: np.ndarray, supersample: int, include_open_ocean: bool):
    nlat, nlon = len(lat), len(lon)
    cell_area = grid_cell_area_rows(lat, regular_step(lon), regular_step(lat))[:, None]
    table: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    covered_fraction = np.zeros((nlat, nlon), dtype=np.float32) if include_open_ocean else None
    for i, (rid, geom) in enumerate(geoms.items(), 1):
        merged: dict[int, float] = {}
        for part in antimeridian_safe_parts(geom):
            ww = fractional_weight_window(part, lat, lon, supersample)
            if ww is None:
                continue
            rr, cc = np.nonzero(ww.weights > 0)
            idx = (rr + ww.row0) * nlon + (cc + ww.col0)
            weights = ww.weights[rr, cc].astype(np.float64)
            for cell, weight in zip(idx.tolist(), weights.tolist()):
                merged[cell] = merged.get(cell, 0.0) + weight
            if covered_fraction is not None:
                area_rows = grid_cell_area_rows(lat[ww.row0:ww.row1], regular_step(lon), regular_step(lat))
                frac = np.divide(ww.weights, area_rows[:, None], out=np.zeros_like(ww.weights), where=area_rows[:, None] > 0)
                covered_fraction[ww.row0:ww.row1, ww.col0:ww.col1] += frac.astype(np.float32)
        if merged:
            cells = np.asarray(sorted(merged), dtype=np.int64)
            weights = np.asarray([merged[int(cell)] for cell in cells], dtype=np.float64)
            weights = np.minimum(weights, cell_area[cells // nlon, 0])
            table[rid] = (cells, weights)
        if i % 25 == 0:
            print(f"  weighted {i}/{len(geoms)} marine regions")
    if covered_fraction is not None:
        covered_fraction = np.clip(covered_fraction, 0.0, 1.0)
        residual = 1.0 - covered_fraction
        cell_area = grid_cell_area_rows(lat, regular_step(lon), regular_step(lat))[:, None]
        open_weights = residual * cell_area
        rr, cc = np.nonzero(open_weights > 0)
        idx = rr * nlon + cc
        table["open_ocean"] = (idx.astype(np.int64), open_weights[rr, cc].astype(np.float64))
    return table


def selected_geometries(geoms, selected, include_open_ocean):
    # Open ocean is the residual of ALL mapped regions, even when it is the only output.
    if include_open_ocean or not selected: return geoms
    return {rid: geom for rid, geom in geoms.items() if rid in selected}


def save_checkpoint(path, **arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".npz.tmp")
    with temporary.open("wb") as stream: np.savez_compressed(stream, **arrays)
    temporary.replace(path)


def process_year(path, year, table, lat, lon, edges, chunk_days):
    ids = list(table)
    hist = np.zeros((len(ids), len(edges) - 1))
    moments = np.zeros((len(ids), 3))  # sum, squared sum, valid area-days
    with xr.open_dataset(path) as original:
        ds, lat_name, lon_name = canonicalize_grid(original)
        if not np.array_equal(ds[lat_name].values, lat) or not np.array_equal(ds[lon_name].values, lon):
            raise ValueError("NOAA grid changed between years")
        da = ds[detect_temperature_variable(ds, "oisst")]
        time_name = detect_time_name(da)
        for dim in list(da.dims):
            if dim not in (time_name, lat_name, lon_name):
                if da.sizes[dim] != 1: raise ValueError(f"Unexpected temperature dimension: {dim}")
                da = da.isel({dim: 0})
        da = da.transpose(time_name, lat_name, lon_name)
        offsets = daily_offsets(da[time_name].values, year)
        daily = np.full((len(ids), len(offsets)), np.nan)
        for t0 in range(0, len(offsets), chunk_days):
            t1 = min(len(offsets), t0 + chunk_days)
            cube = temperature_to_c(da.isel({time_name: slice(t0, t1)}).values, da.attrs.get("units"))
            flat = cube.reshape(cube.shape[0], -1)
            for i, rid in enumerate(ids):
                cells, weights = table[rid]
                num, den, bins, weighted_sum, weighted_sq_sum = accumulate_window(flat[:, cells], weights, edges)
                daily[i, t0:t1] = np.divide(num, den, out=np.full(len(num), np.nan), where=den > 0)
                hist[i] += bins
                moments[i] += [weighted_sum, weighted_sq_sum, float(den.sum())]
            print(f"  {year}: days {t0 + 1}-{t1}/{len(offsets)}", flush=True)
    return {"hist": hist, "moments": moments, "daily": daily}


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    start_year, end_year = parse_year_range(args.years)
    if start_year < 1982: raise ValueError("Complete OISST calendar years start in 1982")
    if args.chunk_days < 1 or args.supersample < 1: raise ValueError("Chunk days and supersample must be positive")
    edges = make_bin_edges(args.bin_min, args.bin_max, args.bin_width)
    marine_index = load_index(repo / "frontend/public/data/marine.index.json")
    all_geoms = load_marine_geometries(repo)
    selected = set(args.region)
    unknown = selected - (set(all_geoms) | {"open_ocean"})
    if unknown: raise ValueError(f"Unknown marine region IDs: {sorted(unknown)}")
    if args.no_open_ocean and "open_ocean" in selected: raise ValueError("Conflicting open ocean options")
    include_open_ocean = not args.no_open_ocean and (not selected or "open_ocean" in selected)
    geoms = selected_geometries(all_geoms, selected, include_open_ocean)
    cache_root = (repo / args.cache_dir).resolve()
    options = {"version": 3, "source": "noaa-oisst-v2.1", "bins": edges.tolist(), "supersample": args.supersample,
               "regions": sorted(selected), "openOcean": include_open_ocean}
    cache_key = processing_fingerprint(options, geoms)
    signature = processing_fingerprint({**options, "years": [start_year, end_year]}, geoms)
    processed_root = cache_root.parent.parent / "processed-marine" / cache_key
    weight_file = processed_root / "weights.npz"
    if weight_file.exists() and not args.force:
        with np.load(weight_file, allow_pickle=False) as state:
            lat, lon = state["lat"], state["lon"]
            table = {str(rid): (state[f"cells_{i}"], state[f"weights_{i}"]) for i, rid in enumerate(state["ids"])}
    else:
        first_file = download_year(start_year, cache_root)
        with xr.open_dataset(first_file) as ds0:
            grid, lat_name, lon_name = canonicalize_grid(ds0)
            lat, lon = np.asarray(grid[lat_name].values), np.asarray(grid[lon_name].values)
        print("Building marine fractional grid weights...", flush=True)
        table = build_weight_table(geoms, lat, lon, args.supersample, include_open_ocean)
        if selected: table = {rid: pair for rid, pair in table.items() if rid in selected}
        arrays = {"lat": lat, "lon": lon, "ids": np.asarray(list(table))}
        for i, (cells, weights) in enumerate(table.values()):
            arrays[f"cells_{i}"] = cells; arrays[f"weights_{i}"] = weights
        save_checkpoint(weight_file, **arrays)
    region_ids = list(table)
    if not region_ids: raise ValueError("No marine grid cells covered by the selected regions")
    hist = {rid: np.zeros(len(edges) - 1) for rid in region_ids}
    weighted_sum = {rid: 0.0 for rid in region_ids}
    weighted_sq_sum = {rid: 0.0 for rid in region_ids}
    valid_area_time = {rid: 0.0 for rid in region_ids}
    daily = {rid: [] for rid in region_ids}
    for year in range(start_year, end_year + 1):
        checkpoint = processed_root / f"{year}.npz"
        if checkpoint.exists() and not args.force:
            with np.load(checkpoint, allow_pickle=False) as state:
                result = {key: state[key] for key in ("hist", "moments", "daily")}
            print(f"  {year}: resumed processed year", flush=True)
        else:
            path = download_year(year, cache_root)
            result = process_year(path, year, table, lat, lon, edges, args.chunk_days)
            save_checkpoint(checkpoint, **result)
            if not args.keep_raw: path.unlink()
        for i, rid in enumerate(region_ids):
            hist[rid] += result["hist"][i]
            weighted_sum[rid] += float(result["moments"][i, 0])
            weighted_sq_sum[rid] += float(result["moments"][i, 1])
            valid_area_time[rid] += float(result["moments"][i, 2])
            values = result["daily"][i]
            daily[rid].extend(values[np.isfinite(values)].tolist())

    entries: dict[str, dict] = {}
    for rid in region_ids:
        if not daily[rid] or not hist[rid].sum():
            print(f"{rid}: no valid sea cells; no inventory published", flush=True)
            continue
        is_open = rid == "open_ocean"
        meta = marine_index.get("regions", {}).get(rid, {})
        temporal_hist = np.histogram(np.asarray(daily[rid]), bins=edges)[0].astype(np.float64)
        rel = write_region_payload(
            repo,
            "marine",
            rid,
            {
                "schemaVersion": 1,
                "queryFingerprint": signature,
                "regionId": rid,
                "regionName": "Open Ocean" if is_open else meta.get("name"),
                "regionKind": "marine",
                "generatedAt": utc_now_iso(),
                "variable": "sea_surface_temperature",
                "unit": "degC",
                "baseline": {"startYear": start_year, "endYear": end_year, "dailyAggregation": "daily_mean"},
                "bins": {"edgesC": edges.tolist(), "widthC": args.bin_width},
                "distributions": {
                    "regionalDailyMean": {
                        "label": "Regional mean over time",
                        "definition": "Area-weighted mean sea-surface temperature is computed for each day; the histogram is the percentage of valid days in each temperature bin.",
                        "percent": to_percent(temporal_hist),
                        "sampleCountDays": len(daily[rid]),
                        "stats": scalar_stats(daily[rid]),
                    },
                    "spaceTime": {
                        "label": "Space × time",
                        "definition": "Every valid OISST grid-cell day contributes according to fractional region coverage and grid-cell surface area.",
                        "percent": to_percent(hist[rid]),
                        "weightedAreaTimeM2Days": valid_area_time[rid],
                        "stats": weighted_stats_from_hist(hist[rid], edges, weighted_sum[rid], weighted_sq_sum[rid]),
                    },
                },
                "quality": {
                    "boundaryWeighting": f"{args.supersample}x{args.supersample} sub-cell fractional rasterization",
                    "weightedGridCellCount": int(len(table[rid][0])),
                    "openOceanDefinition": "Residual grid-cell fraction not covered by mapped marine ecoregions" if is_open else None,
                },
                "source": {
                    "id": "noaa-oisst-v2.1",
                    "label": "NOAA Optimum Interpolation Sea Surface Temperature v2.1",
                    "variable": "sst",
                    "spatialResolution": "0.25°",
                    "temporalResolution": "daily",
                    "datasetStart": "1981-09-01",
                    "licenseNote": "NOAA public data; cite the OISST product and source publications.",
                    "url": "https://www.ncei.noaa.gov/products/optimum-interpolation-sst",
                },
            },
        )
        entries[rid] = {"url": rel, "kind": "marine", "source": "noaa-oisst-v2.1", "generatedAt": utc_now_iso(), "queryFingerprint": signature}

    update_climate_index(
        repo,
        entries,
        {
            "noaa-oisst-v2.1": {
                "label": "NOAA OISST v2.1",
                "url": "https://www.ncei.noaa.gov/products/optimum-interpolation-sst",
            }
        },
    )
    print(f"Done. Generated/registered {len(entries)} marine climate files.")


if __name__ == "__main__":
    main()
