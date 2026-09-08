#!/usr/bin/env python
from __future__ import annotations
import argparse, os
from datetime import date
from pathlib import Path
import ee
from env_common import load_geometries, load_metadata, write_json, utc_now_iso, finite

DW='GOOGLE/DYNAMICWORLD/V1'; HANSEN='UMD/hansen/global_forest_change_2025_v1_13'

def parse():
 p=argparse.ArgumentParser(description='Build terrestrial habitat conversion metrics from Dynamic World + Hansen GFC.'); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--project',default=os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('EE_PROJECT')); p.add_argument('--authenticate',action='store_true'); p.add_argument('--start-year',type=int,default=2016); p.add_argument('--end-year',type=int,default=min(2025,date.today().year-1)); p.add_argument('--region',action='append',default=[]); p.add_argument('--scale',type=float,default=100); p.add_argument('--max-pixels',type=int,default=750000); p.add_argument('--tree-threshold',type=float,default=30); p.add_argument('--force',action='store_true'); return p.parse_args()

def reduce_mean(img,geom,a): return img.reduceRegion(ee.Reducer.mean(),geom,scale=a.scale,bestEffort=True,maxPixels=a.max_pixels,tileScale=4).getInfo()
def reduce_sum(img,geom,a): return img.reduceRegion(ee.Reducer.sum(),geom,scale=a.scale,bestEffort=True,maxPixels=a.max_pixels,tileScale=4).getInfo()

def slope(rows,key):
 pts=[(r['year'],finite(r.get(key))) for r in rows if finite(r.get(key)) is not None]
 if len(pts)<3:return None
 xm=sum(x for x,_ in pts)/len(pts); ym=sum(y for _,y in pts)/len(pts); den=sum((x-xm)**2 for x,_ in pts)
 return None if den<=0 else 10*sum((x-xm)*(y-ym) for x,y in pts)/den

def main():
 a=parse(); repo=a.repo.resolve();
 if a.authenticate: ee.Authenticate()
 if not a.project: raise SystemExit('Pass --project or set GOOGLE_CLOUD_PROJECT/EE_PROJECT')
 ee.Initialize(project=a.project)
 meta=load_metadata(repo); geoms=load_geometries(repo); rids=[rid for rid,(k,_) in meta.items() if k=='land' and rid in geoms]
 if a.region:rids=[r for r in rids if r in set(a.region)]
 root=repo/'frontend/public/data/habitat/land'; root.mkdir(parents=True,exist_ok=True)
 idx={'schemaVersion':1,'generatedAt':utc_now_iso(),'regions':{},'sources':{'dynamic-world':{'asset':DW,'resolution':'10 m','coverage':'2015-06-27 to present'},'hansen-gfc':{'asset':HANSEN,'resolution':'30 m','coverage':'2000-2025'}}}
 hansen=ee.Image(HANSEN); pixel_area=ee.Image.pixelArea(); baseline=hansen.select('treecover2000').gte(a.tree_threshold); loss=hansen.select('lossyear').gt(0)
 for n,rid in enumerate(rids,1):
  target=root/f'{rid}.habitat.json'
  if target.exists() and not a.force: idx['regions'][rid]={'url':f'land/{rid}.habitat.json'}; continue
  geom=ee.Geometry(geoms[rid].__geo_interface__); annual=[]
  for y in range(a.start_year,a.end_year+1):
   col=ee.ImageCollection(DW).filterDate(f'{y}-01-01',f'{y+1}-01-01')
   if col.size().getInfo()==0: continue
   mean=col.select(['crops','built','trees','grass','flooded_vegetation','shrub_and_scrub']).mean()
   s=reduce_mean(mean,geom,a)
   annual.append({'year':y,'croplandPct':100*float(s.get('crops',0) or 0),'builtPct':100*float(s.get('built',0) or 0),'naturalVegetationProbabilityPct':100*sum(float(s.get(k,0) or 0) for k in ['trees','grass','flooded_vegetation','shrub_and_scrub'])})
  area_m2=float(meta[rid][1].get('areaKm2') or 0)*1e6
  if area_m2<=0: area_m2=float(ee.Number(geom.area(maxError=1000)).getInfo())
  fbase=reduce_sum(pixel_area.updateMask(baseline).rename('a'),geom,a).get('a') or 0
  floss=reduce_sum(pixel_area.updateMask(baseline.And(loss)).rename('a'),geom,a).get('a') or 0
  latest=annual[-1] if annual else {}
  payload={'schemaVersion':1,'regionId':rid,'regionName':meta[rid][1].get('name') or rid,'regionKind':'land','latest':{**latest,'directCropBuiltPct':(latest.get('croplandPct',0)+latest.get('builtPct',0)) if latest else None},'annual':annual,'forest':{'treeCover2000ThresholdPct':a.tree_threshold,'baselineForestAreaKm2':fbase/1e6,'treeLossAreaKm2':floss/1e6,'treeLossPctOfRegion':100*floss/area_m2 if area_m2 else None,'treeLossPctOfBaselineForest':100*floss/fbase if fbase else None,'lossPeriod':'2001-2025'},'trends':{'croplandPpPerDecade':slope(annual,'croplandPct'),'builtPpPerDecade':slope(annual,'builtPct')},'supplemental':{},'sources':['dynamic-world','hansen-gfc'],'generatedAt':utc_now_iso(),'interpretation':'Direct crop+built probability is mutually exclusive within Dynamic World. Plantation, mining, and waste footprints are kept as supplemental components and are not blindly summed because overlaps are possible.'}
  write_json(target,payload,compact=True); idx['regions'][rid]={'url':f'land/{rid}.habitat.json','latestYear':latest.get('year')}; print(f'[{n}/{len(rids)}] {rid}')
 write_json(repo/'frontend/public/data/habitat/habitat.index.json',idx)
if __name__=='__main__': main()
