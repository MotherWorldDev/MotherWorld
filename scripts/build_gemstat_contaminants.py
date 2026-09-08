#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from contaminants_common import (
    SeriesAccumulator, attach_analysis_geometry_provenance, canonical_unit, classify_parameter, convert_mass_per_l, find_column,
    merge_region_categories, norm_name, point_region_map, target_unit_for, to_float, year_from,
    CATEGORY_LABELS,
)


def open_csv_from_zip(zf: zipfile.ZipFile, name: str):
    raw = zf.open(name)
    sample = raw.read(65536).decode('utf-8-sig', errors='replace')
    raw.close()
    first = sample.splitlines()[0] if sample else ''
    delim = '\t' if first.count('\t') > first.count(',') else ','
    return io.TextIOWrapper(zf.open(name), encoding='utf-8-sig', errors='replace', newline=''), delim


def find_member(zf, needle):
    needle=needle.lower()
    for n in zf.namelist():
        if needle in Path(n).name.lower(): return n
    return None


def read_station_metadata(zf):
    name=find_member(zf,'gemstat_station_metadata')
    if not name: raise RuntimeError('GEMStat station metadata CSV not found in archive')
    f,delim=open_csv_from_zip(zf,name)
    try: rows=list(csv.DictReader(f,delimiter=delim))
    finally: f.close()
    if not rows: return pd.DataFrame(columns=['station_id','lat','lon'])
    cols=rows[0].keys()
    sid=find_column(cols,('GEMS Station Code','GEMS_Station_Code','Station Code','station_id','stationcode'))
    lat=find_column(cols,('Latitude','lat','Station Latitude'))
    lon=find_column(cols,('Longitude','lon','long','Station Longitude'))
    if not (sid and lat and lon): raise RuntimeError(f'Could not identify station code/lat/lon columns: {list(cols)}')
    out=[]
    for r in rows:
        la,lo=to_float(r.get(lat)),to_float(r.get(lon))
        if la is None or lo is None: continue
        out.append({'station_id':str(r.get(sid) or '').strip(),'lat':la,'lon':lo})
    return pd.DataFrame(out).drop_duplicates('station_id').reset_index(drop=True)


def read_parameter_metadata(zf):
    name=find_member(zf,'gemstat_parameter_metadata')
    if not name: raise RuntimeError('GEMStat parameter metadata CSV not found in archive')
    f,delim=open_csv_from_zip(zf,name)
    try: rows=list(csv.DictReader(f,delimiter=delim))
    finally: f.close()
    if not rows: return {}
    cols=rows[0].keys()
    code=find_column(cols,('Parameter Code','Parameter_Code','parametercode','code'))
    namec=find_column(cols,('Parameter Name','Parameter_Name','parametername','name'))
    longc=find_column(cols,('Parameter Long Name','Parameter_Long_Name','longname'))
    g1=find_column(cols,('Parameter Group 1','Group 1','Parameter_Group_1'))
    g2=find_column(cols,('Parameter Group 2','Group 2','Parameter_Group_2'))
    g3=find_column(cols,('Parameter Group 3','Group 3','Parameter_Group_3'))
    desc=find_column(cols,('Description','parameterdescription'))
    if not code: raise RuntimeError(f'Could not identify parameter-code column: {list(cols)}')
    out={}
    for r in rows:
        c=str(r.get(code) or '').strip()
        if not c: continue
        meta={'code':c,'name':r.get(namec) if namec else c,'long_name':r.get(longc) if longc else '',
              'group1':r.get(g1) if g1 else '','group2':r.get(g2) if g2 else '','group3':r.get(g3) if g3 else '',
              'description':r.get(desc) if desc else ''}
        cat=classify_parameter(meta)
        if cat: meta['category']=cat
        out[c]=meta
    return out


def is_measurement_member(name: str) -> bool:
    base=Path(name).name.lower()
    return base.endswith('.csv') and 'metadata' not in base and not base.startswith('readme')


def detected_from(row, qualifier_col, value) -> bool:
    q=str(row.get(qualifier_col) if qualifier_col else '').strip().lower()
    if q.startswith('<') or any(x in q for x in ('below','non-detect','nondetect','not detected','nd')): return False
    return value is not None


def normalize_value(category, value, unit):
    if value is None: return None, canonical_unit(unit)
    target=target_unit_for(category,unit)
    if category in {'pfas','mercury','pesticides','petroleum_hydrocarbons','other_toxics'}:
        converted=convert_mass_per_l(value,unit,target)
        if converted: return converted
    return float(value), canonical_unit(unit)


