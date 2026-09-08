#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import *
def main():
    ap=argparse.ArgumentParser(description='Aggregate a staged USGS ComCat earthquake CSV by MotherWorld region.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--min-magnitude',type=float,default=5.0);a=ap.parse_args();repo=a.repo.resolve();df=read_table(a.input);lat=find_col(df,['latitude','lat'],True);lon=find_col(df,['longitude','lon'],True);mag=find_col(df,['mag','magnitude'],True);time=find_col(df,['time','date']);place=find_col(df,['place','location'])
    rows=[]
    for _,r in df.iterrows():
        m=as_float(r.get(mag));la=as_float(r.get(lat));lo=as_float(r.get(lon));
        if m is None or m<a.min_magnitude or la is None or lo is None:continue
        rows.append({'magnitude':m,'time':str(r.get(time) or '') if time else None,'place':r.get(place) if place else None,'geometry':Point(lo,la)})
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_regions(repo,globalize_open_ocean=True);j=assign_points(pts,regs);source=source_obj('usgs_comcat','USGS Earthquake Catalog (ComCat)',license='U.S. federal public data')
    for rid,g in j.groupby('regionId'):
        meta=g.iloc[0];events=sorted([{'magnitude':float(x.magnitude),'time':x.time,'place':x.place} for x in g.itertuples()],key=lambda x:x['magnitude'],reverse=True)
        sec={'tectonics':{'seismicity':{'minMagnitude':a.min_magnitude,'eventCount':len(g),'m6Plus':sum(1 for x in events if x['magnitude']>=6),'m7Plus':sum(1 for x in events if x['magnitude']>=7),'maxMagnitude':events[0]['magnitude'] if events else None,'events':events[:30],'source':source}}}
        write_fragment(repo,'usgs_comcat',{'regionId':rid,'regionName':meta.regionName,'kind':meta.kind},sec,source)
    print(f'Seismicity matched {len(j)} event-region records')
if __name__=='__main__':main()
