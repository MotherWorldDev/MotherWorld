from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import transform, unary_union

from analysis_geometry import apply_land_geometry_overrides


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(text + ("" if compact else "\n"), encoding="utf-8")


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i+size]


def iter_polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    out = []
    for g in getattr(geom, "geoms", []):
        out.extend(iter_polygon_parts(g))
    return out


def antimeridian_safe_geometry(geom):
    parts = []
    for part in iter_polygon_parts(geom):
        minx, _, maxx, _ = part.bounds
        if maxx - minx <= 180:
            parts.append(part)
            continue
        shifted = transform(lambda x,y,z=None: (np.where(np.asarray(x)<0, np.asarray(x)+360, np.asarray(x)), y), part)
        west = shifted.intersection(box(0,-90,180,90))
        east = shifted.intersection(box(180,-90,360,90))
        parts.extend(iter_polygon_parts(west))
        if not east.is_empty:
            east = transform(lambda x,y,z=None: (np.where(np.asarray(x)>=180, np.asarray(x)-360, np.asarray(x)), y), east)
            parts.extend(iter_polygon_parts(east))
    return unary_union(parts) if parts else None


def _read_layer(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _row_id(row, idx, kind: str) -> str | None:
    for field in ("id", "region_id", "regionId"):
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value)
    sidx = str(idx)
    prefix = {"land":"eco_", "marine":"marine_", "lakes":"lake_"}[kind]
    if sidx.startswith(prefix):
        return sidx
    if kind == "land":
        for field in ("ECO_ID", "eco_id", "ECOID"):
            value = row.get(field)
            if value is not None:
                try:
                    return f"eco_{int(float(value))}"
                except Exception:
                    pass
    return None


def load_region_geometries(repo: Path) -> dict[str, tuple[str, object]]:
    out: dict[str, tuple[str, object]] = {}

    shp = repo / "Ecoregions2017/Ecoregions2017.shp"
    if shp.exists():
        gdf = _read_layer(shp)
        for idx, row in gdf.iterrows():
            rid = _row_id(row, idx, "land")
            if rid and row.geometry is not None and not row.geometry.is_empty:
                out[rid] = ("land", row.geometry)
    else:
        realm_files = sorted((repo / "frontend/public/data/lod1_realms").glob("*.topojson"))
        if not realm_files:
            realm_files = [repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"]
        for path in realm_files:
            if not path.exists():
                continue
            for idx, row in _read_layer(path).iterrows():
                rid = _row_id(row, idx, "land")
                if rid and row.geometry is not None and not row.geometry.is_empty:
                    out[rid] = ("land", row.geometry)

    for kind, rel in (
        ("marine", "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson"),
        ("lakes", "frontend/public/data/lakes/lod0/lakes_lod0.topojson"),
    ):
        path = repo / rel
        if not path.exists():
            continue
        for idx, row in _read_layer(path).iterrows():
            rid = _row_id(row, idx, kind)
            if rid and row.geometry is not None and not row.geometry.is_empty:
                out[rid] = (kind, row.geometry)
    land_geometries = {rid: geom for rid, (kind, geom) in out.items() if kind == "land"}
    corrected_land = apply_land_geometry_overrides(land_geometries)
    for rid, geom in corrected_land.items():
        out[rid] = ("land", geom)
    return out


def load_region_metadata(repo: Path) -> dict[str, dict]:
    out = {}
    for filename in ("regions.index.json", "marine.index.json", "lakes.index.json"):
        data = read_json(repo / "frontend/public/data" / filename, {})
        out.update(data.get("regions", {}))
    return out


def linear_trend_per_decade(years: list[int], values: list[float]) -> float | None:
    pairs = [(float(y), float(v)) for y,v in zip(years, values) if math.isfinite(float(v))]
    if len(pairs) < 2:
        return None
    x = np.array([p[0] for p in pairs], dtype=np.float64)
    y = np.array([p[1] for p in pairs], dtype=np.float64)
    x = x - x.mean()
    den = float(np.dot(x,x))
    if den <= 0:
        return None
    return float(np.dot(x, y-y.mean()) / den * 10.0)


def percentile_rank(values: dict[str,float], value: float) -> float | None:
    arr = np.asarray([v for v in values.values() if v is not None and math.isfinite(float(v))], dtype=np.float64)
    if arr.size == 0 or not math.isfinite(float(value)):
        return None
    return float(np.mean(arr <= float(value)) * 100.0)


def update_index(repo: Path, entries: dict[str,dict]) -> None:
    path = repo / "frontend/public/data/pollution/pollution.index.json"
    data = read_json(path, {"schemaVersion":1,"generatedAt":None,"regions":{},"sources":{}})
    data["generatedAt"] = utc_now_iso()
    data.setdefault("regions", {}).update(entries)
    data.setdefault("sources", {}).update({
        "cams-nrt": {"label":"Copernicus Atmosphere Monitoring Service Global NRT","collection":"ECMWF/CAMS/NRT"},
        "sentinel5p": {"label":"Copernicus Sentinel-5P TROPOMI OFFL L3","collectionPrefix":"COPERNICUS/S5P/OFFL/L3_"}
    })
    write_json(path, data)


def write_region_payload(repo: Path, kind: str, rid: str, payload: dict) -> str:
    rel = f"{kind}/{rid}.pollution.json"
    write_json(repo / "frontend/public/data/pollution" / rel, payload, compact=True)
    return rel
