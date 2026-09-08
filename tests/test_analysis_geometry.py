import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

PROJECT_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_ROOT", Path(__file__).resolve().parents[1]))
TEST_TEMP_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_TEMP", PROJECT_ROOT / ".cache/motherworld/v8-task-temp"))
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from analysis_geometry import (  # noqa: E402
    analysis_geometry_cache_identity,
    analysis_geometry_metadata,
    apply_land_geometry_overrides,
)
from biodiversity_common import load_region_geometries  # noqa: E402
from geology_common import load_regions, write_fragment  # noqa: E402
from merge_biodiversity_metrics import main as merge_biodiversity  # noqa: E402
from merge_geology import merge  # noqa: E402
from species_query_geometry import load_query_geometry_overrides  # noqa: E402


REGION_IDS = {
    "eco_117",
    "eco_121",
    "eco_130",
    "eco_267",
    "eco_509",
    "eco_562",
    "eco_609",
}


def _write_layer(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326").to_file(path, driver="GeoJSON")


def _write_index(path: Path, ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"regions": {rid: {"name": rid} for rid in ids}}) + "\n", encoding="utf-8")


def _minimal_repo(folder: str) -> Path:
    repo = Path(folder)
    data = repo / "frontend/public/data"
    _write_layer(
        data / "lod0/ecoregions_lod0.topojson",
        [{"id": "eco_509", "geometry": box(-30.30, -20.60, -30.10, -20.40)}],
    )
    _write_layer(data / "marine/lod0/marine_ecoregions_lod0.topojson", [{"id": "marine_ocean_0", "geometry": box(-180, -89.999, 180, 89.999)}])
    _write_layer(data / "lakes/lod0/lakes_lod0.topojson", [{"id": "lake_1", "geometry": box(5, 0, 6, 1)}])
    _write_index(data / "regions.index.json", ["eco_509"])
    _write_index(data / "marine.index.json", ["marine_ocean_0"])
    _write_index(data / "lakes.index.json", ["lake_1"])
    return repo


