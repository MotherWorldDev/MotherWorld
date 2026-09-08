#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from biodiversity_common import extract_raster_candidates, load_regions, utc_now_iso, write_provider_region, zonal_raster_stats

PROVIDER = "iucn_rasters"


def one_raster(path: Path | None) -> tuple[Path | None, object | None]:
    if path is None:
        return None, None
    files, tmp = extract_raster_candidates(path)
    if not files:
        raise RuntimeError(f"No raster found in {path}")
    if len(files) > 1:
        print(f"Warning: {path} contains {len(files)} rasters; using {files[0].name}")
    return files[0], tmp


def parse_args():
    p = argparse.ArgumentParser(description="Aggregate IUCN Species Richness and Rarity-Weighted Richness rasters to MotherWorld regions.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--richness", type=Path)
    p.add_argument("--rarity", type=Path)
    p.add_argument("--threatened-rarity", type=Path)
    p.add_argument("--kind", action="append", choices=["land", "marine", "lakes"], default=[])
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--supersample", type=int, default=2)
    return p.parse_args()


def main():
    args = parse_args()
    if not any((args.richness, args.rarity, args.threatened_rarity)):
        raise SystemExit("Provide at least one of --richness, --rarity, --threatened-rarity.")
    repo = args.repo.resolve()
    kinds = args.kind or ["land"]
    regions = load_regions(repo, kinds)
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]
    temps = []
    sources = {}
    try:
        for key, input_path in (
            ("speciesRichness", args.richness),
            ("rarityWeightedRichness", args.rarity),
            ("threatenedRarityWeightedRichness", args.threatened_rarity),
        ):
            raster, tmp = one_raster(input_path)
            if tmp:
                temps.append(tmp)
            if raster:
                sources[key] = raster
        for pos, region in enumerate(regions, 1):
            metrics = {}
            for key, raster in sources.items():
                stats = zonal_raster_stats(raster, region, supersample=args.supersample)
                if stats:
                    metrics[key] = {
                        "areaWeightedMean": stats["areaWeightedMean"],
                        "p90": stats["p90"],
                        "fractionWeightedCellSum": stats["fractionWeightedCellSum"],
                        "validCellCount": stats["validCellCount"],
                    }
            if not metrics:
                continue
            write_provider_region(
                repo,
                PROVIDER,
                region.kind,
                region.region_id,
                {
                    "provider": PROVIDER,
                    "regionId": region.region_id,
                    "regionKind": region.kind,
                    "generatedAt": utc_now_iso(),
                    "metrics": metrics,
                    "source": {
                        "label": "IUCN Red List Species Richness / Rarity-Weighted Richness spatial downloads",
                        "license": "IUCN Red List spatial data terms — non-commercial use",
                        "licenseClass": "restricted_noncommercial",
                        "removableProvider": True,
                        "redListVersion": "user-supplied download",
                    },
                },
            )
            print(f"[{pos}/{len(regions)}] {region.region_id}: {', '.join(metrics)}")
    finally:
        for tmp in temps:
            tmp.cleanup()


if __name__ == "__main__":
    main()
