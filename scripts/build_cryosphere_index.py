#!/usr/bin/env python
from __future__ import annotations
import argparse, io
from datetime import date, datetime, timezone
from pathlib import Path
import pandas as pd, requests
from env_common import write_json, utc_now_iso, clamp, geometric_mean
from backbone_common import write_diagnostic
N='https://noaadata.apps.nsidc.org/NOAA/G02135/north/daily/data/N_seaice_extent_daily_v4.0.csv'; S='https://noaadata.apps.nsidc.org/NOAA/G02135/south/daily/data/S_seaice_extent_daily_v4.0.csv'
def load(url_or_path):
 if isinstance(url_or_path,Path) or (isinstance(url_or_path,str) and Path(url_or_path).exists()):
  d=pd.read_csv(url_or_path,skipinitialspace=True)
 else:
  r=requests.get(url_or_path,timeout=90); r.raise_for_status(); d=pd.read_csv(io.StringIO(r.text),skipinitialspace=True)
 d.columns=[str(c).strip() for c in d.columns]; return d
def amin(d):
 for c in ('Year','Month','Day','Extent'): d[c]=pd.to_numeric(d[c],errors='coerce')
 d=d.dropna(subset=['Year','Month','Day','Extent']).copy()
 d['date']=pd.to_datetime({'year':d['Year'].astype(int),'month':d['Month'].astype(int),'day':d['Day'].astype(int)},errors='coerce')
 d=d.dropna(subset=['date'])
 minima={}; observed={}
 for year, rows in d.groupby('Year'):
  y=int(year); minima[y]=float(rows['Extent'].min())
  dates=rows['date'].dt.date
  observed[y]={'firstDate':str(dates.min()),'lastDate':str(dates.max()),'observationCount':int(len(rows))}
 return minima, observed
def is_scored_year(year, as_of=None):
 """A source year is closed for this backbone once the UTC calendar moves past it."""
 as_of=as_of or datetime.now(timezone.utc).date()
 return int(year) < as_of.year
def opt(path):
 if not path:return None
 d=pd.read_csv(path); y=next((c for c in d if 'year' in c.lower()),d.columns[0]); v=next(c for c in d if c!=y); d[y]=pd.to_numeric(d[y],errors='coerce'); d[v]=pd.to_numeric(d[v],errors='coerce'); d=d.dropna(); return [{'year':int(r[y]),'value':float(r[v])} for _,r in d.iterrows()]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--north-csv',type=Path); p.add_argument('--south-csv',type=Path); p.add_argument('--greenland-csv',type=Path); p.add_argument('--antarctica-csv',type=Path); p.add_argument('--glacier-csv',type=Path); a=p.parse_args(); repo=a.repo.resolve(); as_of=datetime.now(timezone.utc).date(); n,n_observed=amin(load(a.north_csv or N)); s,s_observed=amin(load(a.south_csv or S)); n_base=[n[y] for y in range(1981,2011) if y in n]; s_base=[s[y] for y in range(1981,2011) if y in s]; nb=sum(n_base)/len(n_base); sb=sum(s_base)/len(s_base); years=sorted(y for y in set(map(int,n))&set(map(int,s)) if y>=1981 and is_scored_year(y,as_of)); series=[]
 for y in years:
  ns=clamp(100*n[y]/nb); ss=clamp(100*s[y]/sb); series.append({'year':y,'score':round(geometric_mean([(ns,.5),(ss,.5)]),4),'coverage':1.0,'observedDateCoverage':{'arctic':n_observed[y],'antarctic':s_observed[y]},'raw':{'arcticMinMillionKm2':float(n[y]),'antarcticMinMillionKm2':float(s[y])},'unit':'million km² annual minimum extent'})
 latest=series[-1]; payload={'schemaVersion':2,'familyId':'cryosphere','label':'Cryosphere','scope':'global_only','earthHealthRole':'backbone','score':latest['score'],'coverage':1.0,'latestBackboneYear':latest['year'],'components':[{'id':'sea_ice_minimum_retention','label':'Polar sea-ice minimum retention','score':latest['score'],'weight':1.0,'source':'NOAA/NSIDC Sea Ice Index v4'}],'series':series,'generatedAt':utc_now_iso(),'method':{'backbone':'weighted geometric mean of Arctic and Antarctic annual-minimum extent retention','baseline':'1981–2010 mean annual minimum in each hemisphere, retaining all source-supported observed annual minima in that calendar range','yearGate':f'Only years strictly before the current UTC calendar year are scored (as of {as_of.isoformat()}); the current year is treated as in progress.','historicalGapRule':'A closed historical year remains eligible when both hemispheres contain an observed annual minimum, even if the source record has a late-year observation gap. observedDateCoverage records the source dates and counts; coverage=1 means an annual minimum exists, not that every day was sampled.','scoreDirection':'100 = baseline annual-minimum sea ice retained','diagnosticPolicy':'Greenland/Antarctic land ice and glacier mass are diagnostics only'},'sources':[{'id':'nsidc-sea-ice','label':'NOAA/NSIDC Sea Ice Index v4','northUrl':N,'southUrl':S}]}; write_json(repo/'frontend/public/data/indices/families/cryosphere.index.json',payload); write_diagnostic(repo,'cryosphere',{'schemaVersion':1,'familyId':'cryosphere','label':'Cryosphere diagnostics','components':{'greenlandIceMass':opt(a.greenland_csv),'antarcticaIceMass':opt(a.antarctica_csv),'glacierMassBalance':opt(a.glacier_csv)}}); print('Cryosphere',latest['year'],latest['score'])
if __name__=='__main__': main()
