#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import copernicusmarine
import numpy as np
from shapely import contains_xy

from water_pollution_common import (
    antimeridian_safe_parts,
    load_water_region_geometries,
    load_water_region_metadata,
    metric_payload,
    percentile_rank,
    update_index,
    utc_now_iso,
    weighted_quantile,
    write_region_payload,
)

DATASETS = {
    "plankton": {
        "dataset_id": "cmems_obs-oc_glo_bgc-plankton_my_l4-multi-4km_P1M",
        "metrics": {
            "chlorophyll_a": {
                "variable": "CHL", "label": "Chlorophyll-a", "unit": "mg/m³", "stressDirection": "higher",
                "description": "Surface chlorophyll-a concentration, an indicator of phytoplankton biomass and eutrophication pressure. Elevated chlorophyll can be natural as well as nutrient-driven.",
            }
        },
    },
    "transparency": {
        "dataset_id": "cmems_obs-oc_glo_bgc-transp_my_l4-multi-4km_P1M",
        "metrics": {
            "suspended_particulate_matter": {
                "variable": "SPM", "label": "Suspended particulate matter", "unit": "g/m³", "stressDirection": "higher",
                "description": "Inorganic suspended particulate matter at the sea surface. High values can reflect river sediment, resuspension, erosion, dredging or other particulate inputs.",
            },
            "kd490": {
                "variable": "KD490", "label": "Light attenuation (KD490)", "unit": "1/m", "stressDirection": "higher",
                "description": "Diffuse attenuation at 490 nm. Higher values indicate faster light loss and lower optical clarity.",
            },
            "secchi_depth": {
                "variable": "ZSD", "label": "Secchi-depth clarity", "unit": "m", "stressDirection": "lower",
                "description": "Satellite-estimated Secchi depth. Lower values indicate less transparent water.",
            },
        },
    },
    "optics": {
        "dataset_id": "cmems_obs-oc_glo_bgc-optics_my_l4-multi-4km_P1M",
        "metrics": {
            "cdm": {
                "variable": "CDM", "label": "Dissolved/detrital absorption (CDM)", "unit": "1/m", "stressDirection": "higher",
                "description": "Absorption by coloured dissolved organic matter and non-algal particles; useful for river/runoff and organic-material context but not itself a contaminant concentration.",
            }
        },
    },
}


def parse_args():
    p = argparse.ArgumentParser(description="Build long-term MotherWorld marine water-quality indicators from Copernicus Marine monthly GlobColour data.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--start-year", type=int, default=1998)
    p.add_argument("--end-year", type=int, default=date.today().year - 1)
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--dataset", action="append", choices=tuple(DATASETS), default=[])
    p.add_argument("--pad-deg", type=float, default=0.08)
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def coord_names(ds):
    lat_name = next((n for n in ("latitude", "lat") if n in ds.coords), None)
    lon_name = next((n for n in ("longitude", "lon") if n in ds.coords), None)
    time_name = next((n for n in ("time", "TIME") if n in ds.coords), None)
    if not lat_name or not lon_name or not time_name:
        raise RuntimeError(f"Could not detect coordinates: {list(ds.coords)}")
    return lat_name, lon_name, time_name


def area_weights(lat_values, lon_values, geom):
    lat = np.asarray(lat_values, dtype=np.float64)
    lon = np.asarray(lon_values, dtype=np.float64)
    xx, yy = np.meshgrid(lon, lat)
    inside = contains_xy(geom, xx, yy)
    # For a regular lon/lat grid, cell area is proportional to cos(latitude).
    weights = np.cos(np.deg2rad(yy)) * inside.astype(np.float64)
    return weights


