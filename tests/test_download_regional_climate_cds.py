from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from netCDF4 import Dataset, date2num
from shapely.geometry import box

PROJECT_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_ROOT", Path(__file__).resolve().parents[1]))
TEST_TEMP_ROOT = Path(os.environ.get("MOTHERWORLD_TEST_TEMP", PROJECT_ROOT / ".cache/motherworld/v8-task-temp"))
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import analysis_geometry  # noqa: E402
import download_regional_climate_cds as downloader  # noqa: E402


SEVEN_REGIONS = ("eco_117", "eco_121", "eco_130", "eco_267", "eco_509", "eco_562", "eco_609")
ALL_VARIABLES = tuple(downloader.VARIABLES)


def _write_daily_file(path: Path, year: int, *, missing: str | None = None, days: int | None = None, extra_dimension: bool = False) -> None:
    count = days if days is not None else (366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365)
    dates = [datetime(year, 1, 1) + timedelta(days=index) for index in range(count)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with Dataset(path, "w") as dataset:
        dataset.createDimension("valid_time", count)
        dataset.createDimension("latitude", 1)
        dataset.createDimension("longitude", 1)
        if extra_dimension:
            dataset.createDimension("number", 2)
        time = dataset.createVariable("valid_time", "f8", ("valid_time",))
        time.units = f"days since {year}-01-01 00:00:00"
        time.calendar = "standard"
        time[:] = date2num(dates, time.units, time.calendar)
        latitude = dataset.createVariable("latitude", "f4", ("latitude",))
        latitude.units = "degrees_north"
        latitude[:] = [0.0]
        longitude = dataset.createVariable("longitude", "f4", ("longitude",))
        longitude.units = "degrees_east"
        longitude[:] = [0.0]
        for variable, alias, units in (
            ("2m_temperature", "t2m", "K"),
            ("2m_dewpoint_temperature", "d2m", "K"),
            ("10m_u_component_of_wind", "u10", "m s**-1"),
            ("10m_v_component_of_wind", "v10", "m s**-1"),
        ):
            if variable == missing:
                continue
            dimensions = ("valid_time", "latitude", "longitude")
            if extra_dimension and variable == "2m_temperature":
                dimensions = ("valid_time", "number", "latitude", "longitude")
            output = dataset.createVariable(alias, "f4", dimensions)
            output.units = units
            output[:] = 1.0


def _region_request_parts(region, kind: str, year: int, output_dir: Path, months: list[int]) -> tuple[Path, str]:
    minx, miny, maxx, maxy = region.geometry.bounds
    pad = 0.3 if kind in ("land", "lakes") else 0.6
    bbox = [min(90.0, maxy + pad), max(-180.0, minx - pad), max(-90.0, miny - pad), min(180.0, maxx + pad)]
    request = downloader.request_for(kind, year, bbox, months=months, variables=list(ALL_VARIABLES))
    metadata, identity = downloader._geometry_provenance(region, kind)
    request_id = downloader.fingerprint(downloader.DATASET_BY_KIND[kind], request, region.region_id, region.geometry.wkb, identity)
    month_token = "-".join(f"{month:02d}" for month in months)
    return output_dir / kind / region.region_id / f"{year}.m{month_token}.{request_id[:16]}.nc", request_id


class RegionalClimateDownloaderTests(unittest.TestCase):
    def setUp(self):
        analysis_geometry._validated_overrides.cache_clear()

    def test_seven_maintained_footprints_drive_distinct_requests(self):
        regions = downloader.region_catalog(PROJECT_ROOT, "land", list(SEVEN_REGIONS))
        by_id = {region.region_id: region for region in regions}
        overrides = analysis_geometry.load_query_geometry_overrides()
        self.assertEqual(set(by_id), set(SEVEN_REGIONS))
        fingerprints = {}
        for region_id in SEVEN_REGIONS:
            region = by_id[region_id]
            self.assertTrue(region.geometry.equals(overrides[region_id][0]), region_id)
            metadata, identity = downloader._geometry_provenance(region, "land")
            self.assertTrue(metadata["overrideApplied"], region_id)
            self.assertEqual(metadata["geometryFingerprint"], metadata["geometryOverride"]["registryGeometryFingerprint"])
            request = downloader.request_for("land", 1991, list(region.geometry.bounds), months=[1])
            fingerprints[region_id] = downloader.fingerprint(
                downloader.DATASET_BY_KIND["land"], request, region_id, region.geometry.wkb, identity
            )
        self.assertEqual(len(set(fingerprints.values())), len(SEVEN_REGIONS))
        # These official footprints are close geographically but must remain
        # independently identifiable in the CDS request cache.
        self.assertNotEqual(fingerprints["eco_509"], fingerprints["eco_609"])

    def test_invalid_month_selection_is_rejected(self):
        for value in ("", "0", "13", "3:1", "1,,2", "1,1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                downloader.parse_months(value)
        with self.assertRaises(ValueError):
            downloader.request_for("land", 2001, [1, 2, -1, 3], months=[0])

    def test_leap_and_nonleap_full_year_coverage_is_strict(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            directory = Path(folder)
            leap = directory / "leap.nc"
            nonleap = directory / "nonleap.nc"
            short_leap = directory / "short-leap.nc"
            _write_daily_file(leap, 2000)
            _write_daily_file(nonleap, 2001)
            _write_daily_file(short_leap, 2000, days=365)
            self.assertEqual(downloader.valid_daily_file(leap, 2000), (True, "netcdf-readable"))
            self.assertEqual(downloader.valid_daily_file(nonleap, 2001), (True, "netcdf-readable"))
            valid, reason = downloader.valid_daily_file(short_leap, 2000)
            self.assertFalse(valid)
            self.assertIn("unexpected-daily-count:365 expected 366", reason)

    def test_missing_variable_and_required_dimension_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            directory = Path(folder)
            missing_variable = directory / "missing-variable.nc"
            missing_dimension = directory / "extra-dimension.nc"
            _write_daily_file(missing_variable, 2001, missing="2m_dewpoint_temperature")
            _write_daily_file(missing_dimension, 2001, extra_dimension=True)
            valid, reason = downloader.valid_daily_file(missing_variable, 2001)
            self.assertFalse(valid)
            self.assertEqual(reason, "missing-variables:2m_dewpoint_temperature")
            valid, reason = downloader.valid_daily_file(missing_dimension, 2001)
            self.assertFalse(valid)
            self.assertTrue(reason.startswith("missing-required-dimensions:2m_temperature:"), reason)

    def test_successful_download_sidecar_records_exact_geometry_provenance(self):
        region = downloader.region_catalog(PROJECT_ROOT, "land", ["eco_509"])[0]
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            output_dir = Path(folder)
            source = output_dir / "source.nc"
            _write_daily_file(source, 2001, days=31)

            class FakeClient:
                def retrieve(self, _dataset, _request):
                    return SimpleNamespace(content_length=source.stat().st_size, location=str(source))

            def fake_download(_location, *, target, **_kwargs):
                Path(target).write_bytes(source.read_bytes())

            with patch.object(downloader, "make_client", return_value=FakeClient()), patch.object(
                downloader.multiurl, "download", side_effect=fake_download
            ):
                result = downloader.download_one(region, "land", 2001, output_dir, Path("credentials"), 0.3, [1], list(ALL_VARIABLES))

            target = Path(result["path"])
            sidecar = json.loads(downloader.sidecar_path(target).read_text(encoding="utf-8"))
            expected_metadata, expected_identity = downloader._geometry_provenance(region, "land")
            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(sidecar["requestFingerprint"], result["requestFingerprint"])
            self.assertEqual(sidecar["analysisGeometry"], expected_metadata)
            self.assertEqual(sidecar["analysisGeometryCacheIdentity"], expected_identity)
            self.assertEqual(sidecar["geometryIdentity"], expected_identity)
            self.assertTrue(sidecar["complete"])
            self.assertFalse(target.with_suffix(target.suffix + ".part").exists())
    def test_failed_replacement_preserves_existing_valid_target_and_sidecar(self):
        region = SimpleNamespace(region_id="eco_1", name="Example", geometry=box(10, 10, 11, 11))
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as folder:
            output_dir = Path(folder)
            target, request_id = _region_request_parts(region, "land", 2001, output_dir, [1])
            _write_daily_file(target, 2001, days=31)
            previous_bytes = target.read_bytes()
            previous_sidecar = {"dataset": downloader.DATASET_BY_KIND["land"], "requestFingerprint": "prior-request", "sentinel": "preserve"}
            sidecar = downloader.sidecar_path(target)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(previous_sidecar) + "\n", encoding="utf-8")

            invalid = output_dir / "invalid.nc"
            _write_daily_file(invalid, 2001, missing="2m_dewpoint_temperature", days=31)

            class FakeClient:
                def retrieve(self, _dataset, _request):
                    return SimpleNamespace(content_length=invalid.stat().st_size, location=str(invalid))

            def fake_download(_location, *, target, **_kwargs):
                Path(target).write_bytes(invalid.read_bytes())

            with patch.object(downloader, "make_client", return_value=FakeClient()), patch.object(
                downloader.multiurl, "download", side_effect=fake_download
            ):
                with self.assertRaisesRegex(RuntimeError, "validation failed: missing-variables"):
                    downloader.download_one(region, "land", 2001, output_dir, Path("credentials"), 0.3, [1], list(ALL_VARIABLES))

            self.assertEqual(target.read_bytes(), previous_bytes)
            self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), previous_sidecar)
            self.assertFalse(target.with_suffix(target.suffix + ".staged").exists())


if __name__ == "__main__":
    unittest.main()
