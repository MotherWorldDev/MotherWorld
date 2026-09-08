#!/usr/bin/env python3
"""Resumable CDS downloader for the ERA5-Land monthly soil-moisture backbone."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import cdsapi
import multiurl
from netCDF4 import Dataset


DATASET = "reanalysis-era5-land-monthly-means"
VARIABLES = [
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
]
DEFAULT_CREDENTIALS = Path(r"F:\BiomeSummary\CpernicusToken\.cdsapirc")


def parse_years(value: str) -> list[int]:
    if ":" in value:
        start, end = (int(part) for part in value.split(":", 1))
        return list(range(start, end + 1))
    return sorted({int(part) for part in value.split(",") if part.strip()})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_netcdf(path: Path) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size <= 0:
        return False, "missing-or-empty"
    try:
        with Dataset(path, "r") as dataset:
            missing = [name for name in VARIABLES if name not in dataset.variables]
            if missing:
                return False, f"missing-variables:{','.join(missing)}"
        return True, "netcdf-readable"
    except Exception as exc:
        return False, f"netcdf-invalid:{type(exc).__name__}"


def make_client(credentials: Path):
    os.environ["CDSAPI_RC"] = str(credentials.resolve())
    return cdsapi.Client(quiet=True, timeout=180, retry_max=8, wait_until_complete=True)


def extract_if_zip(source: Path, target: Path) -> None:
    if not zipfile.is_zipfile(source):
        os.replace(source, target)
        return
    with zipfile.ZipFile(source) as archive:
        members = [member for member in archive.infolist() if not member.is_dir() and member.filename.lower().endswith((".nc", ".nc4"))]
        if len(members) != 1:
            raise RuntimeError(f"expected one NetCDF member, found {len(members)}")
        extracted = target.with_suffix(target.suffix + ".extracting")
        with archive.open(members[0]) as source_handle, extracted.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=8 * 1024 * 1024)
        os.replace(extracted, target)
    source.unlink()


def download_one(year: int, output_dir: Path, credentials: Path, area: list[float]) -> dict:
    target = output_dir / f"era5_land_monthly_soil_moisture_{year}.nc"
    valid, validation = valid_netcdf(target)
    if valid:
        return {"year": year, "status": "exists-valid", "path": str(target), "bytes": target.stat().st_size, "sha256": sha256(target), "validation": validation}

    client = make_client(credentials)
    request = {
        "product_type": "monthly_averaged_reanalysis",
        "variable": VARIABLES,
        "year": [str(year)],
        "month": [f"{month:02d}" for month in range(1, 13)],
        "time": ["00:00"],
        "area": area,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }
    print(f"ERA5-Land monthly {year}: submitting", flush=True)
    result = client.retrieve(DATASET, request)
    expected = int(result.content_length)
    part = target.with_suffix(target.suffix + ".part")
    multiurl.download(result.location, target=str(part), resume_transfers=True, stream=True, maximum_retries=8)
    if part.stat().st_size != expected:
        raise RuntimeError(f"{year} size mismatch: {part.stat().st_size} != {expected}")
    staged = target.with_suffix(target.suffix + ".staged")
    extract_if_zip(part, staged)
    valid, validation = valid_netcdf(staged)
    if not valid:
        staged.unlink(missing_ok=True)
        raise RuntimeError(f"{year} validation failed: {validation}")
    os.replace(staged, target)
    return {
        "year": year,
        "status": "downloaded",
        "path": str(target),
        "bytes": target.stat().st_size,
        "transportBytes": expected,
        "sha256": sha256(target),
        "checksum": result.asset.get("file:checksum") if getattr(result, "asset", None) else None,
        "validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--years", default="1991:2025")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-free-gib", type=float, default=40.0)
    parser.add_argument("--area", type=float, nargs=4, default=[90.0, -180.0, -60.0, 180.0], metavar=("N", "W", "S", "E"))
    args = parser.parse_args()
    years = parse_years(args.years)
    if not years or min(years) < 1950 or max(years) > datetime.now(timezone.utc).year:
        raise SystemExit("ERA5-Land monthly catalog supports 1950 through the current year")
    if not args.credentials.exists():
        raise SystemExit(f"Missing CDS credential file: {args.credentials}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(args.output_dir).free / (1024**3)
    if free < args.min_free_gib:
        raise SystemExit(f"Only {free:.1f} GiB free; refusing below {args.min_free_gib:.1f} GiB")
    workers = max(1, min(int(args.workers), 2))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, year, args.output_dir.resolve(), args.credentials.resolve(), list(args.area)): year for year in years}
        for future in as_completed(futures):
            year = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"year": year, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result["status"] == "error":
                for pending in futures:
                    pending.cancel()
                break
    results.sort(key=lambda item: item["year"])
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "variables": VARIABLES,
        "years": years,
        "area": args.area,
        "workers": workers,
        "outputDir": str(args.output_dir.resolve()),
        "results": results,
    }
    (args.output_dir / "era5_land_monthly_download_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if any(item["status"] == "error" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
