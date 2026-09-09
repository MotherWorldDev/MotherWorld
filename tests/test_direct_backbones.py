import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np

from build_pollution_backbone_direct import (
    aerosol_subset_url,
    discover_merra_month,
    land_mask_subset_url,
    pm25_formula,
    probe_anonymous_access,
)
from build_vegetation_backbone_direct import (
    _score_rows,
    discover_noaa_daily_urls,
    quality_mask,
)
from direct_provider_common import auth_error_message, output_family_path


class FakeResponse:
    def __init__(self, *, text="", status=200, location=None):
        self.text = text
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.ok = status < 400

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return self.response


class DirectBackboneTests(unittest.TestCase):
    def test_noaa_direct_directory_and_sensor_contract(self):
        html = """
        <a href="AVHRR-Land_v005_AVH13C1_NOAA-11_19930101_c20170616093855.nc">a</a>
        <a href="AVHRR-Land_v005_AVH13C1_NOAA-11_19930102_c20170616095314.nc">b</a>
        <a href="VIIRS-Land_v001_JP113C1_NOAA-20_20250101_c20250103153010.nc">wrong-year</a>
        """
        session = FakeSession(FakeResponse(text=html))
        records = discover_noaa_daily_urls(1993, session)
        self.assertEqual([record["date"] for record in records], ["19930101", "19930102"])
        self.assertTrue(records[0]["url"].startswith("https://www.ncei.noaa.gov/data/"))

    def test_quality_mask_decodes_the_two_cdr_qa_layouts(self):
        ndvi = np.full(8, 1000, dtype=np.int16)
        avhrr_qa = np.array([0, 2, 4, 8, 64, 256, 512, 1024], dtype=np.uint16)
        self.assertEqual(quality_mask(ndvi, avhrr_qa, "AVHRR").tolist(), [True, False, False, False, False, False, False, True])

        viirs_qa = np.array([0, 1, 2, 4, 8, 16, 32768, 40], dtype=np.uint32)
        self.assertEqual(quality_mask(ndvi, viirs_qa, "VIIRS").tolist(), [True, True, False, False, True, False, False, False])

    def test_vegetation_score_keeps_baseline_relative_normalization(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("MOTHERWORLD_TEST_TMPDIR") or None) as folder:
            root = Path(folder)
            baseline_path = root / "baseline.npy"
            current_path = root / "current.npy"
            np.save(baseline_path, np.array([[0.5, 0.2], [np.nan, 0.4]], dtype=np.float32))
            np.save(current_path, np.array([[0.25, 0.3], [0.8, 0.4]], dtype=np.float32))
            rows = _score_rows(
                annual_paths={1993: current_path},
                baseline=np.load(baseline_path),
                latitudes=np.array([0.0, 60.0]),
                threshold=0.1,
                start_year=1993,
                sensor_by_year={1993: "AVHRR"},
                observed_days_by_year={1993: 364},
            )
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["score"], 80.0, places=5)
        self.assertAlmostEqual(rows[0]["coverage"], 1.0, places=5)
        self.assertEqual(rows[0]["observedDays"], 364)
        self.assertEqual(rows[0]["expectedDays"], 365)
        self.assertAlmostEqual(rows[0]["temporalCoverage"], 364 / 365, places=5)

    def test_merra_direct_contract_uses_the_five_fields_and_land_mask(self):
        fields = {name: np.array([[1e-9]], dtype=np.float64) for name in ("DUSMASS25", "OCSMASS", "BCSMASS", "SSSMASS25", "SO4SMASS")}
        expected = (4e-9 + SULFATE_AMMONIUM_FACTOR * 1e-9) * 1e9
        self.assertAlmostEqual(float(pm25_formula(fields)[0, 0]), expected, places=7)
        aerosol_url = aerosol_subset_url("M2TMNXAER.5.12.4:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4")
        self.assertIn(".dap.nc4?", aerosol_url)
        for variable in ("DUSMASS25", "OCSMASS", "BCSMASS", "SSSMASS25", "SO4SMASS"):
            self.assertIn(variable, aerosol_url)
        self.assertIn("FRLAND", land_mask_subset_url())
        self.assertNotIn("AREA", land_mask_subset_url())

    def test_merra_metadata_discovery_and_anonymous_probe_are_explicit(self):
        html = '<a href="https://data.gesdisc.earthdata.nasa.gov/data/MERRA2_MONTHLY/M2TMNXAER.5.12.4/1993/MERRA2_200.tavgM_2d_aer_Nx.199301.nc4">M2</a>'
        session = FakeSession(FakeResponse(text=html))
        record = discover_merra_month(1993, 1, session)
        self.assertEqual(record["dataset"], "M2TMNXAER.5.12.4:MERRA2_200.tavgM_2d_aer_Nx.199301.nc4")

        anonymous = FakeSession(FakeResponse(status=302, location="/login/urs"))
        probe = probe_anonymous_access(anonymous)
        self.assertTrue(probe["requiresEarthdataLogin"])
        self.assertIn("Earthdata Login", auth_error_message(probe["url"], probe["status"], probe["location"]))

    def test_outputs_keep_frontend_public_relative_layout(self):
        output = output_family_path(Path("F:/BiomeSummary/.cache/motherworld/v8-build/direct-noaa-nasa"), "pollution")
        self.assertTrue(str(output).endswith("frontend\\public\\data\\indices\\families\\pollution.index.json") or str(output).endswith("frontend/public/data/indices/families/pollution.index.json"))


SULFATE_AMMONIUM_FACTOR = 132.14 / 96.06


if __name__ == "__main__":
    unittest.main()
