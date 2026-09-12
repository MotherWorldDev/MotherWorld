#!/usr/bin/env python
"""Build OSM waste-site summaries with durable PBF-block checkpoints.

This writes local staging outputs only; deployment is a separate action.
Legacy continental count files are never treated as verified block checkpoints.
"""
from __future__ import annotations
import argparse
import math
import re
from pathlib import Path
from osm_checkpoint import PARSER_VERSION, checkpointed_process_pbf
from regional_diagnostics_adapters import (
    canonical_region_gdf, output_root, provider_fragment_path, write_json,
    merge_land_pollution_provider, recompute_land_peer_percentiles,
)
SITE_TYPES = ("landfill", "waste_disposal", "waste_transfer_station")


def _load_or_process(path, root, regions, *, repo, checkpoint_root=None, checkpoint_blocks=8, verify_input=False):
    checkpoint_root = checkpoint_root or provider_fragment_path(root, "land-pollution/osm-waste/checkpoints")
    partial = checkpointed_process_pbf(
        path, regions, checkpoint_root=checkpoint_root,
        temp_root=repo / ".cache" / "tmp",
        checkpoint_blocks=checkpoint_blocks, verify_input=verify_input,
    )
    # The complete result is also useful to inspect outside SQLite. Never
    # use a legacy partial to bypass the core's source/geometry validation.
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.name) + ".json"
    dest = provider_fragment_path(root, f"land-pollution/osm-waste/partials/{name}")
    write_json(dest, partial, compact=True)
    print(f"Completed {path.name}: {partial['candidateSiteCount']:,} candidates; {partial['mappedRegionCount']:,} mapped regions", flush=True)
    return partial


def main():
    parser = argparse.ArgumentParser(description="Stream OpenStreetMap landfill/waste features by canonical MotherWorld land ecoregion.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, default=None, help="v8 build root for deployable and provider-fragment output")
    parser.add_argument("--checkpoint-blocks", type=int, default=8)
    parser.add_argument("--checkpoint-root", type=Path)
    parser.add_argument("--verify-input", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    root = output_root(repo, args.output_root)
    inputs = [path.resolve() for path in args.input]
    for path in inputs:
        if not path.exists() or not path.is_file():
            raise SystemExit(f"OSM input does not exist or is not a file: {path}")
        if path.name.lower().endswith(".part") or path.stat().st_size <= 0:
            raise SystemExit(f"OSM input is incomplete or zero-byte; refusing to touch it: {path}")

    regions = canonical_region_gdf(repo, "land")
    if regions.empty:
        raise SystemExit("No canonical land regions available for OSM overlay")
    partials = [_load_or_process(path, root, regions, repo=repo, checkpoint_root=args.checkpoint_root, checkpoint_blocks=args.checkpoint_blocks, verify_input=args.verify_input) for path in inputs]

    merged: dict[str, dict[str, float]] = {}
    for partial in partials:
        for region_index, values in (partial.get("countsByRegionIndex") or {}).items():
            # The OSM worker stores the stable GeoDataFrame row index. It is
            # converted to the canonical ID only after all partials are read.
            try:
                region_id = str(regions.iloc[int(region_index)]["regionId"])
            except (IndexError, TypeError, ValueError):
                continue
            target = merged.setdefault(
                region_id,
                {"landfill": 0, "waste_disposal": 0, "waste_transfer_station": 0, "landfillAreaKm2": 0.0},
            )
            for key in SITE_TYPES:
                target[key] += int(values.get(key, 0))
            target["landfillAreaKm2"] += float(values.get("landfillAreaKm2", 0.0))

    updates = {}
    for region_id, values in sorted(merged.items()):
        meta = regions.loc[regions["regionId"] == region_id].iloc[0]
        try:
            area_candidate = float(meta["areaKm2"])
        except (TypeError, ValueError):
            area_candidate = 0.0
        area = area_candidate if math.isfinite(area_candidate) else 0.0
        total_sites = sum(int(values[key]) for key in SITE_TYPES)
        if total_sites <= 0 and values["landfillAreaKm2"] <= 0:
            continue
        update = {
            "label": "OpenStreetMap waste sites",
            "coverage": "Volunteer-mapped features; completeness varies strongly by country and mapper activity. Mapped absence is not evidence of no site.",
            "metrics": {
                "mappedSiteCount": total_sites,
                "mappedLandfillCount": int(values["landfill"]),
                "mappedWasteDisposalCount": int(values["waste_disposal"]),
                "mappedTransferStationCount": int(values["waste_transfer_station"]),
                "mappedLandfillAreaKm2": round(values["landfillAreaKm2"], 4),
                "mappedSiteDensityPer1000Km2": total_sites / area * 1000 if area > 0 else None,
                "mappedLandfillPctOfRegion": values["landfillAreaKm2"] / area * 100 if area > 0 else None,
            },
        }
        # The corrected root adapter requires producer-side geometry identity
        # for maintained land overrides.  Keep this claim private until the
        # adapter validates it against the geometry used during this stream.
        if meta.get("analysisGeometry") is not None:
            update["_analysisGeometry"] = meta["analysisGeometry"]
            update["_analysisGeometryCacheIdentity"] = meta.get("analysisGeometryCacheIdentity")
        updates[region_id] = update

    source_entry = {
        "id": "osm-waste",
        "label": "OpenStreetMap waste-site mapping",
        "license": "ODbL 1.0",
        "url": "https://www.openstreetmap.org/",
        "caveat": "OSM is not an authoritative waste-site registry and mapped absence is not evidence of no site.",
        "parser": PARSER_VERSION,
        "inputs": [partial.get("input") for partial in partials],
        "completedInputCount": len(partials),
        "mappedRegionCount": len(updates),
    }
    merge_land_pollution_provider(repo, root, updates, source_entry)
    recompute_land_peer_percentiles(root)
    print(f"Updated {len(updates):,} canonical regions from {len(inputs):,} streamed OSM PBFs.")


if __name__ == "__main__":
    main()
