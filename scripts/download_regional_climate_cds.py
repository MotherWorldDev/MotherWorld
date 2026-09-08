#!/usr/bin/env python3
"""Acquire validated CDS daily climate files for canonical regions.

This module is the raw regional acquisition layer.  It does not aggregate or
score regional climate values.  Every completed file is tied to the exact
request and analysis footprint used to create it; downloads are staged and
validated before they can replace a completed file.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import cdsapi
import multiurl
import numpy as np
import xarray as xr

from analysis_geometry import (
    analysis_geometry_cache_identity,
    analysis_geometry_metadata,
    apply_land_geometry_overrides,
)
from species_common import load_region_geometries, sha256_text


DATASET_BY_KIND = {
    "land": "derived-era5-land-daily-statistics",
    "lakes": "derived-era5-land-daily-statistics",
    "marine": "derived-era5-single-levels-daily-statistics",
}
VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]
VARIABLE_ALIASES = {
    "2m_temperature": ("2m_temperature", "t2m"),
    "2m_dewpoint_temperature": ("2m_dewpoint_temperature", "d2m"),
    "10m_u_component_of_wind": ("10m_u_component_of_wind", "u10"),
    "10m_v_component_of_wind": ("10m_v_component_of_wind", "v10"),
}
TIME_NAMES = ("valid_time", "time", "date")
LATITUDE_NAMES = ("latitude", "lat")
LONGITUDE_NAMES = ("longitude", "lon")
DEFAULT_CREDENTIALS = Path(r"F:\BiomeSummary\CpernicusToken\.cdsapirc")
TEMP_ROOT = Path(r"F:\BiomeSummary\.cache\regional-climate-cds-temp")
GEOMETRY_PROVENANCE_SCHEMA_VERSION = 1


def parse_years(value: str) -> list[int]:
    if ":" in value:
        start, end = (int(part) for part in value.split(":", 1))
        if end < start:
            raise ValueError(f"year range must be increasing, got {value!r}")
        return list(range(start, end + 1))
    years = sorted({int(part) for part in value.split(",") if part.strip()})
    if not years:
        raise ValueError("years must contain at least one year")
    return years


def parse_months(value: str) -> list[int]:
    """Parse a comma-separated month list or inclusive range.

    The returned list is canonicalized so equivalent command lines produce the
    same request fingerprint.  Out-of-range, empty, reversed, and duplicate
    selections are rejected instead of being silently turned into a full-year
    request.
    """

    if not str(value).strip():
        raise ValueError("months must contain at least one month")
    try:
        if ":" in value:
            pieces = value.split(":")
            if len(pieces) != 2 or not all(piece.strip() for piece in pieces):
                raise ValueError
            start, end = (int(part) for part in pieces)
            if end < start:
                raise ValueError
            months = list(range(start, end + 1))
        else:
            pieces = [part.strip() for part in value.split(",")]
            if any(not part for part in pieces):
                raise ValueError
            months = [int(part) for part in pieces]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid month selection: {value!r}") from exc
    if not months or any(month < 1 or month > 12 for month in months):
        raise ValueError(f"months must be in 1..12, got {months}")
    if len(set(months)) != len(months):
        raise ValueError(f"months must not contain duplicates, got {months}")
    return sorted(months)


def _normalize_months(months: list[int] | tuple[int, ...] | None) -> list[int]:
    if months is None:
        return list(range(1, 13))
    values = list(months)
    if not values:
        raise ValueError("months must contain at least one month")
    try:
        values = [int(month) for month in values]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid month selection: {months!r}") from exc
    if any(month < 1 or month > 12 for month in values):
        raise ValueError(f"months must be in 1..12, got {values}")
    if len(set(values)) != len(values):
        raise ValueError(f"months must not contain duplicates, got {values}")
    return sorted(values)


def _normalize_variables(variables: list[str] | tuple[str, ...] | None) -> list[str]:
    if variables is None:
        return list(VARIABLES)
    values = [str(variable).strip() for variable in variables]
    if not values or any(not value for value in values):
        raise ValueError("variables must contain at least one supported variable")
    invalid = [value for value in values if value not in VARIABLES]
    if invalid:
        raise ValueError(f"unsupported variables: {invalid}")
    if len(set(values)) != len(values):
        raise ValueError(f"variables must not contain duplicates: {values}")
    return values


def region_catalog(root: Path, kind: str, requested: list[str]) -> list:
    """Return canonical regions while applying maintained land footprints.

    ``species_common.load_region_geometries`` retains the source priority used
    by the species pipeline (the original detailed shapefile first, then the
    committed runtime layer).  Corrections are applied only after that load,
    preserving IDs and names while ensuring the acquisition bbox and identity
    use the maintained analysis footprint.
    """

    root = Path(root)
    kind = str(kind).lower()
    if kind not in DATASET_BY_KIND:
        raise ValueError(f"Unsupported kind: {kind}")
    species_path = root / "frontend/public/data/species/species.index.json"
    if not species_path.exists():
        raise FileNotFoundError(species_path)
    species = json.loads(species_path.read_text(encoding="utf-8"))
    prefix = {"land": "eco_", "lakes": "lake_", "marine": "marine_"}[kind]
    canonical = sorted(rid for rid in species.get("regions", {}) if str(rid).startswith(prefix))
    selected = sorted(set(requested)) if requested else canonical
    invalid = [rid for rid in selected if rid not in canonical]
    if invalid:
        raise ValueError(f"IDs are not in species.index.json for {kind}: {invalid[:10]}")

    loaded = load_region_geometries(root, kind, selected)
    by_id = {region.region_id: region for region in loaded}
    missing = [rid for rid in selected if rid not in by_id]
    if missing:
        raise ValueError(f"canonical IDs missing merged geometry for {kind}: {missing[:10]}")

    if kind == "land":
        corrected = apply_land_geometry_overrides({region.region_id: region.geometry for region in loaded})
        loaded = [replace(region, geometry=corrected[region.region_id]) for region in loaded]
        by_id = {region.region_id: region for region in loaded}
    return [by_id[rid] for rid in selected]


def request_for(
    kind: str,
    year: int,
    bbox: list[float],
    months: list[int] | None = None,
    variables: list[str] | None = None,
) -> dict:
    selected_months = _normalize_months(months)
    selected_variables = _normalize_variables(variables)
    request = {
        "variable": selected_variables,
        "year": str(int(year)),
        "month": [f"{month:02d}" for month in selected_months],
        "day": [f"{day:02d}" for day in range(1, 32)],
        "daily_statistic": "daily_mean",
        "time_zone": "utc+00:00",
        "frequency": "1_hourly",
        "area": [float(value) for value in bbox],
    }
    if kind == "marine":
        request["product_type"] = "reanalysis"
    return request


def fingerprint(
    dataset: str,
    request: dict,
    region_id: str,
    geometry_wkb: bytes,
    geometry_identity: str | None = None,
) -> str:
    """Fingerprint the provider request and exact analysis footprint."""

    digest = hashlib.sha256()
    # Keep the established digest layout so existing completed regional files
    # remain addressable. The corrected analysis geometry is already part of
    # ``geometry_wkb``; its provenance is recorded separately in the sidecar.
    digest.update(str(dataset).encode("utf-8"))
    digest.update(json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(str(region_id).encode("utf-8"))
    digest.update(bytes(geometry_wkb))
    return digest.hexdigest()


def sidecar_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".request.json")


def partial_metadata_path(part: Path) -> Path:
    return part.with_suffix(part.suffix + ".request.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _geometry_provenance(region, kind: str) -> tuple[dict | None, str]:
    geometry = region.geometry
    if kind == "land":
        metadata = analysis_geometry_metadata(region.region_id, geometry)
        identity = analysis_geometry_cache_identity(region.region_id, geometry)
        return metadata, identity
    digest = hashlib.sha256(bytes(geometry.wkb)).hexdigest()
    metadata = {
        "schemaVersion": GEOMETRY_PROVENANCE_SCHEMA_VERSION,
        "regionId": str(region.region_id),
        "geometryFingerprint": digest,
        "geometryFingerprintAlgorithm": "sha256-wkb",
        "geometryType": getattr(geometry, "geom_type", None),
        "geometryBounds": [float(value) for value in geometry.bounds],
        "geometrySource": f"species_common:{kind}",
        "overrideApplied": False,
    }
    identity = f"region-geometry-v{GEOMETRY_PROVENANCE_SCHEMA_VERSION}:{digest}"
    return metadata, identity


def _metadata_payload(
    *,
    dataset: str,
    kind: str,
    region,
    year: int,
    request: dict,
    request_id: str,
    expected: int | None,
    geometry_metadata: dict | None,
    geometry_identity: str,
    complete: bool = False,
    target: Path | None = None,
) -> dict:
    payload = {
        "dataset": dataset,
        "kind": kind,
        "regionId": region.region_id,
        "regionName": region.name,
        "year": int(year),
        "request": request,
        "requestFingerprint": request_id,
        "expectedTransportBytes": expected,
        "geometrySha256": sha256_text(region.geometry.wkb.hex()),
        "geometryFingerprint": hashlib.sha256(bytes(region.geometry.wkb)).hexdigest(),
        "geometryFingerprintAlgorithm": "sha256-wkb",
        "geometryIdentity": geometry_identity,
    }
    if geometry_metadata is not None:
        payload["analysisGeometry"] = geometry_metadata
        payload["analysisGeometryCacheIdentity"] = geometry_identity
    if complete:
        if target is None:
            raise ValueError("target is required for completed metadata")
        payload.update(
            {
                "complete": True,
                "targetBytes": target.stat().st_size,
                "targetSha256": sha256_file(target),
            }
        )
    return payload


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def request_matches(
    sidecar: Path,
    request_id: str,
    dataset: str | None = None,
    geometry_identity: str | None = None,
) -> bool:
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if payload.get("requestFingerprint") != request_id:
            return False
        if dataset is not None and payload.get("dataset") != dataset:
            return False
        if geometry_identity is not None and payload.get("geometryIdentity") != geometry_identity:
            return False
        return True
    except Exception:
        return False


def partial_matches(
    path: Path,
    request_id: str,
    dataset: str,
    expected: int | None = None,
    geometry_identity: str | None = None,
) -> bool:
    if not path.exists() or not request_matches(path, request_id, dataset, geometry_identity):
        return False
    if expected is None:
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        recorded = payload.get("expectedTransportBytes")
        return recorded in (None, expected)
    except Exception:
        return False

def write_metadata(path: Path, payload: dict) -> None:
    _write_json_atomic(path, payload)


def ensure_free_space(path: Path, minimum_bytes: int, context: str) -> None:
    if minimum_bytes <= 0:
        return
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < minimum_bytes:
        raise RuntimeError(
            f"Only {free / (1024**3):.1f} GiB free before {context}; "
            f"required {minimum_bytes / (1024**3):.1f} GiB"
        )


def _open_netcdf(path: Path) -> xr.Dataset:
    try:
        return xr.open_dataset(path, engine="netcdf4")
    except (ImportError, ValueError):
        return xr.open_dataset(path)


def open_downloaded_dataset(path: Path) -> tuple[xr.Dataset, tempfile.TemporaryDirectory | None]:
    """Open a direct NetCDF or CDS ZIP using only F: temporary storage."""

    path = Path(path)
    if not zipfile.is_zipfile(path):
        return _open_netcdf(path), None
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="regional-climate-cds-", dir=str(TEMP_ROOT))
    root = Path(temporary.name).resolve()
    opened: list[xr.Dataset] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.lower().endswith((".nc", ".nc4")):
                    continue
                target = (root / info.filename).resolve()
                if root not in target.parents:
                    raise ValueError("Unsafe NetCDF archive path")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=8 * 1024 * 1024)
        files = sorted(root.rglob("*.nc")) + sorted(root.rglob("*.nc4"))
        if not files:
            raise ValueError("CDS archive contains no NetCDF files")
        opened = [_open_netcdf(file) for file in files]
        if len(opened) == 1:
            return opened[0], temporary
        combined = xr.combine_by_coords(opened, combine_attrs="override")
        combined.set_close(lambda: [dataset.close() for dataset in opened])
        return combined, temporary
    except Exception:
        for dataset in opened:
            dataset.close()
        temporary.cleanup()
        raise


def _coordinate_name(dataset: xr.Dataset, candidates: tuple[str, ...]) -> str | None:
    return next((name for name in candidates if name in dataset.coords or name in dataset.dims), None)


def _unit_family(value) -> str | None:
    text = str(value or "").strip().lower()
    text = text.replace("−", "-").replace("²", "2")
    compact = "".join(text.split())
    if compact in {"k", "kelvin", "degk", "degreeskelvin", "degreekelvin"}:
        return "temperature"
    if compact in {
        "c",
        "degc",
        "celsius",
        "degreecelsius",
        "degreescelsius",
        "degreec",
        "degreesc",
    }:
        return "temperature"
    if compact in {
        "m/s",
        "ms-1",
        "ms**-1",
        "ms^-1",
        "ms−1",
        "metersecond-1",
        "meterssecond-1",
        "metressecond-1",
        "metresecond-1",
    }:
        return "wind"
    return None


def _expected_unit_family(variable: str) -> str:
    return "temperature" if variable in {"2m_temperature", "2m_dewpoint_temperature"} else "wind"


def _expected_dates(year: int, months: list[int]) -> np.ndarray:
    dates: list[np.ndarray] = []
    for month in months:
        start = np.datetime64(f"{year}-{month:02d}-01", "D")
        if month == 12:
            end = np.datetime64(f"{year + 1}-01-01", "D")
        else:
            end = np.datetime64(f"{year}-{month + 1:02d}-01", "D")
        dates.append(np.arange(start, end, dtype="datetime64[D]"))
    return np.concatenate(dates) if dates else np.array([], dtype="datetime64[D]")


def valid_daily_file(
    path: Path,
    year: int,
    months: list[int] | None = None,
    variables: list[str] | None = None,
) -> tuple[bool, str]:
    """Validate variables, dimensions, units, and exact daily date coverage."""

    selected_months = _normalize_months(months)
    selected_variables = _normalize_variables(variables)
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 0:
        return False, "missing-or-empty"
    dataset = None
    temporary = None
    try:
        dataset, temporary = open_downloaded_dataset(path)
        names = set(dataset.data_vars)
        actual_variables: dict[str, str] = {}
        for requested in selected_variables:
            actual = next((name for name in VARIABLE_ALIASES[requested] if name in names), None)
            if actual is None:
                return False, f"missing-variables:{requested}"
            actual_variables[requested] = actual

        time_name = _coordinate_name(dataset, TIME_NAMES)
        if time_name is None:
            return False, "missing-time-coordinate"
        latitude_name = _coordinate_name(dataset, LATITUDE_NAMES)
        if latitude_name is None:
            return False, "missing-spatial-dimension:latitude"
        longitude_name = _coordinate_name(dataset, LONGITUDE_NAMES)
        if longitude_name is None:
            return False, "missing-spatial-dimension:longitude"
        for coordinate_name, label, family in (
            (latitude_name, "latitude", "latitude"),
            (longitude_name, "longitude", "longitude"),
        ):
            coordinate = dataset[coordinate_name]
            if coordinate.ndim != 1 or coordinate.dims != (coordinate_name,):
                return False, f"invalid-spatial-dimension:{label}"
            if coordinate.size == 0:
                return False, f"empty-spatial-dimension:{label}"
            try:
                values = np.asarray(coordinate.values, dtype=np.float64)
            except Exception:
                return False, f"invalid-spatial-coordinate:{label}"
            if not np.all(np.isfinite(values)) or len(np.unique(values)) != len(values):
                return False, f"invalid-spatial-coordinate:{label}"
            if family == "latitude" and (np.min(values) < -90 or np.max(values) > 90):
                return False, "spatial-coordinate-out-of-range:latitude"
            if family == "longitude" and (np.min(values) < -360 or np.max(values) > 360):
                return False, "spatial-coordinate-out-of-range:longitude"
            units = str(coordinate.attrs.get("units") or "").lower().replace(" ", "_")
            expected_units = {"latitude": {"degrees_north", "degree_north"}, "longitude": {"degrees_east", "degree_east"}}[family]
            if units not in expected_units:
                return False, f"invalid-spatial-units:{label}"

        required_dimensions = {time_name, latitude_name, longitude_name}
        for requested, actual in actual_variables.items():
            variable = dataset[actual]
            if set(variable.dims) != required_dimensions or len(variable.dims) != 3:
                return False, f"missing-required-dimensions:{requested}:{list(variable.dims)}"
            units = variable.attrs.get("units")
            if not units:
                return False, f"missing-units:{requested}"
            if _unit_family(units) != _expected_unit_family(requested):
                return False, f"invalid-units:{requested}:{units}"

        try:
            dates = np.asarray(dataset[time_name].values).astype("datetime64[D]")
        except Exception:
            return False, "invalid-time-coordinate"
        if dates.ndim != 1 or dates.size == 0:
            return False, "invalid-time-coordinate"
        if np.any(np.isnat(dates)):
            return False, "invalid-time-coordinate"
        expected = _expected_dates(int(year), selected_months)
        sorted_dates = np.sort(dates)
        if not np.array_equal(sorted_dates, expected):
            expected_start = np.datetime64(f"{year}-01-01")
            expected_end = np.datetime64(f"{year + 1}-01-01")
            if np.any(dates < expected_start) or np.any(dates >= expected_end):
                return False, "time-outside-requested-year"
            months_present = sorted(set(((dates.astype("datetime64[M]").astype(int) % 12) + 1).tolist()))
            if months_present != selected_months:
                return False, f"month-coverage:{months_present} expected {selected_months}"
            if len(np.unique(dates)) != len(dates):
                return False, "duplicate-daily-timestamps"
            expected_days = sum(calendar.monthrange(int(year), month)[1] for month in selected_months)
            return False, f"unexpected-daily-count:{len(dates)} expected {expected_days}"
        return True, "netcdf-readable"
    except Exception as exc:
        return False, f"netcdf-invalid:{type(exc).__name__}"
    finally:
        if dataset is not None:
            dataset.close()
        if temporary is not None:
            temporary.cleanup()


def make_client(credentials: Path):
    os.environ["CDSAPI_RC"] = str(Path(credentials).resolve())
    return cdsapi.Client(quiet=True, timeout=180, retry_max=8, wait_until_complete=True)


def estimated_unpacked_bytes(source: Path) -> int:
    if not zipfile.is_zipfile(source):
        return 0
    with zipfile.ZipFile(source) as archive:
        members = [member for member in archive.infolist() if not member.is_dir() and member.filename.lower().endswith((".nc", ".nc4"))]
        if not members:
            raise RuntimeError("CDS archive contains no NetCDF files")
        return sum(int(member.file_size) for member in members)


def download_one(
    region,
    kind: str,
    year: int,
    output_dir: Path,
    credentials: Path,
    pad: float,
    months: list[int] | None = None,
    variables: list[str] | None = None,
    min_free_bytes: int = 0,
) -> dict:
    kind = str(kind).lower()
    if kind not in DATASET_BY_KIND:
        raise ValueError(f"Unsupported kind: {kind}")
    selected_months = _normalize_months(months)
    selected_variables = _normalize_variables(variables)
    geometry = region.geometry
    minx, miny, maxx, maxy = geometry.bounds
    bbox = [
        min(90.0, maxy + float(pad)),
        max(-180.0, minx - float(pad)),
        max(-90.0, miny - float(pad)),
        min(180.0, maxx + float(pad)),
    ]
    dataset = DATASET_BY_KIND[kind]
    request = request_for(kind, year, bbox, months=selected_months, variables=selected_variables)
    geometry_metadata, geometry_identity = _geometry_provenance(region, kind)
    request_id = fingerprint(dataset, request, region.region_id, geometry.wkb)
    month_token = "-".join(f"{month:02d}" for month in selected_months)
    target = Path(output_dir) / kind / region.region_id / f"{year}.m{month_token}.{request_id[:16]}.nc"
    sidecar = sidecar_path(target)
    valid, validation = valid_daily_file(target, year, months=selected_months, variables=selected_variables)
    if valid and request_matches(sidecar, request_id, dataset, geometry_identity):
        return {
            "regionId": region.region_id,
            "year": year,
            "status": "exists-valid",
            "path": str(target),
            "bytes": target.stat().st_size,
            "validation": validation,
            "requestFingerprint": request_id,
            "analysisGeometryCacheIdentity": geometry_identity if geometry_metadata is not None else None,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    ensure_free_space(target.parent, int(min_free_bytes), f"starting CDS regional {kind} {region.region_id} {year}")
    client = make_client(credentials)
    print(f"CDS regional {kind} {region.region_id} {year}: submitting", flush=True)
    result = client.retrieve(dataset, request)
    expected = int(result.content_length) if getattr(result, "content_length", None) is not None else None
    if expected is not None and expected <= 0:
        raise RuntimeError(f"invalid CDS transport size: {expected}")

    part = target.with_suffix(target.suffix + ".part")
    part_metadata = partial_metadata_path(part)
    if part.exists() and (
        not partial_matches(part_metadata, request_id, dataset, expected, geometry_identity)
        or (expected is not None and part.stat().st_size >= expected)
    ):
        part.unlink(missing_ok=True)
        part_metadata.unlink(missing_ok=True)
    if not part.exists():
        part_metadata.unlink(missing_ok=True)
    partial_payload = _metadata_payload(
        dataset=dataset,
        kind=kind,
        region=region,
        year=year,
        request=request,
        request_id=request_id,
        expected=expected,
        geometry_metadata=geometry_metadata,
        geometry_identity=geometry_identity,
    )
    write_metadata(part_metadata, partial_payload)
    if expected is not None:
        ensure_free_space(target.parent, int(min_free_bytes) + expected, f"transferring CDS regional {kind} {region.region_id} {year}")
    try:
        multiurl.download(result.location, target=str(part), resume_transfers=True, stream=True, maximum_retries=8)
    except Exception:
        # A matching short partial remains resumable.  Stale or complete
        # partials were removed above and cannot be accidentally resumed.
        raise
    if not part.exists() or part.stat().st_size <= 0:
        raise RuntimeError("CDS transfer produced no bytes")
    if expected is not None and part.stat().st_size != expected:
        raise RuntimeError(f"size mismatch {part.stat().st_size} != {expected}")

    staged = target.with_suffix(target.suffix + ".staged")
    staged.unlink(missing_ok=True)
    try:
        transport_bytes = part.stat().st_size
        unpacked_bytes = estimated_unpacked_bytes(part)
        ensure_free_space(
            target.parent,
            int(min_free_bytes) + transport_bytes + unpacked_bytes,
            f"validating CDS regional {kind} {region.region_id} {year}",
        )
        os.replace(part, staged)
        valid, validation = valid_daily_file(staged, year, months=selected_months, variables=selected_variables)
        if not valid:
            raise RuntimeError(f"validation failed: {validation}")
        completed_payload = _metadata_payload(
            dataset=dataset,
            kind=kind,
            region=region,
            year=year,
            request=request,
            request_id=request_id,
            expected=expected,
            geometry_metadata=geometry_metadata,
            geometry_identity=geometry_identity,
        )
        # Compute completion metadata from the staged bytes, then commit the
        # data and sidecar only after validation.  The prior target and its
        # sidecar remain untouched if transfer, extraction, or validation fails.
        completed_payload.update(
            {
                "complete": True,
                "targetBytes": staged.stat().st_size,
                "targetSha256": sha256_file(staged),
                "validation": validation,
            }
        )
        sidecar_staged = sidecar.with_suffix(sidecar.suffix + ".staged")
        _write_json_atomic(sidecar_staged, completed_payload)
        os.replace(staged, target)
        os.replace(sidecar_staged, sidecar)
    except Exception:
        staged.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        part_metadata.unlink(missing_ok=True)
        sidecar_staged = sidecar.with_suffix(sidecar.suffix + ".staged")
        sidecar_staged.unlink(missing_ok=True)
        raise
    part_metadata.unlink(missing_ok=True)
    return {
        "regionId": region.region_id,
        "year": year,
        "status": "downloaded",
        "path": str(target),
        "bytes": target.stat().st_size,
        "transportBytes": expected,
        "sha256": sha256_file(target),
        "validation": validation,
        "requestFingerprint": request_id,
        "analysisGeometryCacheIdentity": geometry_identity if geometry_metadata is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--kind", choices=tuple(DATASET_BY_KIND), required=True)
    parser.add_argument("--region", action="append", default=[])
    parser.add_argument("--years", default="1991:2020")
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--pad", type=float, default=None)
    parser.add_argument(
        "--months",
        default="1:12",
        help="Requested calendar months, e.g. 01 or 01:03; use smaller chunks when CDS cost limits reject a full year",
    )
    parser.add_argument("--variables", default=",".join(VARIABLES), help="Comma-separated subset of the four supported variables")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-free-gib", type=float, default=40.0)
    args = parser.parse_args()
    if not args.credentials.exists():
        raise SystemExit(f"Missing CDS credential file: {args.credentials}")
    try:
        years = parse_years(args.years)
        months = parse_months(args.months)
        variables = _normalize_variables([variable.strip() for variable in args.variables.split(",")])
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not years or min(years) < 1940 or max(years) > datetime.now(timezone.utc).year:
        raise SystemExit("CDS daily catalog supports years 1940 through the current year")
    pad = float(args.pad if args.pad is not None else (0.3 if args.kind in ("land", "lakes") else 0.6))
    if not np.isfinite(pad) or pad < 0:
        raise SystemExit("--pad must be finite and non-negative")
    regions = region_catalog(args.repo.resolve(), args.kind, args.region)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not np.isfinite(args.min_free_gib) or args.min_free_gib < 0:
        raise SystemExit("--min-free-gib must be finite and non-negative")
    min_free_bytes = int(float(args.min_free_gib) * (1024**3))
    ensure_free_space(args.output_dir, min_free_bytes, "starting regional CDS downloads")
    jobs = [(region, year) for region in regions for year in years]
    workers = max(1, min(int(args.workers), 2))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                download_one,
                region,
                args.kind,
                year,
                args.output_dir.resolve(),
                args.credentials.resolve(),
                pad,
                months,
                variables,
                min_free_bytes,
            ): (region.region_id, year)
            for region, year in jobs
        }
        for future in as_completed(futures):
            region_id, year = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"regionId": region_id, "year": year, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result["status"] == "error":
                for pending in futures:
                    pending.cancel()
                break
    results.sort(key=lambda item: (item["regionId"], item["year"]))
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "kind": args.kind,
        "datasets": DATASET_BY_KIND,
        "variables": variables,
        "months": months,
        "years": years,
        "workers": workers,
        "regionCount": len(regions),
        "regions": [region.region_id for region in regions],
        "outputDir": str(args.output_dir.resolve()),
        "results": results,
    }
    (args.output_dir / f"regional_{args.kind}_climate_download_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if any(result["status"] == "error" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
