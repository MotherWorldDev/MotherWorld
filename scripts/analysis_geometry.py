"""Shared analysis-footprint corrections and provenance.

The browser keeps using the simplified LOD geometries.  Analysis builders may
replace an existing land-region geometry with one of the maintained, attributed
footprints used by the species pipeline.  The registry is deliberately loaded
through :mod:`species_query_geometry` so its validation and provenance rules
remain in one place.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from species_query_geometry import DEFAULT_QUERY_GEOMETRIES, load_query_geometry_overrides


ANALYSIS_GEOMETRY_SCHEMA_VERSION = 1
ANALYSIS_GEOMETRY_REGISTRY = "scripts/data/species-query-geometries.geojson"


@lru_cache(maxsize=1)
def _validated_overrides():
    """Return the validated maintained registry.

    Builders process many regions, so validation is cached for the lifetime of
    one process.  A new builder process reads the registry again, which keeps a
    changed registry from being silently reused across runs.
    """

    return load_query_geometry_overrides(DEFAULT_QUERY_GEOMETRIES)


def _geometry_fingerprint(geometry) -> str | None:
    if geometry is None:
        return None
    try:
        if geometry.is_empty:
            return hashlib.sha256(b"EMPTY").hexdigest()
        # Shapely's WKB is stable for a geometry object and is available across
        # the supported Shapely 1.x/2.x environments.  Normalization makes the
        # fingerprint independent of multipart/ring ordering where supported.
        normalized = geometry.normalize() if hasattr(geometry, "normalize") else geometry
        return hashlib.sha256(bytes(normalized.wkb)).hexdigest()
    except Exception as exc:  # pragma: no cover - defensive error context
        raise TypeError("analysis geometry must be a Shapely geometry") from exc


def _geometry_bounds(geometry) -> list[float] | None:
    if geometry is None or geometry.is_empty:
        return None
    return [float(value) for value in geometry.bounds]


def _registry_path_for_output() -> str:
    return ANALYSIS_GEOMETRY_REGISTRY


def apply_land_geometry_overrides(geometries: Mapping[str, object]) -> dict[str, object]:
    """Apply maintained land overrides to IDs already present in ``geometries``.

    Callers are responsible for reading, repairing, and unioning their base
    geometries first.  The returned plain dict preserves the caller's keys and
    order; registry entries missing from the caller are never introduced, and
    non-registry IDs retain their original geometry object.
    """

    overrides = _validated_overrides()
    corrected: dict[str, object] = {}
    for region_id, geometry in geometries.items():
        override = overrides.get(str(region_id))
        corrected[region_id] = override[0] if override is not None else geometry
    return corrected


def analysis_geometry_metadata(region_id: str, geometry) -> dict:
    """Return JSON-serializable provenance and identity for an analysis geometry."""

    region_id = str(region_id)
    overrides = _validated_overrides()
    override = overrides.get(region_id)
    registry_geometry = override[0] if override is not None else None
    registry_fingerprint = _geometry_fingerprint(registry_geometry)
    override_applied = override is not None and _geometry_fingerprint(geometry) == registry_fingerprint
    metadata = {
        "schemaVersion": ANALYSIS_GEOMETRY_SCHEMA_VERSION,
        "regionId": region_id,
        "geometryFingerprint": _geometry_fingerprint(geometry),
        "geometryFingerprintAlgorithm": "sha256-wkb",
        "geometryType": getattr(geometry, "geom_type", None),
        "geometryBounds": _geometry_bounds(geometry),
        "geometrySource": "maintained_query_override" if override_applied else "base_analysis_geometry",
        "overrideApplied": override_applied,
        "registryPath": _registry_path_for_output(),
        "geometryOverride": None,
    }
    if override is not None:
        registry_geometry, provenance = override
        metadata["geometryOverride"] = {
            "regionId": region_id,
            "registryGeometryFingerprint": registry_fingerprint,
            **dict(provenance),
        }
    return metadata


def analysis_geometry_cache_identity(region_id: str, geometry) -> str:
    """Return a compact cache identity that changes with the analysis footprint."""

    metadata = analysis_geometry_metadata(region_id, geometry)
    identity = {
        "schemaVersion": ANALYSIS_GEOMETRY_SCHEMA_VERSION,
        "regionId": str(region_id),
        "geometryFingerprint": metadata["geometryFingerprint"],
        "geometrySource": metadata["geometrySource"],
        "registryPath": metadata["registryPath"],
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"analysis-geometry-v{ANALYSIS_GEOMETRY_SCHEMA_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ANALYSIS_GEOMETRY_REGISTRY",
    "ANALYSIS_GEOMETRY_SCHEMA_VERSION",
    "analysis_geometry_cache_identity",
    "analysis_geometry_metadata",
    "apply_land_geometry_overrides",
]
