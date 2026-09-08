#!/usr/bin/env python
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pandas as pd
import requests

from backbone_common import write_diagnostic
from env_common import clamp, utc_now_iso, write_json

URLS = {
    "aggi": "https://gml.noaa.gov/aggi/AGGI_Table.csv",
    "co2": "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_gl.csv",
    "ch4": "https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_annmean_gl.csv",
    "n2o": "https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_annmean_gl.csv",
}


def fetch(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return pd.read_csv(io.StringIO(response.text), comment="#")


def load(path, url):
    return pd.read_csv(path, comment="#") if path else fetch(url)


def col(data, needle):
    return next((column for column in data.columns if needle in str(column).lower().replace("_", "")), None)


def aggi_column(data):
    direct = col(data, "aggi")
    if direct is not None:
        return direct
    # NOAA's current table names this normalized column "1990 = 1".
    return next(
        (column for column in data.columns if "1990" in str(column) and "=1" in str(column).replace(" ", "")),
        None,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--aggi-upper", type=float, default=2.0)
    parser.add_argument("--aggi-csv", type=Path)
    parser.add_argument("--co2-csv", type=Path)
    parser.add_argument("--ch4-csv", type=Path)
    parser.add_argument("--n2o-csv", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    data = load(args.aggi_csv, URLS["aggi"])
    year_column = col(data, "year") or data.columns[0]
    aggi = aggi_column(data)
    if aggi is None:
        raise SystemExit(f"No AGGI column: {list(data.columns)}")
    data[year_column] = pd.to_numeric(data[year_column], errors="coerce")
    data[aggi] = pd.to_numeric(data[aggi], errors="coerce")
    data = data.dropna(subset=[year_column, aggi])
    series = []
    for _, row in data.iterrows():
        value = float(row[aggi])
        series.append(
            {
                "year": int(row[year_column]),
                "score": round(clamp(100 * (args.aggi_upper - value) / (args.aggi_upper - 1)), 4),
                "coverage": 1.0,
                "raw": value,
                "unit": "AGGI (1990=1)",
            }
        )
    latest = series[-1]
    gases = {}
    local_paths = {"co2": args.co2_csv, "ch4": args.ch4_csv, "n2o": args.n2o_csv}
    for gas in ("co2", "ch4", "n2o"):
        try:
            gas_data = load(local_paths[gas], URLS[gas])
            gas_year = col(gas_data, "year") or gas_data.columns[0]
            numeric = [column for column in gas_data.columns if column != gas_year and "unc" not in str(column).lower()]
            gas_value = numeric[0]
            gas_data[gas_year] = pd.to_numeric(gas_data[gas_year], errors="coerce")
            gas_data[gas_value] = pd.to_numeric(gas_data[gas_value], errors="coerce")
            gas_data = gas_data.dropna(subset=[gas_year, gas_value])
            row = gas_data.iloc[-1]
            gases[gas] = {
                "year": int(row[gas_year]),
                "value": float(row[gas_value]),
                "unit": "ppm" if gas == "co2" else "ppb",
            }
        except Exception as exc:
            gases[gas] = {"error": str(exc)}
    payload = {
        "schemaVersion": 2,
        "familyId": "climate_forcing",
        "label": "Climate forcing",
        "scope": "global_only",
        "earthHealthRole": "backbone",
        "score": latest["score"],
        "coverage": 1.0,
        "latestBackboneYear": latest["year"],
        "components": [
            {
                "id": "aggi",
                "label": "Long-lived greenhouse-gas forcing",
                "score": latest["score"],
                "weight": 1.0,
                "raw": latest["raw"],
                "unit": latest["unit"],
                "source": "NOAA GML AGGI",
            }
        ],
        "series": series,
        "generatedAt": utc_now_iso(),
        "method": {
            "backbone": "NOAA AGGI only",
            "normalization": {"healthyReference": 1.0, "criticalReference": args.aggi_upper},
            "scoreDirection": "100 at AGGI 1.0; 0 at configured upper reference",
            "diagnosticPolicy": "individual greenhouse-gas concentrations are visible diagnostics only",
        },
        "sources": [
            {
                "id": "noaa-aggi",
                "label": "NOAA Annual Greenhouse Gas Index",
                "retrieval": URLS["aggi"],
            }
        ],
    }
    write_json(repo / "frontend/public/data/indices/families/climate_forcing.index.json", payload)
    write_diagnostic(
        repo,
        "climate_forcing",
        {
            "schemaVersion": 1,
            "familyId": "climate_forcing",
            "label": "Climate forcing diagnostics",
            "context": {"greenhouseGases": gases},
            "sources": URLS,
        },
    )
    print("Climate forcing", latest["year"], latest["score"])


if __name__ == "__main__":
    main()