def reduce_annual_da(da, geom, lat_name, lon_name, time_name, stress_direction):
    annual = da.groupby(f"{time_name}.year").mean(time_name, skipna=True)
    years = np.asarray(annual["year"].values, dtype=int)
    lat = np.asarray(annual[lat_name].values)
    lon = np.asarray(annual[lon_name].values)
    weights2d = area_weights(lat, lon, geom)
    if not np.any(weights2d > 0):
        return []
    out = []
    for i, year in enumerate(years):
        values = np.asarray(annual.isel(year=i).values, dtype=np.float64)
        while values.ndim > 2:
            values = values[0]
        mask = np.isfinite(values) & (weights2d > 0)
        if not np.any(mask):
            continue
        vals = values[mask]
        w = weights2d[mask]
        mean = float(np.average(vals, weights=w))
        q = 0.90 if stress_direction == "higher" else 0.10
        stress = weighted_quantile(vals, w, q)
        out.append({
            "year": int(year),
            "mean": mean,
            "stressPercentile": stress,
            "weightSum": float(np.sum(w)),
            "validCellCount": int(mask.sum()),
        })
    return out


def merge_part_annual(part_rows: list[list[dict]], stress_direction: str):
    # Multipart region means are combined with the same cosine-latitude area weights used inside each part.
    # The stress percentile is intentionally conservative across disjoint parts: worst part p90 (or p10 for clarity).
    by_year = {}
    for rows in part_rows:
        for row in rows:
            by_year.setdefault(row["year"], []).append(row)
    merged = []
    for year, rows in sorted(by_year.items()):
        valid = [r for r in rows if r.get("mean") is not None and float(r.get("weightSum") or 0) > 0]
        highs = [r["stressPercentile"] for r in rows if r.get("stressPercentile") is not None]
        if not valid:
            continue
        weights = np.asarray([float(r["weightSum"]) for r in valid], dtype=np.float64)
        means = np.asarray([float(r["mean"]) for r in valid], dtype=np.float64)
        merged.append({
            "year": year,
            "mean": float(np.average(means, weights=weights)),
            "stressPercentile": float(np.max(highs) if stress_direction == "higher" else np.min(highs)) if highs else None,
            "weightSum": float(weights.sum()),
            "validCellCount": int(sum(int(r.get("validCellCount") or 0) for r in valid)),
        })
    return merged


def process_region(repo, rid, geom, meta, args, selected_datasets):
    blocks = {}
    parts = antimeridian_safe_parts(geom)
    if not parts:
        return None
    start = f"{args.start_year}-01-01"
    end = f"{args.end_year}-12-31"

    for dataset_key in selected_datasets:
        spec = DATASETS[dataset_key]
        variables = [m["variable"] for m in spec["metrics"].values()]
        part_metric_rows = {key: [] for key in spec["metrics"]}
        for part_idx, part in enumerate(parts, 1):
            minx, miny, maxx, maxy = part.bounds
            print(f"    {dataset_key}: part {part_idx}/{len(parts)} bbox=({minx:.2f},{miny:.2f},{maxx:.2f},{maxy:.2f})")
            ds = copernicusmarine.open_dataset(
                dataset_id=spec["dataset_id"],
                variables=variables,
                minimum_longitude=max(-180.0, minx - args.pad_deg),
                maximum_longitude=min(180.0, maxx + args.pad_deg),
                minimum_latitude=max(-90.0, miny - args.pad_deg),
                maximum_latitude=min(90.0, maxy + args.pad_deg),
                start_datetime=start,
                end_datetime=end,
                coordinates_selection_method="outside",
            )
            try:
                lat_name, lon_name, time_name = coord_names(ds)
                for metric_key, metric in spec["metrics"].items():
                    da = ds[metric["variable"]]
                    while da.ndim > 3:
                        extra = next(d for d in da.dims if d not in (time_name, lat_name, lon_name))
                        da = da.isel({extra: 0})
                    da = da.transpose(time_name, lat_name, lon_name)
                    rows = reduce_annual_da(da, part, lat_name, lon_name, time_name, metric["stressDirection"])
                    part_metric_rows[metric_key].append(rows)
            finally:
                try:
                    ds.close()
                except Exception:
                    pass

        for metric_key, metric in spec["metrics"].items():
            annual = merge_part_annual(part_metric_rows[metric_key], metric["stressDirection"])
            block = metric_payload(
                key=metric_key,
                label=metric["label"],
                unit=metric["unit"],
                description=metric["description"],
                stress_direction=metric["stressDirection"],
                source=f"Copernicus Marine {spec['dataset_id']}",
                annual=annual,
                high_field="stressPercentile",
                caveat="Satellite ocean-colour indicators describe surface optical/ecological conditions; they do not directly quantify pathogens, plastics, hydrocarbons, heavy metals or toxicity.",
            )
            if block:
                block["stressPercentileType"] = "spatial p90 (or p10 for clarity) of annual-mean surface field; multipart regions retain the conservative worst-part percentile"
                blocks[metric_key] = block

    if not blocks:
        return None
    return {
        "schemaVersion": 1,
        "regionId": rid,
        "name": meta.get("name") or rid,
        "regionKind": "marine",
        "period": {"requestedStartYear": args.start_year, "requestedEndYear": args.end_year},
        "metrics": blocks,
        "method": {
            "provider": "Copernicus Marine Toolbox",
            "aggregation": "monthly 4 km ocean-colour fields aggregated to annual polygon means; annual spatial stress percentile retained",
            "interpretation": "Indicators stay in their native physical units. No synthetic universal water-pollution score is calculated.",
        },
        "generatedAt": utc_now_iso(),
    }


