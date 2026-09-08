from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from netCDF4 import Dataset, date2num

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_era5_land_monthly_cds as downloader  # noqa: E402


class Era5LandDownloadTests(unittest.TestCase):
    @staticmethod
    def _make_netcdf(path: Path, year: int, value: float) -> None:
        with Dataset(path, "w") as dataset:
            dataset.createDimension("valid_time", 12)
            dataset.createDimension("latitude", 1)
            dataset.createDimension("longitude", 1)
            time = dataset.createVariable("valid_time", "f8", ("valid_time",))
            time.units = f"days since {year}-01-01 00:00:00"
            time.calendar = "standard"
            time[:] = date2num([datetime(year, month, 15) for month in range(1, 13)], time.units, time.calendar)
            dataset.createVariable("latitude", "f4", ("latitude",))[:] = [0.0]
            dataset.createVariable("longitude", "f4", ("longitude",))[:] = [0.0]
            for index, name in enumerate(("swvl1", "swvl2", "swvl3"), start=1):
                dataset.createVariable(name, "f4", ("valid_time", "latitude", "longitude"))[:] = value + index

    def test_interrupted_changed_request_does_not_reuse_completed_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_a = directory / "source-a.nc"
            source_b = directory / "source-b.nc"
            self._make_netcdf(source_a, 2001, 1.0)
            self._make_netcdf(source_b, 2001, 2.0)
            area_a = [90.0, -180.0, -60.0, 180.0]
            area_b = [90.0, -10.0, -60.0, 10.0]
            state = {"source": source_a, "fail": False}

            class FakeClient:
                def retrieve(self, _dataset, request):
                    source = source_a if request["area"] == area_a else source_b
                    return SimpleNamespace(content_length=source.stat().st_size, location=str(source), asset={})

            def fake_download(_location, *, target, **_kwargs):
                source = state["source"]
                Path(target).write_bytes(source.read_bytes())
                if state["fail"]:
                    raise RuntimeError("simulated interrupted transfer")

            target = directory / "era5_land_monthly_soil_moisture_2001.nc"
            with patch.object(downloader, "make_client", return_value=FakeClient()), patch.object(downloader.multiurl, "download", side_effect=fake_download):
                first = downloader.download_one(2001, directory, Path("credentials"), area_a)
                self.assertEqual(first["status"], "downloaded")
                state["source"] = source_b
                state["fail"] = True
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    downloader.download_one(2001, directory, Path("credentials"), area_b)
                completed = json.loads(downloader.metadata_path(target).read_text(encoding="utf-8"))
                self.assertEqual(completed["fingerprint"], downloader.request_fingerprint(downloader.request_for(2001, area_a)))
                state["fail"] = False
                retry = downloader.download_one(2001, directory, Path("credentials"), area_b)
            self.assertEqual(retry["status"], "downloaded")
            self.assertEqual(json.loads(downloader.metadata_path(target).read_text(encoding="utf-8"))["fingerprint"], downloader.request_fingerprint(downloader.request_for(2001, area_b)))
            self.assertEqual(target.read_bytes(), source_b.read_bytes())

    def test_year_validation_requires_decodable_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing-time.nc"
            with Dataset(path, "w") as dataset:
                dataset.createDimension("latitude", 1)
                dataset.createDimension("longitude", 1)
                for name in ("swvl1", "swvl2", "swvl3"):
                    dataset.createVariable(name, "f4", ("latitude", "longitude"))[:] = [[1.0]]
            valid, reason = downloader.valid_netcdf(path, expected_year=2001)
            self.assertFalse(valid)
            self.assertEqual(reason, "missing-time-variable")


if __name__ == "__main__":
    unittest.main()
