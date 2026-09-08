#!/usr/bin/env python3
"""Download C3S annual land-cover classifications with resumable F: staging.

The CDS response may be either a NetCDF file or a ZIP containing one NetCDF
member.  A request sidecar is written beside every target so an interrupted
or stale partial transfer cannot be mistaken for a different request.
"""

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
from netCDF4 import Dataset, num2date


DATASET = "satellite-land-cover"
DEFAULT_CREDENTIALS = Path(r"F:\BiomeSummary\CpernicusToken\.cdsapirc")
SUPPORTED_VARIABLES = ("lccs_class", "land_cover", "classification", "class")


def parse_years(value: str) -> list[int]:
    if ":" in value:
        start, end = (int(part) for part in value.split(":", 1))
        return list(range(start, end + 1))
    return sorted({int(part) for part in value.split(",") if part.strip()})


def version_for(year: int) -> str:
    return "v2_0_7cds" if year <= 2015 else "v2_1_1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_netcdf(path: Path, expected_year: int | None = None) -> tuple[bool, str]:
    if not path.exists() or path.stat().st_size <= 0:
        return False, "missing-or-empty"
    try:
        with Dataset(path, "r") as dataset:
            if not any(name in dataset.variables for name in SUPPORTED_VARIABLES):
                return False, "no-land-cover-variable"
            if expected_year is not None:
                time_name = next((name for name in ("time", "valid_time", "date") if name in dataset.variables), None)
                if time_name is None:
                    return False, "missing-time-variable"
                variable = dataset.variables[time_name]
                units = getattr(variable, "units", None)
                if not units:
                    return False, f"missing-time-units:{time_name}"
                calendar = getattr(variable, "calendar", "standard")
                decoded = num2date(variable[:], units=units, calendar=calendar, only_use_cftime_datetimes=False, only_use_python_datetimes=False)
                years = {int(value.year) for value in decoded}
                if years != {expected_year}:
                    return False, f"wrong-year:{sorted(years)} expected {expected_year}"
            return True, "netcdf-readable"
    except Exception as exc:
        return False, f"netcdf-invalid:{type(exc).__name__}"


def extract_if_zip(source: Path, target: Path) -> None:
    if not zipfile.is_zipfile(source):
        os.replace(source, target)
        return
    with zipfile.ZipFile(source) as archive:
        members = [member for member in archive.infolist() if not member.is_dir() and member.filename.lower().endswith((".nc", ".nc4"))]
        if len(members) != 1:
            raise RuntimeError(f"expected one NetCDF member, found {len(members)}")
        extracting = target.with_suffix(target.suffix + ".extracting")
        with archive.open(members[0]) as source_handle, extracting.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=8 * 1024 * 1024)
        os.replace(extracting, target)
    source.unlink()


def make_client(credentials: Path):
    os.environ["CDSAPI_RC"] = str(credentials.resolve())
    return cdsapi.Client(quiet=True, timeout=180, retry_max=8, wait_until_complete=True)


def request_for(year: int, area: list[float]) -> dict:
    return {"variable": "all", "year": [str(year)], "version": [version_for(year)], "area": area}


def request_fingerprint(request: dict) -> str:
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def metadata_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".request.json")


def partial_metadata_path(part: Path) -> Path:
    return part.with_suffix(part.suffix + ".request.json")


