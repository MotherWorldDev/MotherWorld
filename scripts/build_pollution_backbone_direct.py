#!/usr/bin/env python
"""Direct NASA MERRA-2 pollution backbone builder.

CMR resolves one official monthly granule at a time. GES DISC Cloud OPeNDAP
receives only the five aerosol fields required for reconstructed PM2.5. The
native M2C0NXASM mask supplies FRLAND; its current NASA file no longer has
AREA, so cell area is derived from native lat/lon bounds. An Earthdata token is
read only from EARTHDATA_TOKEN or an F: token file.
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid
from urllib.parse import quote, urlencode, unquote

import requests

from backbone_common import HISTORY_START_YEAR, family_payload
from direct_provider_common import (
    EarthdataAuthenticationRequired,
    ProviderAccessError,
    date_range_years,
    download_resumable,
    earthdata_headers,
    html_links,
    is_earthdata_login_redirect,
    output_family_path,
    raise_for_provider_response,
    write_json,
)


MERRA_AER_COLLECTION = "C1276812866-GES_DISC"
MERRA_MASK_COLLECTION = "C1276812819-GES_DISC"
MERRA_AER_SHORT = "M2TMNXAER.5.12.4"
MERRA_MASK_SHORT = "M2C0NXASM.5.12.4"
MERRA_MASK_GRANULE = "MERRA2_101.const_2d_asm_Nx.00000000.nc4"
CMR_VIRTUAL_DIRECTORY = "https://cmr.earthdata.nasa.gov/virtual-directory/collections"
OPENDAP_BASE = "https://opendap.earthdata.nasa.gov/collections"
AEROSOL_VARIABLES = ("DUSMASS25", "OCSMASS", "BCSMASS", "SSSMASS25", "SO4SMASS")
MASK_VARIABLES = ("FRLAND",)
SULFATE_AMMONIUM_FACTOR = 132.14 / 96.06
EARTH_RADIUS_M = 6371008.8
DEFAULT_START_YEAR = HISTORY_START_YEAR
DEFAULT_END_YEAR = 2025
DEFAULT_MIN_FREE_GIB = 40.0
CHECKPOINT_SCHEMA_VERSION = 3
REQUEST_SCHEMA_VERSION = 2
NATIVE_LAT_COUNT = 361
NATIVE_LON_COUNT = 576
NATIVE_TIME_COUNT = 1
PM25_METHOD_VERSION = "pm25-five-field-v1"
AREA_METHOD_VERSION = "spherical-native-cell-area-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_hash(payload: object) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _require_f_path(path: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if os.name == "nt" and resolved.drive.upper() != "F:":
        raise ProviderAccessError(f"{label} must be on F:; refusing {resolved.drive or resolved}")
    return resolved


def _read_nasa_bearer_token(token_file: Path | None) -> str | None:
    """Read the Earthdata token without ever exposing it or retaining a UTF-8 BOM."""
    token = os.environ.get("EARTHDATA_TOKEN", "").strip().lstrip("\ufeff")
    if token:
        return token
    if token_file is None:
        return None
    try:
        token = token_file.read_text(encoding="utf-8-sig").strip().lstrip("\ufeff")
    except OSError as exc:
        raise EarthdataAuthenticationRequired(
            f"Earthdata token file could not be read: {token_file}"
        ) from exc
    return token or None


def _free_bytes(path: Path) -> int:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(str(probe)).free)


def ensure_free_space(path: Path, minimum_free_gib: float = DEFAULT_MIN_FREE_GIB, *, context: str = "NASA MERRA-2 staging") -> int:
    if minimum_free_gib < 0:
        raise ValueError("minimum free space must not be negative")
    free = _free_bytes(path)
    if free < int(minimum_free_gib * 1024**3):
        raise ProviderAccessError(
            f"Only {free / 1024**3:.2f} GiB free before {context}; required reserve is {minimum_free_gib:.2f} GiB"
        )
    return free


def _safe_close(response) -> None:
    close = getattr(response, "close", None)
    if close:
        close()


def _href_basename(href: str) -> str:
    value = unquote((href or "").split("?", 1)[0].rstrip("/"))
    return value.rsplit("/", 1)[-1]


def _cmr_names(text: str) -> list[str]:
    """Extract producer granule names from CMR JSON or virtual-directory HTML."""
    try:
        document = json.loads(text or "")
    except (TypeError, ValueError):
        document = None
    if isinstance(document, dict):
        entries = (document.get("feed") or {}).get("entry") or []
        names = []
        for entry in entries:
            if isinstance(entry, dict):
                value = entry.get("producer_granule_id") or entry.get("producerGranuleId")
                if value:
                    names.append(_href_basename(str(value)))
        if names:
            return sorted(set(names))
    return sorted({_href_basename(href) for href in html_links(text or "") if _href_basename(href)})


def _aerosol_dataset_name(name: str) -> str | None:
    basename = _href_basename(name)
    if not re.fullmatch(r"MERRA2_\d{3}\.tavgM_2d_aer_Nx\.\d{6}\.nc4", basename):
        return None
    return f"{MERRA_AER_SHORT}:{basename}"


def discover_merra_month(year: int, month: int, session: requests.Session | None = None) -> dict[str, str]:
    """Resolve exactly one current MERRA monthly granule through public CMR."""
    if not 1 <= int(month) <= 12:
        raise ValueError(f"month must be 1..12, got {month}")
    session = session or requests.Session()
    url = f"{CMR_VIRTUAL_DIRECTORY}/{MERRA_AER_COLLECTION}/temporal/{year}/{month:02d}"
    response = session.get(url, timeout=90)
    response.raise_for_status()
    matches = []
    for name in _cmr_names(response.text):
        dataset = _aerosol_dataset_name(name)
        if dataset and dataset.split(":", 1)[1].endswith(f".{year}{month:02d}.nc4"):
            matches.append(dataset)
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise ProviderAccessError(f"expected one MERRA-2 aerosol granule for {year}-{month:02d}, found {matches}")
    return {"year": str(year), "month": f"{month:02d}", "dataset": matches[0], "metadataUrl": url}


def discover_merra_mask(session: requests.Session | None = None) -> dict[str, str]:
    """Resolve and verify the fixed native M2C0NXASM mask through public CMR."""
    session = session or requests.Session()
    url = f"{CMR_VIRTUAL_DIRECTORY}/{MERRA_MASK_COLLECTION}"
    response = session.get(url, timeout=90)
    response.raise_for_status()
    matches = [_href_basename(name) for name in _cmr_names(response.text) if _href_basename(name) == MERRA_MASK_GRANULE]
    if len(set(matches)) != 1:
        raise ProviderAccessError(f"expected canonical MERRA-2 native land mask {MERRA_MASK_GRANULE}, found {sorted(set(matches))}")
    return {"year": "0000", "month": "00", "dataset": f"{MERRA_MASK_SHORT}:{MERRA_MASK_GRANULE}", "metadataUrl": url}


def opendap_granule_url(collection_id: str, dataset_id: str, variables: tuple[str, ...], *, time_size: int = 1, include_time_dimension: bool = True) -> str:
    """Build a DAP4 constraint against native MERRA dimensions."""
    if time_size < 1:
        raise ValueError("time_size must be at least one")
    if not variables:
        raise ValueError("at least one variable is required")
    if include_time_dimension:
        expressions = [f"/{name}[0:{time_size - 1}][0:360][0:575]" for name in variables]
    else:
        expressions = [f"/{name}[0:360][0:575]" for name in variables]
    expressions.extend(("/lat[0:360]", "/lon[0:575]"))
    if include_time_dimension:
        expressions.append("/time[0:0]")
    dataset = quote(dataset_id, safe="")
    return f"{OPENDAP_BASE}/{collection_id}/granules/{dataset}.dap.nc4?{urlencode({'dap4.ce': ';'.join(expressions)})}"


def aerosol_subset_url(dataset_id: str) -> str:
    return opendap_granule_url(MERRA_AER_COLLECTION, dataset_id, AEROSOL_VARIABLES, include_time_dimension=True)


def land_mask_subset_url(dataset_id: str | None = None) -> str:
    return opendap_granule_url(
        MERRA_MASK_COLLECTION,
        dataset_id or f"{MERRA_MASK_SHORT}:{MERRA_MASK_GRANULE}",
        MASK_VARIABLES,
        include_time_dimension=True,
    )


def pm25_formula(fields: dict[str, object]):
    import numpy as np
    return (
        np.asarray(fields["DUSMASS25"], dtype=np.float64)
        + np.asarray(fields["OCSMASS"], dtype=np.float64)
        + np.asarray(fields["BCSMASS"], dtype=np.float64)
        + np.asarray(fields["SSSMASS25"], dtype=np.float64)
        + np.asarray(fields["SO4SMASS"], dtype=np.float64) * SULFATE_AMMONIUM_FACTOR
    ) * 1e9


def probe_anonymous_access(session: requests.Session) -> dict:
    url = aerosol_subset_url(f"{MERRA_AER_SHORT}:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4")
    response = session.get(url, allow_redirects=False, timeout=45)
    location = response.headers.get("Location")
    result = {
        "url": url,
        "status": response.status_code,
        "location": location,
        "requiresEarthdataLogin": response.status_code in {401, 403} or is_earthdata_login_redirect(location),
    }
    _safe_close(response)
    return result


def _discovery_report(session: requests.Session, year: int, month: int) -> dict:
    report = {}
    for key, resolver in (
        ("aerosol", lambda: discover_merra_month(year, month, session)),
        ("landMask", lambda: discover_merra_mask(session)),
    ):
        try:
            report[key] = {"status": "ok", **resolver()}
        except Exception as exc:
            report[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return report


def write_access_report(output_root: Path, session: requests.Session, *, token_present: bool, start_year: int = DEFAULT_START_YEAR, end_year: int = DEFAULT_END_YEAR) -> Path:
    report = {
        "schemaVersion": 2,
        "generatedAt": date.today().isoformat(),
        "provider": "NASA GES DISC MERRA-2",
        "scope": {"startYear": start_year, "endYear": end_year},
        "defaultScope": "1993-2025",
        "anonymousProbe": probe_anonymous_access(session),
        "publicCmrPreflight": _discovery_report(session, start_year, 1),
        "tokenPresent": token_present,
        "canonicalCollection": "M2TMNXAER.5.12.4 / tavgM_2d_aer_Nx",
        "landMaskCollection": "M2C0NXASM.5.12.4 / const_2d_asm_Nx (FRLAND; AREA derived from native coordinates)",
        "actionRequired": "Create/sign in to NASA Earthdata Login, authorize the GES DISC application, generate a user token, and rerun with EARTHDATA_TOKEN set or --earthdata-token-file.",
        "tokenInstructions": "https://urs.earthdata.nasa.gov/documentation/for_users/user_token",
        "dataAccessInstructions": "https://urs.earthdata.nasa.gov/documentation/for_users/data_access/curl_and_wget",
        "cachePolicy": "NASA subsets require request identity, native NetCDF structure, size, and checksum validation; annual checkpoints require all 12 months.",
        "storagePolicy": "Raw subsets and checkpoints stay on the operator staging volume and are omitted from the published family context.",
    }
    path = output_root / "direct-access-report.json"
    write_json(path, report)
    return path


def _filled_array(values, dtype):
    import numpy as np
    if np.ma.isMaskedArray(values):
        values = np.ma.filled(values, np.nan)
    return np.asarray(values, dtype=dtype)


def _native_coordinate_check(latitude, longitude) -> None:
    import numpy as np
    expected_latitudes = np.linspace(-90.0, 90.0, NATIVE_LAT_COUNT, dtype=np.float64)
    expected_longitudes = -180.0 + np.arange(NATIVE_LON_COUNT, dtype=np.float64) * 0.625
    if (
        latitude.shape != expected_latitudes.shape
        or longitude.shape != expected_longitudes.shape
        or not np.allclose(latitude, expected_latitudes, rtol=0.0, atol=1e-5)
        or not np.allclose(longitude, expected_longitudes, rtol=0.0, atol=1e-5)
    ):
        raise ProviderAccessError("MERRA-2 subset coordinates do not match the native 0.625 x 0.5 degree grid")


def derive_native_cell_area(latitude, longitude):
    """Derive m2 cell areas from the native center coordinates."""
    import numpy as np
    latitude = np.asarray(latitude, dtype=np.float64)
    longitude = np.asarray(longitude, dtype=np.float64)
    _native_coordinate_check(latitude, longitude)
    lat_radians = np.deg2rad(latitude)
    lat_edges = np.empty(latitude.size + 1, dtype=np.float64)
    lat_edges[1:-1] = (lat_radians[:-1] + lat_radians[1:]) / 2.0
    lat_edges[0] = -np.pi / 2.0
    lat_edges[-1] = np.pi / 2.0
    lon_step = float(np.median(np.diff(longitude)))
    if not np.isfinite(lon_step) or lon_step <= 0:
        raise ProviderAccessError("MERRA-2 longitude coordinates cannot derive positive cell widths")
    band_area = EARTH_RADIUS_M * EARTH_RADIUS_M * np.diff(np.sin(lat_edges)) * np.deg2rad(lon_step)
    return np.broadcast_to(band_area[:, None], (NATIVE_LAT_COUNT, NATIVE_LON_COUNT)).copy()


def _validate_subset_netcdf(
    path: Path,
    variable_names: tuple[str, ...],
    *,
    include_time_dimension: bool,
    expected_year: int | None = None,
    expected_month: int | None = None,
) -> dict:
    """Reject HTML, truncated files, wrong ranks, units, grids, and months."""
    import numpy as np
    from netCDF4 import Dataset
    if not path.exists() or path.stat().st_size <= 0:
        raise ProviderAccessError(f"NASA subset is missing or empty: {path}")
    with path.open("rb") as stream:
        signature = stream.read(8)
    if signature not in (b"\x89HDF\r\n\x1a\n", b"CDF\x01", b"CDF\x02", b"CDF\x05"):
        raise ProviderAccessError(f"NASA subset is not a NetCDF file (HTML/error or unknown response): {path}")
    try:
        with Dataset(path, "r") as dataset:
            required = set(variable_names) | {"lat", "lon"}
            missing = sorted(required - set(dataset.variables))
            if missing:
                raise ProviderAccessError(f"NASA subset is missing variables {missing}: {path}")
            if set(dataset.dimensions) < {"lat", "lon"}:
                raise ProviderAccessError(f"NASA subset is missing native lat/lon dimensions: {path}")
            if len(dataset.dimensions["lat"]) != NATIVE_LAT_COUNT or len(dataset.dimensions["lon"]) != NATIVE_LON_COUNT:
                raise ProviderAccessError(f"NASA subset has non-native dimension sizes: {path}")
            dataset.variables["lat"].set_auto_maskandscale(False)
            dataset.variables["lon"].set_auto_maskandscale(False)
            latitude = _filled_array(dataset.variables["lat"][:], np.float64)
            longitude = _filled_array(dataset.variables["lon"][:], np.float64)
            if not np.all(np.isfinite(latitude)) or not np.all(np.isfinite(longitude)):
                raise ProviderAccessError(f"NASA subset coordinates contain non-finite values: {path}")
            _native_coordinate_check(latitude, longitude)
            expected_dimensions = ("time", "lat", "lon") if include_time_dimension else ("lat", "lon")
            expected_shape = (
                (NATIVE_TIME_COUNT, NATIVE_LAT_COUNT, NATIVE_LON_COUNT)
                if include_time_dimension
                else (NATIVE_LAT_COUNT, NATIVE_LON_COUNT)
            )
            time_values = None
            if include_time_dimension:
                if "time" not in dataset.dimensions or len(dataset.dimensions["time"]) != NATIVE_TIME_COUNT or "time" not in dataset.variables:
                    raise ProviderAccessError(f"NASA subset has no single native time dimension: {path}")
                time_variable = dataset.variables["time"]
                time_variable.set_auto_maskandscale(False)
                time_values = _filled_array(time_variable[:], np.float64)
                if time_values.shape != (NATIVE_TIME_COUNT,) or not np.all(np.isfinite(time_values)):
                    raise ProviderAccessError(f"NASA subset has invalid time coordinates: {path}")
                if expected_year is not None or expected_month is not None:
                    if expected_year is None or expected_month is None:
                        raise ValueError("expected_year and expected_month must be supplied together")
                    units = str(getattr(time_variable, "units", "")).strip()
                    if not units:
                        raise ProviderAccessError(f"NASA subset time coordinates have no CF units: {path}")
                    try:
                        from netCDF4 import num2date
                        decoded = num2date(
                            time_values,
                            units=units,
                            calendar=str(getattr(time_variable, "calendar", "standard")),
                            only_use_cftime_datetimes=False,
                            only_use_python_datetimes=False,
                        )
                    except Exception as exc:
                        raise ProviderAccessError(f"NASA subset time coordinates are not CF-decodable: {path}") from exc
                    point = decoded[0]
                    if int(point.year) != expected_year or int(point.month) != expected_month:
                        raise ProviderAccessError(
                            f"NASA subset time {point.year}-{point.month:02d} does not match requested "
                            f"{expected_year}-{expected_month:02d}: {path}"
                        )
            for name in variable_names:
                variable = dataset.variables[name]
                variable.set_auto_maskandscale(True)
                units = str(getattr(variable, "units", "")).strip().lower().replace(" ", "")
                if name in AEROSOL_VARIABLES:
                    if units not in {"kgm-3", "kg/m3", "kgm^-3", "kgm**-3"}:
                        raise ProviderAccessError(f"NASA aerosol variable {name} must use kg m-3 units, got {units!r}: {path}")
                elif name == "FRLAND" and units != "1":
                    raise ProviderAccessError(f"NASA FRLAND must use unit 1, got {units!r}: {path}")
                if tuple(variable.dimensions) != expected_dimensions or tuple(variable.shape) != expected_shape:
                    raise ProviderAccessError(
                        f"NASA variable {name} has dimensions {variable.dimensions}/{variable.shape}; "
                        f"expected {expected_dimensions}/{expected_shape}"
                    )
                if include_time_dimension:
                    variable[0:1, 0:1, 0:1]
                else:
                    variable[0:1, 0:1]
    except ProviderAccessError:
        raise
    except Exception as exc:
        raise ProviderAccessError(f"NASA subset failed NetCDF validation ({type(exc).__name__}): {path}") from exc
    return {
        "variables": list(variable_names),
        "includeTimeDimension": include_time_dimension,
        "timeCount": NATIVE_TIME_COUNT if include_time_dimension else 0,
        "latitudeCount": NATIVE_LAT_COUNT,
        "longitudeCount": NATIVE_LON_COUNT,
        "expectedYear": expected_year,
        "expectedMonth": expected_month,
    }


def _read_subset(
    path: Path,
    variable_names: tuple[str, ...],
    *,
    include_time_dimension: bool,
    expected_year: int | None = None,
    expected_month: int | None = None,
) -> tuple[dict[str, object], object, object]:
    import numpy as np
    from netCDF4 import Dataset
    _validate_subset_netcdf(
        path,
        variable_names,
        include_time_dimension=include_time_dimension,
        expected_year=expected_year,
        expected_month=expected_month,
    )
    fields = {}
    with Dataset(path, "r") as dataset:
        dataset.variables["lat"].set_auto_maskandscale(False)
        dataset.variables["lon"].set_auto_maskandscale(False)
        for name in variable_names:
            variable = dataset.variables[name]
            variable.set_auto_maskandscale(True)
            fields[name] = _filled_array(variable[:], np.float64).squeeze()
        latitude = _filled_array(dataset.variables["lat"][:], np.float64)
        longitude = _filled_array(dataset.variables["lon"][:], np.float64)
    return fields, latitude, longitude


def _load_land_mask(path: Path) -> tuple[object, object, object, object]:
    fields, latitude, longitude = _read_subset(path, MASK_VARIABLES, include_time_dimension=True)
    import numpy as np
    frland = np.asarray(fields["FRLAND"], dtype=np.float64)
    if frland.ndim != 2 or frland.shape != (NATIVE_LAT_COUNT, NATIVE_LON_COUNT):
        raise ProviderAccessError(f"M2C0NXASM FRLAND did not reduce to native 2D grid: {path}")
    finite_fraction = np.isfinite(frland)
    if np.any(frland[finite_fraction] < 0) or np.any(frland[finite_fraction] > 1):
        raise ProviderAccessError("M2C0NXASM FRLAND contains values outside 0..1")
    grid_area = derive_native_cell_area(latitude, longitude)
    return frland, grid_area, latitude, longitude


def _month_path(raw_root: Path, year: int, dataset: str) -> Path:
    filename = dataset.split(":", 1)[-1]
    return raw_root / "pollution" / "nasa_merra2" / "monthly" / str(year) / f"{filename}.subset.nc4"


def _cache_sidecar(path: Path) -> Path:
    return Path(f"{path}.request.json")


def _partial_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.part")


def _partial_sidecar(path: Path) -> Path:
    return Path(f"{_partial_path(path)}.request.json")


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _quarantine(path: Path) -> Path | None:
    if not path.exists():
        return None
    target = path.with_name(f".{path.name}.invalid-{time.time_ns()}-{uuid.uuid4().hex[:8]}")
    path.replace(target)
    return target


def _request_identity(*, url: str, collection_id: str, dataset_id: str, variables: tuple[str, ...], include_time_dimension: bool) -> dict:
    spec = {
        "schemaVersion": REQUEST_SCHEMA_VERSION,
        "protocol": "NASA GES DISC Cloud OPeNDAP DAP4",
        "url": url,
        "collectionId": collection_id,
        "datasetId": dataset_id,
        "variables": list(variables),
        "includeTimeDimension": include_time_dimension,
        "nativeShape": ([NATIVE_TIME_COUNT, NATIVE_LAT_COUNT, NATIVE_LON_COUNT] if include_time_dimension else [NATIVE_LAT_COUNT, NATIVE_LON_COUNT]),
        "pm25MethodVersion": PM25_METHOD_VERSION,
        "areaMethodVersion": AREA_METHOD_VERSION,
    }
    return {**spec, "requestHash": _json_hash(spec)}


def _request_metadata_matches(path: Path, request_identity: dict) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("requestIdentity") == request_identity
    except (OSError, ValueError):
        return False


def _cached_subset_is_valid(
    path: Path,
    request_identity: dict,
    variable_names: tuple[str, ...],
    *,
    include_time_dimension: bool,
    expected_year: int | None = None,
    expected_month: int | None = None,
) -> bool:
    sidecar = _cache_sidecar(path)
    if not path.exists() or not sidecar.exists() or not _request_metadata_matches(sidecar, request_identity):
        return False
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if int(metadata.get("sizeBytes", -1)) != path.stat().st_size:
            return False
        if metadata.get("sha256") != _sha256_file(path):
            return False
        _validate_subset_netcdf(
            path,
            variable_names,
            include_time_dimension=include_time_dimension,
            expected_year=expected_year,
            expected_month=expected_month,
        )
    except (OSError, ValueError, ProviderAccessError):
        return False
    return True


def _download_subset(
    *,
    session: requests.Session,
    url: str,
    destination: Path,
    token: str,
    timeout: int,
    skip_download: bool,
    request_identity: dict,
    variable_names: tuple[str, ...],
    include_time_dimension: bool,
    minimum_free_gib: float,
    expected_year: int | None = None,
    expected_month: int | None = None,
) -> Path:
    """Download one NASA subset with request-bound partial and final caches."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar = _cache_sidecar(destination)
    partial = _partial_path(destination)
    partial_sidecar = _partial_sidecar(destination)
    if _cached_subset_is_valid(
        destination,
        request_identity,
        variable_names,
        include_time_dimension=include_time_dimension,
        expected_year=expected_year,
        expected_month=expected_month,
    ):
        return destination
    if destination.exists():
        _quarantine(destination)
    if sidecar.exists():
        _quarantine(sidecar)
    if skip_download:
        raise ProviderAccessError(f"missing staged NASA subset with --skip-download: {destination}")
    if partial.exists() and not _request_metadata_matches(partial_sidecar, request_identity):
        _quarantine(partial)
        if partial_sidecar.exists():
            _quarantine(partial_sidecar)
    write_json(partial_sidecar, {"schemaVersion": REQUEST_SCHEMA_VERSION, "requestIdentity": request_identity, "startedAt": _utc_now()})
    ensure_free_space(destination.parent, minimum_free_gib, context=f"NASA MERRA-2 subset {request_identity['datasetId']}")
    try:
        result = download_resumable(session, url, destination, headers=earthdata_headers(token), timeout=timeout)
        _validate_subset_netcdf(
            result,
            variable_names,
            include_time_dimension=include_time_dimension,
            expected_year=expected_year,
            expected_month=expected_month,
        )
        metadata = {
            "schemaVersion": REQUEST_SCHEMA_VERSION,
            "requestIdentity": request_identity,
            "sizeBytes": result.stat().st_size,
            "sha256": _sha256_file(result),
            "validatedNetcdf": _validate_subset_netcdf(
                result,
                variable_names,
                include_time_dimension=include_time_dimension,
                expected_year=expected_year,
                expected_month=expected_month,
            ),
            "completedAt": _utc_now(),
        }
        ensure_free_space(destination.parent, minimum_free_gib, context="NASA MERRA-2 post-download reserve")
        write_json(sidecar, metadata)
        partial_sidecar.unlink(missing_ok=True)
        return result
    except EarthdataAuthenticationRequired:
        raise
    except Exception:
        if destination.exists() and not _cached_subset_is_valid(
            destination,
            request_identity,
            variable_names,
            include_time_dimension=include_time_dimension,
            expected_year=expected_year,
            expected_month=expected_month,
        ):
            _quarantine(destination)
            if sidecar.exists():
                _quarantine(sidecar)
        raise


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _month_checkpoint_path(work_root: Path, year: int, month: int) -> Path:
    return work_root / "monthly" / f"{year}-{month:02d}.json"


