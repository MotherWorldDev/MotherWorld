#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import ee
from shapely.geometry import mapping

from analysis_geometry import analysis_geometry_cache_identity, analysis_geometry_metadata

from climate_extras_common import (
    HUMIDITY_EDGES_PCT,
    PRECIP_EDGES_MM,
    WIND_EDGES_MS,
    WIND_SECTORS,
    atomic_json,
    load_geometries,
    load_region_names,
    parse_year_range,
    payload_from_raw,
)

SOURCE_BY_KIND = {
    "land": {
        "id": "era5_land_climate_normals",
        "label": "ECMWF ERA5-Land Daily Aggregated",
        "collection": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "spatialResolution": "~11.1 km",
    },
    "lakes": {
        "id": "era5_land_climate_normals",
        "label": "ECMWF ERA5-Land Daily Aggregated",
        "collection": "ECMWF/ERA5_LAND/DAILY_AGGR",
        "spatialResolution": "~11.1 km",
    },
    "marine": {
        "id": "era5_climate_normals",
        "label": "ECMWF ERA5 Daily + hourly completion",
        "collection": "ECMWF/ERA5/DAILY",
        "completionAsset": "ECMWF/ERA5/HOURLY",
        "spatialResolution": "~27.8 km",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def bounded_edges(edges):
    # EE needs a finite upper edge; these ceilings are well outside ordinary daily climatology.
    out = list(edges)
    if not math.isfinite(out[-1]):
        out[-1] = {tuple(PRECIP_EDGES_MM): 10000.0, tuple(WIND_EDGES_MS): 200.0}.get(tuple(edges), 1e9)
    return out


def rh_from_t_td(t_k: ee.Image, td_k: ee.Image) -> ee.Image:
    t = t_k.subtract(273.15)
    td = td_k.subtract(273.15)
    a = 17.625
    b = 243.04
    exponent = td.multiply(a).divide(td.add(b)).subtract(t.multiply(a).divide(t.add(b)))
    return exponent.exp().multiply(100).clamp(0, 100).rename("rh")


def _derive_daily_image(img, kind: str) -> ee.Image:
    img = ee.Image(img)
    if kind in ("land", "lakes"):
        t = img.select("temperature_2m")
        td = img.select("dewpoint_temperature_2m")
        u = img.select("u_component_of_wind_10m")
        v = img.select("v_component_of_wind_10m")
        p = img.select("total_precipitation_sum").multiply(1000).max(0).rename("precip_mm")
    else:
        t = img.select("mean_2m_air_temperature")
        td = img.select("dewpoint_2m_temperature")
        u = img.select("u_component_of_wind_10m")
        v = img.select("v_component_of_wind_10m")
        p = img.select("total_precipitation").multiply(1000).max(0).rename("precip_mm")
    rh = rh_from_t_td(t, td)
    speed = u.pow(2).add(v.pow(2)).sqrt().rename("wind_ms")
    direction = u.multiply(-1).atan2(v.multiply(-1)).multiply(180 / math.pi).add(360).mod(360).rename("wind_dir")
    return ee.Image.cat([p, rh, speed, direction]).copyProperties(img, ["system:time_start"])


def _era5_hourly_to_daily_for_year(year: int) -> ee.ImageCollection:
    # The precomputed Earth Engine ERA5/DAILY catalog currently stops in July 2020.
    # Build the requested full calendar year from the current hourly ERA5 asset so
    # MotherWorld's 1991–2020 marine climatology does not contain a partial final year.
    from datetime import date, timedelta
    hourly = ee.ImageCollection("ECMWF/ERA5/HOURLY").select([
        "temperature_2m", "dewpoint_temperature_2m",
        "u_component_of_wind_10m", "v_component_of_wind_10m", "total_precipitation",
    ])
    images = []
    day = date(year, 1, 1)
    stop = date(year + 1, 1, 1)
    while day < stop:
        nxt = day + timedelta(days=1)
        c = hourly.filterDate(day.isoformat(), nxt.isoformat())
        t = c.select("temperature_2m").mean().rename("mean_2m_air_temperature")
        td = c.select("dewpoint_temperature_2m").mean().rename("dewpoint_2m_temperature")
        u = c.select("u_component_of_wind_10m").mean().rename("u_component_of_wind_10m")
        v = c.select("v_component_of_wind_10m").mean().rename("v_component_of_wind_10m")
        p = c.select("total_precipitation").sum().rename("total_precipitation")
        images.append(ee.Image.cat([t, td, u, v, p]).set("system:time_start", ee.Date(day.isoformat()).millis()))
        day = nxt
    return ee.ImageCollection.fromImages(images)


def derived_collection(kind: str, years: tuple[int, int]) -> ee.ImageCollection:
    start, end = years
    if kind in ("land", "lakes"):
        c = ee.ImageCollection(SOURCE_BY_KIND[kind]["collection"]).filterDate(f"{start}-01-01", f"{end + 1}-01-01")
        return c.map(lambda img: _derive_daily_image(img, kind))

    # Use the precomputed ERA5 daily asset for complete years through 2019.
    parts = []
    daily_end = min(end, 2019)
    if start <= daily_end:
        pre = ee.ImageCollection("ECMWF/ERA5/DAILY").filterDate(f"{start}-01-01", f"{daily_end + 1}-01-01")
        parts.append(pre.map(lambda img: _derive_daily_image(img, "marine")))
    # Complete 2020 from hourly ERA5 because the precomputed daily EE asset ends mid-2020.
    if start <= 2020 <= end:
        parts.append(_era5_hourly_to_daily_for_year(2020).map(lambda img: _derive_daily_image(img, "marine")))
    if not parts:
        return ee.ImageCollection([])
    merged = parts[0]
    for part in parts[1:]:
        merged = ee.ImageCollection(merged).merge(part)
    return ee.ImageCollection(merged).sort("system:time_start")


def mean_indicator(coll: ee.ImageCollection, band: str, lo: float, hi: float, name: str, inclusive_high: bool = False) -> ee.Image:
    def fn(img):
        x = ee.Image(img).select(band)
        hit = x.gte(lo).And(x.lte(hi) if inclusive_high else x.lt(hi))
        return hit.rename(name).float()
    return coll.map(fn).mean().multiply(100).rename(name)


def summary_image(kind: str, years: tuple[int, int]) -> ee.Image:
    coll = derived_collection(kind, years)
    start, end = years
    n_years = end - start + 1
    bands = []

    p_edges = bounded_edges(PRECIP_EDGES_MM)
    for i in range(len(p_edges) - 1):
        bands.append(mean_indicator(coll, "precip_mm", p_edges[i], p_edges[i + 1], f"pbin_{i:02d}", i == len(p_edges) - 2))
    h_edges = bounded_edges(HUMIDITY_EDGES_PCT)
    for i in range(len(h_edges) - 1):
        bands.append(mean_indicator(coll, "rh", h_edges[i], h_edges[i + 1], f"hbin_{i:02d}", i == len(h_edges) - 2))
    w_edges = bounded_edges(WIND_EDGES_MS)
    for i in range(len(w_edges) - 1):
        bands.append(mean_indicator(coll, "wind_ms", w_edges[i], w_edges[i + 1], f"wbin_{i:02d}", i == len(w_edges) - 2))

    # 16 compass sectors, N centered on 0°.
    for i, _sector in enumerate(WIND_SECTORS):
        center = i * 22.5
        lo = (center - 11.25) % 360
        hi = (center + 11.25) % 360
        name = f"rose_{i:02d}"
        def sector_fn(img, lo=lo, hi=hi, name=name):
            d = ee.Image(img).select("wind_dir")
            hit = d.gte(lo).And(d.lt(hi)) if lo < hi else d.gte(lo).Or(d.lt(hi))
            return hit.rename(name).float()
        bands.append(coll.map(sector_fn).mean().multiply(100).rename(name))

    for month in range(1, 13):
        cm = coll.filter(ee.Filter.calendarRange(month, month, "month"))
        bands.append(cm.select("precip_mm").sum().divide(n_years).rename(f"pmonth_{month:02d}"))
        bands.append(cm.select("rh").mean().rename(f"hmonth_{month:02d}"))
        bands.append(cm.select("wind_ms").mean().rename(f"wmonth_{month:02d}"))

    bands.extend([
        coll.select("precip_mm").sum().divide(n_years).rename("precip_annual_mm"),
        coll.select("rh").mean().rename("humidity_mean_pct"),
        coll.select("wind_ms").mean().rename("wind_mean_ms"),
        coll.map(lambda img: ee.Image(img).select("precip_mm").gte(1).float().rename("wet")).sum().divide(n_years).rename("wet_days_per_year"),
        coll.map(lambda img: ee.Image(img).select("precip_mm").gte(10).float().rename("heavy")).sum().divide(n_years).rename("heavy_days_per_year"),
        coll.map(lambda img: ee.Image(img).select("wind_ms").gte(10).float().rename("strong")).sum().divide(n_years).rename("strong_wind_days_per_year"),
    ])
    return ee.Image.cat(bands)


def region_weight(kind: str) -> ee.Image:
    if kind != "marine":
        return ee.Image.constant(1).rename("region_fraction")
    # ERA5-Land static is used only as a fractional land/lake mask; ERA5 supplies the marine meteorology.
    static = ee.Image("ECMWF/ERA5_LAND/STATIC")
    land = static.select("land_sea_mask")
    lake = static.select("lake_cover")
    return ee.Image.constant(1).subtract(land).subtract(lake).clamp(0, 1).rename("region_fraction")


def weighted_summary_image(kind: str, years: tuple[int, int]) -> ee.Image:
    summary = summary_image(kind, years)
    area = ee.Image.pixelArea().multiply(region_weight(kind)).rename("area_m2")
    weighted = summary.multiply(area)
    names = summary.bandNames()
    weighted_names = names.map(lambda n: ee.String(n).cat("__aw"))
    return weighted.rename(weighted_names).addBands(area)


def ee_geometry(geom, simplify_deg: float):
    g = geom.simplify(simplify_deg, preserve_topology=True) if simplify_deg > 0 else geom
    return ee.Geometry(mapping(g), proj="EPSG:4326", geodesic=False)


def reduce_chunk(image: ee.Image, rows: list[tuple[str, str, object]], kind: str, scale: float, tile_scale: int, simplify_deg: float, retries: int):
    features = []
    for rid, name, geom in rows:
        features.append(ee.Feature(ee_geometry(geom, simplify_deg), {"region_id": rid, "region_name": name}))
    fc = ee.FeatureCollection(features)
    reduced = image.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.sum(),
        scale=scale,
        tileScale=tile_scale,
    )
    last = None
    for attempt in range(retries):
        try:
            return reduced.getInfo().get("features", [])
        except Exception as exc:
            last = exc
            if attempt + 1 >= retries:
                break
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"Earth Engine reduction failed after {retries} attempts") from last