def main():
    ap=argparse.ArgumentParser(description='Generate MotherWorld freshwater contaminant observations from UNEP GEMStat open archive.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    ap.add_argument('--archive',type=Path,required=True,help='GFQA_v3.zip from Zenodo DOI 10.5281/zenodo.18459694')
    ap.add_argument('--max-values-per-series',type=int,default=10000)
    args=ap.parse_args(); repo=args.repo.resolve()

    with zipfile.ZipFile(args.archive) as zf:
        stations=read_station_metadata(zf)
        params=read_parameter_metadata(zf)
        wanted={k:v for k,v in params.items() if v.get('category')}
        print(f'GEMStat stations with coordinates: {len(stations):,}; selected contaminant parameters: {len(wanted):,}')
        station_regions=point_region_map(stations[['lat','lon']],repo,kinds={'land','lakes'})
        sid_to_regions={stations.iloc[i]['station_id']: station_regions.get(i,[]) for i in range(len(stations))}
        acc={}

        for member in zf.namelist():
            if not is_measurement_member(member): continue
            f,delim=open_csv_from_zip(zf,member)
            reader=csv.DictReader(f,delimiter=delim)
            try:
                cols=reader.fieldnames or []
                sid=find_column(cols,('GEMS Station Code','GEMS_Station_Code','Station Code','station_id','stationcode'))
                pcode=find_column(cols,('Parameter Code','Parameter_Code','parametercode','parameter'))
                datec=find_column(cols,('Sample Date','Sample_Date','sampledate','date','Sampling Date'))
                valuec=find_column(cols,('Value','value','Result','result','Sample Value','Result Value'))
                unitc=find_column(cols,('Unit','unit','Units','Result Unit'))
                qualc=find_column(cols,('Value Flag','Value_Flag','qualifier','Result Qualifier','Remark'))
                if not (sid and pcode and valuec): continue
                kept=0
                for row in reader:
                    code=str(row.get(pcode) or '').strip(); meta=wanted.get(code)
                    if not meta: continue
                    station=str(row.get(sid) or '').strip(); regions=sid_to_regions.get(station,[])
                    if not regions: continue
                    category=meta['category']; raw=to_float(row.get(valuec)); unit=str(row.get(unitc) or '') if unitc else ''
                    value,out_unit=normalize_value(category,raw,unit)
                    detected=detected_from(row,qualc,raw)
                    year=year_from(row.get(datec)) if datec else None
                    analyte=str(meta.get('long_name') or meta.get('name') or code).strip()
                    series_key=(category,analyte,out_unit)
                    for rid in regions:
                        key=(rid,)+series_key
                        if key not in acc:
                            acc[key]=SeriesAccumulator(analyte,category,out_unit,args.max_values_per_series)
                        acc[key].add(value=value,detected=detected,station_id=station,year=year)
                    kept+=1
                if kept: print(f'  {Path(member).name}: {kept:,} selected records')
            finally: f.close()

    by_region=defaultdict(lambda:defaultdict(list))
    for (rid,category,_,_),series in acc.items(): by_region[rid][category].append(series.payload())
    updates={}
    for rid,cats in by_region.items():
        categories={}
        for category,series_list in cats.items():
            series_list.sort(key=lambda x:(-x['sampleCount'],x['name']))
            categories[category]={
                'label':CATEGORY_LABELS[category],'type':'measurements','analytes':series_list,
                'sampleCount':sum(s['sampleCount'] for s in series_list),
                'detectedCount':sum(s['detectedCount'] for s in series_list),
                'stationCount':len(set()),
                'coverageNote':'In-situ monitoring stations inside this MotherWorld region; coverage is opportunistic and uneven.'
            }
            # Station counts cannot be summed across analytes without double-counting; expose conservative max.
            categories[category]['stationCount']=max((s['stationCount'] for s in series_list),default=0)
        kind='lakes' if rid.startswith('lake_') else 'land'
        updates[rid]={'_kind':kind,'categories':categories,'sources':{'gemstat':{'label':'UNEP GEMS/Water GEMStat Global Freshwater Quality Archive','doi':'10.5281/zenodo.18459694','license':'Open subset CC BY 4.0 or equivalent'}}}
    attach_analysis_geometry_provenance(repo, updates)
    merge_region_categories(repo,updates,{'id':'gemstat','label':'UNEP GEMS/Water GEMStat','doi':'10.5281/zenodo.18459694'})
    print(f'Wrote/updated contaminant observations for {len(updates):,} MotherWorld regions.')

if __name__=='__main__': main()
