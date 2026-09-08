#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import *
def main():
    ap=argparse.ArgumentParser(description='Aggregate IHFC Global Heat Flow Database measurements by MotherWorld region.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();df=read_table(a.input);lat=find_col(df,['Latitude','lat'],True);lon=find_col(df,['Longitude','lon','long'],True);hf=find_col(df,['Heat Flow','Heat_Flow','heatflow','q','heat_flow_mw_m2'],True);qual=find_col(df,['Quality_code','qualitycode','quality'])
    rows=[]
    for _,r in df.iterrows():
        la=as_float(r.get(lat));lo=as_float(r.get(lon));val=as_float(r.get(hf));
        if la is None or lo is None or val is None:continue
        rows.append({'heatFlow':val,'quality':r.get(qual) if qual else None,'geometry':Point(lo,la)})
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_regions(repo,globalize_open_ocean=True);j=assign_points(pts,regs);source=source_obj('ihfc_heat_flow','IHFC Global Heat Flow Database',version='Release 2024 / quality update 2026.03',doi='10.5880/fidgeo.2024.014',license='CC BY 4.0')
    bins=[(0,40),(40,60),(60,80),(80,100),(100,150),(150,200),(200,300),(300,None)]
    for rid,g in j.groupby('regionId'):
        meta=g.iloc[0];vals=[float(x) for x in g.heatFlow if as_float(x) is not None];n=len(vals);bout=[]
        for lo,hi in bins:
            c=sum(1 for x in vals if x>=lo and (hi is None or x<hi));bout.append({'label':f'{lo}–{hi} mW/m²' if hi is not None else f'≥{lo} mW/m²','count':c,'percent':c/n*100 if n else 0})
        sec={'geothermal':{'measurementCount':n,'medianMwM2':percentile(vals,50),'p10MwM2':percentile(vals,10),'p90MwM2':percentile(vals,90),'maxMwM2':max(vals) if vals else None,'bins':bout,'source':source}}
        write_fragment(repo,'ihfc_heat_flow',{'regionId':rid,'regionName':meta.regionName,'kind':meta.kind},sec,source)
    print(f'Heat-flow matched {len(j)} measurement-region records')
if __name__=='__main__':main()