def metadata_matches(path: Path, fingerprint: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("fingerprint") == fingerprint and payload.get("dataset") == DATASET
    except Exception:
        return False


def completed_metadata_matches(path: Path, target: Path, fingerprint: str) -> bool:
    if not target.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return (
            payload.get("dataset") == DATASET
            and payload.get("fingerprint") == fingerprint
            and payload.get("complete") is True
            and payload.get("targetBytes") == target.stat().st_size
            and payload.get("targetSha256") == sha256(target)
        )
    except Exception:
        return False


def write_metadata(path: Path, request: dict, fingerprint: str, expected: int | None, target: Path | None = None) -> None:
    payload = {"dataset": DATASET, "request": request, "fingerprint": fingerprint, "expectedTransportBytes": expected}
    if target is not None:
        payload.update({"complete": True, "targetBytes": target.stat().st_size, "targetSha256": sha256(target)})
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def ensure_free_space(path: Path, minimum_bytes: int, context: str) -> None:
    if minimum_bytes <= 0:
        return
    free = shutil.disk_usage(path).free
    if free < minimum_bytes:
        raise RuntimeError(f"Only {free / (1024**3):.1f} GiB free before {context}; required {minimum_bytes / (1024**3):.1f} GiB")


def estimated_unpacked_bytes(source: Path) -> int:
    if not zipfile.is_zipfile(source):
        return 0
    with zipfile.ZipFile(source) as archive:
        members = [member for member in archive.infolist() if not member.is_dir() and member.filename.lower().endswith((".nc", ".nc4"))]
        if len(members) != 1:
            raise RuntimeError(f"expected one NetCDF member, found {len(members)}")
        return int(members[0].file_size)


def download_one(year: int, output_dir: Path, credentials: Path, area: list[float], min_free_bytes: int = 0) -> dict:
    target = output_dir / f"c3s_land_cover_{year}.nc"
    request = request_for(year, area)
    fingerprint = request_fingerprint(request)
    metadata = metadata_path(target)
    valid, validation = valid_netcdf(target, expected_year=year)
    if valid and completed_metadata_matches(metadata, target, fingerprint):
        return {"year": year, "status": "exists-valid", "path": str(target), "bytes": target.stat().st_size, "sha256": sha256(target), "version": version_for(year), "validation": validation, "requestFingerprint": fingerprint}

    ensure_free_space(output_dir, min_free_bytes, f"starting C3S {year}")
    client = make_client(credentials)
    print(f"C3S land cover {year}: submitting {version_for(year)}", flush=True)
    result = client.retrieve(DATASET, request)
    expected = int(result.content_length) if getattr(result, "content_length", None) is not None else None
    part = target.with_suffix(target.suffix + ".part")
    part_metadata = partial_metadata_path(part)
    if part.exists() and (not metadata_matches(part_metadata, fingerprint) or (expected is not None and part.stat().st_size >= expected)):
        part.unlink()
        part_metadata.unlink(missing_ok=True)
    write_metadata(part_metadata, request, fingerprint, expected)
    if expected is not None:
        ensure_free_space(output_dir, min_free_bytes + expected, f"transferring C3S {year}")
    multiurl.download(result.location, target=str(part), resume_transfers=True, stream=True, maximum_retries=8)
    transport_bytes = part.stat().st_size
    if expected is not None and transport_bytes != expected:
        raise RuntimeError(f"C3S {year} size mismatch: {transport_bytes} != {expected}")
    staged = target.with_suffix(target.suffix + ".staged")
    try:
        unpacked_bytes = estimated_unpacked_bytes(part)
        ensure_free_space(output_dir, min_free_bytes + transport_bytes + unpacked_bytes, f"extracting C3S {year}")
        extract_if_zip(part, staged)
        valid, validation = valid_netcdf(staged, expected_year=year)
        if not valid:
            raise RuntimeError(f"C3S {year} validation failed: {validation}")
        # Do not let a failed request promote its fingerprint over the last
        # completed target.  Invalidate the completed sidecar only once the
        # replacement has passed all validation, then promote atomically.
        metadata.unlink(missing_ok=True)
        os.replace(staged, target)
        write_metadata(metadata, request, fingerprint, expected, target=target)
        part_metadata.unlink(missing_ok=True)
    except Exception:
        part.unlink(missing_ok=True)
        staged.unlink(missing_ok=True)
        part_metadata.unlink(missing_ok=True)
        raise
    return {
        "year": year,
        "status": "downloaded",
        "path": str(target),
        "bytes": target.stat().st_size,
        "transportBytes": transport_bytes,
        "sha256": sha256(target),
        "checksum": (getattr(result, "asset", {}) or {}).get("file:checksum"),
        "version": version_for(year),
        "validation": validation,
        "requestFingerprint": fingerprint,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--years", default="1993:2022")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-free-gib", type=float, default=40.0)
    parser.add_argument("--area", type=float, nargs=4, default=[90.0, -180.0, -90.0, 180.0], metavar=("N", "W", "S", "E"))
    args = parser.parse_args()
    years = parse_years(args.years)
    if not years or min(years) < 1992 or max(years) > 2022:
        raise SystemExit("C3S satellite-land-cover catalog supports the requested v8 range 1992–2022")
    if not args.credentials.exists():
        raise SystemExit(f"Missing CDS credential file: {args.credentials}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(args.output_dir).free / (1024**3)
    if free < args.min_free_gib:
        raise SystemExit(f"Only {free:.1f} GiB free on target volume; refusing C3S download below {args.min_free_gib:.1f} GiB")
    workers = max(1, min(int(args.workers), 2))
    min_free_bytes = int(args.min_free_gib * (1024**3))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_one, year, args.output_dir.resolve(), args.credentials.resolve(), list(args.area), min_free_bytes): year for year in years}
        for future in as_completed(futures):
            year = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"year": year, "status": "error", "error": f"{type(exc).__name__}: {exc}", "version": version_for(year)}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result["status"] == "error":
                for pending in futures:
                    pending.cancel()
                break
    results.sort(key=lambda row: row["year"])
    report = {"generatedAt": datetime.now(timezone.utc).isoformat(), "dataset": DATASET, "years": years, "area": args.area, "workers": workers, "outputDir": str(args.output_dir.resolve()), "results": results}
    report_path = args.output_dir / "c3s_land_cover_download_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if any(row["status"] == "error" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
