#!/usr/bin/env python
from __future__ import annotations
import argparse, os, time
from datetime import date
from pathlib import Path
import ee
from env_common import load_geometries, load_metadata, write_json, utc_now_iso, finite

COL='MODIS/061/MOD44B'; BANDS=['Percent_Tree_Cover','Percent_NonTree_Vegetation','Percent_NonVegetated']

def args():
 p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--project',default=os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('EE_PROJECT')); p.add_argument('--authenticate',action='store_true'); p.add_argument('--start-year',type=int,default=2000); p.add_argument('--end-year',type=int,default=date.today().year-1); p.add_argument('--region',action='append',default=[]); p.add_argument('--batch-size',type=int,default=25); p.add_argument('--scale',type=float,default=250); p.add_argument('--max-pixels',type=int,default=500000); p.add_argument('--tile-scale',type=float,default=4); p.add_argument('--force',action='store_true'); return p.parse_args()

def gm(rows,key):
 vals=[finite(r.get(key)) for r in rows]; vals=[v for v in vals if v is not None]; return sum(vals)/len(vals) if vals else None

def trend(rows,key):
 pts=[(r['year'],finite(r.get(key))) for r in rows if finite(r.get(key)) is not None]
 if len(pts)<3:return None
 xm=sum(x for x,_ in pts)/len(pts); ym=sum(y for _,y in pts)/len(pts); d=sum((x-xm)**2 for x,_ in pts)
 return None if d<=0 else 10*sum((x-xm)*(y-ym) for x,y in pts)/d

def main():
 a=args(); repo=a.repo.resolve();
 if a.authenticate: ee.Authenticate()
 if not a.project: raise SystemExit('Pass --project or set GOOGLE_CLOUD_PROJECT/EE_PROJECT.')
 ee.Initialize(project=a.project)
 meta=load_metadata(repo); geoms=load_geometries(repo)
 rids=[rid for rid,(kind,_) in meta.items() if kind=='land' and rid in geoms]
 if a.region:rids=[r for r in rids if r in set(a.region)]
 outroot=repo/'frontend/public/data/vegetation/land'; outroot.mkdir(parents=True,exist_ok=True)
 idx={'schemaVersion':1,'generatedAt':utc_now_iso(),'regions':{},'sources':{'MOD44B.061':{'label':'MODIS/Terra Vegetation Continuous Fields v6.1','doi':'10.5067/MODIS/MOD44B.061','resolution':'250 m'}}}
 for n,rid in enumerate(rids,1):
  target=outroot/f'{rid}.vegetation.json'
  if target.exists() and not a.force:
   idx['regions'][rid]={'url':f'land/{rid}.vegetation.json'}; continue
  geom=ee.Geometry(geoms[rid].__geo_interface__)
  rows=[]
  for year in range(a.start_year,a.end_year+1):
   image=ee.ImageCollection(COL).filterDate(f'{year}-01-01',f'{year+1}-01-01').first().select(BANDS)
   stats=image.reduceRegion(ee.Reducer.mean(),geom,scale=a.scale,bestEffort=True,maxPixels=a.max_pixels,tileScale=a.tile_scale).getInfo()
   tree=finite(stats.get(BANDS[0])); non=finite(stats.get(BANDS[1])); bare=finite(stats.get(BANDS[2]));
   if tree is None or non is None or bare is None: continue
   rows.append({'year':year,'treePct':tree,'nonTreePct':non,'barePct':bare,'vegetationPct':max(0,min(100,tree+non))})
  if not rows: continue
  first5=rows[:min(5,len(rows))]; latest=rows[-1]; area=float(meta[rid][1].get('areaKm2') or 0)
  latest={**latest,'vegetationAreaKm2':area*latest['vegetationPct']/100 if area else None,'treeAreaKm2':area*latest['treePct']/100 if area else None}
  payload={'schemaVersion':1,'regionId':rid,'name':meta[rid][1].get('name') or rid,'latest':latest,'years':rows,'baseline':{'years':[r['year'] for r in first5],'vegetationPct':gm(first5,'vegetationPct'),'treePct':gm(first5,'treePct')},'trend':{'vegetationPpPerDecade':trend(rows,'vegetationPct'),'treePpPerDecade':trend(rows,'treePct')},'source':{'id':'MOD44B.061','label':'MODIS/Terra Vegetation Continuous Fields v6.1','doi':'10.5067/MODIS/MOD44B.061'},'generatedAt':utc_now_iso()}
  write_json(target,payload,compact=True); idx['regions'][rid]={'url':f'land/{rid}.vegetation.json','endYear':latest['year']}; print(f'[{n}/{len(rids)}] {rid}')
 write_json(repo/'frontend/public/data/vegetation/vegetation.index.json',idx)

if __name__=='__main__': main()
