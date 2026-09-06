#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.ops import unary_union

from preprocess_ecoregions import (
    AREA_CRS,
    WGS84,
    find_mapshaper_executable,
    repair_geometry,
    run_mapshaper,
    sanitize_final_topojson_file,
    slugify,
)


LAKE_SPECS = [
    {"id": "lake_caspian_sea", "name": "Caspian Sea", "source_ids": [1]},
    {"id": "lake_superior", "name": "Lake Superior", "source_ids": [5]},
    {"id": "lake_michigan", "name": "Lake Michigan", "source_ids": [6]},
    {"id": "lake_ontario", "name": "Lake Ontario", "source_ids": [7]},
    {"id": "lake_huron", "name": "Lake Huron", "source_ids": [8]},
    {"id": "lake_erie", "name": "Lake Erie", "source_ids": [9]},
    {"id": "lake_ladoga", "name": "Lake Ladoga", "source_ids": [10]},
    {"id": "lake_baikal", "name": "Lake Baikal", "source_ids": [11]},
    {"id": "lake_balkhash", "name": "Lake Balkhash", "source_ids": [12]},
    {"id": "lake_aral_sea", "name": "Aral Sea", "source_ids": [13, 130]},
    {"id": "lake_victoria", "name": "Lake Victoria", "source_ids": [16]},
    {"id": "lake_tanganyika", "name": "Lake Tanganyika", "source_ids": [17]},
    {"id": "lake_malawi", "name": "Lake Malawi", "source_ids": [18]},
    {"id": "lake_nicaragua", "name": "Lake Nicaragua", "source_ids": [72]},
    {"id": "lake_titicaca", "name": "Lake Titicaca", "source_ids": [78]},
    {"id": "lake_onega", "name": "Lake Onega", "source_ids": [100]},
    {"id": "lake_vanern", "name": "Lake Vanern", "source_ids": [105]},
    {"id": "lake_issyk_kul", "name": "Lake Issyk Kul", "source_ids": [136]},
    {"id": "lake_turkana", "name": "Lake Turkana", "source_ids": [158]},
    {"id": "lake_albert", "name": "Lake Albert", "source_ids": [159]},
    {"id": "lake_kivu", "name": "Lake Kivu", "source_ids": [163]},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess selected HydroLAKES polygons into simplified web LOD assets + metadata index."
    )
    parser.add_argument("--lod-overview-retain-pct", type=float, default=3.0)
    parser.add_argument("--lod0-retain-pct", type=float, default=8.0)
    parser.add_argument("--hole-min-km2", type=float, default=2.0)
    parser.add_argument("--fragment-min-km2", type=float, default=0.5)
    parser.add_argument("--sliver-min-km2", type=float, default=0.2)
    return parser.parse_args()


def load_selected_hydrolakes(source_path: Path) -> gpd.GeoDataFrame:
    wanted_ids = sorted({lake_id for spec in LAKE_SPECS for lake_id in spec["source_ids"]})
    where = f"Hylak_id IN ({','.join(str(v) for v in wanted_ids)})"
    gdf = pyogrio.read_dataframe(
        source_path,
        where=where,
        columns=["Hylak_id", "Lake_name", "Country", "Continent", "Lake_area"],
    )
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    return gdf


