#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.polygon import orient as orient_polygon
from shapely.ops import unary_union

try:
    from shapely import make_valid  # Shapely 2.x
except ImportError:  # pragma: no cover
    make_valid = None


WGS84 = "EPSG:4326"
AREA_CRS = "EPSG:6933"  # World Cylindrical Equal Area

CURSED_REGION_TARGETS = [
    ("Antarctica", "eco_134"),      # Transantarctic Mountains tundra
    ("Siberia/Ural", "eco_719"),    # Urals montane forest and taiga
    ("North Africa", "eco_833"),    # North Saharan Xeric Steppe and Woodland
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preprocess WWF/RESOLVE Ecoregions2017 shapefile into topology-preserving web LOD assets + metadata index."
    )
    parser.add_argument("--lod-overview-retain-pct", type=float, default=2.0)
    parser.add_argument("--lod0-retain-pct", type=float, default=4.0)
    parser.add_argument("--lod1-retain-pct", type=float, default=5.0)
    parser.add_argument("--lod0-hole-min-km2", type=float, default=25.0)
    parser.add_argument("--lod1-hole-min-km2", type=float, default=5.0)
    parser.add_argument("--lod0-fragment-min-km2", type=float, default=8.0)
    parser.add_argument("--lod1-fragment-min-km2", type=float, default=2.0)
    parser.add_argument("--lod0-sliver-min-km2", type=float, default=3.0)
    parser.add_argument("--lod1-sliver-min-km2", type=float, default=0.8)
    parser.add_argument("--keep-clean-geojson", action="store_true")
    return parser.parse_args()


def _polygonal_only(geom: Any) -> Polygon | MultiPolygon | None:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type in {"Polygon", "MultiPolygon"}:
        return geom
    if isinstance(geom, GeometryCollection):
        polys = []
        for part in geom.geoms:
            out = _polygonal_only(part)
            if out is not None and not out.is_empty:
                polys.append(out)
        if not polys:
            return None
        return unary_union(polys)
    return None


def repair_geometry(geom):
    if geom is None or geom.is_empty:
        return None
    out = geom
    if not out.is_valid:
        out = make_valid(out) if make_valid is not None else out.buffer(0)
    out = _polygonal_only(out)
    if out is None:
        return None
    if not out.is_valid:
        out = out.buffer(0)
        out = _polygonal_only(out)
    return out


