from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import geopandas as gpd
import numpy as np
import xarray as xr
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.ops import unary_union
from pyproj import Geod

from geology_common import WGS84, as_float, load_regions, percentile

OPEN_OCEAN_IDS = {"marine_ocean_0", "open_ocean"}


def is_open_ocean_id(region_id: str) -> bool:
    rid = str(region_id or "")
    return rid in OPEN_OCEAN_IDS or rid.endswith("ocean_0")


def load_marine_analysis_regions(repo: Path, whole_ocean_for_open_ocean: bool = True) -> gpd.GeoDataFrame:
    # Include canonical land coverage while constructing the synthetic
    # whole-ocean scope, then return only marine analysis regions.
    regions = load_regions(
        Path(repo),
        kinds=("land", "marine", "lakes"),
        globalize_open_ocean=whole_ocean_for_open_ocean,
    )
    return regions[regions.kind == "marine"].copy()


def polygon_parts(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g is not None and not g.is_empty]
    out = []
    for child in getattr(geom, "geoms", []):
        out.extend(polygon_parts(child))
    return out


def detect_coord_names(ds: xr.Dataset | xr.DataArray) -> tuple[str, str]:
    coords = getattr(ds, "coords", {})
    lat = next((n for n in ("lat", "latitude", "y") if n in coords), None)
    lon = next((n for n in ("lon", "longitude", "x") if n in coords), None)
    if not lat or not lon:
        raise KeyError(f"Could not detect latitude/longitude coordinates; coords={list(coords)}")
    return lat, lon


def choose_variable(ds: xr.Dataset, requested: str | None = None, candidates=()) -> str:
    if requested:
        if requested not in ds.data_vars:
            raise KeyError(f"Variable {requested!r} not found; available={list(ds.data_vars)}")
        return requested
    for name in candidates:
        if name in ds.data_vars:
            return name
    scored = []
    for name, da in ds.data_vars.items():
        try:
            lat, lon = detect_coord_names(da)
        except Exception:
            continue
        if lat in da.dims and lon in da.dims:
            scored.append(name)
    if len(scored) == 1:
        return scored[0]
    if scored:
        return scored[0]
    raise KeyError(f"Could not choose a 2-D gridded variable; available={list(ds.data_vars)}")


def _native_step(values: np.ndarray) -> float:
    vals = np.asarray(values, dtype=float)
    if vals.size < 2:
        return 1.0
    diffs = np.abs(np.diff(vals))
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    return float(np.median(diffs)) if diffs.size else 1.0


@dataclass
class Grid:
    values: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    variable: str
    units: str | None
    native_step_deg: float
    sampled_step_deg: float


def load_grid(path: Path, variable: str | None = None, candidates=(), target_step_deg: float = 0.10) -> Grid:
    """Load a global lon/lat NetCDF/GMT grid at a statistics-friendly resolution.

    The source remains authoritative; this routine only thins it for regional summary statistics.
    Files are expected to be geographic regular grids. GeoTIFF support is intentionally left to
    conversion with GDAL because provider NetCDF/GMT grids retain variable/unit metadata better.
    """
    path = Path(path)
    if path.suffix.lower() not in {".nc", ".grd", ".netcdf"}:
        raise ValueError("Seafloor raster builders currently expect NetCDF/GMT .nc/.grd grids")
    ds = xr.open_dataset(path, mask_and_scale=True)
    try:
        var = choose_variable(ds, variable, candidates)
        da = ds[var].squeeze(drop=True)
        lat_name, lon_name = detect_coord_names(da)
        # Only spatial dims may remain after squeezing.
        extra = [d for d in da.dims if d not in (lat_name, lon_name)]
        if extra:
            raise ValueError(f"Variable {var!r} has unsupported non-spatial dimensions: {extra}")
        lon = np.asarray(da[lon_name].values, dtype=float)
        lat = np.asarray(da[lat_name].values, dtype=float)
        if lon.ndim != 1 or lat.ndim != 1:
            raise ValueError("Only regular 1-D latitude/longitude grids are supported")
        if np.nanmax(lon) > 180.0001:
            lon180 = ((lon + 180.0) % 360.0) - 180.0
            da = da.assign_coords({lon_name: lon180}).sortby(lon_name)
        else:
            da = da.sortby(lon_name)
        da = da.sortby(lat_name, ascending=False)
        lat = np.asarray(da[lat_name].values, dtype=float)
        lon = np.asarray(da[lon_name].values, dtype=float)
        native = max(_native_step(lat), _native_step(lon))
        stride = max(1, int(round(max(float(target_step_deg), native) / native)))
        sampled = da.isel({lat_name: slice(None, None, stride), lon_name: slice(None, None, stride)}).load()
        vals = np.asarray(sampled.values, dtype=float)
        lat = np.asarray(sampled[lat_name].values, dtype=float)
        lon = np.asarray(sampled[lon_name].values, dtype=float)
        return Grid(
            values=vals,
            lat=lat,
            lon=lon,
            variable=var,
            units=str(da.attrs.get("units")) if da.attrs.get("units") is not None else None,
            native_step_deg=native,
            sampled_step_deg=max(_native_step(lat), _native_step(lon)),
        )
    finally:
        ds.close()


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float | None:
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not np.any(ok):
        return None
    v = v[ok]; w = w[ok]
    order = np.argsort(v)
    v = v[order]; w = w[order]
    cdf = np.cumsum(w)
    target = float(q) * float(cdf[-1])
    idx = int(np.searchsorted(cdf, target, side="left"))
    return float(v[min(max(idx, 0), len(v)-1)])


