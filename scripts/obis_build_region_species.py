#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from species_common import (
    geometry_wkt_chunks,
    load_region_geometries,
    make_http_session,
    project_root_from,
    region_geometry_fingerprint,
    query_fingerprint,
    sha256_text,
    safe_int,
    write_json_atomic,
)

OBIS_CHECKLIST = "https://api.obis.org/v3/checklist"


def parse_args() -> argparse.Namespace:
    root = project_root_from(__file__)
    p = argparse.ArgumentParser(
        description=(
            "Generate marine recorded-species inventories from the public OBIS checklist API using MotherWorld MEOW polygons. "
            "OBIS resolves marine taxonomy through WoRMS and excludes dropped records by default."
        )
    )
    p.add_argument("--output-dir", type=Path, default=root / "frontend/public/data/species")
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--max-wkt-chars", type=int, default=6500)
    p.add_argument("--request-delay", type=float, default=0.2)
    p.add_argument("--startdate", default=None, help="Optional ISO date")
    p.add_argument("--enddate", default=None, help="Optional ISO date")
    p.add_argument("--exclude-flag", action="append", default=[], help="OBIS QC flag to exclude; repeatable")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def load_index(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schemaVersion": 1, "generatedAt": None, "regions": {}, "sources": {}}


def obis_checklist(session, wkt: str, args: argparse.Namespace) -> list[dict]:
    all_rows: list[dict] = []
    partition = 0
    while True:
        params: list[tuple[str, str | int]] = [("geometry", wkt), ("partition", partition)]
        if args.startdate:
            params.append(("startdate", args.startdate))
        if args.enddate:
            params.append(("enddate", args.enddate))
        for flag in args.exclude_flag:
            params.append(("exclude", flag))

        r = session.get(OBIS_CHECKLIST, params=params, timeout=120)
        r.raise_for_status()
        payload = r.json()
        rows = payload.get("results") or []
        if isinstance(rows, dict):
            rows = [rows]
        all_rows.extend(rows)
        partitions = safe_int(payload.get("partitions"), default=1)
        partition += 1
        if partition >= max(1, partitions):
            break
        time.sleep(max(0.0, args.request_delay))
    return all_rows


def normalize_obis_species(rows: list[dict]) -> list[dict]:
    # OBIS checklist responses can contain species, subspecies and lower taxa.
    # Group child taxa back to their parent `species` name so the MotherWorld UI
    # presents a species-level inventory rather than double-counting subspecies.
    grouped: dict[str, dict] = {}
    for row in rows:
        canonical = (
            row.get("species")
            or (row.get("scientificName") if str(row.get("taxonRank") or row.get("rank") or "").lower() == "species" else None)
            or ""
        )
        canonical = str(canonical).strip()
        if not canonical:
            continue
        records = safe_int(row.get("records"), 0)
        entry = grouped.setdefault(
            canonical,
            {
                "scientificName": canonical,
                "aphiaId": row.get("speciesid") or row.get("speciesID") or row.get("aphiaID") or row.get("AphiaID"),
                "kingdom": row.get("kingdom"),
                "phylum": row.get("phylum"),
                "class": row.get("class") or row.get("class_name"),
                "order": row.get("order"),
                "family": row.get("family"),
                "genus": row.get("genus"),
                "iucnRedListCategory": row.get("redlistCategory") or row.get("redlist") or row.get("iucnRedListCategory"),
                "occurrenceCount": 0,
            },
        )
        entry["occurrenceCount"] = int(entry.get("occurrenceCount") or 0) + records
        for field in ("kingdom", "phylum", "class", "order", "family", "genus", "iucnRedListCategory", "aphiaId"):
            if not entry.get(field):
                incoming = (
                    row.get("speciesid") or row.get("speciesID") or row.get("aphiaID") or row.get("AphiaID")
                    if field == "aphiaId"
                    else row.get(field)
                )
                if incoming:
                    entry[field] = incoming
    out = list(grouped.values())
    out.sort(key=lambda s: (-int(s.get("occurrenceCount") or 0), str(s.get("scientificName") or "")))
    return out


def main() -> None:
    args = parse_args()
    root = project_root_from(__file__)
    regions = load_region_geometries(root, "marine")
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]

    out_root = args.output_dir.resolve()
    index_path = out_root / "species.index.json"
    index = load_index(index_path)
    index.setdefault("sources", {})["OBIS"] = {
        "label": "Ocean Biodiversity Information System (OBIS)",
        "method": "OBIS checklist API clipped to MotherWorld marine ecoregion polygons",
        "url": "https://obis.org/",
    }
    session = make_http_session()

    for position, region in enumerate(regions, start=1):
        rel_url = f"marine/{region.region_id}.json"
        target = out_root / rel_url
        wkts = geometry_wkt_chunks(region.geometry, max_chars=args.max_wkt_chars)
        fingerprint = sha256_text("\n".join(wkts))
        query_key = query_fingerprint(fingerprint, {"version": 2, "startdate": args.startdate, "enddate": args.enddate, "exclude": sorted(args.exclude_flag)})
        old = index.get("regions", {}).get(region.region_id)
        if (
            not args.force
            and target.exists()
            and old
            and old.get("queryFingerprint") == query_key
            and old.get("source") == "OBIS"
        ):
            print(f"[{position}/{len(regions)}] {region.region_id} {region.name}: cached")
            continue

        print(f"[{position}/{len(regions)}] {region.region_id} {region.name}")
        rows: list[dict] = []
        for i, wkt in enumerate(wkts, start=1):
            print(f"    OBIS polygon chunk {i}/{len(wkts)}", end="\r", flush=True)
            rows.extend(obis_checklist(session, wkt, args))
            time.sleep(max(0.0, args.request_delay))
        print(" " * 70, end="\r")

        species = normalize_obis_species(rows)
        by_kingdom = Counter((s.get("kingdom") or "Unknown") for s in species)
        by_iucn = Counter((s.get("iucnRedListCategory") or "Unspecified") for s in species)
        generated_at = datetime.now(timezone.utc).isoformat()
        occurrence_count = sum(int(s.get("occurrenceCount") or 0) for s in species)
        payload = {
            "schemaVersion": 1,
            "regionId": region.region_id,
            "regionName": region.name,
            "regionKind": "marine",
            "source": "OBIS",
            "generatedAt": generated_at,
            "inventoryType": "recorded_species",
            "method": (
                "Species-level checklist derived from OBIS occurrence records intersecting the region polygon. "
                "OBIS quality-control processing and WoRMS taxonomy are used; child taxa are grouped to species level. "
                "Occurrence counts are record counts, not population abundance."
            ),
            "query": {
                "endpoint": OBIS_CHECKLIST,
                "geometryFingerprint": fingerprint,
                "queryFingerprint": query_key,
                "geometryChunkCount": len(wkts),
                "startdate": args.startdate,
                "enddate": args.enddate,
                "excludeFlags": args.exclude_flag,
            },
            "stats": {
                "recordedSpeciesCount": len(species),
                "occurrenceCount": occurrence_count,
                "byKingdom": dict(sorted(by_kingdom.items())),
                "byIucnCategory": dict(sorted(by_iucn.items())),
            },
            "species": species,
        }
        write_json_atomic(target, payload)
        index.setdefault("regions", {})[region.region_id] = {
            "url": rel_url,
            "source": "OBIS",
            "regionKind": "marine",
            "recordedSpeciesCount": len(species),
            "occurrenceCount": occurrence_count,
            "generatedAt": generated_at,
            "geometryFingerprint": fingerprint,
                "queryFingerprint": query_key,
        }
        index["generatedAt"] = generated_at
        write_json_atomic(index_path, index, indent=2)
        print(f"    -> {len(species):,} recorded species; {occurrence_count:,} occurrence records")


if __name__ == "__main__":
    main()
