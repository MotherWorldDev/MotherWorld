#!/usr/bin/env python
from __future__ import annotations
import argparse, csv, io, re
from pathlib import Path
import requests
from backbone_common import HISTORY_START_YEAR, family_payload, write_family, write_diagnostic

URL='https://gml.noaa.gov/odgi/odgi_table2.csv'
def norm(s): return re.sub(r'[^a-z0-9]+','',str(s).lower())
def main():
    p=argparse.ArgumentParser(description='Build global Ozone Layer Earth Health backbone by reversing NOAA ODGI-ML.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--csv',type=Path); p.add_argument('--url',default=URL); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); a=p.parse_args(); repo=a.repo.resolve(); 
    if a.csv: text=a.csv.read_text(encoding='utf-8',errors='ignore')
    else:
        response=requests.get(a.url,timeout=90); response.raise_for_status(); text=response.text
    reader=csv.DictReader(io.StringIO(text)); fields=reader.fieldnames or []; ym=next((c for c in fields if 'year' in norm(c)),None)
    mid=next((c for c in fields if 'odgi' in norm(c) and any(k in norm(c) for k in ('midlat','midlatitude','ml')) and 'old' not in norm(c)),None)
    ant=next((c for c in fields if 'odgi' in norm(c) and any(k in norm(c) for k in ('antarctic','ant','aodgi')) and 'old' not in norm(c)),None)
    if mid is None:
        candidates=[c for c in fields if 'odgi' in norm(c) and 'old' not in norm(c)]; mid=candidates[0] if candidates else None
    if ym is None or mid is None: raise SystemExit(f'Could not identify Year/ODGI-ML columns: {fields}')
    series=[]; ant_series=[]
    for r in reader:
        try: y=int(float(r[ym])); v=float(r[mid])
        except Exception: continue
        if y>=a.start_year: series.append({'year':y,'score':max(0,min(100,100-v)),'coverage':1.0,'raw':v,'unit':'NOAA ODGI-ML'})
        if ant:
            try: av=float(r[ant]); ant_series.append({'year':y,'odgiA':av,'healthEquivalent':max(0,min(100,100-av))})
            except Exception: pass
    payload=family_payload(family_id='ozone',label='Ozone layer',series=series,component={'id':'odgi_ml_recovery','label':'Ozone-depleting gas recovery','weight':1.0,'source':'NOAA Ozone Depleting Gas Index (mid-latitude)'},source={'id':'noaa-odgi','label':'NOAA Global Monitoring Laboratory Ozone Depleting Gas Index','retrieval':str(a.csv) if a.csv else a.url},method={'backbone':'100 − NOAA ODGI-ML','scoreDirection':'100 = return to NOAA 1980 benchmark; 0 = peak ozone-depleting halogen burden','diagnosticPolicy':'Antarctic ODGI-A is context only and receives no extra Earth Health weight'},context={'globalOnly':True})
    out=write_family(repo,'ozone',payload)
    write_diagnostic(repo,'ozone',{'schemaVersion':1,'familyId':'ozone','label':'Ozone layer diagnostics','components':[{'id':'antarctic_odgi','label':'Antarctic ODGI-A','series':ant_series,'source':'NOAA GML'}],'note':'Antarctic ODGI-A is not additionally scored in Earth Health.'})
    print(f'Ozone backbone latest={payload["latestBackboneYear"]} score={payload["score"]} -> {out}')
if __name__=='__main__': main()