def _region_subset(grid: Grid, geom):
    minx, miny, maxx, maxy = geom.bounds
    lat_mask = (grid.lat >= miny - grid.sampled_step_deg) & (grid.lat <= maxy + grid.sampled_step_deg)
    lon_mask = (grid.lon >= minx - grid.sampled_step_deg) & (grid.lon <= maxx + grid.sampled_step_deg)
    ri = np.flatnonzero(lat_mask); ci = np.flatnonzero(lon_mask)
    if not ri.size or not ci.size:
        return None
    r0, r1 = int(ri.min()), int(ri.max()) + 1
    c0, c1 = int(ci.min()), int(ci.max()) + 1
    vals = grid.values[r0:r1, c0:c1]
    lat = grid.lat[r0:r1]; lon = grid.lon[c0:c1]
    if vals.size == 0 or lat.size == 0 or lon.size == 0:
        return None
    dlat = _native_step(lat) if lat.size > 1 else grid.sampled_step_deg
    dlon = _native_step(lon) if lon.size > 1 else grid.sampled_step_deg
    transform = from_origin(float(lon[0] - dlon/2), float(lat[0] + dlat/2), float(dlon), float(dlat))
    mask = geometry_mask([mapping(geom)], out_shape=vals.shape, transform=transform, invert=True, all_touched=False)
    weights = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, lon.size), dtype=float)
    return vals, mask, weights


def regional_values(grid: Grid, geom, transform_value: Callable[[np.ndarray], np.ndarray] | None = None,
                    valid: Callable[[np.ndarray], np.ndarray] | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    all_v=[]; all_w=[]; covered=0.0; total=0.0
    parts = polygon_parts(geom)
    # A unioned global-ocean polygon usually carries land holes; keep it intact instead of exploding it.
    if geom is not None and not geom.is_empty and (geom.bounds[2] - geom.bounds[0]) > 300:
        parts = [geom]
    for part in parts:
        sub = _region_subset(grid, part)
        if sub is None: continue
        values, mask, weights = sub
        total += float(weights[mask].sum())
        v = np.asarray(values, dtype=float)
        if transform_value is not None:
            v = transform_value(v)
        ok = mask & np.isfinite(v)
        if valid is not None:
            ok &= np.asarray(valid(v), dtype=bool)
        if not np.any(ok): continue
        covered += float(weights[ok].sum())
        all_v.append(v[ok]); all_w.append(weights[ok])
    if not all_v:
        return np.asarray([],dtype=float), np.asarray([],dtype=float), 0.0
    coverage = 100.0 * covered / total if total > 0 else 0.0
    return np.concatenate(all_v), np.concatenate(all_w), coverage


def stats(values: np.ndarray, weights: np.ndarray) -> dict:
    v=np.asarray(values,dtype=float); w=np.asarray(weights,dtype=float)
    ok=np.isfinite(v)&np.isfinite(w)&(w>0); v=v[ok]; w=w[ok]
    if not v.size:return {}
    mean=float(np.average(v,weights=w))
    return {
        "mean": mean,
        "median": weighted_quantile(v,w,0.50),
        "p10": weighted_quantile(v,w,0.10),
        "p90": weighted_quantile(v,w,0.90),
        "min": float(np.min(v)),
        "max": float(np.max(v)),
        "sampleCells": int(v.size),
    }


def category_percentages(values: np.ndarray, weights: np.ndarray, bins) -> list[dict]:
    v=np.asarray(values,dtype=float); w=np.asarray(weights,dtype=float); total=float(np.sum(w))
    out=[]
    for label,lo,hi in bins:
        sel=(v>=lo) & (v < hi if math.isfinite(hi) else True)
        pct=100.0*float(w[sel].sum())/total if total>0 else 0.0
        out.append({"label":label,"percent":pct,"min":None if not math.isfinite(lo) else lo,"max":None if not math.isfinite(hi) else hi})
    return out


def projected_line_intersections(lines:gpd.GeoDataFrame, regions:gpd.GeoDataFrame) -> dict:
    """Return intersection count and length (km) by region for line features."""
    if lines.empty or regions.empty:return {}
    if lines.crs is None: lines=lines.set_crs(WGS84)
    lines=lines.to_crs(WGS84)
    out={}
    # spatial index narrows candidates before expensive projected length calculation
    joined=gpd.sjoin(lines[["geometry"]],regions[["regionId","regionName","kind","geometry"]],how="inner",predicate="intersects")
    for rid,g in joined.groupby("regionId"):
        reg=regions[regions.regionId==rid].iloc[0]
        reg_geom=reg.geometry
        clips=[]
        for idx in g.index.unique():
            inter=lines.loc[idx].geometry.intersection(reg_geom)
            if inter is not None and not inter.is_empty:clips.append(inter)
        if not clips:continue
        geod=Geod(ellps="WGS84")
        length_m=sum(abs(float(geod.geometry_length(geom))) for geom in clips)
        out[rid]={"count":len(clips),"intersectionLengthKm":length_m/1000.0,"regionName":reg.regionName,"kind":reg.kind}
    return out
