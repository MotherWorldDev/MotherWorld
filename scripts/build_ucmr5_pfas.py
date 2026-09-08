#!/usr/bin/env python
from __future__ import annotations

import argparse, csv, io, zipfile
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from contaminants_common import SeriesAccumulator, find_column, load_region_geometries, merge_region_categories, to_float, year_from, CATEGORY_LABELS

ZCTA_URL='https://www2.census.gov/geo/tiger/TIGER2020/ZCTA520/tl_2020_us_zcta520.zip'


def member(zf, text):
    text=text.lower()
    return next((n for n in zf.namelist() if text in Path(n).name.lower()),None)


def dict_reader(zf,name):
    return csv.DictReader(io.TextIOWrapper(zf.open(name),encoding='utf-8-sig',errors='replace',newline=''),delimiter='\t')


def main():
    ap=argparse.ArgumentParser(description='Build approximate MotherWorld land-region PFAS exposure observations from EPA UCMR5 drinking-water data.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--ucmr-zip',type=Path,required=True,help='EPA UCMR5 occurrence-data ZIP containing All results and ZipCodes files')
    ap.add_argument('--zcta',type=Path,default=None,help='2020 Census ZCTA shapefile ZIP; download automatically if omitted')
    args=ap.parse_args(); repo=args.repo.resolve()
    zcta=args.zcta or repo/'contaminants_raw/census/tl_2020_us_zcta520.zip'
    if not zcta.exists():
        zcta.parent.mkdir(parents=True,exist_ok=True); print('Downloading Census 2020 ZCTA polygons…')
        r=requests.get(ZCTA_URL,timeout=180); r.raise_for_status(); zcta.write_bytes(r.content)

    with zipfile.ZipFile(args.ucmr_zip) as zf:
        zip_member=member(zf,'UCMR5_ZipCodes')
        result_members=[n for n in zf.namelist() if Path(n).name.lower().startswith('ucmr5_all') and n.lower().endswith('.txt') and 'zipcode' not in n.lower()]
        if not zip_member or not result_members: raise SystemExit('Could not find UCMR5_ZipCodes.txt and UCMR5_All*.txt in supplied ZIP')
        zr=dict_reader(zf,zip_member); zcols=zr.fieldnames or []
        pws=find_column(zcols,('PWSID','PWS ID')); zipc=find_column(zcols,('ZipCode','ZIP Code','ZIP'))
        pws_zips=defaultdict(set)
        for row in zr:
            pid=str(row.get(pws) or '').strip(); z=str(row.get(zipc) or '').strip()[:5]
            if pid and len(z)==5 and z.isdigit(): pws_zips[pid].add(z)

        zgdf=gpd.read_file(f'zip://{zcta}')
        if zgdf.crs is None: zgdf=zgdf.set_crs('EPSG:4269')
        zgdf=zgdf.to_crs('EPSG:4326')
        zcol=find_column(zgdf.columns,('ZCTA5CE20','ZCTA5CE','GEOID20','GEOID'))
        needed=set().union(*pws_zips.values()) if pws_zips else set()
        zgdf=zgdf[zgdf[zcol].astype(str).isin(needed)].copy(); zgdf['zip']=zgdf[zcol].astype(str); zgdf['geometry']=zgdf.geometry.representative_point()
        land=[{'regionId':rid,'geometry':geom} for rid,(kind,geom) in load_region_geometries(repo).items() if kind=='land']
        rgdf=gpd.GeoDataFrame(land,geometry='geometry',crs='EPSG:4326')
        joined=gpd.sjoin(zgdf[['zip','geometry']],rgdf,how='left',predicate='within')
        zip_regions=defaultdict(set)
        for _,row in joined.iterrows():
            if isinstance(row.get('regionId'),str): zip_regions[row['zip']].add(row['regionId'])
        pws_regions={pid:set().union(*(zip_regions.get(z,set()) for z in zs)) for pid,zs in pws_zips.items()}

        acc={}
        for name in result_members:
            rr=dict_reader(zf,name); cols=rr.fieldnames or []
            pwscol=find_column(cols,('PWSID','PWS ID')); cont=find_column(cols,('Contaminant','Analyte')); sign=find_column(cols,('AnalyticalResultsSign','Analytical Results Sign')); val=find_column(cols,('AnalyticalResultValue','Analytical Result Value')); datec=find_column(cols,('CollectionDate','Collection Date'))
            if not (pwscol and cont): continue
            for row in rr:
                analyte=str(row.get(cont) or '').strip()
                if not analyte or analyte.lower()=='lithium': continue
                pid=str(row.get(pwscol) or '').strip(); regions=pws_regions.get(pid,set())
                if not regions: continue
                raw=to_float(row.get(val)) if val else None; detected=not str(row.get(sign) or '').strip().startswith('<') and raw is not None
                year=year_from(row.get(datec)) if datec else None
                for rid in regions:
                    key=(rid,analyte)
                    if key not in acc: acc[key]=SeriesAccumulator(analyte,'pfas','ng/L')
                    acc[key].add(value=raw,detected=detected,station_id=pid,year=year)

    by=defaultdict(list)
    for (rid,_),series in acc.items(): by[rid].append(series.payload())
    updates={}
    for rid,series in by.items():
        series.sort(key=lambda x:(-x['sampleCount'],x['name']))
        updates[rid]={'_kind':'land','categories':{'pfas':{
            'label':CATEGORY_LABELS['pfas'],'type':'measurements','analytes':series,
            'sampleCount':sum(s['sampleCount'] for s in series),'detectedCount':sum(s['detectedCount'] for s in series),
            'stationCount':max((s['stationCount'] for s in series),default=0),
            'coverageNote':'U.S. EPA UCMR5 drinking-water PFAS results mapped to MotherWorld ecoregions by representative point of ZIP codes served by each public water system. This is a service-area exposure proxy, not exact source-water or sample-point geography.'
        }},'sources':{'epa_ucmr5':{'label':'U.S. EPA UCMR 5 PFAS occurrence data','period':'2023-2025','scope':'U.S. public drinking-water systems'}}}
    merge_region_categories(repo,updates,{'id':'epa_ucmr5','label':'U.S. EPA UCMR 5 PFAS occurrence data'})
    print(f'Wrote/updated PFAS service-area observations for {len(updates):,} land ecoregions.')

if __name__=='__main__': main()
