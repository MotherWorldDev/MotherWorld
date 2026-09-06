#!/usr/bin/env python
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

from preprocess_ecoregions import (
    AREA_CRS,
    WGS84,
    find_mapshaper_executable,
    repair_geometry,
    run_mapshaper,
    sanitize_display_geometry_projected,
)


OPEN_OCEAN_ID = "marine_ocean_0"


def _load_layer(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    gdf["geometry"] = gdf.geometry.map(repair_geometry)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    return gdf


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    land_path = project_root / "frontend" / "public" / "data" / "lod0" / "ecoregions_lod0.topojson"
    marine_path = project_root / "frontend" / "public" / "data" / "marine" / "lod0" / "marine_ecoregions_lod0.topojson"
    index_path = project_root / "frontend" / "public" / "data" / "marine.index.json"
    if not land_path.exists():
        raise FileNotFoundError(f"Land layer not found: {land_path}")
    if not marine_path.exists():
        raise FileNotFoundError(f"Marine layer not found: {marine_path}")
    if not index_path.exists():
        raise FileNotFoundError(f"Marine index not found: {index_path}")

    print(f"Loading land display layer: {land_path}")
    land_gdf = _load_layer(land_path)
    print(f"Loading marine display layer: {marine_path}")
    marine_gdf = _load_layer(marine_path)
    if "id" in marine_gdf.columns:
        marine_gdf = marine_gdf[marine_gdf["id"] != OPEN_OCEAN_ID].copy()

    print("Computing synthetic open-ocean remainder ...")
    coverage_union = unary_union([*land_gdf.geometry.tolist(), *marine_gdf.geometry.tolist()])
    world_geom = box(-180.0, -89.999, 180.0, 89.999)
    ocean_geom = repair_geometry(world_geom.difference(coverage_union))
    if ocean_geom is None or ocean_geom.is_empty:
        raise RuntimeError("Synthetic open-ocean geometry resolved empty.")

    ocean_proj = gpd.GeoSeries([ocean_geom], crs=WGS84).to_crs(AREA_CRS)
    ocean_proj_geom = sanitize_display_geometry_projected(
        ocean_proj.iloc[0],
        min_hole_area_m2=500.0 * 1_000_000,
        min_fragment_area_m2=100.0 * 1_000_000,
    )
    if ocean_proj_geom is None or ocean_proj_geom.is_empty:
        raise RuntimeError("Synthetic open-ocean geometry was removed during sanitation.")
    ocean_geom = repair_geometry(gpd.GeoSeries([ocean_proj_geom], crs=AREA_CRS).to_crs(WGS84).iloc[0])
    if ocean_geom is None or ocean_geom.is_empty:
        raise RuntimeError("Synthetic open-ocean geometry became invalid after reprojection.")

    ocean_area_km2 = float(gpd.GeoSeries([ocean_geom], crs=WGS84).to_crs(AREA_CRS).area.iloc[0] / 1_000_000)
    ocean_center = gpd.GeoSeries([ocean_geom], crs=WGS84).representative_point().iloc[0]
    ocean_row = {
        "id": OPEN_OCEAN_ID,
        "name": "Open Ocean",
        "layerType": "OCEAN",
        "realm": "Global ocean",
        "realmSlug": "global-ocean",
        "province": "",
        "biome": "Open ocean",
        "ecoId": 0,
        "globalId": "SYNTHETIC_OPEN_OCEAN",
        "areaKm2": ocean_area_km2,
        "centerLon": float(ocean_center.x),
        "centerLat": float(ocean_center.y),
        "FID": 0,
        "geometry": ocean_geom,
    }
    ocean_gdf = gpd.GeoDataFrame([ocean_row], geometry="geometry", crs=WGS84)
    merged_gdf = gpd.GeoDataFrame(
        pd.concat([marine_gdf, ocean_gdf], ignore_index=True),
        geometry="geometry",
        crs=WGS84,
    )

    mapshaper_cmd = find_mapshaper_executable(project_root)
    with tempfile.TemporaryDirectory(prefix="open_ocean_build_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "marine_with_open_ocean.geojson"
        merged_gdf.to_file(tmp_geojson, driver="GeoJSON")
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(tmp_geojson),
                "-o",
                str(marine_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "no-quantization",
                "force",
            ],
        )

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    regions = payload.setdefault("regions", {})
    regions[OPEN_OCEAN_ID] = {
        "id": OPEN_OCEAN_ID,
        "ecoId": 0,
        "name": "Open Ocean",
        "biomeNum": None,
        "biome": "Open ocean",
        "realm": "Global ocean",
        "ecoBiomeCode": "OCEAN",
        "nnhCode": None,
        "nnhName": "",
        "areaKm2": ocean_area_km2,
        "center": {"lon": float(ocean_center.x), "lat": float(ocean_center.y)},
        "realmSlug": "global-ocean",
        "layerType": "OCEAN",
        "province": "",
        "globalId": "SYNTHETIC_OPEN_OCEAN",
        "isMarine": True,
        "isSynthetic": True,
    }
    stats = payload.setdefault("stats", {})
    stats["featureCount"] = len(regions)
    stats["syntheticCount"] = 1
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote marine layer with synthetic open ocean: {marine_path}")
    print(f"Wrote marine index: {index_path}")


if __name__ == "__main__":
    main()
