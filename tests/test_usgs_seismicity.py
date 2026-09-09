from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import box


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_usgs_seismicity as builder


TEST_ROOT = ROOT / ".cache" / "motherworld" / "v8-test-tmp"


class ComCatBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_ROOT.mkdir(parents=True, exist_ok=True)

    def _repo(self) -> tempfile.TemporaryDirectory[str]:
        return tempfile.TemporaryDirectory(dir=TEST_ROOT, prefix="usgs-builder-")

    @staticmethod
    def _regions() -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame(
            [
                {
                    "regionId": "eco_test",
                    "regionName": "Test Ecoregion",
                    "kind": "land",
                    "geometry": box(-10, -10, 10, 10),
                }
            ],
            geometry="geometry",
            crs="EPSG:4326",
        )

    @staticmethod
    def _write_csv(repo: Path, rows: list[dict[str, object]]) -> Path:
        path = repo / "comcat.csv"
        pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n", encoding="utf-8")
        return path

    @staticmethod
    def _joined_one() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "regionId": "eco_test",
                    "magnitude": 6.2,
                    "time": "2020-01-01T00:00:00Z",
                    "place": "Test earthquake",
                }
            ]
        )

    @staticmethod
    def _joined_empty() -> pd.DataFrame:
        return pd.DataFrame(columns=["regionId", "magnitude", "time", "place"])

    def _seed_old_fragment(self, repo: Path) -> tuple[Path, str]:
        provider_dir = builder.provider_output_dir(repo)
        provider_dir.mkdir(parents=True, exist_ok=True)
        stale = provider_dir / "old_region.json"
        original = '{"providerId":"usgs_comcat","legacy":true}\n'
        stale.write_text(original, encoding="utf-8")
        return stale, original

    def test_mixed_event_types_are_filtered_and_provenance_is_accurate(self) -> None:
        with self._repo() as raw_repo:
            repo = Path(raw_repo)
            source = self._write_csv(
                repo,
                [
                    {
                        "type": "  EARTHQUAKE ",
                        "latitude": 1,
                        "longitude": 2,
                        "mag": 6.2,
                        "time": "2020-01-01T00:00:00Z",
                        "place": "Test earthquake",
                    },
                    {
                        "type": " Volcanic Eruption ",
                        "latitude": 1,
                        "longitude": 2,
                        "mag": 7.0,
                    },
                    {
                        "type": "LANDSLIDE",
                        "latitude": 1,
                        "longitude": 2,
                        "mag": 5.5,
                    },
                ],
            )
            regions = self._regions()
            with patch.object(builder, "load_regions", return_value=regions), patch.object(
                builder, "assign_points", return_value=self._joined_one()
            ):
                result = builder.build(repo, source)

            self.assertEqual(result["inputRowCount"], 3)
            self.assertEqual(result["includedRowCount"], 1)
            self.assertEqual(result["excludedRowCount"], 2)
            self.assertEqual(
                result["excludedTypeCounts"],
                {"landslide": 1, "volcanic eruption": 1},
            )
            output = repo / ".cache" / "motherworld" / "geology" / "providers" / "usgs_comcat" / "eco_test.json"
            payload = json.loads(output.read_text(encoding="utf-8"))
            source_metadata = payload["sections"]["tectonics"]["seismicity"]["source"]
            self.assertEqual(source_metadata["eventTypeFilter"], builder.EVENT_TYPE_FILTER)
            self.assertEqual(source_metadata["inputRowCount"], 3)
            self.assertEqual(source_metadata["includedRowCount"], 1)
            self.assertEqual(source_metadata["excludedRowCount"], 2)
            self.assertEqual(
                source_metadata["excludedTypeCounts"],
                {"landslide": 1, "volcanic eruption": 1},
            )
            self.assertEqual(payload["sections"]["tectonics"]["seismicity"]["eventCount"], 1)

    def test_zero_earthquake_run_replaces_stale_fragments_with_empty_provider(self) -> None:
        with self._repo() as raw_repo:
            repo = Path(raw_repo)
            stale, _ = self._seed_old_fragment(repo)
            source = self._write_csv(
                repo,
                [
                    {
                        "type": "volcanic eruption",
                        "latitude": 1,
                        "longitude": 2,
                        "mag": 6.0,
                    },
                    {
                        "type": "landslide",
                        "latitude": 1,
                        "longitude": 2,
                        "mag": 7.0,
                    },
                ],
            )
            regions = self._regions()
            with patch.object(builder, "load_regions", return_value=regions), patch.object(
                builder, "assign_points", return_value=self._joined_empty()
            ):
                result = builder.build(repo, source)

            provider_dir = builder.provider_output_dir(repo)
            self.assertFalse(stale.exists())
            self.assertEqual(list(provider_dir.glob("*.json")), [])
            self.assertEqual(result["includedRowCount"], 0)
            self.assertEqual(result["excludedRowCount"], 2)
            self.assertEqual(result["fragmentCount"], 0)
            self.assertEqual(list(provider_dir.parent.glob(".usgs_comcat.*")), [])

    def test_missing_or_blank_type_preserves_existing_fragments(self) -> None:
        cases = (
            (
                "missing type column",
                [
                    {"latitude": 1, "longitude": 2, "mag": 6.0},
                ],
                "type column",
            ),
            (
                "blank type value",
                [
                    {"type": "  ", "latitude": 1, "longitude": 2, "mag": 6.0},
                ],
                "blank event types",
            ),
        )
        for name, rows, message in cases:
            with self.subTest(name=name), self._repo() as raw_repo:
                repo = Path(raw_repo)
                stale, original = self._seed_old_fragment(repo)
                source = self._write_csv(repo, rows)
                with self.assertRaisesRegex(ValueError, message):
                    builder.build(repo, source)
                self.assertTrue(stale.exists())
                self.assertEqual(stale.read_text(encoding="utf-8"), original)

    def test_region_validation_failure_preserves_existing_fragments(self) -> None:
        with self._repo() as raw_repo:
            repo = Path(raw_repo)
            stale, original = self._seed_old_fragment(repo)
            source = self._write_csv(
                repo,
                [{"type": "earthquake", "latitude": 1, "longitude": 2, "mag": 6.0}],
            )
            with patch.object(
                builder,
                "load_regions",
                side_effect=ValueError("invalid region inventory"),
            ):
                with self.assertRaisesRegex(ValueError, "invalid region inventory"):
                    builder.build(repo, source)
            self.assertTrue(stale.exists())
            self.assertEqual(stale.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
