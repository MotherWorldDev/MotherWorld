#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import math
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

from biodiversity_common import (
    EQUAL_AREA,
    WGS84,
    discover_shapefiles,
    load_regions,
    normalize_category,
    project_geometry,
    utc_now_iso,
    write_provider_region,
)

PROVIDER = "iucn_ranges"
RISK_WEIGHTS = {"NT": 1.0, "VU": 2.0, "EN": 3.0, "CR": 4.0}


def infer_field(columns, candidates):
    lookup = {str(c).lower(): str(c) for c in columns}
    for c in candidates:
        if c.lower() in lookup:
            return lookup[c.lower()]
    return None


def load_category_map(path: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    by_name: dict[str, str] = {}
    by_id: dict[str, str] = {}
    if not path:
        return by_name, by_id
    df = pd.read_csv(path, low_memory=False)
    name_col = infer_field(df.columns, ("sci_name", "scientific_name", "scientificName", "binomial", "species"))
    id_col = infer_field(df.columns, ("id_no", "sis_id", "taxonid", "taxon_id"))
    cat_col = infer_field(df.columns, ("category", "redlist_category", "red_list_category", "rl_category", "iucn"))
    if not cat_col:
        raise RuntimeError(f"Could not find Red List category column in {path}; columns={list(df.columns)}")
    for _, row in df.iterrows():
        cat = normalize_category(row.get(cat_col))
        if not cat:
            continue
        if name_col and pd.notna(row.get(name_col)):
            by_name[str(row[name_col]).strip()] = cat
        if id_col and pd.notna(row.get(id_col)):
            by_id[str(row[id_col]).strip()] = cat
    return by_name, by_id


def parse_args():
    p = argparse.ArgumentParser(description="Build STAR-inspired range-weighted extinction-risk metrics from staged IUCN Red List range polygons.")
    p.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--ranges", type=Path, action="append", required=True, help="IUCN shapefile/ZIP/directory; repeatable by taxon group.")
    p.add_argument("--categories", type=Path, default=None, help="Optional assessment/species CSV containing sci_name/id and Red List category.")
    p.add_argument("--kind", action="append", choices=["land", "marine", "lakes"], default=[])
    p.add_argument("--region", action="append", default=[])
    p.add_argument("--range-restricted-threshold", type=float, default=0.25, help="Fraction of global mapped range inside one region.")
    return p.parse_args()


def main():
    args = parse_args()
    repo = args.repo.resolve()
    kinds = args.kind or ["land"]
    regions = load_regions(repo, kinds)
    if args.region:
        wanted = set(args.region)
        regions = [r for r in regions if r.region_id in wanted]
    if not regions:
        raise SystemExit("No MotherWorld regions selected.")

    region_gdf = gpd.GeoDataFrame(
        {"regionId": [r.region_id for r in regions], "kind": [r.kind for r in regions]},
        geometry=[project_geometry(r.geometry, WGS84, EQUAL_AREA) for r in regions],
        crs=EQUAL_AREA,
    )
    sindex = region_gdf.sindex
    name_categories, id_categories = load_category_map(args.categories)

    result = {
        r.region_id: {
            "counts": Counter(),
            "riskResponsibility": 0.0,
            "rangeResponsibility": 0.0,
            "rangeRestrictedThreatened": set(),
            "extinctMapped": set(),
            "speciesSeen": set(),
        }
        for r in regions
    }
    temp_handles = []
    processed_species = 0

    try:
        shapefiles = []
        for input_path in args.ranges:
            files, temps = discover_shapefiles(input_path)
            shapefiles.extend(files)
            temp_handles.extend(temps)
        if not shapefiles:
            raise SystemExit("No shapefiles found in --ranges inputs.")

        for shp_i, shp in enumerate(shapefiles, 1):
            print(f"[{shp_i}/{len(shapefiles)}] reading {shp}")
            gdf = gpd.read_file(shp)
            if gdf.empty:
                continue
            if gdf.crs is None:
                gdf = gdf.set_crs(WGS84)
            name_col = infer_field(gdf.columns, ("sci_name", "binomial", "scientific", "species"))
            id_col = infer_field(gdf.columns, ("id_no", "sis_id", "taxonid"))
            presence_col = infer_field(gdf.columns, ("presence",))
            origin_col = infer_field(gdf.columns, ("origin",))
            seasonal_col = infer_field(gdf.columns, ("seasonal",))
            cat_col = infer_field(gdf.columns, ("category", "rl_category", "redlist"))
            if not name_col:
                print(f"  skipping {shp.name}: no sci_name/binomial field")
                continue

            keep = gdf.geometry.notna() & ~gdf.geometry.is_empty
            if presence_col:
                # extant / possibly extinct for current responsibility, plus code 5 retained separately below
                pass
            gdf = gdf.loc[keep].copy()
            if gdf.empty:
                continue
            gdf = gdf.to_crs(EQUAL_AREA)

            # Process one species at a time so multiple polygons / seasons do not double-count.
            for species_name, group in gdf.groupby(name_col, sort=False):
                species_name = str(species_name).strip()
                if not species_name:
                    continue
                processed_species += 1
                id_value = str(group.iloc[0].get(id_col)).strip() if id_col and pd.notna(group.iloc[0].get(id_col)) else ""
                category = None
                if cat_col:
                    category = normalize_category(group.iloc[0].get(cat_col))
                category = category or name_categories.get(species_name) or (id_categories.get(id_value) if id_value else None)

                current = group
                if presence_col:
                    current = current[current[presence_col].isin([1, 4])]
                if origin_col:
                    current = current[current[origin_col].isin([1, 2, 6])]
                if seasonal_col:
                    current = current[current[seasonal_col].isin([1, 2, 3, 5])]

                if not current.empty:
                    species_geom = unary_union(list(current.geometry))
                    if species_geom is not None and not species_geom.is_empty:
                        total_area = float(species_geom.area)
                        if total_area > 0:
                            candidate_idx = list(sindex.query(species_geom, predicate="intersects"))
                            for idx in candidate_idx:
                                rid = region_gdf.iloc[idx]["regionId"]
                                inter = species_geom.intersection(region_gdf.iloc[idx].geometry)
                                if inter.is_empty:
                                    continue
                                frac = float(inter.area) / total_area
                                if frac <= 0:
                                    continue
                                rr = result[rid]
                                rr["speciesSeen"].add(species_name)
                                rr["rangeResponsibility"] += frac
                                if category in RISK_WEIGHTS:
                                    rr["counts"][category] += 1
                                    rr["riskResponsibility"] += RISK_WEIGHTS[category] * frac
                                    if frac >= args.range_restricted_threshold:
                                        rr["rangeRestrictedThreatened"].add(species_name)

                # Historical mapped post-1500 extinction evidence (IUCN presence code 5).
                if presence_col:
                    extinct = group[group[presence_col] == 5]
                    if origin_col:
                        extinct = extinct[extinct[origin_col].isin([1, 5])]
                    if not extinct.empty:
                        extinct_geom = unary_union(list(extinct.geometry))
                        if extinct_geom is not None and not extinct_geom.is_empty:
                            for idx in sindex.query(extinct_geom, predicate="intersects"):
                                rid = region_gdf.iloc[idx]["regionId"]
                                if not extinct_geom.intersection(region_gdf.iloc[idx].geometry).is_empty:
                                    result[rid]["extinctMapped"].add(species_name)

            print(f"  processed species so far: {processed_species:,}")

        kind_lookup = {r.region_id: r.kind for r in regions}
        for rid, rr in result.items():
            counts = {k: int(rr["counts"].get(k, 0)) for k in ("CR", "EN", "VU", "NT")}
            threatened = counts["CR"] + counts["EN"] + counts["VU"]
            payload = {
                "provider": PROVIDER,
                "regionId": rid,
                "regionKind": kind_lookup[rid],
                "generatedAt": utc_now_iso(),
                "metrics": {
                    "mappedSpeciesCount": len(rr["speciesSeen"]),
                    "threatenedSpeciesCount": threatened,
                    "nearThreatenedSpeciesCount": counts["NT"],
                    "categoryCounts": counts,
                    "extinctionRiskResponsibility": rr["riskResponsibility"],
                    "rangeResponsibility": rr["rangeResponsibility"],
                    "rangeRestrictedThreatenedCount": len(rr["rangeRestrictedThreatened"]),
                    "rangeRestrictedThreatenedSpecies": sorted(rr["rangeRestrictedThreatened"]),
                    "mappedPost1500ExtinctionCount": len(rr["extinctMapped"]),
                    "mappedPost1500ExtinctSpecies": sorted(rr["extinctMapped"]),
                },
                "source": {
                    "label": "IUCN Red List spatial range polygons",
                    "license": "IUCN Red List spatial data terms — non-commercial use",
                    "licenseClass": "restricted_noncommercial",
                    "removableProvider": True,
                    "method": "MotherWorld STAR-inspired range-weighted responsibility; not official STAR",
                    "riskWeights": RISK_WEIGHTS,
                    "presenceFilter": [1, 4],
                    "originFilter": [1, 2, 6],
                    "seasonalityFilter": [1, 2, 3, 5],
                    "extinctionEvidencePresenceCode": 5,
                },
            }
            write_provider_region(repo, PROVIDER, kind_lookup[rid], rid, payload)
        print(f"Done. Processed approximately {processed_species:,} species groups across {len(shapefiles)} shapefile(s).")
    finally:
        for temp in temp_handles:
            temp.cleanup()


if __name__ == "__main__":
    main()
