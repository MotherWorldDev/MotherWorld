import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_earth_health_index.py"
FAMILIES = [
    ("biodiversity", 0.14),
    ("habitat", 0.12),
    ("vegetation", 0.08),
    ("pollution", 0.12),
    ("climate_forcing", 0.15),
    ("cryosphere", 0.09),
    ("ocean", 0.13),
    ("freshwater", 0.11),
    ("ozone", 0.06),
]


class EarthHealthContractTests(unittest.TestCase):
    def temporary_repo(self):
        # CI can point this at an F: task cache; the portable fallback keeps the
        # test runnable elsewhere without making a repository-local scratch tree.
        return tempfile.TemporaryDirectory(dir=os.environ.get("MOTHERWORLD_TEST_TMPDIR") or None)

    def write_fixture(self, folder, *, coverages=(1.0,), scores=None, missing=(), min_effective=0.55):
        repo = Path(folder)
        index_root = repo / "frontend/public/data/indices"
        families_root = index_root / "families"
        families_root.mkdir(parents=True)
        config = {
            "schemaVersion": 2,
            "name": "Earth Health test",
            "historyStartYear": 1993,
            "requireAllFamiliesForHistory": True,
            "minimumFamilyCoverage": 0.45,
            "minimumEffectiveDataCoverage": min_effective,
            "statusThresholds": [{"min": 0, "label": "Test"}],
            "families": [
                {
                    "id": family_id,
                    "label": family_id,
                    "weight": weight,
                    "enabled": True,
                }
                for family_id, weight in FAMILIES
            ],
        }
        (index_root / "earth-health.config.json").write_text(json.dumps(config), encoding="utf-8")
        scores = scores or {family_id: 80.0 for family_id, _ in FAMILIES}
        years = list(range(1993, 1993 + len(coverages)))
        for family_id, _ in FAMILIES:
            if family_id in missing:
                continue
            series = [
                {
                    "year": year,
                    "score": scores[family_id] if not isinstance(scores[family_id], list) else scores[family_id][i],
                    "coverage": coverage,
                    "raw": scores[family_id] if not isinstance(scores[family_id], list) else scores[family_id][i],
                    "unit": "test score",
                }
                for i, (year, coverage) in enumerate(zip(years, coverages))
            ]
            payload = {
                "schemaVersion": 2,
                "familyId": family_id,
                "label": family_id,
                "components": [{"id": "test", "label": "Test component", "weight": 1.0}],
                "series": series,
                "method": {"test": True},
                "sources": [{"id": "synthetic", "label": "Synthetic test fixture"}],
            }
            (families_root / f"{family_id}.index.json").write_text(json.dumps(payload), encoding="utf-8")
        return repo

    def run_builder(self, repo, output=None):
        output = output or (Path(repo) / "frontend/public/data/indices/earth-health.json")
        return subprocess.run(
            [sys.executable, str(BUILDER), "--repo", str(repo), "--output", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_individual_family_floor_is_not_enough_for_effective_coverage(self):
        with self.temporary_repo() as folder:
            repo = self.write_fixture(folder, coverages=(0.50,))
            result = self.run_builder(repo)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("effective data coverage", result.stderr + result.stdout)
            self.assertFalse((repo / "frontend/public/data/indices/earth-health.json").exists())

    def test_complete_timeline_uses_weighted_geometric_mean_and_publishes_threshold(self):
        scores = {family_id: 40.0 + index * 5.0 for index, (family_id, _) in enumerate(FAMILIES)}
        with self.temporary_repo() as folder:
            repo = self.write_fixture(folder, coverages=(1.0, 1.0), scores=scores)
            result = self.run_builder(repo)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((repo / "frontend/public/data/indices/earth-health.json").read_text())
            expected = math.exp(sum(weight * math.log(scores[family_id]) for family_id, weight in FAMILIES))
            self.assertAlmostEqual(payload["score"], expected, places=3)
            self.assertEqual([row["year"] for row in payload["series"]], [1993, 1994])
            self.assertEqual(payload["coverage"]["minimumEffectiveDataCoverage"], 0.55)
            self.assertEqual(payload["coverage"]["effectiveDataCoverage"], 1.0)
            self.assertTrue(payload["coverage"]["sufficient"])

    def test_later_low_effective_coverage_stops_continuous_history(self):
        with self.temporary_repo() as folder:
            repo = self.write_fixture(folder, coverages=(1.0, 0.50, 1.0))
            result = self.run_builder(repo)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((repo / "frontend/public/data/indices/earth-health.json").read_text())
            self.assertEqual(payload["historyStartYear"], 1993)
            self.assertEqual(payload["historyEndYear"], 1993)
            self.assertEqual([row["year"] for row in payload["series"]], [1993])

    def test_diagnostics_do_not_change_historical_score(self):
        with self.temporary_repo() as folder:
            repo = self.write_fixture(folder, coverages=(1.0,))
            first_output = repo / "first.json"
            self.assertEqual(self.run_builder(repo, first_output).returncode, 0)
            before = json.loads(first_output.read_text())
            diagnostics = repo / "frontend/public/data/indices/diagnostics"
            diagnostics.mkdir(parents=True)
            for family_id, _ in FAMILIES:
                (diagnostics / f"{family_id}.diagnostics.json").write_text(
                    json.dumps({"score": 0, "coverage": 1, "earthHealthRole": "diagnostic_only"}),
                    encoding="utf-8",
                )
            second_output = repo / "second.json"
            self.assertEqual(self.run_builder(repo, second_output).returncode, 0)
            after = json.loads(second_output.read_text())
            self.assertEqual(after["score"], before["score"])
            self.assertEqual(after["series"], before["series"])
            self.assertTrue(all(family["diagnostics"] for family in after["families"]))

    def test_missing_family_fails_closed(self):
        with self.temporary_repo() as folder:
            repo = self.write_fixture(folder, missing={"ozone"})
            result = self.run_builder(repo)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ozone", result.stderr + result.stdout)
            self.assertFalse((repo / "frontend/public/data/indices/earth-health.json").exists())


if __name__ == "__main__":
    unittest.main()
