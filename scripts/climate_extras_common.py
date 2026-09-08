from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from analysis_geometry import apply_land_geometry_overrides

PRECIP_EDGES_MM = [0.0, 0.1, 1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, float("inf")]
HUMIDITY_EDGES_PCT = [float(x) for x in range(0, 101, 10)]
WIND_EDGES_MS = [0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 17.0, 25.0, 40.0, float("inf")]
WIND_SECTORS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def parse_year_range(text: str) -> tuple[int, int]:
    value = str(text).strip()
    if ":" in value:
        a, b = value.split(":", 1)
    elif "-" in value:
        a, b = value.split("-", 1)
    else:
        a = b = value
    start, end = int(a), int(b)
    if start < 1900 or end < start:
        raise ValueError(f"Invalid year range: {text}")
    return start, end


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            f.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _parts(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    out = []
    for g in getattr(geom, "geoms", []):
        out.extend(_parts(g))
    return out


def _region_id(row, idx=None) -> str | None:
    for key in ("regionId", "region_id", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    eco = row.get("ECO_ID") or row.get("eco_id") or row.get("ecoId")
    if eco is not None and str(eco).strip():
        return f"eco_{eco}"
    if idx is not None:
        s = str(idx)
        if s.startswith(("eco_", "marine_", "lake_")):
            return s
    return None


def load_geometries(repo: Path, kind: str) -> dict[str, object]:
    paths = {
        "land": [repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"],
        "lakes": [repo / "frontend/public/data/lakes/lod0/lakes_lod0.topojson"],
        "marine": [repo / "frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson"],
    }
    if kind not in paths:
        raise ValueError(f"Unsupported kind: {kind}")
    bucket: dict[str, list[Polygon]] = {}
    for path in paths[kind]:
        if not path.exists():
            raise FileNotFoundError(path)
        gdf = gpd.read_file(path)
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        else:
            gdf = gdf.to_crs("EPSG:4326")
        for idx, row in gdf.iterrows():
            rid = _region_id(row, idx)
            if not rid or row.geometry is None or row.geometry.is_empty:
                continue
            geom = row.geometry if row.geometry.is_valid else make_valid(row.geometry)
            bucket.setdefault(rid, []).extend(_parts(geom))
    geometries = {rid: unary_union(parts) for rid, parts in bucket.items() if parts}
    return apply_land_geometry_overrides(geometries) if kind == "land" else geometries


def load_region_names(repo: Path, kind: str) -> dict[str, str]:
    index_paths = {
        "land": repo / "frontend/public/data/regions.index.json",
        "lakes": repo / "frontend/public/data/lakes.index.json",
        "marine": repo / "frontend/public/data/marine.index.json",
    }
    path = index_paths[kind]
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    regions = data.get("regions") or {}
    out = {}
    for rid, meta in regions.items():
        if not isinstance(meta, dict):
            continue
        name = meta.get("name") or meta.get("ecoName") or meta.get("ECO_NAME") or meta.get("label")
        if name:
            out[str(rid)] = str(name)
    return out


def finite_edges_for_json(edges: list[float]) -> list[float | None]:
    return [None if not math.isfinite(float(x)) else float(x) for x in edges]


def normalize_percentages(values: Iterable[float]) -> list[float]:
    vals = [max(0.0, float(x or 0.0)) for x in values]
    total = sum(vals)
    if total <= 0:
        return [0.0 for _ in vals]
    return [x * 100.0 / total for x in vals]


def percentile_from_hist(percent: Iterable[float], edges: list[float], q: float) -> float | None:
    vals = normalize_percentages(percent)
    if not vals or not 0 <= q <= 1:
        return None
    target = q * 100.0
    running = 0.0
    for i, p in enumerate(vals):
        previous = running
        running += p
        if running + 1e-12 < target:
            continue
        lo = float(edges[i])
        hi = float(edges[i + 1])
        if not math.isfinite(hi):
            return lo
        if p <= 0:
            return (lo + hi) / 2.0
        frac = max(0.0, min(1.0, (target - previous) / p))
        return lo + frac * (hi - lo)
    hi = float(edges[-1])
    return None if not math.isfinite(hi) else hi


def dominant_sector(percent: Iterable[float]) -> str | None:
    vals = list(percent)
    if len(vals) != 16 or max(vals, default=0) <= 0:
        return None
    return WIND_SECTORS[max(range(16), key=lambda i: vals[i])]


def package_stats(raw: dict) -> dict:
    p_hist = normalize_percentages(raw.get("precip_hist_pct", []))
    h_hist = normalize_percentages(raw.get("humidity_hist_pct", []))
    w_hist = normalize_percentages(raw.get("wind_hist_pct", []))
    rose = normalize_percentages(raw.get("wind_rose_pct", []))
    return {
        "precipitation": {
            "annualMeanMm": raw.get("precip_annual_mm"),
            "wetAreaDaysPerYear": raw.get("wet_days_per_year"),
            "heavyRainAreaDaysPerYear": raw.get("heavy_days_per_year"),
            "p95DailyMmApprox": percentile_from_hist(p_hist, PRECIP_EDGES_MM, 0.95),
            "p99DailyMmApprox": percentile_from_hist(p_hist, PRECIP_EDGES_MM, 0.99),
        },
        "humidity": {
            "meanPct": raw.get("humidity_mean_pct"),
            "medianPctApprox": percentile_from_hist(h_hist, HUMIDITY_EDGES_PCT, 0.50),
            "p10PctApprox": percentile_from_hist(h_hist, HUMIDITY_EDGES_PCT, 0.10),
            "p90PctApprox": percentile_from_hist(h_hist, HUMIDITY_EDGES_PCT, 0.90),
        },
        "wind": {
            "meanMs": raw.get("wind_mean_ms"),
            "medianMsApprox": percentile_from_hist(w_hist, WIND_EDGES_MS, 0.50),
            "p90MsApprox": percentile_from_hist(w_hist, WIND_EDGES_MS, 0.90),
            "p99MsApprox": percentile_from_hist(w_hist, WIND_EDGES_MS, 0.99),
            "calmPct": w_hist[0] if w_hist else None,
            "strongWindAreaDaysPerYear": raw.get("strong_wind_days_per_year"),
            "prevailingDirection": dominant_sector(rose),
        },
    }


def payload_from_raw(*, region_id: str, region_name: str, kind: str, years: tuple[int, int], source: dict, raw: dict, area_km2: float | None, generated_at: str, analysis_geometry: dict | None = None, analysis_geometry_cache_identity: str | None = None) -> dict:
    precip_hist = normalize_percentages(raw.get("precip_hist_pct", []))
    humidity_hist = normalize_percentages(raw.get("humidity_hist_pct", []))
    wind_hist = normalize_percentages(raw.get("wind_hist_pct", []))
    wind_rose = normalize_percentages(raw.get("wind_rose_pct", []))
    payload = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "regionId": region_id,
        "regionName": region_name,
        "kind": kind,
        "baseline": {"startYear": years[0], "endYear": years[1]},
        "source": source,
        "aggregation": {
            "distributionWeighting": "physical grid-cell area × days",
            "monthlyWeighting": "physical grid-cell area",
            "regionAreaKm2Approx": area_km2,
        },
        "precipitation": {
            "unit": "mm/day",
            "distribution": {"edgesMm": finite_edges_for_json(PRECIP_EDGES_MM), "percent": precip_hist},
            "monthlyClimatologyMm": raw.get("precip_monthly_mm", []),
            "stats": package_stats(raw)["precipitation"],
            "notes": [
                "Daily precipitation includes rain plus snow water equivalent.",
                "Tiny negative packed/reanalysis precipitation values are clamped to zero before aggregation.",
            ],
        },
        "humidity": {
            "unit": "% RH",
            "distribution": {"edgesPct": finite_edges_for_json(HUMIDITY_EDGES_PCT), "percent": humidity_hist},
            "monthlyClimatologyPct": raw.get("humidity_monthly_pct", []),
            "stats": package_stats(raw)["humidity"],
            "notes": [
                "Relative humidity is derived from daily-mean 2 m temperature and daily-mean 2 m dew point.",
                "Because RH is nonlinear, this is a daily-mean-state estimate, not the exact mean of hourly relative humidity.",
            ],
        },
        "wind": {
            "unit": "m/s",
            "distribution": {"edgesMs": finite_edges_for_json(WIND_EDGES_MS), "percent": wind_hist},
            "monthlyClimatologyMs": raw.get("wind_monthly_ms", []),
            "rose": {"sectors": WIND_SECTORS, "percent": wind_rose},
            "stats": package_stats(raw)["wind"],
            "notes": [
                "Wind speed and direction use daily-mean 10 m u/v components; this is not a gust climatology.",
                "Wind-rose percentages are area × day weighted.",
            ],
        },
    }

    if analysis_geometry and analysis_geometry.get("overrideApplied"):
        payload["analysisGeometry"] = analysis_geometry
        if analysis_geometry_cache_identity:
            payload["analysisGeometryCacheIdentity"] = analysis_geometry_cache_identity
    return payload
