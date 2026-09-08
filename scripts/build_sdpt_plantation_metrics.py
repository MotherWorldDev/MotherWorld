#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
from env_common import load_geometries, load_metadata, read_json, write_json, utc_now_iso

def main():
 p=argparse.ArgumentParser(description='Aggregate WRI Spatial Database of Planted Trees polygons to MotherWorld ecoregions.'); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--input',type=Path,required=True); a=p.parse_args(); repo=a.repo.resolve()
 src=gpd.read_file(a.input); src=src.set_crs('EPSG:4326') if src.crs is None else src.to_crs('EPSG:4326'); geoms=load_geometries(repo); meta=load_metadata(repo)
 regions=gpd.GeoDataFrame([{'regionId':rid,'geometry':geom} for rid,(k,m) in meta.items() if k=='land' and (geom:=geoms.get(rid)) is not None],crs='EPSG:4326').to_crs('ESRI:54009')
 src=src.to_crs('ESRI:54009'); joined=gpd.overlay(src[['geometry']],regions[['regionId','geometry']],how='intersection',keep_geom_type=False); joined['areaKm2']=joined.geometry.area/1e6; sums=joined.groupby('regionId').areaKm2.sum()
 for rid,area in sums.items():
  hp=repo/'frontend/public/data/habitat/land'/f'{rid}.habitat.json'; h=read_json(hp,{'schemaVersion':1,'regionId':rid,'regionName':meta[rid][1].get('name') or rid,'regionKind':'land','supplemental':{}}); total=float(meta[rid][1].get('areaKm2') or 0); h.setdefault('supplemental',{})['plantations']={'areaKm2':float(area),'pctOfRegion':100*float(area)/total if total else None,'source':'WRI SDPT v2.1'}; h['generatedAt']=utc_now_iso(); write_json(hp,h,compact=True)
 print(f'Updated {len(sums)} habitat regions from planted-tree polygons.')
if __name__=='__main__': main()
