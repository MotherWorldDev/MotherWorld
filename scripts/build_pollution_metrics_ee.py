#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
import os
import time
from datetime import date
from pathlib import Path

import ee

from pollution_common import (
    antimeridian_safe_geometry,
    chunked,
    linear_trend_per_decade,
    load_region_geometries,
    load_region_metadata,
    percentile_rank,
    read_json,
    update_index,
    utc_now_iso,
    write_json,
    write_region_payload,
)

METRICS = {
    "pm25": {
        "label": "PM2.5",
        "unit": "µg/m³",
        "source": "CAMS Global NRT",
        "kind": "cams",
        "collection": "ECMWF/CAMS/NRT",
        "band": "particulate_matter_d_less_than_25_um_surface",
        "scale": 44528,
        "convert": 1e9,
        "startYear": 2017,
        "description": "Surface particulate matter with aerodynamic diameter below 2.5 µm, modelled/assimilated by CAMS.",
    },
    "no2": {
        "label": "NO₂",
        "unit": "mol/m²",
        "source": "Sentinel-5P TROPOMI",
        "kind": "s5p",
        "collection": "COPERNICUS/S5P/OFFL/L3_NO2",
        "band": "tropospheric_NO2_column_number_density",
        "scale": 7000,
        "convert": 1.0,
        "startYear": 2019,
        "description": "Tropospheric nitrogen dioxide vertical column density from TROPOMI.",
    },
    "so2": {
        "label": "SO₂",
        "unit": "mol/m²",
        "source": "Sentinel-5P TROPOMI",
        "kind": "s5p",
        "collection": "COPERNICUS/S5P/OFFL/L3_SO2",
        "band": "SO2_column_number_density",
        "scale": 7000,
        "convert": 1.0,
        "startYear": 2019,
        "description": "Sulfur dioxide vertical column density from TROPOMI; includes both anthropogenic and natural/volcanic sources.",
    },
    "co": {
        "label": "CO",
        "unit": "mol/m²",
        "source": "Sentinel-5P TROPOMI",
        "kind": "s5p",
        "collection": "COPERNICUS/S5P/OFFL/L3_CO",
        "band": "CO_column_number_density",
        "scale": 7000,
        "convert": 1.0,
        "startYear": 2019,
        "description": "Vertically integrated carbon monoxide column density from TROPOMI.",
    },
    "aerosolIndex": {
        "label": "Absorbing aerosol index",
        "unit": "index",
        "source": "Sentinel-5P TROPOMI",
        "kind": "s5p",
        "collection": "COPERNICUS/S5P/OFFL/L3_AER_AI",
        "band": "absorbing_aerosol_index",
        "scale": 7000,
        "convert": 1.0,
        "startYear": 2019,
        "description": "UV absorbing aerosol index; positive values indicate absorbing aerosol such as smoke, dust, or ash.",
    },
}


