#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box
from shapely.ops import split, unary_union

from preprocess_ecoregions import (
    AREA_CRS,
    WGS84,
    find_mapshaper_executable,
    repair_geometry,
    run_mapshaper,
    sanitize_display_geometry_projected,
    sanitize_final_topojson_file,
    slugify,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess Marine Ecoregions of the World into a simplified web layer + metadata index."
    )
    parser.add_argument(
        "--include-open-ocean",
        action="store_true",
        help="Add a synthetic open-ocean remainder region. Disabled by default because it is better built as a separate fast postprocess step.",
    )
    parser.add_argument("--lod-overview-retain-pct", type=float, default=2.0)
    parser.add_argument("--lod0-retain-pct", type=float, default=4.0)
    parser.add_argument(
        "--land-clip-buffer-km",
        type=float,
        default=0.0,
        help="Optional expansion of the terrestrial clip mask before subtracting it from the final marine display geometry. Defaults to exact subtraction (0 km).",
    )
    parser.add_argument("--lod0-hole-min-km2", type=float, default=25.0)
    parser.add_argument("--lod0-fragment-min-km2", type=float, default=8.0)
    parser.add_argument("--lod0-sliver-min-km2", type=float, default=3.0)
    return parser.parse_args()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _display_name(row: dict[str, Any]) -> str:
    ecoregion = _clean_text(row.get("ECOREGION"))
    province = _clean_text(row.get("PROVINCE"))
    if ecoregion:
        return ecoregion
    return province or "Marine region"


def _display_biome(row: dict[str, Any]) -> str:
    if _clean_text(row.get("ECOREGION")).lower() == "open ocean":
        return "Open ocean"
    lat_zone = _clean_text(row.get("Lat_Zone"))
    if lat_zone:
        return f"{lat_zone} marine ecoregion"
    return "Marine ecoregion"


def _clean_number(value: Any) -> int | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


ANTIMERIDIAN_SPLITTER = LineString([(180.0, -90.0), (180.0, 90.0)])
ANTIMERIDIAN_EPSILON_DEG = 0.01
ANTIMERIDIAN_EXACT_THRESHOLD_DEG = 1e-6
DATELINE_SLIVER_MIN_KM2 = 5.0


def _shift_ring_longitudes(coords, mode: str):
    shifted = []
    for coord in coords:
        x, y = coord[0], coord[1]
        if mode == "to_positive" and x < 0:
            x += 360.0
        elif mode == "to_wgs84" and x > 180.0:
            x -= 360.0
        shifted.append((x, y))
    return shifted


def _shift_polygon_longitudes(poly: Polygon, mode: str, *, right_side: bool = False) -> Polygon:
    def _shift(coords):
        shifted = []
        for coord in coords:
            x, y = coord[0], coord[1]
            if mode == "to_positive" and x < 0:
                x += 360.0
            elif mode == "to_wgs84":
                if right_side:
                    if x >= 180.0:
                        x -= 360.0
                elif x > 180.0:
                    x -= 360.0
            shifted.append((x, y))
        return shifted

    return Polygon(
        _shift(poly.exterior.coords),
        [_shift(ring.coords) for ring in poly.interiors],
    )


def _polygon_parts(geom) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [poly for poly in geom.geoms if not poly.is_empty]
    if isinstance(geom, GeometryCollection):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(_polygon_parts(part))
        return out
    return []


def _ring_has_antimeridian_jump(coords) -> bool:
    xs = [pt[0] for pt in coords]
    return any(abs(curr - prev) > 180.0 for prev, curr in zip(xs, xs[1:]))


def _geometry_has_antimeridian_jump(geom) -> bool:
    for poly in _polygon_parts(geom):
        if _ring_has_antimeridian_jump(poly.exterior.coords):
            return True
        for ring in poly.interiors:
            if _ring_has_antimeridian_jump(ring.coords):
                return True
    return False


def _geometry_touches_antimeridian(geom, threshold_deg: float = 0.25) -> bool:
    for poly in _polygon_parts(geom):
        for x, _ in poly.exterior.coords:
            if abs(abs(x) - 180.0) <= threshold_deg:
                return True
        for ring in poly.interiors:
            for x, _ in ring.coords:
                if abs(abs(x) - 180.0) <= threshold_deg:
                    return True
    return False


