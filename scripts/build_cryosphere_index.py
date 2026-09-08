#!/usr/bin/env python
from __future__ import annotations
import argparse, io
from pathlib import Path
import pandas as pd, requests
from env_common import write_json, utc_now_iso, clamp, geometric_mean
from backbone_common import write_diagnostic
N='https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv'; S='https://noaadata.apps.nsidc.org/NOAA/G02135/south/daily/data/S_seaice_extent_daily_v4.0.csv'
def load(url): r=requests.get(url,timeout=90); r.raise_for_status(); d=pd.read_csv(io.StringIO(r.text),skipinitialspace=True); d.columns=[str(c).strip() for c in d.columns]; return d
def amin(d):
 for c in ('Year','Extent'): d[c]=pd.to_numeric(d[c],errors='coerce')
 return d.dropna(subset=['Year','Extent']).groupby('Year').Extent.min().to_dict()
def opt(path):
 if not path:return None
 d=pd.read_csv(path); y=next((c for c in d if 'year' in c.lower()),d.columns[0]); v=next(c for c in d if c!=y); d[y]=pd.to_numeric(d[y],errors='coerce'); d[v]=pd.to_numeric(d[v],errors='coerce'); d=d.dropna(); return [{'year':int(r[y]),'value':float(r[v])} for _,r in d.iterrows()]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--greenland-csv',type=Path); p.add_argument('--antarctica-csv',type=Path); p.add_argument('--glacier-csv',type=Path); a=p.parse_args(); repo=a.repo.resolve(); n=amin(load(N)); s=amin(load(S)); nb=sum(v for y,v in n.items() if 1981<=int(y)<=2010)/len([1 for y in n if 1981<=int(y)<=2010]); sb=sum(v for y,v in s.items() if 1981<=int(y)<=2010)/len([1 for y in s if 1981<=int(y)<=2010]); years=sorted(set(map(int,n))&set(map(int,s))); series=[]
 for y in years:
  if y<1981: continue
  ns=clamp(100*n[y]/nb); ss=clamp(100*s[y]/sb); series.append({'year':y,'score':round(geometric_mean([(ns,.5),(ss,.5)]),4),'coverage':1.0,'raw':{'arcticMinMillionKm2':float(n[y]),'antarcticMinMillionKm2':float(s[y])},'unit':'million km² annual minimum extent'})
 latest=series[-1]; payload={'schemaVersion':2,'familyId':'cryosphere','label':'Cryosphere','scope':'global_only','earthHealthRole':'backbone','score':latest['score'],'coverage':1.0,'latestBackboneYear':latest['year'],'components':[{'id':'sea_ice_minimum_retention','label':'Polar sea-ice minimum retention','score':latest['score'],'weight':1.0,'source':'NOAA/NSIDC Sea Ice Index v4'}],'series':series,'generatedAt':utc_now_iso(),'method':{'backbone':'weighted geometric mean of Arctic and Antarctic annual-minimum extent retention','baseline':'1981–2010 mean annual minimum in each hemisphere','scoreDirection':'100 = baseline annual-minimum sea ice retained','diagnosticPolicy':'Greenland/Antarctic land ice and glacier mass are diagnostics only'},'sources':[{'id':'nsidc-sea-ice','label':'NOAA/NSIDC Sea Ice Index v4'}]}; write_json(repo/'frontend/public/data/indices/families/cryosphere.index.json',payload); write_diagnostic(repo,'cryosphere',{'schemaVersion':1,'familyId':'cryosphere','label':'Cryosphere diagnostics','components':{'greenlandIceMass':opt(a.greenland_csv),'antarcticIceMass':opt(a.antarctica_csv),'glacierMassBalance':opt(a.glacier_csv)}}); print('Cryosphere',latest['year'],latest['score'])
if __name__=='__main__': main()
