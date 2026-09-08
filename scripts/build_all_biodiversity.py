#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd):
    print("+", " ".join(str(x) for x in cmd))
    subprocess.run([str(x) for x in cmd], check=True)


def main():
    p = argparse.ArgumentParser(description="Orchestrate whichever MotherWorld biodiversity providers are available locally, then merge them.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--phylacine-root", type=Path)
    p.add_argument("--bii", type=Path)
    p.add_argument("--iucn-richness", type=Path)
    p.add_argument("--iucn-rarity", type=Path)
    p.add_argument("--iucn-threatened-rarity", type=Path)
    p.add_argument("--iucn-ranges", type=Path, action="append", default=[])
    p.add_argument("--iucn-categories", type=Path)
    p.add_argument("--exclude", action="append", default=[])
    args = p.parse_args()
    scripts = Path(__file__).resolve().parent
    repo = args.repo.resolve()

    if args.phylacine_root:
        run([sys.executable, scripts / "build_phylacine_metrics.py", "--repo", repo, "--phylacine-root", args.phylacine_root])
    if args.bii:
        run([sys.executable, scripts / "build_bii_metrics.py", "--repo", repo, "--input", args.bii])
    if any((args.iucn_richness, args.iucn_rarity, args.iucn_threatened_rarity)):
        cmd = [sys.executable, scripts / "build_iucn_raster_metrics.py", "--repo", repo]
        if args.iucn_richness:
            cmd += ["--richness", args.iucn_richness]
        if args.iucn_rarity:
            cmd += ["--rarity", args.iucn_rarity]
        if args.iucn_threatened_rarity:
            cmd += ["--threatened-rarity", args.iucn_threatened_rarity]
        run(cmd)
    if args.iucn_ranges:
        cmd = [sys.executable, scripts / "build_iucn_range_metrics.py", "--repo", repo]
        for path in args.iucn_ranges:
            cmd += ["--ranges", path]
        if args.iucn_categories:
            cmd += ["--categories", args.iucn_categories]
        run(cmd)

    cmd = [sys.executable, scripts / "merge_biodiversity_metrics.py", "--repo", repo]
    for item in args.exclude:
        cmd += ["--exclude", item]
    run(cmd)


if __name__ == "__main__":
    main()
