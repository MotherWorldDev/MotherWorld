#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

import ee
import numpy as np

from freshwater_common import geometric_mean, stability_score_from_abs_z, utc_now_iso, write_json

COLLECTION = "JRC/GSW1_4/YearlyHistory"


def initialize(project: str, authenticate: bool) -> None:
    if authenticate:
        ee.Authenticate()
    ee.Initialize(project=project)


def main() -> None:
    p = argparse.ArgumentParser(description="Build global Freshwater Stability surface-water component from JRC Global Surface Water in Earth Engine.")
    p.add_argument("--project", required=True)
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--baseline-start", type=int, default=1984)
    p.add_argument("--baseline-end", type=int, default=2000)
    p.add_argument("--recent-start", type=int, default=2017)
    p.add_argument("--recent-end", type=int, default=2021)
    p.add_argument("--scale", type=float, default=1000.0)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()
    initialize(args.project, args.authenticate)
    repo = args.repo.resolve()
    output = args.output or repo / "index_raw/freshwater/surface_water.json"

    coll = ee.ImageCollection(COLLECTION)
    geom = ee.Geometry.Rectangle([-180, -59, 180, 78], None, False)
    area = ee.Image.pixelArea()
    features = []
    for year in range(1984, 2022):
        image = ee.Image(coll.filter(ee.Filter.eq("year", year)).first()).select("waterClass")
        valid = image.gt(0)
        seasonal = image.eq(2)
        permanent = image.eq(3)
        any_water = seasonal.Or(permanent)
        bands = ee.Image.cat([
            area.updateMask(any_water).rename("water_area"),
            area.updateMask(permanent).rename("permanent_area"),
            area.updateMask(seasonal).rename("seasonal_area"),
            area.updateMask(valid).rename("valid_area"),
        ])
        stats = bands.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=geom,
            scale=args.scale,
            bestEffort=True,
            maxPixels=1e10,
            tileScale=4,
        )
        features.append(ee.Feature(None, stats).set("year", year))

    info = ee.FeatureCollection(features).getInfo()
    rows = []
    for feature in info.get("features", []):
        r = feature.get("properties", {})
        if not r.get("water_area"):
            continue
        rows.append({
            "year": int(r["year"]),
            "waterAreaKm2": float(r.get("water_area") or 0) / 1e6,
            "permanentAreaKm2": float(r.get("permanent_area") or 0) / 1e6,
            "seasonalAreaKm2": float(r.get("seasonal_area") or 0) / 1e6,
            "validAreaKm2": float(r.get("valid_area") or 0) / 1e6,
        })
    rows.sort(key=lambda x: x["year"])
    baseline = [r for r in rows if args.baseline_start <= r["year"] <= args.baseline_end]
    recent = [r for r in rows if args.recent_start <= r["year"] <= args.recent_end]
    if len(baseline) < 8 or len(recent) < 2:
        raise RuntimeError("Insufficient JRC yearly water history for configured baseline/recent windows")

    base_any = np.asarray([r["waterAreaKm2"] for r in baseline], dtype=float)
    recent_any = np.asarray([r["waterAreaKm2"] for r in recent], dtype=float)
    base_perm = float(np.mean([r["permanentAreaKm2"] for r in baseline]))
    recent_perm = float(np.mean([r["permanentAreaKm2"] for r in recent]))
    mean = float(np.mean(base_any))
    sd = float(np.std(base_any, ddof=1))
    recent_mean = float(np.mean(recent_any))
    z = abs(recent_mean - mean) / sd if sd > 0 else None
    extent_score = stability_score_from_abs_z(z)
    permanent_retention = min(100.0, 100.0 * recent_perm / base_perm) if base_perm > 0 else None
    score = geometric_mean([(extent_score, 0.65), (permanent_retention, 0.35)])
    expected_recent_years = args.recent_end - args.recent_start + 1
    coverage = min(1.0, len(recent) / max(1, expected_recent_years))
    change_pct = 100.0 * (recent_mean - mean) / mean if mean > 0 else None
    perm_change_pct = 100.0 * (recent_perm - base_perm) / base_perm if base_perm > 0 else None

    payload = {
        "schemaVersion": 1,
        "providerId": "jrc_surface_water",
        "label": "Surface-water extent stability",
        "score": round(score, 4) if score is not None else None,
        "coverage": round(coverage, 6),
        "raw": round(change_pct, 4) if change_pct is not None else None,
        "unit": "% change in total mapped surface-water area vs baseline mean",
        "context": {
            "extentStabilityScore": round(extent_score, 4) if extent_score is not None else None,
            "permanentWaterRetentionScore": round(permanent_retention, 4) if permanent_retention is not None else None,
            "recentMeanWaterAreaKm2": round(recent_mean, 2),
            "baselineMeanWaterAreaKm2": round(mean, 2),
            "recentMeanPermanentWaterKm2": round(recent_perm, 2),
            "baselineMeanPermanentWaterKm2": round(base_perm, 2),
            "permanentWaterChangePct": round(perm_change_pct, 4) if perm_change_pct is not None else None,
            "standardizedExtentDepartureAbsZ": round(z, 4) if z is not None else None,
            "baseline": f"{args.baseline_start}-{args.baseline_end}",
            "recentPeriod": f"{args.recent_start}-{args.recent_end}",
        },
        "series": [{"year": r["year"], "value": r["waterAreaKm2"], "permanentAreaKm2": r["permanentAreaKm2"], "seasonalAreaKm2": r["seasonalAreaKm2"]} for r in rows],
        "source": {
            "id": COLLECTION,
            "label": "JRC Global Surface Water Yearly History v1.4",
            "variable": "waterClass",
            "nativeResolution": "30 m",
            "coverage": "1984-2021",
        },
        "method": {
            "scope": "global mapped inland surface water",
            "normalization": "65% extent stability from recent-vs-baseline global annual water-area z-score + 35% permanent-water retention; gains cannot improve retention above 100",
            "requestedReductionScaleM": args.scale,
            "scoreDirection": "100 = global surface-water extent remains near its historical regime with permanent-water area retained",
        },
        "generatedAt": utc_now_iso(),
    }
    write_json(output, payload)
    print(f"Surface-water stability: {payload['score']} coverage={coverage:.1%} -> {output}")


if __name__ == "__main__":
    main()
