from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from scripts.regional_diagnostics_adapters import (
    canonical_region_id,
    expected_region_ids,
    load_canonical_regions,
    write_json,
)
from scripts.build_noaa_microplastics import (
    _positive_measurement,
    _protocol_descriptor,
    _protocol_key,
)


def test_fragment_ids_normalize_only_known_rendered_region_prefixes():
    assert canonical_region_id("marine_meow_20002__283") == "marine_meow_20002"
    assert canonical_region_id("eco_123__4") == "eco_123"
    assert canonical_region_id("lake_7__12") == "lake_7"
    assert canonical_region_id("provider__12") == "provider__12"


def test_canonical_region_load_unions_rendered_fragments():
    land = load_canonical_regions(REPO, ("land",))
    marine = load_canonical_regions(REPO, ("marine",))
    lakes = load_canonical_regions(REPO, ("lakes",))

    assert len(land) == 847
    assert len(marine) == 232
    assert len(lakes) == 21
    assert all("__" not in region_id for region_id in marine["regionId"])
    assert len(expected_region_ids(REPO)) == 1100


def test_write_json_rejects_nonfinite_numbers(tmp_path):
    with pytest.raises(ValueError):
        write_json(tmp_path / "nan.json", {"value": math.nan})

    path = tmp_path / "valid.json"
    write_json(path, {"value": None, "number": 1.25}, compact=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": None, "number": 1.25}


def test_microplastics_protocols_stay_separate_and_zero_is_not_positive():
    first = _protocol_descriptor(
        {"Marine Setting": "Ocean water", "Sampling Method": "Neuston net", "Mesh Size (mm)": 0.335},
        {"setting": "Marine Setting", "method": "Sampling Method", "mesh": "Mesh Size (mm)", "water_depth": None, "sediment_depth": None},
    )
    second = dict(first)
    second["samplingMethod"] = "Manta trawl"
    assert _protocol_key(first) != _protocol_key(second)
    assert not _positive_measurement(0.0)
    assert _positive_measurement(0.1)
