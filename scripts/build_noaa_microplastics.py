#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd

from contaminants_common import (
    CATEGORY_LABELS,
    SeriesAccumulator,
    canonical_unit,
    find_column,
    to_float,
    year_from,
)
from regional_diagnostics_adapters import (
    canonical_point_region_map,
    merge_contaminant_categories,
    output_root,
)


NO_PROTOCOL = "not reported"


def _clean_field(value, fallback: str = NO_PROTOCOL) -> str:
    if value is None or pd.isna(value):
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return fallback
    return text


def _positive_measurement(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) > 0


def _protocol_descriptor(row, columns: dict[str, str | None]) -> dict[str, str]:
    return {
        "marineSetting": _clean_field(row.get(columns["setting"]) if columns["setting"] else None, "marine sample"),
        "samplingMethod": _clean_field(row.get(columns["method"]) if columns["method"] else None),
        "meshSizeMm": _clean_field(row.get(columns["mesh"]) if columns["mesh"] else None),
        "waterSampleDepthM": _clean_field(row.get(columns["water_depth"]) if columns["water_depth"] else None),
        "sedimentSampleDepthM": _clean_field(row.get(columns["sediment_depth"]) if columns["sediment_depth"] else None),
    }


def _protocol_key(protocol: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(protocol.items()))