def _geometry_spans_dateline_window(geom, edge_threshold_deg: float = 170.0) -> bool:
    parts = _polygon_parts(geom)
    if not parts:
        return False
    min_x = min(part.bounds[0] for part in parts)
    max_x = max(part.bounds[2] for part in parts)
    return min_x <= -edge_threshold_deg and max_x >= edge_threshold_deg


def _nudge_ring_off_antimeridian(coords):
    nudged = []
    for coord in coords:
        x, y = coord[0], coord[1]
        if abs(x - 180.0) <= ANTIMERIDIAN_EXACT_THRESHOLD_DEG:
            x = 180.0 - ANTIMERIDIAN_EPSILON_DEG
        elif abs(x + 180.0) <= ANTIMERIDIAN_EXACT_THRESHOLD_DEG:
            x = -180.0 + ANTIMERIDIAN_EPSILON_DEG
        nudged.append((x, y))
    return nudged


def nudge_antimeridian_vertices(geom):
    geom = repair_geometry(geom)
    if geom is None or geom.is_empty:
        return None
    parts = []
    for poly in _polygon_parts(geom):
        nudged = Polygon(
            _nudge_ring_off_antimeridian(poly.exterior.coords),
            [_nudge_ring_off_antimeridian(ring.coords) for ring in poly.interiors],
        )
        nudged = repair_geometry(nudged)
        if nudged is None or nudged.is_empty:
            continue
        parts.extend(_polygon_parts(nudged))
    if not parts:
        return geom
    if len(parts) == 1:
        return repair_geometry(parts[0])
    return repair_geometry(MultiPolygon(parts))


def _projected_area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    return float(gpd.GeoSeries([geom], crs=WGS84).to_crs(AREA_CRS).area.iloc[0] / 1_000_000)


def filter_tiny_dateline_parts(geom):
    geom = repair_geometry(geom)
    if geom is None or geom.is_empty:
        return None
    parts = []
    for part in _polygon_parts(geom):
        if _geometry_touches_antimeridian(part, 0.35) and _projected_area_km2(part) < DATELINE_SLIVER_MIN_KM2:
            continue
        parts.append(part)
    if not parts:
        return None
    if len(parts) == 1:
        return repair_geometry(parts[0])
    return repair_geometry(MultiPolygon(parts))


def explode_dateline_touching_source_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    expanded_records: list[dict[str, Any]] = []
    split_count = 0
    for record in gdf.to_dict(orient="records"):
        geom = record.get("geometry")
        if geom is None or geom.is_empty:
            continue
        if _geometry_has_antimeridian_jump(geom) or _geometry_touches_antimeridian(geom, 0.25):
            split_count += 1
            repaired = filter_tiny_dateline_parts(nudge_antimeridian_vertices(split_antimeridian_geometry(geom)))
            if repaired is None or repaired.is_empty:
                continue
            parts = _polygon_parts(repaired)
            if len(parts) > 1:
                for part in parts:
                    next_record = dict(record)
                    next_record["geometry"] = part
                    expanded_records.append(next_record)
                continue
            record["geometry"] = repaired
        expanded_records.append(record)

    if split_count:
        print(f"Pre-splitting {split_count} dateline-adjacent marine source geometries before simplify ...")
    return gpd.GeoDataFrame(expanded_records, geometry="geometry", crs=gdf.crs or WGS84)


def split_antimeridian_geometry(geom):
    geom = repair_geometry(geom)
    if geom is None or geom.is_empty:
        return None
    if not _geometry_has_antimeridian_jump(geom) and not (
        _geometry_touches_antimeridian(geom, 0.25) and _geometry_spans_dateline_window(geom, 170.0)
    ):
        return geom

    shifted_parts = [_shift_polygon_longitudes(poly, "to_positive") for poly in _polygon_parts(geom)]
    pieces: list[Polygon] = []
    for poly in shifted_parts:
        try:
            split_result = split(poly, ANTIMERIDIAN_SPLITTER)
            split_parts = _polygon_parts(split_result)
        except Exception:
            split_parts = [poly]
        if not split_parts:
            split_parts = [poly]
        for part in split_parts:
            is_right_side = part.bounds[0] >= 180.0 or part.representative_point().x > 180.0
            wrapped = repair_geometry(_shift_polygon_longitudes(part, "to_wgs84", right_side=is_right_side))
            if wrapped is None or wrapped.is_empty:
                continue
            pieces.extend(_polygon_parts(wrapped))

    if not pieces:
        return geom
    if len(pieces) == 1:
        return repair_geometry(pieces[0])
    return repair_geometry(MultiPolygon(pieces))


