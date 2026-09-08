#!/usr/bin/env python
from __future__ import annotations
import argparse, math
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from geology_common import *

def load_source(path:Path,cell_size:float,class_field:str|None):
    if path.suffix.lower() in {'.shp','.gpkg','.geojson','.json','.gml','.kml'}:
        g=read_vector(path); c=class_field or find_col(g,['xx','class','lith','lithology','glim'])
        if not c: raise KeyError('GLiM class field not found; use --class-field')
        g['_code']=g[c].astype(str).str.lower().str[:2];return g
    df=read_table(path); lat=find_col(df,['lat','latitude'],True);lon=find_col(df,['lon','long','longitude'],True);c=class_field or find_col(df,['xx','class','lith','lithology','glim'],True)
    h=cell_size/2; rows=[]
    for _, r in df.iterrows():
        la=as_float(r.get(lat));lo=as_float(r.get(lon));code=str(r.get(c) or '').lower()[:2]
        if la is None or lo is None:continue
        rows.append({'_code':code,'geometry':box(lo-h,la-h,lo+h,la+h)})
    return gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84)

def main():
    ap=argparse.ArgumentParser(description='Aggregate GLiM lithology classes into MotherWorld regions.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--class-field');ap.add_argument('--cell-size',type=float,default=.5);ap.add_argument('--region',action='append');a=ap.parse_args();repo=a.repo.resolve()
    src=load_source(a.input,a.cell_size,a.class_field).to_crs(AREA_CRS); regs=load_regions(repo,('land',)).to_crs(AREA_CRS); sidx=src.sindex
    if a.region:regs=regs[regs.regionId.isin(a.region)]
    source=source_obj('glim','Global Lithological Map (GLiM)',version='v1.0',doi='10.1594/PANGAEA.788537',license='CC BY 3.0')
    for rr in regs.itertuples():
        geom=rr.geometry; region_area=float(geom.area)/1e6; totals={}
        for i in sidx.query(geom,predicate='intersects'):
            row=src.iloc[int(i)]; inter=geom.intersection(row.geometry)
            if inter.is_empty:continue
            area=float(inter.area)/1e6; code=str(row['_code']).lower(); totals[code]=totals.get(code,0)+area
        rock={k:v for k,v in totals.items() if k not in {'wb','ig','nd'} and v>0}; rock_area=sum(rock.values())
        classes=sorted([{'code':k,'label':GLIM_CLASSES.get(k,k),'areaKm2':v,'percent':v/rock_area*100 if rock_area else 0} for k,v in rock.items()],key=lambda x:x['percent'],reverse=True)
        excluded=[{'code':k,'label':GLIM_CLASSES.get(k,k),'areaKm2':totals.get(k,0),'percentOfRegion':totals.get(k,0)/region_area*100 if region_area else 0} for k in ('wb','ig','nd') if totals.get(k,0)>0]
        sec={'surfaceGeology':{'rockCoveragePct':rock_area/region_area*100 if region_area else 0,'dominantClass':classes[0] if classes else None,'classes':classes,'excluded':excluded,'source':source}}
        write_fragment(repo,'glim',{'regionId':rr.regionId,'regionName':rr.regionName,'kind':rr.kind},sec,source)
    print(f'GLiM fragments written for {len(regs)} regions')
if __name__=='__main__':main()
