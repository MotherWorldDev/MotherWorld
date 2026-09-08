#!/usr/bin/env python
from __future__ import annotations
import argparse,re
from collections import defaultdict
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import *
def main():
    ap=argparse.ArgumentParser(description='Aggregate Smithsonian GVP volcanoes and confirmed eruptions by MotherWorld region.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--volcanoes',type=Path,required=True);ap.add_argument('--eruptions',type=Path);a=ap.parse_args();repo=a.repo.resolve();v=read_table(a.volcanoes)
    lat=find_col(v,['Latitude','lat'],True);lon=find_col(v,['Longitude','lon'],True);vid=find_col(v,['Volcano Number','volcano_number','volcanonumber']);name=find_col(v,['Volcano Name','volcano_name','name']);typ=find_col(v,['Primary Volcano Type','primaryvolcanotype','volcano_type','type']);country=find_col(v,['Country','country']);last=find_col(v,['Last Known Eruption','last_known_eruption','lastknowneruption']);rockcols=[c for c in v.columns if 'rock' in norm(c)]
    eru=defaultdict(list)
    if a.eruptions and a.eruptions.exists():
        e=read_table(a.eruptions);evid=find_col(e,['Volcano Number','volcano_number','volcanonumber']);sy=find_col(e,['Start Year','start_year','startyear']);vei=find_col(e,['VEI','vei']);
        if evid:
            for _,r in e.iterrows():
                key=str(r.get(evid) or '').strip();year=as_float(r.get(sy)) if sy else None;vi=as_float(r.get(vei)) if vei else None
                if key:eru[key].append({'year':int(year) if year is not None else None,'vei':vi})
    rows=[]
    for _,r in v.iterrows():
        la=as_float(r.get(lat));lo=as_float(r.get(lon));
        if la is None or lo is None:continue
        key=str(r.get(vid) or '').strip() if vid else '';es=eru.get(key,[]);years=[x['year'] for x in es if x['year'] is not None];veis=[x['vei'] for x in es if x['vei'] is not None]
        rocks=[]
        for c in rockcols:
            val=str(r.get(c) or '').strip()
            if val and val.lower()!='nan':rocks.append(val)
        rows.append({'volcanoId':key,'name':r.get(name) if name else None,'type':r.get(typ) if typ else None,'country':r.get(country) if country else None,'lastKnownEruption':r.get(last) if last else None,'latestEruptionYear':max(years) if years else None,'maxVEI':max(veis) if veis else None,'eruptionCount':len(es),'rocks':rocks[:8],'geometry':Point(lo,la)})
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_regions(repo,globalize_open_ocean=True);j=assign_points(pts,regs);source=source_obj('smithsonian_gvp','Smithsonian Global Volcanism Program — Volcanoes of the World',version='5.4.0 (2026-08-07)',doi='10.5479/si.GVP.VOTW5-2026.5.4')
    for rid,g in j.groupby('regionId'):
        meta=g.iloc[0];vs=[]
        for x in g.itertuples():vs.append({'id':x.volcanoId,'name':x.name,'type':x.type,'country':x.country,'lastKnownEruption':x.lastKnownEruption,'latestEruptionYear':x.latestEruptionYear,'maxVEI':x.maxVEI,'rocks':x.rocks})
        latest=max([x.latestEruptionYear for x in g.itertuples() if x.latestEruptionYear is not None],default=None);mvei=max([x.maxVEI for x in g.itertuples() if x.maxVEI is not None],default=None);erupt=sum(int(x.eruptionCount or 0) for x in g.itertuples());hist=sum(1 for x in g.itertuples() if x.latestEruptionYear is not None and x.latestEruptionYear>=1800)
        sec={'volcanism':{'volcanoCount':len(g),'confirmedEruptionCount':erupt,'historicallyActiveCount':hist,'latestConfirmedYear':latest,'maxVEI':mvei,'volcanoes':vs[:40],'source':source}}
        write_fragment(repo,'smithsonian_gvp',{'regionId':rid,'regionName':meta.regionName,'kind':meta.kind},sec,source)
    print(f'GVP matched {len(j)} volcano-region records')
if __name__=='__main__':main()
