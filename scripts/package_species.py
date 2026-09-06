#!/usr/bin/env python
"""Validate and compress completed inventories into the static site directory.

Generation can continue in separate cache directories while this command takes
an atomic manifest snapshot. It never includes the private taxonomy cache.
"""
from __future__ import annotations
import argparse
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from species_common import project_root_from, write_json_atomic

SAFE_PATH = re.compile(r"^(land|lakes|marine)/[a-zA-Z0-9_-]+\.json$")

def package(inputs, output):
    output = output.resolve()
    manifest = {"schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(), "regions": {}, "sources": {}}
    original_bytes = packed_bytes = 0
    prepared = []
    # Validate every input before changing the public manifest.
    for source in inputs:
        source = source.resolve()
        index = json.loads((source / "species.index.json").read_text(encoding="utf-8"))
        manifest["sources"].update(index.get("sources", {}))
        for region_id, entry in index["regions"].items():
            if not SAFE_PATH.fullmatch(entry["url"]): raise ValueError("Unexpected source inventory path")
            source_path = source / entry["url"]
            data = json.loads(source_path.read_text(encoding="utf-8"))
            if data.get("regionId") != region_id or not isinstance(data.get("species"), list):
                raise ValueError(f"Inventory does not match manifest: {region_id}")
            species = data["species"]
            if data.get("source") != entry["source"] or len(species) != data["stats"]["recordedSpeciesCount"]:
                raise ValueError(f"Inventory statistics or source mismatch: {region_id}")
            if sum(int(row.get("occurrenceCount", 0)) for row in species) != data["stats"]["occurrenceCount"]:
                raise ValueError(f"Occurrence totals do not match rows: {region_id}")
            if any(not row.get("scientificName") for row in species): raise ValueError(f"Missing species label: {region_id}")
            if data.get("taxonomySource", {}).get("type") == "GBIF Backbone Taxonomy":
                for row in species: row.pop("globalOccurrenceCount", None)
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            packed = gzip.compress(raw, compresslevel=9, mtime=0)
            rel = entry["url"] + ".gz"
            prepared.append((rel, packed))
            original_bytes += len(raw); packed_bytes += len(packed)
            manifest["regions"][region_id] = {**entry, "url": rel, "generatedAt": data["generatedAt"], "recordedSpeciesCount": len(species), "occurrenceCount": data["stats"]["occurrenceCount"]}
    for rel, packed in prepared:
        path = output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # Avoid changing files whose contents are already packaged.
        if path.exists() and path.read_bytes() == packed: continue
        temp = path.with_suffix(".gz.tmp"); temp.write_bytes(packed); temp.replace(path)
    write_json_atomic(output / "species.index.json", manifest, indent=2)
    # Remove only the replaced, generated plain JSON files inside this output.
    for rel, _ in prepared:
        plain = (output / rel.removesuffix(".gz")).resolve()
        if output not in plain.parents: raise ValueError("Output escaped species directory")
        if plain.exists(): plain.unlink()
    counts = {kind: sum(e["regionKind"] == kind for e in manifest["regions"].values()) for kind in ("land", "lakes", "marine")}
    print(f"Packaged {len(prepared)} inventories {counts}: {original_bytes/1048576:.1f} MiB JSON -> {packed_bytes/1048576:.1f} MiB gzip")
    return manifest

def main():
    root = project_root_from(__file__)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir", type=Path, action="append", required=True)
    p.add_argument("--output-dir", type=Path, default=root / "frontend/public/data/species")
    args = p.parse_args(); package(args.input_dir, args.output_dir)

if __name__ == "__main__": main()
