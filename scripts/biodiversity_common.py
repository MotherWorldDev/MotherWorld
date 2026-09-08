from __future__ import annotations

import json
import math
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import transform as shp_transform, unary_union

WGS84 = CRS.from_epsg(4326)
EQUAL_AREA = CRS.from_epsg(6933)
EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class Region:
    region_id: str
    name: str
    kind: str
    geometry: object
    area_km2: float | None = None


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


def provider_root(repo: Path, provider: str) -> Path:
    return repo / ".biodiversity-work" / "providers" / provider


def provider_region_path(repo: Path, provider: str, kind: str, region_id: str) -> Path:
    return provider_root(repo, provider) / kind / f"{region_id}.json"


def write_provider_region(repo: Path, provider: str, kind: str, region_id: str, payload: dict) -> None:
    write_json(provider_region_path(repo, provider, kind, region_id), payload, compact=True)


def iter_provider_regions(repo: Path, provider: str) -> Iterator[tuple[str, str, dict]]:
    root = provider_root(repo, provider)
    if not root.exists():
        return
    for kind_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(kind_dir.glob("*.json")):
            yield kind_dir.name, path.stem, read_json(path, {})


def load_metadata(repo: Path, kind: str) -> dict[str, dict]:
    if kind == "land":
        path = repo / "frontend/public/data/regions.index.json"
    elif kind == "marine":
        path = repo / "frontend/public/data/marine.index.json"
    elif kind == "lakes":
        path = repo / "frontend/public/data/lakes.index.json"
    else:
        return {}
    return read_json(path, {}).get("regions", {})