def repair_antimeridian_in_final_topojson(
    project_root: Path,
    topo_path: Path,
    mapshaper_cmd: list[str],
) -> None:
    gdf = gpd.read_file(topo_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    crossing_count = int(gdf.geometry.map(_geometry_has_antimeridian_jump).sum())
    touching_count = int(gdf.geometry.map(_geometry_touches_antimeridian).sum())
    if crossing_count == 0 and touching_count == 0:
        return

    print(f"Repairing {crossing_count} antimeridian-crossing marine display geometries after simplification ...")
    gdf["geometry"] = (
        gdf.geometry
        .map(split_antimeridian_geometry)
        .map(nudge_antimeridian_vertices)
        .map(filter_tiny_dateline_parts)
    )
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()

    with tempfile.TemporaryDirectory(prefix="marine_antimeridian_fix_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "marine_antimeridian_fixed.geojson"
        gdf.to_file(tmp_geojson, driver="GeoJSON")
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(tmp_geojson),
                "-o",
                str(topo_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "no-quantization",
                "force",
            ],
        )


def _build_open_ocean_source_row(
    project_root: Path,
    marine_gdf: gpd.GeoDataFrame,
) -> dict[str, Any] | None:
    land_union = load_terrestrial_coverage_union(project_root, log_prefix="open-ocean mask")
    if land_union is None or land_union.is_empty:
        print("Warning: terrestrial coverage resolved to zero valid geometries, skipping synthetic open ocean region.")
        return None

    print("Computing synthetic open-ocean geometry ...")
    coverage_parts = [*marine_gdf.geometry.tolist(), land_union]
    coverage_union = unary_union([geom for geom in coverage_parts if geom is not None and not geom.is_empty])
    world_geom = box(-180.0, -89.999, 180.0, 89.999)
    open_ocean_geom = repair_geometry(world_geom.difference(coverage_union))
    if open_ocean_geom is None or open_ocean_geom.is_empty:
        print("Warning: synthetic open-ocean geometry resolved empty, skipping.")
        return None

    open_ocean_proj = gpd.GeoSeries([open_ocean_geom], crs=WGS84).to_crs(AREA_CRS)
    sanitized_proj = sanitize_display_geometry_projected(
        open_ocean_proj.iloc[0],
        min_hole_area_m2=500.0 * 1_000_000,
        min_fragment_area_m2=100.0 * 1_000_000,
    )
    if sanitized_proj is None or sanitized_proj.is_empty:
        print("Warning: synthetic open-ocean geometry was removed during sanitation, skipping.")
        return None

    sanitized_series = gpd.GeoSeries([sanitized_proj], crs=AREA_CRS).to_crs(WGS84)
    open_ocean_geom = repair_geometry(sanitized_series.iloc[0])
    if open_ocean_geom is None or open_ocean_geom.is_empty:
        print("Warning: synthetic open-ocean geometry became invalid after reprojection, skipping.")
        return None

    return {
        "ECO_CODE": 0,
        "ECOREGION": "Open Ocean",
        "PROV_CODE": None,
        "PROVINCE": "",
        "RLM_CODE": None,
        "REALM": "Global ocean",
        "ALT_CODE": None,
        "ECO_CODE_X": None,
        "Lat_Zone": "Global",
        "ORIG_FID": 0,
        "geometry": open_ocean_geom,
    }


def load_terrestrial_coverage_union(
    project_root: Path,
    *,
    log_prefix: str = "marine land-clip",
    clip_buffer_km: float = 0.0,
):
    lod1_dir = project_root / "frontend" / "public" / "data" / "lod1_realms"
    lod1_paths = sorted(lod1_dir.glob("*.topojson")) if lod1_dir.exists() else []
    if lod1_paths:
        print(f"Loading terrestrial coverage for {log_prefix}: {lod1_dir} ({len(lod1_paths)} realm files) ...")
        land_frames = [gpd.read_file(path) for path in lod1_paths]
        land_gdf = gpd.GeoDataFrame(
            pd.concat(land_frames, ignore_index=True),
            geometry="geometry",
            crs=land_frames[0].crs or WGS84,
        )
    else:
        land_src_path = project_root / "frontend" / "public" / "data" / "lod0" / "ecoregions_lod0.topojson"
        if not land_src_path.exists():
            land_src_path = project_root / "Ecoregions2017" / "Ecoregions2017.shp"
        if not land_src_path.exists():
            print(f"Warning: terrestrial source dataset not found, skipping {log_prefix}: {land_src_path}")
            return None

        print(f"Loading terrestrial coverage for {log_prefix}: {land_src_path} ...")
        if land_src_path.suffix.lower() == ".topojson":
            land_gdf = gpd.read_file(land_src_path)
        else:
            land_gdf = pyogrio.read_dataframe(land_src_path)
    if land_gdf.crs is None:
        land_gdf = land_gdf.set_crs(WGS84)
    else:
        land_gdf = land_gdf.to_crs(WGS84)

    land_gdf["geometry"] = land_gdf.geometry.map(repair_geometry)
    land_gdf = land_gdf[land_gdf.geometry.notna() & ~land_gdf.geometry.is_empty].copy()
    if land_gdf.empty:
        return None
    land_union = repair_geometry(unary_union(land_gdf.geometry.tolist()))
    if land_union is None or land_union.is_empty:
        return None
    if clip_buffer_km > 0:
        print(f"Expanding terrestrial clip mask by {clip_buffer_km:.1f} km ...")
        buffered_proj = gpd.GeoSeries([land_union], crs=WGS84).to_crs(AREA_CRS).buffer(clip_buffer_km * 1_000.0)
        land_union = gpd.GeoSeries(buffered_proj, crs=AREA_CRS).to_crs(WGS84).iloc[0]
        try:
            land_union = repair_geometry(land_union)
        except Exception:
            land_union = land_union.buffer(0)
    return land_union


def subtract_terrestrial_overlap(
    gdf: gpd.GeoDataFrame,
    land_union,
) -> gpd.GeoDataFrame:
    if land_union is None or land_union.is_empty or gdf.empty:
        return gdf

    print("Clipping marine source geometries against terrestrial coverage ...")
    changed_count = 0
    kept_records: list[dict[str, Any]] = []
    for record in gdf.to_dict(orient="records"):
        geom = record.get("geometry")
        if geom is None or geom.is_empty:
            continue
        clipped = repair_geometry(geom.difference(land_union))
        if clipped is None or clipped.is_empty:
            changed_count += 1
            continue
        if not clipped.equals(geom):
            changed_count += 1
        record["geometry"] = clipped
        kept_records.append(record)

    if changed_count:
        print(f"Clipped terrestrial overlap out of {changed_count} marine source geometries.")
    return gpd.GeoDataFrame(kept_records, geometry="geometry", crs=gdf.crs or WGS84)


def subtract_terrestrial_overlap_in_final_topojson(
    project_root: Path,
    topo_path: Path,
    mapshaper_cmd: list[str],
    *,
    clip_buffer_km: float = 0.0,
) -> None:
    land_union = load_terrestrial_coverage_union(
        project_root,
        log_prefix="final marine overlap clip",
        clip_buffer_km=max(0.0, clip_buffer_km),
    )
    if land_union is None or land_union.is_empty:
        return

    gdf = gpd.read_file(topo_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    changed_count = 0
    kept_records: list[dict[str, Any]] = []
    for record in gdf.to_dict(orient="records"):
        geom = record.get("geometry")
        if geom is None or geom.is_empty:
            continue
        clipped = repair_geometry(geom.difference(land_union))
        clipped = split_antimeridian_geometry(clipped) if clipped is not None else None
        clipped = filter_tiny_dateline_parts(clipped) if clipped is not None else None
        if clipped is None or clipped.is_empty:
            changed_count += 1
            continue
        if not clipped.equals(geom):
            changed_count += 1
        if isinstance(clipped, MultiPolygon) and (
            _geometry_touches_antimeridian(clipped, 0.25) or _geometry_spans_dateline_window(clipped, 170.0)
        ):
            for part_index, part in enumerate(_polygon_parts(clipped), start=1):
                next_record = dict(record)
                next_record["id"] = f"{record['id']}__p{part_index}"
                next_record["geometry"] = part
                kept_records.append(next_record)
            continue
        record["geometry"] = clipped
        kept_records.append(record)

    if changed_count == 0:
        return

    print(f"Subtracting exact terrestrial overlap from {changed_count} marine display geometries ...")
    gdf = gpd.GeoDataFrame(kept_records, geometry="geometry", crs=WGS84)
    with tempfile.TemporaryDirectory(prefix="marine_land_overlap_fix_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "marine_land_overlap_fixed.geojson"
        gdf.to_file(tmp_geojson, driver="GeoJSON")
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(tmp_geojson),
                "-o",
                str(topo_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "no-quantization",
                "force",
            ],
        )


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    src_path = (
        project_root
        / "Marine Ecoregions of the World"
        / "data"
        / "commondata"
        / "data0"
        / "meow_ecos_expl_clipped_expl.shp"
    )
    if not src_path.exists():
        raise FileNotFoundError(f"Marine source dataset not found: {src_path}")

    out_dir = project_root / "frontend" / "public" / "data"
    marine_dir = out_dir / "marine"
    overview_dir = marine_dir / "lod_overview"
    lod0_dir = marine_dir / "lod0"
    overview_dir.mkdir(parents=True, exist_ok=True)
    lod0_dir.mkdir(parents=True, exist_ok=True)

    mapshaper_cmd = find_mapshaper_executable(project_root)

    print(f"Loading {src_path} ...")
    gdf = pyogrio.read_dataframe(
        src_path,
        columns=[
            "ECO_CODE",
            "ECOREGION",
            "PROV_CODE",
            "PROVINCE",
            "RLM_CODE",
            "REALM",
            "ALT_CODE",
            "ECO_CODE_X",
            "Lat_Zone",
            "ORIG_FID",
        ],
    )
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)

    gdf["geometry"] = gdf.geometry.map(repair_geometry)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if gdf.empty:
        raise RuntimeError("Marine dataset resolved to zero valid polygon features after repair.")
    gdf["layerType"] = "MEOW"
    gdf = explode_dateline_touching_source_geometries(gdf)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    open_ocean_row = _build_open_ocean_source_row(project_root, gdf) if args.include_open_ocean else None
    if open_ocean_row:
        gdf = gpd.GeoDataFrame(
            pd.concat([gdf.drop(columns=["layerType"]), gpd.GeoDataFrame([open_ocean_row], geometry="geometry", crs=WGS84)]),
            geometry="geometry",
            crs=WGS84,
        )
    gdf["layerType"] = gdf.apply(
        lambda row: "OCEAN"
        if _clean_text(row.get("ECOREGION")).lower() == "open ocean"
        else "MEOW",
        axis=1,
    )
    gdf["name"] = gdf.apply(lambda row: _display_name(row), axis=1)
    gdf["biomeLabel"] = gdf.apply(lambda row: _display_biome(row), axis=1)
    gdf["province"] = gdf["PROVINCE"].map(_clean_text)
    gdf["realmName"] = gdf["REALM"].map(_clean_text)
    gdf["realmSlug"] = gdf["realmName"].map(slugify)
    gdf["ecoCode"] = gdf["ECO_CODE"].map(_clean_number)
    gdf["origFid"] = gdf["ORIG_FID"].map(_clean_number)
    gdf["altCode"] = gdf["ALT_CODE"].map(_clean_number)
    gdf["provinceCode"] = gdf["PROV_CODE"].map(_clean_number)
    gdf["realmCode"] = gdf["RLM_CODE"].map(_clean_number)
    gdf["ecoCodeX"] = gdf["ECO_CODE_X"].map(_clean_number)
    gdf["latZone"] = gdf["Lat_Zone"].map(_clean_text)
    gdf["region_id"] = gdf.apply(
        lambda row: f"marine_meow_{row['ecoCode'] or row['origFid'] or 0}"
        if row["layerType"] == "MEOW"
        else "open_ocean",
        axis=1,
    )
    gdf = gdf.reset_index(drop=True)
    gdf["display_id"] = [f"{region_id}__{i}" for i, region_id in enumerate(gdf["region_id"], start=1)]

    gdf_proj = gdf.to_crs(AREA_CRS)
    gdf["areaKm2"] = gdf_proj.geometry.area / 1_000_000

    representative_points = gdf.geometry.representative_point()
    gdf["centerLon"] = representative_points.x
    gdf["centerLat"] = representative_points.y

    export_gdf = gdf[
        [
            "display_id",
            "region_id",
            "name",
            "layerType",
            "realmName",
            "realmSlug",
            "province",
            "biomeLabel",
            "ecoCode",
            "altCode",
            "origFid",
            "provinceCode",
            "realmCode",
            "ecoCodeX",
            "latZone",
            "areaKm2",
            "centerLon",
            "centerLat",
            "geometry",
        ]
    ].copy()
    export_gdf = export_gdf.rename(
        columns={
            "display_id": "id",
            "region_id": "regionId",
            "name": "name",
            "layerType": "layerType",
            "realmName": "realm",
            "province": "province",
            "biomeLabel": "biome",
            "ecoCode": "ecoId",
            "areaKm2": "areaKm2",
            "centerLon": "centerLon",
            "centerLat": "centerLat",
        }
    )

    def build_marine_output_lod(out_path: Path, retain_pct: float) -> None:
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(tmp_geojson),
                "-simplify",
                f"{retain_pct}%",
                "weighted",
                "keep-shapes",
                "-o",
                str(out_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "force",
            ],
        )
        try:
            sanitize_final_topojson_file(
                project_root,
                out_path,
                mapshaper_cmd,
                hole_min_km2=args.lod0_hole_min_km2,
                fragment_min_km2=args.lod0_fragment_min_km2,
                sliver_min_km2=args.lod0_sliver_min_km2,
            )
        except Exception as exc:
            print(f"Warning: final Python sanitation pass failed, keeping mapshaper output as-is: {exc}")
        subtract_terrestrial_overlap_in_final_topojson(
            project_root,
            out_path,
            mapshaper_cmd,
            clip_buffer_km=max(0.0, float(args.land_clip_buffer_km)),
        )
        repair_antimeridian_in_final_topojson(project_root, out_path, mapshaper_cmd)

    with tempfile.TemporaryDirectory(prefix="marine_prep_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "marine_clean.geojson"
        export_gdf.to_file(tmp_geojson, driver="GeoJSON")

        overview_path = overview_dir / "marine_ecoregions_overview.topojson"
        lod0_path = lod0_dir / "marine_ecoregions_lod0.topojson"
        build_marine_output_lod(overview_path, float(args.lod_overview_retain_pct))
        build_marine_output_lod(lod0_path, float(args.lod0_retain_pct))

    regions_index: dict[str, dict[str, Any]] = {}
    for row in export_gdf.itertuples(index=False):
        regions_index[row.regionId] = {
            "id": row.regionId,
            "ecoId": int(row.ecoId) if row.ecoId is not None else None,
            "name": row.name,
            "biomeNum": None,
            "biome": row.biome,
            "realm": row.realm,
            "ecoBiomeCode": row.layerType,
            "nnhCode": None,
            "nnhName": row.province,
            "areaKm2": float(row.areaKm2),
            "center": {"lon": float(row.centerLon), "lat": float(row.centerLat)},
            "realmSlug": row.realmSlug,
            "layerType": row.layerType,
            "province": row.province,
            "altCode": int(row.altCode) if row.altCode is not None else None,
            "origFid": int(row.origFid) if row.origFid is not None else None,
            "provinceCode": int(row.provinceCode) if row.provinceCode is not None else None,
            "realmCode": int(row.realmCode) if row.realmCode is not None else None,
            "ecoCodeX": int(row.ecoCodeX) if row.ecoCodeX is not None else None,
            "latZone": row.latZone,
            "isMarine": True,
        }

    logical_meow_count = sum(1 for record in regions_index.values() if record["layerType"] == "MEOW")
    synthetic_count = sum(1 for record in regions_index.values() if record["layerType"] == "OCEAN")
    dataset_label = "Marine Ecoregions of the World"
    index_payload = {
        "dataset": dataset_label,
        "stats": {
            "featureCount": len(regions_index),
            "displayFeatureCount": len(export_gdf),
            "meowCount": logical_meow_count,
            "syntheticCount": synthetic_count,
        },
        "geometry": {
            "overviewLod": {
                "level": "overview",
                "url": "marine/lod_overview/marine_ecoregions_overview.topojson",
                "retainPct": float(args.lod_overview_retain_pct),
            },
            "startupLod": {
                "level": "lod0",
                "url": "marine/lod0/marine_ecoregions_lod0.topojson",
                "retainPct": float(args.lod0_retain_pct),
            }
        },
        "regions": regions_index,
    }

    index_path = out_dir / "marine.index.json"
    index_path.write_text(json.dumps(index_payload, indent=2), encoding="utf-8")
    print(f"Wrote marine geometry: {lod0_dir / 'marine_ecoregions_lod0.topojson'}")
    print(f"Wrote marine index: {index_path}")


if __name__ == "__main__":
    main()
