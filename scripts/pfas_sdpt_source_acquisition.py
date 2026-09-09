"""Acquire and validate the official inputs for the PFAS/SDPT v8 providers.

The Census TIGER/Line ZIP endpoints are currently returning HTTP 403 from the
build host.  Census also publishes the same 2020 ZCTA vintage through the
official TIGERweb Census2020 REST service; this module uses that service only
for the ZIP codes present in the UCMR5 service-area file.  It requests the
complete polygon geometry and writes a normal GeoJSON FeatureCollection that
the provider builder can consume.

WRI's Data Explorer exposes the public SDPT v2.1 file-geodatabase ZIP on the
Global Forest Watch S3 distribution.  The archive is kept in the isolated
stage, and the manifest records the landing page, direct URL, release label,
archive structure, byte counts, and SHA-256 digest.

All outputs are deliberately staged under F:.  No source values are
synthesized when an endpoint or an input is unavailable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
import requests


CENSUS_TIGER_LINE_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2020/ZCTA520/"
    "tl_2020_us_zcta520.zip"
)
CENSUS_CARTOGRAPHIC_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2020/shp/"
    "cb_2020_us_zcta520_500k.zip"
)
CENSUS_TIGERWEB_QUERY_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020/"
    "PUMA_TAD_TAZ_UGA_ZCTA/MapServer/2/query"
)
EPA_UCMR5_PAGE_URL = "https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule"
EPA_UCMR5_ZIP_URL = "https://www.epa.gov/system/files/other-files/2023-08/ucmr5-occurrence-data.zip"
WRI_SDPT_PAGE_URL = "https://datasets.wri.org/datasets/spatial-database-of-planted-trees-sdpt-version-21"
WRI_SDPT_URL = "https://gfw-files.s3.amazonaws.com/plantations/SDPT_v2.1/sdpt_v21_v09152024_public.gdb.zip"

UCMR5_ALL = "UCMR5_All.txt"
UCMR5_ZIPCODES = "UCMR5_ZIPCodes.txt"
EXPECTED_UCMR5_PFAS_COUNT = 29
MIN_FREE_RESERVE_BYTES = 40 * 1024**3
_ZIP5 = re.compile(r"^\d{5}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f_path(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved.drive.upper() == "C:":
        raise ValueError(f"Source staging must stay on F:, got {resolved}")
    return resolved


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path = _f_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _walk_nonfinite(value: Any, path: str = "") -> Iterable[str]:
    if isinstance(value, float) and not math.isfinite(value):
        yield path
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk_nonfinite(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_nonfinite(child, f"{path}[{index}]")


def validate_geojson(payload: Mapping[str, Any], requested_zips: set[str] | None = None) -> dict:
    """Validate a TIGERweb GeoJSON response and return a compact audit."""

    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"Expected GeoJSON FeatureCollection, got {payload.get('type')!r}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON FeatureCollection has no feature list")
    observed: set[str] = set()
    geometry_types: Counter[str] = Counter()
    for index, feature in enumerate(features):
        if not isinstance(feature, Mapping) or feature.get("type") != "Feature":
            raise ValueError(f"Feature {index} is not a GeoJSON Feature")
        properties = feature.get("properties")
        geoid = str((properties or {}).get("GEOID") or "").strip()
        if not _ZIP5.fullmatch(geoid):
            raise ValueError(f"Feature {index} has invalid Census GEOID {geoid!r}")
        if geoid in observed:
            raise ValueError(f"Duplicate Census GEOID {geoid}")
        observed.add(geoid)
        geometry = feature.get("geometry")
        if not isinstance(geometry, Mapping) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Feature {geoid} does not contain a polygon geometry")
        geometry_types[str(geometry["type"])] += 1
    nonfinite = list(_walk_nonfinite(payload))
    if nonfinite:
        raise ValueError(f"GeoJSON contains non-finite numbers at {nonfinite[:3]}")
    missing = sorted((requested_zips or set()) - observed)
    return {
        "featureCount": len(features),
        "geoidCount": len(observed),
        "missingRequestedZips": missing,
        "geometryTypes": dict(sorted(geometry_types.items())),
        "crs": payload.get("crs"),
    }


def read_ucmr5_zipcodes(path: Path) -> set[str]:
    """Read canonical five-digit service ZIP codes from UCMR5_ZIPCodes.txt."""

    path = _f_path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="cp1252", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = {str(field).strip().lower() for field in (reader.fieldnames or [])}
        if not {"pwsid", "zipcode"}.issubset(fields):
            raise ValueError(f"Unexpected UCMR5 ZIP code columns: {reader.fieldnames}")
        zips = set()
        for row in reader:
            value = str(row.get("ZIPCODE") or row.get("ZipCode") or "").strip()[:5]
            if _ZIP5.fullmatch(value):
                zips.add(value)
    if not zips:
        raise ValueError(f"No valid five-digit ZIP codes in {path}")
    return zips


def _query_where(zips: Iterable[str]) -> str:
    values = sorted(set(zips))
    if not values or any(not _ZIP5.fullmatch(value) for value in values):
        raise ValueError("Census query contains an invalid ZIP code")
    quoted = ",".join("'" + value + "'" for value in values)
    return f"GEOID IN ({quoted})"


def query_tigerweb_zctas(
    requested_zips: set[str],
    *,
    destination: Path,
    session: requests.Session | None = None,
    batch_size: int = 100,
    timeout: float = 180.0,
    workers: int = 4,
) -> dict:
    """Fetch complete 2020 ZCTA polygons through official TIGERweb REST."""

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if workers <= 0:
        raise ValueError("workers must be positive")
    requested = set(requested_zips)
    if not requested or any(not _ZIP5.fullmatch(value) for value in requested):
        raise ValueError("requested_zips must contain only five-digit ZIP codes")
    destination = _f_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The national service-area set can contain more than twenty thousand
    # ZCTAs. Keeping every decoded polygon in a Python object graph before
    # writing would multiply the several-gigabyte GeoJSON payload in memory.
    # Stream validated pages instead, keeping at most workers responses
    # buffered while the output remains deterministic by sorted GEOID.
    temporary = destination.with_name(destination.name + ".tmp")
    observed: set[str] = set()
    geometry_types: Counter[str] = Counter()
    feature_count = 0
    batches = [
        sorted(requested)[offset : offset + batch_size]
        for offset in range(0, len(requested), batch_size)
    ]

    def fetch_page(number: int, batch: list[str]) -> tuple[int, list[str], dict]:
        params = {
            "where": _query_where(batch),
            "outFields": "GEOID,BASENAME,NAME,INTPTLAT,INTPTLON",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
        }
        client = session or requests.Session()
        response = client.get(CENSUS_TIGERWEB_QUERY_URL, params=params, timeout=timeout)
        response.raise_for_status()
        try:
            page = response.json()
        except ValueError as exc:
            raise ValueError(f"TIGERweb returned non-JSON for batch {number}") from exc
        validate_geojson(page, set(batch))
        return number, batch, page

    def write_page(handle, page: dict) -> None:
        nonlocal feature_count
        page_features = sorted(
            page["features"],
            key=lambda feature: str(feature["properties"]["GEOID"]).strip(),
        )
        for feature in page_features:
            geoid = str(feature["properties"]["GEOID"]).strip()
            if geoid in observed:
                raise ValueError(f"TIGERweb returned duplicate GEOID {geoid}")
            observed.add(geoid)
            geometry_types[str(feature["geometry"]["type"])] += 1
            encoded = json.dumps(
                feature,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            if feature_count:
                handle.write(",\n")
            handle.write(encoded)
            feature_count += 1

    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write('{"type":"FeatureCollection","features":[\n')
            # Submit only a bounded number of requests at once. executor.map
            # eagerly retains every response, which is too large for national
            # full-geometry output.
            from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

            pending = {}
            next_submit = 0
            next_write = 1
            completed = {}
            with ThreadPoolExecutor(max_workers=min(workers, len(batches) or 1)) as executor:
                while next_submit < len(batches) and len(pending) < workers:
                    batch = batches[next_submit]
                    number = next_submit + 1
                    pending[executor.submit(fetch_page, number, batch)] = number
                    next_submit += 1
                while pending:
                    done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                    for future in done:
                        number = pending.pop(future)
                        completed[number] = future.result()
                        if next_submit < len(batches):
                            batch = batches[next_submit]
                            next_number = next_submit + 1
                            pending[executor.submit(fetch_page, next_number, batch)] = next_number
                            next_submit += 1
                    while next_write in completed:
                        _number, _batch, page = completed.pop(next_write)
                        write_page(handle, page)
                        next_write += 1
            handle.write("\n]}\n")
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    missing = sorted(requested - observed)
    return {
        "featureCount": feature_count,
        "geoidCount": len(observed),
        "missingRequestedZips": missing,
        "geometryTypes": dict(sorted(geometry_types.items())),
        "crs": None,
        "requestedZipCount": len(requested),
        "batchCount": len(batches),
        "workerCount": min(workers, len(batches) or 1),
        "url": CENSUS_TIGERWEB_QUERY_URL,
        "outFields": "GEOID,BASENAME,NAME,INTPTLAT,INTPTLON",
        "outSR": "4326",
        "geometryPolicy": "complete TIGERweb polygon geometry; no simplification",
    }


def validate_sdpt_archive(path: Path) -> dict:
    """Validate the WRI SDPT archive directory and geodatabase members."""

    path = _f_path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"WRI SDPT ZIP CRC check failed for {bad_member}")
        members = archive.infolist()
        gdb_roots = sorted(
            {
                info.filename.split("/", 1)[0]
                for info in members
                if "/" in info.filename and info.filename.lower().endswith(".gdb/")
            }
        )
        if len(gdb_roots) != 1:
            raise ValueError(f"Expected one SDPT file geodatabase root, got {gdb_roots}")
        root = gdb_roots[0]
        members_under_root = [info for info in members if info.filename.startswith(root + "/")]
        table_count = sum(info.filename.lower().endswith(".gdbtable") for info in members_under_root)
        if table_count == 0:
            raise ValueError("WRI SDPT archive has no .gdbtable members")
        return {
            "memberCount": len(members),
            "gdbRoot": root,
            "gdbTableMemberCount": table_count,
            "compressedBytes": sum(int(info.compress_size) for info in members),
            "uncompressedBytes": sum(int(info.file_size) for info in members),
            "zipCrcValidated": True,
            "format": "Esri file geodatabase ZIP",
        }


def extract_sdpt_archive(path: Path, destination: Path) -> dict:
    """Extract the validated WRI archive and return the discovered GDB path."""

    path = _f_path(path)
    destination = _f_path(destination)
    audit = validate_sdpt_archive(path)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(destination)
    gdb = destination / audit["gdbRoot"]
    if not gdb.is_dir():
        raise ValueError(f"Extracted SDPT geodatabase missing at {gdb}")
    audit["extractedPath"] = str(gdb)
    audit["extractedBytes"] = sum(item.stat().st_size for item in gdb.rglob("*") if item.is_file())
    return audit


def _head_status(url: str, *, timeout: float = 60.0) -> dict:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        return {
            "url": url,
            "statusCode": response.status_code,
            "contentLength": response.headers.get("Content-Length"),
            "contentType": response.headers.get("Content-Type"),
            "finalUrl": response.url,
        }
    except requests.RequestException as exc:
        return {"url": url, "error": f"{type(exc).__name__}: {exc}"}


def stage_manifest(
    *,
    stage_root: Path,
    census: dict,
    ucmr_dir: Path,
    sdpt_archive: Path | None,
    sdpt_audit: dict | None,
    census_endpoint_audit: list[dict],
) -> Path:
    """Write one strict source receipt for the provider handoff."""

    stage_root = _f_path(stage_root)
    ucmr_dir = _f_path(ucmr_dir)
    files = {}
    for name in (UCMR5_ALL, UCMR5_ZIPCODES):
        path = ucmr_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files[name] = {
            "path": str(path),
            "bytes": int(path.stat().st_size),
            "sha256": sha256_file(path),
            "encoding": "Windows-1252",
            "delimiter": "TAB",
        }
    datasets = {
        "census_zcta": {
            "provider": "U.S. Census Bureau",
            "vintage": "2020",
            "sourceUrl": CENSUS_TIGERWEB_QUERY_URL,
            "landingPage": "https://www.census.gov/data/developers/data-sets/TIGERweb-map-service.html",
            "license": "U.S. federal public data; Census Bureau source attribution",
            **census,
        },
        "epa_ucmr5": {
            "provider": "U.S. Environmental Protection Agency",
            "sourcePage": EPA_UCMR5_PAGE_URL,
            "occurrenceZipUrl": EPA_UCMR5_ZIP_URL,
            "release": "UCMR 5 final occurrence data, 2023-2025",
            "license": "U.S. federal public data",
            "files": files,
            "analytePolicy": "29 PFAS analytes; lithium excluded",
            "unitPolicy": "Source µg/L values converted to ng/L using the exact 1000x mass factor",
        },
    }
    if sdpt_archive is not None:
        archive = _f_path(sdpt_archive)
        datasets["wri_sdpt"] = {
            "provider": "World Resources Institute Land & Carbon Lab",
            "sourcePage": WRI_SDPT_PAGE_URL,
            "distributionUrl": WRI_SDPT_URL,
            "version": "2.1",
            "dataVintage": "Source years 2000-2023; nominally representing 2020",
            "license": "Creative Commons Attribution (WRI Data Explorer metadata; retain component-source caveats)",
            "archive": {
                "path": str(archive),
                "bytes": int(archive.stat().st_size),
                "sha256": sha256_file(archive),
                **(sdpt_audit or {}),
            },
        }
    payload = {
        "schemaVersion": 1,
        "generatedAt": utc_now_iso(),
        "stageRoot": str(stage_root),
        "minimumFreeReserveBytes": MIN_FREE_RESERVE_BYTES,
        "endpointChecks": census_endpoint_audit,
        "datasets": datasets,
    }
    manifest_path = stage_root / "source-manifest.json"
    write_json(manifest_path, payload)
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage-root",
        type=Path,
        default=Path("F:/BiomeSummary/.cache/motherworld/v8-build/pfas-sdpt-20260909"),
    )
    parser.add_argument(
        "--ucmr-dir",
        type=Path,
        default=Path("F:/BiomeSummary/MotherWorld-v8-dataset-downloader/contaminants_raw/ucmr5"),
    )
    parser.add_argument("--query-zctas", action="store_true", help="Fetch UCMR5 service ZIP polygons from TIGERweb")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4, help="bounded concurrent TIGERweb requests")
    parser.add_argument("--sdpt-archive", type=Path, default=None)
    parser.add_argument("--extract-sdpt", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage_root = _f_path(args.stage_root)
    ucmr_dir = _f_path(args.ucmr_dir)
    stage_root.mkdir(parents=True, exist_ok=True)
    zips = read_ucmr5_zipcodes(ucmr_dir / UCMR5_ZIPCODES)
    census_endpoint_audit = [
        _head_status(CENSUS_TIGER_LINE_URL),
        _head_status(CENSUS_CARTOGRAPHIC_URL),
        _head_status(CENSUS_TIGERWEB_QUERY_URL),
    ]
    census_path = stage_root / "census2020_zcta_tigerweb.geojson"
    census = {"path": str(census_path), "requestedZipCount": len(zips)}
    if args.query_zctas or not census_path.exists():
        census.update(query_tigerweb_zctas(zips, destination=census_path, batch_size=args.batch_size, workers=args.workers))
    else:
        receipt = stage_root / "source-manifest.json"
        previous_census = {}
        if receipt.is_file():
            try:
                previous_census = (
                    json.loads(receipt.read_text(encoding="utf-8"))
                    .get("datasets", {})
                    .get("census_zcta", {})
                )
            except (OSError, ValueError, TypeError):
                previous_census = {}
        if previous_census.get("sha256") and previous_census.get("bytes"):
            # The query path already validated every page. Reuse its compact
            # receipt here instead of decoding a multi-gigabyte GeoJSON again
            # merely to add the later SDPT archive to the manifest.
            census.update(previous_census)
        else:
            payload = json.loads(census_path.read_text(encoding="utf-8"))
            census.update(validate_geojson(payload, zips))
        census["url"] = CENSUS_TIGERWEB_QUERY_URL
    if census_path.is_file():
        census["bytes"] = int(census_path.stat().st_size)
        census["sha256"] = sha256_file(census_path)
    sdpt_archive = _f_path(args.sdpt_archive) if args.sdpt_archive else None
    sdpt_audit = validate_sdpt_archive(sdpt_archive) if sdpt_archive else None
    if sdpt_archive and args.extract_sdpt:
        sdpt_audit = extract_sdpt_archive(sdpt_archive, stage_root / "sdpt-extracted")
    manifest = stage_manifest(
        stage_root=stage_root,
        census=census,
        ucmr_dir=ucmr_dir,
        sdpt_archive=sdpt_archive,
        sdpt_audit=sdpt_audit,
        census_endpoint_audit=census_endpoint_audit,
    )
    print(json.dumps({"manifest": str(manifest), "census": census, "sdpt": sdpt_audit}, allow_nan=False))


if __name__ == "__main__":
    main()

