from pathlib import Path
import json
import sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_ucmr5_pfas_v8 as ucmr_builder

def _feature(code):
    return {"type":"Feature","properties":{"ZCTA5CE20":code},"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}}

def test_ucmr5_builder_filters_lithium_and_converts_micrograms(monkeypatch):
    f_stage = ROOT / ".cache" / "pfas-source-test"
    f_stage.mkdir(parents=True, exist_ok=True)
    source = f_stage / "ucmr5"
    source.mkdir(exist_ok=True)
    (source / "UCMR5_ZIPCodes.txt").write_text(
        "PWSID\tZIPCODE\nPWS-1\t00001\n",
        encoding="cp1252",
    )
    (source / "UCMR5_All.txt").write_text(
        "PWSID\tContaminant\tAnalyticalResultsSign\tAnalyticalResultValue\tUnits\tCollectionDate\n"
        "PWS-1\tPFOS\t<\t0.001\tµg/L\t01/02/2023\n"
        "PWS-1\tPFOA\t=\t0.003\tµg/L\t01/03/2026\n"
        "PWS-1\tPFBS\t=\t2\tµg/L\t01/04/2023\n"
        "PWS-1\tLithium\t=\t2\tµg/L\t01/05/2023\n"
        "PWS-1\tFuture compound\t=\t9\tµg/L\t01/06/2023\n",
        encoding="cp1252",
    )
    zcta = f_stage / "zctas.geojson"
    zcta.write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [_feature("00001")],
        }, allow_nan=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ucmr_builder,
        "canonical_point_region_map",
        lambda points, repo, kinds: {int(index): ["eco_1"] for index in points.index},
    )

    updates, audit = ucmr_builder.build_updates(
        ROOT,
        source,
        zcta,
        max_values_per_series=100,
    )
    category = updates["eco_1"]["categories"]["pfas"]
    analytes = {item["name"]: item for item in category["analytes"]}
    assert set(analytes) == {"PFOS", "PFOA", "PFBS"}
    assert category["sampleCount"] == 3
    assert category["detectedCount"] == 2
    assert all(item["unit"] == "ng/L" for item in analytes.values())
    assert analytes["PFOA"]["medianDetected"] == pytest.approx(3.0)
    assert analytes["PFOS"]["sampleCount"] == 1
    assert analytes["PFOS"]["detectedCount"] == 0
    assert audit["sourceRows"] == 5
    assert audit["observedPfasAnalyteCount"] == 3
    assert audit["expectedPfasAnalyteCount"] == 29


    assert updates["eco_1"]["sources"]["epa_ucmr5"]["period"] == "2023-2026"
    assert audit["collectionPeriod"] == "2023-2026"
