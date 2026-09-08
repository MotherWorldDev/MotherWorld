#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import xarray as xr

from freshwater_common import linear_trend_per_decade, stability_score_from_abs_z, utc_now_iso, write_json


def parse_years(value: str) -> tuple[int, int]:
    a, b = value.replace("-", ":").split(":", 1)
    return int(a), int(b)


def coord_name(ds: xr.Dataset, candidates):
    for name in candidates:
        if name in ds.coords or name in ds.dims:
            return name
    raise KeyError(f"Missing coordinate from {candidates}; available={list(ds.coords)}")


def month_numbers(time_coord) -> np.ndarray:
    return np.asarray(time_coord.dt.month.values, dtype=int)


def year_numbers(time_coord) -> np.ndarray:
    return np.asarray(time_coord.dt.year.values, dtype=int)


def main() -> None:
    p = argparse.ArgumentParser(description="Build global Freshwater Stability storage component from JPL GRACE/GRACE-FO Mascon NetCDF.")
    p.add_argument("--input", required=True, type=Path, help="TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4 NetCDF")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--baseline", default="2004:2014")
    p.add_argument("--recent-months", type=int, default=60)
    p.add_argument("--include-land-ice", action="store_true", help="Do not apply the default Antarctica/Greenland exclusion used to avoid cryosphere double counting.")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    repo = args.repo.resolve()
    output = args.output or repo / "index_raw/freshwater/grace_storage.json"
    b0, b1 = parse_years(args.baseline)

    ds = xr.open_dataset(args.input)
    try:
        lat_name = coord_name(ds, ("lat", "latitude"))
        lon_name = coord_name(ds, ("lon", "longitude"))
        time_name = coord_name(ds, ("time", "date", "valid_time"))
        if "lwe_thickness" not in ds:
            raise KeyError("GRACE NetCDF must contain lwe_thickness")
        da = ds["lwe_thickness"]
        if time_name not in da.dims:
            raise KeyError(f"lwe_thickness has no {time_name} dimension")
        da = da.transpose(time_name, lat_name, lon_name)
        lat = np.asarray(ds[lat_name].values, dtype=float)
        lon = np.asarray(ds[lon_name].values, dtype=float)
        values = np.asarray(da.values, dtype=float)

        if "land_mask" in ds:
            land_mask = np.asarray(ds["land_mask"].squeeze().values, dtype=float) > 0
        else:
            land_mask = np.isfinite(values).any(axis=0)
        if land_mask.shape != values.shape[1:]:
            land_mask = np.broadcast_to(land_mask, values.shape[1:]).copy()

        # Prevent freshwater storage from double-counting the dominant land-ice signal.
        # Antarctica is excluded south of 60S. Greenland is approximated using a generous
        # bounding box; the removed non-Greenland land fraction is tiny at the global scale.
        if not args.include_land_ice:
            lat2 = np.broadcast_to(lat[:, None], land_mask.shape)
            lon2 = np.broadcast_to(lon[None, :], land_mask.shape)
            antarctica = lat2 < -60.0
            greenland_box = (lat2 >= 58.0) & (lat2 <= 85.0) & (lon2 >= -75.0) & (lon2 <= -10.0)
            land_mask = land_mask & ~antarctica & ~greenland_box

        # Latitude-area weighting is sufficient on this regular 0.5° grid. CRI's land mask
        # handles coastal separation; invalid cells are excluded month-by-month.
        row_weight = np.cos(np.deg2rad(lat))
        weights = np.broadcast_to(row_weight[:, None], land_mask.shape) * land_mask
        global_monthly = []
        valid_area_fraction = []
        total_weight = float(weights.sum())
        for t in range(values.shape[0]):
            v = values[t]
            valid = np.isfinite(v) & land_mask
            w = np.where(valid, weights, 0.0)
            denom = float(w.sum())
            global_monthly.append(float(np.nansum(np.where(valid, v, 0.0) * w) / denom) if denom > 0 else np.nan)
            valid_area_fraction.append(denom / total_weight if total_weight > 0 else 0.0)

        years = year_numbers(ds[time_name])
        months = month_numbers(ds[time_name])
        vals = np.asarray(global_monthly, dtype=float)
        baseline_mask = (years >= b0) & (years <= b1) & np.isfinite(vals)
        if baseline_mask.sum() < 48:
            raise RuntimeError(f"Baseline {b0}-{b1} has only {baseline_mask.sum()} valid monthly values")

        climatology = {}
        for m in range(1, 13):
            x = vals[baseline_mask & (months == m)]
            if len(x) >= 3:
                climatology[m] = (float(np.mean(x)), float(np.std(x, ddof=1)))

        valid_idx = np.flatnonzero(np.isfinite(vals))
        recent_idx = valid_idx[-max(12, int(args.recent_months)):]
        recent_z = []
        for i in recent_idx:
            pair = climatology.get(int(months[i]))
            if not pair or pair[1] <= 0:
                continue
            recent_z.append((vals[i] - pair[0]) / pair[1])
        mean_abs_z = float(np.mean(np.abs(recent_z))) if recent_z else None
        score = stability_score_from_abs_z(mean_abs_z)

        annual_series = []
        for y in sorted(set(int(x) for x in years[np.isfinite(vals)])):
            x = vals[(years == y) & np.isfinite(vals)]
            if len(x) >= 6:
                annual_series.append({"year": y, "value": float(np.mean(x))})
        trend = linear_trend_per_decade(annual_series)
        latest = float(np.mean(vals[recent_idx])) if len(recent_idx) else None
        recent_start = int(years[recent_idx[0]]) if len(recent_idx) else None
        recent_end = int(years[recent_idx[-1]]) if len(recent_idx) else None
        temporal_coverage = len(recent_z) / max(1, len(recent_idx))
        spatial_coverage = float(np.mean(np.asarray(valid_area_fraction)[recent_idx])) if len(recent_idx) else 0.0
        coverage = max(0.0, min(1.0, temporal_coverage * spatial_coverage))

        payload = {
            "schemaVersion": 1,
            "providerId": "grace_tws",
            "label": "Terrestrial water storage stability",
            "score": round(score, 4) if score is not None else None,
            "coverage": round(coverage, 6),
            "raw": round(mean_abs_z, 4) if mean_abs_z is not None else None,
            "unit": "mean |z|",
            "context": {
                "recentMeanEquivalentWaterThicknessCm": round(latest, 5) if latest is not None else None,
                "trendCmPerDecade": round(trend, 6) if trend is not None else None,
                "recentStartYear": recent_start,
                "recentEndYear": recent_end,
                "baseline": f"{b0}-{b1}",
                "recentMonths": len(recent_idx),
                "iceSheetExclusion": "Antarctica south of 60S + Greenland bounding mask" if not args.include_land_ice else "disabled",
            },
            "series": annual_series,
            "source": {
                "id": "TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4",
                "label": "JPL GRACE/GRACE-FO Mascon CRI Filtered RL06.3Mv04",
                "doi": "10.5067/TEMSC-3JC634",
                "variable": "lwe_thickness",
                "nativeUnit": "cm equivalent water thickness",
            },
            "method": {
                "scope": "global ice-sheet-excluded land",
                "normalization": "calendar-month standardized anomaly; 100 at mean |z| <= 0.5, linearly decreasing to 0 at |z| >= 3",
                "scoreDirection": "100 = recent global terrestrial water storage remains inside its historical monthly envelope",
            },
            "generatedAt": utc_now_iso(),
        }
        write_json(output, payload)
        print(f"GRACE storage stability: {payload['score']} coverage={coverage:.1%} -> {output}")
    finally:
        ds.close()


if __name__ == "__main__":
    main()
