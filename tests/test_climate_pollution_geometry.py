import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import geopandas as gpd
from shapely.geometry import box

PROJECT_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_ROOT", Path(__file__).resolve().parents[1]))
TEST_TEMP_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_TEMP", str(PROJECT_ROOT / ".cache/motherworld/v8-task-temp")))
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

# The pollution builder only needs the Earth Engine module at runtime.  Keep
# these focused tests runnable in the lightweight climate test environment.
sys.modules.setdefault("ee", types.ModuleType("ee"))

import analysis_geometry  # noqa: E402
import climate_extras_common  # noqa: E402
import contaminants_common  # noqa: E402
import land_pollution_common  # noqa: E402
import pollution_common  # noqa: E402
import temperature_common  # noqa: E402
from build_pollution_metrics_ee import (  # noqa: E402
    build_payload,
    cached_batch_matches_geometry,
    load_cached,
)


OLD_TRINDADE = box(-30.30, -20.60, -30.10, -20.40)
OTHER = box(10, 10, 11, 11)


def _frame(*, eco_field="id"):
    if eco_field == "ECO_ID":
        return gpd.GeoDataFrame(
            {"ECO_ID": [509, 900], "geometry": [OLD_TRINDADE, OTHER]},
            geometry="geometry",
            crs="EPSG:4326",
        )
    return gpd.GeoDataFrame(
        {"id": ["eco_509", "eco_900"], "geometry": [OLD_TRINDADE, OTHER]},
        geometry="geometry",
        crs="EPSG:4326",
    )


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("placeholder", encoding="utf-8")


def _region_index(repo: Path):
    path = repo / "frontend/public/data/regions.index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"regions": {"eco_509": {"name": "Trindade"}, "eco_900": {"name": "Other"}}}),
        encoding="utf-8",
    )


