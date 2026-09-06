from __future__ import annotations

import json
import hashlib
import contextlib
import os
import re
import math
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import xarray as xr
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import transform, unary_union
from shapely import make_valid

EARTH_RADIUS_M = 6_371_008.8


@dataclass
class WeightWindow:
    row0: int
    row1: int
    col0: int
    col1: int
    weights: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return self.weights.shape


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_year_range(value: str) -> tuple[int, int]:
    if ":" in value:
        a, b = value.split(":", 1)
    elif "-" in value:
        a, b = value.split("-", 1)
    else:
        a = b = value
    start, end = int(a), int(b)
    if end < start:
        raise ValueError("end year must be >= start year")
    return start, end


def make_bin_edges(min_c: float = -70.0, max_c: float = 60.0, width_c: float = 0.5) -> np.ndarray:
    if width_c <= 0:
        raise ValueError("bin width must be > 0")
    if not all(math.isfinite(v) for v in (min_c, max_c, width_c)) or max_c <= min_c:
        raise ValueError("bin limits must be finite and increasing")
    count = int(round((max_c - min_c) / width_c))
    if count < 1 or not math.isclose(count * width_c, max_c - min_c, abs_tol=1e-8):
        raise ValueError("bin width must divide the temperature range")
    return np.linspace(min_c, max_c, count + 1, dtype=np.float64)


def to_percent(hist: np.ndarray) -> list[float]:
    hist = np.asarray(hist, dtype=np.float64)
    total = float(hist.sum())
    if not math.isfinite(total) or total <= 0:
        return [0.0 for _ in hist]
    return (hist / total * 100.0).tolist()


