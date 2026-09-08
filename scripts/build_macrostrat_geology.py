#!/usr/bin/env python
from __future__ import annotations
import argparse, json, time
from pathlib import Path
import geopandas as gpd
import requests
from geology_common import *

def main():
    ap=argparse.ArgumentParser(description='Fetch Macrostrat mapped units/ages for MotherWorld land regions.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--region',action='append');ap.add_argument('--endpoint',default='https://macrostrat.org/api/v2/carto/small');ap.add_argument('--delay',type=float,default=.12);ap.add_argument('--force',action='store_true');a=ap.parse_args();repo=a.repo.resolve();regs=load_regions(repo,('land',));
    if a.region:regs=regs[regs.regionId.isin(a.region)]
    cache=repo/'.cache/motherworld/geology/macrostrat';cache.mkdir(parents=True,exist_ok=True);source=source_obj('macrostrat','Macrostrat geologic maps',license='CC BY 4.0')
    session=requests.Session();session.headers['User-Agent']='MotherWorld/1 geology preprocessor'
    for rr in regs.itertuples():
        cp=cache/f'{rr.regionId}.json'
        if cp.exists() and not a.force: raw=json.loads(cp.read_text())
        else:
            params={'shape':rr.geometry.envelope.wkt,'format':'geojson'};resp=session.get(a.endpoint,params=params,timeout=90);resp.raise_for_status();raw=resp.json();cp.write_text(json.dumps(raw));time.sleep(max(0,a.delay))
        feats=((raw.get('success') or {}).get('data') or {}).get('features',[]) if isinstance(raw,dict) else []
        if not feats:continue
        g=gpd.GeoDataFrame.from_features(feats,crs=WGS84).to_crs(AREA_CRS); rgeom=gpd.GeoSeries([rr.geometry],crs=WGS84).to_crs(AREA_CRS).iloc[0];
        records=[];era_area={};geoms=[]
        for row in g.itertuples():
            inter=rgeom.intersection(row.geometry)
            if inter.is_empty:continue
            area=float(inter.area)/1e6;geoms.append(inter)
            top=as_float(getattr(row,'best_t_age',None) if hasattr(row,'best_t_age') else getattr(row,'best_age_top',None));bot=as_float(getattr(row,'best_b_age',None) if hasattr(row,'best_b_age') else getattr(row,'best_age_bottom',None));mid=(top+bot)/2 if top is not None and bot is not None else (top if top is not None else bot);era=era_for_age(mid)
            if era:era_area[era]=era_area.get(era,0)+area
            records.append({'name':getattr(row,'name',None),'lith':getattr(row,'lith',None),'age':getattr(row,'age',None),'bestTopAgeMa':top,'bestBottomAgeMa':bot,'sourceId':getattr(row,'source_id',None),'areaKm2':area})
        total=sum(era_area.values());eras=[{'label':e,'percent':era_area.get(e,0)/total*100 if total else 0} for e,_,_ in ERA_BOUNDS if era_area.get(e,0)>0]
        records.sort(key=lambda x:x['areaKm2'],reverse=True);mapped=unary_union(geoms).area/1e6 if geoms else 0;regarea=rgeom.area/1e6
        sec={'geologicAge':{'mappedCoveragePct':mapped/regarea*100 if regarea else 0,'eras':eras,'units':records[:30],'source':source}}
        write_fragment(repo,'macrostrat',{'regionId':rr.regionId,'regionName':rr.regionName,'kind':rr.kind},sec,source)
    print(f'Macrostrat fragments attempted for {len(regs)} regions')
if __name__=='__main__':main()
