#!/usr/bin/env python
"""Build an isolated v8 UCMR5 PFAS provider snapshot.

The public EPA files are tab-delimited Windows-1252 text.  UCMR5 reports PFAS
concentrations in micrograms per litre, while the MotherWorld PFAS contract is
nanograms per litre.  This builder keeps censored observations in the sample
denominator and converts only finite reported concentrations with the exact
mass-unit factor.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import geopandas as gpd
import pandas as pd

from contaminants_common import (
    CATEGORY_LABELS,
    SeriesAccumulator,
    canonical_unit,
    convert_mass_per_l,
    find_column,
    to_float,
    year_from,
)
from pfas_sdpt_source_acquisition import UCMR5_ALL, UCMR5_ZIPCODES
from regional_diagnostics_adapters import (
    canonical_point_region_map,
    attach_analysis_geometry_provenance,
    merge_contaminant_categories,
    output_root,
)


PROVIDER_ID = "epa_ucmr5"
EPA_PAGE_URL = "https://www.epa.gov/dwucmr/occurrence-data-unregulated-contaminant-monitoring-rule"
EPA_OCCURRENCE_ZIP_URL = "https://www.epa.gov/system/files/other-files/2023-08/ucmr5-occurrence-data.zip"

# EPA's final UCMR5 occurrence file has these 29 PFAS analytes plus lithium.
# Keeping the allow-list explicit prevents an accidental future EPA file
# extension from being silently labelled as PFAS.
UCMR5_PFAS = frozenset(
    {
        "11Cl-PF3OUdS",
        "4:2 FTS",
        "6:2 FTS",
        "8:2 FTS",
        "9Cl-PF3ONS",
        "ADONA",
        "HFPO-DA",
        "NEtFOSAA",
        "NFDHA",
        "NMeFOSAA",
        "PFBA",
        "PFBS",
        "PFDA",
        "PFDoA",
        "PFEESA",
        "PFHpA",
        "PFHpS",
        "PFHxA",
        "PFHxS",
        "PFMBA",
        "PFMPA",
        "PFNA",
        "PFOA",
        "PFOS",
        "PFPeA",
        "PFPeS",
        "PFTA",
        "PFTrDA",
        "PFUnA",
    }
)


@contextmanager
def _tabular(path: Path, filename: str) -> Iterator[io.TextIOBase]:
    """Open a loose or zipped EPA tab-delimited file with the source encoding."""

    path = Path(path).resolve()
    if path.is_dir():
        candidate = path / filename
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        with candidate.open("r", encoding="cp1252", errors="replace", newline="") as handle:
            yield handle
        return
    if not path.is_file():
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if Path(name).name.lower() == filename.lower()]
        if not names:
            raise FileNotFoundError(f"{filename} not found in {path}")
        with archive.open(names[0]) as raw:
            with io.TextIOWrapper(raw, encoding="cp1252", errors="replace", newline="") as handle:
                yield handle


def _read_service_zips(path: Path) -> dict[str, set[str]]:
    with _tabular(path, UCMR5_ZIPCODES) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        pws_col = find_column(reader.fieldnames or [], ("PWSID", "PWS ID"))
        zip_col = find_column(reader.fieldnames or [], ("ZIPCODE", "ZipCode", "ZIP Code", "ZIP"))
        if not pws_col or not zip_col:
            raise ValueError(f"Could not identify PWSID/ZIPCODE columns in {UCMR5_ZIPCODES}")
        pws_zips: dict[str, set[str]] = defaultdict(set)
        for row in reader:
            pws = str(row.get(pws_col) or "").strip()
            value = str(row.get(zip_col) or "").strip()[:5]
            if pws and len(value) == 5 and value.isdigit():
                pws_zips[pws].add(value)
    if not pws_zips:
        raise ValueError("UCMR5 ZIP code file has no valid PWS service areas")
    return dict(pws_zips)


def _read_zcta_points(path: Path, needed: set[str]) -> tuple[pd.DataFrame, dict[str, int], dict]:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".zip":
        frame = gpd.read_file(f"zip://{path}")
    else:
        frame = gpd.read_file(path)
    if frame.crs is None:
        frame = frame.set_crs("EPSG:4269")
    frame = frame.to_crs("EPSG:4326")
    zcol = find_column(frame.columns, ("ZCTA5CE20", "ZCTA5CE", "GEOID20", "GEOID", "ZCTA5"))
    if not zcol:
        raise ValueError(f"Could not identify a ZCTA identifier field in {path}")
    frame["_zip"] = frame[zcol].astype(str).str.strip().str.zfill(5)
    frame = frame[frame["_zip"].isin(needed)].copy()
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    if frame.empty:
        raise ValueError("No requested UCMR5 service ZIPs were returned by Census")
    frame["geometry"] = frame.geometry.representative_point()
    points = pd.DataFrame(
        {
            "lat": frame.geometry.y.to_numpy(),
            "lon": frame.geometry.x.to_numpy(),
        }
    )
    zip_index = {str(row["_zip"]): int(index) for index, row in frame.reset_index(drop=True).iterrows()}
    audit = {
        "path": str(path),
        "featureCount": int(len(frame)),
        "requestedZipCount": int(len(needed)),
        "resolvedZipCount": int(len(zip_index)),
        "geometryPolicy": "representative point of complete 2020 ZCTA polygon for service-area mapping",
    }
    return points, zip_index, audit


def build_updates(
    repo: Path,
    ucmr_source: Path,
    zcta_path: Path,
    *,
    max_values_per_series: int = 10000,
) -> tuple[dict[str, dict], dict]:
    """Return canonical PFAS updates and a compact source audit."""

    if max_values_per_series <= 0:
        raise ValueError("max_values_per_series must be positive")
    repo = Path(repo).resolve()
    ucmr_source = Path(ucmr_source).resolve()
    pws_zips = _read_service_zips(ucmr_source)
    needed_zips = set().union(*pws_zips.values())
    points, zip_index, zcta_audit = _read_zcta_points(zcta_path, needed_zips)

    # This loads and unions the authoritative analysis footprint before any
    # provider result is assigned.  It also records the exact producer context
    # consumed by attach_analysis_geometry_provenance for corrected regions.
    zip_region_map: dict[str, set[str]] = defaultdict(set)
    point_regions = canonical_point_region_map(points, repo, kinds={"land"})
    for zip_code, point_index in zip_index.items():
        zip_region_map[zip_code].update(point_regions.get(point_index, []))
    pws_regions = {
        pws: set().union(*(zip_region_map.get(zip_code, set()) for zip_code in zips))
        for pws, zips in pws_zips.items()
    }

    accumulators: dict[tuple[str, str], SeriesAccumulator] = {}
    station_ids: dict[str, set[str]] = defaultdict(set)
    observed_analytes: set[str] = set()
    row_count = 0
    skipped_unknown_units = 0
    observed_collection_years: set[int] = set()
    with _tabular(ucmr_source, UCMR5_ALL) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        pws_col = find_column(fields, ("PWSID", "PWS ID"))
        analyte_col = find_column(fields, ("Contaminant", "Analyte"))
        sign_col = find_column(fields, ("AnalyticalResultsSign", "Analytical Results Sign"))
        value_col = find_column(fields, ("AnalyticalResultValue", "Analytical Result Value"))
        unit_col = find_column(fields, ("Units", "Unit"))
        date_col = find_column(fields, ("CollectionDate", "Collection Date"))
        if not pws_col or not analyte_col:
            raise ValueError(f"Could not identify PWSID/Contaminant in {UCMR5_ALL}: {fields}")
        for row in reader:
            row_count += 1
            analyte = str(row.get(analyte_col) or "").strip()
            if analyte not in UCMR5_PFAS:
                # Includes lithium and any future EPA non-PFAS additions.
                continue
            observed_analytes.add(analyte)
            pws = str(row.get(pws_col) or "").strip()
            regions = pws_regions.get(pws, set())
            if not regions:
                continue
            raw_value = to_float(row.get(value_col)) if value_col else None
            reported_unit = canonical_unit(row.get(unit_col) if unit_col else "")
            value = None
            if raw_value is not None:
                converted = convert_mass_per_l(raw_value, reported_unit, "ng/L")
                if converted is None:
                    skipped_unknown_units += 1
                else:
                    value = converted[0]
            sign = str(row.get(sign_col) or "").strip() if sign_col else ""
            detected = not sign.startswith("<") and value is not None
            year = year_from(row.get(date_col)) if date_col else None
            if year is not None:
                observed_collection_years.add(year)
            for region_id in regions:
                key = (region_id, analyte)
                if key not in accumulators:
                    accumulators[key] = SeriesAccumulator(
                        analyte,
                        "pfas",
                        "ng/L",
                        max_values_per_series,
                    )
                accumulators[key].add(
                    value=value,
                    detected=detected,
                    station_id=pws,
                    year=year,
                )
                station_ids[region_id].add(pws)

    by_region: dict[str, list[dict]] = defaultdict(list)
    for (region_id, _), series in accumulators.items():
        by_region[region_id].append(series.payload())
    collection_period = f"{min(observed_collection_years)}-{max(observed_collection_years)}" if observed_collection_years else None
    updates: dict[str, dict] = {}
    for region_id, analytes in by_region.items():
        for analyte in analytes:
            analyte["sampleCountLabel"] = "Analyte results"
            analyte["stationCountLabel"] = "Water systems"
        analytes.sort(key=lambda item: (-item["sampleCount"], item["name"]))
        sample_count = sum(int(item["sampleCount"]) for item in analytes)
        detected_count = sum(int(item["detectedCount"]) for item in analytes)
        updates[region_id] = {
            "_kind": "land",
            "categories": {
                "pfas": {
                    "label": CATEGORY_LABELS["pfas"],
                    "type": "measurements",
                    "analytes": analytes,
                    "sampleCount": sample_count,
                    "detectedCount": detected_count,
                    "stationCount": len(station_ids[region_id]),
                    "sampleCountLabel": "Analyte results",
                    "stationCountLabel": "Water systems",
                    "coverageNote": (
                        "U.S. EPA UCMR5 PFAS occurrence results mapped to canonical MotherWorld "
                        "ecoregions through representative points of the 2020 ZCTAs served by each "
                        "public water system. This is a service-area exposure proxy, not exact "
                        "source-water or sample-point geography. Non-detects remain in sampleCount "
                        "and detection-rate denominators without substituted concentrations. Counts are analyte result rows (sampleCount) and distinct PWS IDs (stationCount); coverage is incomplete where Census ZCTAs or PWS ZIP links are unresolved."
                    ),
                }
            },
            "sources": {
                PROVIDER_ID: {
                    "label": "U.S. EPA UCMR 5 PFAS occurrence data",
                    "url": EPA_PAGE_URL,
                    "occurrenceDataUrl": EPA_OCCURRENCE_ZIP_URL,
                    "period": collection_period,
                    "programPeriod": "2023-2025",
                    "scope": "U.S. public drinking-water systems",
                    "analyteCount": len(UCMR5_PFAS),
                    "unitConversion": "µg/L to ng/L (1000x)",
                }
            },
        }
    audit = {
        "source": str(ucmr_source),
        "sourceRows": row_count,
        "mappedRegions": len(updates),
        "observedPfasAnalytes": sorted(observed_analytes),
        "observedPfasAnalyteCount": len(observed_analytes),
        "expectedPfasAnalyteCount": len(UCMR5_PFAS),
        "skippedUnknownUnitValues": skipped_unknown_units,
        "servicePwsCount": len(pws_zips),
        "serviceZipCount": len(needed_zips),
        "mappedServicePwsCount": sum(bool(regions) for regions in pws_regions.values()),
        "observedCollectionYears": sorted(observed_collection_years),
        "collectionPeriod": collection_period,
        "zcta": zcta_audit,
    }
    return updates, audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--ucmr5",
        "--ucmr-zip",
        dest="ucmr5",
        type=Path,
        required=True,
        help="EPA UCMR5 directory containing UCMR5_All.txt/UCMR5_ZIPCodes.txt or official ZIP",
    )
    parser.add_argument("--zcta", type=Path, required=True, help="Census 2020 ZCTA ZIP or TIGERweb GeoJSON")
    parser.add_argument("--output-root", type=Path, default=None, help="isolated v8 build root")
    parser.add_argument("--max-values-per-series", type=int, default=10000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    root = output_root(repo, args.output_root)
    updates, audit = build_updates(
        repo,
        args.ucmr5,
        args.zcta,
        max_values_per_series=args.max_values_per_series,
    )
    attach_analysis_geometry_provenance(repo, updates)
    source_entry = {
        "id": PROVIDER_ID,
        "label": "U.S. EPA UCMR 5 PFAS occurrence data",
        "url": EPA_PAGE_URL,
        "occurrenceDataUrl": EPA_OCCURRENCE_ZIP_URL,
        "period": audit["collectionPeriod"],
        "programPeriod": "2023-2025",
        "scope": "U.S. public drinking-water systems",
        "license": "U.S. federal public data",
        "stagedInput": str(args.ucmr5.resolve()),
        "zctaInput": str(args.zcta.resolve()),
        "unitConversion": "µg/L to ng/L (1000x)",
        "audit": audit,
    }
    merge_contaminant_categories(repo, root, updates, source_entry)
    print(f"Wrote isolated UCMR5 PFAS observations for {len(updates):,} canonical land ecoregions.")


if __name__ == "__main__":
    main()





