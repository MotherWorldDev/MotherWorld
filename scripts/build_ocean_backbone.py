#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import requests
from backbone_common import family_payload, parse_year_value_text, write_family

URL='https://www.ncei.noaa.gov/data/oceans/woa/DATA_ANALYSIS/3M_HEAT_CONTENT/DATA/basin/yearly/h22-w0-700m.dat'

def main():
    p=argparse.ArgumentParser(description='Build Global Ocean Earth Health backbone from NOAA World Ocean 0-700 m heat content.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--input',type=Path); p.add_argument('--url',default=URL); p.add_argument('--baseline-start',type=int,default=1955); p.add_argument('--baseline-end',type=int,default=1964); p.add_argument('--critical-increase-zj',type=float,default=500.0); a=p.parse_args(); repo=a.repo.resolve()
    
    if a.input: text=a.input.read_text(encoding='utf-8',errors='ignore')
    else:
        response=requests.get(a.url,timeout=90); response.raise_for_status(); text=response.text
    rows=parse_year_value_text(text)
    if not rows: raise SystemExit('Could not parse NOAA OHC year/value series')
    # NOAA h22 files are in 10^22 J; convert to zettajoules (1e21 J) by ×10.
    rows=[{'year':r['year'],'value':r['value']*10.0} for r in rows]
    base=[r['value'] for r in rows if a.baseline_start<=r['year']<=a.baseline_end]
    if not base: raise SystemExit('No OHC rows in configured baseline')
    baseline=sum(base)/len(base); series=[]
    for r in rows:
        delta=r['value']-baseline; score=max(0,min(100,100*(1-delta/a.critical_increase_zj)))
        series.append({'year':r['year'],'score':score,'coverage':1.0,'raw':r['value'],'unit':'ZJ (NOAA 0–700 m OHC series)','changeFromBaselineZJ':delta})
    payload=family_payload(family_id='ocean',label='Global Ocean',series=series,component={'id':'ocean_heat_content','label':'Ocean heat-content stability','weight':1.0,'source':'NOAA World Ocean Heat Content'},source={'id':'noaa-ohc','label':'NOAA/NCEI World Ocean 0–700 m annual heat content','retrieval':str(a.input) if a.input else a.url},method={'baseline':f'{a.baseline_start}-{a.baseline_end} mean','normalization':f'100 at baseline; 0 after +{a.critical_increase_zj:g} ZJ','scoreDirection':'100 = baseline ocean heat content retained','metricScope':'entire ocean; not only the visual open-ocean residual','diagnosticPolicy':'ocean pH, sea level, marine heatwaves, oxygen and detailed water quality remain present-day diagnostics only'},scope='global_ocean',context={'selectionGeometry':'open_ocean residual','speciesScope':'residual only','metricScope':'entire ocean = residual + all coastal/marine regions'})
    out=write_family(repo,'ocean',payload); print(f'Ocean backbone latest={payload["latestBackboneYear"]} score={payload["score"]:.2f} -> {out}')
if __name__=='__main__': main()
