import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shapely.geometry import box, mapping
from species_common import RegionGeometry, geometry_wkt_chunks, query_fingerprint
from species_query_geometry import load_query_geometry_overrides, apply_query_geometry

class QueryGeometryTests(unittest.TestCase):
    def write(self, folder, features):
        path=Path(folder)/"query.geojson"
        path.write_text(json.dumps({"type":"FeatureCollection","features":features}),encoding="utf-8")
        return path
    def feature(self, **properties):
        return {"type":"Feature","properties":{"regionId":"eco_1","source":"official_island_baseline","sourceUrl":"https://example.org/source","notes":"Source perimeter includes inter-islet water.",**properties},"geometry":mapping(box(-29.4,-20.6,-29.2,-20.4))}
    def test_only_target_geometry_changes_and_provenance_invalidates_resume(self):
        original=RegionGeometry("eco_1","Test island",box(-30.4,-20.6,-30.2,-20.4),"land")
        other=RegionGeometry("eco_2","Other island",box(1,1,2,2),"land")
        with tempfile.TemporaryDirectory() as folder:
            overrides=load_query_geometry_overrides(self.write(folder,[self.feature()]))
            corrected,provenance=apply_query_geometry(original,overrides)
            unchanged,empty=apply_query_geometry(other,overrides)
        self.assertIs(unchanged,other);self.assertIsNone(empty)
        self.assertEqual(corrected.name,original.name)
        self.assertAlmostEqual(corrected.geometry.centroid.x,-29.3)
        self.assertAlmostEqual(original.geometry.centroid.x,-30.3)
        self.assertEqual(provenance["sourceUrl"],"https://example.org/source")
        self.assertNotEqual(query_fingerprint("same geometry",{"geometryOverride":provenance}),query_fingerprint("same geometry",{"geometryOverride":{**provenance,"notes":"Updated boundary provenance"}}))
    def test_missing_provenance_duplicate_ids_and_non_geographic_coordinates_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            bad=self.feature();bad["geometry"]=mapping(box(1000,1,1001,2))
            for features in [[self.feature(sourceUrl="")],[self.feature(sourceUrl="http://example.org/source")],[self.feature(),self.feature()],[bad]]:
                with self.subTest(features=features):
                    with self.assertRaises(ValueError):load_query_geometry_overrides(self.write(folder,features))
    def test_maintained_geometry_corrections_keep_remote_islands(self):
        overrides=load_query_geometry_overrides()
        self.assertEqual(set(overrides),{"eco_117","eco_121","eco_130","eco_267","eco_509","eco_562","eco_609"})
        trindade=overrides["eco_509"][0]
        self.assertGreater(trindade.bounds[0],-30)
        self.assertGreater(trindade.bounds[2],-29) # Martin Vaz must also be included.
        st_paul=overrides["eco_609"][0]
        self.assertLess(st_paul.centroid.x,-29.34)
        for key,(geometry,provenance) in overrides.items():
            self.assertTrue(geometry.is_valid,key)
            self.assertTrue(geometry_wkt_chunks(geometry,max_chars=2500),key)
            self.assertTrue(provenance["notes"],key)

if __name__ == "__main__":unittest.main()
