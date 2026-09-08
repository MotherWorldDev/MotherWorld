"""Provider-scoped adapters for the v8 local regional diagnostics build.

The checked-in provider helpers predate the v8 build workspace and generally
write directly into ``frontend/public``.  This module keeps the assigned
providers isolated from that tree while preserving their public schemas.  It
also normalizes split marine topology features to the canonical selection IDs
used by the species index.

This is intentionally a provider adapter rather than a change to the shared
environment/contaminant helpers.  The parent integration task can consume the
provider fragments or copy the deployable snapshot without importing this
module at runtime.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.ops import unary_union

from analysis_geometry import (
    analysis_geometry_cache_identity,
    analysis_geometry_metadata,
    apply_land_geometry_overrides,
)


REGION_PREFIXES = ("eco_", "marine_", "lake_")
KIND_DIR = {"land": "land", "marine": "marine", "lakes": "lakes"}
_PRODUCER_GEOMETRIES: dict[tuple[str, str], dict[str, dict]] = {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text + ("" if compact else "\n"), encoding="utf-8")


def canonical_region_id(value) -> str | None:
    """Return a canonical region ID from an index or rendered fragment ID."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(eco_|marine_|lake_).+__(\d+)$", text)
    if match:
        return text[: text.rfind("__")]
    return text


def _metadata(repo: Path) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for kind, filename in (
        ("land", "regions.index.json"),
        ("marine", "marine.index.json"),
        ("lakes", "lakes.index.json"),
    ):
        data = read_json(repo / "frontend/public/data" / filename, {})
        for rid, meta in (data.get("regions") or {}).items():
            out[canonical_region_id(rid) or str(rid)] = (kind, meta or {})
    return out


def expected_region_ids(repo: Path, *, include_open_ocean: bool = False) -> set[str]:
    """Read the canonical region contract without trusting rendered fragments."""

    species_index = repo / "frontend/public/data/species/species.index.json"
    if species_index.exists():
        ids = {canonical_region_id(x) or str(x) for x in (read_json(species_index, {}).get("regions") or {})}
    else:
        ids = set(_metadata(repo))
    if include_open_ocean:
        ids.add("open_ocean")
    return ids


