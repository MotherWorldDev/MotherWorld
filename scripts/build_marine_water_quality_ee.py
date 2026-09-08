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
    "chlorophyll_a": {
        "label": "Chlorophyll-a",
        "unit": "mg/m³",
        "collection": "COPERNICUS/MARINE/SATELLITE_OCEAN_COLOR/V6",
        "band": "chlor_a",
        "scale": 4000,
        "startYear": 1997,
        "stressDirection": "higher",
        "source": "Copernicus C3S / ESA OC-CCI ocean colour",
        "description": "Surface chlorophyll-a concentration, used as an indicator of phytoplankton biomass and eutrophication pressure; elevated values are not automatically anthropogenic pollution.",
        "highPercentile": 90,
    },
    "kd490": {
        "label": "Light attenuation (KD490)",
        "unit": "1/m",
        "collection": "COPERNICUS/MARINE/OC_GLO_BGC/TRANSPARENCY_MULTI_4KM",
        "band": "KD490",
        "scale": 4000,
        "startYear": 2025,
        "stressDirection": "higher",
        "source": "Copernicus Marine GlobColour",
        "description": "Diffuse attenuation at 490 nm. Higher values mean light is attenuated faster and the water is optically less clear.",
        "highPercentile": 90,
    },
    "secchi_depth": {
        "label": "Secchi-depth clarity",
        "unit": "m",
        "collection": "COPERNICUS/MARINE/OC_GLO_BGC/TRANSPARENCY_MULTI_4KM",
        "band": "ZSD",
        "scale": 4000,
        "startYear": 2025,
        "stressDirection": "lower",
        "source": "Copernicus Marine GlobColour",
        "description": "Satellite-estimated Secchi depth. Lower values indicate lower water transparency.",
        "highPercentile": 10,
    },
    "cdm": {
        "label": "Dissolved/detrital absorption (CDM)",
        "unit": "1/m",
        "collection": "COPERNICUS/MARINE/OC_GLO_BGC/OPTICS_MULTI_4KM",
        "band": "CDM",
        "scale": 4000,
        "startYear": 2025,
        "stressDirection": "higher",
        "source": "Copernicus Marine GlobColour",
        "description": "Optical absorption by coloured dissolved organic matter and non-algal particles; useful for runoff and water-colour context but not a contaminant concentration by itself.",
        "highPercentile": 90,
    },
    "particle_backscatter": {
        "label": "Particle backscatter (BBP)",
        "unit": "1/m",
        "collection": "COPERNICUS/MARINE/OC_GLO_BGC/OPTICS_MULTI_4KM",
        "band": "BBP",
        "scale": 4000,
        "startYear": 2025,
        "stressDirection": "higher",
        "source": "Copernicus Marine GlobColour",
        "description": "Backscattering by particles in surface waters; useful as a particulate-load proxy, not a direct chemical-pollution measurement.",
        "highPercentile": 90,
    },
}


