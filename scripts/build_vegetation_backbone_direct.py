#!/usr/bin/env python
"""Build the vegetation Earth Health backbone from NOAA's direct CDR files.

The canonical source path here is NOAA NCEI's anonymous HTTPS directory of
daily AVHRR v5 / VIIRS v1 NetCDF files.  Earth Engine is intentionally not
used.  Files are downloaded one at a time, reduced to a configurable working
grid, and removed after processing unless ``--retain-raw`` is supplied.  A
per-year annual-median checkpoint makes a rerun reuse completed work.
"""

from __future__ import annotations

import argparse
import calendar
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import warnings
from urllib.parse import urljoin

import requests

from backbone_common import HISTORY_START_YEAR, family_payload
from direct_provider_common import (
    ProviderAccessError,
    date_range_years,
    download_resumable,
    html_links,
    output_family_path,
    raise_for_provider_response,
    write_json,
)


NOAA_BASE_URL = "https://www.ncei.noaa.gov/data/land-normalized-difference-vegetation-index/access/"
AVHRR_PREFIX = "AVHRR-Land_v005_"
VIIRS_PREFIX = "VIIRS-Land_v001_"
AVHRR_BAD_QA_MASK = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 6) | (1 << 8) | (1 << 9)
DEFAULT_RAW_RESERVE_BYTES = 40 * 1024**3
DEFAULT_MAX_OWNED_RAW_BYTES = 32 * 1024**3
DEFAULT_MAX_SINGLE_RAW_BYTES = 128 * 1024**2


def sensor_for_year(year: int) -> str:
    return "AVHRR" if year <= 2013 else "VIIRS"

def sha256_file(path: Path, chunk_size: int = 4 * 1024**2) -> str:
    """Hash the complete source file before native NetCDF reduction."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _ownership_path(raw_root: Path) -> Path:
    return raw_root / "vegetation" / "noaa_cdr" / "stream-ownership.jsonl"


def _load_owned_records(path: Path) -> dict[str, dict]:
    """Load the worker ledger without claiming pre-existing staged files."""

    records: dict[str, dict] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        file_path = record.get("path")
        if not file_path:
            continue
        if record.get("status") == "deleted":
            records.pop(file_path, None)
        elif record.get("status") in {"downloaded", "retained"}:
            records[file_path] = record
    return records


def _append_ownership(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _owned_raw_bytes(raw_root: Path, owned: dict[str, dict]) -> int:
    """Count worker-owned files and resumable parts for the retention cap."""

    total = 0
    counted_parts: set[Path] = set()
    for file_path in owned:
        path = Path(file_path)
        if path.exists():
            total += path.stat().st_size
        part = path.with_name(f".{path.name}.part")
        if part.exists():
            total += part.stat().st_size
            counted_parts.add(part)
    part_root = raw_root / "vegetation" / "noaa_cdr"
    if part_root.exists():
        for part in part_root.rglob(".*.part"):
            if part.is_file() and part not in counted_parts:
                total += part.stat().st_size
    return total


def _check_storage_budget(
    *,
    raw_root: Path,
    owned: dict[str, dict],
    incoming_bytes: int,
    max_owned_raw_bytes: int,
    reserve_bytes: int,
) -> None:
    """Stop before a download can consume the reserved F: capacity."""

    if incoming_bytes <= 0:
        raise ProviderAccessError("NOAA source did not provide a positive file-size estimate")
    owned_bytes = _owned_raw_bytes(raw_root, owned)
    if owned_bytes + incoming_bytes > max_owned_raw_bytes:
        raise ProviderAccessError(
            "NOAA worker raw retention cap would be exceeded: "
            f"{owned_bytes + incoming_bytes} > {max_owned_raw_bytes} bytes"
        )
    free_bytes = shutil.disk_usage(raw_root).free
    if free_bytes - incoming_bytes < reserve_bytes:
        raise ProviderAccessError(
            "NOAA worker stopped before download to preserve the configured disk reserve: "
            f"free={free_bytes} incoming={incoming_bytes} reserve={reserve_bytes}"
        )


def remote_file_size(session: requests.Session, url: str, timeout: int) -> int | None:
    """Read the official server's size hint used by the raw-retention guard."""

    response = session.head(url, allow_redirects=False, timeout=timeout)
    if response.status_code in {405, 501}:
        return None
    raise_for_provider_response(response, url)
    value = response.headers.get("Content-Length")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

