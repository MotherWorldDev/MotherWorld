import json
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np

from build_vegetation_backbone_direct import (
    DEFAULT_MAX_SINGLE_RAW_BYTES,
    DEFAULT_MAX_OWNED_RAW_BYTES,
    _append_ownership,
    _check_storage_budget,
    _cleanup_checkpoint_owned,
    _load_owned_records,
    _ownership_path,
    _valid_annual_checkpoint,
    process_year,
    read_daily_working_grid,
    sha256_file,
)
from direct_provider_common import ProviderAccessError
from run_vegetation_backbone_worker import require_f_path


TEST_TMP = ROOT / ".cache" / "tmp" / "vegetation-stream-tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)
REAL_SOURCE = (
    ROOT
    / "MotherWorld-v8-dataset-downloader"
    / "earth_health_backbone_raw"
    / "vegetation"
    / "noaa_cdr"
    / "1993"
    / "AVHRR-Land_v005_AVH13C1_NOAA-11_19930101_c20170616093855.nc"
)

REAL_VIIRS_SOURCE = (
    ROOT
    / "MotherWorld-v8-dataset-downloader"
    / "earth_health_backbone_raw"
    / "vegetation"
    / "noaa_cdr"
    / "2025"
    / "VIIRS-Land_v001_JP113C1_NOAA-20_20250101_c20250103153010.nc"
)
class VegetationStreamingTests(unittest.TestCase):

    def test_hash_and_ownership_ledger_are_restartable(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            source = root / "source.nc"
            source.write_bytes(b"native-cdr-provenance")
            self.assertEqual(
                sha256_file(source),
                "2952b629e5b15eb4c687d6ef86a869a18472f2015807944318f9f31fca90bffa",
            )
            ledger = root / "stream-ownership.jsonl"
            record = {"path": str(source.resolve()), "status": "downloaded", "sha256": sha256_file(source), "bytes": source.stat().st_size}
            _append_ownership(ledger, record)
            loaded = _load_owned_records(ledger)
            self.assertEqual(loaded[str(source.resolve())]["sha256"], record["sha256"])
            _append_ownership(ledger, {"path": str(source.resolve()), "status": "deleted"})
            self.assertEqual(_load_owned_records(ledger), {})

    def test_storage_guard_enforces_raw_cap_and_free_space_reserve(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            owned_path = root / "vegetation" / "noaa_cdr" / "1982" / "owned.nc"
            owned_path.parent.mkdir(parents=True)
            owned_path.write_bytes(b"1234567")
            owned = {str(owned_path.resolve()): {"status": "downloaded"}}
            with self.assertRaisesRegex(ProviderAccessError, "raw retention cap"):
                _check_storage_budget(
                    raw_root=root,
                    owned=owned,
                    incoming_bytes=4,
                    max_owned_raw_bytes=10,
                    reserve_bytes=1,
                )
            with self.assertRaisesRegex(ProviderAccessError, "disk reserve"):
                _check_storage_budget(
                    raw_root=root,
                    owned={},
                    incoming_bytes=1,
                    max_owned_raw_bytes=DEFAULT_MAX_OWNED_RAW_BYTES,
                    reserve_bytes=10**18,
                )

    def test_mismatched_checkpoint_cannot_take_fastpath_or_clean_raw(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            raw_root = root / "raw"
            work_root = root / "work"
            source = raw_root / "vegetation" / "noaa_cdr" / "1993" / "fake.nc"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            annual_path = work_root / "annual" / "1993.npy"
            annual_path.parent.mkdir(parents=True)
            np.save(annual_path, np.array([[0.2]], dtype=np.float32))
            grid_path = work_root / "grid.json"
            grid_path.write_text(json.dumps({"coarsen": 8, "latitude": [0.0], "longitude": [0.0], "sensor": "AVHRR"}), encoding="utf-8")
            checkpoint_path = work_root / "annual" / "1993.manifest.json"
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "year": 1993,
                        "sensor": "VIIRS",
                        "coarsen": 8,
                        "annualFile": "annual\\1993.npy",
                        "gridFile": "grid.json",
                        "records": [{"catalogId": "1993/fake.nc", "file": "vegetation\\noaa_cdr\\1993\\fake.nc", "owned": True}],
                    }
                ),
                encoding="utf-8",
            )
            records = [{"date": "19930101", "name": "fake.nc", "url": "https://example.invalid/fake.nc"}]
            self.assertFalse(
                _valid_annual_checkpoint(
                    annual_path=annual_path,
                    checkpoint_path=checkpoint_path,
                    grid_path=grid_path,
                    year=1993,
                    sensor="AVHRR",
                    coarsen=8,
                    records=records,
                )
            )
            with patch("build_vegetation_backbone_direct._cleanup_checkpoint_owned") as cleanup, patch(
                "build_vegetation_backbone_direct.read_daily_working_grid",
                side_effect=ProviderAccessError("forced recompute"),
            ):
                with self.assertRaisesRegex(ProviderAccessError, "forced recompute"):
                    process_year(
                        year=1993,
                        records=records,
                        sensor="AVHRR",
                        raw_root=raw_root,
                        work_root=work_root,
                        coarsen=8,
                        session=object(),
                        retain_raw=False,
                        skip_download=True,
                        timeout=5,
                    )
            cleanup.assert_not_called()
            self.assertTrue(source.exists(), "invalid cached metadata must not delete source raw")
    def test_worker_rejects_c_drive_write_targets(self):
        with self.assertRaises(SystemExit):
            require_f_path("--output-root", Path("C:/outside-workspace"))

    def test_real_staged_viirs_file_uses_native_viirs_qa_and_working_grid(self):
        if not REAL_VIIRS_SOURCE.exists():
            self.skipTest("the staged real NOAA VIIRS validation file is not present")
        if importlib.util.find_spec("netCDF4") is None:
            self.skipTest("netCDF4 is not installed in this test runtime")
        values, latitudes, longitudes = read_daily_working_grid(REAL_VIIRS_SOURCE, "VIIRS", 8)
        self.assertEqual(values.shape, (362, 900))
        self.assertEqual(latitudes.shape, (362,))
        self.assertEqual(longitudes.shape, (900,))
        self.assertGreater(int(np.isfinite(values).sum()), 0)
        self.assertGreater(float(np.nanmean(values)), 0.0)
    def test_download_path_hashes_and_deletes_only_worker_owned_raw_after_checkpoint(self):
        class Response:
            status_code = 200
            ok = True
            headers = {"Content-Length": "4"}

            def iter_content(self, chunk_size=1024 * 1024):
                yield b"data"

            def close(self):
                return None

        class Session:
            def head(self, url, **kwargs):
                return Response()

            def get(self, url, **kwargs):
                return Response()

        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            raw_root = root / "raw"
            work_root = root / "work"
            values = np.array([[0.2]], dtype=np.float32)
            with patch("build_vegetation_backbone_direct.read_daily_working_grid", return_value=(values, np.array([0.0]), np.array([0.0]))):
                annual_path, _ = process_year(
                    year=1993,
                    records=[{"date": "19930101", "name": "fake.nc", "url": "https://example.invalid/fake.nc"}],
                    sensor="AVHRR",
                    raw_root=raw_root,
                    work_root=work_root,
                    coarsen=8,
                    session=Session(),
                    retain_raw=False,
                    skip_download=False,
                    timeout=5,
                    max_owned_raw_bytes=16,
                    reserve_bytes=1,
                    max_single_raw_bytes=16,
                )
            destination = raw_root / "vegetation" / "noaa_cdr" / "1993" / "fake.nc"
            checkpoint = json.loads((work_root / "annual" / "1993.manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(annual_path.exists())
            self.assertFalse(destination.exists())
            self.assertTrue(checkpoint["records"][0]["owned"])
            self.assertEqual(checkpoint["records"][0]["bytes"], 4)
            self.assertEqual(
                checkpoint["records"][0]["sha256"],
                "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7",
            )
            self.assertEqual(_load_owned_records(_ownership_path(raw_root)), {})
    def test_real_staged_avhrr_file_is_reduced_and_owned_raw_is_deleted_after_checkpoint(self):
        if not REAL_SOURCE.exists():
            self.skipTest("the staged real NOAA AVHRR validation file is not present")
        if importlib.util.find_spec("netCDF4") is None:
            self.skipTest("netCDF4 is not installed in this test runtime")
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root = Path(folder)
            raw_root = root / "raw"
            destination = raw_root / "vegetation" / "noaa_cdr" / "1993" / REAL_SOURCE.name
            destination.parent.mkdir(parents=True)
            # Copy the real staged source into an isolated F: test root; the staged original stays untouched.
            destination.write_bytes(REAL_SOURCE.read_bytes())
            ledger = _ownership_path(raw_root)
            digest = sha256_file(destination)
            _append_ownership(
                ledger,
                {
                    "path": str(destination.resolve()),
                    "status": "downloaded",
                    "year": 1993,
                    "date": "19930101",
                    "url": "https://www.ncei.noaa.gov/data/land-normalized-difference-vegetation-index/access/1993/" + REAL_SOURCE.name,
                    "bytes": destination.stat().st_size,
                    "sha256": digest,
                },
            )
            work_root = root / "work"
            annual_path, grid = process_year(
                year=1993,
                records=[
                    {
                        "date": "19930101",
                        "name": REAL_SOURCE.name,
                        "url": "https://www.ncei.noaa.gov/data/land-normalized-difference-vegetation-index/access/1993/" + REAL_SOURCE.name,
                    }
                ],
                sensor="AVHRR",
                raw_root=raw_root,
                work_root=work_root,
                coarsen=8,
                session=object(),
                retain_raw=False,
                skip_download=True,
                timeout=30,
                ownership=_load_owned_records(ledger),
                ownership_path=ledger,
            )
            annual = np.load(annual_path)
            checkpoint = json.loads((work_root / "annual" / "1993.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(annual.shape, (362, 900))
            self.assertGreater(int(np.isfinite(annual).sum()), 0)
            self.assertEqual(grid["sensor"], "AVHRR")
            self.assertEqual(checkpoint["records"][0]["sha256"], digest)
            self.assertTrue(checkpoint["records"][0]["owned"])
            self.assertFalse(destination.exists(), "worker-owned source must be removed after durable checkpoint")
            manifest = raw_root / "vegetation" / "noaa_cdr" / "manifest.jsonl"
            self.assertTrue(manifest.exists())
            self.assertIn('"status": "reduced"', manifest.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()