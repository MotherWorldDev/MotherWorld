"""Precise, attributed query footprints for regions whose display geometry is unsuitable."""
from __future__ import annotations
import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse
from shapely.geometry import shape
from species_common import _repair_polygonal

DEFAULT_QUERY_GEOMETRIES = Path(__file__).resolve().parent / "data" / "species-query-geometries.geojson"

def load_query_geometry_overrides(path=None):
    path = Path(path) if path is not None else DEFAULT_QUERY_GEOMETRIES
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("type") != "FeatureCollection":
        raise ValueError("Query geometry overrides must be a GeoJSON FeatureCollection")
    overrides = {}
    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        region_id = props.get("regionId", "")
        if not re.fullmatch(r"eco_\d+", region_id) or region_id in overrides:
            raise ValueError(f"Invalid or duplicate query geometry region: {region_id}")
        if not all(isinstance(props.get(key), str) and props[key].strip() for key in ("source", "sourceUrl", "notes")):
            raise ValueError(f"Query geometry provenance is missing: {region_id}")
        reference = urlparse(props["sourceUrl"])
        if reference.scheme != "https" or not reference.netloc or reference.username or reference.password:
            raise ValueError(f"Invalid query geometry reference: {region_id}")
        raw = feature.get("geometry") or {}
        if raw.get("type") not in {"Polygon", "MultiPolygon"}:
            raise ValueError(f"Query geometry must be polygonal: {region_id}")
        geometry = _repair_polygonal(shape(raw))
        if geometry is None or geometry.is_empty:
            raise ValueError(f"Empty query geometry: {region_id}")
        west, south, east, north = geometry.bounds
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError(f"Query geometry must use longitude/latitude: {region_id}")
        overrides[region_id] = (geometry, {key: props[key] for key in ("source", "sourceUrl", "notes")})
    return overrides

def apply_query_geometry(region, overrides):
    override = overrides.get(region.region_id)
    if override is None:
        return region, None
    geometry, provenance = override
    return replace(region, geometry=geometry), dict(provenance)
