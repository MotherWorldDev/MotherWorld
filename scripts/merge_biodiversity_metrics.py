#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from pathlib import Path

from biodiversity_common import load_metadata, percentile_rank, provider_region_path, read_json, utc_now_iso, write_json

PROVIDERS = ("nhm_bii", "iucn_rasters", "iucn_ranges", "phylacine")


def parse_args():
    p = argparse.ArgumentParser(description="Merge modular biodiversity providers into frontend-ready MotherWorld region payloads.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--exclude", action="append", default=[], help="Provider id to exclude; repeatable or comma-separated.")
    p.add_argument("--kind", action="append", choices=["land", "marine", "lakes"], default=[])
    return p.parse_args()


def clean_excludes(values):
    out = set()
    for value in values:
        out.update(x.strip() for x in str(value).split(",") if x.strip())
    return out


def recorded_index(repo: Path) -> dict:
    path = repo / "frontend/public/data/species/species.index.json"
    return read_json(path, {}).get("regions", {}) if path.exists() else {}


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def main():
    args = parse_args()
    repo = args.repo.resolve()
    exclude = clean_excludes(args.exclude)
    kinds = args.kind or ["land", "marine", "lakes"]
    manifest = read_json(repo / "frontend/public/data/biodiversity/provider-manifest.json", {})
    species_idx = recorded_index(repo)
    regions = {}

    for kind in kinds:
        meta = load_metadata(repo, kind)
        for rid, region_meta in meta.items():
            provider_data = {}
            for provider in PROVIDERS:
                if provider in exclude:
                    continue
                path = provider_region_path(repo, provider, kind, rid)
                if path.exists():
                    provider_data[provider] = read_json(path, {})
            if "recorded_species" not in exclude and rid in species_idx:
                rec = species_idx[rid]
                provider_data["recorded_species"] = {
                    "provider": "recorded_species",
                    "metrics": {
                        "recordedSpeciesCount": rec.get("recordedSpeciesCount"),
                        "occurrenceCount": rec.get("occurrenceCount"),
                    },
                    "source": {"source": rec.get("source")},
                }
            if not provider_data:
                continue

            summary = {}
            bii = provider_data.get("nhm_bii", {}).get("metrics", {})
            if bii:
                summary["intactness"] = {
                    "valuePct": num(bii.get("intactnessPct")),
                    "changePp": num(bii.get("intactnessChangePp")),
                    "year": bii.get("latestYear"),
                }
            rasters = provider_data.get("iucn_rasters", {}).get("metrics", {})
            if rasters:
                summary["rarity"] = {
                    "speciesRichnessMean": num(rasters.get("speciesRichness", {}).get("areaWeightedMean")),
                    "rarityWeightedRichnessMean": num(rasters.get("rarityWeightedRichness", {}).get("areaWeightedMean")),
                    "rarityWeightedRichnessSum": num(rasters.get("rarityWeightedRichness", {}).get("fractionWeightedCellSum")),
                    "threatenedRarityWeightedRichnessMean": num(rasters.get("threatenedRarityWeightedRichness", {}).get("areaWeightedMean")),
                    "threatenedRarityWeightedRichnessSum": num(rasters.get("threatenedRarityWeightedRichness", {}).get("fractionWeightedCellSum")),
                }
            ranges = provider_data.get("iucn_ranges", {}).get("metrics", {})
            if ranges:
                summary["extinctionRisk"] = {
                    "threatenedSpeciesCount": ranges.get("threatenedSpeciesCount"),
                    "nearThreatenedSpeciesCount": ranges.get("nearThreatenedSpeciesCount"),
                    "categoryCounts": ranges.get("categoryCounts", {}),
                    "responsibilityScore": num(ranges.get("extinctionRiskResponsibility")),
                    "rangeResponsibility": num(ranges.get("rangeResponsibility")),
                    "rangeRestrictedThreatenedCount": ranges.get("rangeRestrictedThreatenedCount"),
                    "mappedPost1500ExtinctionCount": ranges.get("mappedPost1500ExtinctionCount"),
                    "mappedPost1500ExtinctSpecies": ranges.get("mappedPost1500ExtinctSpecies", []),
                }
            phy = provider_data.get("phylacine", {}).get("metrics", {})
            if phy:
                summary["historicalMammals"] = {
                    "presentNaturalSpeciesCount": phy.get("presentNaturalMammalSpeciesCount"),
                    "currentSpeciesCount": phy.get("currentMammalSpeciesCount"),
                    "locallyLostSpeciesCount": phy.get("locallyLostMammalSpeciesCount"),
                    "faunalRetentionPct": num(phy.get("mammalFaunalRetentionPct")),
                    "rangeOccupancyRetentionPct": num(phy.get("mammalRangeOccupancyRetentionPct")),
                    "exampleLocallyLostSpecies": phy.get("exampleLocallyLostSpecies", []),
                }
            rec = provider_data.get("recorded_species", {}).get("metrics", {})
            if rec:
                summary["recorded"] = {
                    "recordedSpeciesCount": rec.get("recordedSpeciesCount"),
                    "occurrenceCount": rec.get("occurrenceCount"),
                }

            regions[rid] = {
                "schemaVersion": 1,
                "regionId": rid,
                "regionName": region_meta.get("name") or rid,
                "regionKind": kind,
                "generatedAt": utc_now_iso(),
                "summary": summary,
                "providers": provider_data,
                "providerOrder": [p for p in ("nhm_bii", "iucn_rasters", "iucn_ranges", "phylacine", "recorded_species") if p in provider_data],
            }

    # Percentile ranks are computed separately by region kind so land and sea are not mixed.
    percentile_specs = [
        ("intactness", "valuePct", "intactnessPercentile", True),
        ("rarity", "rarityWeightedRichnessMean", "rarityPercentile", True),
        ("rarity", "threatenedRarityWeightedRichnessMean", "threatenedRarityPercentile", True),
        ("extinctionRisk", "responsibilityScore", "responsibilityPercentile", True),
        ("historicalMammals", "faunalRetentionPct", "faunalRetentionPercentile", True),
        ("recorded", "recordedSpeciesCount", "recordedRichnessPercentile", True),
    ]
    for kind in kinds:
        kind_ids = [rid for rid, p in regions.items() if p["regionKind"] == kind]
        for section, metric, out_key, high_is_high in percentile_specs:
            values = {
                rid: regions[rid]["summary"].get(section, {}).get(metric)
                for rid in kind_ids
                if regions[rid]["summary"].get(section, {}).get(metric) is not None
            }
            ranks = percentile_rank(values, high_is_high=high_is_high)
            for rid, rank in ranks.items():
                regions[rid]["summary"][section][out_key] = rank

    data_root = repo / "frontend/public/data/biodiversity"
    index = {
        "schemaVersion": 1,
        "generatedAt": utc_now_iso(),
        "regions": {},
        "providers": manifest.get("providers", {}),
        "excludedProviders": sorted(exclude),
        "design": {
            "headline": "BII intactness when available",
            "compositeScore": False,
            "note": "MotherWorld deliberately keeps condition, uniqueness, historical loss, extinction risk and observed richness as separate metrics.",
        },
    }
    for rid, payload in regions.items():
        kind = payload["regionKind"]
        rel = f"{kind}/{rid}.biodiversity.json"
        write_json(data_root / rel, payload, compact=True)
        index["regions"][rid] = {
            "url": rel,
            "kind": kind,
            "providers": payload["providerOrder"],
            "headlineIntactnessPct": payload["summary"].get("intactness", {}).get("valuePct"),
        }
    write_json(data_root / "biodiversity.index.json", index)
    print(f"Merged {len(regions)} biodiversity region payloads. Excluded providers: {sorted(exclude) or 'none'}")


if __name__ == "__main__":
    main()
