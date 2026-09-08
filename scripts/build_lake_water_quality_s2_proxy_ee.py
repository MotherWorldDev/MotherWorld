#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import time
from datetime import date
from pathlib import Path

import ee

from water_pollution_common import (
    antimeridian_safe_geometry,
    chunked,
    load_water_region_geometries,
    load_water_region_metadata,
    metric_payload,
    percentile_rank,
    read_json,
    update_index,
    utc_now_iso,
    write_json,
    write_region_payload,
)

METRICS = {
    "turbidity_ndti": {
        "label": "Turbidity optical proxy (NDTI)",
        "unit": "index",
        "stressDirection": "higher",
        "description": "Normalized Difference Turbidity Index from Sentinel-2 red and green surface reflectance. Higher values generally indicate more optically turbid water, but this is not a calibrated NTU measurement.",
    },
    "algae_ndci": {
        "label": "Algal/chlorophyll optical proxy (NDCI)",
        "unit": "index",
        "stressDirection": "higher",
        "description": "Normalized Difference Chlorophyll Index from Sentinel-2 red-edge and red bands. Useful as a relative algal-biomass/bloom proxy, not a universal chlorophyll-a concentration retrieval.",
    },
    "red_reflectance": {
        "label": "Red water reflectance proxy",
        "unit": "reflectance",
        "stressDirection": "higher",
        "description": "Sentinel-2 red-band surface reflectance over persistent water. Elevated values can track suspended matter/turbidity but are not a direct pollutant concentration.",
    },
}


def parse_args():
    p = argparse.ArgumentParser(description="Build fallback lake water-quality optical proxies from Sentinel-2 in Earth Engine.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("EE_PROJECT"))
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=date.today().year - 1)
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--simplify-deg", type=float, default=0.0005)
    p.add_argument("--scale", type=float, default=100.0, help="Analysis scale; 100 m default keeps global preprocessing practical.")
    p.add_argument("--max-pixels", type=int, default=400000)
    p.add_argument("--tile-scale", type=float, default=4.0)
    p.add_argument("--min-water-occurrence", type=int, default=20)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--no-resume", action="store_true")
    return p.parse_args()


def initialize(args):
    if args.authenticate:
        ee.Authenticate()
    if not args.project:
        raise SystemExit("Earth Engine requires --project PROJECT_ID or GOOGLE_CLOUD_PROJECT. Authenticate first with `earthengine authenticate`.")
    ee.Initialize(project=args.project)


def clean_s2(image):
    scl = image.select("SCL")
    cloud_free = (
        scl.neq(1).And(scl.neq(3)).And(scl.neq(7)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
    )
    return image.updateMask(cloud_free)


def annual_proxy_image(year, min_water_occurrence):
    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(f"{year}-01-01", f"{year+1}-01-01")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
        .map(clean_s2)
    )
    median = col.select(["B3", "B4", "B5"]).median().multiply(0.0001)
    water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gte(min_water_occurrence)
    ndti = median.normalizedDifference(["B4", "B3"]).rename("turbidity_ndti")
    ndci = median.normalizedDifference(["B5", "B4"]).rename("algae_ndci")
    red = median.select("B4").rename("red_reflectance")
    return ee.Image.cat([ndti, ndci, red]).updateMask(water)


def ee_fc(batch, geoms, simplify):
    feats = []
    kept = []
    for rid in batch:
        geom = antimeridian_safe_geometry(geoms[rid])
        if geom is None or geom.is_empty:
            continue
        if simplify > 0:
            geom = geom.simplify(simplify, preserve_topology=True)
        feats.append(ee.Feature(ee.Geometry(geom.__geo_interface__), {"region_id": rid}))
        kept.append(rid)
    return ee.FeatureCollection(feats), kept


def reduce_batch(year, batch, geoms, args):
    fc, kept = ee_fc(batch, geoms, args.simplify_deg)
    if not kept:
        return []
    image = annual_proxy_image(year, args.min_water_occurrence)
    reducer = ee.Reducer.mean().combine(ee.Reducer.percentile([90]), sharedInputs=True)

    def annotate(feature):
        stats = image.reduceRegion(
            reducer=reducer,
            geometry=feature.geometry(),
            scale=args.scale,
            bestEffort=True,
            maxPixels=args.max_pixels,
            tileScale=args.tile_scale,
        )
        return ee.Feature(None, stats).set("region_id", feature.get("region_id")).set("year", year)

    result = fc.map(annotate)
    last = None
    for attempt in range(1, args.retries + 1):
        try:
            info = result.getInfo()
            return [f.get("properties", {}) for f in info.get("features", [])]
        except Exception as exc:
            last = exc
            if attempt >= args.retries:
                break
            delay = min(60, 3 * 2 ** (attempt - 1))
            print(f"    retry {attempt}/{args.retries} after {delay}s: {exc}")
            time.sleep(delay)
    raise RuntimeError(f"Earth Engine reduction failed: {last}")


