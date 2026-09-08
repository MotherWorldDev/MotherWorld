from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from netCDF4 import Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_habitat_backbone_c3s as builder  # noqa: E402


class HabitatInputDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _make_netcdf(path: Path) -> None:
        with Dataset(path, "w") as dataset:
            dataset.createDimension("time", 1)
            dataset.createDimension("latitude", 1)
            dataset.createDimension("longitude", 1)
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = "days since 2001-01-01 00:00:00"
            time[:] = [0.0]
            dataset.createVariable("latitude", "f4", ("latitude",))[:] = [0.0]
            dataset.createVariable("longitude", "f4", ("longitude",))[:] = [0.0]
            dataset.createVariable("lccs_class", "i2", ("time", "latitude", "longitude"))[:] = [[[10]]]

    def test_builder_ignores_sidecars_staging_files_and_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "inputs"
            repo = root / "repo"
            input_dir.mkdir()
            valid = input_dir / "c3s_land_cover_2001.nc"
            self._make_netcdf(valid)
            (input_dir / "c3s_land_cover_2001.nc.request.json").write_text(
                json.dumps({"complete": True}), encoding="utf-8"
            )
            for suffix in (".part", ".staged", ".extracting"):
                (input_dir / f"c3s_land_cover_2001.nc{suffix}").write_bytes(b"incomplete")
            (input_dir / "c3s_land_cover_2002.nc").mkdir()

            argv = [
                "build_habitat_backbone_c3s.py",
                "--repo",
                str(repo),
                "--input-dir",
                str(input_dir),
                "--glob",
                "*.nc*",
                "--start-year",
                "2001",
                "--chunk-rows",
                "1",
            ]
            with patch.object(sys, "argv", argv):
                builder.main()

            output = repo / "frontend/public/data/indices/families/habitat.index.json"
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["latestBackboneYear"], 2001)
            self.assertEqual([row["year"] for row in payload["series"]], [2001])
            self.assertEqual(payload["series"][0]["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