def biome_palette() -> dict[int, str]:
    return {
        1: "#2f9e44",
        2: "#3dbb6f",
        3: "#76c893",
        4: "#7fc97f",
        5: "#4d908e",
        6: "#577590",
        7: "#4ea8de",
        8: "#adb5bd",
        9: "#6c757d",
        10: "#1d7874",
        11: "#8ecae6",
        12: "#c77dff",
        13: "#e76f51",
        14: "#f4a261",
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "unknown"


def canonicalize_biome_labels(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    biome_rows = gdf[["biome_num", "biome_name"]].dropna(subset=["biome_num"]).copy()
    biome_name_map: dict[int, str] = {}
    for biome_num in sorted({int(v) for v in biome_rows["biome_num"].tolist()}):
        labels = biome_rows.loc[biome_rows["biome_num"] == biome_num, "biome_name"].astype(str).str.strip()
        labels = labels[~labels.isin(["", "N/A", "Unknown"])]
        biome_name_map[biome_num] = labels.mode().iloc[0] if not labels.empty else "Unknown"

    def _norm(row):
        biome_num = row["biome_num"]
        if biome_num is None or str(biome_num) == "<NA>":
            return row["biome_name"]
        return biome_name_map.get(int(biome_num), row["biome_name"])

    gdf["biome_name"] = gdf.apply(_norm, axis=1)
    return gdf


def find_mapshaper_executable(project_root: Path) -> list[str]:
    local_bin = project_root / "node_modules" / "mapshaper" / "bin" / "mapshaper"
    if local_bin.exists() and shutil.which("node"):
        return ["node", str(local_bin)]
    if shutil.which("mapshaper"):
        return ["mapshaper"]
    if shutil.which("npx"):
        return ["npx", "--yes", "mapshaper"]
    raise RuntimeError("mapshaper not found. Install with `npm install --save-dev mapshaper`.")


def run_mapshaper(project_root: Path, mapshaper_cmd: list[str], args: list[str]) -> None:
    cmd = [*mapshaper_cmd, *args]
    print("mapshaper>", " ".join(cmd))
    subprocess.run(cmd, cwd=project_root, check=True)


def _sanitize_polygon_projected(poly: Polygon, min_hole_area_m2: float) -> Polygon | MultiPolygon | None:
    if poly.is_empty:
        return None
    holes = []
    for ring in poly.interiors:
        ring_poly = Polygon(ring)
        if ring_poly.area >= min_hole_area_m2:
            holes.append(ring.coords)
    out = Polygon(poly.exterior.coords, holes)
    out = repair_geometry(out)
    return out


def _normalize_winding(geom):
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return orient_polygon(geom, sign=1.0)
    if isinstance(geom, MultiPolygon):
        parts = [orient_polygon(p, sign=1.0) for p in geom.geoms if not p.is_empty]
        return MultiPolygon(parts) if len(parts) > 1 else (parts[0] if parts else None)
    return geom


def sanitize_display_geometry_projected(geom, min_hole_area_m2: float, min_fragment_area_m2: float):
    geom = repair_geometry(geom)
    if geom is None:
        return None

    if isinstance(geom, Polygon):
        out = _sanitize_polygon_projected(geom, min_hole_area_m2)
        return _normalize_winding(repair_geometry(out))

    if isinstance(geom, MultiPolygon):
        parts = []
        for poly in geom.geoms:
            out = _sanitize_polygon_projected(poly, min_hole_area_m2)
            if out is None:
                continue
            if isinstance(out, Polygon):
                parts.append(out)
            elif isinstance(out, MultiPolygon):
                parts.extend([p for p in out.geoms if not p.is_empty])

        if not parts:
            return None

        # Remove tiny detached fragments, but preserve at least one part per feature.
        kept = [p for p in parts if p.area >= min_fragment_area_m2]
        if not kept:
            kept = [max(parts, key=lambda p: p.area)]

        if len(kept) == 1:
            return _normalize_winding(repair_geometry(kept[0]))
        return _normalize_winding(repair_geometry(MultiPolygon(kept)))

    return _normalize_winding(repair_geometry(_polygonal_only(geom)))


def sanitize_final_topojson_file(
    project_root: Path,
    topo_path: Path,
    mapshaper_cmd: list[str],
    hole_min_km2: float,
    fragment_min_km2: float,
    sliver_min_km2: float,
) -> None:
    # Topology-preserving cleanup first (shared-edge-safe), but avoid feature-dropping commands.
    run_mapshaper(
        project_root,
        mapshaper_cmd,
        [
            str(topo_path),
            "-filter-slivers",
            f"min-area={sliver_min_km2}km2",
            "keep-shapes",
            "-o",
            str(topo_path),
            "format=topojson",
            "id-field=id",
            "bbox",
            "no-quantization",
            "force",
        ],
    )

    # Final Python pass for explicit hole/fragment cleanup + validation.
    gdf = gpd.read_file(topo_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if gdf.empty:
        return

    min_hole_m2 = hole_min_km2 * 1_000_000
    min_frag_m2 = fragment_min_km2 * 1_000_000
    gdf_proj = gdf.to_crs(AREA_CRS)
    gdf_proj["geometry"] = gdf_proj.geometry.map(
        lambda geom: sanitize_display_geometry_projected(geom, min_hole_m2, min_frag_m2)
    )
    gdf_proj = gdf_proj[gdf_proj.geometry.notna() & ~gdf_proj.geometry.is_empty].copy()
    gdf_clean = gdf_proj.to_crs(WGS84)
    gdf_clean["geometry"] = gdf_clean.geometry.map(lambda geom: _normalize_winding(repair_geometry(geom)))
    gdf_clean = gdf_clean[gdf_clean.geometry.notna() & ~gdf_clean.geometry.is_empty].copy()

    with tempfile.TemporaryDirectory(prefix="sanitized_topo_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "sanitized.geojson"
        gdf_clean.to_file(tmp_geojson, driver="GeoJSON")
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

    # Final revalidation in case TopoJSON rebuild reintroduces invalid rings.
    final_check = gpd.read_file(topo_path)
    if final_check.crs is None:
        final_check = final_check.set_crs(WGS84)
    invalid_mask = ~final_check.geometry.is_valid
    if int(invalid_mask.sum()) == 0:
        return

    print(f"  Revalidating {topo_path.name}: repairing {int(invalid_mask.sum())} invalid geometries after final export")
    final_check["geometry"] = final_check.geometry.map(lambda geom: _normalize_winding(repair_geometry(geom)))
    final_check = final_check[final_check.geometry.notna() & ~final_check.geometry.is_empty].copy()
    with tempfile.TemporaryDirectory(prefix="revalidate_topo_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "revalidated.geojson"
        final_check.to_file(tmp_geojson, driver="GeoJSON")
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


def load_lake_coverage_union(project_root: Path, *, use_overview: bool = False):
    lakes_path = (
        project_root
        / "frontend"
        / "public"
        / "data"
        / "lakes"
        / ("lod_overview" if use_overview else "lod0")
        / ("lakes_overview.topojson" if use_overview else "lakes_lod0.topojson")
    )
    if not lakes_path.exists():
        print(f"Warning: lakes coverage not found, skipping lake clip: {lakes_path}")
        return None

    print(f"Loading lakes coverage for land clip: {lakes_path} ...")
    gdf = gpd.read_file(lakes_path)
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    gdf["geometry"] = gdf.geometry.map(repair_geometry)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    if gdf.empty:
        return None
    lakes_union = repair_geometry(unary_union(gdf.geometry.tolist()))
    if lakes_union is None or lakes_union.is_empty:
        return None
    return lakes_union


def subtract_lake_overlap_in_final_topojson(
    project_root: Path,
    topo_path: Path,
    mapshaper_cmd: list[str],
    *,
    use_overview_lakes: bool = False,
) -> None:
    lakes_union = load_lake_coverage_union(project_root, use_overview=use_overview_lakes)
    if lakes_union is None or lakes_union.is_empty:
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
        clipped = repair_geometry(geom.difference(lakes_union))
        if clipped is None or clipped.is_empty:
            changed_count += 1
            continue
        if not clipped.equals(geom):
            changed_count += 1
        record["geometry"] = clipped
        kept_records.append(record)

    if changed_count == 0:
        return

    print(f"Subtracting exact lake overlap from {changed_count} land display geometries in {topo_path.name} ...")
    fixed_gdf = gpd.GeoDataFrame(kept_records, geometry="geometry", crs=WGS84)
    with tempfile.TemporaryDirectory(prefix="land_lake_overlap_fix_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        tmp_geojson = tmp_dir / "land_lake_overlap_fixed.geojson"
        fixed_gdf.to_file(tmp_geojson, driver="GeoJSON")
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


def geom_component_stats(geom) -> dict[str, Any]:
    if geom is None or geom.is_empty:
        return {
            "multipartCount": 0,
            "outerRingCount": 0,
            "holeCount": 0,
            "vertexCount": 0,
            "valid": False,
            "geomType": "Empty",
        }

    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    else:
        poly_only = _polygonal_only(geom)
        if poly_only is None:
            polys = []
        elif isinstance(poly_only, Polygon):
            polys = [poly_only]
        else:
            polys = list(poly_only.geoms)

    hole_count = 0
    vertex_count = 0
    for poly in polys:
        vertex_count += len(poly.exterior.coords)
        for ring in poly.interiors:
            hole_count += 1
            vertex_count += len(ring.coords)

    return {
        "multipartCount": len(polys),
        "outerRingCount": len(polys),
        "holeCount": hole_count,
        "vertexCount": vertex_count,
        "valid": bool(geom.is_valid),
        "geomType": geom.geom_type,
    }


def inspect_exported_geometry(
    out_dir: Path,
    regions_index: dict[str, dict[str, Any]],
    lod0_rel_path: str,
    realm_files: dict[str, str],
) -> None:
    print("\nFinal export geometry diagnostics")
    files_to_check = [out_dir / lod0_rel_path] + [out_dir / rel for rel in realm_files.values()]
    for path in files_to_check:
        gdf = gpd.read_file(path)
        if gdf.crs is None:
            gdf = gdf.set_crs(WGS84)
        invalid_count = int((~gdf.geometry.is_valid).sum())
        print(f"  {path.relative_to(out_dir)} -> features={len(gdf)}, invalid={invalid_count}")

    print("\nCursed region stats (after final export)")
    for label, region_id in CURSED_REGION_TARGETS:
        region_meta = regions_index.get(region_id)
        if not region_meta:
            print(f"  {label}: {region_id} not found in index")
            continue

        candidate_files = [("LOD0", out_dir / lod0_rel_path)]
        realm_rel = realm_files.get(region_meta["realmSlug"])
        if realm_rel:
            candidate_files.append(("LOD1", out_dir / realm_rel))

        print(f"  {label}: {region_id} ({region_meta['name']})")
        for lod_label, path in candidate_files:
            gdf = gpd.read_file(path)
            row = gdf[gdf["id"] == region_id]
            if row.empty:
                print(f"    {lod_label}: not present in {path.name}")
                continue
            stats = geom_component_stats(row.iloc[0].geometry)
            print(
                "    "
                f"{lod_label}: multipart={stats['multipartCount']}, "
                f"outerRings={stats['outerRingCount']}, holes={stats['holeCount']}, "
                f"vertices={stats['vertexCount']}, valid={'valid' if stats['valid'] else 'invalid'}"
            )


def build_topology_lods(
    project_root: Path,
    out_dir: Path,
    cleaned_geo: gpd.GeoDataFrame,
    realm_catalog: list[dict[str, str]],
    lod_overview_retain_pct: float,
    lod0_retain_pct: float,
    lod1_retain_pct: float,
    keep_clean_geojson: bool,
    lod0_hole_min_km2: float,
    lod1_hole_min_km2: float,
    lod0_fragment_min_km2: float,
    lod1_fragment_min_km2: float,
    lod0_sliver_min_km2: float,
    lod1_sliver_min_km2: float,
 ) -> tuple[dict[str, str], str, str]:
    lod_overview_dir = out_dir / "lod_overview"
    lod0_dir = out_dir / "lod0"
    lod1_realms_dir = out_dir / "lod1_realms"
    lod_overview_dir.mkdir(parents=True, exist_ok=True)
    lod0_dir.mkdir(parents=True, exist_ok=True)
    lod1_realms_dir.mkdir(parents=True, exist_ok=True)

    lod_overview_path = lod_overview_dir / "ecoregions_overview.topojson"
    lod0_path = lod0_dir / "ecoregions_lod0.topojson"
    mapshaper_cmd = find_mapshaper_executable(project_root)
    realm_files: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="ecoregions_preprocess_", dir=project_root) as tmp_str:
        tmp_dir = Path(tmp_str)
        cleaned_path = tmp_dir / "ecoregions.cleaned.geojson"
        print(f"Writing topology input (cleaned GeoJSON): {cleaned_path}")
        cleaned_geo.to_file(cleaned_path, driver="GeoJSON")

        if keep_clean_geojson:
            debug_dir = out_dir / "_debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cleaned_path, debug_dir / "ecoregions.cleaned.geojson")

        print("Building overview LOD (zoomed out global) TopoJSON ...")
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(cleaned_path),
                "-simplify",
                f"{lod_overview_retain_pct}%",
                "weighted",
                "keep-shapes",
                "-o",
                str(lod_overview_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "no-quantization",
                "force",
            ],
        )
        sanitize_final_topojson_file(
            project_root,
            lod_overview_path,
            mapshaper_cmd,
            hole_min_km2=lod0_hole_min_km2,
            fragment_min_km2=lod0_fragment_min_km2,
            sliver_min_km2=lod0_sliver_min_km2,
        )
        subtract_lake_overlap_in_final_topojson(
            project_root,
            lod_overview_path,
            mapshaper_cmd,
            use_overview_lakes=True,
        )

        print("Building LOD0 (global startup) TopoJSON ...")
        run_mapshaper(
            project_root,
            mapshaper_cmd,
            [
                str(cleaned_path),
                "-simplify",
                f"{lod0_retain_pct}%",
                "weighted",
                "keep-shapes",
                "-o",
                str(lod0_path),
                "format=topojson",
                "id-field=id",
                "bbox",
                "no-quantization",
                "force",
            ],
        )
        sanitize_final_topojson_file(
            project_root,
            lod0_path,
            mapshaper_cmd,
            hole_min_km2=lod0_hole_min_km2,
            fragment_min_km2=lod0_fragment_min_km2,
            sliver_min_km2=lod0_sliver_min_km2,
        )
        subtract_lake_overlap_in_final_topojson(
            project_root,
            lod0_path,
            mapshaper_cmd,
            use_overview_lakes=False,
        )

        print("Building LOD1 per-realm TopoJSON files ...")
        for stale in lod1_realms_dir.glob("*.topojson"):
            stale.unlink()
        # Remove stale outputs from earlier 3-LOD pipeline versions to avoid confusion.
        lod2_realms_dir = out_dir / "lod2_realms"
        if lod2_realms_dir.exists():
            for stale in lod2_realms_dir.glob("*.topojson"):
                stale.unlink()

        for realm in realm_catalog:
            slug = realm["realmSlug"]
            out_file = lod1_realms_dir / f"{slug}.topojson"
            run_mapshaper(
                project_root,
                mapshaper_cmd,
                [
                    str(cleaned_path),
                    "-filter",
                    f"realmSlug=='{slug}'",
                    "-simplify",
                    f"{lod1_retain_pct}%",
                    "weighted",
                    "keep-shapes",
                    "-o",
                    str(out_file),
                    "format=topojson",
                    "id-field=id",
                    "bbox",
                    "no-quantization",
                    "force",
                ],
            )
            sanitize_final_topojson_file(
                project_root,
                out_file,
                mapshaper_cmd,
                hole_min_km2=lod1_hole_min_km2,
                fragment_min_km2=lod1_fragment_min_km2,
                sliver_min_km2=lod1_sliver_min_km2,
            )
            subtract_lake_overlap_in_final_topojson(
                project_root,
                out_file,
                mapshaper_cmd,
                use_overview_lakes=False,
            )
            realm_files[slug] = str(out_file.relative_to(out_dir)).replace("\\", "/")

    return (
        realm_files,
        str(lod0_path.relative_to(out_dir)).replace("\\", "/"),
        str(lod_overview_path.relative_to(out_dir)).replace("\\", "/"),
    )


def main() -> None:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "Ecoregions2017" / "Ecoregions2017.shp"
    out_dir = project_root / "frontend" / "public" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_path.exists():
        raise FileNotFoundError(f"Shapefile not found: {src_path}")

    print(f"Loading {src_path} ...")
    gdf = gpd.read_file(src_path)
    print(f"Loaded {len(gdf)} rows, CRS={gdf.crs}")

    keep = [
        "ECO_ID",
        "ECO_NAME",
        "BIOME_NUM",
        "BIOME_NAME",
        "REALM",
        "ECO_BIOME_",
        "NNH",
        "NNH_NAME",
        "geometry",
    ]
    missing = [c for c in keep if c not in gdf.columns]
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")
    gdf = gdf[keep].copy().to_crs(WGS84)

    print("Repairing invalid geometries ...")
    invalid_before = int((~gdf.geometry.is_valid).sum())
    gdf["geometry"] = gdf.geometry.map(repair_geometry)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    invalid_after = int((~gdf.geometry.is_valid).sum())
    print(f"Invalid geometries: {invalid_before} -> {invalid_after}")

    gdf["region_id"] = gdf["ECO_ID"].astype(int).map(lambda v: f"eco_{v}")
    gdf["biome_num"] = gdf["BIOME_NUM"].astype("Int64")
    gdf["biome_name"] = gdf["BIOME_NAME"].fillna("Unknown")
    gdf["realm"] = gdf["REALM"].fillna("Unknown")
    gdf["realm_slug"] = gdf["realm"].astype(str).map(slugify)
    gdf["ecoregion_name"] = gdf["ECO_NAME"].fillna("Unnamed ecoregion")
    gdf["eco_biome_code"] = gdf["ECO_BIOME_"].fillna("")
    gdf["nnh_code"] = gdf["NNH"].astype("Int64")
    gdf["nnh_name"] = gdf["NNH_NAME"].fillna("")
    gdf = canonicalize_biome_labels(gdf)

    print("Computing derived metrics (area / representative point) ...")
    gdf_area = gdf.to_crs(AREA_CRS)
    gdf["area_km2"] = (gdf_area.geometry.area / 1_000_000).round(2)
    reps = gdf.geometry.representative_point()
    gdf["center_lon"] = reps.x.round(5)
    gdf["center_lat"] = reps.y.round(5)

    palette = biome_palette()

    regions_index: dict[str, dict[str, Any]] = {}
    for row in gdf.itertuples(index=False):
        biome_num = int(row.biome_num) if str(row.biome_num) != "<NA>" else None
        nnh_code = int(row.nnh_code) if str(row.nnh_code) != "<NA>" else None
        regions_index[row.region_id] = {
            "id": row.region_id,
            "ecoId": int(row.ECO_ID),
            "name": row.ecoregion_name,
            "biomeNum": biome_num,
            "biome": row.biome_name,
            "realm": row.realm,
            "realmSlug": row.realm_slug,
            "ecoBiomeCode": row.eco_biome_code,
            "nnhCode": nnh_code,
            "nnhName": row.nnh_name,
            "areaKm2": float(row.area_km2),
            "center": {"lon": float(row.center_lon), "lat": float(row.center_lat)},
        }

    biome_catalog = []
    for biome_num, biome_name in (
        gdf[["biome_num", "biome_name"]]
        .drop_duplicates()
        .sort_values(["biome_num", "biome_name"], na_position="last")
        .itertuples(index=False, name=None)
    ):
        bnum = int(biome_num) if str(biome_num) != "<NA>" else None
        biome_catalog.append({"biomeNum": bnum, "biome": biome_name, "color": palette.get(bnum, "#8b949e")})

    realm_catalog = [
        {"realm": realm, "realmSlug": realm_slug}
        for realm, realm_slug in (
            gdf[["realm", "realm_slug"]]
            .drop_duplicates()
            .sort_values(["realm"])
            .itertuples(index=False, name=None)
        )
    ]

    cleaned_geo = (
        gdf[
            [
                "region_id",
                "ecoregion_name",
                "biome_num",
                "biome_name",
                "realm",
                "realm_slug",
                "area_km2",
                "geometry",
            ]
        ]
        .copy()
        .rename(
            columns={
                "region_id": "id",
                "ecoregion_name": "name",
                "biome_num": "biomeNum",
                "biome_name": "biome",
                "realm_slug": "realmSlug",
                "area_km2": "areaKm2",
            }
        )
    )
    cleaned_geo["biomeNum"] = cleaned_geo["biomeNum"].astype("Int64").astype(object)
    cleaned_geo["areaKm2"] = cleaned_geo["areaKm2"].astype(float)

    realm_files, lod0_rel_path, lod_overview_rel_path = build_topology_lods(
        project_root=project_root,
        out_dir=out_dir,
        cleaned_geo=cleaned_geo,
        realm_catalog=realm_catalog,
        lod_overview_retain_pct=float(args.lod_overview_retain_pct),
        lod0_retain_pct=float(args.lod0_retain_pct),
        lod1_retain_pct=float(args.lod1_retain_pct),
        keep_clean_geojson=bool(args.keep_clean_geojson),
        lod0_hole_min_km2=float(args.lod0_hole_min_km2),
        lod1_hole_min_km2=float(args.lod1_hole_min_km2),
        lod0_fragment_min_km2=float(args.lod0_fragment_min_km2),
        lod1_fragment_min_km2=float(args.lod1_fragment_min_km2),
        lod0_sliver_min_km2=float(args.lod0_sliver_min_km2),
        lod1_sliver_min_km2=float(args.lod1_sliver_min_km2),
    )

    metadata_path = out_dir / "regions.index.json"
    index_payload = {
        "source": {
            "dataset": "WWF/RESOLVE Terrestrial Ecoregions of the World (Ecoregions2017)",
            "license": "CC-BY 4.0 (as provided in source dataset fields)",
            "inputPath": str(src_path.relative_to(project_root)).replace("\\", "/"),
        },
        "stats": {
            "featureCount": int(len(gdf)),
            "invalidGeometriesFixed": invalid_before,
        },
        "geometry": {
            "format": "topojson",
            "overviewLod": {
                "level": "overview",
                "url": lod_overview_rel_path,
                "retainPct": float(args.lod_overview_retain_pct),
            },
            "startupLod": {"level": "lod0", "url": lod0_rel_path, "retainPct": float(args.lod0_retain_pct)},
            "realmDetailLod": {
                "level": "lod1",
                "baseDir": "lod1_realms",
                "retainPct": float(args.lod1_retain_pct),
                "files": realm_files,
            },
            "sanitation": {
                "lod0": {
                    "holeMinKm2": float(args.lod0_hole_min_km2),
                    "fragmentMinKm2": float(args.lod0_fragment_min_km2),
                    "sliverMinKm2": float(args.lod0_sliver_min_km2),
                },
                "lod1": {
                    "holeMinKm2": float(args.lod1_hole_min_km2),
                    "fragmentMinKm2": float(args.lod1_fragment_min_km2),
                    "sliverMinKm2": float(args.lod1_sliver_min_km2),
                },
            },
        },
        "realms": realm_catalog,
        "biomes": biome_catalog,
        "regions": regions_index,
    }

    print(f"Writing {metadata_path} ...")
    metadata_path.write_text(json.dumps(index_payload, separators=(",", ":")), encoding="utf-8")

    inspect_exported_geometry(out_dir, regions_index, lod0_rel_path, realm_files)

    lod0_path = out_dir / lod0_rel_path
    lod1_dir = out_dir / "lod1_realms"
    size_lod0_mb = lod0_path.stat().st_size / (1024 * 1024)
    size_lod1_total_mb = sum(p.stat().st_size for p in lod1_dir.glob("*.topojson")) / (1024 * 1024)
    size_meta_mb = metadata_path.stat().st_size / (1024 * 1024)
    print(
        "\nDone. "
        f"LOD0 TopoJSON={size_lod0_mb:.2f} MB, "
        f"LOD1 realms total={size_lod1_total_mb:.2f} MB, "
        f"metadata={size_meta_mb:.2f} MB"
    )


if __name__ == "__main__":
    main()