def parse_args():
    p = argparse.ArgumentParser(description="Build MotherWorld marine water-quality/pollution indicators with Google Earth Engine.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("EE_PROJECT"))
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--start-year", type=int, default=1997)
    p.add_argument("--end-year", type=int, default=date.today().year - 1)
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--metric", action="append", choices=tuple(METRICS), default=[])
    p.add_argument("--batch-size", type=int, default=20)
    p.add_argument("--simplify-deg", type=float, default=0.002)
    p.add_argument("--max-pixels", type=int, default=400000)
    p.add_argument("--tile-scale", type=float, default=4.0)
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--no-resume", action="store_true")
    return p.parse_args()


def initialize(args):
    if args.authenticate:
        ee.Authenticate()
    if not args.project:
        raise SystemExit("Earth Engine requires --project PROJECT_ID or GOOGLE_CLOUD_PROJECT. Authenticate first with `earthengine authenticate`.")
    ee.Initialize(project=args.project)


def ee_fc(batch, geometries, simplify_deg):
    features = []
    kept = []
    for rid in batch:
        geom = antimeridian_safe_geometry(geometries[rid])
        if geom is None or geom.is_empty:
            continue
        if simplify_deg > 0:
            geom = geom.simplify(simplify_deg, preserve_topology=True)
        features.append(ee.Feature(ee.Geometry(geom.__geo_interface__), {"region_id": rid}))
        kept.append(rid)
    return ee.FeatureCollection(features), kept


def annual_image(metric, year):
    spec = METRICS[metric]
    return (
        ee.ImageCollection(spec["collection"])
        .filterDate(f"{year}-01-01", f"{year+1}-01-01")
        .select(spec["band"])
        .mean()
        .rename(metric)
    )


def reduce_batch(metric, year, batch, geometries, args):
    spec = METRICS[metric]
    fc, kept = ee_fc(batch, geometries, args.simplify_deg)
    if not kept:
        return []
    image = annual_image(metric, year)
    q = int(spec["highPercentile"])
    reducer = ee.Reducer.mean().combine(ee.Reducer.percentile([q]), sharedInputs=True)

    def annotate(feature):
        stats = image.reduceRegion(
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
    for attempt in range(1, args.retries + 1):
        try:
            info = result.getInfo()
            return [f.get("properties", {}) for f in info.get("features", [])]
        except Exception as exc:
            last = exc
            if attempt >= args.retries:
                break
            delay = min(60, 3 * (2 ** (attempt - 1)))
            print(f"    retry {attempt}/{args.retries} after {delay}s: {exc}")
            time.sleep(delay)
    raise RuntimeError(f"Earth Engine reduction failed: {last}")


def load_cached(cache_root, metrics, years, region_ids):
    wanted = set(region_ids)
    out = {rid: {m: {} for m in metrics} for rid in region_ids}
    for metric in metrics:
        q = METRICS[metric]["highPercentile"]
        for year in years:
            for path in sorted((cache_root / metric / str(year)).glob("batch_*.json")):
                data = read_json(path, {})
                for row in data.get("rows", []):
                    rid = row.get("region_id")
                    if rid not in wanted:
                        continue
                    mean = row.get(f"{metric}_mean", row.get("mean"))
                    high = row.get(f"{metric}_p{q}", row.get(f"p{q}"))
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


def build_region_payload(rid, meta, data, metrics, start_year, end_year):
    blocks = {}
    for metric in metrics:
        spec = METRICS[metric]
        rows = data.get(metric, {})
        annual = [
            {"year": year, "mean": rows[year]["mean"], "stressPercentile": rows[year].get("stressPercentile")}
            for year in sorted(rows)
        ]
        block = metric_payload(
            key=metric,
            label=spec["label"],
            unit=spec["unit"],
            description=spec["description"],
            stress_direction=spec["stressDirection"],
            source=spec["source"],
            annual=annual,
            high_field="stressPercentile",
            caveat="This is an optical/environmental water-quality indicator; it does not directly measure toxins, pathogens, plastics, hydrocarbons or heavy metals.",
        )
        if block:
            block["stressPercentileType"] = f"spatial p{spec['highPercentile']} of annual-mean raster"
            blocks[metric] = block
    if not blocks:
        return None
    return {
        "schemaVersion": 1,
        "regionId": rid,
        "name": meta.get("name") or rid,
        "regionKind": "marine",
        "period": {"requestedStartYear": start_year, "requestedEndYear": end_year},
        "metrics": blocks,
        "method": {
            "provider": "Google Earth Engine",
            "aggregation": "annual mean images reduced over MotherWorld marine ecoregion polygons",
            "interpretation": "Water-quality pressure indicators are kept in their native physical units. No synthetic universal water-pollution score is calculated.",
        },
        "generatedAt": utc_now_iso(),
    }


def main():
    args = parse_args()
    repo = args.repo.resolve()
    initialize(args)
    all_geoms = load_water_region_geometries(repo)
    geoms = {rid: geom for rid, (kind, geom) in all_geoms.items() if kind == "marine"}
    meta = load_water_region_metadata(repo)
    region_ids = sorted(geoms)
    if args.region:
        wanted = set(args.region)
        region_ids = [rid for rid in region_ids if rid in wanted]
    if not region_ids:
        raise SystemExit("No matching marine regions.")
    metrics = args.metric or list(METRICS)
    cache_root = repo / "water_pollution_raw" / f"earthengine_marine_{args.start_year}_{args.end_year}"
    print(f"Marine regions: {len(region_ids)} | metrics: {', '.join(metrics)} | years: {args.start_year}-{args.end_year}")

    for metric in metrics:
        first = max(args.start_year, METRICS[metric]["startYear"])
        for year in range(first, args.end_year + 1):
            for batch_idx, batch in enumerate(chunked(region_ids, max(1, args.batch_size)), 1):
                path = cache_root / metric / str(year) / f"batch_{batch_idx:04d}.json"
                if path.exists() and not args.no_resume:
                    print(f"{metric} {year} batch {batch_idx}: cached")
                    continue
                print(f"{metric} {year} batch {batch_idx}: {len(batch)} regions")
                rows = reduce_batch(metric, year, batch, geoms, args)
                write_json(path, {"metric": metric, "year": year, "regionIds": batch, "rows": rows})

    data = load_cached(cache_root, metrics, range(args.start_year, args.end_year + 1), region_ids)
    latest_maps = {metric: {} for metric in metrics}
    for metric in metrics:
        for rid in region_ids:
            rows = data[rid].get(metric, {})
            if rows:
                latest_maps[metric][rid] = rows[max(rows)]["mean"]

    entries = {}
    for rid in region_ids:
        payload = build_region_payload(rid, meta.get(rid, {}), data[rid], metrics, args.start_year, args.end_year)
        if not payload:
            continue
        for metric, block in payload["metrics"].items():
            higher = block["stressDirection"] == "higher"
            block["regionalStressPercentile"] = percentile_rank(latest_maps.get(metric, {}), block["latest"]["mean"], higher_is_worse=higher)
        rel = write_region_payload(repo, "marine", rid, payload)
        entries[rid] = {"url": rel, "kind": "marine", "metrics": list(payload["metrics"]), "generatedAt": payload["generatedAt"]}

    update_index(
        repo,
        entries,
        sources={
            "copernicus-ocean-colour-v6": {"label": "Copernicus Satellite Ocean Color V6", "doi": "10.24381/cds.f85b319d", "resolution": "4 km"},
            "copernicus-marine-globcolour": {"label": "Copernicus Marine GlobColour L4", "doi": "10.48670/moi-00279", "resolution": "4 km"},
        },
    )
    print(f"Done. Wrote/updated {len(entries)} marine water-quality payloads.")


if __name__ == "__main__":
    main()
