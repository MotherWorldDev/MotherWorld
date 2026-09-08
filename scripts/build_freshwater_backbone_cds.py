#!/usr/bin/env python3
"""Build the global ERA5-Land root-zone soil-moisture stability backbone locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr


VARIABLES = [
    "volumetric_soil_water_layer_1",
    "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3",
]
VARIABLE_ALIASES = {
    "volumetric_soil_water_layer_1": ("volumetric_soil_water_layer_1", "swvl1", "swvl_1"),
    "volumetric_soil_water_layer_2": ("volumetric_soil_water_layer_2", "swvl2", "swvl_2"),
    "volumetric_soil_water_layer_3": ("volumetric_soil_water_layer_3", "swvl3", "swvl_3"),
}
TIME_COORDINATES = ("valid_time", "time", "date")
VERSION_DIMENSIONS = ("expver",)
ICE_EXCLUSION = {
    "southernLatitude": -60.0,
    "greenlandBox": [-75.0, 58.0, -10.0, 85.0],
    "description": "Matches the package geometry: all land south of 60°S and the broad rectangle lon -75..-10, lat 58..85 are excluded. This intentionally also removes Iceland, Baffin and nearby Canadian land; it is not a pixel-level glacier mask.",
}
HISTORY_START_YEAR = 1993
DEFAULT_AREA = [90.0, -180.0, -60.0, 180.0]


def root_file(directory: Path, year: int) -> Path:
    return directory / f"era5_land_monthly_soil_moisture_{year}.nc"


def coord_name(dataset: xr.Dataset, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in dataset.coords or name in dataset.variables:
            return name
    raise KeyError(f"missing coordinate; tried {candidates}")


def time_name(dataset: xr.Dataset) -> str:
    for name in TIME_COORDINATES:
        if name not in dataset.variables:
            continue
        if not any(name in dataset[variable].dims for variable in dataset.data_vars):
            continue
        values = dataset[name]
        if values.ndim == 1:
            try:
                pd.to_datetime(values.values)
                return name
            except Exception:
                continue
    raise KeyError(f"missing decoded monthly coordinate; tried {TIME_COORDINATES}")


def variable_name(dataset: xr.Dataset, requested: str) -> str:
    for name in VARIABLE_ALIASES[requested]:
        if name in dataset.data_vars or name in dataset.variables:
            return name
    raise KeyError(f"missing ERA5-Land variable {requested}; available={list(dataset.data_vars)}")


def same_grid(left: np.ndarray, right: np.ndarray) -> bool:
    return left.shape == right.shape and np.array_equal(left, right)


def monthly_field(dataset: xr.Dataset, requested: str, month: int, temporal_name: str, lat_name: str, lon_name: str) -> np.ndarray:
    name = variable_name(dataset, requested)
    field = dataset[name]
    if temporal_name not in field.dims:
        raise ValueError(f"{name} has no {temporal_name} dimension")
    months = np.asarray(pd.to_datetime(dataset[temporal_name].values).month, dtype=int)
    matches = np.flatnonzero(months == month + 1)
    if len(matches) != 1:
        raise ValueError(f"{dataset.encoding.get('source', 'NetCDF')}: expected exactly one record for month {month + 1}, found {len(matches)}")
    field = field.isel({temporal_name: int(matches[0])})
    unknown = [dim for dim in field.dims if dim not in (lat_name, lon_name)]
    for dim in unknown:
        if dim not in VERSION_DIMENSIONS:
            raise ValueError(f"{name} contains unsupported extra dimension {dim}; refusing to average it")
        if field.sizes[dim] == 1:
            field = field.isel({dim: 0})
        else:
            # ERA5 can expose two product versions in expver.  Merge them
            # explicitly by taking the non-NaN maximum; no unknown dimension
            # is averaged or silently collapsed.
            field = field.max(dim=dim, skipna=True)
    field = field.transpose(lat_name, lon_name)
    values = np.asarray(field.values, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"{name} month {month + 1} is not a 2-D lat/lon field: {values.shape}")
    return values


def root_zone(path: Path, month: int, expected_year: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with xr.open_dataset(path, engine="netcdf4", mask_and_scale=True) as dataset:
        lat_name = coord_name(dataset, ("latitude", "lat"))
        lon_name = coord_name(dataset, ("longitude", "lon"))
        temporal_name = time_name(dataset)
        lat = np.asarray(dataset[lat_name].values, dtype=np.float64)
        lon = np.asarray(dataset[lon_name].values, dtype=np.float64)
        if lat.ndim != 1 or lon.ndim != 1:
            raise ValueError(f"only regular 1-D lat/lon grids are supported: {lat.shape}, {lon.shape}")
        decoded_years = np.asarray(pd.to_datetime(dataset[temporal_name].values).year, dtype=int)
        if expected_year is not None and set(decoded_years.tolist()) != {expected_year}:
            raise ValueError(f"{path.name}: decoded years {sorted(set(decoded_years.tolist()))} do not match filename/request year {expected_year}")
        values = [monthly_field(dataset, name, month, temporal_name, lat_name, lon_name) for name in VARIABLES]
        if any(value.shape != (len(lat), len(lon)) for value in values):
            raise ValueError(f"soil-water fields do not match grid in {path.name}")
        result = values[0] * 0.07 + values[1] * 0.21 + values[2] * 0.72
    return result, lat, lon


def spatial_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat_grid = lat[:, None]
    # CDS ERA5 grids commonly use 0–360°E; the package geometry is expressed
    # in -180..180° longitude.  The data order is preserved, so each mask
    # column still corresponds to the same source grid column.
    normalized_lon = ((lon + 180.0) % 360.0) - 180.0
    lon_grid = normalized_lon[None, :]
    mask = np.isfinite(lat_grid) & np.isfinite(lon_grid) & (lat_grid >= -60.0)
    greenland = (lat_grid >= 58.0) & (lat_grid <= 85.0) & (lon_grid >= -75.0) & (lon_grid <= -10.0)
    return mask & ~greenland


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprint(directory: Path, years: list[int], area: list[float]) -> str:
    manifest = []
    for year in years:
        path = root_file(directory, year)
        manifest.append({"year": year, "name": path.name, "bytes": path.stat().st_size, "sha256": file_sha256(path)})
    payload = {"inputDir": str(directory), "area": area, "variables": VARIABLES, "files": manifest}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def compute_envelopes(directory: Path, baseline_years: list[int], cache_path: Path, source_id: str, area: list[float]) -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray, np.ndarray]:
    if cache_path.exists():
        try:
            with np.load(cache_path, allow_pickle=False) as data:
                required = {"lat", "lon", "baseline_start", "baseline_end", "variables", "source_id", "input_dir", "area"}
                required.update({f"p10_{month:02d}" for month in range(1, 13)})
                required.update({f"p90_{month:02d}" for month in range(1, 13)})
                cached_variables = tuple(str(value) for value in data["variables"].tolist())
                cached_area = [float(value) for value in data["area"].tolist()]
                if not required.issubset(set(data.files)) or int(data["baseline_start"]) != baseline_years[0] or int(data["baseline_end"]) != baseline_years[-1] or cached_variables != tuple(VARIABLES) or str(data["source_id"].item()) != source_id or str(data["input_dir"].item()) != str(directory) or cached_area != [float(value) for value in area]:
                    raise ValueError("envelope cache metadata does not match this request")
                lat = np.asarray(data["lat"], dtype=np.float64)
                lon = np.asarray(data["lon"], dtype=np.float64)
                p10 = [np.asarray(data[f"p10_{month:02d}"], dtype=np.float32) for month in range(1, 13)]
                p90 = [np.asarray(data[f"p90_{month:02d}"], dtype=np.float32) for month in range(1, 13)]
            if any(item.shape != (len(lat), len(lon)) for item in p10 + p90):
                raise ValueError("envelope cache grid shape is invalid")
            return p10, p90, lat, lon
        except Exception:
            # A stale or interrupted cache is rebuilt atomically below.
            pass
    p10 = []
    p90 = []
    lat = lon = None
    for month in range(12):
        stack = []
        for year in baseline_years:
            values, current_lat, current_lon = root_zone(root_file(directory, year), month, expected_year=year)
            if lat is None:
                lat, lon = current_lat, current_lon
            elif not (same_grid(lat, current_lat) and same_grid(lon, current_lon)):
                raise ValueError(f"baseline grid changed in {year} month {month + 1}")
            stack.append(values)
        array = np.stack(stack, axis=0)
        p10.append(np.nanpercentile(array, 10.0, axis=0).astype(np.float32))
        p90.append(np.nanpercentile(array, 90.0, axis=0).astype(np.float32))
        del array, stack
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".part")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, lat=lat, lon=lon, baseline_start=baseline_years[0], baseline_end=baseline_years[-1], variables=np.asarray(VARIABLES), source_id=np.asarray(source_id), input_dir=np.asarray(str(directory)), area=np.asarray(area, dtype=np.float64), **{f"p10_{month + 1:02d}": p10[month] for month in range(12)}, **{f"p90_{month + 1:02d}": p90[month] for month in range(12)})
    os.replace(temporary, cache_path)
    return p10, p90, lat, lon


def build(args) -> dict:
    directory = args.input_dir.resolve()
    area = [float(value) for value in getattr(args, "area", DEFAULT_AREA)]
    report_path = directory / "era5_land_monthly_download_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reported_area = [float(value) for value in report.get("area", [])]
        if reported_area and reported_area != area:
            raise ValueError(f"download report area {reported_area} does not match requested build area {area}")
        if report.get("dataset") not in (None, "reanalysis-era5-land-monthly-means"):
            raise ValueError(f"unexpected downloader dataset: {report.get('dataset')}")
    baseline_years = list(range(args.baseline_start, args.baseline_end + 1))
    missing = [year for year in baseline_years if not root_file(directory, year).exists()]
    if missing:
        raise FileNotFoundError(f"missing baseline years: {missing}")
    cache = args.envelope_cache or directory / f"era5_land_soil_moisture_p10_p90_{args.baseline_start}_{args.baseline_end}.npz"
    source_id = source_fingerprint(directory, baseline_years, area)
    p10, p90, lat, lon = compute_envelopes(directory, baseline_years, cache, source_id, area)
    mask = spatial_mask(lat, lon)
    weights = np.cos(np.deg2rad(lat))[:, None] * np.ones((1, len(lon)), dtype=np.float64)
    weights[~mask] = 0.0
    series = []
    missing_years = []
    for year in range(args.start_year, args.end_year + 1):
        source = root_file(directory, year)
        if not source.exists():
            missing_years.append(year)
            continue
        annual_valid = 0.0
        annual_normal = 0.0
        valid_months = 0
        for month in range(12):
            values, current_lat, current_lon = root_zone(source, month, expected_year=year)
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
        "method": {"backbone": "depth-weighted ERA5-Land volumetric soil water layers 1–3", "layerWeights": {"0–7 cm": 0.07, "7–28 cm": 0.21, "28–100 cm": 0.72}, "baseline": f"{args.baseline_start}–{args.baseline_end} calendar-month per-grid-cell P10–P90 envelope", "normalization": "80% of valid ice-sheet-excluded land-area-months inside the local envelope = 100", "yearDefinition": f"Calendar years {args.start_year}–{args.end_year}; missing source years are omitted and monthly coverage is explicit", "missingYears": missing_years, "scope": "global ice-sheet-excluded land", "iceExclusion": ICE_EXCLUSION, "scoreDirection": "100 = root-zone soil moisture remains within historical local regimes", "diagnosticPolicy": "GRACE terrestrial water storage and JRC surface-water retention remain diagnostics only"},
        "sources": [{"id": "reanalysis-era5-land-monthly-means", "label": "Copernicus CDS ERA5-Land monthly averaged data", "variables": VARIABLES, "acceptedAliases": VARIABLE_ALIASES, "longitudeConvention": "Mask normalizes source longitudes to [-180,180) without reordering data columns", "area": area, "retrievalDirectory": str(directory), "baselineSourceFingerprint": source_id}],
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
