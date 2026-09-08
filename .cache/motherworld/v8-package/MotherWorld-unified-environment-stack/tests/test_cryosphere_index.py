from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from build_cryosphere_index import amin, is_scored_year  # noqa: E402


class CryosphereCalendarGateTests(unittest.TestCase):
    def test_closed_historical_gap_keeps_observed_min_current_year_is_excluded(self):
        rows = [
            {"Year": 1987, "Month": 1, "Day": 1, "Extent": 8.0},
            {"Year": 1987, "Month": 12, "Day": 2, "Extent": 6.0},
            {"Year": 2026, "Month": 1, "Day": 1, "Extent": 7.0},
            {"Year": 2026, "Month": 9, "Day": 6, "Extent": 5.0},
        ]
        minima, observed = amin(pd.DataFrame(rows))

        self.assertEqual(minima[1987], 6.0)
        self.assertEqual(observed[1987]["lastDate"], "1987-12-02")
        self.assertTrue(is_scored_year(1987, date(2026, 9, 8)))
        self.assertFalse(is_scored_year(2026, date(2026, 9, 8)))


if __name__ == "__main__":
    unittest.main()
