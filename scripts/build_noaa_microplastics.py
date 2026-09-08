#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import pandas as pd

from contaminants_common import SeriesAccumulator, find_column, merge_region_categories, point_region_map, to_float, year_from, CATEGORY_LABELS


def main():
    ap=argparse.ArgumentParser(description='Map NOAA NCEI Marine Microplastics observations into MotherWorld marine ecoregions.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--input',type=Path,required=True,help='CSV exported from NOAA NCEI Marine Microplastics database')
    ap.add_argument('--max-values-per-series',type=int,default=10000)
    args=ap.parse_args(); repo=args.repo.resolve()
    df=pd.read_csv(args.input,low_memory=False)
    lat=find_column(df.columns,('Latitude','lat','Latitude (degree)','Latitude (degrees)'))
    lon=find_column(df.columns,('Longitude','lon','Longitude (degree)','Longitude (degrees)'))
    val=find_column(df.columns,('Concentration','Microplastics Measurement','Measurement','Value','Concentration Value'))
    unit=find_column(df.columns,('Unit','Units','Concentration Unit','Measurement Unit'))
    date=find_column(df.columns,('Date','Sampling Date','Collection Date','Sample Date','Year'))
    setting=find_column(df.columns,('Marine Setting','Setting','Sample Type','Medium'))
    if not (lat and lon): raise SystemExit(f'Could not identify latitude/longitude columns. Columns: {list(df.columns)}')
    df['_lat']=pd.to_numeric(df[lat],errors='coerce'); df['_lon']=pd.to_numeric(df[lon],errors='coerce')
    df=df[df['_lat'].between(-90,90)&df['_lon'].between(-180,180)].reset_index(drop=True)
    mapping=point_region_map(pd.DataFrame({'lat':df['_lat'],'lon':df['_lon']}),repo,kinds={'marine'})
    acc={}; observation_counts=defaultdict(int)
    for i,row in df.iterrows():
        regions=mapping.get(i,[])
        if not regions: continue
        raw=to_float(row.get(val)) if val else None
        u=str(row.get(unit) or 'reported unit') if unit else 'reported unit'
        medium=str(row.get(setting) or 'marine sample') if setting else 'marine sample'
        analyte=f'Microplastics · {medium}'
        y=year_from(row.get(date)) if date else None
        for rid in regions:
            observation_counts[rid]+=1
            if raw is None: continue
            key=(rid,analyte,u)
            if key not in acc: acc[key]=SeriesAccumulator(analyte,'microplastics',u,args.max_values_per_series)
            acc[key].add(value=raw,detected=True,station_id=f'obs-{i}',year=y)
    by=defaultdict(list)
    for (rid,_,_),series in acc.items(): by[rid].append(series.payload())
    updates={}
    for rid,count in observation_counts.items():
        analytes=sorted(by.get(rid,[]),key=lambda x:(-x['sampleCount'],x['unit']))
        updates[rid]={'_kind':'marine','categories':{'microplastics':{
            'label':CATEGORY_LABELS['microplastics'],'type':'measurements','analytes':analytes,
            'sampleCount':count,'detectedCount':count,'stationCount':count,
            'coverageNote':'NOAA global marine microplastics observations. Sampling methods and reporting units vary across studies; concentrations are never merged across units or protocols.'
        }},'sources':{'noaa_microplastics':{'label':'NOAA NCEI Marine Microplastics Database','url':'https://www.ncei.noaa.gov/products/microplastics','coverage':'1972-present global marine observations'}}}
    merge_region_categories(repo,updates,{'id':'noaa_microplastics','label':'NOAA NCEI Marine Microplastics Database'})
    print(f'Wrote/updated marine microplastics observations for {len(updates):,} regions.')

if __name__=='__main__': main()
