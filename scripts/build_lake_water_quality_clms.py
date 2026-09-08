#!/usr/bin/env python
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from shapely.geometry import mapping
from shapely.ops import transform as shapely_transform
from pyproj import Transformer

from water_pollution_common import (
    load_water_region_geometries,
    load_water_region_metadata,
    metric_payload,
    percentile_rank,
    update_index,
    utc_now_iso,
    write_region_payload,
)

METRICS = {
    "turbidity": {
        "tokens": ("TUR", "TURBIDITY"),
        "label": "Turbidity",
        "unit": "NTU",
        "stressDirection": "higher",
        "description": "Copernicus Lake Water Quality turbidity. Higher values indicate hazier water and reduced light penetration.",
    },
    "total_suspended_matter": {
        "tokens": ("TSM", "TOTAL_SUSPENDED_MATTER"),
        "label": "Total suspended matter",
        "unit": "g/m³",
        "stressDirection": "higher",
        "description": "Concentration of suspended particles in the water column.",
    },
    "trophic_state_index": {
        "tokens": ("TSI", "TROPHIC_STATE"),
        "label": "Trophic State Index",
        "unit": "index",
        "stressDirection": "higher",
        "description": "Lake trophic-state indicator; higher values generally represent greater phytoplankton productivity/eutrophication state.",
    },
    "chlorophyll_a": {
        "tokens": ("CHL", "CHLA", "CHLOROPHYLL"),
        "label": "Chlorophyll-a",
        "unit": "g/m³",
        "stressDirection": "higher",
        "description": "Satellite-derived chlorophyll-a concentration, used as a proxy for phytoplankton biomass.",
    },
    "cyanobacteria_risk": {
        "tokens": ("CYAN", "CYANO", "CYANOBACTERIA"),
        "label": "Floating cyanobacteria risk",
        "unit": "index",
        "stressDirection": "higher",
        "description": "Copernicus floating cyanobacteria risk index indicating detection/risk of surface cyanobacterial blooms.",
    },
}

DATE_PATTERNS = [
    re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)(?!\d)"),
]


def parse_args():
    p = argparse.ArgumentParser(description="Aggregate staged Copernicus CLMS Lake Water Quality GeoTIFFs into MotherWorld lake-region payloads.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--input", type=Path, required=True, help="Directory containing CLMS LWQ 300 m GeoTIFF/COG files (recursive).")
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--metric", action="append", choices=tuple(METRICS), default=[])
    p.add_argument("--start-year", type=int, default=None)
    p.add_argument("--end-year", type=int, default=None)
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def detect_metric(path: Path) -> str | None:
    text = path.name.upper().replace("-", "_")
    # Test longer/specific names before CHL to avoid accidental collisions.
    order = ("total_suspended_matter", "trophic_state_index", "cyanobacteria_risk", "turbidity", "chlorophyll_a")
    for key in order:
        for token in METRICS[key]["tokens"]:
            if re.search(rf"(^|[_\.]){re.escape(token)}([_\.]|$)", text):
                return key
    return None


def detect_year(path: Path) -> int | None:
    text = path.as_posix()
    for pattern in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def polygon_in_crs(geom, crs):
    if crs is None or str(crs).upper() in {"EPSG:4326", "OGC:CRS84"}:
        return geom
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    return shapely_transform(transformer.transform, geom)


def summarize_raster(path: Path, lake_geoms: dict[str, object]) -> dict[str, dict]:
    out = {}
    with rasterio.open(path) as ds:
        scale = float(ds.scales[0]) if ds.scales and ds.scales[0] not in (None, 0) else 1.0
        offset = float(ds.offsets[0]) if ds.offsets and ds.offsets[0] is not None else 0.0
        bounds_poly = None
        for rid, geom4326 in lake_geoms.items():
            geom = polygon_in_crs(geom4326, ds.crs)
            if bounds_poly is None:
                from shapely.geometry import box
                bounds_poly = box(*ds.bounds)
            if not geom.intersects(bounds_poly):
                continue
            try:
                data, _ = rio_mask(ds, [mapping(geom)], crop=True, filled=False, indexes=1)
            except ValueError:
                continue
            arr = np.asarray(data, dtype=np.float64)
            if np.ma.isMaskedArray(data):
                vals = data.compressed().astype(np.float64)
            else:
                vals = arr[np.isfinite(arr)]
            if vals.size == 0:
                continue
            vals = vals * scale + offset
            vals = vals[np.isfinite(vals)]
            if vals.size == 0:
                continue
            out[rid] = {
                "mean": float(np.mean(vals)),
                "p90": float(np.percentile(vals, 90)),
                "p10": float(np.percentile(vals, 10)),
                "pixelCount": int(vals.size),
            }
    return out


