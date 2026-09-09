import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_pollution_backbone_direct as pollution
from direct_provider_common import ProviderAccessError


class FakeResponse:
    def __init__(self, *, text="", content=b"", status=200, location=None, content_type=None):
        self.text = text
        self.content = content or text.encode("utf-8")
        self.status_code = status
        self.headers = {}
        if location:
            self.headers["Location"] = location
        if content_type:
            self.headers["Content-Type"] = content_type
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size=1024):
        yield self.content

    def close(self):
        pass


class QueueSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class PollutionHardeningTests(unittest.TestCase):
    def temp_folder(self):
        base = ROOT / ".cache"
        base.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=str(base))

    def test_mask_request_is_native_frland_with_no_area_variable(self):
        constraint = unquote(urlsplit(pollution.land_mask_subset_url()).query)
        self.assertIn("/FRLAND[0:0][0:360][0:575]", constraint)
        self.assertIn("/lat[0:360]", constraint)
        self.assertIn("/lon[0:575]", constraint)
        self.assertIn("/time[0:0]", constraint)
        self.assertNotIn("AREA", constraint)

    def test_derived_cell_areas_cover_the_sphere(self):
        import numpy as np
        latitude = np.linspace(-90.0, 90.0, pollution.NATIVE_LAT_COUNT)
        longitude = -180.0 + np.arange(pollution.NATIVE_LON_COUNT) * 0.625
        area = pollution.derive_native_cell_area(latitude, longitude)
        self.assertEqual(area.shape, (361, 576))
        self.assertTrue(np.all(area > 0))
        self.assertAlmostEqual(float(area.sum()), 4.0 * np.pi * pollution.EARTH_RADIUS_M**2, places=2)

    def test_cmr_json_discovery_accepts_stream_selected_by_cmr(self):
        html = json.dumps({"feed": {"entry": [
            {"producer_granule_id": "MERRA2_400.tavgM_2d_aer_Nx.202501.nc4"}
        ]}})
        record = pollution.discover_merra_month(
            2025,
            1,
            QueueSession([FakeResponse(text=html)]),
        )
        self.assertEqual(record["dataset"], "M2TMNXAER.5.12.4:MERRA2_400.tavgM_2d_aer_Nx.202501.nc4")

    def test_preflight_report_keeps_storage_paths_private(self):
        responses = [
            FakeResponse(status=302, location="/login/urs"),
            FakeResponse(text='<a href="MERRA2_200.tavgM_2d_aer_Nx.199301.nc4">a</a>'),
            FakeResponse(text='<a href="MERRA2_101.const_2d_asm_Nx.00000000.nc4">m</a>'),
        ]
        with self.temp_folder() as folder:
            output = Path(folder)
            report_path = pollution.write_access_report(
                output,
                QueueSession(responses),
                token_present=False,
                start_year=1993,
                end_year=2025,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertNotIn("rawRoot", report)
        self.assertNotIn("raw_root", report)
        self.assertEqual(report["publicCmrPreflight"]["aerosol"]["status"], "ok")
        self.assertEqual(report["publicCmrPreflight"]["landMask"]["status"], "ok")

    def test_nonempty_unverified_file_is_not_reused_with_skip_download(self):
        with self.temp_folder() as folder:
            destination = Path(folder) / "bad.subset.nc4"
            destination.write_text("<html>NASA login error</html>", encoding="utf-8")
            identity = pollution._request_identity(
                url="https://example.invalid/a",
                collection_id=pollution.MERRA_AER_COLLECTION,
                dataset_id="M2TMNXAER.5.12.4:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4",
                variables=pollution.AEROSOL_VARIABLES,
                include_time_dimension=True,
            )
            with self.assertRaises(ProviderAccessError):
                pollution._download_subset(
                    session=QueueSession([]),
                    url=identity["url"],
                    destination=destination,
                    token="test-token",
                    timeout=1,
                    skip_download=True,
                    request_identity=identity,
                    variable_names=pollution.AEROSOL_VARIABLES,
                    include_time_dimension=True,
                    minimum_free_gib=0,
                )
            self.assertFalse(destination.exists())
            self.assertTrue(list(Path(folder).glob(".bad.subset.nc4.invalid-*")))

    def test_http_200_html_response_is_rejected_and_quarantined(self):
        with self.temp_folder() as folder:
            destination = Path(folder) / "bad.subset.nc4"
            identity = pollution._request_identity(
                url="https://example.invalid/a",
                collection_id=pollution.MERRA_AER_COLLECTION,
                dataset_id="M2TMNXAER.5.12.4:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4",
                variables=pollution.AEROSOL_VARIABLES,
                include_time_dimension=True,
            )
            with self.assertRaises(ProviderAccessError):
                pollution._download_subset(
                    session=QueueSession([FakeResponse(text="<html>backend error</html>", content_type="text/html")]),
                    url=identity["url"],
                    destination=destination,
                    token="test-token",
                    timeout=1,
                    skip_download=False,
                    request_identity=identity,
                    variable_names=pollution.AEROSOL_VARIABLES,
                    include_time_dimension=True,
                    minimum_free_gib=0,
                )
            self.assertFalse(destination.exists())
            self.assertTrue(list(Path(folder).glob(".bad.subset.nc4.invalid-*")))

    def test_truncated_hdf_header_is_rejected(self):
        with self.temp_folder() as folder:
            path = Path(folder) / "truncated.nc4"
            path.write_bytes(b"\x89HDF\r\n\x1a\ntruncated")
            with self.assertRaises(ProviderAccessError):
                pollution._validate_subset_netcdf(
                    path,
                    pollution.AEROSOL_VARIABLES,
                    include_time_dimension=True,
                )

    def test_partial_annual_checkpoint_is_never_complete(self):
        with self.temp_folder() as folder:
            path = Path(folder) / "1993.json"
            pollution.write_json(path, {
                "schemaVersion": pollution.CHECKPOINT_SCHEMA_VERSION,
                "status": "complete",
                "year": 1993,
                "annualIdentity": {},
                "months": [{"month": month} for month in range(1, 12)],
                "row": {"year": 1993, "temporalCoverage": 1.0, "observedMonths": 12},
            })
            result = pollution._read_complete_annual_checkpoint(
                path,
                year=1993,
                annual_identity={},
                month_records=[],
                work_root=Path(folder),
                subset_paths={},
            )
        self.assertIsNone(result)


    def test_verified_nasa_probe_schema_and_month(self):
        mask = ROOT / ".cache/motherworld/v8-build/nasa-pollution-parent-pilot/verified-probes/mask.nc4"
        aerosol = ROOT / ".cache/motherworld/v8-build/nasa-pollution-parent-pilot/verified-probes/199301.nc4"
        if not mask.exists() or not aerosol.exists():
            self.skipTest("verified NASA probe artifacts are not staged")
        mask_result = pollution._validate_subset_netcdf(mask, pollution.MASK_VARIABLES, include_time_dimension=True)
        aerosol_result = pollution._validate_subset_netcdf(
            aerosol,
            pollution.AEROSOL_VARIABLES,
            include_time_dimension=True,
            expected_year=1993,
            expected_month=1,
        )
        self.assertEqual(mask_result["variables"], ["FRLAND"])
        self.assertEqual(aerosol_result["variables"], list(pollution.AEROSOL_VARIABLES))
        with self.assertRaises(ProviderAccessError):
            pollution._validate_subset_netcdf(
                aerosol,
                pollution.AEROSOL_VARIABLES,
                include_time_dimension=True,
                expected_year=1993,
                expected_month=2,
            )

    def test_annual_checkpoint_requires_exact_current_month_set(self):
        with self.temp_folder() as folder:
            path = Path(folder) / "1993.json"
            pollution.write_json(path, {
                "schemaVersion": pollution.CHECKPOINT_SCHEMA_VERSION,
                "status": "complete",
                "year": 1993,
                "annualIdentity": {},
                "months": [{"month": month} for month in range(1, 13)],
                "row": {"year": 1993, "temporalCoverage": 1.0, "observedMonths": 12},
            })
            result = pollution._read_complete_annual_checkpoint(
                path,
                year=1993,
                annual_identity={},
                month_records=[{"month": month} for month in range(1, 12)],
                work_root=Path(folder),
                subset_paths={},
            )
        self.assertIsNone(result)

    def test_annual_checkpoint_rejects_corrupt_row_and_source_months(self):
        records = [
            {
                "month": month,
                "dataset": f"dataset-{month}",
                "requestIdentity": {"requestHash": f"hash-{month}"},
            }
            for month in range(1, 13)
        ]
        identity = pollution._annual_identity(
            start_year=1993,
            end_year=2025,
            year=1993,
            month_records=records,
            mask_request_identity={"requestHash": "mask-hash"},
            healthy_ugm3=5.0,
            critical_ugm3=50.0,
        )
        months = [
            {
                "month": month,
                "dataset": f"dataset-{month}",
                "requestHash": f"hash-{month}",
                "days": 31,
            }
            for month in range(1, 13)
        ]
        row = {
            "year": 1993,
            "score": 72.0,
            "coverage": 1.0,
            "temporalCoverage": 1.0,
            "observedMonths": 12,
            "expectedMonths": 12,
            "raw": 12.0,
            "sourceMonths": months,
        }
        with self.temp_folder() as folder:
            path = Path(folder) / "1993.json"
            pollution.write_json(path, {
                "schemaVersion": pollution.CHECKPOINT_SCHEMA_VERSION,
                "status": "complete",
                "year": 1993,
                "annualIdentity": identity,
                "months": months,
                "row": {**row, "score": 101.0},
            })
            subset_paths = {month: Path(folder) / f"{month}.nc4" for month in range(1, 13)}
            with patch.object(pollution, "_read_month_checkpoint", return_value={"status": "complete"}):
                self.assertIsNone(pollution._read_complete_annual_checkpoint(
                    path,
                    year=1993,
                    annual_identity=identity,
                    month_records=records,
                    work_root=Path(folder),
                    subset_paths=subset_paths,
                ))
            pollution.write_json(path, {
                "schemaVersion": pollution.CHECKPOINT_SCHEMA_VERSION,
                "status": "complete",
                "year": 1993,
                "annualIdentity": identity,
                "months": months,
                "row": {**row, "sourceMonths": [{**months[0], "requestHash": "stale"}] + months[1:]},
            })
            with patch.object(pollution, "_read_month_checkpoint", return_value={"status": "complete"}):
                self.assertIsNone(pollution._read_complete_annual_checkpoint(
                    path,
                    year=1993,
                    annual_identity=identity,
                    month_records=records,
                    work_root=Path(folder),
                    subset_paths=subset_paths,
                ))

    def test_token_file_strips_utf8_bom_without_output(self):
        with self.temp_folder() as folder:
            token_file = Path(folder) / "earthdata-token.txt"
            token_file.write_bytes(b"\xef\xbb\xbfsecret-token\n")
            self.assertEqual(pollution._read_nasa_bearer_token(token_file), "secret-token")

    def test_open_calendar_year_is_rejected(self):
        args = SimpleNamespace(
            start_year=1993,
            end_year=date.today().year,
            healthy_ugm3=5.0,
            critical_ugm3=50.0,
            min_free_gib=40.0,
        )
        with self.assertRaises(ValueError):
            pollution._validate_build_args(args)

    def test_storage_reserve_is_enforced(self):
        with patch.object(pollution.shutil, "disk_usage", return_value=SimpleNamespace(free=1024)):
            with self.assertRaises(ProviderAccessError):
                pollution.ensure_free_space(Path("F:/"), 40, context="test")


if __name__ == "__main__":
    unittest.main()