def scalar_stats(values: Iterable[float]) -> dict:
    arr = np.asarray(list(values), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {}
    return {
        "meanC": float(np.mean(arr)),
        "medianC": float(np.median(arr)),
        "p05C": float(np.percentile(arr, 5)),
        "p25C": float(np.percentile(arr, 25)),
        "p75C": float(np.percentile(arr, 75)),
        "p95C": float(np.percentile(arr, 95)),
        "minC": float(np.min(arr)),
        "maxC": float(np.max(arr)),
        "stdC": float(np.std(arr)),
    }


def weighted_stats_from_hist(hist: np.ndarray, edges: np.ndarray, weighted_sum: float, weighted_sq_sum: float) -> dict:
    hist = np.asarray(hist, dtype=np.float64)
    total = float(hist.sum())
    if total <= 0:
        return {}
    centers = (edges[:-1] + edges[1:]) / 2.0
    cdf = np.cumsum(hist) / total

    def q(p: float) -> float:
        idx = int(np.searchsorted(cdf, p, side="left"))
        idx = max(0, min(idx, len(centers) - 1))
        return float(centers[idx])

    mean = weighted_sum / total
    variance = max(0.0, weighted_sq_sum / total - mean * mean)
    nonzero = np.flatnonzero(hist > 0)
    return {
        "meanC": float(mean),
        "medianC": q(0.50),
        "p05C": q(0.05),
        "p25C": q(0.25),
        "p75C": q(0.75),
        "p95C": q(0.95),
        "minC": float(edges[nonzero[0]]) if nonzero.size else None,
        "maxC": float(edges[nonzero[-1] + 1]) if nonzero.size else None,
        "stdC": float(math.sqrt(variance)),
        "quantilesApproximate": True,
    }


def detect_coord_names(ds: xr.Dataset) -> tuple[str, str]:
    lat_name = next((n for n in ("latitude", "lat") if n in ds.coords), None)
    lon_name = next((n for n in ("longitude", "lon") if n in ds.coords), None)
    if not lat_name or not lon_name:
        raise KeyError(f"Could not find latitude/longitude coordinates. Available: {list(ds.coords)}")
    return lat_name, lon_name




def detect_time_name(obj) -> str:
    dims = getattr(obj, "dims", {})
    coords = getattr(obj, "coords", {})
    for name in ("time", "valid_time", "date"):
        if name in dims or name in coords:
            return name
    raise KeyError(f"Could not find time dimension. Available dims: {list(dims)}")

def detect_temperature_variable(ds: xr.Dataset, source: str) -> str:
    candidates = {
        "era5-land": ("t2m", "2m_temperature", "temperature_2m"),
        "era5": ("t2m", "2m_temperature", "temperature_2m"),
        "oisst": ("sst",),
    }.get(source, ("t2m", "sst"))
    for name in candidates:
        if name in ds.data_vars:
            return name
    vars_ = list(ds.data_vars)
    if len(vars_) == 1:
        return vars_[0]
    raise KeyError(f"Could not detect temperature variable for {source}. Available: {vars_}")


def temperature_to_c(values: np.ndarray, units: str | None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    u = str(units or "").lower().strip().replace("_", " ")
    if u in {"k", "degk", "kelvin", "degrees kelvin", "degree kelvin"}:
        return arr - 273.15
    if u in {"degc", "c", "celsius", "degree celsius", "degrees celsius", "degree c", "degrees c"}:
        return arr
    raise ValueError(f"Unsupported or missing temperature units: {units!r}")



def canonicalize_grid(ds: xr.Dataset) -> tuple[xr.Dataset, str, str]:
    lat_name, lon_name = detect_coord_names(ds)
    lon = np.asarray(ds[lon_name].values, dtype=np.float64)
    lon180 = ((lon + 180.0) % 360.0) - 180.0
    ds = ds.assign_coords({lon_name: lon180}).sortby(lon_name).sortby(lat_name, ascending=False)
    return ds, lat_name, lon_name


def regular_step(values: np.ndarray) -> float:
    vals = np.asarray(values, dtype=np.float64)
    if vals.size < 2:
        raise ValueError("grid needs at least two coordinates")
    diffs = np.abs(np.diff(vals))
    step = float(np.median(diffs))
    if step <= 0 or not np.allclose(diffs, step, atol=1e-6):
        raise ValueError("temperature coordinates must form a regular grid")
    return step


def grid_cell_area_rows(lat_desc: np.ndarray, lon_step_deg: float, lat_step_deg: float) -> np.ndarray:
    lat = np.asarray(lat_desc, dtype=np.float64)
    north = np.deg2rad(np.clip(lat + lat_step_deg / 2.0, -90.0, 90.0))
    south = np.deg2rad(np.clip(lat - lat_step_deg / 2.0, -90.0, 90.0))
    dlon = math.radians(lon_step_deg)
    return (EARTH_RADIUS_M ** 2) * dlon * (np.sin(north) - np.sin(south))


def fractional_weight_window(geom, lat_desc: np.ndarray, lon_asc: np.ndarray, supersample: int = 4) -> WeightWindow | None:
    if geom is None or geom.is_empty:
        return None
    lat = np.asarray(lat_desc, dtype=np.float64)
    lon = np.asarray(lon_asc, dtype=np.float64)
    dlat = regular_step(lat)
    dlon = regular_step(lon)
    minx, miny, maxx, maxy = geom.bounds
    row_mask = (lat >= miny - dlat) & (lat <= maxy + dlat)
    col_mask = (lon >= minx - dlon) & (lon <= maxx + dlon)
    rows = np.flatnonzero(row_mask)
    cols = np.flatnonzero(col_mask)
    if not rows.size or not cols.size:
        return None
    row0, row1 = int(rows.min()), int(rows.max()) + 1
    col0, col1 = int(cols.min()), int(cols.max()) + 1
    h, w = row1 - row0, col1 - col0
    ss = max(1, int(supersample))
    west = float(lon[col0] - dlon / 2.0)
    north = float(lat[row0] + dlat / 2.0)
    transform = from_origin(west, north, dlon / ss, dlat / ss)
    hi = rasterize(
        [(mapping(geom), 1)],
        out_shape=(h * ss, w * ss),
        transform=transform,
        fill=0,
        default_value=1,
        dtype="uint8",
        all_touched=False,
    )
    frac = hi.reshape(h, ss, w, ss).mean(axis=(1, 3), dtype=np.float64)
    area_rows = grid_cell_area_rows(lat[row0:row1], dlon, dlat)
    weights = frac * area_rows[:, None]
    if not np.any(weights > 0):
        return None
    return WeightWindow(row0, row1, col0, col1, weights)


def iter_polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty]
    parts: list[Polygon] = []
    for g in getattr(geom, "geoms", []):
        parts.extend(iter_polygon_parts(g))
    return parts


def antimeridian_safe_parts(geom) -> list[Polygon]:
    """Return polygon parts whose longitude span is <= 180 degrees when possible.

    Most source GIS files already split antimeridian features. This repairs the
    remaining wide rings by shifting negative longitudes into 0..360, clipping
    at 180, then shifting the eastern piece back to -180..0.
    """
    out: list[Polygon] = []
    for part in iter_polygon_parts(geom):
        minx, _, maxx, _ = part.bounds
        if maxx - minx <= 180:
            out.append(part)
            continue

        shifted = transform(lambda x, y, z=None: (np.where(np.asarray(x) < 0, np.asarray(x) + 360.0, np.asarray(x)), y), part)
        west = shifted.intersection(box(0, -90, 180, 90))
        east = shifted.intersection(box(180, -90, 360, 90))
        out.extend(iter_polygon_parts(west))
        if not east.is_empty:
            east_back = transform(lambda x, y, z=None: (np.where(np.asarray(x) >= 180, np.asarray(x) - 360.0, np.asarray(x)), y), east)
            out.extend(iter_polygon_parts(east_back))
    return [p for p in out if not p.is_empty]


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_topojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def region_id_from_row(row, index_value=None) -> str | None:
    for key in ("regionId", "region_id", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    if index_value is not None:
        idx = str(index_value).strip()
        if idx.startswith(("eco_", "marine_", "lake_")):
            return idx
    eco = row.get("ECO_ID") or row.get("eco_id") or row.get("ecoId")
    if eco is not None:
        return f"eco_{eco}"
    return None


def merged_geometries(paths) -> dict[str, object]:
    parts = {}
    for path in paths:
        if not path.exists(): continue
        for idx, row in read_topojson(path).iterrows():
            rid = region_id_from_row(row, idx)
            if not rid or row.geometry is None or row.geometry.is_empty: continue
            geom = row.geometry if row.geometry.is_valid else make_valid(row.geometry)
            parts.setdefault(rid, []).extend(iter_polygon_parts(geom))
    return {rid: unary_union(items) for rid, items in parts.items() if items}


def load_land_geometries(repo: Path) -> dict[str, object]:
    # Match the committed detailed map, merging split features by logical region ID.
    return merged_geometries([repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"])


def load_marine_geometries(repo: Path) -> dict[str, object]:
    return merged_geometries([repo / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson"])


def load_lake_geometries(repo: Path) -> dict[str, object]:
    return merged_geometries([repo / "frontend/public/data/lakes/lod0/lakes_lod0.topojson"])


def open_downloaded_dataset(path: Path) -> tuple[xr.Dataset, tempfile.TemporaryDirectory | None]:
    if not zipfile.is_zipfile(path): return xr.open_dataset(path), None
    tmp = tempfile.TemporaryDirectory(prefix="motherworld_climate_")
    opened = []
    try:
        root = Path(tmp.name).resolve()
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".nc"): continue
                target = (root / info.filename).resolve()
                if root not in target.parents: raise ValueError("Unsafe NetCDF archive path")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as dest:
                    import shutil
                    shutil.copyfileobj(source, dest)
        nc_files = sorted(root.rglob("*.nc"))
        if not nc_files: raise ValueError("CDS archive contains no NetCDF files")
        opened = [xr.open_dataset(file) for file in nc_files]
        if len(opened) == 1: return opened[0], tmp
        combined = xr.combine_by_coords(opened, combine_attrs="override")
        combined.set_close(lambda: [ds.close() for ds in opened])
        return combined, tmp
    except Exception:
        for ds in opened: ds.close()
        tmp.cleanup()
        raise


def processing_fingerprint(options: dict, geometries: dict) -> str:
    digest = hashlib.sha256(json.dumps(options, sort_keys=True).encode())
    for rid, geom in sorted(geometries.items()):
        digest.update(rid.encode()); digest.update(geom.wkb)
    return digest.hexdigest()


def daily_offsets(times, year: int) -> np.ndarray:
    days = np.asarray(times).astype("datetime64[D]")
    begin, end = np.datetime64(f"{year}-01-01"), np.datetime64(f"{year+1}-01-01")
    if np.any(np.isnat(days)) or np.any(days < begin) or np.any(days >= end):
        raise ValueError("Temperature dates fall outside the requested year")
    offsets = (days - begin).astype(int)
    if len(set(offsets)) != len(offsets): raise ValueError("Duplicate daily temperature timestamps")
    return offsets


def accumulate_window(data_c: np.ndarray, weights: np.ndarray, edges: np.ndarray):
    flat = np.asarray(data_c, dtype=np.float64).reshape(data_c.shape[0], -1)
    w = np.asarray(weights, dtype=np.float64).reshape(-1)
    if flat.shape[1] != w.size or np.any(~np.isfinite(w)) or np.any(w < 0):
        raise ValueError("Invalid climate area weights")
    valid = np.isfinite(flat) & (w > 0)[None, :]
    if np.any(valid & ((flat < edges[0]) | (flat > edges[-1]))):
        raise ValueError("Temperature falls outside histogram limits; increase --bin-min/--bin-max")
    safe = np.where(valid, flat, 0.0)
    den = valid.astype(np.float64) @ w
    num = safe @ w
    hist = np.zeros(len(edges) - 1, dtype=np.float64)
    weighted_sum = weighted_sq_sum = 0.0
    for i in range(len(flat)):
        mask = valid[i]
        if not np.any(mask): continue
        values, ww = flat[i, mask], w[mask]
        hist += np.histogram(values, bins=edges, weights=ww)[0]
        weighted_sum += float(np.dot(values, ww))
        weighted_sq_sum += float(np.dot(values * values, ww))
    if not np.isclose(hist.sum(), den.sum(), rtol=1e-8): raise ValueError("Histogram lost weighted samples")
    return num, den, hist, weighted_sum, weighted_sq_sum


def write_json_atomic(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


@contextlib.contextmanager
def climate_index_lock(repo: Path):
    path = repo / ".cache/motherworld/climate/index.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0: stream.write(b"0"); stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try: yield
        finally:
            stream.seek(0)
            if os.name == "nt": msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


def update_climate_index(repo: Path, entries: dict[str, dict], sources: dict[str, dict]) -> None:
    path = repo / "frontend/public/data/climate/climate.index.json"
    with climate_index_lock(repo):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schemaVersion": 1, "regions": {}, "sources": {}}
        data["generatedAt"] = utc_now_iso()
        data.setdefault("regions", {}).update(entries)
        data.setdefault("sources", {}).update(sources)
        write_json_atomic(path, data)


def write_region_payload(repo: Path, kind: str, region_id: str, payload: dict) -> str:
    if kind not in {"land", "lakes", "marine"} or not re.fullmatch(r"[a-zA-Z0-9_-]+", region_id):
        raise ValueError("Invalid climate output path")
    if payload.get("regionId") != region_id: raise ValueError("Climate payload region mismatch")
    edges = np.asarray(payload["bins"]["edgesC"], dtype=float)
    if len(edges) < 3 or not np.all(np.isfinite(edges)) or np.any(np.diff(edges) <= 0):
        raise ValueError("Invalid climate bin edges")
    for dist in payload["distributions"].values():
        percent = dist["percent"]
        if len(percent) != len(edges) - 1 or not np.all(np.isfinite(percent)) or np.any(np.asarray(percent) < 0) or not np.isclose(sum(percent), 100):
            raise ValueError(f"Cannot publish an empty or incomplete distribution: {region_id}")
    rel = f"{kind}/{region_id}.temperature.json"
    write_json_atomic(repo / "frontend/public/data/climate" / rel, payload)
    return rel