class ClimatePollutionGeometryTests(unittest.TestCase):
    def setUp(self):
        analysis_geometry._validated_overrides.cache_clear()

    def test_temperature_and_climate_extras_apply_after_base_union(self):
        expected = analysis_geometry.load_query_geometry_overrides()["eco_509"][0]
        base = {"eco_509": OLD_TRINDADE, "eco_900": OTHER}
        with mock.patch.object(temperature_common, "merged_geometries", return_value=base) as merged:
            result = temperature_common.load_land_geometries(Path("F:/unused"))
        merged.assert_called_once()
        self.assertTrue(result["eco_509"].equals(expected))
        self.assertTrue(result["eco_900"].equals(OTHER))

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            path = repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"
            _touch(path)
            frame = _frame()
            with mock.patch.object(climate_extras_common.gpd, "read_file", return_value=frame):
                result = climate_extras_common.load_geometries(repo, "land")
            self.assertTrue(result["eco_509"].equals(expected))
            self.assertTrue(result["eco_900"].equals(OTHER))

    def test_pollution_and_contaminant_original_and_fallback_paths_apply_override(self):
        expected = analysis_geometry.load_query_geometry_overrides()["eco_509"][0]
        for module, loader_name in (
            (pollution_common, "load_region_geometries"),
            (contaminants_common, "load_region_geometries"),
        ):
            for original in (True, False):
                with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
                    repo = Path(folder)
                    if original:
                        layer = repo / "Ecoregions2017/Ecoregions2017.shp"
                        frame = _frame(eco_field="ECO_ID")
                    else:
                        layer = repo / "frontend/public/data/lod0/ecoregions_lod0.topojson"
                        frame = _frame()
                    _touch(layer)
                    if module is pollution_common:
                        with mock.patch.object(module, "_read_layer", return_value=frame):
                            result = getattr(module, loader_name)(repo)
                        geom = result["eco_509"][1]
                    else:
                        with mock.patch.object(module.gpd, "read_file", return_value=frame):
                            result = getattr(module, loader_name)(repo)
                        geom = result["eco_509"][1]
                    self.assertTrue(geom.equals(expected), (module.__name__, original))

    def test_climate_extra_and_pollution_payloads_record_corrected_provenance(self):
        expected = analysis_geometry.load_query_geometry_overrides()["eco_509"][0]
        metadata = analysis_geometry.analysis_geometry_metadata("eco_509", expected)
        identity = analysis_geometry.analysis_geometry_cache_identity("eco_509", expected)
        raw = {
            "precip_hist_pct": [100],
            "humidity_hist_pct": [100],
            "wind_hist_pct": [100],
            "wind_rose_pct": [100],
            "precip_mean_mm_day": 1,
            "precip_p90_mm_day": 2,
            "humidity_mean_pct": 50,
            "humidity_p10_pct": 40,
            "humidity_p90_pct": 60,
            "wind_mean_ms": 3,
            "wind_p90_ms": 4,
            "wind_directional_coverage_pct": 100,
        }
        extras = climate_extras_common.payload_from_raw(
            region_id="eco_509",
            region_name="Trindade",
            kind="land",
            years=(1991, 2020),
            source={"id": "test"},
            raw=raw,
            area_km2=1,
            generated_at="now",
            analysis_geometry=metadata,
            analysis_geometry_cache_identity=identity,
        )
        self.assertEqual(extras["analysisGeometryCacheIdentity"], identity)
        self.assertTrue(extras["analysisGeometry"]["overrideApplied"])

        pollution = build_payload(
            "eco_509",
            "land",
            {"name": "Trindade"},
            {"pm25": {2020: {"mean": 1.5, "spatialP90": 2.0}}},
            ["pm25"],
            2019,
            2020,
            metadata,
            identity,
        )
        self.assertEqual(pollution["analysisGeometryCacheIdentity"], identity)
        self.assertTrue(pollution["analysisGeometry"]["overrideApplied"])

    def test_pollution_cache_requires_identity_for_corrected_target(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            cache = Path(folder) / "pm25/2020/batch_0001.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {"region_id": "eco_509", "mean": 9, "p90": 10},
                {"region_id": "eco_900", "mean": 3, "p90": 4},
            ]
            cache.write_text(json.dumps({"rows": rows}), encoding="utf-8")
            expected = {"eco_509": "current"}
            self.assertFalse(cached_batch_matches_geometry(cache, ["eco_509", "eco_900"], expected))
            data = load_cached(Path(folder), ["pm25"], range(2020, 2021), ["eco_509", "eco_900"], expected)
            self.assertEqual(data["eco_509"]["pm25"], {})
            self.assertEqual(data["eco_900"]["pm25"][2020]["mean"], 3.0)

            cache.write_text(
                json.dumps({
                    "rows": rows,
                    "analysisGeometryCacheIdentities": expected,
                }),
                encoding="utf-8",
            )
            self.assertTrue(cached_batch_matches_geometry(cache, ["eco_509", "eco_900"], expected))
            data = load_cached(Path(folder), ["pm25"], range(2020, 2021), ["eco_509", "eco_900"], expected)
            self.assertEqual(data["eco_509"]["pm25"][2020]["mean"], 9.0)

    def test_land_pollution_merge_rejects_missing_or_stale_claim_and_accepts_valid_claim(self):
        expected = {
            "schemaVersion": 1,
            "regionId": "eco_509",
            "geometryFingerprint": "fp",
            "overrideApplied": True,
            "geometryOverride": {"registryGeometryFingerprint": "fp"},
        }
        source = {"id": "test-provider", "label": "test"}
        geometry = box(0, 0, 1, 1)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            _region_index(repo)
            expected_path = str(repo.resolve())
            with mock.patch.object(land_pollution_common, "load_land_regions", return_value={"eco_509": geometry, "eco_900": OTHER}), \
                 mock.patch.object(land_pollution_common, "analysis_geometry_metadata", side_effect=lambda rid, geom: expected if str(rid) == "eco_509" else {"overrideApplied": False}), \
                 mock.patch.object(land_pollution_common, "analysis_geometry_cache_identity", return_value="current"):
                land_pollution_common.merge_region_provider(repo, {"eco_509": {"metrics": {"n": 1}}}, source)
                index_path = repo / "frontend/public/data/land-pollution/land-pollution.index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                self.assertNotIn("eco_509", index["regions"])

                valid = {"metrics": {"n": 2}, "_analysisGeometry": expected, "_analysisGeometryCacheIdentity": "current"}
                land_pollution_common.merge_region_provider(repo, {"eco_509": valid}, source)
                path = repo / "frontend/public/data/land-pollution/land/eco_509.land-pollution.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["analysisGeometryCacheIdentity"], "current")
                self.assertIn("test-provider", payload["providers"])

                stale = {"metrics": {"n": 3}, "_analysisGeometry": expected, "_analysisGeometryCacheIdentity": "old"}
                land_pollution_common.merge_region_provider(repo, {"eco_509": stale}, source)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("test-provider", payload["providers"])
                self.assertNotIn("analysisGeometryStatus", payload)
                index = json.loads(index_path.read_text(encoding="utf-8"))
                self.assertIn("eco_509", index["regions"])

                # Existing callers for unaffected IDs keep the old merge contract.
                land_pollution_common.merge_region_provider(repo, {"eco_900": {"metrics": {"n": 4}}}, source)
                other = repo / "frontend/public/data/land-pollution/land/eco_900.land-pollution.json"
                self.assertTrue(other.exists())

    def test_missing_expected_geometry_withholds_corrected_targets(self):
        source = {"id": "test-source", "label": "test"}
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            _region_index(repo)
            with mock.patch.object(land_pollution_common, "load_land_regions", return_value={}):
                land_pollution_common.merge_region_provider(repo, {"eco_509": {"metrics": {"n": 1}}}, source)
            index = json.loads(
                (repo / "frontend/public/data/land-pollution/land-pollution.index.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("eco_509", index["regions"])

        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            _region_index(repo)
            with mock.patch.object(contaminants_common, "load_region_geometries", return_value={}):
                contaminants_common.merge_region_categories(
                    repo,
                    {"eco_509": {"categories": {"pfas": {"sampleCount": 1}}, "sources": {}}},
                    source,
                )
            index = json.loads(
                (repo / "frontend/public/data/contaminants/contaminants.index.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("eco_509", index["regions"])

    def test_contaminant_merge_rejects_missing_claim_for_corrected_target(self):
        expected = {
            "schemaVersion": 1,
            "regionId": "eco_509",
            "geometryFingerprint": "fp",
            "overrideApplied": True,
            "geometryOverride": {"registryGeometryFingerprint": "fp"},
        }
        source = {"id": "test-source", "label": "test"}
        geometry = box(0, 0, 1, 1)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            repo = Path(folder)
            _region_index(repo)
            with mock.patch.object(contaminants_common, "load_region_geometries", return_value={"eco_509": ("land", geometry)}), \
                 mock.patch.object(contaminants_common, "analysis_geometry_metadata", return_value=expected), \
                 mock.patch.object(contaminants_common, "analysis_geometry_cache_identity", return_value="current"):
                contaminants_common.merge_region_categories(
                    repo,
                    {"eco_509": {"categories": {"pfas": {"sampleCount": 1}}, "sources": {}}},
                    source,
                )
                index_path = repo / "frontend/public/data/contaminants/contaminants.index.json"
                index = json.loads(index_path.read_text(encoding="utf-8"))
                self.assertNotIn("eco_509", index["regions"])

                update = {
                    "categories": {"pfas": {"sampleCount": 2}},
                    "sources": {},
                    "_analysisGeometry": expected,
                    "_analysisGeometryCacheIdentity": "current",
                }
                contaminants_common.merge_region_categories(repo, {"eco_509": update}, source)
                path = repo / "frontend/public/data/contaminants/land/eco_509.contaminants.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["analysisGeometryCacheIdentity"], "current")
                self.assertIn("pfas", payload["categories"])

                stale = {
                    "categories": {"pfas": {"sampleCount": 99}},
                    "sources": {},
                    "_analysisGeometry": expected,
                    "_analysisGeometryCacheIdentity": "old",
                }
                contaminants_common.merge_region_categories(repo, {"eco_509": stale}, source)
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["categories"]["pfas"]["sampleCount"], 2)
                self.assertNotIn("analysisGeometryStatus", payload)
                index = json.loads(index_path.read_text(encoding="utf-8"))
                self.assertIn("eco_509", index["regions"])


if __name__ == "__main__":
    unittest.main()
