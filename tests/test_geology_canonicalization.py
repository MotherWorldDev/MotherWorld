import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

PROJECT_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_ROOT", Path(__file__).resolve().parents[1]))
TEST_TEMP_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_TEMP", r"F:\BiomeSummary\.cache\motherworld\v8-task-temp"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from geology_common import json_safe, load_regions  # noqa: E402
from seafloor_common import load_marine_analysis_regions  # noqa: E402


def _write_layer(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    frame.to_file(path, driver="GeoJSON")


def _write_index(path: Path, ids: list[str]) -> None:
    path.write_text(json.dumps({"regions": {rid: {"name": rid} for rid in ids}}) + "\n", encoding="utf-8")


class GeologyCanonicalizationTests(unittest.TestCase):
    def test_marine_fragments_group_and_lakes_are_excluded_from_open_ocean(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temp:
            repo = Path(temp)
            data = repo / "frontend/public/data"
            land_id = "eco_1"
            marine_id = "marine_meow_20001"
            lake_id = "lake_1"
            lake_geom = box(5, 0, 6, 1)
            _write_layer(data / "lod0/ecoregions_lod0.topojson", [{"id": land_id, "geometry": box(-10, 0, -5, 5)}])
            _write_layer(
                data / "marine/lod0/marine_ecoregions_lod0.topojson",
                [
                    {"id": f"{marine_id}__1", "regionId": marine_id, "geometry": box(0, 0, 2, 2)},
                    {"id": f"{marine_id}__2", "regionId": marine_id, "geometry": box(2, 0, 4, 2)},
                ],
            )
            _write_layer(data / "lakes/lod0/lakes_lod0.topojson", [{"id": lake_id, "geometry": lake_geom}])
            _write_index(data / "regions.index.json", [land_id])
            _write_index(data / "marine.index.json", [marine_id])
            _write_index(data / "lakes.index.json", [lake_id])

            canonical = load_regions(repo, ("land", "marine", "lakes"))
            self.assertEqual(set(canonical.regionId), {land_id, marine_id, lake_id})
            marine = canonical.loc[canonical.regionId == marine_id].iloc[0]
            self.assertAlmostEqual(marine.geometry.area, 8)
            self.assertNotIn("__", marine.regionId)

            scoped = load_regions(repo, ("land", "marine", "lakes"), globalize_open_ocean=True)
            open_geom = scoped.loc[scoped.regionId == "open_ocean"].iloc[0].geometry
            self.assertFalse(open_geom.contains(lake_geom.representative_point()))
            marine_only = load_regions(repo, ("marine",), globalize_open_ocean=True)
            self.assertEqual(set(marine_only.regionId), {marine_id, "open_ocean"})
            whole = marine_only.loc[marine_only.regionId == "open_ocean"].iloc[0].geometry
            self.assertFalse(whole.contains(lake_geom.representative_point()))
            self.assertFalse(whole.contains(box(-10, 0, -5, 5).representative_point()))
            analysis = load_marine_analysis_regions(repo)
            self.assertEqual(set(analysis.regionId), {marine_id, "open_ocean"})
            self.assertFalse(analysis.loc[analysis.regionId == "open_ocean"].iloc[0].geometry.contains(lake_geom.representative_point()))

    def test_json_safe_rejects_nonfinite_numbers_for_browser_json(self):
        safe = json_safe({"nan": float("nan"), "inf": float("inf"), "nested": [np.float64("nan"), 4.0]})
        self.assertEqual(safe, {"nan": None, "inf": None, "nested": [None, 4.0]})
        json.dumps(safe, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
