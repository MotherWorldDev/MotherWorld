#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from species_common import make_http_session, project_root_from, safe_int

GBIF_API = "https://api.gbif.org/v1"


def parse_args() -> argparse.Namespace:
    root = project_root_from(__file__)
    parser = argparse.ArgumentParser(
        description=(
            "Build a local speciesKey -> taxonomy SQLite cache from a GBIF SPECIES_LIST occurrence download. "
            "Use --species-list-zip with an existing download, or --request-download to request one using GBIF credentials."
        )
    )
    parser.add_argument("--species-list-zip", type=Path, default=None)
    parser.add_argument("--request-download", action="store_true")
    parser.add_argument("--output", type=Path, default=root / ".cache/motherworld/gbif-species-taxonomy.sqlite")
    parser.add_argument("--username", default=os.getenv("GBIF_USERNAME"))
    parser.add_argument("--password", default=os.getenv("GBIF_PASSWORD"))
    parser.add_argument("--email", default=os.getenv("GBIF_EMAIL"))
    parser.add_argument("--checklist-key", default=os.getenv("GBIF_CHECKLIST_KEY"))
    parser.add_argument("--poll-seconds", type=int, default=20)
    return parser.parse_args()


def request_species_list(args: argparse.Namespace) -> Path:
    if not args.username or not args.password or not args.email:
        raise SystemExit("GBIF_USERNAME, GBIF_PASSWORD and GBIF_EMAIL are required for --request-download")

    session = make_http_session()
    body: dict = {
        "creator": args.username,
        "notificationAddresses": [args.email],
        "sendNotification": True,
        "format": "SPECIES_LIST",
        "predicate": {
            "type": "and",
            "predicates": [
                {"type": "equals", "key": "HAS_COORDINATE", "value": "true"},
                {"type": "equals", "key": "HAS_GEOSPATIAL_ISSUE", "value": "false"},
                {"type": "equals", "key": "OCCURRENCE_STATUS", "value": "PRESENT"},
            ],
        },
    }
    if args.checklist_key:
        body["checklistKey"] = args.checklist_key

    print("Requesting one global GBIF SPECIES_LIST download used only as a taxonomy/name lookup cache...")
    response = session.post(
        f"{GBIF_API}/occurrence/download/request",
        auth=(args.username, args.password),
        json=body,
        timeout=90,
    )
    response.raise_for_status()
    download_key = response.text.strip().strip('"')
    if not download_key:
        raise RuntimeError("GBIF did not return a download key")
    print(f"GBIF download key: {download_key}")

    metadata = None
    while True:
        r = session.get(f"{GBIF_API}/occurrence/download/{download_key}", timeout=60)
        r.raise_for_status()
        metadata = r.json()
        status = str(metadata.get("status") or "").upper()
        print(f"  status: {status or 'unknown'}")
        if status == "SUCCEEDED":
            break
        if status in {"FAILED", "KILLED", "CANCELLED"}:
            raise RuntimeError(f"GBIF download {download_key} ended with status {status}: {metadata}")
        time.sleep(max(5, args.poll_seconds))

    download_link = metadata.get("downloadLink") or f"https://api.gbif.org/v1/occurrence/download/request/{download_key}.zip"
    target = args.output.parent / f"{download_key}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {download_link}")
    with session.get(download_link, stream=True, timeout=180) as r:
        r.raise_for_status()
        with target.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)

    sidecar = target.with_suffix(".metadata.json")
    sidecar.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved {target}")
    return target


def detect_species_table(zf: zipfile.ZipFile) -> tuple[str, str]:
    candidates = [n for n in zf.namelist() if n.lower().endswith((".txt", ".tsv", ".csv")) and not n.endswith("/")]
    for name in candidates:
        with zf.open(name) as raw:
            sample = raw.read(65536).decode("utf-8-sig", errors="replace")
        first = sample.splitlines()[0] if sample else ""
        delimiter = "\t" if "\t" in first else ","
        headers = {h.strip() for h in next(csv.reader([first], delimiter=delimiter))}
        if "speciesKey" in headers and ("scientificName" in headers or "species" in headers):
            return name, delimiter
    raise RuntimeError(f"Could not find the GBIF species-list table in ZIP. Files: {candidates[:20]}")


def create_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE species (
            species_key TEXT PRIMARY KEY,
            scientific_name TEXT NOT NULL,
            kingdom TEXT,
            phylum TEXT,
            class_name TEXT,
            order_name TEXT,
            family TEXT,
            genus TEXT,
            iucn_red_list_category TEXT,
            global_occurrence_count INTEGER DEFAULT 0
        );
        CREATE INDEX idx_species_name ON species(scientific_name);
        CREATE INDEX idx_species_kingdom ON species(kingdom);
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    return conn