def parse_args():
    p = argparse.ArgumentParser(description="Build MotherWorld atmospheric pollution metrics with Google Earth Engine.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("EE_PROJECT"))
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--start-year", type=int, default=2019)
    p.add_argument("--end-year", type=int, default=date.today().year - 1)
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--kind", choices=("all","land","marine","lakes"), default="all")
    p.add_argument("--metric", action="append", choices=tuple(METRICS), default=[])
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--simplify-deg", type=float, default=0.002)
    p.add_argument("--max-pixels", type=int, default=300000)
    p.add_argument("--tile-scale", type=float, default=4.0)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--no-resume", action="store_true")
    return p.parse_args()


def initialize(args):
    if args.authenticate:
        ee.Authenticate()
    if not args.project:
        raise SystemExit("Earth Engine requires --project PROJECT_ID or GOOGLE_CLOUD_PROJECT. Authenticate first with earthengine authenticate.")
    ee.Initialize(project=args.project)


def ee_fc(batch, geometries, simplify_deg):
    features = []
    kept = []
    for rid in batch:
        _, geom = geometries[rid]
        geom = antimeridian_safe_geometry(geom)
        if geom is None or geom.is_empty:
            continue
        if simplify_deg > 0:
            geom = geom.simplify(simplify_deg, preserve_topology=True)
        features.append(ee.Feature(ee.Geometry(geom.__geo_interface__), {"region_id": rid}))
        kept.append(rid)
    return ee.FeatureCollection(features), kept


def annual_image(metric_key: str, year: int):
    spec = METRICS[metric_key]
    col = ee.ImageCollection(spec["collection"]).filterDate(f"{year}-01-01", f"{year+1}-01-01")
    if spec["kind"] == "cams":
        col = col.filter(ee.Filter.eq("model_initialization_hour", 0)).filter(ee.Filter.eq("model_forecast_hour", 0))
    img = col.select(spec["band"]).mean().multiply(spec["convert"]).rename(metric_key)
    return img


def reduce_batch(metric_key, year, batch, geometries, args):
    spec = METRICS[metric_key]
    fc, kept = ee_fc(batch, geometries, args.simplify_deg)
    if not kept:
        return []
    img = annual_image(metric_key, year)
    reducer = ee.Reducer.mean().combine(ee.Reducer.percentile([90]), sharedInputs=True)

    def annotate(feature):
        stats = img.reduceRegion(
            reducer=reducer,
            geometry=feature.geometry(),
            scale=spec["scale"],
            bestEffort=True,
            maxPixels=args.max_pixels,
            tileScale=args.tile_scale,
        )
        return ee.Feature(None, stats).set("region_id", feature.get("region_id")).set("year", year)

    result = fc.map(annotate)
    last = None
    for attempt in range(1, args.retries+1):
        try:
            info = result.getInfo()
            return [f.get("properties", {}) for f in info.get("features", [])]
        except Exception as exc:
            last = exc
            if attempt >= args.retries:
                break
            delay = min(60, 3 * (2 ** (attempt-1)))
            print(f"    retry {attempt}/{args.retries} after {delay}s: {exc}")
            time.sleep(delay)
    raise RuntimeError(f"Earth Engine reduction failed: {last}")


def load_cached(cache_root: Path, metrics: list[str], years: range, region_ids: list[str]):
    region_set = set(region_ids)
    out = {rid: {m: {} for m in metrics} for rid in region_ids}
    for metric in metrics:
        for year in years:
            for path in sorted((cache_root / metric / str(year)).glob("batch_*.json")):
                data = read_json(path, {})
                for row in data.get("rows", []):
                    rid = row.get("region_id")
                    if rid not in region_set:
                        continue
                    mean = row.get(f"{metric}_mean")
                    p90 = row.get(f"{metric}_p90")
                    if mean is None:
                        # Single-band combined reducers in EE sometimes emit plain 'mean'/'p90'.
                        mean = row.get("mean")
                    if p90 is None:
                        p90 = row.get("p90")
                    try:
                        mean = float(mean)
                    except Exception:
                        continue
                    try:
                        p90 = float(p90) if p90 is not None else None
                    except Exception:
                        p90 = None
                    out[rid][metric][year] = {"mean": mean, "spatialP90": p90}
    return out


def build_payload(rid, kind, meta, data, metrics, start_year, end_year):
    metric_payloads = {}
    for metric in metrics:
        spec = METRICS[metric]
        rows = data.get(metric, {})
        years = sorted(rows)
        if not years:
            continue
        annual = [{"year": y, "mean": rows[y]["mean"], "spatialP90": rows[y].get("spatialP90")} for y in years]
        vals = [r["mean"] for r in annual]
        metric_payloads[metric] = {
            "label": spec["label"], "unit": spec["unit"], "description": spec["description"],
            "source": spec["source"], "startYear": years[0], "endYear": years[-1],
            "latest": annual[-1], "annual": annual,
            "trendPerDecade": linear_trend_per_decade(years, vals),
            "change": float(vals[-1] - vals[0]) if len(vals) >= 2 else None,
        }
    if not metric_payloads:
        return None
    return {
        "schemaVersion": 1,
        "regionId": rid,
        "name": meta.get("name") or rid,
        "regionKind": kind,
        "period": {"requestedStartYear": start_year, "requestedEndYear": end_year},
        "metrics": metric_payloads,
        "method": {
            "provider": "Google Earth Engine",
            "aggregation": "annual image means reduced spatially over MotherWorld region polygons",
            "spatialHotspot": "90th percentile within the annual-mean raster",
            "note": "PM2.5 is a CAMS model/assimilation surface concentration. NO2, SO2 and CO are Sentinel-5P atmospheric column observations; their units are not directly comparable. No synthetic combined score is calculated."
        },
        "sources": {
            "cams": {"collection":"ECMWF/CAMS/NRT", "pixelSize":"~44.5 km", "metric":"PM2.5 surface concentration"},
            "sentinel5p": {"collectionPrefix":"COPERNICUS/S5P/OFFL/L3_", "metrics":["NO2","SO2","CO","AER_AI"]},
        },
        "generatedAt": utc_now_iso(),
    }


def main():
    args = parse_args()
    repo = args.repo.resolve()
    initialize(args)
    geoms = load_region_geometries(repo)
    meta = load_region_metadata(repo)
    region_ids = sorted(set(geoms) & set(meta))
    if args.kind != "all":
        region_ids = [rid for rid in region_ids if geoms[rid][0] == args.kind]
    if args.region:
        wanted = set(args.region)
        region_ids = [rid for rid in region_ids if rid in wanted]
    if not region_ids:
        raise SystemExit("No matching regions.")
    metrics = args.metric or list(METRICS)
    cache_root = repo / "pollution_raw" / f"earthengine_{args.start_year}_{args.end_year}"
    print(f"Regions: {len(region_ids)} | metrics: {', '.join(metrics)} | years: {args.start_year}-{args.end_year}")

    for metric in metrics:
        metric_start = max(args.start_year, METRICS[metric]["startYear"])
        for year in range(metric_start, args.end_year+1):
            for batch_idx, batch in enumerate(chunked(region_ids, max(1,args.batch_size)), 1):
                cache = cache_root / metric / str(year) / f"batch_{batch_idx:04d}.json"
                if cache.exists() and not args.no_resume:
                    print(f"{metric} {year} batch {batch_idx}: cached")
                    continue
                print(f"{metric} {year} batch {batch_idx}: {len(batch)} regions")
                rows = reduce_batch(metric, year, batch, geoms, args)
                write_json(cache, {"metric":metric,"year":year,"regionIds":batch,"rows":rows})

    data = load_cached(cache_root, metrics, range(args.start_year,args.end_year+1), region_ids)

    # Cross-region latest-value percentile ranks are useful but keep the metrics separate.
    latest_maps = {}
    for metric in metrics:
        latest_maps[metric] = {}
        for rid in region_ids:
            years = data[rid].get(metric, {})
            if years:
                y = max(years)
                latest_maps[metric][rid] = years[y]["mean"]

    entries = {}
    for rid in region_ids:
        kind = geoms[rid][0]
        payload = build_payload(rid, kind, meta.get(rid,{}), data[rid], metrics, args.start_year,args.end_year)
        if not payload:
            continue
        for metric, block in payload["metrics"].items():
            block["regionalPercentile"] = percentile_rank(latest_maps.get(metric,{}), block["latest"]["mean"])
        rel = write_region_payload(repo, kind, rid, payload)
        entries[rid] = {"url":rel,"kind":kind,"metrics":list(payload["metrics"]),"generatedAt":payload["generatedAt"]}
    update_index(repo, entries)
    print(f"Done. Wrote/updated {len(entries)} region pollution payloads.")

if __name__ == "__main__":
    main()