def main():
    args = parse_args()
    repo = args.repo.resolve()
    input_dir = args.input.resolve()
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    geoms_all = load_water_region_geometries(repo)
    lake_geoms = {rid: geom for rid, (kind, geom) in geoms_all.items() if kind == "lakes"}
    meta = load_water_region_metadata(repo)
    if args.region:
        wanted = set(args.region)
        lake_geoms = {rid: geom for rid, geom in lake_geoms.items() if rid in wanted}
    selected_metrics = set(args.metric or METRICS)

    files = sorted([*input_dir.rglob("*.tif"), *input_dir.rglob("*.tiff")])
    observations = {rid: {m: defaultdict(list) for m in selected_metrics} for rid in lake_geoms}
    used_files = 0
    for idx, path in enumerate(files, 1):
        metric = detect_metric(path)
        year = detect_year(path)
        if metric not in selected_metrics or year is None:
            continue
        if args.start_year and year < args.start_year:
            continue
        if args.end_year and year > args.end_year:
            continue
        print(f"[{idx}/{len(files)}] {metric} {year}: {path.name}")
        stats = summarize_raster(path, lake_geoms)
        if not stats:
            continue
        used_files += 1
        spec = METRICS[metric]
        high_field = "p90" if spec["stressDirection"] == "higher" else "p10"
        for rid, row in stats.items():
            observations[rid][metric][year].append({"mean": row["mean"], "stress": row[high_field], "pixelCount": row["pixelCount"]})

    payloads = {}
    latest_maps = {m: {} for m in selected_metrics}
    for rid in lake_geoms:
        blocks = {}
        for metric in selected_metrics:
            spec = METRICS[metric]
            annual = []
            for year, rows in sorted(observations[rid][metric].items()):
                if not rows:
                    continue
                annual.append({
                    "year": int(year),
                    "mean": float(np.mean([r["mean"] for r in rows])),
                    "stressPercentile": float(np.mean([r["stress"] for r in rows])),
                    "observationCount": len(rows),
                    "medianValidPixelCount": int(np.median([r["pixelCount"] for r in rows])),
                })
            block = metric_payload(
                key=metric,
                label=spec["label"],
                unit=spec["unit"],
                description=spec["description"],
                stress_direction=spec["stressDirection"],
                source="Copernicus Land Monitoring Service Lake Water Quality 300 m",
                annual=annual,
                high_field="stressPercentile",
                caveat="Satellite optical water-quality retrieval. Interpret with the CLMS quality flags/validation documentation and local in-situ context where decisions depend on regulatory thresholds.",
            )
            if block:
                block["calibratedProduct"] = True
                blocks[metric] = block
                latest_maps[metric][rid] = block["latest"]["mean"]
        if blocks:
            payloads[rid] = {
                "schemaVersion": 1,
                "regionId": rid,
                "name": meta.get(rid, {}).get("name") or rid,
                "regionKind": "lakes",
                "metrics": blocks,
                "method": {
                    "provider": "Copernicus Land Monitoring Service (CLMS)",
                    "aggregation": "10-daily CLMS lake-water-quality rasters summarized within MotherWorld lake polygons, then averaged annually",
                    "qualityTier": "official-calibrated",
                    "inputDirectory": str(input_dir),
                },
                "generatedAt": utc_now_iso(),
            }

    entries = {}
    for rid, payload in payloads.items():
        for metric, block in payload["metrics"].items():
            block["regionalStressPercentile"] = percentile_rank(
                latest_maps[metric], block["latest"]["mean"], higher_is_worse=block["stressDirection"] == "higher"
            )
        rel = write_region_payload(repo, "lakes", rid, payload)
        entries[rid] = {"url": rel, "kind": "lakes", "metrics": list(payload["metrics"]), "qualityTier": "official-calibrated", "generatedAt": payload["generatedAt"]}

    update_index(
        repo,
        entries,
        sources={
            "clms-lake-water-quality": {
                "label": "Copernicus Land Monitoring Service Lake Water Quality",
                "resolution": "300 m",
                "cadence": "10-daily",
                "v2Doi": "10.2909/801137b8-9575-43ef-a073-140b663cc61c",
                "qualityTier": "official-calibrated",
            }
        },
    )
    print(f"Scanned {len(files)} raster files; used {used_files}. Wrote/updated {len(entries)} lake payloads.")


if __name__ == "__main__":
    main()