def import_species_list(zip_path: Path, output: Path) -> None:
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    # Validate before building, and replace the existing cache only on success.
    with zipfile.ZipFile(zip_path) as zf:
        detect_species_table(zf)
    temporary = output.with_suffix(".building.sqlite")
    conn = create_db(temporary)
    inserted = 0
    skipped = 0
    with zipfile.ZipFile(zip_path) as zf:
        table_name, delimiter = detect_species_table(zf)
        print(f"Reading {table_name} from {zip_path.name}")
        with zf.open(table_name) as raw:
            wrapper = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            reader = csv.DictReader(wrapper, delimiter=delimiter)
            batch: list[tuple] = []
            for row in reader:
                species_key = str(row.get("speciesKey") or "").strip()
                if not species_key:
                    skipped += 1
                    continue
                name = (row.get("species") or row.get("acceptedScientificName") or row.get("scientificName") or "").strip()
                if not name:
                    skipped += 1
                    continue
                batch.append(
                    (
                        species_key,
                        name,
                        (row.get("kingdom") or "").strip() or None,
                        (row.get("phylum") or "").strip() or None,
                        (row.get("class") or "").strip() or None,
                        (row.get("order") or "").strip() or None,
                        (row.get("family") or "").strip() or None,
                        (row.get("genus") or "").strip() or None,
                        (row.get("iucnRedListCategory") or "").strip() or None,
                        safe_int(row.get("numberOfOccurrences"), default=0),
                    )
                )
                if len(batch) >= 10000:
                    conn.executemany(
                        """
                        INSERT INTO species(
                          species_key, scientific_name, kingdom, phylum, class_name, order_name,
                          family, genus, iucn_red_list_category, global_occurrence_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(species_key) DO UPDATE SET
                          scientific_name=excluded.scientific_name,
                          kingdom=COALESCE(excluded.kingdom, species.kingdom),
                          phylum=COALESCE(excluded.phylum, species.phylum),
                          class_name=COALESCE(excluded.class_name, species.class_name),
                          order_name=COALESCE(excluded.order_name, species.order_name),
                          family=COALESCE(excluded.family, species.family),
                          genus=COALESCE(excluded.genus, species.genus),
                          iucn_red_list_category=COALESCE(excluded.iucn_red_list_category, species.iucn_red_list_category),
                          global_occurrence_count=MAX(excluded.global_occurrence_count, species.global_occurrence_count)
                        """,
                        batch,
                    )
                    inserted += len(batch)
                    batch.clear()
                    conn.commit()
                    print(f"  processed {inserted:,} rows", end="\r", flush=True)
            if batch:
                conn.executemany(
                    """
                    INSERT INTO species(
                      species_key, scientific_name, kingdom, phylum, class_name, order_name,
                      family, genus, iucn_red_list_category, global_occurrence_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(species_key) DO UPDATE SET
                      scientific_name=excluded.scientific_name,
                      kingdom=COALESCE(excluded.kingdom, species.kingdom),
                      phylum=COALESCE(excluded.phylum, species.phylum),
                      class_name=COALESCE(excluded.class_name, species.class_name),
                      order_name=COALESCE(excluded.order_name, species.order_name),
                      family=COALESCE(excluded.family, species.family),
                      genus=COALESCE(excluded.genus, species.genus),
                      iucn_red_list_category=COALESCE(excluded.iucn_red_list_category, species.iucn_red_list_category),
                      global_occurrence_count=MAX(excluded.global_occurrence_count, species.global_occurrence_count)
                    """,
                    batch,
                )
                inserted += len(batch)
                conn.commit()

    metadata: dict[str, str] = {
        "source_zip": str(zip_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count_processed": str(inserted),
        "row_count_skipped": str(skipped),
    }
    sidecar = zip_path.with_suffix(".metadata.json")
    if sidecar.exists():
        try:
            gbif_meta = json.loads(sidecar.read_text(encoding="utf-8"))
            for key in ("key", "doi", "downloadLink", "created", "modified", "status"):
                if gbif_meta.get(key) is not None:
                    metadata[f"gbif_{key}"] = str(gbif_meta[key])
        except Exception:
            pass
    conn.executemany("INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", metadata.items())
    conn.commit()
    unique_count = conn.execute("SELECT COUNT(*) FROM species").fetchone()[0]
    conn.close()
    if not unique_count:
        raise ValueError("Species list contained no usable species names; existing cache preserved")
    temporary.replace(output)
    print(f"\nBuilt {output} with {unique_count:,} unique species keys ({skipped:,} rows skipped).")


def main() -> None:
    args = parse_args()
    zip_path = args.species_list_zip
    if args.request_download:
        zip_path = request_species_list(args)
    if not zip_path:
        raise SystemExit(
            "Provide --species-list-zip path/to/GBIF-species-list.zip or use --request-download. "
            "A single species-list download avoids making hundreds of thousands of species-name API calls."
        )
    import_species_list(zip_path.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
