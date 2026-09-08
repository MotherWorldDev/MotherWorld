#!/usr/bin/env python
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import *
def main():
    ap=argparse.ArgumentParser(description='Aggregate USGS MRDS mineral occurrences by MotherWorld region.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();df=read_table(a.input)
    lat=find_col(df,['latitude','lat_dec','lat','y'],True);lon=find_col(df,['longitude','long_dec','lon','lng','x'],True);name=find_col(df,['dep_name','site_name','name','deposit_name']);dtype=find_col(df,['dep_type','deposit_type','model_name','dev_stat']);commod=[c for c in [find_col(df,['commod1']),find_col(df,['commod2']),find_col(df,['commod3']),find_col(df,['commodity','commodities'])] if c]
    rows=[]
    for _,r in df.iterrows():
        la=as_float(r.get(lat));lo=as_float(r.get(lon));
        if la is None or lo is None:continue
        cs=[]
        for c in commod:cs+=split_multi(r.get(c))
        rows.append({'name':r.get(name) if name else None,'depositType':r.get(dtype) if dtype else None,'commodities':sorted(set(cs)),'geometry':Point(lo,la)})
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_regions(repo,globalize_open_ocean=True);j=assign_points(pts,regs);source=source_obj('usgs_mrds','USGS Mineral Resources Data System (MRDS)',license='U.S. federal public data')
    for rid,g in j.groupby('regionId'):
        meta=g.iloc[0];cc=Counter(c for arr in g.commodities for c in arr);sites=[{'name':x.name,'depositType':x.depositType,'commodities':x.commodities} for x in g.itertuples()]
        sites=sites[:40];total=sum(cc.values()) or 1;com=[{'label':k,'count':v,'sharePct':v/total*100} for k,v in cc.most_common(30)]
        sec={'minerals':{'occurrenceCount':len(g),'commodityCount':len(cc),'commodities':com,'sites':sites,'source':source}}
        write_fragment(repo,'usgs_mrds',{'regionId':rid,'regionName':meta.regionName,'kind':meta.kind},sec,source)
    print(f'MRDS matched {len(j)} records to {j.regionId.nunique() if len(j) else 0} regions')
if __name__=='__main__':main()
