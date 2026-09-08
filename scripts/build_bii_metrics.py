#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from pathlib import Path

from biodiversity_common import (
    extract_raster_candidates,
    load_regions,
    write_provider_region,
    year_from_name,
    zonal_raster_stats,
    utc_now_iso,
)

PROVIDER = "nhm_bii"


def parse_args():
    p = argparse.ArgumentParser(description="Aggregate NHM Biodiversity Intactness Index rasters to MotherWorld regions.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--input", required=True, type=Path, help="NHM BII ZIP, directory, or raster file.")
    p.add_argument("--kind", action="append", choices=["land", "marine", "lakes"], default=[])
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--supersample", type=int, default=2)
    p.add_argument("--year", type=int, default=None, help="Assign this year if the raster filename has no year.")
    p.add_argument("--latest-only", action="store_true")
    p.add_argument("--scale", choices=["auto", "fraction", "percent"], default="auto")
    return p.parse_args()


def scaled_stats(stats: dict, mode: str) -> tuple[dict, str]:
    if not stats:
        return stats, mode
    mean = float(stats["areaWeightedMean"])
    detected = mode
    if mode == "auto":
        detected = "fraction" if -0.05 <= mean <= 1.5 else "percent"
    factor = 100.0 if detected == "fraction" else 1.0
    out = dict(stats)
    for key in ("areaWeightedMean", "p10", "median", "p90", "min", "max"):
        if out.get(key) is not None:
            out[key] = float(out[key]) * factor
    return out, detected


def main():
    args = parse_args()
    repo = args.repo.resolve()
    kinds = args.kind or ["land"]
    regions = load_regions(repo, kinds)
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]
    rasters, tmp = extract_raster_candidates(args.input)
    try:
        if not rasters:
            raise SystemExit("No raster files found in BII input.")
        items = []
        for raster in rasters:
            year = year_from_name(raster) or args.year
            items.append((year, raster))
        if args.latest_only:
            items.sort(key=lambda x: (-1 if x[0] is None else x[0], str(x[1])))
            items = [items[-1]]
        print(f"BII rasters: {len(items)} | regions: {len(regions)}")
        for pos, region in enumerate(regions, 1):
            series = []
            detected_scale = None
            for year, raster in items:
                stats = zonal_raster_stats(raster, region, supersample=args.supersample)
                if not stats:
                    continue
                stats, scale = scaled_stats(stats, args.scale)
                detected_scale = detected_scale or scale
                series.append({"year": year, "intactnessPct": stats["areaWeightedMean"], "spatial": stats})
            if not series:
                continue
            series.sort(key=lambda x: (-1 if x["year"] is None else x["year"]))
            latest = series[-1]
            known = [x for x in series if x["year"] is not None and math.isfinite(float(x["intactnessPct"]))]
            change = None
            if len(known) >= 2:
                change = float(known[-1]["intactnessPct"] - known[0]["intactnessPct"])
            payload = {
                "provider": PROVIDER,
                "regionId": region.region_id,
                "regionKind": region.kind,
                "generatedAt": utc_now_iso(),
                "metrics": {
                    "intactnessPct": float(latest["intactnessPct"]),
                    "intactnessChangePp": change,
                    "latestYear": latest["year"],
                    "series": series,
                },
                "source": {
                    "label": "Natural History Museum Biodiversity Intactness Index v2.1.1",
                    "doi": "10.5519/k33reyb6",
                    "license": "CC BY-NC-SA 4.0 (non-commercial limited release)",
                    "licenseClass": "restricted_noncommercial",
                    "removableProvider": True,
                    "input": str(args.input),
                    "detectedValueScale": detected_scale,
                },
            }
            write_provider_region(repo, PROVIDER, region.kind, region.region_id, payload)
            print(f"[{pos}/{len(regions)}] {region.region_id}: {latest['intactnessPct']:.1f}%")
    finally:
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    main()