def build_logical_lakes(source_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    records: list[dict] = []
    source_by_id = {int(row.Hylak_id): row for row in source_gdf.itertuples(index=False)}

    for spec in LAKE_SPECS:
        rows = [source_by_id[lake_id] for lake_id in spec["source_ids"] if lake_id in source_by_id]
        if not rows:
            raise RuntimeError(f"Missing HydroLAKES rows for {spec['name']} ({spec['source_ids']})")

        parts = [repair_geometry(row.geometry) for row in rows]
        parts = [geom for geom in parts if geom is not None and not geom.is_empty]
        if not parts:
            raise RuntimeError(f"All geometries collapsed for {spec['name']}")

        geometry = repair_geometry(unary_union(parts))
        if geometry is None or geometry.is_empty:
            raise RuntimeError(f"Failed to union geometry for {spec['name']}")

        area_km2 = float(gpd.GeoSeries([geometry], crs=WGS84).to_crs(AREA_CRS).area.iloc[0] / 1_000_000)
        rep = gpd.GeoSeries([geometry], crs=WGS84).representative_point().iloc[0]
        countries = sorted({str(row.Country).strip() for row in rows if str(row.Country).strip()})
        continents = sorted({str(row.Continent).strip() for row in rows if str(row.Continent).strip()})
        raw_names = sorted({str(row.Lake_name).strip() for row in rows if str(row.Lake_name).strip()})
        eco_id = "|".join(str(v) for v in spec["source_ids"])

        records.append(
            {
                "id": spec["id"],
                "ecoId": eco_id,
                "name": spec["name"],
                "biome": "Lake system",
                "biomeNum": None,
                "realm": continents[0] if len(continents) == 1 else "Multi-continent",
                "ecoBiomeCode": "LAKE",
                "nnhCode": None,
                "nnhName": ", ".join(countries) if countries else "HydroLAKES target lake",
                "layerType": "lake",
                "country": ", ".join(countries),
                "continent": ", ".join(continents),
                "sourceLakeName": " | ".join(raw_names),
                "areaKm2": round(area_km2, 2),
                "centerLon": round(float(rep.x), 6),
                "centerLat": round(float(rep.y), 6),
                "isMarine": False,
                "isLake": True,
                "geometry": geometry,
            }
        )

    return gpd.GeoDataFrame(records, geometry="geometry", crs=WGS84)


def build_topojson_lod(
    project_root: Path,
    mapshaper_cmd: list[str],
    clean_geojson: Path,
    out_path: Path,
    retain_pct: float,
    hole_min_km2: float,
    fragment_min_km2: float,
    sliver_min_km2: float,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_mapshaper(
        project_root,
        mapshaper_cmd,
        [
            str(clean_geojson),
            "-simplify",
            f"{retain_pct}%",
            "weighted",
            "keep-shapes",
            "-o",
            str(out_path),
            "format=topojson",
            "id-field=id",
            "bbox",
            "no-quantization",
            "force",
        ],
    )
    sanitize_final_topojson_file(
        project_root,
        out_path,
        mapshaper_cmd,
        hole_min_km2=hole_min_km2,
        fragment_min_km2=fragment_min_km2,
        sliver_min_km2=sliver_min_km2,
    )


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    source_path = (
        project_root
        / "HydroLAKES_polys_v10_shp"
        / "HydroLAKES_polys_v10_shp"
        / "HydroLAKES_polys_v10.shp"
    )
    if not source_path.exists():
        raise FileNotFoundError(f"HydroLAKES shapefile not found: {source_path}")

    print(f"Loading {source_path} ...")
    source_gdf = load_selected_hydrolakes(source_path)
    lakes_gdf = build_logical_lakes(source_gdf)
    print(f"Selected {len(lakes_gdf)} logical lake regions from {len(source_gdf)} raw HydroLAKES polygons")

    mapshaper_cmd = find_mapshaper_executable(project_root)
    clean_dir = Path(tempfile.mkdtemp(prefix="lakes_preprocess_", dir=project_root))
    clean_geojson = clean_dir / "lakes_clean.geojson"
    lakes_gdf.to_file(clean_geojson, driver="GeoJSON")

    overview_path = project_root / "frontend" / "public" / "data" / "lakes" / "lod_overview" / "lakes_overview.topojson"
    lod0_path = project_root / "frontend" / "public" / "data" / "lakes" / "lod0" / "lakes_lod0.topojson"

    build_topojson_lod(
        project_root,
        mapshaper_cmd,
        clean_geojson,
        overview_path,
        args.lod_overview_retain_pct,
        args.hole_min_km2,
        args.fragment_min_km2,
        args.sliver_min_km2,
    )
    build_topojson_lod(
        project_root,
        mapshaper_cmd,
        clean_geojson,
        lod0_path,
        args.lod0_retain_pct,
        args.hole_min_km2,
        args.fragment_min_km2,
        args.sliver_min_km2,
    )

    logical_regions = {}
    for row in lakes_gdf.drop(columns="geometry").to_dict(orient="records"):
        logical_regions[row["id"]] = {
            "id": row["id"],
            "ecoId": row["ecoId"],
            "name": row["name"],
            "biomeNum": row["biomeNum"],
            "biome": row["biome"],
            "realm": row["realm"],
            "ecoBiomeCode": row["ecoBiomeCode"],
            "nnhCode": row["nnhCode"],
            "nnhName": row["nnhName"],
            "layerType": row["layerType"],
            "areaKm2": row["areaKm2"],
            "center": {"lon": row["centerLon"], "lat": row["centerLat"]},
            "country": row["country"],
            "continent": row["continent"],
            "isMarine": False,
            "isLake": True,
            "climateSummary": "Large inland water body tracked from HydroLAKES. This layer fills major lacustrine gaps that are outside the land and marine ecoregion polygons.",
            "tertiarySummary": "Lake polygons are routed after land and MEOW marine regions, but before the synthetic open-ocean fallback. They are intended as a pragmatic inland-water coverage layer, not a biome source.",
            "plotsSummary": "Lake summaries do not have dedicated plots yet. The geometry is simplified into a startup and overview LOD for fast global rendering.",
        }

    display_gdf = gpd.read_file(lod0_path)
    metadata = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "HydroLAKES_polys_v10",
        "stats": {
            "featureCount": len(logical_regions),
            "displayFeatureCount": len(display_gdf),
        },
        "geometry": {
            "overviewLod": {
                "level": "overview",
                "url": "lakes/lod_overview/lakes_overview.topojson",
                "retainPct": args.lod_overview_retain_pct,
            },
            "startupLod": {
                "level": "global",
                "url": "lakes/lod0/lakes_lod0.topojson",
                "retainPct": args.lod0_retain_pct,
            },
        },
        "regions": logical_regions,
        "palette": {
            "type": "fixed",
            "default": "#38bdf8",
        },
    }

    index_path = project_root / "frontend" / "public" / "data" / "lakes.index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Wrote {overview_path.relative_to(project_root)}")
    print(f"Wrote {lod0_path.relative_to(project_root)}")
    print(f"Wrote {index_path.relative_to(project_root)}")


if __name__ == "__main__":
    main()
