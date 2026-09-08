#!/usr/bin/env python
from __future__ import annotations
import argparse,subprocess,sys
from pathlib import Path

def first(root,names):
    for pat in names:
        xs=sorted(root.glob(pat))
        if not xs: xs=sorted(root.rglob(pat))
        if xs:return xs[0]
    return None

def run(script,args):
    print('+',script,*map(str,args));subprocess.run([sys.executable,script,*map(str,args)],check=True)

def main():
    ap=argparse.ArgumentParser(description='Build every staged MotherWorld geology + seafloor provider that is present.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--macrostrat',action='store_true');ap.add_argument('--region',action='append')
    a=ap.parse_args();repo=a.repo.resolve();raw=repo/'geology_raw';scripts=repo/'scripts';raw.mkdir(exist_ok=True)

    glim=first(raw,['glim*.gpkg','glim*.shp','glim*.geojson','glim*.csv','glim*.tsv'])
    mrds=first(raw,['mrds*.csv','mrds*.tsv','mrds*.xlsx'])
    faults=first(raw,['gem*active*fault*.gpkg','gem*active*fault*.geojson','gem*active*fault*.shp'])
    vol=first(raw,['gvp*volcano*.csv','gvp*volcano*.xlsx','gvp*volcano*.xls','gvp*volcano*.xml'])
    eru=first(raw,['gvp*eruption*.csv','gvp*eruption*.xlsx','gvp*eruption*.xls','gvp*eruption*.xml'])
    heat=first(raw,['*heat*flow*.csv','*heat*flow*.xlsx','*heat*flow*.xls'])
    impact=first(raw,['*impact*.csv','*impact*.xlsx'])
    quake=first(raw,['*earthquake*.csv','*comcat*.csv'])

    # Marine / seafloor providers. The suggested names are documented in GEOLOGY_DOWNLOADS.md.
    gebco=first(raw,['GEBCO_2026*.nc','gebco_2026*.nc','gebco*.grd'])
    age=first(raw,['seton2020*age*.nc','*seafloor*age*.nc','*oceanic*crust*age*.nc','*agegrid*.nc'])
    rate=first(raw,['seton2020*rate*.nc','*spreading*rate*.nc'])
    globsed=first(raw,['GlobSed*.grd','globsed*.grd','GlobSed*.nc','globsed*.nc'])
    ridges=first(raw,['*spreading*ridge*.gpkg','*spreading*ridge*.geojson','*spreading*ridge*.shp','*ridges*.shp','*ridges*.gpkg','*ridges*.geojson'])
    trenches=first(raw,['*trench*.gpkg','*trench*.geojson','*trench*.shp','*subduction*.gpkg','*subduction*.geojson','*subduction*.shp'])
    vents=first(raw,['*interridge*vent*.csv','*interridge*vent*.tsv','*interridge*vent*.tab','*ventfields*.csv','*ventfields*.tsv','*ventfields*.tab'])
    crust=first(raw,['CRUST1.0*.nc','crust1*.nc'])

    if glim:run(scripts/'build_glim_lithology.py',['--repo',repo,'--input',glim])
    if a.macrostrat:
        args=['--repo',repo]
        for r in a.region or []:args+=['--region',r]
        run(scripts/'build_macrostrat_geology.py',args)
    if mrds:run(scripts/'build_mrds_occurrences.py',['--repo',repo,'--input',mrds])
    if faults:run(scripts/'build_gem_active_faults.py',['--repo',repo,'--input',faults])
    if vol:
        args=['--repo',repo,'--volcanoes',vol]
        if eru:args+=['--eruptions',eru]
        run(scripts/'build_gvp_volcanoes.py',args)
    if heat:run(scripts/'build_heatflow_geothermal.py',['--repo',repo,'--input',heat])
    if impact:run(scripts/'build_impact_structures.py',['--repo',repo,'--input',impact])
    if quake:run(scripts/'build_usgs_seismicity.py',['--repo',repo,'--input',quake])

    # Dedicated seafloor summaries; open_ocean is automatically widened to the whole ocean.
    region_args=[]
    for r in a.region or []:region_args+=['--region',r]
    if gebco:run(scripts/'build_gebco_bathymetry.py',['--repo',repo,'--input',gebco,*region_args])
    if age:
        args=['--repo',repo,'--age-grid',age,*region_args]
        if rate:args+=['--rate-grid',rate]
        run(scripts/'build_seafloor_age.py',args)
    if globsed:run(scripts/'build_globsed_sediment.py',['--repo',repo,'--input',globsed,*region_args])
    if ridges or trenches:
        args=['--repo',repo]
        if ridges:args+=['--ridges',ridges]
        if trenches:args+=['--trenches',trenches]
        run(scripts/'build_seafloor_tectonics.py',args)
    if vents:run(scripts/'build_interridge_vents.py',['--repo',repo,'--input',vents])
    if crust:run(scripts/'build_crust1_seafloor.py',['--repo',repo,'--input',crust,*region_args])

    run(scripts/'merge_geology.py',['--repo',repo]);print('Geology + seafloor build complete. Missing staged providers were skipped.')
if __name__=='__main__':main()
