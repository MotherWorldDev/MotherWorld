#!/usr/bin/env python
"""Build the USGS ComCat earthquake provider.

The provider is rebuilt transactionally.  Input and region validation, event
type filtering, and fragment serialization all complete in a temporary
directory before the resolved provider directory is replaced.  This keeps a
previous valid build available when a new input or region load is malformed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from geology_common import *


PROVIDER_ID = "usgs_comcat"
EVENT_TYPE_FILTER = "type == 'earthquake' (trimmed, case-insensitive)"
PROVIDER_RELATIVE_DIR = Path(".cache/motherworld/geology/providers") / PROVIDER_ID


def provider_output_dir(repo: Path) -> Path:
    """Resolve the only directory this builder is allowed to replace."""

    resolved_repo = repo.resolve()
    provider_dir = (resolved_repo / PROVIDER_RELATIVE_DIR).resolve()
    try:
        provider_dir.relative_to(resolved_repo)
    except ValueError as exc:
        raise ValueError(f"ComCat provider directory escapes repository: {provider_dir}") from exc
    if provider_dir.parent != resolved_repo / PROVIDER_RELATIVE_DIR.parent:
        raise ValueError(f"Unexpected ComCat provider directory: {provider_dir}")
    return provider_dir


def prepare_provider_output(repo: Path) -> Path:
    """Return the resolved output directory without changing its contents.

    Kept as a small compatibility helper for callers that used the previous
    preparation function.  Rebuild cleanup is deferred until the new output
    has passed validation and is installed atomically.
    """

    return provider_output_dir(repo)


def _read_input(path: Path) -> pd.DataFrame:
    """Read a ComCat table with an explicit UTF-8 CSV/TSV encoding."""

    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, sep=None, engine="python", encoding="utf-8")
    if suffix in {".tsv", ".tab"}:
        return pd.read_csv(path, sep="\t", low_memory=False, encoding="utf-8")
    return read_table(path)


def filter_event_types(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return normalized earthquake rows and complete filter provenance."""

    type_col = find_col(df, ["type"], False)
    if type_col is None:
        raise ValueError(
            "ComCat input requires a type column for earthquake-only filtering; "
            f"columns={list(df.columns)}"
        )

    normalized = df[type_col].astype("string").str.strip().str.casefold()
    blank = normalized.isna() | normalized.eq("")
    if bool(blank.any()):
        raise ValueError(
            "ComCat input type column contains missing or blank event types; "
            "refusing to replace the existing provider output"
        )

    earthquake_mask = normalized.eq("earthquake")
    excluded_counts = Counter(str(value) for value in normalized[~earthquake_mask].tolist())
    metadata = {
        "eventTypeFilter": EVENT_TYPE_FILTER,
        "inputRowCount": int(len(df)),
        "includedRowCount": int(earthquake_mask.sum()),
        "excludedRowCount": int((~earthquake_mask).sum()),
        "excludedTypeCounts": dict(sorted(excluded_counts.items())),
    }
    return df.loc[earthquake_mask].copy(), metadata


def _validate_input_columns(df: pd.DataFrame) -> tuple[str, str, str, str | None, str | None]:
    """Validate required ComCat columns before loading or replacing output."""

    try:
        latitude = find_col(df, ["latitude", "lat"], True)
        longitude = find_col(df, ["longitude", "lon"], True)
        magnitude = find_col(df, ["mag", "magnitude"], True)
    except KeyError as exc:
        raise ValueError(f"ComCat input is missing a required column: {exc}") from exc
    time = find_col(df, ["time", "date"])
    place = find_col(df, ["place", "location"])
    return latitude, longitude, magnitude, time, place


def _validate_regions(regions: gpd.GeoDataFrame) -> None:
    """Reject an unusable region inventory before any output is replaced."""

    if regions is None or len(regions) == 0:
        raise ValueError("ComCat region validation failed: no regions were loaded")
    required = {"regionId", "regionName", "kind", "geometry"}
    missing = sorted(required - set(regions.columns))
    if missing:
        raise ValueError(f"ComCat region validation failed; missing columns: {missing}")
    region_ids = [str(value).strip() for value in regions["regionId"].tolist()]
    if any(not value for value in region_ids):
        raise ValueError("ComCat region validation failed: blank region ID")
    if len(region_ids) != len(set(region_ids)):
        raise ValueError("ComCat region validation failed: duplicate region IDs")
    if any(geometry is None or geometry.is_empty for geometry in regions.geometry.tolist()):
        raise ValueError("ComCat region validation failed: empty region geometry")


def _build_points(
    df: pd.DataFrame,
    latitude: str,
    longitude: str,
    magnitude: str,
    time: str | None,
    place: str | None,
    min_magnitude: float,
) -> gpd.GeoDataFrame:
    rows: list[dict[str, Any]] = []
    for _, record in df.iterrows():
        event_magnitude = as_float(record.get(magnitude))
        event_latitude = as_float(record.get(latitude))
        event_longitude = as_float(record.get(longitude))
        if (
            event_magnitude is None
            or event_magnitude < min_magnitude
            or event_latitude is None
            or event_longitude is None
        ):
            continue
        rows.append(
            {
                "magnitude": event_magnitude,
                "time": str(record.get(time) or "") if time else None,
                "place": record.get(place) if place else None,
                "geometry": Point(event_longitude, event_latitude),
            }
        )
    if not rows:
        return gpd.GeoDataFrame(
            [],
            columns=["magnitude", "time", "place", "geometry"],
            geometry="geometry",
            crs=WGS84,
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=WGS84)


