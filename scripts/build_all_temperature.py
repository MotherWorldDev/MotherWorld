#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description="Run MotherWorld temperature distribution builders.")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--years", default="1991:2020")
    p.add_argument("--land-source", choices=("era5-land", "era5"), default="era5-land")
    p.add_argument("--skip-land", action="store_true")
    p.add_argument("--skip-marine", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    scripts = Path(__file__).resolve().parent

    if not args.skip_land:
        cmd = [sys.executable, str(scripts / "build_land_temperature.py"), "--repo", str(args.repo), "--years", args.years, "--source", args.land_source]
        if args.force:
            cmd.append("--force")
        subprocess.run(cmd, check=True)

    if not args.skip_marine:
        cmd = [sys.executable, str(scripts / "build_marine_temperature.py"), "--repo", str(args.repo), "--years", args.years]
        if args.force:
            cmd.append("--force")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
