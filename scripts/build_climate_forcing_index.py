#!/usr/bin/env python
from __future__ import annotations
import argparse, io
from pathlib import Path
import pandas as pd, requests
from env_common import write_json, utc_now_iso, clamp
from backbone_common import write_diagnostic
URLS={'aggi':'https://gml.noaa.gov/aggi/AGGI_Table.csv','co2':'https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_annmean_gl.csv','ch4':'https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_annmean_gl.csv','n2o':'https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_annmean_gl.csv'}
def fetch(url): r=requests.get(url,timeout=60); r.raise_for_status(); return pd.read_csv(io.StringIO(r.text),comment='#')
def col(df,needle): return next((c for c in df.columns if needle in str(c).lower().replace('_','')),None)
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--aggi-upper',type=float,default=2.0); a=p.parse_args(); repo=a.repo.resolve(); df=fetch(URLS['aggi']); yc=col(df,'year') or df.columns[0]; ac=col(df,'aggi');
 if ac is None: raise SystemExit(f'No AGGI column: {list(df.columns)}')
 df[yc]=pd.to_numeric(df[yc],errors='coerce'); df[ac]=pd.to_numeric(df[ac],errors='coerce'); df=df.dropna(subset=[yc,ac]); series=[]
 for _,r in df.iterrows(): av=float(r[ac]); series.append({'year':int(r[yc]),'score':round(clamp(100*(a.aggi_upper-av)/(a.aggi_upper-1)),4),'coverage':1.0,'raw':av,'unit':'AGGI (1990=1)'})
 latest=series[-1]; gases={}
 for gas in ('co2','ch4','n2o'):
  try:
   g=fetch(URLS[gas]); y=col(g,'year') or g.columns[0]; nums=[c for c in g.columns if c!=y and 'unc' not in str(c).lower()]; v=nums[0]; g[y]=pd.to_numeric(g[y],errors='coerce'); g[v]=pd.to_numeric(g[v],errors='coerce'); g=g.dropna(subset=[y,v]); row=g.iloc[-1]; gases[gas]={'year':int(row[y]),'value':float(row[v]),'unit':'ppm' if gas=='co2' else 'ppb'}
  except Exception as e: gases[gas]={'error':str(e)}
 payload={'schemaVersion':2,'familyId':'climate_forcing','label':'Climate forcing','scope':'global_only','earthHealthRole':'backbone','score':latest['score'],'coverage':1.0,'latestBackboneYear':latest['year'],'components':[{'id':'aggi','label':'Long-lived greenhouse-gas forcing','score':latest['score'],'weight':1.0,'raw':latest['raw'],'unit':latest['unit'],'source':'NOAA GML AGGI'}],'series':series,'generatedAt':utc_now_iso(),'method':{'backbone':'NOAA AGGI only','normalization':{'healthyReference':1.0,'criticalReference':a.aggi_upper},'scoreDirection':'100 at AGGI 1.0; 0 at configured upper reference','diagnosticPolicy':'individual greenhouse-gas concentrations are visible diagnostics only'},'sources':[{'id':'noaa-aggi','label':'NOAA Annual Greenhouse Gas Index'}]}; write_json(repo/'frontend/public/data/indices/families/climate_forcing.index.json',payload); write_diagnostic(repo,'climate_forcing',{'schemaVersion':1,'familyId':'climate_forcing','label':'Climate forcing diagnostics','context':{'greenhouseGases':gases},'sources':URLS}); print('Climate forcing',latest['year'],latest['score'])
if __name__=='__main__': main()
