#!/usr/bin/env python
from __future__ import annotations
import argparse, io
from pathlib import Path
import pandas as pd, requests
from backbone_common import HISTORY_START_YEAR, family_payload, write_family

DEFAULT_URL='https://ourworldindata.org/grapher/red-list-index.csv'

def load_csv(path, url):
    if path: return pd.read_csv(path)
    r=requests.get(url,timeout=90); r.raise_for_status(); return pd.read_csv(io.StringIO(r.text))

def find_col(df, words):
    for c in df.columns:
        s=str(c).lower()
        if all(w in s for w in words): return c
    return None

def main():
    p=argparse.ArgumentParser(description='Build Earth Health Biodiversity backbone from the BirdLife/IUCN Red List Index.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--csv',type=Path); p.add_argument('--url',default=DEFAULT_URL); p.add_argument('--entity',default='World'); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); a=p.parse_args(); repo=a.repo.resolve()
    df=load_csv(a.csv,a.url); yc=find_col(df,['year']) or next((c for c in df.columns if str(c).lower()=='year'),None)
    vc=find_col(df,['red','list','index']) or next((c for c in df.columns if 'rli' in str(c).lower()),None)
    if yc is None or vc is None: raise SystemExit(f'Could not identify Year/RLI columns: {list(df.columns)}')
    ec=next((c for c in ('Entity','Area','GeoAreaName','Location') if c in df.columns),None)
    if ec:
        sub=df[df[ec].astype(str).str.casefold()==a.entity.casefold()]
        if not sub.empty: df=sub
    df[yc]=pd.to_numeric(df[yc],errors='coerce'); df[vc]=pd.to_numeric(df[vc],errors='coerce'); df=df.dropna(subset=[yc,vc]).sort_values(yc)
    series=[]
    for _,r in df.iterrows():
        y=int(r[yc]); raw=float(r[vc]); score=raw*100 if abs(raw)<=1.5 else raw
        if y>=a.start_year: series.append({'year':y,'score':score,'coverage':1.0,'raw':raw,'unit':'RLI (1 = all species Least Concern; 0 = all extinct)'})
    payload=family_payload(family_id='biodiversity',label='Biodiversity',series=series,component={'id':'red_list_index','label':'Red List Index','weight':1.0,'source':'BirdLife International / IUCN Red List Index'},source={'id':'iucn-birdlife-rli','label':'BirdLife International / IUCN Red List Index','retrieval':str(a.csv) if a.csv else a.url},method={'backbone':'Red List Index × 100','scoreDirection':'100 = best conservation-status state represented by RLI','historicalConsistency':'same global RLI backbone is used for every Earth Health year','diagnosticPolicy':'BII, IUCN range metrics, PHYLACINE and recorded species remain present-day diagnostics and do not change Earth Health'},context={'warning':'RLI tracks aggregate extinction risk, not full biodiversity intactness.'})
    out=write_family(repo,'biodiversity',payload); print(f'Biodiversity backbone {payload["latestBackboneYear"]}: {payload["score"]:.2f}/100 -> {out}')
if __name__=='__main__': main()
