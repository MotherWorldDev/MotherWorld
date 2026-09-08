#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
import pandas as pd
from land_pollution_common import attach_analysis_geometry_provenance, merge_region_provider, recompute_peer_percentiles, region_gdf


def extract_pbf(path: Path):
    try:
        from pyrosm import OSM
    except ImportError as exc:
        raise SystemExit('Reading .osm.pbf requires optional package pyrosm. Install with `pip install pyrosm`, or pass an already-extracted GeoPackage/GeoJSON.') from exc
    osm=OSM(str(path))
    filters={'landuse':['landfill'],'amenity':['waste_disposal','waste_transfer_station']}
    return osm.get_data_by_custom_criteria(custom_filter=filters,filter_type='keep',keep_nodes=True,keep_ways=True,keep_relations=True)


def read_input(path: Path):
    if path.suffix.lower()=='.pbf' or path.name.endswith('.osm.pbf'): return extract_pbf(path)
    return gpd.read_file(path)


def main():
    ap=argparse.ArgumentParser(description='Aggregate OpenStreetMap mapped landfill/dump/waste-disposal features by MotherWorld terrestrial ecoregion.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--input',type=Path,action='append',required=True)
    args=ap.parse_args(); repo=args.repo.resolve(); parts=[]
    for p in args.input:
        g=read_input(p)
        if g.crs is None:g=g.set_crs('EPSG:4326')
        else:g=g.to_crs('EPSG:4326')
        parts.append(g)
    sites=gpd.GeoDataFrame(pd.concat(parts,ignore_index=True),geometry='geometry',crs='EPSG:4326')
    regions=region_gdf(repo)
    if sites.empty:return
    # classify
    def tag(row,key):
        v=row.get(key); return str(v) if v is not None else ''
    sites['siteType']=sites.apply(lambda r:'landfill' if tag(r,'landuse')=='landfill' else 'waste_disposal' if tag(r,'amenity')=='waste_disposal' else 'waste_transfer_station' if tag(r,'amenity')=='waste_transfer_station' else 'waste_site',axis=1)
    reps=sites.copy(); reps['geometry']=reps.geometry.representative_point()
    j=gpd.sjoin(reps,regions[['regionId','geometry']],how='inner',predicate='within')
    counts=j.groupby(['regionId','siteType']).size().unstack(fill_value=0)
    # landfill area apportioned by polygon intersection in equal-area CRS
    landfills=sites[(sites.siteType=='landfill') & sites.geometry.geom_type.isin(['Polygon','MultiPolygon'])].copy()
    area_by={}
    if not landfills.empty:
        cand=gpd.sjoin(landfills[['geometry']],regions[['regionId','geometry']],how='inner',predicate='intersects').reset_index().rename(columns={'index':'siteIndex'})
        regmap=regions.set_index('regionId').geometry
        for _,r in cand.iterrows():
            geom=landfills.loc[r['siteIndex'],'geometry'].intersection(regmap.loc[r['regionId']])
            if geom.is_empty:continue
            area=float(gpd.GeoSeries([geom],crs='EPSG:4326').to_crs('EPSG:6933').area.iloc[0]/1e6)
            area_by[r['regionId']]=area_by.get(r['regionId'],0)+area
    updates={}
    for rid,row in counts.iterrows():
        meta=regions.loc[regions.regionId==rid].iloc[0]; area=float(meta.areaKm2 or 0)
        metrics={
            'mappedSiteCount':int(row.sum()),'mappedLandfillCount':int(row.get('landfill',0)),
            'mappedWasteDisposalCount':int(row.get('waste_disposal',0)),'mappedTransferStationCount':int(row.get('waste_transfer_station',0)),
            'mappedLandfillAreaKm2':round(area_by.get(rid,0.0),4),
            'mappedSiteDensityPer1000Km2':float(row.sum())/area*1000 if area>0 else None,
            'mappedLandfillPctOfRegion':area_by.get(rid,0.0)/area*100 if area>0 else None,
        }
        updates[rid]={'label':'OpenStreetMap waste sites','coverage':'Volunteer-mapped features; completeness varies strongly by country and mapper activity.','metrics':metrics}
    attach_analysis_geometry_provenance(repo, updates)
    merge_region_provider(repo,updates,{'id':'osm-waste','label':'OpenStreetMap waste-site mapping','license':'ODbL 1.0','url':'https://www.openstreetmap.org/','caveat':'OSM is not an authoritative waste-site registry and mapped absence is not evidence of no site.'})
    recompute_peer_percentiles(repo); print(f'Updated {len(updates)} regions from OSM waste-site mapping.')
if __name__=='__main__':main()
