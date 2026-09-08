import argparse
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
import requests
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from shapely.geometry import Polygon, MultiPolygon, box
from shapely import from_wkt
from shapely.ops import unary_union
from species_common import geometry_wkt_chunks, load_region_geometries, query_fingerprint
from obis_build_region_species import obis_checklist, normalize_obis_species
from gbif_build_region_species import fetch_facet_counts, gbif_wkt
from gbif_prepare_taxonomy import import_species_list
from gbif_prepare_backbone import import_backbone
from package_species import package

class Response:
    ok = True
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload

class PipelineTests(unittest.TestCase):
    def test_marine_and_lake_geometry_ids_match_site(self):
        root = Path(__file__).resolve().parents[1]
        for kind in ["marine", "lakes"]:
            expected = json.loads((root / f"frontend/public/data/{kind}.index.json").read_text(encoding="utf-8"))["regions"]
            regions = load_region_geometries(root, kind)
            self.assertEqual({r.region_id for r in regions}, set(expected))
            self.assertEqual(len(regions), len(expected))

    def test_chunks_preserve_islands_holes_and_nonoverlap(self):
        holes = [box(x + .2, y + .2, x + .4, y + .4).exterior.coords for x in range(8) for y in range(8)]
        polygon = Polygon(box(0,0,10,10).exterior.coords, holes)
        original = MultiPolygon([polygon, box(20,0,21,1)])
        chunks = geometry_wkt_chunks(original, max_chars=650)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(wkt) <= 650 for wkt in chunks))
        shapes = [from_wkt(wkt) for wkt in chunks]
        self.assertTrue(all(shape.is_valid for shape in shapes))
        self.assertAlmostEqual(sum(shape.area for shape in shapes), original.area, places=5)
        self.assertAlmostEqual(unary_union(shapes).area, original.area, places=5)
        self.assertFalse(unary_union(shapes).contains(box(.21,.21,.39,.39)))

    def test_gbif_exterior_rings_are_counterclockwise(self):
        wkt = gbif_wkt(unary_union([box(0,0,1,1), box(2,2,3,3)]).wkt)
        self.assertTrue(all(p.exterior.is_ccw for p in from_wkt(wkt).geoms))

    def test_gbif_repairs_invalid_rounded_query_geometry(self):
        raw = "MULTIPOLYGON (((0 0, 2 2, 2 0, 0 2, 0 0)), ((3 0, 4 0, 4 1, 3 1, 3 0)))"
        fixed = from_wkt(gbif_wkt(raw))
        self.assertTrue(fixed.is_valid)
        self.assertTrue(all(part.exterior.is_ccw for part in fixed.geoms))

    def test_query_filters_invalidate_resume(self):
        self.assertEqual(query_fingerprint("geometry", {"year":2026,"basis":"A"}), query_fingerprint("geometry", {"basis":"A","year":2026}))
        self.assertNotEqual(query_fingerprint("geometry", {"year":2025}), query_fingerprint("geometry", {"year":2026}))

    def test_obis_reads_every_partition_and_groups_children(self):
        calls = []
        class Session:
            def get(self, url, params, timeout):
                calls.append(dict(params));part = dict(params)["partition"]
                return Response({"total":3,"partitions":3,"results":[{"species":"Animal test","scientificName":"Animal test child" if part else "Animal test", "speciesid":42,"taxonRank":"Subspecies" if part else "Species","kingdom":"Animalia","records":part+1}]})
        args = argparse.Namespace(startdate=None,enddate=None,exclude_flag=[],request_delay=0)
        rows = obis_checklist(Session(), "POLYGON", args)
        result = normalize_obis_species(rows + [{"scientificName":"Animal","taxonRank":"Genus","records":100}])
        self.assertEqual([c["partition"] for c in calls], [0,1,2])
        self.assertEqual(len(result),1);self.assertEqual(result[0]["occurrenceCount"],6)
        self.assertEqual(result[0]["aphiaId"],42)

    def test_gbif_pages_facets_and_retains_filters(self):
        calls=[]
        class Session:
            def get(self, url, params, timeout):
                calls.append(params);offset=dict(params)["facetOffset"]
                return Response({"facets":[{"field":"SPECIES_KEY","counts":[{"name":str(offset+1),"count":2},{"name":str(offset+2),"count":3}] if offset==0 else []}]})
        args=argparse.Namespace(year="2020,2026",basis_of_record=[],request_delay=0)
        result=fetch_facet_counts(Session(), "POLYGON", facet="speciesKey",page_size=2,args=args)
        self.assertEqual(dict(result),{"1":2,"2":3})
        self.assertEqual([dict(c)["facetOffset"] for c in calls],[0,2])
        self.assertTrue(all(("hasGeospatialIssue","false") in c and ("year","2020,2026") in c for c in calls))

    def test_gbif_retries_transient_failures_with_bounded_backoff(self):
        calls = []
        class HTTPResponse:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self.ok = 200 <= status_code < 400
                self.text = f"status {status_code}"
                self.headers = {}
                self._payload = payload or {}
            def json(self):
                return self._payload
        class Session:
            def get(self, url, params, timeout):
                calls.append(params)
                if len(calls) == 1:
                    raise requests.Timeout("temporary backend timeout")
                if len(calls) == 2:
                    return HTTPResponse(503)
                return HTTPResponse(200, {"facets": []})
        args = argparse.Namespace(year=None, basis_of_record=[], request_delay=0)
        with patch("gbif_build_region_species.time.sleep") as sleeper:
            result = fetch_facet_counts(Session(), "POLYGON", facet="speciesKey", page_size=2, args=args)
        self.assertEqual(dict(result), {})
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleeper.call_args_list], [1.0, 2.0])

    def test_species_list_bom_and_failed_import_preserves_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"names.zip";output=root/"taxonomy.sqlite"
            with zipfile.ZipFile(source,"w") as z:
                z.writestr("species.csv", '\ufeff"speciesKey","species","kingdom"\n"42","Animal test","Animalia"\n')
            import_species_list(source,output)
            with closing(sqlite3.connect(output)) as conn: self.assertEqual(conn.execute("SELECT scientific_name FROM species").fetchone()[0],"Animal test")
            with zipfile.ZipFile(source,"w") as z:z.writestr("wrong.csv","wrong,header\na,b\n")
            with self.assertRaises(RuntimeError): import_species_list(source,output)
            with closing(sqlite3.connect(output)) as conn:self.assertEqual(conn.execute("SELECT count(*) FROM species").fetchone()[0],1)

    def test_release_compression_validates_before_replacing_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"source";output=root/"public"
            (source/"land").mkdir(parents=True)
            payload={"regionId":"eco_1","source":"GBIF","generatedAt":"2026-09-07","stats":{"recordedSpeciesCount":1,"occurrenceCount":3},"species":[{"scientificName":"Animal test","occurrenceCount":3}]}
            (source/"land/eco_1.json").write_text(json.dumps(payload),encoding="utf-8")
            index={"regions":{"eco_1":{"url":"land/eco_1.json","source":"GBIF","regionKind":"land"}}}
            (source/"species.index.json").write_text(json.dumps(index),encoding="utf-8")
            manifest=package([source],output)
            self.assertEqual(manifest["regions"]["eco_1"]["url"],"land/eco_1.json.gz")
            with gzip.open(output/"land/eco_1.json.gz","rt",encoding="utf-8") as f:self.assertEqual(json.load(f),payload)
            before=(output/"species.index.json").read_bytes()
            payload["stats"]["recordedSpeciesCount"]=99
            (source/"land/eco_1.json").write_text(json.dumps(payload),encoding="utf-8")
            with self.assertRaises(ValueError):package([source],output)
            self.assertEqual((output/"species.index.json").read_bytes(),before)

    def test_public_backbone_resolves_higher_taxonomy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"names.gz";output=root/"taxonomy.sqlite"
            def row(key,rank,name):
                fields=[r"\N"]*30;fields[0]=str(key);fields[5]=rank;fields[18]=name;fields[19]=name;fields[10]="1";return "\t".join(fields)
            with gzip.open(source,"wt",encoding="utf-8") as f:f.write(row(1,"KINGDOM","Animalia")+"\n"+row(42,"SPECIES","Animal test")+"\n")
            import_backbone(source,output)
            with closing(sqlite3.connect(output)) as conn:
                self.assertEqual(conn.execute("SELECT scientific_name,kingdom,iucn_red_list_category FROM species").fetchone(),("Animal test","Animalia",None))
                self.assertEqual(dict(conn.execute("SELECT * FROM metadata"))["edition"],"2023-08-28")

if __name__ == "__main__": unittest.main()