def discover_noaa_daily_urls(year: int, session: requests.Session | None = None) -> list[dict[str, str]]:
    """Read the official NCEI directory listing and return dated NetCDF links."""

    session = session or requests.Session()
    directory = urljoin(NOAA_BASE_URL, f"{year}/")
    response = session.get(directory, timeout=90)
    response.raise_for_status()
    prefix = AVHRR_PREFIX if sensor_for_year(year) == "AVHRR" else VIIRS_PREFIX
    by_day: dict[str, dict[str, str]] = {}
    for href in html_links(response.text):
        name = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if not name.startswith(prefix) or not name.endswith(".nc"):
            continue
        match = re.search(r"_(\d{8})_", name)
        if not match or int(match.group(1)[:4]) != year:
            continue
        day = match.group(1)
        by_day[day] = {"date": day, "name": name, "url": urljoin(directory, name)}
    return [by_day[key] for key in sorted(by_day)]


def quality_mask(ndvi_raw, qa_raw, sensor: str):
    """Return the valid-land mask used before annual aggregation.

    AVHRR follows the bit exclusions in the former EE builder.  VIIRS v1
    exposes a different flag layout, so its cloud, shadow, snow/ice, and
    land-water fields are decoded from the CDR flags instead of applying
    AVHRR bit positions to a different product.
    """

    import numpy as np

    ndvi = np.asarray(ndvi_raw)
    qa = np.asarray(qa_raw).astype(np.uint16, copy=False)
    valid_ndvi = np.isfinite(ndvi) & (ndvi >= -1000) & (ndvi <= 10000)
    if sensor == "AVHRR":
        return valid_ndvi & ((qa & AVHRR_BAD_QA_MASK) == 0)
    if sensor != "VIIRS":
        raise ValueError(f"unsupported NOAA NDVI sensor: {sensor}")
    # VIIRS QA: bits 0-1 cloud state (0/1 are clear or probably clear),
    # bit 2 cloud shadow, bits 3-5 land/water class, bit 15 snow/ice.
    cloud_state = qa & 0b11
    land_water = qa & 0b00111000
    return (
        valid_ndvi
        & (cloud_state <= 1)
        & ((qa & (1 << 2)) == 0)
        & np.isin(land_water, (0, 8))
        & ((qa & (1 << 15)) == 0)
    )


def read_daily_working_grid(path: Path, sensor: str, coarsen: int):
    """Read one NOAA file and return a valid-value mean on the working grid."""

    import numpy as np
    from netCDF4 import Dataset

    if coarsen < 1:
        raise ValueError("coarsen must be at least 1")
    with Dataset(path, "r") as dataset:
        ndvi_variable = dataset.variables["NDVI"]
        qa_variable = dataset.variables["QA"]
        ndvi_variable.set_auto_maskandscale(False)
        qa_variable.set_auto_maskandscale(False)
        ndvi_raw = np.asarray(ndvi_variable[0, :, :])
        qa_raw = np.asarray(qa_variable[0, :, :])
        latitudes = np.asarray(dataset.variables["latitude"][:], dtype=np.float32)
        longitudes = np.asarray(dataset.variables["longitude"][:], dtype=np.float32)

    latitude_indices = np.flatnonzero((latitudes >= -60.0) & (latitudes <= 85.0))
    if latitude_indices.size < coarsen:
        raise ProviderAccessError(f"NOAA file has no usable latitude band: {path}")
    n_lat = latitude_indices.size // coarsen
    n_lon = longitudes.size // coarsen
    latitude_indices = latitude_indices[: n_lat * coarsen]
    longitude_indices = np.arange(n_lon * coarsen)
    ndvi_raw = ndvi_raw[latitude_indices, :][:, longitude_indices]
    qa_raw = qa_raw[latitude_indices, :][:, longitude_indices]
    selected_latitudes = latitudes[latitude_indices]
    good = quality_mask(ndvi_raw, qa_raw, sensor)
    values = np.where(good, ndvi_raw.astype(np.float32) * 0.0001, 0.0)
    pixel_weights = np.cos(np.deg2rad(selected_latitudes)).astype(np.float32)
    weighted_values = values * pixel_weights[:, None]
    weighted_valid = good.astype(np.float32) * pixel_weights[:, None]
    value_blocks = weighted_values.reshape(n_lat, coarsen, n_lon, coarsen)
    valid_blocks = weighted_valid.reshape(n_lat, coarsen, n_lon, coarsen)
    numerator = value_blocks.sum(axis=(1, 3), dtype=np.float32)
    denominator = valid_blocks.sum(axis=(1, 3), dtype=np.float32)
    result = np.full((n_lat, n_lon), np.nan, dtype=np.float32)
    np.divide(numerator, denominator, out=result, where=denominator > 0)
    working_latitudes = selected_latitudes.reshape(n_lat, coarsen).mean(axis=1)
    working_longitudes = longitudes[longitude_indices].reshape(n_lon, coarsen).mean(axis=1)
    return result, working_latitudes, working_longitudes