def _fragment_payload(
    provider_id: str,
    region: dict[str, Any],
    sections: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    region_id = str(region["regionId"])
    geometry = region.get("geometry")
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "providerId": provider_id,
        "regionId": region_id,
        "regionName": region.get("regionName", region_id),
        "kind": region.get("kind"),
        "generatedAt": utc_now_iso(),
        "sections": sections,
        "source": source,
    }
    if geometry is not None:
        payload["analysisGeometry"] = analysis_geometry_metadata(region_id, geometry)
        payload["analysisGeometryCacheIdentity"] = analysis_geometry_cache_identity(region_id, geometry)
    return json_safe(payload)


def _write_fragments(
    provider_dir: Path,
    provider_id: str,
    regions: gpd.GeoDataFrame,
    joined: gpd.GeoDataFrame,
    source: dict[str, Any],
    min_magnitude: float,
) -> int:
    """Serialize all new fragments into a temporary provider directory."""

    region_records_by_id = {
        str(record.regionId): {
            "regionId": str(record.regionId),
            "regionName": record.regionName,
            "kind": record.kind,
            "geometry": record.geometry,
        }
        for record in regions.itertuples()
    }
    if joined.empty:
        return 0

    written = 0
    for raw_region_id, group in joined.groupby("regionId"):
        region_id = str(raw_region_id)
        if region_id not in region_records_by_id:
            raise ValueError(f"ComCat region validation failed: point assigned to unknown region {region_id}")
        region = region_records_by_id[region_id]
        events = sorted(
            [
                {
                    "magnitude": float(event.magnitude),
                    "time": event.time,
                    "place": event.place,
                }
                for event in group.itertuples()
            ],
            key=lambda event: event["magnitude"],
            reverse=True,
        )
        seismicity = {
            "minMagnitude": min_magnitude,
            "eventCount": len(group),
            "m6Plus": sum(1 for event in events if event["magnitude"] >= 6),
            "m7Plus": sum(1 for event in events if event["magnitude"] >= 7),
            "maxMagnitude": events[0]["magnitude"] if events else None,
            "events": events[:30],
            "source": source,
        }
        sections = {"tectonics": {"seismicity": seismicity}}
        path = provider_dir / f"{region_id}.json"
        path.write_text(
            json.dumps(
                _fragment_payload(provider_id, region, sections, source),
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        written += 1
    return written


def _install_provider_output(provider_dir: Path, temporary_dir: Path) -> None:
    """Atomically install a complete provider directory and retain rollback."""

    parent = provider_dir.parent
    backup: Path | None = None
    if provider_dir.exists():
        backup = parent / f".{provider_dir.name}.backup-{os.getpid()}-{uuid.uuid4().hex}"
        provider_dir.rename(backup)
    try:
        temporary_dir.rename(provider_dir)
    except Exception:
        if provider_dir.exists():
            shutil.rmtree(provider_dir)
        if backup is not None and backup.exists():
            backup.rename(provider_dir)
        raise
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


def build(repo: Path, input_path: Path, min_magnitude: float = 5.0) -> dict[str, Any]:
    """Build and atomically install ComCat fragments for ``repo``."""

    resolved_repo = repo.resolve()
    resolved_input = input_path.resolve()
    provider_dir = provider_output_dir(resolved_repo)

    # Everything through region validation is read-only with respect to the
    # existing provider directory.  A bad source therefore cannot erase a
    # previously valid build.
    df = _read_input(resolved_input)
    filtered, filter_metadata = filter_event_types(df)
    latitude, longitude, magnitude, time, place = _validate_input_columns(filtered)
    points = _build_points(
        filtered,
        latitude,
        longitude,
        magnitude,
        time,
        place,
        min_magnitude,
    )
    regions = load_regions(resolved_repo, globalize_open_ocean=True)
    _validate_regions(regions)
    joined = assign_points(points, regions)

    source = source_obj(
        PROVIDER_ID,
        "USGS Earthquake Catalog (ComCat)",
        license="U.S. federal public data",
    )
    source.update(filter_metadata)

    provider_parent = provider_dir.parent
    provider_parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=f".{provider_dir.name}.", dir=str(provider_parent)))
    installed = False
    try:
        written = _write_fragments(
            temporary_dir,
            PROVIDER_ID,
            regions,
            joined,
            source,
            min_magnitude,
        )
        _install_provider_output(provider_dir, temporary_dir)
        installed = True
    finally:
        if not installed and temporary_dir.exists():
            shutil.rmtree(temporary_dir)

    return {
        **filter_metadata,
        "matchedEventRegionRecords": int(len(joined)),
        "fragmentCount": written,
        "providerDir": str(provider_dir),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Aggregate a staged USGS ComCat earthquake CSV by MotherWorld region."
    )
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--min-magnitude", type=float, default=5.0)
    args = ap.parse_args()
    result = build(args.repo, args.input, args.min_magnitude)
    print(
        "Seismicity matched "
        f"{result['matchedEventRegionRecords']} event-region records; "
        f"earthquakeRows={result['includedRowCount']} "
        f"excludedRows={result['excludedRowCount']} "
        f"fragments={result['fragmentCount']}"
    )


if __name__ == "__main__":
    main()
