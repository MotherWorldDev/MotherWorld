#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from freshwater_common import geometric_mean, read_json, utc_now_iso, write_json

COMPONENTS = [
    {"id": "grace_tws", "label": "Terrestrial water storage", "weight": 0.45, "file": "grace_storage.json"},
    {"id": "era5land_soil_moisture", "label": "Root-zone soil moisture", "weight": 0.30, "file": "soil_moisture.json"},
    {"id": "jrc_surface_water", "label": "Surface-water extent / retention", "weight": 0.25, "file": "surface_water.json"},
]


def main() -> None:
    p = argparse.ArgumentParser(description="Merge global freshwater provider components into the MotherWorld Freshwater Stability family index.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--raw-dir", type=Path, default=None)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--min-weight-coverage", type=float, default=0.70)
    p.add_argument("--min-effective-coverage", type=float, default=0.60)
    args = p.parse_args()
    repo = args.repo.resolve()
    raw_dir = args.raw_dir or repo / "index_raw/freshwater"
    output = args.output or repo / "frontend/public/data/indices/diagnostics/freshwater.diagnostics.json"

    rows = []
    score_inputs = []
    total_weight = sum(c["weight"] for c in COMPONENTS)
    valid_weight = 0.0
    effective = 0.0
    source_ids = []
    for spec in COMPONENTS:
        path = raw_dir / spec["file"]
        payload = read_json(path, {})
        score = payload.get("score")
        coverage = float(payload.get("coverage") or 0.0) if payload else 0.0
        available = score is not None and coverage > 0
        if available:
            valid_weight += spec["weight"]
            effective += spec["weight"] * min(1.0, max(0.0, coverage))
            score_inputs.append((float(score), spec["weight"]))
            source_ids.append(payload.get("source", {}).get("id") or payload.get("providerId"))
        rows.append({
            "id": spec["id"],
            "label": spec["label"],
            "score": round(float(score), 4) if score is not None else None,
            "coverage": round(coverage, 6),
            "weight": spec["weight"],
            "raw": payload.get("raw") if payload else None,
            "unit": payload.get("unit") if payload else None,
            "source": payload.get("source", {}).get("label") if payload else None,
            "context": payload.get("context", {}) if payload else {},
            "included": available,
            "reason": None if available else "provider_component_not_generated",
        })

    weight_coverage = valid_weight / total_weight if total_weight else 0.0
    effective_coverage = effective / total_weight if total_weight else 0.0
    sufficient = weight_coverage >= args.min_weight_coverage and effective_coverage >= args.min_effective_coverage
    score = geometric_mean(score_inputs) if sufficient else None
    payload = {
        "schemaVersion": 1,
        "earthHealthRole": "diagnostic_only",
        "scoredInEarthHealth": False,
        "earthHealthRole": "diagnostic_only",
        "scoredInEarthHealth": False,
        "earthHealthRole": "diagnostic_only",
        "scoredInEarthHealth": False,
        "familyId": "freshwater",
        "label": "Freshwater stability",
        "scope": "global_only",
        "score": round(score, 4) if score is not None else None,
        "coverage": round(effective_coverage, 6),
        "components": rows,
        "context": {
            "familyWeightCoverage": round(weight_coverage, 6),
            "effectiveDataCoverage": round(effective_coverage, 6),
            "regionalFreshwaterScoring": False,
            "rationale": "Freshwater is evaluated globally because water storage and hydrological processes cross ecoregion boundaries; regional basin/lake scoring is deferred.",
        },
        "sources": [x for x in source_ids if x],
        "series": [],
        "generatedAt": utc_now_iso(),
        "method": {
            "crossComponentAggregation": "weighted_geometric_mean",
            "componentWeights": {c["id"]: c["weight"] for c in COMPONENTS},
            "scoreDirection": "100 = best condition / closest to historical freshwater regime",
            "minimumWeightCoverage": args.min_weight_coverage,
            "minimumEffectiveCoverage": args.min_effective_coverage,
            "missingDataPolicy": "missing components lower family coverage and can suppress Freshwater Stability; missing data is never treated as healthy",
            "regionalPolicy": "global family only; no ecoregion freshwater score in v1",
        },
    }
    write_json(output, payload)
    if score is None:
        print(f"Freshwater Stability withheld: family weight coverage={weight_coverage:.1%}, effective coverage={effective_coverage:.1%}")
    else:
        print(f"Freshwater Stability {score:.2f}/100 coverage={effective_coverage:.1%}")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