def _first_column(columns: Iterable[str], names: Iterable[str]) -> str | None:
    lookup = {str(c).lower(): str(c) for c in columns}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def _source_layers(repo: Path, kind: str) -> list[Path]:
    if kind == "land":
        # The original ecoregion shapefile is the authoritative analysis
        # footprint.  Rendered realm layers remain a fallback because their
        # simplification can omit geometry parts.
        shp = repo / "Ecoregions2017/Ecoregions2017.shp"
        if shp.exists():
            return [shp]
        realm_dir = repo / "frontend/public/data/lod1_realms"
        files = sorted(realm_dir.glob("*.topojson"))
        if files:
            return files
        return [repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"]
    if kind == "marine":
        return [repo / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson"]
    if kind == "lakes":
        return [repo / "frontend/public/data/lakes/lod0/lakes_lod0.topojson"]
    raise ValueError(f"Unsupported region kind: {kind}")


def _layer_region_id(row: pd.Series, kind: str, index) -> str | None:
    # regionId is the canonical field in the v8 MEOW topology.  It must be
    # preferred over id/FID, which identify split rendered fragments.
    candidates = ["regionId", "region_id"]
    if kind == "land":
        candidates += ["ECO_ID", "eco_id", "ECOID"]
    candidates += ["id", "FID"]
    field = _first_column(row.index, candidates)
    value = row.get(field) if field else index
    rid = canonical_region_id(value)
    if rid is None:
        return None
    if kind == "land" and not rid.startswith("eco_"):
        try:
            rid = f"eco_{int(float(rid))}"
        except (TypeError, ValueError):
            return None
    if kind == "marine" and not rid.startswith("marine_"):
        return None
    if kind == "lakes" and not rid.startswith("lake_"):
        return None
    return rid


def _analysis_geometry_entry(region_id: str, geometry) -> dict:
    metadata = analysis_geometry_metadata(region_id, geometry)
    return {
        "analysisGeometry": metadata,
        "analysisGeometryCacheIdentity": analysis_geometry_cache_identity(region_id, geometry),
    }


def load_canonical_regions(repo: Path, kinds: Iterable[str]) -> gpd.GeoDataFrame:
    """Load canonical region geometries and apply maintained land corrections.

    The override registry is applied after source fragments have been unioned,
    and only to land IDs that were present in the source layers.  Each row
    carries the provenance and cache identity for the exact geometry returned
    to downstream point and raster providers.
    """

    repo = Path(repo).resolve()
    metadata = _metadata(repo)
    wanted_kinds = tuple(dict.fromkeys(kinds))
    grouped: dict[tuple[str, str], list[object]] = defaultdict(list)
    for kind in wanted_kinds:
        for layer in _source_layers(repo, kind):
            if not layer.exists():
                continue
            frame = gpd.read_file(layer)
            if frame.crs is None:
                frame = frame.set_crs("EPSG:4326")
            else:
                frame = frame.to_crs("EPSG:4326")
            for index, row in frame.iterrows():
                rid = _layer_region_id(row, kind, index)
                geom = row.geometry
                if rid and geom is not None and not geom.is_empty:
                    grouped[(kind, rid)].append(geom)

    unioned: dict[tuple[str, str], object] = {}
    for key, geometries in grouped.items():
        # The authoritative shapefile has one feature per region.  Reusing
        # that object avoids an expensive copy of every detailed polygon;
        # split rendered/topology layers still receive the required union.
        geom = geometries[0] if len(geometries) == 1 else unary_union(geometries)
        if geom is not None and not geom.is_empty:
            unioned[key] = geom

    land_geometries = {
        region_id: geometry
        for (kind, region_id), geometry in unioned.items()
        if kind == "land"
    }
    corrected_land = apply_land_geometry_overrides(land_geometries)

    rows = []
    for (kind, rid), base_geometry in sorted(unioned.items()):
        geometry = corrected_land.get(rid, base_geometry) if kind == "land" else base_geometry
        geometry_entry = _analysis_geometry_entry(rid, geometry)
        meta = metadata.get(rid, (kind, {}))[1]
        rows.append(
            {
                "regionId": rid,
                "kind": kind,
                "name": meta.get("name") or rid,
                "areaKm2": meta.get("areaKm2"),
                "geometry": geometry,
                **geometry_entry,
            }
        )
    # Record the geometries actually loaded by this producer process. Claims
    # must not be manufactured later by loading current geometry at write time.
    for kind in wanted_kinds:
        _PRODUCER_GEOMETRIES[(str(repo), kind)] = {
            row["regionId"]: {
                "analysisGeometry": row["analysisGeometry"],
                "analysisGeometryCacheIdentity": row["analysisGeometryCacheIdentity"],
            }
            for row in rows if row["kind"] == kind
        }
    if not rows:
        return gpd.GeoDataFrame(
            columns=[
                "regionId",
                "kind",
                "name",
                "areaKm2",
                "geometry",
                "analysisGeometry",
                "analysisGeometryCacheIdentity",
            ],
            geometry="geometry",
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def canonical_region_gdf(repo: Path, kind: str) -> gpd.GeoDataFrame:
    return load_canonical_regions(repo, (kind,))


def _canonical_geometry_entries(repo: Path, kinds: Iterable[str]) -> dict[tuple[str, str], dict]:
    regions = load_canonical_regions(repo, kinds)
    entries: dict[tuple[str, str], dict] = {}
    for _, row in regions.iterrows():
        entries[(str(row["regionId"]), str(row["kind"]))] = {
            "geometry": row["geometry"],
            "analysisGeometry": row.get("analysisGeometry"),
            "analysisGeometryCacheIdentity": row.get("analysisGeometryCacheIdentity"),
        }
    return entries


def canonical_region_geometry_metadata(
    repo: Path,
    region_id: str,
    *,
    kind: str = "land",
) -> dict | None:
    """Return provider-ready geometry provenance for an existing region.

    ``None`` means that the region was not present in the canonical source
    layer.  Base geometries receive an identity too; callers that require a
    maintained correction should require ``analysisGeometry.overrideApplied``
    and validate the identity before merging results.
    """

    rid = canonical_region_id(region_id) or str(region_id)
    entry = _canonical_geometry_entries(repo, (kind,)).get((rid, kind))
    if entry is None:
        return None
    return {
        "analysisGeometry": dict(entry["analysisGeometry"]),
        "analysisGeometryCacheIdentity": entry["analysisGeometryCacheIdentity"],
    }


def attach_analysis_geometry_provenance(
    repo: Path,
    updates: Mapping[str, dict],
) -> Mapping[str, dict]:
    """Attach producer-side identity for maintained corrected land regions.

    Claims use the producer's loaded geometry context; absent context leaves
    updates unverified. Never attach claims to cached results before checking
    their saved geometry identity. The private fields precede the merge. A
    producer that supplied either field already is left untouched so a stale
    or incorrect claim is rejected by the merge rather than silently replaced
    with a newly derived value.
    """

    entries = _PRODUCER_GEOMETRIES.get((str(Path(repo).resolve()), "land"), {})
    for region_id, payload in updates.items():
        if not isinstance(payload, dict) or payload.get("_kind", "land") != "land":
            continue
        entry = entries.get(str(region_id))
        if entry is None:
            continue
        metadata = entry["analysisGeometry"]
        if not isinstance(metadata, dict) or not metadata.get("overrideApplied"):
            continue
        if "_analysisGeometry" in payload or "_analysisGeometryCacheIdentity" in payload:
            continue
        payload["_analysisGeometry"] = dict(metadata)
        payload["_analysisGeometryCacheIdentity"] = entry["analysisGeometryCacheIdentity"]
    return updates

def canonical_point_region_map(
    points: pd.DataFrame,
    repo: Path,
    *,
    kinds: set[str],
) -> dict[int, list[str]]:
    """Map points to canonical IDs after unioning split region fragments."""

    if points.empty:
        return {}
    regions = load_canonical_regions(repo, tuple(sorted(kinds)))
    if regions.empty:
        return {}
    point_frame = points.copy()
    point_frame["_row_index"] = list(point_frame.index)
    pts = gpd.GeoDataFrame(
        point_frame,
        geometry=gpd.points_from_xy(point_frame["lon"], point_frame["lat"]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        pts,
        regions[["regionId", "kind", "geometry"]],
        how="left",
        predicate="within",
    )
    out: dict[int, list[str]] = defaultdict(list)
    for _, row in joined.iterrows():
        original_index = row.get("_row_index")
        rid = row.get("regionId")
        if isinstance(rid, str) and rid and original_index is not None:
            if rid not in out[int(original_index)]:
                out[int(original_index)].append(rid)
    return dict(out)


def output_root(repo: Path, requested: Path | None) -> Path:
    return (requested if requested is not None else repo).resolve()


def deploy_path(root: Path, relative_data_path: str | Path) -> Path:
    return root / "frontend/public/data" / Path(relative_data_path)


def provider_fragment_path(root: Path, relative_path: str | Path) -> Path:
    return root / "provider-fragments" / Path(relative_path)


def input_fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "bytes": int(stat.st_size),
        "mtimeNs": int(stat.st_mtime_ns),
    }


def _region_name(repo: Path, region_id: str) -> str:
    return (_metadata(repo).get(region_id, ("", {}))[1].get("name") or region_id)


def _corrected_geometry_output(entry: dict | None) -> dict:
    if not entry:
        return {}
    metadata = entry.get("analysisGeometry")
    identity = entry.get("analysisGeometryCacheIdentity")
    if not isinstance(metadata, dict) or not metadata.get("overrideApplied"):
        return {}
    return {
        "analysisGeometry": dict(metadata),
        "analysisGeometryCacheIdentity": identity,
    }


def _requires_corrected_geometry(region_id: str, entry: dict | None) -> tuple[bool, str | None]:
    """Return whether a registry-listed region needs a corrected-footprint claim."""

    expected = analysis_geometry_metadata(region_id, None)
    if entry:
        metadata = entry.get("analysisGeometry") or {}
        guarded = expected.get("geometryOverride") is not None
        unavailable = guarded and not metadata.get("overrideApplied")
        return guarded, "expected_analysis_geometry_unavailable" if unavailable else None
    # The canonical layer may be missing an ID that is explicitly listed in the
    # maintained registry.  Treat that as an unavailable expected geometry,
    # rather than allowing an old provider result through the unaffected path.
    expected = analysis_geometry_metadata(region_id, None)
    if expected.get("geometryOverride") is not None:
        return True, "expected_analysis_geometry_unavailable"
    return False, None


def _valid_producer_geometry_claim(
    region_id: str,
    provided_metadata,
    provided_identity,
    entry: dict | None,
) -> bool:
    """Validate a corrected-footprint claim without deriving one at merge time."""

    requires_claim, unavailable = _requires_corrected_geometry(region_id, entry)
    if unavailable:
        return False
    if not requires_claim or not entry:
        return not requires_claim
    expected_metadata = entry.get("analysisGeometry")
    return (
        provided_identity == entry.get("analysisGeometryCacheIdentity")
        and isinstance(provided_metadata, dict)
        and provided_metadata == expected_metadata
        and provided_metadata.get("regionId") == str(region_id)
    )


def _withhold_geometry_mismatch(
    path: Path,
    index: dict,
    region_id: str,
    *,
    clear_key: str,
    expected_identity: str | None,
    reason: str | None = None,
) -> None:
    """Keep stale corrected outputs out of the public index until replacement."""

    existing = read_json(path, {}) if path.exists() else {}
    # A previously validated output remains usable if a later producer sends a
    # bad replacement.  An unavailable expected geometry has no usable identity
    # and must always be withheld, even when an old file has no identity either.
    if expected_identity is not None and path.exists() and existing.get("analysisGeometryCacheIdentity") == expected_identity:
        return
    if path.exists():
        existing[clear_key] = {}
        if clear_key == "categories":
            existing["sources"] = {}
        existing["analysisGeometryStatus"] = "withheld"
        existing["analysisGeometryReason"] = reason or (
            "Producer geometry identity/provenance is missing or does not "
            "match the maintained corrected footprint."
        )
        write_json(path, existing, compact=True)
    index.setdefault("regions", {}).pop(str(region_id), None)


def merge_contaminant_categories(
    repo: Path,
    root: Path,
    region_updates: Mapping[str, dict],
    source_entry: dict,
) -> None:
    """Write contaminant provider output and deployable regional files.

    Corrected land regions require producer-side geometry provenance.  The
    merge validates that claim against the geometry used by the canonical
    mapping and withholds stale or unclaimed corrected-region outputs.
    """

    index_path = deploy_path(root, "contaminants/contaminants.index.json")
    index = read_json(index_path, {"schemaVersion": 1, "generatedAt": None, "regions": {}, "sources": {}})
    provider_id = str(source_entry["id"])
    land_updates = [
        region_id
        for region_id, raw_update in region_updates.items()
        if isinstance(raw_update, dict) and raw_update.get("_kind", "land") == "land"
    ]
    geometry_entries = _canonical_geometry_entries(repo, ("land",)) if land_updates else {}
    for region_id, raw_update in sorted(region_updates.items()):
        update = dict(raw_update)
        kind = update.pop("_kind", "land")
        provided_metadata = update.pop("_analysisGeometry", None)
        provided_identity = update.pop("_analysisGeometryCacheIdentity", None)
        entry = geometry_entries.get((str(region_id), str(kind)))
        requires_claim, unavailable_reason = (
            _requires_corrected_geometry(str(region_id), entry) if kind == "land" else (False, None)
        )
        corrected = bool(
            entry
            and isinstance(entry.get("analysisGeometry"), dict)
            and entry["analysisGeometry"].get("overrideApplied")
        )
        rel_kind = KIND_DIR.get(kind, "land")
        path = deploy_path(root, f"contaminants/{rel_kind}/{region_id}.contaminants.json")
        if requires_claim and not _valid_producer_geometry_claim(
            str(region_id), provided_metadata, provided_identity, entry
        ):
            _withhold_geometry_mismatch(
                path,
                index,
                str(region_id),
                clear_key="categories",
                expected_identity=entry.get("analysisGeometryCacheIdentity") if entry else None,
                reason=unavailable_reason,
            )
            continue
        existing = read_json(
            path,
            {
                "schemaVersion": 1,
                "regionId": region_id,
                "regionName": _region_name(repo, region_id),
                "regionKind": kind,
                "generatedAt": utc_now_iso(),
                "categories": {},
                "sources": {},
                "coverageWarning": "Observation coverage is incomplete and uneven. No data does not mean no contamination.",
            },
        )
        geometry_output = _corrected_geometry_output(entry) if corrected else {}
        if geometry_output and existing.get("analysisGeometryCacheIdentity") != geometry_output["analysisGeometryCacheIdentity"]:
            existing["categories"] = {}
            existing["sources"] = {}
        existing["generatedAt"] = utc_now_iso()
        existing.update(geometry_output)
        existing.setdefault("categories", {}).update(update.get("categories", {}))
        existing.setdefault("sources", {}).update(update.get("sources", {}))
        write_json(path, existing, compact=True)
        region_index = {
            "url": f"{rel_kind}/{region_id}.contaminants.json",
            "kind": kind,
            "categoryKeys": sorted(existing.get("categories", {})),
            "generatedAt": existing["generatedAt"],
        }
        if geometry_output:
            region_index.update(geometry_output)
        index.setdefault("regions", {})[region_id] = region_index
        fragment = {
            "schemaVersion": 1,
            "providerId": provider_id,
            "regionId": region_id,
            "regionKind": kind,
            "generatedAt": existing["generatedAt"],
            "payload": update,
            "source": source_entry,
            **geometry_output,
        }
        write_json(
            provider_fragment_path(root, f"contaminants/{provider_id}/{rel_kind}/{region_id}.json"),
            fragment,
            compact=True,
        )
    index.setdefault("sources", {})[provider_id] = source_entry
    index["generatedAt"] = utc_now_iso()
    write_json(index_path, index)

def merge_land_pollution_provider(
    repo: Path,
    root: Path,
    region_updates: Mapping[str, dict],
    source_entry: dict,
) -> None:
    """Write a land-pollution provider snapshot with geometry provenance."""

    index_path = deploy_path(root, "land-pollution/land-pollution.index.json")
    index = read_json(index_path, {"schemaVersion": 1, "generatedAt": None, "regions": {}, "sources": {}})
    provider_id = str(source_entry["id"])
    geometry_entries = _canonical_geometry_entries(repo, ("land",)) if region_updates else {}
    for region_id, raw_provider_payload in sorted(region_updates.items()):
        provider_payload = dict(raw_provider_payload)
        provided_metadata = provider_payload.pop("_analysisGeometry", None)
        provided_identity = provider_payload.pop("_analysisGeometryCacheIdentity", None)
        entry = geometry_entries.get((str(region_id), "land"))
        requires_claim, unavailable_reason = _requires_corrected_geometry(str(region_id), entry)
        corrected = bool(
            entry
            and isinstance(entry.get("analysisGeometry"), dict)
            and entry["analysisGeometry"].get("overrideApplied")
        )
        path = deploy_path(root, f"land-pollution/land/{region_id}.land-pollution.json")
        if requires_claim and not _valid_producer_geometry_claim(
            str(region_id), provided_metadata, provided_identity, entry
        ):
            _withhold_geometry_mismatch(
                path,
                index,
                str(region_id),
                clear_key="providers",
                expected_identity=entry.get("analysisGeometryCacheIdentity") if entry else None,
                reason=unavailable_reason,
            )
            continue
        existing = read_json(
            path,
            {
                "schemaVersion": 1,
                "regionId": region_id,
                "regionName": _region_name(repo, region_id),
                "regionKind": "land",
                "generatedAt": utc_now_iso(),
                "providers": {},
                "interpretation": "Known/mapped land-pollution pressure. Data coverage differs by provider; absence of mapped sites does not imply clean land.",
            },
        )
        geometry_output = _corrected_geometry_output(entry) if corrected else {}
        if geometry_output and existing.get("analysisGeometryCacheIdentity") != geometry_output["analysisGeometryCacheIdentity"]:
            existing["providers"] = {}
        existing["generatedAt"] = utc_now_iso()
        existing.update(geometry_output)
        existing.setdefault("providers", {})[provider_id] = provider_payload
        write_json(path, existing, compact=True)
        region_index = {
            "url": f"land/{region_id}.land-pollution.json",
            "providerIds": sorted(existing.get("providers", {})),
            "generatedAt": existing["generatedAt"],
        }
        if geometry_output:
            region_index.update(geometry_output)
        index.setdefault("regions", {})[region_id] = region_index
        write_json(
            provider_fragment_path(root, f"land-pollution/{provider_id}/land/{region_id}.json"),
            {
                "schemaVersion": 1,
                "providerId": provider_id,
                "regionId": region_id,
                "regionKind": "land",
                "generatedAt": existing["generatedAt"],
                "payload": provider_payload,
                "source": source_entry,
                **geometry_output,
            },
            compact=True,
        )
    index.setdefault("sources", {})[provider_id] = source_entry
    index["generatedAt"] = utc_now_iso()
    write_json(index_path, index)

def recompute_land_peer_percentiles(root: Path) -> None:
    """Recompute within-provider metric ranks without creating missing regions."""

    from collections import defaultdict as _defaultdict

    directory = deploy_path(root, "land-pollution/land")
    payloads = {
        path.name.split(".land-pollution.json")[0]: read_json(path, {})
        for path in directory.glob("*.land-pollution.json")
    }
    maps: dict[tuple[str, str], dict[str, float]] = _defaultdict(dict)
    for region_id, payload in payloads.items():
        for provider_id, provider in (payload.get("providers") or {}).items():
            for key, value in (provider.get("metrics") or {}).items():
                if "year" in key.lower() or "reportcount" in key.lower():
                    continue
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    maps[(provider_id, key)][region_id] = float(value)
    ranks = {}
    for key, values in maps.items():
        ordered = sorted(values.values())
        ranks[key] = {
            region_id: 100.0 * sum(candidate <= value for candidate in ordered) / len(ordered)
            for region_id, value in values.items()
        }
    for region_id, payload in payloads.items():
        for provider_id, provider in (payload.get("providers") or {}).items():
            provider["peerPercentiles"] = {
                key: ranks.get((provider_id, key), {}).get(region_id)
                for key in (provider.get("metrics") or {})
                if region_id in ranks.get((provider_id, key), {})
            }
        write_json(directory / f"{region_id}.land-pollution.json", payload, compact=True)


def validate_output_ids(root: Path, repo: Path, relative_glob: str, *, include_open_ocean: bool = False) -> dict:
    """Return an evidence object for handoff/status generation."""

    paths = sorted(root.glob(relative_glob))
    observed = {canonical_region_id(path.stem.split(".")[0]) or path.stem for path in paths}
    expected = expected_region_ids(repo, include_open_ocean=include_open_ocean)
    return {
        "observedCount": len(observed),
        "expectedCount": len(expected),
        "unexpectedIds": sorted(observed - expected),
        "missingIds": sorted(expected - observed),
    }
