from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from netCDF4 import Dataset, date2num

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_freshwater_backbone_cds import build, root_file  # noqa: E402


def make_fixture(path: Path, year: int, value: float) -> None:
    with Dataset(path, "w") as dataset:
        dataset.createDimension("valid_time", 12)
        dataset.createDimension("latitude", 3)
        dataset.createDimension("longitude", 2)
        time = dataset.createVariable("valid_time", "f8", ("valid_time",))
        time.units = f"days since {year}-01-01 00:00:00"
        time.calendar = "standard"
        time[:] = date2num([datetime(year, month, 15) for month in range(1, 13)], time.units, time.calendar)
        dataset.createVariable("latitude", "f4", ("latitude",))[:] = [70.0, 0.0, -70.0]
        dataset.createVariable("longitude", "f4", ("longitude",))[:] = [-20.0, 0.0]
        for index, name in enumerate(("swvl1", "swvl2", "swvl3"), start=1):
            variable = dataset.createVariable(name, "f4", ("valid_time", "latitude", "longitude"))
            variable[:] = value + index


class FreshwaterCdsFixtureTests(unittest.TestCase):
    def test_aliases_valid_time_and_greenland_box_are_handled(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            make_fixture(root_file(directory, 1991), 1991, 0.0)
            make_fixture(root_file(directory, 1992), 1992, 0.0)
            args = SimpleNamespace(
                input_dir=directory,
                start_year=1992,
                end_year=1992,
                baseline_start=1991,
                baseline_end=1991,
                envelope_cache=None,
            )
            payload = build(args)
            self.assertEqual(payload["latestBackboneYear"], 1992)
            self.assertEqual(payload["series"][0]["coverage"], 1.0)
            self.assertEqual(payload["series"][0]["score"], 100.0)
            self.assertIn("acceptedAliases", payload["sources"][0])
            self.assertIn("greenlandBox", payload["method"]["iceExclusion"])


if __name__ == "__main__":
    unittest.main()