def main():
    ap = argparse.ArgumentParser(description="Map NOAA NCEI Marine Microplastics observations into MotherWorld marine ecoregions.")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--input", type=Path, required=True, help="CSV exported from NOAA NCEI Marine Microplastics database")
    ap.add_argument("--output-root", type=Path, default=None, help="v8 build root; deployable files are written below frontend/public/data/")
    ap.add_argument("--max-values-per-series", type=int, default=10000)
    args = ap.parse_args()
    repo = args.repo.resolve()
    root = output_root(repo, args.output_root)

    df = pd.read_csv(args.input, low_memory=False)
    lat = find_column(df.columns, ("Latitude", "lat", "Latitude (degree)", "Latitude (degrees)"))
    lon = find_column(df.columns, ("Longitude", "lon", "Longitude (degree)", "Longitude (degrees)"))
    val = find_column(df.columns, ("Concentration", "Microplastics Measurement", "Measurement", "Value", "Concentration Value"))
    unit = find_column(df.columns, ("Unit", "Units", "Concentration Unit", "Measurement Unit"))
    date = find_column(df.columns, ("Date", "Sampling Date", "Collection Date", "Sample Date", "Year"))
    setting = find_column(df.columns, ("Marine Setting", "Setting", "Sample Type", "Medium"))
    method = find_column(df.columns, ("Sampling Method", "Method", "Sampling protocol", "Protocol"))
    mesh = find_column(df.columns, ("Mesh Size (mm)", "Mesh Size", "Mesh (mm)"))
    water_depth = find_column(df.columns, ("Water Sample Depth (m)", "Water Sample Depth", "Water Depth (m)"))
    sediment_depth = find_column(df.columns, ("Sediment Sample Depth (m)", "Sediment Sample Depth", "Sediment Depth (m)"))
    if not (lat and lon):
        raise SystemExit(f"Could not identify latitude/longitude columns. Columns: {list(df.columns)}")

    df["_lat"] = pd.to_numeric(df[lat], errors="coerce")
    df["_lon"] = pd.to_numeric(df[lon], errors="coerce")
    df = df[df["_lat"].between(-90, 90) & df["_lon"].between(-180, 180)].reset_index(drop=True)
    mapping = canonical_point_region_map(
        pd.DataFrame({"lat": df["_lat"], "lon": df["_lon"]}),
        repo,
        kinds={"marine"},
    )

    columns = {
        "setting": setting,
        "method": method,
        "mesh": mesh,
        "water_depth": water_depth,
        "sediment_depth": sediment_depth,
    }
    accumulators = {}
    observation_counts = defaultdict(int)
    quantified_counts = defaultdict(int)
    positive_counts = defaultdict(int)
    observation_ids = defaultdict(set)

    for i, row in df.iterrows():
        regions = mapping.get(i, [])
        if not regions:
            continue
        raw = to_float(row.get(val)) if val else None
        reported_unit = canonical_unit(_clean_field(row.get(unit) if unit else None, "reported unit"))
        protocol = _protocol_descriptor(row, columns)
        analyte = f"Microplastics · {protocol['marineSetting']}"
        year = year_from(row.get(date)) if date and pd.notna(row.get(date)) else None
        unique_id = _clean_field(row.get("Unique ID") if "Unique ID" in row else None, f"obs-{i}")
        for region_id in regions:
            observation_counts[region_id] += 1
            observation_ids[region_id].add(unique_id)
            if raw is None:
                continue
            quantified_counts[region_id] += 1
            if _positive_measurement(raw):
                positive_counts[region_id] += 1
            key = (region_id, analyte, reported_unit, _protocol_key(protocol))
            if key not in accumulators:
                accumulators[key] = SeriesAccumulator(
                    analyte,
                    "microplastics",
                    reported_unit,
                    args.max_values_per_series,
                )
            accumulators[key].add(
                value=raw,
                detected=_positive_measurement(raw),
                station_id=unique_id,
                year=year,
            )

    by_region = defaultdict(list)
    for (region_id, _analyte, _unit, protocol_key), series in accumulators.items():
        payload = series.payload()
        payload["protocol"] = dict(protocol_key)
        payload["quantifiedCount"] = payload["sampleCount"]
        payload["detectionBasis"] = "Positive reported concentration; NOAA export has no explicit nondetect flag."
        by_region[region_id].append(payload)

    updates = {}
    for region_id, count in observation_counts.items():
        analytes = sorted(
            by_region.get(region_id, []),
            key=lambda item: (-item["sampleCount"], item["name"], item["unit"], str(item.get("protocol"))),
        )
        updates[region_id] = {
            "_kind": "marine",
            "categories": {
                "microplastics": {
                    "label": CATEGORY_LABELS["microplastics"],
                    "type": "measurements",
                    "analytes": analytes,
                    "sampleCount": count,
                    "quantifiedCount": quantified_counts[region_id],
                    "detectedCount": positive_counts[region_id],
                    "stationCount": len(observation_ids[region_id]),
                    "detectionBasis": "Positive reported concentration; NOAA export has no explicit nondetect flag.",
                    "protocolFields": [
                        "marineSetting",
                        "samplingMethod",
                        "meshSizeMm",
                        "waterSampleDepthM",
                        "sedimentSampleDepthM",
                    ],
                    "coverageNote": "NOAA global marine microplastics observations. Series remain separated by reporting unit and available sampling protocol fields; missing or unresolved protocol fields are reported as not reported.",
                }
            },
            "sources": {
                "noaa_microplastics": {
                    "label": "NOAA NCEI Marine Microplastics Database",
                    "url": "https://www.ncei.noaa.gov/products/microplastics",
                    "coverage": "1972-present global marine observations",
                }
            },
        }

    source_entry = {
        "id": "noaa_microplastics",
        "label": "NOAA NCEI Marine Microplastics Database",
        "url": "https://www.ncei.noaa.gov/products/microplastics",
        "coverage": "1972-present global marine observations",
        "stagedInput": args.input.name,
        "inputBytes": args.input.stat().st_size,
        "protocolFields": ["Marine Setting", "Sampling Method", "Mesh Size (mm)", "Water Sample Depth (m)", "Sediment Sample Depth (m)"],
        "detectionNote": "The export has no explicit nondetect flag; detectedCount is positive reported concentration and quantifiedCount includes finite zero values.",
    }
    merge_contaminant_categories(repo, root, updates, source_entry)
    print(f"Wrote/updated canonical marine microplastics observations for {len(updates):,} regions.")


if __name__ == "__main__":
    main()
