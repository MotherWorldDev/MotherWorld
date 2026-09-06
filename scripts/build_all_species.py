#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from species_common import project_root_from


def parse_args() -> argparse.Namespace:
    root = project_root_from(__file__)
    p = argparse.ArgumentParser(description="Build MotherWorld species inventories for land, lakes and marine regions.")
    p.add_argument("--taxonomy-db", type=Path, default=root / ".cache/motherworld/gbif-species-taxonomy.sqlite")
    p.add_argument("--output-dir", type=Path, default=root / "frontend/public/data/species")
    p.add_argument("--skip-gbif", action="store_true")
    p.add_argument("--skip-obis", action="store_true")
    p.add_argument("--runtime-geometry", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--year", default=None)
    return p.parse_args()


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    if not args.skip_gbif:
        cmd = [
            sys.executable,
            str(script_dir / "gbif_build_region_species.py"),
            "--kind",
            "both",
            "--taxonomy-db",
            str(args.taxonomy_db),
            "--output-dir",
            str(args.output_dir),
        ]
        if args.force:
            cmd.append("--force")
        if args.year:
            cmd += ["--year", args.year]
        if args.runtime_geometry:
            cmd.append("--runtime-geometry")
        run(cmd)
    if not args.skip_obis:
        cmd = [
            sys.executable,
            str(script_dir / "obis_build_region_species.py"),
            "--output-dir",
            str(args.output_dir),
        ]
        if args.force:
            cmd.append("--force")
        run(cmd)


if __name__ == "__main__":
    main()
