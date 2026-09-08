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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(text + ("" if compact else "\n"), encoding="utf-8")


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def linear_trend_per_decade(years: Iterable[int], values: Iterable[float]) -> float | None:
    pairs = []
    for year, value in zip(years, values):
        try:
            y, v = float(year), float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(y) and math.isfinite(v):
            pairs.append((y, v))
    if len(pairs) < 2:
        return None
    x = np.asarray([p[0] for p in pairs], dtype=np.float64)
    y = np.asarray([p[1] for p in pairs], dtype=np.float64)
    x -= x.mean()
    denom = float(np.dot(x, x))
    if denom <= 0:
        return None
    return float(np.dot(x, y - y.mean()) / denom * 10.0)


def percentile_rank(values_by_region: dict[str, float], value: float, *, higher_is_worse: bool = True) -> float | None:
    vals = np.asarray([float(v) for v in values_by_region.values() if v is not None and math.isfinite(float(v))])
    if vals.size < 2 or value is None or not math.isfinite(float(value)):
        return None
    v = float(value)
    if higher_is_worse:
        return float(np.mean(vals <= v) * 100.0)
    return float(np.mean(vals >= v) * 100.0)


def iter_polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    out: list[Polygon] = []
    for part in getattr(geom, "geoms", []):
        out.extend(iter_polygon_parts(part))
    return out


def antimeridian_safe_parts(geom) -> list[Polygon]:
    out: list[Polygon] = []
    for part in iter_polygon_parts(geom):
        minx, _, maxx, _ = part.bounds
        if maxx - minx <= 180:
            out.append(part)
            continue
        shifted = transform(
            lambda x, y, z=None: (np.where(np.asarray(x) < 0, np.asarray(x) + 360.0, np.asarray(x)), y),
            part,
        )
        west = shifted.intersection(box(0, -90, 180, 90))
        east = shifted.intersection(box(180, -90, 360, 90))
        out.extend(iter_polygon_parts(west))
        if not east.is_empty:
            east_back = transform(
                lambda x, y, z=None: (np.where(np.asarray(x) >= 180, np.asarray(x) - 360.0, np.asarray(x)), y),
                east,
            )
            out.extend(iter_polygon_parts(east_back))
    return [p for p in out if p is not None and not p.is_empty]


def antimeridian_safe_geometry(geom):
    parts = antimeridian_safe_parts(geom)
    return unary_union(parts) if parts else None


def _region_id(row, idx, kind: str) -> str | None:
    for key in ("id", "region_id", "regionId"):
        if key in row and row[key] is not None:
            value = str(row[key]).strip()
            if value:
                return value
    idx_text = str(idx)
    prefixes = {"marine": "marine_", "lakes": "lake_"}
    if idx_text.startswith(prefixes[kind]):
        return idx_text
    return None


def _load_topo(path: Path, kind: str) -> dict[str, object]:
    if not path.exists():
        return {}
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    out = {}
    for idx, row in gdf.iterrows():
        rid = _region_id(row, idx, kind)
        geom = row.geometry
        if rid and geom is not None and not geom.is_empty:
            out[rid] = geom
    return out


def load_water_region_geometries(repo: Path) -> dict[str, tuple[str, object]]:
    result: dict[str, tuple[str, object]] = {}
    marine = _load_topo(repo / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson", "marine")
    lakes = _load_topo(repo / "frontend/public/data/lakes/lod0/lakes_lod0.topojson", "lakes")
    result.update({rid: ("marine", geom) for rid, geom in marine.items()})
    result.update({rid: ("lakes", geom) for rid, geom in lakes.items()})
    return result


def load_water_region_metadata(repo: Path) -> dict[str, dict]:
    out = {}
    for filename in ("marine.index.json", "lakes.index.json"):
        data = read_json(repo / "frontend/public/data" / filename, {})
        out.update(data.get("regions", {}))
    out.setdefault("open_ocean", {"name": "Open Ocean"})
    return out


def weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float | None:
    v = np.asarray(values, dtype=np.float64).reshape(-1)
    w = np.asarray(weights, dtype=np.float64).reshape(-1)
    mask = np.isfinite(v) & np.isfinite(w) & (w > 0)
    v, w = v[mask], w[mask]
    if not v.size:
        return None
    order = np.argsort(v)
    v, w = v[order], w[order]
    cdf = np.cumsum(w)
    cutoff = float(quantile) * float(cdf[-1])
    idx = int(np.searchsorted(cdf, cutoff, side="left"))
    idx = min(max(idx, 0), len(v) - 1)
    return float(v[idx])


def write_region_payload(repo: Path, kind: str, region_id: str, payload: dict) -> str:
    rel_kind = "marine" if kind == "marine" else "lakes"
    rel = f"{rel_kind}/{region_id}.water-pollution.json"
    write_json(repo / "frontend/public/data/water-pollution" / rel, payload, compact=True)
    return rel


def update_index(repo: Path, entries: dict[str, dict], sources: dict[str, dict] | None = None) -> None:
    path = repo / "frontend/public/data/water-pollution/water-pollution.index.json"
    data = read_json(path, {"schemaVersion": 1, "generatedAt": None, "regions": {}, "sources": {}})
    data["schemaVersion"] = 1
    data["generatedAt"] = utc_now_iso()
    data.setdefault("regions", {}).update(entries)
    if sources:
        data.setdefault("sources", {}).update(sources)
    write_json(path, data)


def metric_payload(
    *,
    key: str,
    label: str,
    unit: str,
    description: str,
    stress_direction: str,
    source: str,
    annual: list[dict],
    high_field: str = "spatialP90",
    caveat: str | None = None,
) -> dict | None:
    annual = [row for row in annual if row.get("mean") is not None and math.isfinite(float(row["mean"]))]
    if not annual:
        return None
    annual.sort(key=lambda r: int(r["year"]))
    years = [int(r["year"]) for r in annual]
    vals = [float(r["mean"]) for r in annual]
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "description": description,
        "stressDirection": stress_direction,
        "source": source,
        "firstYear": years[0],
        "lastYear": years[-1],
        "latest": annual[-1],
        "annual": annual,
        "trendPerDecade": linear_trend_per_decade(years, vals),
        "change": float(vals[-1] - vals[0]) if len(vals) >= 2 else None,
        "highConditionField": high_field,
        "caveat": caveat,
    }
