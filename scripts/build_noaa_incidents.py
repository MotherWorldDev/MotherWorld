#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

from contaminants_common import CATEGORY_LABELS, to_float, year_from
from regional_diagnostics_adapters import canonical_point_region_map, merge_contaminant_categories, output_root

URL='https://incidentnews.noaa.gov/raw/incidents.csv'

def clean_text(value, default=''):
    if value is None or pd.isna(value):
        return default
    return str(value).strip()


def main():
    ap=argparse.ArgumentParser(description='Map NOAA IncidentNews oil/chemical spill incidents to MotherWorld marine ecoregions.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--input',type=Path,default=None)
    ap.add_argument('--download',action='store_true')
    ap.add_argument('--output-root',type=Path,default=None,help='v8 build root; deployable files are written below frontend/public/data/')
    args=ap.parse_args(); repo=args.repo.resolve(); root=output_root(repo,args.output_root)
    path=args.input
    if args.download or path is None:
        path=repo/'contaminants_raw/noaa_incidents/incidents.csv'; path.parent.mkdir(parents=True,exist_ok=True)
        r=requests.get(URL,timeout=120); r.raise_for_status(); path.write_bytes(r.content)
    df=pd.read_csv(path,low_memory=False)
    df=df[pd.to_numeric(df.get('lat'),errors='coerce').notna() & pd.to_numeric(df.get('lon'),errors='coerce').notna()].copy()
    df['_lat']=pd.to_numeric(df['lat']); df['_lon']=pd.to_numeric(df['lon']); df=df.reset_index(drop=True)
    mapping=canonical_point_region_map(pd.DataFrame({'lat':df['_lat'],'lon':df['_lon']}),repo,kinds={'marine'})
    events=defaultdict(lambda:defaultdict(list))
    for i,row in df.iterrows():
        threat=clean_text(row.get('threat')).lower()
        cat='oil_incidents' if threat=='oil' else 'chemical_incidents' if threat=='chemical' else None
        if not cat: continue
        for rid in mapping.get(i,[]):
            events[rid][cat].append({
                'id':int(row['id']) if pd.notna(row.get('id')) else None,'date':clean_text(row.get('open_date')),
                'year':year_from(row.get('open_date')),'name':clean_text(row.get('name')),'location':clean_text(row.get('location')),
                'commodity':clean_text(row.get('commodity')),'maxPotentialReleaseGallons':to_float(row.get('max_ptl_release_gallons')),
                'tags':clean_text(row.get('tags')),
            })
    updates={}
    for rid,cats in events.items():
        payload_cats={}
        for cat,rows in cats.items():
            rows.sort(key=lambda r:r.get('date') or '',reverse=True)
            known=[r['maxPotentialReleaseGallons'] for r in rows if r['maxPotentialReleaseGallons'] is not None]
            payload_cats[cat]={
                'label':CATEGORY_LABELS[cat],'type':'incidents','eventCount':len(rows),
                'firstYear':min((r['year'] for r in rows if r['year']),default=None),'lastYear':max((r['year'] for r in rows if r['year']),default=None),
                'knownPotentialReleaseGallonsSum':sum(known) if known else None,'maxPotentialReleaseGallons':max(known) if known else None,
                'recentEvents':rows[:20],
                'coverageNote':'Selected incidents where NOAA Office of Response and Restoration provided scientific support; this is not a complete global spill inventory. Release quantity is maximum potential release where reported, not necessarily actual release.'
            }
        updates[rid]={'_kind':'marine','categories':payload_cats,'sources':{'noaa_incidentnews':{'label':'NOAA IncidentNews raw incident archive','url':URL,'scope':'Selected U.S. coastal oil and chemical incidents'}}}
    merge_contaminant_categories(repo,root,updates,{'id':'noaa_incidentnews','label':'NOAA IncidentNews','url':URL,'scope':'Selected U.S. coastal oil and chemical incidents','stagedInput':path.name,'inputBytes':path.stat().st_size})
    print(f'Wrote/updated canonical incident observations for {len(updates):,} marine regions.')

if __name__=='__main__': main()
