#!/usr/bin/env python
from __future__ import annotations
import argparse, zipfile, io, re
from pathlib import Path
import pandas as pd
from land_pollution_common import assign_points, attach_analysis_geometry_provenance, find_column, merge_region_provider, recompute_peer_percentiles, to_float, year_from


def read_tables(path: Path):
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                low=name.lower()
                if 'solid-waste-disposal' in low and 'emissions_sources' in low and low.endswith('.csv') and 'confidence' not in low and 'ownership' not in low:
                    with z.open(name) as f: return pd.read_csv(f,low_memory=False)
        raise SystemExit('Could not find solid-waste-disposal *_emissions_sources.csv in Climate TRACE ZIP.')
    return pd.read_csv(path,low_memory=False)


def main():
    ap=argparse.ArgumentParser(description='Aggregate Climate TRACE solid-waste-disposal landfill/dumpsite emissions sources by ecoregion.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--input',type=Path,required=True)
    args=ap.parse_args(); repo=args.repo.resolve(); df=read_tables(args.input)
    lat=find_column(df.columns,('lat','latitude')); lon=find_column(df.columns,('lon','lng','longitude')); sid=find_column(df.columns,('source_id','asset_id')); gas=find_column(df.columns,('gas',)); qty=find_column(df.columns,('emissions_quantity','emissions')); start=find_column(df.columns,('start_time','year')); name=find_column(df.columns,('source_name','asset_name','name'))
    if not lat or not lon or not sid: raise SystemExit(f'Missing required Climate TRACE fields. columns={list(df.columns)[:30]}')
    df=df[pd.to_numeric(df[lat],errors='coerce').notna() & pd.to_numeric(df[lon],errors='coerce').notna()].copy(); df['lat']=pd.to_numeric(df[lat]); df['lon']=pd.to_numeric(df[lon]); df['year']=df[start].map(year_from) if start else None
    joined=assign_points(df,repo)
    updates={}
    for rid,g in joined.dropna(subset=['regionId']).groupby('regionId'):
        facilities=int(g[sid].astype(str).nunique()); latest_year=int(g['year'].dropna().max()) if g['year'].notna().any() else None
        latest=g[g.year==latest_year] if latest_year else g
        metrics={'facilityCount':facilities,'latestYear':latest_year}
        if gas and qty:
            for target,outkey in [('ch4','latestMethaneTonnes'),('co2e_100yr','latestCO2e100Tonnes'),('co2e100','latestCO2e100Tonnes')]:
                m=latest[latest[gas].astype(str).str.lower().str.replace('-','_').str.replace(' ','_')==target]
                if not m.empty:
                    vals=pd.to_numeric(m[qty],errors='coerce'); metrics[outkey]=float(vals.sum(skipna=True))
        sample=[]
        cols=[sid]+([name] if name else [])
        for _,r in g.drop_duplicates(subset=[sid]).head(20).iterrows(): sample.append({'id':str(r[sid]),'name':str(r[name]) if name and pd.notna(r[name]) else None,'lat':float(r['lat']),'lon':float(r['lon'])})
        updates[rid]={'label':'Climate TRACE solid-waste disposal','coverage':'Global emissions inventory of modeled/reported landfills and dumpsites; source completeness and methods vary by country.','metrics':metrics,'sampleSites':sample}
    attach_analysis_geometry_provenance(repo, updates)
    merge_region_provider(repo,updates,{'id':'climate-trace-solid-waste','label':'Climate TRACE solid waste disposal','license':'CC BY 4.0 (Climate TRACE outputs; review upstream-source terms)','url':'https://climatetrace.org/data','caveat':'Methane/CO2e are waste-activity/emissions proxies, not direct measurements of soil contamination.'})
    recompute_peer_percentiles(repo); print(f'Updated {len(updates)} regions from Climate TRACE solid-waste disposal sources.')
if __name__=='__main__':main()