def _infer_field(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    lookup = {str(c).lower(): str(c) for c in columns}
    for candidate in candidates:
        hit = lookup.get(candidate.lower())
        if hit:
            return hit
    return None


def _normalize_land_id(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("eco_"):
        return text
    try:
        return f"eco_{int(float(text))}"
    except Exception:
        return text


def _read_layer(path: Path, kind: str) -> dict[str, object]:
    if not path.exists():
        return {}
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    id_field = _infer_field(gdf.columns, ("id", "region_id", "regionId"))
    eco_field = _infer_field(gdf.columns, ("ECO_ID", "eco_id", "ECOID")) if kind == "land" else None
    result: dict[str, object] = {}
    for idx, row in gdf.iterrows():
        rid = None
        if id_field and row.get(id_field) is not None:
            rid = str(row[id_field])
        elif kind == "land" and eco_field and row.get(eco_field) is not None:
            rid = _normalize_land_id(row[eco_field])
        elif str(idx).startswith(("eco_", "marine_", "lake_")):
            rid = str(idx)
        if kind == "land":
            rid = _normalize_land_id(rid)
        if not rid or row.geometry is None or row.geometry.is_empty:
            continue
        if rid in result:
            result[rid] = unary_union([result[rid], row.geometry])
        else:
            result[rid] = row.geometry
    return result


def load_region_geometries(repo: Path, kind: str) -> dict[str, object]:
    if kind == "land":
        shp = repo / "Ecoregions2017/Ecoregions2017.shp"
        if shp.exists():
            return _read_layer(shp, "land")
        realm_dir = repo / "frontend/public/data/lod1_realms"
        files = sorted(realm_dir.glob("*.topojson"))
        result: dict[str, object] = {}
        for file in files:
            result.update(_read_layer(file, "land"))
        if result:
            return result
        return _read_layer(repo / "frontend/public/data/lod0/ecoregions_lod0.topojson", "land")
    if kind == "marine":
        return _read_layer(repo / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson", "marine")
    if kind == "lakes":
        return _read_layer(repo / "frontend/public/data/lakes/lod0/lakes_lod0.topojson", "lakes")
    raise ValueError(f"Unsupported region kind: {kind}")


def load_regions(repo: Path, kinds: Iterable[str] = ("land",)) -> list[Region]:
    out: list[Region] = []
    for kind in kinds:
        metadata = load_metadata(repo, kind)
        geoms = load_region_geometries(repo, kind)
        for rid, geom in geoms.items():
            meta = metadata.get(rid, {})
            out.append(
                Region(
                    rid,
                    str(meta.get("name") or rid),
                    kind,
                    geom,
                    float(meta["areaKm2"]) if meta.get("areaKm2") not in (None, "") else None,
                )
            )
    out.sort(key=lambda r: (r.kind, r.region_id))
    return out


def polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    out: list[Polygon] = []
    for part in getattr(geom, "geoms", []):
        out.extend(polygon_parts(part))
    return out


def antimeridian_safe_geometry(geom):
    pieces: list[Polygon] = []
    for part in polygon_parts(geom):
        minx, _, maxx, _ = part.bounds
        if maxx - minx <= 180:
            pieces.append(part)
            continue
        shifted = shp_transform(
            lambda x, y, z=None: (np.where(np.asarray(x) < 0, np.asarray(x) + 360.0, np.asarray(x)), y),
            part,
        )
        west = shifted.intersection(box(0, -90, 180, 90))
        east = shifted.intersection(box(180, -90, 360, 90))
        pieces.extend(polygon_parts(west))
        if not east.is_empty:
            back = shp_transform(
                lambda x, y, z=None: (np.where(np.asarray(x) >= 180, np.asarray(x) - 360.0, np.asarray(x)), y),
                east,
            )
            pieces.extend(polygon_parts(back))
    return unary_union(pieces) if pieces else geom


def project_geometry(geom, src_crs, dst_crs):
    src = CRS.from_user_input(src_crs)
    dst = CRS.from_user_input(dst_crs)
    if src == dst:
        return geom
    transformer = Transformer.from_crs(src, dst, always_xy=True)
    return shp_transform(transformer.transform, geom)


def equal_area_geometry(geom, src_crs=WGS84):
    return project_geometry(geom, src_crs, EQUAL_AREA)


def _window_for_geometry(ds: rasterio.DatasetReader, geom) -> Window | None:
    try:
        raw = from_bounds(*geom.bounds, transform=ds.transform)
    except Exception:
        return None
    row0 = max(0, int(math.floor(raw.row_off)))
    col0 = max(0, int(math.floor(raw.col_off)))
    row1 = min(ds.height, int(math.ceil(raw.row_off + raw.height)))
    col1 = min(ds.width, int(math.ceil(raw.col_off + raw.width)))
    if row1 <= row0 or col1 <= col0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def _fraction_mask(ds, geom, window: Window, supersample: int) -> np.ndarray:
    h, w = int(window.height), int(window.width)
    ss = max(1, int(supersample))
    transform = ds.window_transform(window)
    if ss == 1:
        return rasterize(
            [(mapping(geom), 1.0)],
            out_shape=(h, w),
            transform=transform,
            fill=0,
            dtype="float32",
            all_touched=False,
        )
    hi_transform = transform * rasterio.Affine.scale(1 / ss, 1 / ss)
    hi = rasterize(
        [(mapping(geom), 1.0)],
        out_shape=(h * ss, w * ss),
        transform=hi_transform,
        fill=0,
        dtype="float32",
        all_touched=False,
    )
    return hi.reshape(h, ss, w, ss).mean(axis=(1, 3), dtype=np.float64)


def _cell_area_weights(ds, window: Window) -> np.ndarray:
    transform = ds.window_transform(window)
    h, w = int(window.height), int(window.width)
    crs = CRS.from_user_input(ds.crs) if ds.crs else None
    if crs and crs.is_geographic and abs(transform.b) < 1e-12 and abs(transform.d) < 1e-12:
        dlon = abs(transform.a)
        dlat = abs(transform.e)
        rows = np.arange(h)
        lat_center = transform.f + (rows + 0.5) * transform.e
        north = np.deg2rad(np.clip(lat_center + dlat / 2, -90, 90))
        south = np.deg2rad(np.clip(lat_center - dlat / 2, -90, 90))
        row_area = EARTH_RADIUS_M**2 * math.radians(dlon) * np.abs(np.sin(north) - np.sin(south))
        return np.repeat(row_area[:, None], w, axis=1)
    pixel_area = abs(transform.a * transform.e - transform.b * transform.d)
    return np.full((h, w), pixel_area if pixel_area > 0 else 1.0, dtype=np.float64)


def zonal_raster_stats(
    raster_path: Path,
    region: Region,
    *,
    band: int = 1,
    supersample: int = 2,
    value_scale: float = 1.0,
    value_offset: float = 0.0,
) -> dict | None:
    with rasterio.open(raster_path) as ds:
        if not ds.crs:
            raise RuntimeError(f"Raster has no CRS: {raster_path}")
        geom = project_geometry(antimeridian_safe_geometry(region.geometry), WGS84, ds.crs)
        window = _window_for_geometry(ds, geom)
        if window is None:
            return None
        arr = ds.read(band, window=window, masked=True).astype(np.float64)
        if arr.size == 0:
            return None
        values = np.asarray(arr.filled(np.nan), dtype=np.float64) * value_scale + value_offset
        fraction = _fraction_mask(ds, geom, window, supersample)
        valid = np.isfinite(values) & (fraction > 0)
        if not np.any(valid):
            return None
        cell_area = _cell_area_weights(ds, window)
        weights = fraction * cell_area
        vw = weights[valid]
        vv = values[valid]
        total_weight = float(vw.sum())
        if total_weight <= 0:
            return None
        mean = float(np.dot(vv, vw) / total_weight)
        order = np.argsort(vv)
        svals = vv[order]
        sw = vw[order]
        cdf = np.cumsum(sw) / total_weight

        def q(p: float) -> float:
            idx = int(np.searchsorted(cdf, p, side="left"))
            return float(svals[min(max(idx, 0), len(svals) - 1)])

        return {
            "areaWeightedMean": mean,
            "p10": q(0.10),
            "median": q(0.50),
            "p90": q(0.90),
            "min": float(np.nanmin(vv)),
            "max": float(np.nanmax(vv)),
            "fractionWeightedCellSum": float(np.sum(vv * fraction[valid])),
            "validCellCount": int(np.count_nonzero(valid)),
            "coveredAreaKm2": total_weight / 1_000_000.0,
        }


def extract_raster_candidates(input_path: Path, temp_dir: Path | None = None) -> tuple[list[Path], tempfile.TemporaryDirectory | None]:
    path = input_path.resolve()
    if path.is_dir():
        files = [
            p for p in path.rglob("*")
            if p.suffix.lower() in {".tif", ".tiff", ".img", ".nc", ".grd"} and p.is_file()
        ]
        return sorted(files), None
    if path.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="motherworld_biodiversity_") if temp_dir is None else None
        target = Path(tmp.name) if tmp else temp_dir
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(target)
        files = [
            p for p in target.rglob("*")
            if p.suffix.lower() in {".tif", ".tiff", ".img", ".nc", ".grd"} and p.is_file()
        ]
        return sorted(files), tmp
    return [path], None


def year_from_name(path: Path) -> int | None:
    matches = re.findall(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", path.name)
    if not matches:
        return None
    return int(matches[-1])


def percentile_rank(values: dict[str, float], *, high_is_high: bool = True) -> dict[str, float]:
    clean = [(rid, float(v)) for rid, v in values.items() if v is not None and math.isfinite(float(v))]
    if not clean:
        return {}
    clean.sort(key=lambda x: x[1])
    n = len(clean)
    result = {}
    for i, (rid, _) in enumerate(clean):
        pct = 100.0 if n == 1 else 100.0 * i / (n - 1)
        result[rid] = pct if high_is_high else 100.0 - pct
    return result


def discover_shapefiles(input_path: Path) -> tuple[list[Path], list[tempfile.TemporaryDirectory]]:
    path = input_path.resolve()
    temps: list[tempfile.TemporaryDirectory] = []
    if path.is_dir():
        return sorted(path.rglob("*.shp")), temps
    if path.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="motherworld_iucn_")
        temps.append(tmp)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp.name)
        return sorted(Path(tmp.name).rglob("*.shp")), temps
    if path.suffix.lower() == ".shp":
        return [path], temps
    raise ValueError(f"Expected shapefile, zip, or directory: {input_path}")


def normalize_category(value) -> str | None:
    text = str(value or "").strip().upper().replace(" ", "_")
    aliases = {
        "CRITICALLY_ENDANGERED": "CR",
        "ENDANGERED": "EN",
        "VULNERABLE": "VU",
        "NEAR_THREATENED": "NT",
        "LEAST_CONCERN": "LC",
        "DATA_DEFICIENT": "DD",
        "EXTINCT": "EX",
        "EXTINCT_IN_THE_WILD": "EW",
    }
    if text in aliases:
        return aliases[text]
    if text in {"CR", "EN", "VU", "NT", "LC", "DD", "EX", "EW", "NE"}:
        return text
    return None


def safe_float(value, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default