def load_cache(root, years, region_ids):
    wanted = set(region_ids)
    out = {rid: {m: {} for m in METRICS} for rid in region_ids}
    for year in years:
        for path in sorted((root / str(year)).glob("batch_*.json")):
            data = read_json(path, {})
            for row in data.get("rows", []):
                rid = row.get("region_id")
                if rid not in wanted:
                    continue
                for metric in METRICS:
                    mean = row.get(f"{metric}_mean")
                    high = row.get(f"{metric}_p90")
                    try:
                        mean = float(mean)
                    except (TypeError, ValueError):
                        continue
                    try:
                        high = float(high) if high is not None else None
                    except (TypeError, ValueError):
                        high = None
                    out[rid][metric][year] = {"mean": mean, "stressPercentile": high}
    return out


def main():
    args = parse_args()
    repo = args.repo.resolve()
    initialize(args)
    all_geoms = load_water_region_geometries(repo)
    geoms = {rid: geom for rid, (kind, geom) in all_geoms.items() if kind == "lakes"}
    meta = load_water_region_metadata(repo)
    region_ids = sorted(geoms)
    if args.region:
        wanted = set(args.region)
        region_ids = [rid for rid in region_ids if rid in wanted]
    if not region_ids:
        raise SystemExit("No matching lake regions.")

    cache_root = repo / "water_pollution_raw" / f"earthengine_lake_proxy_{args.start_year}_{args.end_year}"
    for year in range(args.start_year, args.end_year + 1):
        for batch_idx, batch in enumerate(chunked(region_ids, max(1, args.batch_size)), 1):
            path = cache_root / str(year) / f"batch_{batch_idx:04d}.json"
            if path.exists() and not args.no_resume:
                print(f"lake proxy {year} batch {batch_idx}: cached")
                continue
            print(f"lake proxy {year} batch {batch_idx}: {len(batch)} regions")
            rows = reduce_batch(year, batch, geoms, args)
            write_json(path, {"year": year, "regionIds": batch, "rows": rows})

    data = load_cache(cache_root, range(args.start_year, args.end_year + 1), region_ids)
    latest_maps = {m: {} for m in METRICS}
    entries = {}
    payloads = {}
    for rid in region_ids:
        blocks = {}
        for metric, spec in METRICS.items():
            annual = [
                {"year": y, "mean": data[rid][metric][y]["mean"], "stressPercentile": data[rid][metric][y].get("stressPercentile")}
                for y in sorted(data[rid][metric])
            ]
            block = metric_payload(
                key=metric,
                label=spec["label"],
                unit=spec["unit"],
                description=spec["description"],
                stress_direction=spec["stressDirection"],
                source="Sentinel-2 SR Harmonized + JRC Global Surface Water",
                annual=annual,
                high_field="stressPercentile",
                caveat="Fallback optical proxy only. Prefer official Copernicus Lake Water Quality turbidity/TSI/chlorophyll/TSM products when available.",
            )
            if block:
                block["calibratedConcentration"] = False
                blocks[metric] = block
                latest_maps[metric][rid] = block["latest"]["mean"]
        if not blocks:
            continue
        payloads[rid] = {
            "schemaVersion": 1,
            "regionId": rid,
            "name": meta.get(rid, {}).get("name") or rid,
            "regionKind": "lakes",
            "period": {"requestedStartYear": args.start_year, "requestedEndYear": args.end_year},
            "metrics": blocks,
            "method": {
                "provider": "Google Earth Engine",
                "aggregation": "annual median cloud-screened Sentinel-2 surface reflectance over persistent-water pixels, then polygon mean/p90",
                "waterMask": f"JRC Global Surface Water occurrence >= {args.min_water_occurrence}%",
                "warning": "These are relative optical proxies, not regulatory water-quality concentrations.",
            },
            "generatedAt": utc_now_iso(),
        }

    for rid, payload in payloads.items():
        for metric, block in payload["metrics"].items():
            block["regionalStressPercentile"] = percentile_rank(latest_maps[metric], block["latest"]["mean"], higher_is_worse=True)
        rel = write_region_payload(repo, "lakes", rid, payload)
        entries[rid] = {"url": rel, "kind": "lakes", "metrics": list(payload["metrics"]), "qualityTier": "proxy", "generatedAt": payload["generatedAt"]}

    update_index(
        repo,
        entries,
        sources={
            "sentinel2-lake-proxy": {
                "label": "Sentinel-2 Surface Reflectance optical proxy fallback",
                "collection": "COPERNICUS/S2_SR_HARMONIZED",
                "waterMask": "JRC/GSW1_4/GlobalSurfaceWater",
                "qualityTier": "proxy",
            }
        },
    )
    print(f"Done. Wrote/updated {len(entries)} lake proxy payloads.")


if __name__ == "__main__":
    main()
