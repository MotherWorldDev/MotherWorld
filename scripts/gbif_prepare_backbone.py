#!/usr/bin/env python
"""Build the pack-compatible taxonomy cache from GBIF's public backbone dump.

The frozen 2023-08-28 edition matches the legacy speciesKey facet used by
GBIF occurrence search. This name lookup contains no occurrence/IUCN estimates.
Source schema: https://hosted-datasets.gbif.org/datasets/backbone/README.html
"""
from __future__ import annotations
import argparse
import gzip
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from gbif_prepare_taxonomy import create_db
from species_common import make_http_session, project_root_from

EDITION = "2023-08-28"
SOURCE_URL = f"https://hosted-datasets.gbif.org/datasets/backbone/{EDITION}/simple.txt.gz"

def rows(path):
    # PostgreSQL COPY text, not CSV: backslashes escape controls and \N is NULL.
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 30:
                raise ValueError("Unexpected GBIF backbone schema")
            yield [None if value == r"\N" else value.replace(r"\t", " ").replace(r"\n", " ").replace(r"\r", " ").replace(r"\\", "\\") for value in fields]

def import_backbone(source: Path, output: Path):
    temporary = output.with_suffix(".building.sqlite")
    conn = create_db(temporary)
    try:
        # Higher ranks are a small lookup; species rows stream through SQLite.
        higher = {}
        for row in rows(source):
            if row[5] in {"KINGDOM", "PHYLUM", "CLASS", "ORDER", "FAMILY", "GENUS"}:
                higher[row[0]] = row[19] or row[18]
        batch = []
        count = 0
        for row in rows(source):
            if row[5] != "SPECIES" or not (row[19] or row[18]):
                continue
            batch.append((row[0], row[19] or row[18], *[higher.get(row[i]) for i in range(10, 16)], None, 0))
            if len(batch) >= 10000:
                conn.executemany("INSERT INTO species VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                count += len(batch); batch.clear(); conn.commit()
                if count % 500000 == 0: print(f"Imported {count:,} species names", flush=True)
        if batch:
            conn.executemany("INSERT INTO species VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
            count += len(batch)
        if not count: raise ValueError("Backbone contained no species names")
        metadata = {"source_type": "GBIF Backbone Taxonomy", "source_url": SOURCE_URL,
                    "edition": EDITION, "generated_at": datetime.now(timezone.utc).isoformat(), "species_count": str(count)}
        conn.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        conn.commit()
    finally:
        conn.close()
    temporary.replace(output)
    print(f"Built taxonomy cache: {count:,} species names", flush=True)

def main():
    root = project_root_from(__file__)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=root / f".cache/motherworld/backbone-{EDITION}-simple.txt.gz")
    p.add_argument("--output", type=Path, default=root / ".cache/motherworld/gbif-species-taxonomy.sqlite")
    p.add_argument("--download", action="store_true", help="Download the public 466 MiB archive if missing; no account needed")
    args = p.parse_args()
    if not args.source.exists() and args.download:
        args.source.parent.mkdir(parents=True, exist_ok=True)
        part = args.source.with_suffix(".gz.part")
        with make_http_session().get(SOURCE_URL, stream=True, timeout=(30, 120)) as response:
            response.raise_for_status()
            with part.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024): stream.write(chunk)
        part.replace(args.source)
    if not args.source.exists(): p.error("Source archive is missing; supply --source or --download")
    import_backbone(args.source, args.output)

if __name__ == "__main__": main()
