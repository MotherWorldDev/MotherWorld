#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from geology_common import utc_now_iso


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

    if source_root.exists():
        for provider_dir in sorted(source_root.iterdir()):
            if not provider_dir.is_dir() or provider_dir.name in excluded:
                continue
            for file in provider_dir.glob("*.json"):
                data = json.loads(file.read_text(encoding="utf-8"))
                region_id = data["regionId"]
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
                source = data.get("source") or {}
                current["providers"].append(
                    {
                        "id": data.get("providerId"),
                        "label": source.get("label"),
                        "version": source.get("version"),
                    }
                )

    regions = {}
    for region_id, data in by_region.items():
        kind = data.get("kind") or "land"
        folder = output_root / kind
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{region_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        regions[region_id] = {
            "url": f"{kind}/{region_id}.json",
            "kind": kind,
            "providers": [provider["id"] for provider in data["providers"]],
            "generatedAt": data["generatedAt"],
        }

    index = {"schemaVersion": 1, "generatedAt": utc_now_iso(), "regions": regions}
    (output_root / "geology.index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
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
