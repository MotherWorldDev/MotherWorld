#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
from land_pollution_common import merge_region_provider, recompute_peer_percentiles, region_gdf


def main():
    ap=argparse.ArgumentParser(description='Intersect Global-scale mining polygons with MotherWorld ecoregions as a mining-related land disturbance/waste-pressure indicator.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--input',type=Path,required=True)
    args=ap.parse_args(); repo=args.repo.resolve(); mines=gpd.read_file(args.input); mines=mines.set_crs('EPSG:4326') if mines.crs is None else mines.to_crs('EPSG:4326'); regions=region_gdf(repo)
    cand=gpd.sjoin(mines[['geometry']],regions[['regionId','geometry']],how='inner',predicate='intersects').reset_index().rename(columns={'index':'mineIndex'})
    regmap=regions.set_index('regionId').geometry; area_by={}; features={}
    for _,r in cand.iterrows():
        rid=r['regionId']; geom=mines.loc[r['mineIndex'],'geometry'].intersection(regmap.loc[rid])
        if geom.is_empty: continue
        km2=float(gpd.GeoSeries([geom],crs='EPSG:4326').to_crs('EPSG:6933').area.iloc[0]/1e6); area_by[rid]=area_by.get(rid,0)+km2; features.setdefault(rid,set()).add(int(r['mineIndex']))
    updates={}
    for rid,km2 in area_by.items():
        area=float(regions.loc[regions.regionId==rid,'areaKm2'].iloc[0] or 0)
        updates[rid]={'label':'Global mining footprint','coverage':'Satellite-mapped land directly used by mining; includes pits, tailings dams, waste-rock dumps, ponds and processing infrastructure, but is not itself a contamination measurement.','metrics':{'miningFootprintKm2':km2,'miningFootprintPctOfRegion':km2/area*100 if area>0 else None,'intersectingMiningPolygonCount':len(features.get(rid,set()))}}
    merge_region_provider(repo,updates,{'id':'global-mining-polygons-v2','label':'Global-scale mining polygons v2','license':'CC BY-SA 4.0','url':'https://doi.org/10.1594/PANGAEA.942325','caveat':'Mining footprint is an industrial-land/mining-waste pressure proxy, not proof that all mapped pixels are contaminated.'})
    recompute_peer_percentiles(repo); print(f'Updated {len(updates)} regions from global mining polygons.')
if __name__=='__main__':main()
