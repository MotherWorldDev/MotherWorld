#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(list(map(str, cmd)), check=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Build MotherWorld global Freshwater Stability providers and family index.")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--grace", type=Path, default=None, help="JPL GRACE/GRACE-FO Mascon CRI NetCDF")
    p.add_argument("--project", default=None, help="Google Cloud project with Earth Engine access")
    p.add_argument("--authenticate", action="store_true")
    p.add_argument("--skip-grace", action="store_true")
    p.add_argument("--skip-soil-moisture", action="store_true")
    p.add_argument("--skip-surface-water", action="store_true")
    p.add_argument("--index-only", action="store_true", help="Do not rebuild providers; merge whatever provider outputs already exist.")
    args = p.parse_args()
    repo = args.repo.resolve()
    scripts = Path(__file__).resolve().parent
    py = sys.executable

    if not args.index_only:
        if args.grace and not args.skip_grace:
            run([py, scripts / "build_grace_freshwater_storage.py", "--repo", repo, "--input", args.grace])
        elif not args.skip_grace:
            print("GRACE input not supplied; reusing existing GRACE component if present.")
        if args.project and not args.skip_soil_moisture:
            cmd = [py, scripts / "build_freshwater_soil_moisture_ee.py", "--repo", repo, "--project", args.project]
            if args.authenticate:
                cmd.append("--authenticate")
            run(cmd)
        elif not args.skip_soil_moisture:
            print("Earth Engine project not supplied; reusing existing soil-moisture component if present.")
        if args.project and not args.skip_surface_water:
            cmd = [py, scripts / "build_freshwater_surface_water_ee.py", "--repo", repo, "--project", args.project]
            if args.authenticate:
                cmd.append("--authenticate")
            run(cmd)
        elif not args.skip_surface_water:
            print("Earth Engine project not supplied; reusing existing surface-water component if present.")

    run([py, scripts / "build_freshwater_stability_index.py", "--repo", repo])


if __name__ == "__main__":
    main()
