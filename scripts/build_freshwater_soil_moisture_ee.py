#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import ee

from freshwater_common import score_from_expected_in_envelope, utc_now_iso, write_json

COLLECTION = "ECMWF/ERA5_LAND/MONTHLY_AGGR"


def initialize(project: str, authenticate: bool) -> None:
    if authenticate:
        ee.Authenticate()
    ee.Initialize(project=project)


def root_zone(image):
    # Approximate 0-100 cm root-zone volumetric soil water from ERA5-Land's
    # first three layers (0-7, 7-28, 28-100 cm), weighted by layer thickness.
    return (
        image.select("volumetric_soil_water_layer_1").multiply(0.07)
        .add(image.select("volumetric_soil_water_layer_2").multiply(0.21))
        .add(image.select("volumetric_soil_water_layer_3").multiply(0.72))
        .rename("root_zone_soil_water")
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Build global Freshwater Stability soil-moisture component from ERA5-Land in Earth Engine.")
    p.add_argument("--project", required=True)
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--baseline-start", type=int, default=1991)
    p.add_argument("--baseline-end", type=int, default=2020)
    p.add_argument("--recent-start", type=int, default=None)
    p.add_argument("--recent-end", type=int, default=date.today().year - 1)
    p.add_argument("--scale", type=float, default=25000.0)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()
    initialize(args.project, args.authenticate)
    repo = args.repo.resolve()
    output = args.output or repo / "index_raw/freshwater/soil_moisture.json"
    recent_end = int(args.recent_end)
    recent_start = int(args.recent_start if args.recent_start is not None else recent_end - 4)

    coll = ee.ImageCollection(COLLECTION)
    baseline = coll.filterDate(f"{args.baseline_start}-01-01", f"{args.baseline_end + 1}-01-01").map(root_zone)
    # Exclude Antarctica and a generous Greenland box so this family measures
    # hydrology rather than duplicating land-ice condition in Cryosphere.
    geom = ee.Geometry.Rectangle([-180, -60, 180, 90], None, False).difference(
        ee.Geometry.Rectangle([-75, 58, -10, 85], None, False), 1000
    )
    pixel_area = ee.Image.pixelArea()
    features = []
    for year in range(recent_start, recent_end + 1):
        for month in range(1, 13):
            pctl = (
                baseline.filter(ee.Filter.calendarRange(month, month, "month"))
                .reduce(ee.Reducer.percentile([10, 90]))
            )
            p10 = pctl.select("root_zone_soil_water_p10")
            p90 = pctl.select("root_zone_soil_water_p90")
            start = ee.Date.fromYMD(year, month, 1)
            end = start.advance(1, "month")
            current = root_zone(coll.filterDate(start, end).mean())
            valid = current.mask().And(p10.mask()).And(p90.mask())
            low = current.lt(p10).And(valid)
            high = current.gt(p90).And(valid)
            normal = current.gte(p10).And(current.lte(p90)).And(valid)
            bands = ee.Image.cat([
                pixel_area.updateMask(valid).rename("valid_area"),
                pixel_area.updateMask(normal).rename("normal_area"),
                pixel_area.updateMask(low).rename("low_area"),
                pixel_area.updateMask(high).rename("high_area"),
            ])
            stats = bands.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=geom,
                scale=args.scale,
                bestEffort=True,
                maxPixels=1e10,
                tileScale=4,
            )
            features.append(ee.Feature(None, stats).set({"year": year, "month": month}))

    info = ee.FeatureCollection(features).getInfo()
    rows = [f.get("properties", {}) for f in info.get("features", [])]
    valid_sum = sum(float(r.get("valid_area") or 0) for r in rows)
    normal_sum = sum(float(r.get("normal_area") or 0) for r in rows)
    low_sum = sum(float(r.get("low_area") or 0) for r in rows)
    high_sum = sum(float(r.get("high_area") or 0) for r in rows)
    normal_fraction = normal_sum / valid_sum if valid_sum > 0 else None
    low_fraction = low_sum / valid_sum if valid_sum > 0 else None
    high_fraction = high_sum / valid_sum if valid_sum > 0 else None
    score = score_from_expected_in_envelope(normal_fraction, 0.80)
    valid_months = sum(1 for r in rows if float(r.get("valid_area") or 0) > 0)
    expected_months = (recent_end - recent_start + 1) * 12
    coverage = valid_months / expected_months if expected_months else 0.0

    series = []
    for year in range(recent_start, recent_end + 1):
        yr = [r for r in rows if int(r.get("year") or -1) == year]
        va = sum(float(r.get("valid_area") or 0) for r in yr)
        na = sum(float(r.get("normal_area") or 0) for r in yr)
        la = sum(float(r.get("low_area") or 0) for r in yr)
        ha = sum(float(r.get("high_area") or 0) for r in yr)
        if va > 0:
            series.append({
                "year": year,
                "value": na / va,
                "normalFraction": na / va,
                "dryExtremeFraction": la / va,
                "wetExtremeFraction": ha / va,
            })

    payload = {
        "schemaVersion": 1,
        "providerId": "era5land_soil_moisture",
        "label": "Root-zone soil-moisture stability",
        "score": round(score, 4) if score is not None else None,
        "coverage": round(coverage, 6),
        "raw": round(normal_fraction * 100.0, 4) if normal_fraction is not None else None,
        "unit": "% land-month area inside historical P10-P90 envelope",
        "context": {
            "dryExtremeFraction": round(low_fraction, 6) if low_fraction is not None else None,
            "wetExtremeFraction": round(high_fraction, 6) if high_fraction is not None else None,
            "normalFraction": round(normal_fraction, 6) if normal_fraction is not None else None,
            "baseline": f"{args.baseline_start}-{args.baseline_end}",
            "recentPeriod": f"{recent_start}-{recent_end}",
            "expectedBaselineEnvelopeFraction": 0.80,
        },
        "series": series,
        "source": {
            "id": COLLECTION,
            "label": "ERA5-Land Monthly Aggregated",
            "variables": [
                "volumetric_soil_water_layer_1",
                "volumetric_soil_water_layer_2",
                "volumetric_soil_water_layer_3",
            ],
            "nativeResolution": "~11.1 km",
        },
        "method": {
            "scope": "global ice-sheet-excluded land",
            "normalization": "recent land-area-month fraction inside each pixel/calendar-month historical P10-P90 envelope; 80% is the baseline expectation and scores 100",
            "requestedReductionScaleM": args.scale,
            "scoreDirection": "100 = recent root-zone soil moisture remains within the historical hydrological envelope",
        },
        "generatedAt": utc_now_iso(),
    }
    write_json(output, payload)
    print(f"Soil-moisture stability: {payload['score']} coverage={coverage:.1%} -> {output}")


if __name__ == "__main__":
    main()
