#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize

from biodiversity_common import WGS84, load_regions, project_geometry, utc_now_iso, write_provider_region

PROVIDER = "phylacine"


def parse_args():
    p = argparse.ArgumentParser(description="Build current-vs-present-natural mammal faunal retention from PHYLACINE range rasters.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--phylacine-root", type=Path, required=True, help="PHYLACINE repo/Data/Ranges or equivalent folder containing Current and Present_natural.")
    p.add_argument("--kind", action="append", choices=["land", "marine", "lakes"], default=[])
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--all-touched", action="store_true", default=True)
    p.add_argument("--lost-species-limit", type=int, default=100)
    return p.parse_args()


def find_range_dirs(root: Path):
    root = root.resolve()
    candidates = [root, root / "Data/Ranges", root / "Ranges"]
    for c in candidates:
        current = c / "Current"
        natural = c / "Present_natural"
        if current.is_dir() and natural.is_dir():
            return current, natural
    raise FileNotFoundError("Could not find Current/ and Present_natural/ under PHYLACINE root")


def cell_area_grid(ds):
    transform = ds.transform
    h, w = ds.height, ds.width
    if ds.crs and getattr(ds.crs, "is_geographic", False) and abs(transform.b) < 1e-12 and abs(transform.d) < 1e-12:
        R = 6_371_008.8
        dlon = abs(transform.a)
        dlat = abs(transform.e)
        rows = np.arange(h)
        lat = transform.f + (rows + 0.5) * transform.e
        north = np.deg2rad(np.clip(lat + dlat / 2, -90, 90))
        south = np.deg2rad(np.clip(lat - dlat / 2, -90, 90))
        row_area = R**2 * math.radians(dlon) * np.abs(np.sin(north) - np.sin(south))
        return np.repeat(row_area[:, None], w, axis=1)
    px = abs(transform.a * transform.e - transform.b * transform.d)
    return np.full((h, w), px if px > 0 else 1.0, dtype=np.float64)


def main():
    args = parse_args()
    repo = args.repo.resolve()
    kinds = args.kind or ["land"]
    regions = load_regions(repo, kinds)
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]
    current_dir, natural_dir = find_range_dirs(args.phylacine_root)
    current_files = {p.stem: p for p in current_dir.glob("*.tif")}
    natural_files = {p.stem: p for p in natural_dir.glob("*.tif")}
    species = sorted(set(current_files) & set(natural_files))
    if not species:
        raise SystemExit("No paired PHYLACINE .tif range rasters found.")

    with rasterio.open(current_files[species[0]]) as template:
        crs = template.crs
        shape = (template.height, template.width)
        transform = template.transform
        labels = np.zeros(shape, dtype=np.int32)
        shapes = []
        region_by_label = {}
        for label, region in enumerate(regions, 1):
            geom = project_geometry(region.geometry, WGS84, crs)
            shapes.append((geom, label))
            region_by_label[label] = region
        labels = rasterize(shapes, out_shape=shape, transform=transform, fill=0, dtype="int32", all_touched=args.all_touched)
        area_grid = cell_area_grid(template)

    stats = {
        r.region_id: {
            "naturalSpecies": 0,
            "currentSpecies": 0,
            "naturalOccupancyArea": 0.0,
            "currentOccupancyArea": 0.0,
            "locallyLost": [],
            "locallyLostCount": 0,
        }
        for r in regions
    }
    max_label = len(regions)
    label_flat = labels.ravel()
    area_flat = area_grid.ravel()

    for i, name in enumerate(species, 1):
        with rasterio.open(current_files[name]) as csrc, rasterio.open(natural_files[name]) as nsrc:
            if (csrc.height, csrc.width) != shape or csrc.transform != transform or nsrc.transform != transform:
                raise RuntimeError(f"PHYLACINE raster grid mismatch for {name}")
            cur = csrc.read(1, masked=True).filled(0) > 0
            nat = nsrc.read(1, masked=True).filled(0) > 0
        cur_flat = cur.ravel() & (label_flat > 0)
        nat_flat = nat.ravel() & (label_flat > 0)
        cur_count = np.bincount(label_flat[cur_flat], minlength=max_label + 1)
        nat_count = np.bincount(label_flat[nat_flat], minlength=max_label + 1)
        cur_area = np.bincount(label_flat[cur_flat], weights=area_flat[cur_flat], minlength=max_label + 1)
        nat_area = np.bincount(label_flat[nat_flat], weights=area_flat[nat_flat], minlength=max_label + 1)
        for label in range(1, max_label + 1):
            rid = region_by_label[label].region_id
            s = stats[rid]
            if nat_count[label] > 0:
                s["naturalSpecies"] += 1
                s["naturalOccupancyArea"] += float(nat_area[label])
            if cur_count[label] > 0:
                s["currentSpecies"] += 1
                s["currentOccupancyArea"] += float(cur_area[label])
            if nat_count[label] > 0 and cur_count[label] == 0:
                s["locallyLostCount"] += 1
                if len(s["locallyLost"]) < args.lost_species_limit:
                    s["locallyLost"].append(name.replace("_", " "))
        if i % 250 == 0 or i == len(species):
            print(f"PHYLACINE species {i:,}/{len(species):,}")

    for region in regions:
        s = stats[region.region_id]
        natural = s["naturalSpecies"]
        current = s["currentSpecies"]
        retention = 100.0 * current / natural if natural else None
        occupancy = 100.0 * s["currentOccupancyArea"] / s["naturalOccupancyArea"] if s["naturalOccupancyArea"] > 0 else None
        payload = {
            "provider": PROVIDER,
            "regionId": region.region_id,
            "regionKind": region.kind,
            "generatedAt": utc_now_iso(),
            "metrics": {
                "presentNaturalMammalSpeciesCount": natural,
                "currentMammalSpeciesCount": current,
                "locallyLostMammalSpeciesCount": s["locallyLostCount"],
                "mammalFaunalRetentionPct": retention,
                "mammalRangeOccupancyRetentionPct": occupancy,
                "exampleLocallyLostSpecies": s["locallyLost"],
            },
            "source": {
                "label": "PHYLACINE 1.2.1 — current and present-natural mammal ranges",
                "license": "CC0",
                "licenseClass": "open",
                "removableProvider": False,
                "repository": "https://github.com/MegaPast2Future/PHYLACINE_1.2",
                "methodNote": "Region presence is evaluated on the native PHYLACINE raster grid; present-natural is counterfactual, not a fossil map.",
            },
        }
        write_provider_region(repo, PROVIDER, region.kind, region.region_id, payload)
    print(f"Done. Wrote PHYLACINE metrics for {len(regions)} regions.")


if __name__ == "__main__":
    main()