def main():
    args = parse_args()
    repo = args.repo.resolve()
    geoms_all = load_water_region_geometries(repo)
    geoms = {rid: geom for rid, (kind, geom) in geoms_all.items() if kind == "marine"}
    meta = load_water_region_metadata(repo)
    region_ids = sorted(geoms)
    if args.region:
        wanted = set(args.region)
        region_ids = [rid for rid in region_ids if rid in wanted]
    selected = args.dataset or list(DATASETS)
    entries = {}
    latest_maps = {}

    for idx, rid in enumerate(region_ids, 1):
        target = repo / "frontend/public/data/water-pollution/marine" / f"{rid}.water-pollution.json"
        if target.exists() and not args.force:
            print(f"[{idx}/{len(region_ids)}] {rid}: exists, skipping")
            continue
        print(f"[{idx}/{len(region_ids)}] {rid} {meta.get(rid,{}).get('name','')}")
        payload = process_region(repo, rid, geoms[rid], meta.get(rid, {}), args, selected)
        if not payload:
            continue
        rel = write_region_payload(repo, "marine", rid, payload)
        entries[rid] = {"url": rel, "kind": "marine", "metrics": list(payload["metrics"]), "generatedAt": payload["generatedAt"]}
        for key, block in payload["metrics"].items():
            latest_maps.setdefault(key, {})[rid] = block["latest"]["mean"]

    # Percentile ranks are only meaningful across regions generated in the same run; add where possible.
    for rid in entries:
        path = repo / "frontend/public/data/water-pollution" / entries[rid]["url"]
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, block in payload["metrics"].items():
            block["regionalStressPercentile"] = percentile_rank(
                latest_maps.get(key, {}), block["latest"]["mean"], higher_is_worse=block["stressDirection"] == "higher"
            )
        path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    update_index(
        repo,
        entries,
        sources={
            "copernicus-marine-oceancolour-my": {
                "label": "Copernicus Marine GlobColour L4 multi-year monthly ocean colour",
                "product": "OCEANCOLOUR_GLO_BGC_L4_MY_009_104",
                "doi": "10.48670/moi-00279",
                "resolution": "4 km",
                "coverage": "1997-ongoing",
            }
        },
    )
    print(f"Done. Wrote/updated {len(entries)} marine payloads.")


if __name__ == "__main__":
    main()
