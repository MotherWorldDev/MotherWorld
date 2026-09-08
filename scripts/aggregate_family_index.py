#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from index_engine import aggregate_family_regions, read_json, score_level, utc_now_iso, write_json


def parse_args():
    p = argparse.ArgumentParser(description="Aggregate standardized regional family scores into one global family index.")
    p.add_argument("--family", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--input", type=Path, required=True, help="JSON file containing a list or {regions:[...]} of regional scores.")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--score-key", default="score")
    p.add_argument("--coverage-key", default="coverage")
    p.add_argument("--area-key", default="areaKm2")
    return p.parse_args()


def main():
    args = parse_args()
    raw = read_json(args.input, [])
    rows = raw.get("regions", []) if isinstance(raw, dict) else raw
    if isinstance(rows, dict):
        rows = list(rows.values())
    result = aggregate_family_regions(rows, score_key=args.score_key, area_key=args.area_key, coverage_key=args.coverage_key)
    payload = {
        "schemaVersion": 1,
        "familyId": args.family,
        "label": args.label,
        "generatedAt": utc_now_iso(),
        "score": result["score"],
        "level": score_level(result["score"]),
        "coverage": result["coverage"],
        "regionalAggregation": result,
        "components": [],
        "series": [],
    }
    write_json(args.output, payload)
    print(json.dumps({"family": args.family, "score": payload["score"], "coverage": payload["coverage"]}, indent=2))


if __name__ == "__main__":
    main()
