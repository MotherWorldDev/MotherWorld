#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
from geology_common import read_vector, source_obj, write_fragment
from seafloor_common import load_marine_analysis_regions, projected_line_intersections

def ingest(path):
    if not path:return None
    g=read_vector(Path(path));g=g[g.geometry.notna()&~g.geometry.is_empty].copy();return g

def main():
    ap=argparse.ArgumentParser(description='Aggregate mapped mid-ocean ridges and trenches/subduction lines.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--ridges',type=Path);ap.add_argument('--trenches',type=Path);a=ap.parse_args();repo=a.repo.resolve();regs=load_marine_analysis_regions(repo)
    if not a.ridges and not a.trenches:raise SystemExit('Pass --ridges and/or --trenches')
    ridges=ingest(a.ridges);trenches=ingest(a.trenches)
    rr=projected_line_intersections(ridges,regs) if ridges is not None else {};tt=projected_line_intersections(trenches,regs) if trenches is not None else {}
    source=source_obj('earthbyte_seafloor_tectonics','EarthByte / GPlates seafloor tectonic features',version='GPlates GeoData / staged release',license='CC BY 3.0 for EarthByte GeoData; cite original feature sources')
    n=0
    for r in regs.itertuples():
        if r.regionId not in rr and r.regionId not in tt:continue
        payload={}
        if r.regionId in rr:payload['ridges']={'featureCount':rr[r.regionId]['count'],'intersectionLengthKm':rr[r.regionId]['intersectionLengthKm']}
        if r.regionId in tt:payload['trenches']={'featureCount':tt[r.regionId]['count'],'intersectionLengthKm':tt[r.regionId]['intersectionLengthKm']}
        payload['source']=source
        write_fragment(repo,'earthbyte_seafloor_tectonics',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},{'seafloor':{'tectonics':payload}},source);n+=1
    print(f'Seafloor tectonic features built for {n} marine regions')
if __name__=='__main__':main()