def _read_month_checkpoint(
    path: Path,
    *,
    year: int,
    month: int,
    request_identity: dict,
    subset_path: Path,
) -> dict | None:
    record = _read_json(path)
    if not isinstance(record, dict) or (
        record.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
        or record.get("status") != "complete"
        or record.get("year") != year
        or record.get("month") != month
        or record.get("requestIdentity") != request_identity
        or record.get("subsetPath") != str(subset_path)
    ):
        return None
    if not _cached_subset_is_valid(
        subset_path,
        request_identity,
        AEROSOL_VARIABLES,
        include_time_dimension=True,
        expected_year=year,
        expected_month=month,
    ):
        return None
    try:
        days = int(record["days"])
        weighted = float(record["weightedPm25Days"])
        area_days = float(record["validAreaDays"])
    except (KeyError, TypeError, ValueError):
        return None
    if days != calendar.monthrange(year, month)[1] or not all(
        value >= 0 and value == value and value not in (float("inf"), float("-inf"))
        for value in (weighted, area_days)
    ):
        return None
    return record


def _annual_identity(*, start_year: int, end_year: int, year: int, month_records: list[dict], mask_request_identity: dict, healthy_ugm3: float, critical_ugm3: float) -> dict:
    spec = {
        "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
        "scope": {"startYear": start_year, "endYear": end_year},
        "year": year,
        "aerosolCollection": MERRA_AER_COLLECTION,
        "aerosolVariables": list(AEROSOL_VARIABLES),
        "landMaskCollection": MERRA_MASK_COLLECTION,
        "landMaskVariables": list(MASK_VARIABLES),
        "maskRequestHash": mask_request_identity["requestHash"],
        "pm25MethodVersion": PM25_METHOD_VERSION,
        "areaMethodVersion": AREA_METHOD_VERSION,
        "healthyUgM3": healthy_ugm3,
        "criticalUgM3": critical_ugm3,
        "months": [
            {"month": int(record["month"]), "dataset": record["dataset"], "requestHash": record["requestIdentity"]["requestHash"]}
            for record in month_records
        ],
    }
    return {**spec, "identityHash": _json_hash(spec)}


