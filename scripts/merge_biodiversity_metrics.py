#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from pathlib import Path

from analysis_geometry import analysis_geometry_cache_identity, analysis_geometry_metadata
from biodiversity_common import load_metadata, load_region_geometries, percentile_rank, provider_region_path, read_json, utc_now_iso, write_json

PROVIDERS = ("nhm_bii", "iucn_rasters", "iucn_ranges", "phylacine")
AFFECTED_ANALYSIS_REGIONS = {
    "eco_117", "eco_121", "eco_130", "eco_267",
    "eco_509", "eco_562", "eco_609",
}


def _is_open_ocean(region_id: str) -> bool:
    rid = str(region_id or "")
    return rid == "open_ocean" or rid == "marine_ocean_0" or rid.endswith("ocean_0")


def _expected_geometry(repo: Path, kinds) -> dict[tuple[str, str], tuple[str, dict]]:
    expected = {}
    for kind in kinds:
        try:
            geometries = load_region_geometries(repo, kind)
        except Exception:
            continue
        for rid, geometry in geometries.items():
            rid = str(rid)
            if rid not in AFFECTED_ANALYSIS_REGIONS:
                continue
            expected[(rid, str(kind))] = (
                analysis_geometry_cache_identity(rid, geometry),
                analysis_geometry_metadata(rid, geometry),
            )
    if "marine" in kinds:
        # Biodiversity has no synthetic ocean loader of its own, but its merge
        # can still receive an open_ocean fragment from a shared provider.
        try:
            from geology_common import load_regions as load_geology_regions
            regions = load_geology_regions(repo, kinds=("land", "marine", "lakes"), globalize_open_ocean=True)
            for row in regions.itertuples():
                if _is_open_ocean(row.regionId):
                    expected[("open_ocean", "marine")] = (
                        analysis_geometry_cache_identity("open_ocean", row.geometry),
                        analysis_geometry_metadata("open_ocean", row.geometry),
                    )
        except Exception:
            pass
    return expected


def _stale_reason(data: dict, expected: dict[tuple[str, str], tuple[str, dict]]):
    rid = str(data.get("regionId") or "")
    if rid not in AFFECTED_ANALYSIS_REGIONS and not _is_open_ocean(rid):
        return None
    kind = str(data.get("regionKind") or data.get("kind") or ("marine" if _is_open_ocean(rid) else "land"))
    key = ("open_ocean" if _is_open_ocean(rid) else rid, kind)
    target = expected.get(key)
    if target is None:
        return {"reason": "expected_analysis_geometry_unavailable"}
    expected_identity, expected_metadata = target
    actual_identity = data.get("analysisGeometryCacheIdentity")
    actual_metadata = data.get("analysisGeometry") or {}
    if actual_identity != expected_identity:
        return {
            "reason": "missing_analysis_geometry_identity" if not actual_identity else "analysis_geometry_identity_mismatch",
            "expected": expected_identity,
            "actual": actual_identity,
            "expectedFingerprint": expected_metadata.get("geometryFingerprint"),
            "actualFingerprint": actual_metadata.get("geometryFingerprint"),
        }
    if actual_metadata.get("geometryFingerprint") != expected_metadata.get("geometryFingerprint"):
        return {
            "reason": "analysis_geometry_fingerprint_mismatch",
            "expected": expected_identity,
            "actual": actual_identity,
            "expectedFingerprint": expected_metadata.get("geometryFingerprint"),
            "actualFingerprint": actual_metadata.get("geometryFingerprint"),
        }
    return None


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
    expected_geometry = _expected_geometry(repo, kinds)
    stale_fragments = []
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
                    data = read_json(path, {})
                    stale = _stale_reason(data, expected_geometry)
                    if stale is not None:
                        stale_fragments.append({
                            "providerId": data.get("provider") or provider,
                            "regionId": rid,
                            "kind": kind,
                            "path": path.relative_to(repo).as_posix(),
                            **stale,
                        })
                        continue
                    provider_data[provider] = data
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
                expected = expected_geometry.get((str(rid), str(kind)))
                if expected is not None:
                    provider_data["recorded_species"]["analysisGeometry"] = expected[1]
                    provider_data["recorded_species"]["analysisGeometryCacheIdentity"] = expected[0]
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

            expected = expected_geometry.get((str(rid), str(kind)))
            analysis_metadata = expected[1] if expected is not None else next(
                (value.get("analysisGeometry") for value in provider_data.values() if value.get("analysisGeometry")),
                None,
            )
            analysis_identity = expected[0] if expected is not None else next(
                (value.get("analysisGeometryCacheIdentity") for value in provider_data.values() if value.get("analysisGeometryCacheIdentity")),
                None,
            )
            regions[rid] = {
                "schemaVersion": 1,
                "regionId": rid,
                "regionName": region_meta.get("name") or rid,
                "regionKind": kind,
                "generatedAt": utc_now_iso(),
                "analysisGeometry": analysis_metadata,
                "analysisGeometryCacheIdentity": analysis_identity,
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
        "analysisGeometryPolicy": {
            "schemaVersion": 1,
            "guardedRegionIds": sorted(AFFECTED_ANALYSIS_REGIONS),
            "guardedSyntheticIds": ["open_ocean"],
            "requireProducerIdentity": True,
        },
        "staleFragments": stale_fragments,
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
    if stale_fragments:
        print(f"Withheld {len(stale_fragments)} stale biodiversity fragments for corrected analysis footprints")
    print(f"Merged {len(regions)} biodiversity region payloads. Excluded providers: {sorted(exclude) or 'none'}")


if __name__ == "__main__":
    main()