class AnalysisGeometryTests(unittest.TestCase):
    def test_missing_reference_geometry_withholds_affected_fragments(self):
        from merge_geology import _stale_reason as geology_reason
        from merge_biodiversity_metrics import _stale_reason as biodiversity_reason
        for check in (geology_reason, biodiversity_reason):
            for rid in (*REGION_IDS, "open_ocean"):
                self.assertEqual(check({"regionId": rid}, {})["reason"],
                                 "expected_analysis_geometry_unavailable")
            self.assertIsNone(check({"regionId": "eco_1"}, {}))

    def test_all_seven_overrides_apply_only_to_existing_land_ids(self):
        overrides = load_query_geometry_overrides()
        self.assertEqual(set(overrides), REGION_IDS)
        base = {rid: box(-1, -1, 1, 1) for rid in REGION_IDS}
        base["eco_1"] = box(2, 2, 3, 3)
        corrected = apply_land_geometry_overrides(base)

        self.assertEqual(set(corrected), set(base))
        self.assertIs(corrected["eco_1"], base["eco_1"])
        for rid in REGION_IDS:
            self.assertEqual(corrected[rid], overrides[rid][0])
            metadata = analysis_geometry_metadata(rid, corrected[rid])
            self.assertTrue(metadata["overrideApplied"], rid)
            self.assertEqual(metadata["geometrySource"], "maintained_query_override")
            json.dumps(metadata, allow_nan=False)

    def test_official_brazilian_footprints_keep_the_corrected_locations(self):
        overrides = load_query_geometry_overrides()
        trindade = overrides["eco_509"][0]
        st_paul = overrides["eco_609"][0]
        self.assertGreater(trindade.bounds[0], -30)
        self.assertGreater(trindade.bounds[2], -29)
        self.assertLess(st_paul.centroid.x, -29.34)

        old = box(-30.30, -20.60, -30.10, -20.40)
        old_metadata = analysis_geometry_metadata("eco_509", old)
        self.assertFalse(old_metadata["overrideApplied"])
        self.assertEqual(old_metadata["geometrySource"], "base_analysis_geometry")
        self.assertNotEqual(
            analysis_geometry_cache_identity("eco_509", old),
            analysis_geometry_cache_identity("eco_509", trindade),
        )

    def test_biodiversity_original_and_fallback_loaders_apply_overrides(self):
        old = box(-30.30, -20.60, -30.10, -20.40)
        overrides = load_query_geometry_overrides()
        expected = overrides["eco_509"][0]

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            data = repo / "frontend/public/data"
            _write_layer(data / "lod0/ecoregions_lod0.topojson", [{"id": "eco_509", "geometry": old}])
            original = repo / "Ecoregions2017/Ecoregions2017.shp"
            _write_layer(original, [{"ECO_ID": 509, "geometry": old}])
            self.assertEqual(load_region_geometries(repo, "land")["eco_509"], expected)

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            realm = repo / "frontend/public/data/lod1_realms/realm.topojson"
            _write_layer(realm, [{"id": "eco_509", "geometry": old}])
            self.assertEqual(load_region_geometries(repo, "land")["eco_509"], expected)

    def test_full_and_subset_whole_ocean_exclude_corrected_land_and_lakes(self):
        overrides = load_query_geometry_overrides()
        corrected = overrides["eco_509"][0]
        old = box(-30.30, -20.60, -30.10, -20.40)

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = _minimal_repo(folder)
            full = load_regions(repo, ("land", "marine", "lakes"), globalize_open_ocean=True)
            subset = load_regions(repo, ("marine",), globalize_open_ocean=True)
            for frame in (full, subset):
                ocean = frame.loc[frame.regionId == "open_ocean"].iloc[0].geometry
                self.assertFalse(ocean.contains(corrected.representative_point()))
                self.assertFalse(ocean.contains(box(5, 0, 6, 1).representative_point()))
                self.assertTrue(ocean.contains(old.representative_point()))

    def test_unverified_cached_sections_are_withheld_by_merge(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = _minimal_repo(folder)
            write_fragment(
                repo,
                "legacy_provider",
                {"regionId": "eco_509", "regionName": "Trindade", "kind": "land"},
                {"surfaceGeology": {"rockCoveragePct": 1}},
                {"label": "legacy"},
            )
            fragment = json.loads(
                (repo / ".cache/motherworld/geology/providers/legacy_provider/eco_509.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("analysisGeometry", fragment)

            index = merge(repo)
            stale = index["staleFragments"]
            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0]["reason"], "missing_analysis_geometry_identity")
            self.assertNotIn("eco_509", index["regions"])

    def test_biodiversity_merge_withholds_unverified_affected_provider(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = _minimal_repo(folder)
            biodiversity_root = repo / ".biodiversity-work/providers/nhm_bii/land"
            biodiversity_root.mkdir(parents=True, exist_ok=True)
            (biodiversity_root / "eco_509.json").write_text(
                json.dumps({
                    "provider": "nhm_bii",
                    "regionId": "eco_509",
                    "regionKind": "land",
                    "metrics": {"intactnessPct": 12},
                }),
                encoding="utf-8",
            )
            (repo / "frontend/public/data/biodiversity/provider-manifest.json").parent.mkdir(
                parents=True, exist_ok=True
            )
            (repo / "frontend/public/data/biodiversity/provider-manifest.json").write_text(
                json.dumps({"providers": {}}), encoding="utf-8"
            )
            with patch.object(sys, "argv", ["merge_biodiversity_metrics", "--repo", str(repo), "--kind", "land"]):
                merge_biodiversity()
            index = json.loads(
                (repo / "frontend/public/data/biodiversity/biodiversity.index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(index["staleFragments"][0]["reason"], "missing_analysis_geometry_identity")
            self.assertNotIn("eco_509", index["regions"])

    def test_producer_context_attaches_identity_for_corrected_geometry(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = _minimal_repo(folder)
            regions = load_regions(repo, ("land",))
            self.assertEqual(set(regions.regionId), {"eco_509"})
            write_fragment(
                repo,
                "new_provider",
                {"regionId": "eco_509", "regionName": "Trindade", "kind": "land"},
                {"surfaceGeology": {"rockCoveragePct": 1}},
                {"label": "new"},
            )
            fragment = json.loads(
                (repo / ".cache/motherworld/geology/providers/new_provider/eco_509.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(fragment["analysisGeometry"]["overrideApplied"])
            self.assertTrue(fragment["analysisGeometryCacheIdentity"].startswith("analysis-geometry-v1:"))


if __name__ == "__main__":
    unittest.main()