def _read_complete_annual_checkpoint(path: Path, *, year: int, annual_identity: dict, month_records: list[dict], work_root: Path, subset_paths: dict[int, Path]) -> dict | None:
    cached = _read_json(path)
    if not isinstance(cached, dict) or (
        cached.get("schemaVersion") != CHECKPOINT_SCHEMA_VERSION
        or cached.get("status") != "complete"
        or cached.get("year") != year
        or cached.get("annualIdentity") != annual_identity
    ):
        return None
    months = cached.get("months")
    if (
        not isinstance(month_records, list)
        or len(month_records) != 12
        or any(not isinstance(source, dict) for source in month_records)
        or [source.get("month") for source in month_records] != list(range(1, 13))
        or not isinstance(months, list)
        or len(months) != 12
        or any(not isinstance(item, dict) for item in months)
        or [item.get("month") for item in months] != list(range(1, 13))
    ):
        return None
    for month, source in enumerate(month_records, start=1):
        cached_month = months[month - 1]
        source_identity = source.get("requestIdentity")
        if (
            cached_month.get("dataset") != source.get("dataset")
            or cached_month.get("requestHash") != (
                source_identity.get("requestHash") if isinstance(source_identity, dict) else None
            )
        ):
            return None
        if _read_month_checkpoint(
            _month_checkpoint_path(work_root, year, month),
            year=year,
            month=month,
            request_identity=source["requestIdentity"],
            subset_path=subset_paths[month],
        ) is None:
            return None
    row = cached.get("row")
    if (
        not isinstance(row, dict)
        or row.get("year") != year
        or row.get("temporalCoverage") != 1.0
        or row.get("observedMonths") != 12
        or row.get("expectedMonths") != 12
        or row.get("sourceMonths") != months
    ):
        return None
    try:
        score = float(row["score"])
        raw = float(row["raw"])
        coverage = float(row["coverage"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(value == value and value not in (float("inf"), float("-inf")) for value in (score, raw, coverage)):
        return None
    if not (0.0 <= score <= 100.0 and raw >= 0.0 and 0.0 <= coverage <= 1.0):
        return None
    return row


def _month_source_record(granule: dict[str, str]) -> dict:
    dataset = granule["dataset"]
    url = aerosol_subset_url(dataset)
    return {
        "month": int(granule["month"]),
        "dataset": dataset,
        "metadataUrl": granule["metadataUrl"],
        "url": url,
        "requestIdentity": _request_identity(
            url=url,
            collection_id=MERRA_AER_COLLECTION,
            dataset_id=dataset,
            variables=AEROSOL_VARIABLES,
            include_time_dimension=True,
        ),
    }


def _emit_progress(path: Path, *, status: str, year: int | None = None, month: int | None = None, detail: str = "") -> None:
    record = {"timestamp": _utc_now(), "status": status}
    if year is not None:
        record["year"] = year
    if month is not None:
        record["month"] = month
    if detail:
        record["detail"] = detail
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    label = f"{year}-{month:02d}" if year is not None and month is not None else ""
    print(f"[NASA MERRA-2] {label} {status}{(': ' + detail) if detail else ''}".rstrip(), flush=True)


def _validate_build_args(args: argparse.Namespace) -> None:
    if args.start_year < HISTORY_START_YEAR:
        raise ValueError(f"start year must be at least {HISTORY_START_YEAR}")
    if args.end_year < args.start_year:
        raise ValueError(f"end year {args.end_year} precedes start year {args.start_year}")
    if args.end_year >= date.today().year:
        raise ValueError(f"end year {args.end_year} is not a closed calendar year; partial annual claims are refused")
    if args.healthy_ugm3 < 0 or args.critical_ugm3 <= args.healthy_ugm3:
        raise ValueError("healthy/critical PM2.5 thresholds must satisfy 0 <= healthy < critical")
    if args.min_free_gib < 0:
        raise ValueError("minimum free space must not be negative")


def build(args: argparse.Namespace) -> Path:
    import numpy as np
    _validate_build_args(args)
    raw_root = _require_f_path(args.raw_root, "raw root")
    output_root = _require_f_path(args.output_root, "output root")
    work_root = _require_f_path(args.work_root or (output_root / "pollution-work"), "work root")
    if args.earthdata_token_file:
        _require_f_path(args.earthdata_token_file, "Earthdata token file")
    ensure_free_space(raw_root, args.min_free_gib, context="NASA MERRA-2 build start")
    token = _read_nasa_bearer_token(args.earthdata_token_file)
    session = requests.Session()
    session.headers.update({"User-Agent": "MotherWorld-direct-backbone/1.1"})
    report_path = write_access_report(session=session, output_root=output_root, token_present=bool(token), start_year=args.start_year, end_year=args.end_year)
    if args.preflight_only:
        print(f"NASA public CMR/anonymous preflight report -> {report_path}")
        return report_path
    if not token:
        raise EarthdataAuthenticationRequired(
            f"{report_path}: NASA GES DISC is not anonymous. Create/sign in to NASA Earthdata Login, authorize GES DISC, "
            "generate a user token, then set EARTHDATA_TOKEN or --earthdata-token-file on F:."
        )

    probe_url = aerosol_subset_url(f"{MERRA_AER_SHORT}:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4")
    probe = session.get(probe_url, headers=earthdata_headers(token), allow_redirects=False, timeout=args.timeout)
    try:
        if probe.status_code in {401, 403} or is_earthdata_login_redirect(probe.headers.get("Location", "")):
            raise EarthdataAuthenticationRequired("The supplied Earthdata token was rejected by NASA GES DISC Earthdata Login.")
        raise_for_provider_response(probe, probe_url)
        content_type = (probe.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type or probe.content[:1] in (b"<", b"!"):
            raise ProviderAccessError("NASA token probe returned HTML instead of a GES DISC data response")
    finally:
        _safe_close(probe)

    mask_granule = discover_merra_mask(session)
    mask_dataset = mask_granule["dataset"]
    mask_url = land_mask_subset_url(mask_dataset)
    mask_request_identity = _request_identity(
        url=mask_url,
        collection_id=MERRA_MASK_COLLECTION,
        dataset_id=mask_dataset,
        variables=MASK_VARIABLES,
        include_time_dimension=True,
    )
    mask_destination = raw_root / "pollution" / "nasa_merra2" / "mask" / f"{MERRA_MASK_GRANULE}.subset.nc4"
    mask_path = _download_subset(
        session=session,
        url=mask_url,
        destination=mask_destination,
        token=token,
        timeout=args.timeout,
        skip_download=args.skip_download,
        request_identity=mask_request_identity,
        variable_names=MASK_VARIABLES,
        include_time_dimension=True,
        minimum_free_gib=args.min_free_gib,
    )
    frland, grid_area, latitudes, longitudes = _load_land_mask(mask_path)
    latitude_selection = (latitudes >= -60.0) & (latitudes <= 85.0)
    if not latitude_selection.any():
        raise ProviderAccessError("M2C0NXASM land mask has no configured Earth Health latitude band")
    valid_mask = np.isfinite(frland) & np.isfinite(grid_area) & (frland > 0) & (grid_area > 0)
    land_weights = np.where(latitude_selection[:, None] & valid_mask, frland * grid_area, 0.0)
    total_land_area = float(np.sum(land_weights))
    if not np.isfinite(total_land_area) or total_land_area <= 0:
        raise ProviderAccessError("M2C0NXASM FRLAND and derived native cell area produced no positive land area")

    work_root.mkdir(parents=True, exist_ok=True)
    progress_path = work_root / "progress.jsonl"
    _emit_progress(progress_path, status="build-start", detail=f"scope={args.start_year}-{args.end_year}; reserve={args.min_free_gib:g}GiB")
    records_by_year: dict[int, list[dict]] = {}
    for year in date_range_years(args.start_year, args.end_year):
        records = []
        for month in range(1, 13):
            source = _month_source_record(discover_merra_month(year, month, session))
            records.append(source)
            _emit_progress(progress_path, status="metadata", year=year, month=month, detail="CMR granule resolved")
        if len(records) != 12:
            raise ProviderAccessError(f"MERRA-2 year {year} did not resolve all 12 monthly granules")
        records_by_year[year] = records

    annual_paths = work_root / "annual"
    annual_paths.mkdir(parents=True, exist_ok=True)
    rows = []
    for year in date_range_years(args.start_year, args.end_year):
        records = records_by_year[year]
        subset_paths = {month: _month_path(raw_root, year, source["dataset"]) for month, source in enumerate(records, start=1)}
        annual_identity = _annual_identity(
            start_year=args.start_year,
            end_year=args.end_year,
            year=year,
            month_records=records,
            mask_request_identity=mask_request_identity,
            healthy_ugm3=args.healthy_ugm3,
            critical_ugm3=args.critical_ugm3,
        )
        annual_checkpoint = annual_paths / f"{year}.json"
        cached_row = _read_complete_annual_checkpoint(
            annual_checkpoint,
            year=year,
            annual_identity=annual_identity,
            month_records=records,
            work_root=work_root,
            subset_paths=subset_paths,
        )
        if cached_row is not None:
            rows.append(cached_row)
            _emit_progress(progress_path, status="annual-cached", year=year, detail="12 validated month checkpoints reused")
            continue

        weighted_sum = 0.0
        valid_area_days = 0.0
        total_days = 0
        month_summaries = []
        for month, source in enumerate(records, start=1):
            subset_path = subset_paths[month]
            month_checkpoint = _month_checkpoint_path(work_root, year, month)
            month_cached = _read_month_checkpoint(
                month_checkpoint,
                year=year,
                month=month,
                request_identity=source["requestIdentity"],
                subset_path=subset_path,
            )
            if month_cached is not None:
                month_result = month_cached
                _emit_progress(progress_path, status="month-cached", year=year, month=month, detail="validated subset/checkpoint reused")
            else:
                subset_path = _download_subset(
                    session=session,
                    url=source["url"],
                    destination=subset_path,
                    token=token,
                    timeout=args.timeout,
                    skip_download=args.skip_download,
                    request_identity=source["requestIdentity"],
                    variable_names=AEROSOL_VARIABLES,
                    include_time_dimension=True,
                    minimum_free_gib=args.min_free_gib,
                    expected_year=year,
                    expected_month=month,
                )
                fields, month_latitudes, month_longitudes = _read_subset(
                    subset_path,
                    AEROSOL_VARIABLES,
                    include_time_dimension=True,
                    expected_year=year,
                    expected_month=month,
                )
                if not (np.allclose(month_latitudes, latitudes) and np.allclose(month_longitudes, longitudes)):
                    raise ProviderAccessError(f"MERRA-2 grid changed in {subset_path}")
                pm25 = pm25_formula(fields)
                days = calendar.monthrange(year, month)[1]
                valid = np.isfinite(pm25) & (pm25 >= 0) & (pm25 < 1e10) & (land_weights > 0)
                weighted_sum_month = float(np.sum(np.where(valid, pm25 * land_weights, 0.0))) * days
                valid_area_days_month = float(np.sum(np.where(valid, land_weights, 0.0))) * days
                month_result = {
                    "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
                    "status": "complete",
                    "year": year,
                    "month": month,
                    "days": days,
                    "weightedPm25Days": weighted_sum_month,
                    "validAreaDays": valid_area_days_month,
                    "requestIdentity": source["requestIdentity"],
                    "subsetPath": str(subset_path),
                    "sourceDataset": source["dataset"],
                    "metadataUrl": source["metadataUrl"],
                    "completedAt": _utc_now(),
                }
                write_json(month_checkpoint, month_result)
                _emit_progress(progress_path, status="month-complete", year=year, month=month, detail="subset validated and reduced")
            weighted_sum += float(month_result["weightedPm25Days"])
            valid_area_days += float(month_result["validAreaDays"])
            total_days += int(month_result["days"])
            month_summaries.append({
                "month": month,
                "dataset": source["dataset"],
                "requestHash": source["requestIdentity"]["requestHash"],
                "days": int(month_result["days"]),
            })

        expected_days = 366 if calendar.isleap(year) else 365
        if len(month_summaries) != 12 or [item["month"] for item in month_summaries] != list(range(1, 13)):
            raise ProviderAccessError(f"MERRA-2 annual reduction for {year} is missing one or more calendar months")
        if total_days != expected_days:
            raise ProviderAccessError(f"MERRA-2 annual reduction for {year} covers {total_days} days; expected {expected_days}")
        if valid_area_days <= 0:
            raise ProviderAccessError(f"MERRA-2 produced no valid land PM2.5 for {year}")
        raw = weighted_sum / valid_area_days
        row = {
            "year": year,
            "score": max(0.0, min(100.0, 100.0 * (args.critical_ugm3 - raw) / (args.critical_ugm3 - args.healthy_ugm3))),
            "coverage": min(1.0, valid_area_days / (total_land_area * expected_days)),
            "temporalCoverage": 1.0,
            "observedMonths": 12,
            "expectedMonths": 12,
            "raw": raw,
            "unit": "ug/m3 reconstructed surface PM2.5",
            "sourceCollection": MERRA_AER_SHORT,
            "sourceMonths": month_summaries,
        }
        annual_record = {
            "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
            "status": "complete",
            "year": year,
            "annualIdentity": annual_identity,
            "months": month_summaries,
            "row": row,
            "completedAt": _utc_now(),
        }
        write_json(annual_checkpoint, annual_record)
        rows.append(row)
        _emit_progress(progress_path, status="annual-complete", year=year, detail="12/12 months; annual checkpoint committed")

    expected_rows = args.end_year - args.start_year + 1
    if len(rows) != expected_rows:
        raise ProviderAccessError(f"refusing to publish partial MERRA-2 history: got {len(rows)} annual rows, expected {expected_rows}")
    payload = family_payload(
        family_id="pollution",
        label="Pollution",
        series=rows,
        component={"id": "merra2_pm25", "label": "Global ambient particulate burden", "weight": 1.0, "source": "NASA MERRA-2 aerosol reanalysis"},
        source={
            "id": "nasa-merra2-aerosol",
            "label": "NASA MERRA-2 aerosol reanalysis",
            "collection": "M2TMNXAER.5.12.4 / tavgM_2d_aer_Nx",
            "collectionId": MERRA_AER_COLLECTION,
            "access": "https://disc.gsfc.nasa.gov/datasets/M2TMNXAER_5.12.4/summary",
        },
        method={
            "historicalScope": f"{args.start_year}-{args.end_year}",
            "pm25Formula": "DUSMASS25 + OCSMASS + BCSMASS + SSSMASS25 + SO4SMASS*(132.14/96.06), then x1e9",
            "spatialAggregation": "calendar-day-weighted annual mean weighted by derived native spherical cell area x FRLAND over lat -60..85",
            "areaMethod": "M2C0NXASM currently omits AREA; derive exact spherical cell areas from native 0.5 degree latitude and 0.625 degree longitude center bounds using Earth radius 6371008.8 m",
            "temporalAggregation": "calendar-day-weighted mean of official MERRA-2 monthly means",
            "normalization": {"type": "linear", "healthyUgM3": args.healthy_ugm3, "criticalUgM3": args.critical_ugm3},
            "scoreDirection": "100 = lowest particulate burden under configured reference",
            "sourceCompleteness": "A scored year is emitted only after all 12 CMR-resolved monthly granules and validated native subsets are complete.",
            "nativeGrid": "0.625 degree longitude x 0.5 degree latitude; aerosol and FRLAND [time=1,lat=361,lon=576]",
            "diagnosticPolicy": "CAMS, Sentinel-5P/TROPOMI, land/water pollution and contaminants remain present-day diagnostics only",
        },
        context={
            "providerPath": "NASA GES DISC Cloud OPeNDAP DAP4 variable subsets; Earthdata token required",
            "aerosolCollectionId": MERRA_AER_COLLECTION,
            "landMask": "M2C0NXASM.5.12.4 FRLAND; AREA derived from native coordinates because the current NASA mask removed AREA",
            "historyScope": f"{args.start_year}-{args.end_year}",
            "cachePolicy": "Per-month request identity plus NetCDF structure/size/checksum validation; annual checkpoints require 12 complete months.",
            "storagePolicy": "Raw subsets and checkpoints remain on the operator staging volume; staging paths are intentionally omitted from this published payload.",
            "caveat": "MERRA-2 PM2.5 is a reanalysis proxy; its standard reconstruction does not include nitrate aerosol and includes natural aerosol contributions.",
        },
    )
    output = output_family_path(output_root, "pollution")
    write_json(output, payload)
    print(f"Pollution direct backbone latest={payload['latestBackboneYear']} score={payload['score']} -> {output}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the NASA MERRA-2 pollution Earth Health backbone without Earth Engine.")
    parser.add_argument("--raw-root", type=Path, default=Path("F:/BiomeSummary/MotherWorld-v8-dataset-downloader/earth_health_backbone_raw"))
    parser.add_argument("--output-root", type=Path, default=Path("F:/BiomeSummary/.cache/motherworld/v8-build/direct-noaa-nasa"))
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--healthy-ugm3", type=float, default=5.0)
    parser.add_argument("--critical-ugm3", type=float, default=50.0)
    parser.add_argument("--earthdata-token-file", type=Path, help="F: file containing an Earthdata bearer token")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--preflight-only", action="store_true", help="write public CMR/anonymous access evidence without credentials or downloads")
    parser.add_argument("--min-free-gib", type=float, default=DEFAULT_MIN_FREE_GIB)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        build(parse_args())
    except EarthdataAuthenticationRequired as error:
        print(f"BLOCKED: {error}")
        raise SystemExit(2)
    except ProviderAccessError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
