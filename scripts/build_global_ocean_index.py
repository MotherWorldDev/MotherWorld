#!/usr/bin/env python
from __future__ import annotations
import argparse, re
from pathlib import Path
import numpy as np, pandas as pd, xarray as xr
from env_common import write_json, utc_now_iso, clamp, geometric_mean, finite

def annualize(rows):
 by={}
 for r in rows:
  y=int(r['year']); v=finite(r.get('value'))
  if v is not None: by.setdefault(y,[]).append(v)
 return [{'year':y,'value':sum(vals)/len(vals)} for y,vals in sorted(by.items()) if vals]

def series_file(path:Path,kind:str):
 if path.suffix.lower() in {'.nc','.nc4','.netcdf'}:
  ds=xr.open_dataset(path)
  try:
   time=next((c for c in ('time','year','date') if c in ds.coords),None); var=next((v for v in ds.data_vars if kind in v.lower()),None) or list(ds.data_vars)[0]; da=ds[var]
   if time is None: raise ValueError('no time coordinate')
   for d in list(da.dims):
    if d!=time: da=da.mean(d,skipna=True)
   raw_time=np.asarray(ds[time].values)
   try: years=pd.to_datetime(raw_time).year
   except Exception: years=np.asarray(raw_time,dtype=int)
   vals=np.asarray(da.values,float); return annualize([{'year':int(y),'value':float(v)} for y,v in zip(years,vals) if np.isfinite(v)])
  finally: ds.close()
 txt=path.read_text(encoding='utf-8',errors='ignore')
 try:
  df=pd.read_csv(path,comment='#')
  nums=[c for c in df.columns if pd.to_numeric(df[c],errors='coerce').notna().sum()>max(2,len(df)//2)]
  if len(nums)>=2:
   y,v=nums[0],nums[1]; yy=pd.to_numeric(df[y],errors='coerce'); vv=pd.to_numeric(df[v],errors='coerce'); return annualize([{'year':int(float(a)),'value':float(b)} for a,b in zip(yy,vv) if pd.notna(a) and pd.notna(b)])
 except Exception: pass
 out=[]
 for line in txt.splitlines():
  vals=re.findall(r'[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?',line)
  if len(vals)>=2:
   try:
    y=int(float(vals[0])); v=float(vals[1]);
    if 1900<=y<=2200: out.append({'year':y,'value':v})
   except Exception: pass
 return annualize(out)

def baseline(rows,n=5):
 vals=[r['value'] for r in rows[:min(n,len(rows))]]; return sum(vals)/len(vals) if vals else None

def main():
 p=argparse.ArgumentParser(description='Build whole-ocean health index. Global Ocean metrics cover the residual open ocean PLUS all coastal/marine ecoregions.'); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--ohc',type=Path,help='NOAA 0-2000 m yearly basin .dat or standardized year,value CSV'); p.add_argument('--ph',type=Path,help='CMEMS global mean pH NetCDF/CSV'); p.add_argument('--sea-level',type=Path,help='Global mean sea-level text/CSV'); p.add_argument('--ohc-critical-zj',type=float,default=500); p.add_argument('--ph-critical-drop',type=float,default=.20); p.add_argument('--sea-level-critical-mm',type=float,default=500); a=p.parse_args(); repo=a.repo.resolve(); comps=[]; context={}; series_by={}
 if a.ohc:
  rows=series_file(a.ohc,'heat');
  if rows:
   # NOAA basin files use 10^22 J; standardized CSV may already be ZJ. Infer from magnitude conservatively.
   vals=[r['value'] for r in rows]; scale=10.0 if max(abs(x) for x in vals)<100 else 1.0
   rows=[{'year':r['year'],'value':r['value']*scale} for r in rows]; b=baseline(rows); latest=rows[-1]; delta=latest['value']-b; sc=clamp(100*(1-delta/a.ohc_critical_zj)); comps.append({'id':'ocean_heat','label':'Ocean heat-content stability','score':sc,'weight':.4,'raw':latest['value'],'unit':'ZJ anomaly-series units','baseline':b,'changeFromBaseline':delta}); series_by['ohc']=(rows,b,a.ohc_critical_zj)
 if a.ph:
  rows=series_file(a.ph,'ph');
  if rows:
   b=baseline(rows); latest=rows[-1]; drop=b-latest['value']; sc=clamp(100*(1-drop/a.ph_critical_drop)); comps.append({'id':'ocean_ph','label':'Ocean pH retention','score':sc,'weight':.35,'raw':latest['value'],'unit':'pH','baseline':b,'changeFromBaseline':latest['value']-b}); series_by['ph']=(rows,b,a.ph_critical_drop)
 if a.sea_level:
  rows=series_file(a.sea_level,'sea');
  if rows:
   vals=[r['value'] for r in rows]; # Infer metres vs millimetres.
   scale=1000.0 if max(abs(x) for x in vals)<10 else 1.0; rows=[{'year':r['year'],'value':r['value']*scale} for r in rows]; b=baseline(rows); latest=rows[-1]; rise=latest['value']-b; sc=clamp(100*(1-rise/a.sea_level_critical_mm)); comps.append({'id':'sea_level','label':'Global mean sea-level stability','score':sc,'weight':.25,'raw':latest['value'],'unit':'mm','baseline':b,'changeFromBaseline':rise}); series_by['sea']=(rows,b,a.sea_level_critical_mm)
 configured=1.0; available=sum(c['weight'] for c in comps); score=geometric_mean([(c['score'],c['weight']) for c in comps]) if available>=.5 else None
 years=sorted(set(y for rows,_,_ in series_by.values() for y in [r['year'] for r in rows])); s=[]
 for y in years:
  vals=[]
  for key,(rows,b,crit) in series_by.items():
   row=next((r for r in rows if r['year']==y),None)
   if not row: continue
   if key=='ohc': sc=clamp(100*(1-(row['value']-b)/crit)); w=.4
   elif key=='ph': sc=clamp(100*(1-(b-row['value'])/crit)); w=.35
   else: sc=clamp(100*(1-(row['value']-b)/crit)); w=.25
   vals.append((sc,w))
  if sum(w for _,w in vals)>=.5: s.append({'year':y,'score':round(geometric_mean(vals),4),'coverage':sum(w for _,w in vals)})
 payload={'schemaVersion':1,'familyId':'ocean','label':'Global Ocean','score':round(score,4) if score is not None else None,'coverage':available/configured,'components':comps,'series':s,'scope':{'selectionGeometry':'open_ocean residual','metricScope':'entire ocean = open_ocean residual + all mapped marine/coastal ecoregions','speciesScope':'open_ocean residual only','namedOceanTabs':['All','Pacific','Atlantic','Indian','Southern','Arctic']},'generatedAt':utc_now_iso(),'method':{'crossComponentAggregation':'weighted geometric mean','normalizationsAre':'transparent configurable MotherWorld reference ranges, not official ecological thresholds','missingDataPolicy':'score withheld until at least 50% component weight is available'},'sources':[{'id':'noaa-ohc','label':'NOAA Global Ocean Heat Content'},{'id':'cmems-ph','label':'Copernicus Marine global ocean pH indicator'},{'id':'global-mean-sea-level','label':'Satellite global mean sea level'}]}; payload['earthHealthRole']='diagnostic_only'; payload['scoredInEarthHealth']=False; write_json(repo/'frontend/public/data/indices/diagnostics/ocean.diagnostics.json',payload); write_json(repo/'frontend/public/data/environment/global-ocean.json',payload); print('Global Ocean diagnostics:', 'withheld' if score is None else f'{score:.1f}/100',f'coverage={available:.0%}')
if __name__=='__main__': main()