def extract_raw(props: dict) -> tuple[dict, float]:
    area = float(props.get("area_m2") or 0.0)
    if area <= 0:
        raise ValueError("region has zero weighted ERA5 area")
    def val(name):
        raw = props.get(name + "__aw")
        if raw is None:
            # Some Earth Engine reducer combinations suffix the reducer name.
            raw = props.get(name + "__aw_sum")
        return float(raw) / area if raw is not None else None
    raw = {
        "precip_hist_pct": [val(f"pbin_{i:02d}") or 0.0 for i in range(len(PRECIP_EDGES_MM) - 1)],
        "humidity_hist_pct": [val(f"hbin_{i:02d}") or 0.0 for i in range(len(HUMIDITY_EDGES_PCT) - 1)],
        "wind_hist_pct": [val(f"wbin_{i:02d}") or 0.0 for i in range(len(WIND_EDGES_MS) - 1)],
        "wind_rose_pct": [val(f"rose_{i:02d}") or 0.0 for i in range(16)],
        "precip_monthly_mm": [val(f"pmonth_{m:02d}") for m in range(1, 13)],
        "humidity_monthly_pct": [val(f"hmonth_{m:02d}") for m in range(1, 13)],
        "wind_monthly_ms": [val(f"wmonth_{m:02d}") for m in range(1, 13)],
        "precip_annual_mm": val("precip_annual_mm"),
        "humidity_mean_pct": val("humidity_mean_pct"),
        "wind_mean_ms": val("wind_mean_ms"),
        "wet_days_per_year": val("wet_days_per_year"),
        "heavy_days_per_year": val("heavy_days_per_year"),
        "strong_wind_days_per_year": val("strong_wind_days_per_year"),
    }
    return raw, area / 1_000_000.0


