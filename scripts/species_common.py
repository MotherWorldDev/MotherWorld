from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import geopandas as gpd
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely import to_wkt
from urllib3.util.retry import Retry

WGS84 = "EPSG:4326"


@dataclass(frozen=True)
class RegionGeometry:
    region_id: str
    name: str
    geometry: BaseGeometry
    kind: str


def project_root_from(script_file: str | Path) -> Path:
    # pack/scripts/foo.py -> after copying into repo/scripts/foo.py this resolves repo root
    return Path(script_file).resolve().parents[1]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: object, *, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")), encoding="utf-8")
    tmp.replace(path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_http_session() -> requests.Session:
    retry = Retry(
        total=7,
        connect=5,
        read=5,
        status=7,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "MotherWorld biodiversity enrichment/1.0 (+https://github.com/MotherWorldDev/MotherWorld)"})
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
    session.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8))
    return session


def get_json(session: requests.Session, url: str, *, params=None, timeout: float = 90.0) -> dict:
    response = session.get(url, params=params, timeout=timeout)
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                time.sleep(max(1.0, float(retry_after)))
            except ValueError:
                pass
    response.raise_for_status()
    return response.json()


def _repair_polygonal(geom: BaseGeometry | None) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    out = geom
    if not out.is_valid:
        try:
            from shapely import make_valid

            out = make_valid(out)
        except Exception:
            out = out.buffer(0)
    if isinstance(out, (Polygon, MultiPolygon)):
        return out
    if isinstance(out, GeometryCollection):
        polys: list[Polygon] = []
        for part in out.geoms:
            fixed = _repair_polygonal(part)
            if isinstance(fixed, Polygon):
                polys.append(fixed)
            elif isinstance(fixed, MultiPolygon):
                polys.extend(list(fixed.geoms))
        if not polys:
            return None
        return polys[0] if len(polys) == 1 else MultiPolygon(polys)
    return None


def _infer_id_column(gdf: gpd.GeoDataFrame, kind: str) -> str | None:
    columns = {str(c).lower(): str(c) for c in gdf.columns}
    for candidate in ("regionid", "region_id", "id"):
        if candidate in columns:
            return columns[candidate]
    if kind == "land":
        for candidate in ("eco_id", "ecoid", "eco_id_u", "ecoid_u"):
            if candidate in columns:
                return columns[candidate]
    return None


def _infer_name_column(gdf: gpd.GeoDataFrame, kind: str) -> str | None:
    columns = {str(c).lower(): str(c) for c in gdf.columns}
    for candidate in ("name", "eco_name", "ecoregion", "ecoregion_name"):
        if candidate in columns:
            return columns[candidate]
    return None


def _read_runtime_layer(path: Path, kind: str) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    id_col = _infer_id_column(gdf, kind)
    if id_col is None:
        # Fiona/GDAL can expose TopoJSON feature IDs as the dataframe index.
        idx = gdf.index.astype(str)
        if kind == "land" and idx.str.match(r"eco_\d+").all():
            gdf["__region_id"] = idx
            id_col = "__region_id"
        elif kind == "marine" and idx.str.startswith("marine_").all():
            gdf["__region_id"] = idx
            id_col = "__region_id"
        elif kind == "lakes" and idx.str.startswith("lake_").all():
            gdf["__region_id"] = idx
            id_col = "__region_id"
        else:
            raise RuntimeError(f"Could not infer region ID column from {path}; columns={list(gdf.columns)}")
    if kind == "land":
        def normalize_land_id(v: object) -> str:
            s = str(v)
            if s.startswith("eco_"):
                return s
            try:
                return f"eco_{int(float(s))}"
            except Exception:
                return s
        gdf["__region_id"] = gdf[id_col].map(normalize_land_id)
    else:
        gdf["__region_id"] = gdf[id_col].astype(str)
    return gdf


