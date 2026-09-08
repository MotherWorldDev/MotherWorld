from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from build_climate_forcing_index import aggi_column  # noqa: E402


class ClimateForcingCompatibilityTests(unittest.TestCase):
    def test_current_noaa_normalized_column_is_accepted(self):
        data = pd.DataFrame({"Year": [2024], "1990 = 1": [1.5]})
        self.assertEqual(aggi_column(data), "1990 = 1")

    def test_legacy_explicit_aggi_column_remains_accepted(self):
        data = pd.DataFrame({"Year": [2024], "AGGI": [1.5]})
        self.assertEqual(aggi_column(data), "AGGI")


if __name__ == "__main__":
    unittest.main()