def output_path(root: Path, kind: str, rid: str) -> Path:
    return root / kind / f"{rid}.climate.json"


def load_index(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "generatedAt": None, "baseline": None, "regions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("schemaVersion", 1)
    data.setdefault("regions", {})
    return data


def main():
    ap = argparse.ArgumentParser(description="Build MotherWorld precipitation, humidity and wind climate normals with Earth Engine.")
    ap.add_argument("--repo", type=Path, default=Path.cwd())
    ap.add_argument("--project", help="Google Cloud project used for Earth Engine")
    ap.add_argument("--years", default="1991:2020")
    ap.add_argument("--kinds", default="land,lakes,marine", help="Comma-separated land,lakes,marine")
    ap.add_argument("--region", action="append", default=[], help="Build only a region id; repeatable")
    ap.add_argument("--chunk-size", type=int, default=12)
    ap.add_argument("--tile-scale", type=int, default=4)
    ap.add_argument("--simplify-deg", type=float, default=0.015, help="Topology-preserving geometry simplification before upload to EE")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--retries", type=int, default=4)
    args = ap.parse_args()

    repo = args.repo.resolve()
    years = parse_year_range(args.years)
    if years[0] < 1979 or years[1] > 2020:
        raise SystemExit("v1 marine provider uses ECMWF/ERA5/DAILY, so the common baseline must stay within 1979–2020. Recommended: 1991:2020.")
    kinds = [x.strip() for x in args.kinds.split(",") if x.strip()]
    invalid = [x for x in kinds if x not in SOURCE_BY_KIND]
    if invalid:
        raise SystemExit(f"Unknown kinds: {invalid}")

    ee.Initialize(project=args.project) if args.project else ee.Initialize()
    out_root = repo / "frontend/public/data/climate-extras"
    index_path = out_root / "climate-extras.index.json"
    index = load_index(index_path)
    index["baseline"] = {"startYear": years[0], "endYear": years[1]}
    selected = set(args.region)

    for kind in kinds:
        geoms = load_geometries(repo, kind)
        names = load_region_names(repo, kind)
        rows = [(rid, names.get(rid, rid), geom) for rid, geom in sorted(geoms.items()) if not selected or rid in selected]
        if kind == "marine" and (not selected or "open_ocean" in selected):
            # Non-species metrics for the open-ocean selection intentionally represent the entire ocean system.
            from shapely.geometry import box
            rows.append(("open_ocean", "Global Ocean", box(-180, -90, 180, 90)))
        pending = []
        for row in rows:
            rid, _name, geometry = row
            geometry_metadata = analysis_geometry_metadata(rid, geometry)
            geometry_identity = (analysis_geometry_cache_identity(rid, geometry)
                                 if geometry_metadata.get("overrideApplied") else None)
            dst = output_path(out_root, kind, rid)
            if not args.force and dst.exists():
                try:
                    cached_payload = json.loads(dst.read_text(encoding="utf-8"))
                    cached_identity = cached_payload.get("analysisGeometryCacheIdentity")
                    identity_matches = (not geometry_identity or cached_identity == geometry_identity)
                    if identity_matches:
                        if rid not in index["regions"]:
                            index["regions"][rid] = {
                                "url": f"{kind}/{rid}.climate.json",
                                "kind": kind,
                                "source": cached_payload.get("source", {}).get("id"),
                                "baseline": cached_payload.get("baseline"),
                                "generatedAt": cached_payload.get("generatedAt"),
                            }
                        continue
                except Exception:
                    pass
            pending.append(row)
        rows = pending
        if not rows:
            index["generatedAt"] = utc_now()
            atomic_json(index_path, index)
            continue
        image = weighted_summary_image(kind, years)
        scale = 11_132 if kind in ("land", "lakes") else 27_830
        for start in range(0, len(rows), max(1, args.chunk_size)):
            chunk = rows[start:start + max(1, args.chunk_size)]
            print(f"[{kind}] regions {start + 1}-{start + len(chunk)} of {len(rows)}")
            features = reduce_chunk(image, chunk, kind, scale, args.tile_scale, args.simplify_deg, args.retries)
            by_id = {f.get("properties", {}).get("region_id"): f.get("properties", {}) for f in features}
            for rid, name, _geom in chunk:
                props = by_id.get(rid)
                if not props:
                    print(f"WARNING: no Earth Engine result for {rid}")
                    continue
                raw, area_km2 = extract_raw(props)
                now = utc_now()
                source = dict(SOURCE_BY_KIND[kind])
                source["aggregationAsset"] = source.pop("collection")
                if kind == "marine":
                    source["mask"] = "ERA5-Land static land_sea_mask + lake_cover; open_ocean selection is whole-ocean aggregate"
                geometry_metadata = analysis_geometry_metadata(rid, _geom)
                geometry_identity = (analysis_geometry_cache_identity(rid, _geom)
                                     if geometry_metadata.get("overrideApplied") else None)
                payload = payload_from_raw(
                    region_id=rid, region_name=name, kind=kind, years=years, source=source,
                    raw=raw, area_km2=area_km2, generated_at=now,
                    analysis_geometry=geometry_metadata,
                    analysis_geometry_cache_identity=geometry_identity,
                )
                dst = output_path(out_root, kind, rid)
                atomic_json(dst, payload)
                index["regions"][rid] = {
                    "url": f"{kind}/{rid}.climate.json",
                    "kind": kind,
                    "source": source["id"],
                    "baseline": payload["baseline"],
                    "generatedAt": now,
                }
            index["generatedAt"] = utc_now()
            atomic_json(index_path, index)

    print(f"Wrote climate extras index: {index_path}")


if __name__ == "__main__":
    main()