def load_region_geometries(root: Path, kind: str, region_ids=None, *, runtime_geometry=False) -> list[RegionGeometry]:
    kind = kind.lower()
    if kind not in {"land", "marine", "lakes"}:
        raise ValueError(f"Unsupported kind: {kind}")

    name_index: dict[str, str] = {}
    if kind == "land":
        idx_path = root / "frontend/public/data/regions.index.json"
    elif kind == "marine":
        idx_path = root / "frontend/public/data/marine.index.json"
    else:
        idx_path = root / "frontend/public/data/lakes.index.json"
    if idx_path.exists():
        idx = load_json(idx_path)
        name_index = {rid: str(meta.get("name") or rid) for rid, meta in idx.get("regions", {}).items()}

    if kind == "land" and not runtime_geometry and (root / "Ecoregions2017/Ecoregions2017.shp").exists():
        source = root / "Ecoregions2017/Ecoregions2017.shp"
        gdf = gpd.read_file(source)
        if gdf.crs is None:
            gdf = gdf.set_crs(WGS84)
        else:
            gdf = gdf.to_crs(WGS84)
        id_col = _infer_id_column(gdf, kind)
        if id_col is None:
            raise RuntimeError(f"Could not infer ECO_ID in {source}")
        gdf["__region_id"] = gdf[id_col].map(lambda v: f"eco_{int(float(v))}")
    elif kind == "land":
        gdf = _read_runtime_layer(root / "frontend/public/data/lod0/ecoregions_lod0.topojson", kind)
    elif kind == "marine":
        gdf = _read_runtime_layer(root / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson", kind)
    else:
        gdf = _read_runtime_layer(root / "frontend/public/data/lakes/lod0/lakes_lod0.topojson", kind)

    if region_ids:
        gdf = gdf[gdf["__region_id"].isin(set(region_ids))]
    name_col = _infer_name_column(gdf, kind)
    output: list[RegionGeometry] = []
    seen: set[str] = set()
    for _, row in gdf.iterrows():
        region_id = str(row["__region_id"])
        geom = _repair_polygonal(row.geometry)
        if geom is None or geom.is_empty:
            continue
        name = name_index.get(region_id)
        if not name and name_col:
            name = str(row.get(name_col) or region_id)
        if not name:
            name = region_id
        if region_id in seen:
            # Some source layers can contain multiple features for one logical region.
            previous = next(r for r in output if r.region_id == region_id)
            merged = _repair_polygonal(previous.geometry.union(geom))
            output = [r for r in output if r.region_id != region_id]
            if merged is not None:
                output.append(RegionGeometry(region_id, name, merged, kind))
        else:
            output.append(RegionGeometry(region_id, name, geom, kind))
            seen.add(region_id)
    output.sort(key=lambda r: r.region_id)
    return output


def polygon_parts(geom: BaseGeometry) -> list[Polygon]:
    fixed = _repair_polygonal(geom)
    if isinstance(fixed, Polygon):
        return [fixed]
    if isinstance(fixed, MultiPolygon):
        return [p for p in fixed.geoms if not p.is_empty]
    return []


def _simplify_polygon_for_chars(poly: Polygon, max_chars: int) -> Polygon:
    if len(poly.wkt) <= max_chars:
        return poly
    minx, miny, maxx, maxy = poly.bounds
    span = max(maxx - minx, maxy - miny, 0.01)
    tolerance = span / 4000.0
    best = poly
    for _ in range(18):
        candidate = poly.simplify(tolerance, preserve_topology=True)
        if isinstance(candidate, Polygon) and not candidate.is_empty:
            best = candidate
            if len(candidate.wkt) <= max_chars:
                return candidate
        tolerance *= 1.65
    return best


def geometry_wkt_chunks(geom: BaseGeometry, *, max_chars: int = 6500) -> list[str]:
    """Simplify modestly, then split oversized polygons into disjoint query areas.

    API URL limits must never cause islands or holes to be discarded. Splitting
    also handles polygons whose many holes cannot be simplified away safely.
    Six decimal places retain approximately 0.1 m coordinate precision.
    """
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    fixed = _repair_polygonal(geom)
    if fixed is None: return []
    span = max(fixed.bounds[2] - fixed.bounds[0], fixed.bounds[3] - fixed.bounds[1], 0.01)
    simplified = fixed.simplify(min(0.01, span / 4000), preserve_topology=True)
    parts = polygon_parts(unary_union(polygon_parts(simplified)))
    fitted = []

    def fit(poly, depth=0):
        poly = orient(poly, sign=1.0)
        if len(to_wkt(poly, rounding_precision=6)) <= max_chars - 100:
            fitted.append(poly)
            return
        if depth >= 30: raise ValueError("Cannot partition query geometry within API limits")
        x1, y1, x2, y2 = poly.bounds
        if x2 - x1 >= y2 - y1:
            mid = (x1 + x2) / 2
            boxes = [box(x1, y1, mid, y2), box(mid, y1, x2, y2)]
        else:
            mid = (y1 + y2) / 2
            boxes = [box(x1, y1, x2, mid), box(x1, mid, x2, y2)]
        for bounds in boxes:
            for part in polygon_parts(poly.intersection(bounds)): fit(part, depth + 1)

    for poly in parts: fit(poly)
    chunks, bucket = [], []
    for poly in fitted:
        trial = bucket + [poly]
        trial_geom = unary_union(trial)
        if bucket and len(to_wkt(trial_geom, rounding_precision=6)) > max_chars:
            current = unary_union(bucket)
            chunks.append(to_wkt(current, rounding_precision=6))
            bucket = [poly]
        else: bucket = trial
    if bucket:
        current = unary_union(bucket)
        chunks.append(to_wkt(current, rounding_precision=6))
    return chunks


def region_geometry_fingerprint(region: RegionGeometry, *, max_chars: int = 6500) -> str:
    chunks = geometry_wkt_chunks(region.geometry, max_chars=max_chars)
    return sha256_text("\n".join(chunks))


def batched(values: list[int], size: int = 800) -> Iterator[list[int]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def query_fingerprint(geometry_fingerprint: str, options: dict) -> str:
    return sha256_text(json.dumps({"geometry": geometry_fingerprint, "options": options}, sort_keys=True))
