#!/usr/bin/env python
from __future__ import annotations
import runpy
from pathlib import Path

# Kept as a memorable entry point. It forwards all CLI arguments to the full builder.
runpy.run_path(str(Path(__file__).with_name("build_regional_climate_extras_ee.py")), run_name="__main__")
