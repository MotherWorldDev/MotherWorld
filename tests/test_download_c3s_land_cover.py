from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from netCDF4 import Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_c3s_land_cover as downloader  # noqa: E402
from download_c3s_land_cover import extract_if_zip, request_for, request_fingerprint, valid_netcdf  # noqa: E402


class C3SLandCoverDownloadTests(unittest.TestCase):
    def test_zip_response_is_extracted_and_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            member = directory / "member.nc"
            with Dataset(member, "w") as dataset:
                dataset.createDimension("time", 1)
                dataset.createDimension("latitude", 1)
                dataset.createDimension("longitude", 1)
                time = dataset.createVariable("time", "f8", ("time",))
                time.units = "days since 2001-01-01 00:00:00"
                time[:] = [0.0]
                dataset.createVariable("latitude", "f4", ("latitude",))[:] = [0.0]
                dataset.createVariable("longitude", "f4", ("longitude",))[:] = [0.0]
                dataset.createVariable("lccs_class", "i2", ("time", "latitude", "longitude"))[:] = [[[190]]]

            archive_path = directory / "response.part"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(member, "nested/land_cover.nc")
            target = directory / "c3s_land_cover_2001.nc"
            extract_if_zip(archive_path, target.with_suffix(target.suffix + ".staged"))
            self.assertTrue(valid_netcdf(target.with_suffix(target.suffix + ".staged"), expected_year=2001)[0])

    def test_request_fingerprint_changes_with_area_year_and_catalog_version(self):
        request = request_for(2001, [90.0, -180.0, -90.0, 180.0])
        self.assertNotEqual(request_fingerprint(request), request_fingerprint(request_for(2001, [90.0, -10.0, -90.0, 10.0])))
        self.assertNotEqual(request_fingerprint(request), request_fingerprint(request_for(2002, [90.0, -180.0, -90.0, 180.0])))
        self.assertNotEqual(request_fingerprint(request), request_fingerprint(request_for(2016, [90.0, -180.0, -90.0, 180.0])))

    @staticmethod
    def _make_netcdf(path: Path, year: int, value: int) -> None:
        with Dataset(path, "w") as dataset:
            dataset.createDimension("time", 1)
            dataset.createDimension("latitude", 1)
            dataset.createDimension("longitude", 1)
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = f"days since {year}-01-01 00:00:00"
            time[:] = [0.0]
            dataset.createVariable("latitude", "f4", ("latitude",))[:] = [0.0]
            dataset.createVariable("longitude", "f4", ("longitude",))[:] = [0.0]
            dataset.createVariable("lccs_class", "i2", ("time", "latitude", "longitude"))[:] = [[[value]]]

    def test_interrupted_changed_request_does_not_reuse_completed_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_a = directory / "source-a.nc"
            source_b = directory / "source-b.nc"
            self._make_netcdf(source_a, 2001, 190)
            self._make_netcdf(source_b, 2001, 10)
            area_a = [90.0, -180.0, -90.0, 180.0]
            area_b = [90.0, -10.0, -90.0, 10.0]
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

            target = directory / "c3s_land_cover_2001.nc"
            with patch.object(downloader, "make_client", return_value=FakeClient()), patch.object(downloader.multiurl, "download", side_effect=fake_download):
                first = downloader.download_one(2001, directory, Path("credentials"), area_a)
                self.assertEqual(first["status"], "downloaded")
                state["source"] = source_b
                state["fail"] = True
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    downloader.download_one(2001, directory, Path("credentials"), area_b)
                completed = json.loads(downloader.metadata_path(target).read_text(encoding="utf-8"))
                self.assertEqual(completed["fingerprint"], request_fingerprint(request_for(2001, area_a)))
                state["fail"] = False
                retry = downloader.download_one(2001, directory, Path("credentials"), area_b)
            self.assertEqual(retry["status"], "downloaded")
            self.assertEqual(json.loads(downloader.metadata_path(target).read_text(encoding="utf-8"))["fingerprint"], request_fingerprint(request_for(2001, area_b)))
            self.assertEqual(target.read_bytes(), source_b.read_bytes())


if __name__ == "__main__":
    unittest.main()
