#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis_geometry import analysis_geometry_cache_identity, analysis_geometry_metadata
from geology_common import json_safe, load_regions, utc_now_iso


AFFECTED_ANALYSIS_REGIONS = {
    "eco_117", "eco_121", "eco_130", "eco_267",
    "eco_509", "eco_562", "eco_609",
}


def _is_open_ocean(region_id: str) -> bool:
    rid = str(region_id or "")
    return rid == "open_ocean" or rid == "marine_ocean_0" or rid.endswith("ocean_0")


def _expected_geometry(repo: Path) -> dict[tuple[str, str], tuple[str, dict]]:
    """Load identities from the same corrected analysis loaders used by builders."""
    try:
        regions = load_regions(repo, kinds=("land", "marine", "lakes"), globalize_open_ocean=True)
    except Exception:
        return {}
    expected = {}
    for row in regions.itertuples():
        rid = "open_ocean" if _is_open_ocean(row.regionId) else str(row.regionId)
        if rid not in AFFECTED_ANALYSIS_REGIONS and rid != "open_ocean":
            continue
        kind = str(row.kind)
        expected[(rid, kind)] = (
            analysis_geometry_cache_identity(rid, row.geometry),
            analysis_geometry_metadata(rid, row.geometry),
        )
    return expected


def _stale_reason(data: dict, expected: dict[tuple[str, str], tuple[str, dict]]):
    rid = str(data.get("regionId") or "")
    if rid not in AFFECTED_ANALYSIS_REGIONS and not _is_open_ocean(rid):
        return None
    kind = str(data.get("kind") or ("marine" if _is_open_ocean(rid) else "land"))
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


def deep(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for key, value in b.items():
            out[key] = deep(out[key], value) if key in out else value
        return out
    return b


def merge(repo: Path, exclude=None) -> dict:
    repo = Path(repo).resolve()
    excluded = set(exclude or [])
    source_root = repo / ".cache/motherworld/geology/providers"
    output_root = repo / "frontend/public/data/geology"
    output_root.mkdir(parents=True, exist_ok=True)
    by_region = {}
    expected_geometry = _expected_geometry(repo)
    stale_fragments = []

    if source_root.exists():
        for provider_dir in sorted(source_root.iterdir()):
            if not provider_dir.is_dir() or provider_dir.name in excluded:
                continue
            for file in provider_dir.glob("*.json"):
                data = json.loads(file.read_text(encoding="utf-8"))
                region_id = data["regionId"]
                stale = _stale_reason(data, expected_geometry)
                if stale is not None:
                    stale_fragments.append({
                        "providerId": data.get("providerId") or provider_dir.name,
                        "regionId": str(region_id),
                        "kind": data.get("kind") or ("marine" if _is_open_ocean(region_id) else "land"),
                        "path": file.relative_to(repo).as_posix(),
                        **stale,
                    })
                    continue
                current = by_region.setdefault(
                    region_id,
                    {
                        "schemaVersion": 1,
                        "regionId": region_id,
                        "regionName": data.get("regionName", region_id),
                        "kind": data.get("kind"),
                        "generatedAt": utc_now_iso(),
                        "sections": {},
                        "providers": [],
                    },
                )
                current["sections"] = deep(current["sections"], data.get("sections", {}))
                if "analysisGeometry" not in current and data.get("analysisGeometry") is not None:
                    current["analysisGeometry"] = data["analysisGeometry"]
                    current["analysisGeometryCacheIdentity"] = data.get("analysisGeometryCacheIdentity")
                source = data.get("source") or {}
                provider = {
                    "id": data.get("providerId"),
                    "label": source.get("label"),
                    "version": source.get("version"),
                }
                if data.get("analysisGeometryCacheIdentity"):
                    provider["analysisGeometryCacheIdentity"] = data["analysisGeometryCacheIdentity"]
                current["providers"].append(provider)

    regions = {}
    for region_id, data in by_region.items():
        kind = data.get("kind") or "land"
        folder = output_root / kind
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{region_id}.json"
        path.write_text(json.dumps(json_safe(data), indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        regions[region_id] = {
            "url": f"{kind}/{region_id}.json",
            "kind": kind,
            "providers": [provider["id"] for provider in data["providers"]],
            "generatedAt": data["generatedAt"],
        }

    index = {
        "schemaVersion": 1,
        "generatedAt": utc_now_iso(),
        "regions": regions,
        "staleFragments": stale_fragments,
        "analysisGeometryPolicy": {
            "schemaVersion": 1,
            "guardedRegionIds": sorted(AFFECTED_ANALYSIS_REGIONS),
            "guardedSyntheticIds": ["open_ocean"],
            "requireProducerIdentity": True,
        },
    }
    (output_root / "geology.index.json").write_text(json.dumps(json_safe(index), indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if stale_fragments:
        print(f"Withheld {len(stale_fragments)} stale geology fragments for corrected analysis footprints")
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge provider geology fragments into compact public per-region JSON.")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()
    index = merge(args.repo, args.exclude)
    print(f"Published geology for {len(index['regions'])} regions")


if __name__ == "__main__":
    main()
