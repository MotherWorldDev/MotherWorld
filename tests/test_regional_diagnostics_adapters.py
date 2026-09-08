from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import scripts.regional_diagnostics_adapters as adapters
from scripts.regional_diagnostics_adapters import (
    attach_analysis_geometry_provenance,
    canonical_point_region_map,
    canonical_region_id,
    merge_contaminant_categories,
    expected_region_ids,
    load_canonical_regions,
    merge_land_pollution_provider,
    write_json,
)
from scripts.species_query_geometry import load_query_geometry_overrides
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


def test_original_land_source_precedes_lod1_and_keeps_maintained_override(tmp_path):
    shapefile_dir = tmp_path / "Ecoregions2017"
    shapefile_dir.mkdir()
    shapefile = shapefile_dir / "Ecoregions2017.shp"
    affected_ids = [117, 121, 130, 267, 509, 562, 609]
    original_ids = [999, *affected_ids]
    original = gpd.GeoDataFrame(
        {"ECO_ID": original_ids},
        geometry=[box(0, 0, 1, 1)] + [box(100 + i, 40, 101 + i, 41) for i in range(len(affected_ids))],
        crs="EPSG:4326",
    )
    original.to_file(shapefile, driver="ESRI Shapefile")

    lod_dir = tmp_path / "frontend/public/data/lod1_realms"
    lod_dir.mkdir(parents=True)
    lod_features = []
    for i, region_id in enumerate(original_ids):
        x = -100 - i
        lod_features.append(
            {
                "type": "Feature",
                "properties": {"id": f"eco_{region_id}"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[x, 20], [x + 1, 20], [x + 1, 21], [x, 21], [x, 20]]],
                },
            }
        )
    (lod_dir / "realm.topojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": lod_features}),
        encoding="utf-8",
    )

    assert adapters._source_layers(tmp_path, "land") == [shapefile]
    regions = load_canonical_regions(tmp_path, ("land",))
    assert regions.loc[regions["regionId"] == "eco_999"].iloc[0].geometry.equals(box(0, 0, 1, 1))
    overrides = load_query_geometry_overrides()
    for region_id in affected_ids:
        corrected = regions.loc[regions["regionId"] == f"eco_{region_id}"].iloc[0]
        assert corrected.geometry.equals(overrides[f"eco_{region_id}"][0])
        assert corrected.analysisGeometry["overrideApplied"] is True



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


def test_corrected_land_mapping_and_provider_merge_require_geometry_identity(tmp_path, monkeypatch):
    source = tmp_path / "base.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"regionId": "eco_509"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[140, -10], [141, -10], [141, -9], [140, -9], [140, -10]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        adapters,
        "_source_layers",
        lambda _repo, kind: [source] if kind == "land" else [],
    )

    official = load_query_geometry_overrides()["eco_509"][0]
    representative = official.representative_point()
    assert not representative.within(box(140, -10, 141, -9))
    regions = load_canonical_regions(tmp_path, ("land",))
    row = regions.loc[regions["regionId"] == "eco_509"].iloc[0]
    assert row.geometry.equals(official)
    assert row.analysisGeometry["overrideApplied"] is True
    assert row.analysisGeometry["geometryFingerprint"] == row.analysisGeometry["geometryOverride"]["registryGeometryFingerprint"]

    mapping = canonical_point_region_map(
        pd.DataFrame(
            {
                "lat": [representative.y, 91.0],
                "lon": [representative.x, 181.0],
            }
        ),
        tmp_path,
        kinds={"land"},
    )
    assert mapping[0] == ["eco_509"]
    assert 1 not in mapping

    output = tmp_path / "output"
    old_path = output / "frontend/public/data/land-pollution/land/eco_509.land-pollution.json"
    write_json(
        old_path,
        {
            "schemaVersion": 1,
            "regionId": "eco_509",
            "providers": {"old-osm-partial": {"metrics": {"siteCount": 9}}},
        },
        compact=True,
    )
    source_entry = {"id": "fixture-provider", "label": "Fixture"}
    merge_land_pollution_provider(
        tmp_path,
        output,
        {"eco_509": {"metrics": {"siteCount": 1}}},
        source_entry,
    )
    withheld = json.loads(old_path.read_text(encoding="utf-8"))
    assert withheld["providers"] == {}
    assert withheld["analysisGeometryStatus"] == "withheld"
    assert "eco_509" not in json.loads(
        (output / "frontend/public/data/land-pollution/land-pollution.index.json").read_text(encoding="utf-8")
    ).get("regions", {})

    updates = {"eco_509": {"metrics": {"siteCount": 1}}}
    attach_analysis_geometry_provenance(tmp_path, updates)
    assert updates["eco_509"]["_analysisGeometry"]["overrideApplied"] is True
    merge_land_pollution_provider(tmp_path, output, updates, source_entry)
    merged = json.loads(old_path.read_text(encoding="utf-8"))
    assert set(merged["providers"]) == {"fixture-provider"}
    assert merged["analysisGeometry"]["overrideApplied"] is True
    assert merged["analysisGeometryCacheIdentity"] == updates["eco_509"]["_analysisGeometryCacheIdentity"]
    fragment = json.loads(
        (output / "provider-fragments/land-pollution/fixture-provider/land/eco_509.json").read_text(
            encoding="utf-8"
        )
    )
    assert fragment["analysisGeometry"]["geometryFingerprint"] == merged["analysisGeometry"]["geometryFingerprint"]

    empty_source = tmp_path / "empty.geojson"
    empty_source.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")
    monkeypatch.setattr(
        adapters,
        "_source_layers",
        lambda _repo, kind: [empty_source] if kind == "land" else [],
    )
    unavailable_output = tmp_path / "unavailable-output"
    unavailable_path = unavailable_output / "frontend/public/data/land-pollution/land/eco_509.land-pollution.json"
    write_json(unavailable_path, {"providers": {"old-osm-partial": {"metrics": {"siteCount": 9}}}}, compact=True)
    merge_land_pollution_provider(
        tmp_path,
        unavailable_output,
        {"eco_509": {"metrics": {"siteCount": 1}}},
        source_entry,
    )
    unavailable = json.loads(unavailable_path.read_text(encoding="utf-8"))
    assert unavailable["providers"] == {}
    assert unavailable["analysisGeometryReason"] == "expected_analysis_geometry_unavailable"

    contaminants_output = tmp_path / "unavailable-contaminants"
    contaminants_path = contaminants_output / "frontend/public/data/contaminants/land/eco_509.contaminants.json"
    write_json(contaminants_path, {"categories": {"legacy": {}}, "sources": {"old": {}}}, compact=True)
    merge_contaminant_categories(
        tmp_path,
        contaminants_output,
        {"eco_509": {"categories": {"new": {}}}},
        source_entry,
    )
    contaminants = json.loads(contaminants_path.read_text(encoding="utf-8"))
    assert contaminants["categories"] == {}
    assert contaminants["analysisGeometryReason"] == "expected_analysis_geometry_unavailable"


def test_provenance_requires_producer_context_and_corrected_reference(tmp_path):
    updates = {"eco_509": {"metrics": {"siteCount": 9}}}
    attach_analysis_geometry_provenance(tmp_path, updates)
    assert "_analysisGeometry" not in updates["eco_509"]
    entry = {"analysisGeometry": {"overrideApplied": False},
             "analysisGeometryCacheIdentity": "legacy"}
    assert adapters._requires_corrected_geometry("eco_509", entry) == (
        True, "expected_analysis_geometry_unavailable")
    assert not adapters._valid_producer_geometry_claim(
        "eco_509", entry["analysisGeometry"], "legacy", entry)
