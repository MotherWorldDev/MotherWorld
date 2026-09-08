#!/usr/bin/env python3
"""Build the global ERA5-Land root-zone soil-moisture stability backbone locally."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr


VARIABLES = [
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
]
HISTORY_START_YEAR = 1993


def root_file(directory: Path, year: int) -> Path:
    return directory / f"era5_land_monthly_soil_moisture_{year}.nc"


def coord_name(dataset: xr.Dataset, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in dataset.coords or name in dataset.variables:
            return name
    raise KeyError(f"missing coordinate; tried {candidates}")


def root_zone(path: Path, month: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with xr.open_dataset(path, engine="netcdf4", mask_and_scale=True) as dataset:
        lat_name = coord_name(dataset, ("latitude", "lat"))
        lon_name = coord_name(dataset, ("longitude", "lon"))
        lat = np.asarray(dataset[lat_name].values, dtype=np.float64)
        lon = np.asarray(dataset[lon_name].values, dtype=np.float64)
        values = []
        for name in VARIABLES:
            field = dataset[name]
            extra = [dim for dim in field.dims if dim not in ("time", lat_name, lon_name)]
            if extra:
                field = field.mean(dim=extra, skipna=True)
            if "time" in field.dims:
                field = field.isel(time=month)
            values.append(np.asarray(field.values, dtype=np.float32))
        result = values[0] * 0.07 + values[1] * 0.21 + values[2] * 0.72
    return result, lat, lon


def spatial_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat_grid = lat[:, None]
    lon_grid = lon[None, :]
    mask = np.isfinite(lat_grid) & np.isfinite(lon_grid) & (lat_grid >= -60.0)
    greenland = (lat_grid >= 58.0) & (lat_grid <= 85.0) & (lon_grid >= -75.0) & (lon_grid <= -10.0)
    return mask & ~greenland


def compute_envelopes(directory: Path, baseline_years: list[int], cache_path: Path) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray, np.ndarray]:
    if cache_path.exists():
        data = np.load(cache_path, allow_pickle=False)
        p10 = [data[f"p10_{month:02d}"] for month in range(1, 13)]
        p90 = [data[f"p90_{month:02d}"] for month in range(1, 13)]
        return p10, p90, data["lat"], data["lon"]
    p10 = []
    p90 = []
    lat = lon = None
    for month in range(12):
        stack = []
        for year in baseline_years:
            values, lat, lon = root_zone(root_file(directory, year), month)
            stack.append(values)
        array = np.stack(stack, axis=0)
        p10.append(np.nanpercentile(array, 10.0, axis=0).astype(np.float32))
        p90.append(np.nanpercentile(array, 90.0, axis=0).astype(np.float32))
        del array, stack
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".part")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, lat=lat, lon=lon, **{f"p10_{month + 1:02d}": p10[month] for month in range(12)}, **{f"p90_{month + 1:02d}": p90[month] for month in range(12)})
    os.replace(temporary, cache_path)
    return p10, p90, lat, lon


def build(args) -> dict:
    directory = args.input_dir.resolve()
    baseline_years = list(range(args.baseline_start, args.baseline_end + 1))
    missing = [year for year in baseline_years if not root_file(directory, year).exists()]
    if missing:
        raise FileNotFoundError(f"missing baseline years: {missing}")
    cache = args.envelope_cache or directory / "era5_land_soil_moisture_p10_p90_1991_2020.npz"
    p10, p90, lat, lon = compute_envelopes(directory, baseline_years, cache)
    mask = spatial_mask(lat, lon)
    weights = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, len(lon)), dtype=np.float64)
    weights[~mask] = 0.0
    series = []
    for year in range(args.start_year, args.end_year + 1):
        source = root_file(directory, year)
        if not source.exists():
            continue
        annual_valid = 0.0
        annual_normal = 0.0
        valid_months = 0
        for month in range(12):
            values, current_lat, current_lon = root_zone(source, month)
            if not (np.array_equal(current_lat, lat) and np.array_equal(current_lon, lon)):
                raise ValueError(f"grid changed in {source.name}")
            valid = np.isfinite(values) & np.isfinite(p10[month]) & np.isfinite(p90[month]) & (weights > 0)
            if not np.any(valid):
                continue
            valid_weight = float(weights[valid].sum())
            normal = valid & (values >= p10[month]) & (values <= p90[month])
            normal_weight = float(weights[normal].sum())
            annual_valid += valid_weight
            annual_normal += normal_weight
            valid_months += 1
        if annual_valid > 0:
            fraction = annual_normal / annual_valid
            series.append({"year": year, "score": round(min(100.0, fraction / 0.80 * 100.0), 4), "coverage": round(valid_months / 12.0, 6), "raw": round(fraction * 100.0, 4), "unit": "% land-area-months inside fixed historical P10–P90 envelope"})
    if not series:
        raise RuntimeError("no complete soil-moisture years were available")
    latest = series[-1]
    return {
        "schemaVersion": 2,
        "familyId": "freshwater",
        "label": "Freshwater stability",
        "scope": "global_only",
        "earthHealthRole": "backbone",
        "score": latest["score"],
        "coverage": latest["coverage"],
        "latestBackboneYear": latest["year"],
        "components": [{"id": "root_zone_soil_moisture_stability", "label": "Root-zone soil-moisture stability", "score": latest["score"], "weight": 1.0, "raw": latest["raw"], "unit": latest["unit"], "source": "Copernicus CDS ERA5-Land monthly means"}],
        "series": series,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "method": {"backbone": "depth-weighted ERA5-Land volumetric soil water layers 1–3", "layerWeights": {"0–7 cm": 0.07, "7–28 cm": 0.21, "28–100 cm": 0.72}, "baseline": f"{args.baseline_start}–{args.baseline_end} calendar-month per-grid-cell P10–P90 envelope", "normalization": "80% of valid ice-sheet-excluded land-area-months inside the local envelope = 100", "yearDefinition": f"Calendar years {args.start_year}–{args.end_year}; missing source years are omitted and monthly coverage is explicit", "scope": "global ice-sheet-excluded land; Antarctica below 60°S and a broad Greenland box excluded", "scoreDirection": "100 = root-zone soil moisture remains within historical local regimes", "diagnosticPolicy": "GRACE terrestrial water storage and JRC surface-water retention remain diagnostics only"},
        "sources": [{"id": "reanalysis-era5-land-monthly-means", "label": "Copernicus CDS ERA5-Land monthly averaged data", "variables": VARIABLES, "area": [90.0, -180.0, -60.0, 180.0], "retrievalDirectory": str(directory)}],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=HISTORY_START_YEAR)
    parser.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year - 1)
    parser.add_argument("--baseline-start", type=int, default=1991)
    parser.add_argument("--baseline-end", type=int, default=2020)
    parser.add_argument("--envelope-cache", type=Path)
    args = parser.parse_args()
    payload = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(f"Freshwater backbone {payload['latestBackboneYear']} {payload['score']} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
