#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from shapely import from_wkt, to_wkt
from shapely.geometry import MultiPolygon
from shapely.geometry.polygon import orient

from species_common import (
    _repair_polygonal,
    batched,
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

GBIF_OCCURRENCE_SEARCH = "https://api.gbif.org/v1/occurrence/search"
DEFAULT_BASIS = [
    "HUMAN_OBSERVATION",
    "MACHINE_OBSERVATION",
    "OBSERVATION",
    "PRESERVED_SPECIMEN",
    "MATERIAL_SAMPLE",
    "LIVING_SPECIMEN",
    "MATERIAL_CITATION",
]


def parse_args() -> argparse.Namespace:
    root = project_root_from(__file__)
    p = argparse.ArgumentParser(
        description=(
            "Generate per-region recorded-species inventories from GBIF occurrence facets. "
            "This lists species with georeferenced PRESENT occurrence records inside each region polygon."
        )
    )
    p.add_argument("--runtime-geometry", action="store_true", help="Use the committed LOD0 polygons instead of the large original land shapefile")
    p.add_argument("--kind", choices=["land", "lakes", "both"], default="both")
    p.add_argument("--taxonomy-db", type=Path, default=root / ".cache/motherworld/gbif-species-taxonomy.sqlite")
    p.add_argument("--output-dir", type=Path, default=root / "frontend/public/data/species")
    p.add_argument("--region", action="append", default=[], help="Only build this region ID; may be repeated")
    p.add_argument("--facet-page-size", type=int, default=1000)
    p.add_argument("--max-wkt-chars", type=int, default=2500)
    p.add_argument("--request-delay", type=float, default=0.15)
    p.add_argument("--year", default=None, help="Optional GBIF year/range, e.g. 1950,2026")
    p.add_argument("--basis-of-record", action="append", default=[], help="Override basisOfRecord filters; repeatable")
    p.add_argument("--force", action="store_true")
    p.add_argument("--include-dataset-facets", action="store_true", help="Also store datasetKey counts for regional provenance")
    p.add_argument("--dataset-facet-limit", type=int, default=5000)
    return p.parse_args()


def taxonomy_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return dict(conn.execute("SELECT key, value FROM metadata").fetchall())
    except sqlite3.Error:
        return {}


def lookup_taxonomy(conn: sqlite3.Connection, species_keys: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for chunk in batched(species_keys, 800):
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT species_key, scientific_name, kingdom, phylum, class_name, order_name,
                   family, genus, iucn_red_list_category, global_occurrence_count
            FROM species WHERE species_key IN ({placeholders})
            """,
            chunk,
        ).fetchall()
        for row in rows:
            out[str(row[0])] = {
                "scientificName": row[1],
                "kingdom": row[2],
                "phylum": row[3],
                "class": row[4],
                "order": row[5],
                "family": row[6],
                "genus": row[7],
                "iucnRedListCategory": row[8],
                "globalOccurrenceCount": int(row[9] or 0),
            }
    return out


def fetch_facet_counts(session, wkt: str, *, facet: str, page_size: int, args: argparse.Namespace) -> Counter:
    counts: Counter = Counter()
    offset = 0
    while True:
        params: list[tuple[str, str | int]] = [
            ("geometry", wkt),
            ("hasCoordinate", "true"),
            ("hasGeospatialIssue", "false"),
            ("occurrenceStatus", "PRESENT"),
            ("limit", 0),
            ("facet", facet),
            ("facetLimit", page_size),
            ("facetOffset", offset),
        ]
        if args.year:
            params.append(("year", args.year))
        basis = args.basis_of_record or DEFAULT_BASIS
        for value in basis:
            params.append(("basisOfRecord", value))

        r = session.get(GBIF_OCCURRENCE_SEARCH, params=params, timeout=90)
        if not r.ok:
            raise RuntimeError(f"GBIF occurrence query failed ({r.status_code}): {r.text[:300]}")
        payload = r.json()
        facets = payload.get("facets") or []
        facet_obj = None
        for candidate in facets:
            field = str(candidate.get("field") or "").replace("_", "").lower()
            if field == facet.replace("_", "").lower():
                facet_obj = candidate
                break
        if facet_obj is None and facets:
            facet_obj = facets[0]
        rows = (facet_obj or {}).get("counts") or []
        for row in rows:
            key = row.get("name")
            count = safe_int(row.get("count"), 0)
            if key not in (None, "") and count > 0:
                counts[str(key)] += count
        if len(rows) < page_size:
            break
        offset += len(rows)
        time.sleep(max(0.0, args.request_delay))
    return counts


def gbif_wkt(wkt: str) -> str:
    geom = _repair_polygonal(from_wkt(wkt))
    if geom is None:
        raise ValueError("GBIF query geometry contains no polygonal area")
    fixed = orient(geom, sign=1.0) if geom.geom_type == "Polygon" else MultiPolygon([orient(p, sign=1.0) for p in geom.geoms])
    return to_wkt(fixed, rounding_precision=6)


def species_for_region(session, region, args: argparse.Namespace, wkts=None) -> tuple[Counter, Counter, list[str]]:
    species: Counter = Counter()
    datasets: Counter = Counter()
    wkts = wkts if wkts is not None else geometry_wkt_chunks(region.geometry, max_chars=args.max_wkt_chars)
    for i, wkt in enumerate(wkts, start=1):
        print(f"    GBIF polygon chunk {i}/{len(wkts)}", end="\r", flush=True)
        species.update(fetch_facet_counts(session, wkt, facet="speciesKey", page_size=args.facet_page_size, args=args))
        if args.include_dataset_facets:
            datasets.update(
                fetch_facet_counts(
                    session,
                    wkt,
                    facet="datasetKey",
                    page_size=min(args.dataset_facet_limit, args.facet_page_size),
                    args=args,
                )
            )
        time.sleep(max(0.0, args.request_delay))
    print(" " * 70, end="\r")
    return species, datasets, wkts


def load_index(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "schemaVersion": 1,
        "generatedAt": None,
        "regions": {},
        "sources": {
            "GBIF": {
                "label": "Global Biodiversity Information Facility (GBIF)",
                "method": "Occurrence Search API, speciesKey facets clipped to MotherWorld region polygons",
                "url": "https://www.gbif.org/",
            }
        },
    }


def output_subdir(kind: str) -> str:
    return "land" if kind == "land" else "lakes"


def build_kind(kind: str, args: argparse.Namespace, conn: sqlite3.Connection, source_meta: dict[str, str]) -> None:
    root = project_root_from(__file__)
    regions = load_region_geometries(root, kind, args.region, runtime_geometry=args.runtime_geometry)
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]
    out_root = args.output_dir
    index_path = out_root / "species.index.json"
    index = load_index(index_path)
    index.setdefault("sources", {})["GBIF"] = {"label": "Global Biodiversity Information Facility", "url": "https://www.gbif.org/"}
    session = make_http_session()
    if kind == "land" and not args.region:
        # Cover regions with authored editorial content first during a full build.
        editorial = root / "frontend/public/data/region-content.json"
        priority = set(json.loads(editorial.read_text(encoding="utf-8")).get("regions", {})) if editorial.exists() else set()
        regions.sort(key=lambda r: (r.region_id not in priority, r.region_id))

    for position, region in enumerate(regions, start=1):
        rel_url = f"{output_subdir(kind)}/{region.region_id}.json"
        target = out_root / rel_url
        wkts = [gbif_wkt(wkt) for wkt in geometry_wkt_chunks(region.geometry, max_chars=args.max_wkt_chars)]
        fingerprint = sha256_text("\n".join(wkts))
        query_key = query_fingerprint(fingerprint, {"version": 2, "year": args.year, "basis": sorted(args.basis_of_record or DEFAULT_BASIS), "datasets": args.include_dataset_facets, "taxonomy": source_meta})
        old = index.get("regions", {}).get(region.region_id)
        if (
            not args.force
            and target.exists()
            and old
            and old.get("queryFingerprint") == query_key
            and old.get("source") == "GBIF"
        ):
            print(f"[{position}/{len(regions)}] {region.region_id} {region.name}: cached")
            continue

        print(f"[{position}/{len(regions)}] {region.region_id} {region.name}")
        species_counts_raw, dataset_counts_raw, wkts = species_for_region(session, region, args, wkts)
        species_counts: Counter[str] = Counter({str(key): int(count) for key, count in species_counts_raw.items()})
        keys = list(species_counts.keys())
        taxonomy = lookup_taxonomy(conn, keys)

        species_rows = []
        unresolved = 0
        for key, count in species_counts.items():
            tax = taxonomy.get(key)
            if not tax:
                unresolved += 1
                species_rows.append(
                    {
                        "speciesKey": key,
                        "scientificName": f"GBIF species {key}",
                        "occurrenceCount": count,
                        "taxonomyResolved": False,
                    }
                )
                continue
            species_rows.append(
                {
                    "speciesKey": key,
                    "scientificName": tax["scientificName"],
                    "kingdom": tax.get("kingdom"),
                    "phylum": tax.get("phylum"),
                    "class": tax.get("class"),
                    "order": tax.get("order"),
                    "family": tax.get("family"),
                    "genus": tax.get("genus"),
                    "iucnRedListCategory": tax.get("iucnRedListCategory"),
                    "occurrenceCount": count,
                    "globalOccurrenceCount": tax.get("globalOccurrenceCount", 0),
                    "taxonomyResolved": True,
                }
            )

        species_rows.sort(key=lambda s: (-int(s.get("occurrenceCount") or 0), str(s.get("scientificName") or "")))
        by_kingdom = Counter((s.get("kingdom") or "Unknown") for s in species_rows)
        by_iucn = Counter((s.get("iucnRedListCategory") or "Unspecified") for s in species_rows)
        generated_at = datetime.now(timezone.utc).isoformat()
        data = {
            "schemaVersion": 1,
            "regionId": region.region_id,
            "regionName": region.name,
            "regionKind": kind,
            "source": "GBIF",
            "generatedAt": generated_at,
            "inventoryType": "recorded_species",
            "method": (
                "Distinct GBIF speciesKey values from PRESENT georeferenced occurrence records inside the region polygon; "
                "records flagged by GBIF as having geospatial issues are excluded. Counts are occurrence-record counts, not population abundance."
            ),
            "query": {
                "endpoint": GBIF_OCCURRENCE_SEARCH,
                "geometryFingerprint": fingerprint,
                "queryFingerprint": query_key,
                "geometryChunkCount": len(wkts),
                "geometrySource": "runtime_lod0" if args.runtime_geometry or kind == "lakes" else "original_when_available",
                "year": args.year,
                "basisOfRecord": args.basis_of_record or DEFAULT_BASIS,
                "hasCoordinate": True,
                "hasGeospatialIssue": False,
                "occurrenceStatus": "PRESENT",
            },
            "stats": {
                "recordedSpeciesCount": len(species_rows),
                "occurrenceCount": int(sum(species_counts.values())),
                "taxonomyUnresolvedCount": unresolved,
                "byKingdom": dict(sorted(by_kingdom.items())),
                "byIucnCategory": dict(sorted(by_iucn.items())),
            },
            "taxonomySource": {
                "type": source_meta.get("source_type", "GBIF SPECIES_LIST download"),
                "url": source_meta.get("source_url"),
                "edition": source_meta.get("edition"),
                "downloadKey": source_meta.get("gbif_key"),
                "doi": source_meta.get("gbif_doi"),

            },
            "species": species_rows,
        }
        if args.include_dataset_facets:
            data["datasets"] = [
                {"datasetKey": key, "occurrenceCount": count}
                for key, count in dataset_counts_raw.most_common()
            ]
        write_json_atomic(target, data)

        index.setdefault("regions", {})[region.region_id] = {
            "url": rel_url,
            "source": "GBIF",
            "regionKind": kind,
            "recordedSpeciesCount": len(species_rows),
            "occurrenceCount": int(sum(species_counts.values())),
            "generatedAt": generated_at,
            "geometryFingerprint": fingerprint,
            "queryFingerprint": query_key,
        }
        index["generatedAt"] = generated_at
        write_json_atomic(index_path, index, indent=2)
        print(f"    -> {len(species_rows):,} recorded species; {sum(species_counts.values()):,} occurrence records")


def main() -> None:
    args = parse_args()
    args.output_dir = args.output_dir.resolve()
    if not args.taxonomy_db.exists():
        raise SystemExit(
            f"Taxonomy cache not found: {args.taxonomy_db}\n"
            "First run scripts/gbif_prepare_taxonomy.py with a GBIF SPECIES_LIST download."
        )
    conn = sqlite3.connect(args.taxonomy_db)
    source_meta = taxonomy_metadata(conn)
    try:
        if args.kind in {"lakes", "both"}:
            build_kind("lakes", args, conn, source_meta)
        if args.kind in {"land", "both"}:
            build_kind("land", args, conn, source_meta)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