def _save_npy_atomic(path: Path, array) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    with temporary.open("wb") as stream:
        np.save(stream, array)
    temporary.replace(path)


def _append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _safe_source_path(raw_root: Path, relative_path: str) -> Path:
    root = raw_root.resolve()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ProviderAccessError(f"source path escapes raw root: {relative_path}")
    return candidate


def _grid_fingerprint(grid: dict) -> str:
    """Hash geometry/coarsen metadata while allowing AVHRR→VIIRS sensor changes."""

    payload = {key: value for key, value in grid.items() if key != "sensor"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_annual_checkpoint(
    *,
    annual_path: Path,
    checkpoint_path: Path,
    grid_path: Path,
    year: int,
    sensor: str,
    coarsen: int,
    records: list[dict[str, str]],
) -> bool:
    """Validate a cache before accepting it or deleting its owned source files."""

    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
        if checkpoint.get("year") != year or checkpoint.get("sensor") != sensor:
            return False
        if checkpoint.get("coarsen") != coarsen or grid.get("coarsen") != coarsen:
            return False
        expected_annual = str(annual_path.relative_to(checkpoint_path.parent.parent))
        expected_grid = str(grid_path.relative_to(checkpoint_path.parent.parent))
        if checkpoint.get("annualFile") not in {expected_annual, annual_path.name}:
            return False
        if checkpoint.get("gridFile") not in {expected_grid, grid_path.name}:
            return False
        array = __import__("numpy").load(annual_path, mmap_mode="r", allow_pickle=False)
        if array.ndim != 2 or len(grid.get("latitude", [])) != array.shape[0] or len(grid.get("longitude", [])) != array.shape[1]:
            return False
        if checkpoint.get("gridShape") and list(array.shape) != list(checkpoint["gridShape"]):
            return False
        if checkpoint.get("annualBytes") is not None and annual_path.stat().st_size != int(checkpoint["annualBytes"]):
            return False
        if checkpoint.get("annualSha256") and sha256_file(annual_path) != checkpoint["annualSha256"]:
            return False
        if checkpoint.get("gridSha256") and _grid_fingerprint(grid) != checkpoint["gridSha256"]:
            return False
        source_records = checkpoint.get("records")
        if not isinstance(source_records, list) or len(source_records) != len(records):
            return False
        expected_names = {item["name"] for item in records}
        if {str(item.get("catalogId", "")).split("/", 1)[-1] for item in source_records} != expected_names:
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False

def _cleanup_checkpoint_owned(
    *,
    checkpoint_path: Path,
    raw_root: Path,
    retain_raw: bool,
    owned: dict[str, dict],
    ownership_path: Path,
) -> None:
    """Finish cleanup after a crash between checkpoint and raw deletion."""

    if retain_raw or not checkpoint_path.exists():
        return
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for record in checkpoint.get("records", []):
        if not record.get("owned") or not record.get("file"):
            continue
        source_path = _safe_source_path(raw_root, record["file"])
        source_path.unlink(missing_ok=True)
        key = str(source_path)
        if key in owned:
            _append_ownership(
                ownership_path,
                {"path": key, "status": "deleted", "year": checkpoint.get("year"), "date": record.get("date")},
            )
            owned.pop(key, None)


def process_year(
    *,
    year: int,
    records: list[dict[str, str]],
    sensor: str,
    raw_root: Path,
    work_root: Path,
    coarsen: int,
    session: requests.Session,
    retain_raw: bool,
    skip_download: bool,
    timeout: int,
    max_owned_raw_bytes: int = DEFAULT_MAX_OWNED_RAW_BYTES,
    reserve_bytes: int = DEFAULT_RAW_RESERVE_BYTES,
    max_single_raw_bytes: int = DEFAULT_MAX_SINGLE_RAW_BYTES,
    ownership: dict[str, dict] | None = None,
    ownership_path: Path | None = None,
) -> tuple[Path, dict]:
    """Stage/process one year and persist source provenance before cleanup."""

    import numpy as np

    annual_dir = work_root / "annual"
    annual_path = annual_dir / f"{year}.npy"
    checkpoint_path = annual_dir / f"{year}.manifest.json"
    grid_path = work_root / "grid.json"
    raw_root.mkdir(parents=True, exist_ok=True)
    ownership_path = ownership_path or _ownership_path(raw_root)
    owned = ownership if ownership is not None else _load_owned_records(ownership_path)
    if not records:
        raise ProviderAccessError(f"NOAA NCEI returned no {sensor} files for {year}")
    if (
        annual_path.exists()
        and grid_path.exists()
        and checkpoint_path.exists()
        and _valid_annual_checkpoint(
            annual_path=annual_path,
            checkpoint_path=checkpoint_path,
            grid_path=grid_path,
            year=year,
            sensor=sensor,
            coarsen=coarsen,
            records=records,
        )
    ):
        _cleanup_checkpoint_owned(
            checkpoint_path=checkpoint_path,
            raw_root=raw_root,
            retain_raw=retain_raw,
            owned=owned,
            ownership_path=ownership_path,
        )
        return annual_path, json.loads(grid_path.read_text(encoding="utf-8"))

    raw_year_root = raw_root / "vegetation" / "noaa_cdr" / str(year)
    raw_year_root.mkdir(parents=True, exist_ok=True)
    daily_path = work_root / "daily" / f"{year}.npy"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    if daily_path.exists():
        daily_path.unlink()
    daily = None
    grid = None
    processed_records: list[dict] = []
    try:
        for index, record in enumerate(records):
            destination = _safe_source_path(raw_root, str(Path("vegetation") / "noaa_cdr" / str(year) / record["name"]))
            key = str(destination)
            owner_record = owned.get(key)
            was_present = destination.exists() and destination.stat().st_size > 0
            if not was_present:
                if skip_download:
                    raise ProviderAccessError(f"missing staged NOAA input with --skip-download: {destination}")
                try:
                    size_hint = remote_file_size(session, record["url"], timeout)
                except requests.RequestException:
                    size_hint = None
                if size_hint is None:
                    size_hint = max_single_raw_bytes
                if size_hint > max_single_raw_bytes:
                    raise ProviderAccessError(
                        f"NOAA source file exceeds the configured single-file cap: {size_hint} > {max_single_raw_bytes}"
                    )
                _check_storage_budget(
                    raw_root=raw_root,
                    owned=owned,
                    incoming_bytes=size_hint,
                    max_owned_raw_bytes=max_owned_raw_bytes,
                    reserve_bytes=reserve_bytes,
                )
                download_resumable(
                    session,
                    record["url"],
                    destination,
                    timeout=timeout,
                    expected_size=size_hint if size_hint != max_single_raw_bytes else None,
                )
                actual_size = destination.stat().st_size
                if actual_size > max_single_raw_bytes:
                    destination.unlink(missing_ok=True)
                    raise ProviderAccessError(
                        f"NOAA downloaded file exceeds the configured single-file cap: {actual_size} > {max_single_raw_bytes}"
                    )
                digest = sha256_file(destination)
                owner_record = {
                    "path": key,
                    "status": "downloaded",
                    "year": year,
                    "date": record["date"],
                    "url": record["url"],
                    "bytes": actual_size,
                    "sha256": digest,
                }
                _append_ownership(ownership_path, owner_record)
                owned[key] = owner_record
            else:
                actual_size = destination.stat().st_size
                digest = sha256_file(destination)
                if owner_record and owner_record.get("sha256") and owner_record["sha256"] != digest:
                    raise ProviderAccessError(f"worker-owned NOAA source hash changed: {destination}")
            values, latitudes, longitudes = read_daily_working_grid(destination, sensor, coarsen)
            if daily is None:
                daily = np.lib.format.open_memmap(
                    daily_path,
                    mode="w+",
                    dtype=np.float32,
                    shape=(len(records), values.shape[0], values.shape[1]),
                )
                daily[:] = np.nan
                grid = {
                    "coarsen": coarsen,
                    "latitude": [float(value) for value in latitudes],
                    "longitude": [float(value) for value in longitudes],
                    "sensor": sensor,
                    "sourceGrid": "NOAA CDR 0.05 degree",
                }
            elif values.shape != daily.shape[1:]:
                raise ProviderAccessError(f"NOAA grid changed within {year}: {destination}")
            daily[index] = values
            processed_records.append(
                {
                    "year": year,
                    "date": record["date"],
                    "catalogId": f"{year}/{record['name']}",
                    "url": record["url"],
                    "file": str(destination.relative_to(raw_root.resolve())),
                    "sensor": sensor,
                    "bytes": actual_size,
                    "sha256": digest,
                    "owned": bool(owner_record),
                }
            )
        if daily is None or grid is None:
            raise ProviderAccessError(f"no NOAA files processed for {year}")
        annual = np.full(daily.shape[1:], np.nan, dtype=np.float32)
        # nanmedian over a latitude slab bounds memory even when coarsen=1.
        for start_row in range(0, daily.shape[1], 32):
            stop_row = min(start_row + 32, daily.shape[1])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                annual[start_row:stop_row] = np.nanmedian(np.asarray(daily[:, start_row:stop_row, :]), axis=0)
        _save_npy_atomic(annual_path, annual)
        write_json(grid_path, grid)
        if not annual_path.exists() or annual_path.stat().st_size <= 0:
            raise ProviderAccessError(f"annual NOAA checkpoint was not persisted for {year}")
        write_json(
            checkpoint_path,
            {
                "schemaVersion": 1,
                "year": year,
                "sensor": sensor,
                "coarsen": coarsen,
                "annualFile": str(annual_path.relative_to(work_root.resolve())),
                "gridFile": str(grid_path.relative_to(work_root.resolve())),
                "annualBytes": annual_path.stat().st_size,
                "annualSha256": sha256_file(annual_path),
                "gridShape": [len(grid["latitude"]), len(grid["longitude"])],
                "gridSha256": _grid_fingerprint(grid),
                "source": "NOAA NCEI Land-NDVI CDR direct HTTPS",
                "records": processed_records,
            },
        )
        # The annual file and its complete source manifest are durable before any raw is removed.
        for source in processed_records:
            _append_manifest(
                raw_root / "vegetation" / "noaa_cdr" / "manifest.jsonl",
                {**source, "status": "reduced"},
            )
        for source in processed_records:
            if not source["owned"]:
                continue
            source_path = _safe_source_path(raw_root, source["file"])
            if retain_raw:
                _append_ownership(
                    ownership_path,
                    {"path": str(source_path), "status": "retained", "year": year, "date": source["date"]},
                )
                continue
            source_path.unlink(missing_ok=True)
            key = str(source_path)
            if key in owned:
                _append_ownership(
                    ownership_path,
                    {"path": key, "status": "deleted", "year": year, "date": source["date"]},
                )
                owned.pop(key, None)
        return annual_path, grid
    finally:
        if daily is not None:
            daily.flush()
            del daily
        daily_path.unlink(missing_ok=True)

def _score_rows(
    *,
    annual_paths: dict[int, Path],
    baseline,
    latitudes,
    threshold: float,
    start_year: int,
    sensor_by_year: dict[int, str],
    observed_days_by_year: dict[int, int] | None = None,
) -> list[dict]:
    import numpy as np

    latitude_weights = np.cos(np.deg2rad(np.asarray(latitudes, dtype=np.float32)))[:, None]
    baseline_mask = np.isfinite(baseline) & (baseline > threshold)
    baseline_area = float(np.sum(np.where(baseline_mask, latitude_weights, 0.0)))
    rows = []
    for year in sorted(annual_paths):
        current = np.load(annual_paths[year], mmap_mode="r")
        valid = baseline_mask & np.isfinite(current)
        valid_area = float(np.sum(np.where(valid, latitude_weights, 0.0)))
        if valid_area <= 0 or baseline_area <= 0:
            continue
        ratio = np.clip(np.asarray(current, dtype=np.float32) / baseline, 0.0, 1.0)
        weighted = float(np.sum(np.where(valid, ratio * latitude_weights, 0.0)))
        raw = 100.0 * weighted / valid_area
        expected_days = 366 if calendar.isleap(year) else 365
        observed_days = (observed_days_by_year or {}).get(year, expected_days)
        rows.append(
            {
                "year": year,
                "score": raw,
                "coverage": min(1.0, valid_area / baseline_area),
                "raw": raw,
                "unit": "% baseline-relative NDVI retention",
                "sensor": sensor_by_year[year],
                "observedDays": observed_days,
                "expectedDays": expected_days,
                "temporalCoverage": min(1.0, observed_days / expected_days),
                "observations": f"{observed_days}/{expected_days} daily NOAA CDR files available for this calendar year",
            }
        )
    return rows


def build(args: argparse.Namespace) -> Path:
    import numpy as np

    if args.baseline_start > args.baseline_end:
        raise ValueError("baseline start must not exceed baseline end")
    if args.coarsen < 1:
        raise ValueError("coarsen must be at least 1")
    raw_root = args.raw_root.resolve()
    output_root = args.output_root.resolve()
    work_root = (args.work_root or (output_root / "vegetation-work")).resolve()
    max_owned_raw_bytes = int(getattr(args, "max_owned_raw_bytes", DEFAULT_MAX_OWNED_RAW_BYTES))
    reserve_bytes = int(getattr(args, "reserve_bytes", DEFAULT_RAW_RESERVE_BYTES))
    max_single_raw_bytes = int(getattr(args, "max_single_raw_bytes", DEFAULT_MAX_SINGLE_RAW_BYTES))
    ownership_path = _ownership_path(raw_root)
    ownership = _load_owned_records(ownership_path)
    session = requests.Session()
    session.headers.update({"User-Agent": "MotherWorld-direct-backbone/1.0"})

    all_years = list(date_range_years(args.baseline_start, args.end_year))
    records_by_year: dict[int, list[dict[str, str]]] = {}
    for year in all_years:
        records_by_year[year] = discover_noaa_daily_urls(year, session)

    annual_paths: dict[int, Path] = {}
    grids: dict[int, dict] = {}
    sensor_by_year: dict[int, str] = {}
    for year in all_years:
        path, grid = process_year(
            year=year,
            records=records_by_year[year],
            sensor=sensor_for_year(year),
            raw_root=raw_root,
            work_root=work_root,
            coarsen=args.coarsen,
            session=session,
            retain_raw=args.retain_raw,
            skip_download=args.skip_download,
            timeout=args.timeout,
            max_owned_raw_bytes=max_owned_raw_bytes,
            reserve_bytes=reserve_bytes,
            max_single_raw_bytes=max_single_raw_bytes,
            ownership=ownership,
            ownership_path=ownership_path,
        )
        annual_paths[year] = path
        grids[year] = grid
        sensor_by_year[year] = sensor_for_year(year)

    baseline_paths = [annual_paths[year] for year in range(args.baseline_start, args.baseline_end + 1)]
    if any(not path.exists() for path in baseline_paths):
        raise ProviderAccessError("the complete NOAA AVHRR baseline range is not staged")
    baseline = np.nanmedian(np.stack([np.load(path, mmap_mode="r") for path in baseline_paths]), axis=0)
    baseline_path = work_root / "baseline.npy"
    _save_npy_atomic(baseline_path, baseline.astype(np.float32))
    grid = grids[args.baseline_start]
    rows = _score_rows(
        annual_paths={year: annual_paths[year] for year in range(args.start_year, args.end_year + 1)},
        baseline=baseline,
        latitudes=grid["latitude"],
        threshold=args.vegetated_threshold,
        start_year=args.start_year,
        sensor_by_year=sensor_by_year,
        observed_days_by_year={year: len(records_by_year[year]) for year in records_by_year},
    )
    payload = family_payload(
        family_id="vegetation",
        label="Vegetation condition",
        series=rows,
        component={
            "id": "ndvi_retention",
            "label": "Baseline-relative vegetation greenness retention",
            "weight": 1.0,
            "source": "NOAA NDVI Climate Data Record",
        },
        source={
            "id": "noaa-ndvi-cdr",
            "label": "NOAA AVHRR/VIIRS NDVI Climate Data Record",
            "collections": ["NOAA CDR AVHRR NDVI v5", "NOAA CDR VIIRS NDVI v1"],
            "access": NOAA_BASE_URL,
        },
        method={
            "baseline": f"{args.baseline_start}-{args.baseline_end} per-pixel median annual NDVI",
            "normalization": "annual NDVI / fixed baseline NDVI, clipped 0..1, area-weighted over pixels with baseline NDVI above threshold",
            "vegetatedThreshold": args.vegetated_threshold,
            "scoreDirection": "100 = baseline vegetation greenness retained or exceeded",
            "sensorTransition": "AVHRR through 2013; VIIRS from 2014; both are NOAA CDR products",
            "annualAggregation": "median of all valid daily CDR observations after QA filtering",
            "workingGrid": f"valid-pixel area-weighted daily reduction at approximately {0.05 * args.coarsen:g} degree spacing (coarsen={args.coarsen})",
            "diagnosticPolicy": "MODIS VCF cover/composition remains present-day diagnostics only",
        },
        context={
            "providerPath": "NOAA NCEI direct HTTPS NetCDF; no Earth Engine dependency",
            "qaPolicy": "Native AVHRR v5 exclusions for cloud, shadow, water, night, channel-1/channel-2 invalid flags; native VIIRS v1 cloud, shadow, snow/ice and land/water decoding",
            "storagePolicy": "Worker-owned daily raw is retained only until its annual median and source manifest are persisted; pre-existing staged files are read-only",
            "provenance": "Per-year annual/*.manifest.json checkpoints retain source catalog IDs, URLs, byte counts and SHA-256 hashes",
        },
    )
    output = output_family_path(output_root, "vegetation")
    write_json(output, payload)
    print(f"Vegetation direct backbone latest={payload['latestBackboneYear']} score={payload['score']} -> {output}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the NOAA AVHRR/VIIRS vegetation Earth Health backbone without Earth Engine.")
    parser.add_argument("--raw-root", type=Path, default=Path("F:/BiomeSummary/MotherWorld-v8-dataset-downloader/earth_health_backbone_raw"))
    parser.add_argument("--output-root", type=Path, default=Path("F:/BiomeSummary/.cache/motherworld/v8-build/direct-noaa-nasa"))
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int, default=date.today().year - 1)
    parser.add_argument("--baseline-start", type=int, default=1982)
    parser.add_argument("--baseline-end", type=int, default=1992)
    parser.add_argument("--vegetated-threshold", type=float, default=0.10)
    parser.add_argument("--coarsen", type=int, default=8, help="working-grid reduction factor from NOAA's 0.05 degree grid")
    parser.add_argument("--retain-raw", action="store_true", help="keep downloaded daily files instead of deleting them after processing")
    parser.add_argument("--skip-download", action="store_true", help="fail if any required daily NOAA file is not already staged")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-owned-raw-bytes", type=int, default=DEFAULT_MAX_OWNED_RAW_BYTES, help="worker-owned daily raw cap; default 32 GiB")
    parser.add_argument("--reserve-bytes", type=int, default=DEFAULT_RAW_RESERVE_BYTES, help="minimum free bytes to preserve on the raw volume; default 40 GiB")
    parser.add_argument("--max-single-raw-bytes", type=int, default=DEFAULT_MAX_SINGLE_RAW_BYTES, help="maximum one downloaded CDR file; default 128 MiB")
    return parser.parse_args()


if __name__ == "__main__":
    build(parse_args())
