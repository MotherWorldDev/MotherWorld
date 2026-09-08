#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from land_pollution_common import assign_points, attach_analysis_geometry_provenance, find_column, merge_region_provider, recompute_peer_percentiles


def main():
    ap=argparse.ArgumentParser(description='Aggregate European Industrial Emissions Portal facility-level waste-transfer CSV by ecoregion.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--input',type=Path,required=True,help='F4_2 Detailed waste transfer at facility level CSV from EEA user-friendly exports.')
    args=ap.parse_args(); repo=args.repo.resolve(); df=pd.read_csv(args.input,low_memory=False)
    lat=find_column(df.columns,('Latitude','lat')); lon=find_column(df.columns,('Longitude','lon')); qty=find_column(df.columns,('Quantity','waste quantity')); wtype=find_column(df.columns,('WasteTypeName','Waste Type Name','waste type')); year=find_column(df.columns,('ReportingYear','reporting year','year')); facility=find_column(df.columns,('FacilityID','Facility Id','FacilityReportID','facility identifier')); treatment=find_column(df.columns,('WasteTreatmentName','waste treatment'))
    if not lat or not lon or not qty: raise SystemExit(f'Expected facility-level EEA waste-transfer CSV with latitude, longitude and quantity. columns={list(df.columns)[:40]}')
    df=df.copy(); df['lat']=pd.to_numeric(df[lat],errors='coerce'); df['lon']=pd.to_numeric(df[lon],errors='coerce'); df['quantityTonnes']=pd.to_numeric(df[qty],errors='coerce'); df=df.dropna(subset=['lat','lon'])
    if year: df['_year']=pd.to_numeric(df[year],errors='coerce')
    j=assign_points(df,repo); out={}
    for rid,g in j.dropna(subset=['regionId']).groupby('regionId'):
        latest=int(g._year.max()) if '_year' in g and g._year.notna().any() else None; gg=g[g._year==latest] if latest else g
        hazardous=0.0; nonhaz=0.0
        if wtype:
            wt=gg[wtype].astype(str).str.lower().str.replace('-', ' ', regex=False)
            nonmask=wt.str.contains('non hazardous|nonhazardous', regex=True)
            hazmask=wt.str.contains('hazard', regex=False) & ~nonmask
            hazardous=float(gg.loc[hazmask,'quantityTonnes'].sum(skipna=True)); nonhaz=float(gg.loc[nonmask,'quantityTonnes'].sum(skipna=True))
        else: nonhaz=float(gg.quantityTonnes.sum(skipna=True))
        out[rid]={'label':'European Industrial Emissions Portal · waste transfers','coverage':'Largest regulated industrial facilities in reporting European countries; thresholds and coverage are regulation-specific.','metrics':{'industrialFacilityCount':int(gg[facility].astype(str).nunique()) if facility else int(len(gg)),'hazardousWasteTransferTonnes':hazardous,'nonHazardousWasteTransferTonnes':nonhaz,'totalWasteTransferTonnes':float(gg.quantityTonnes.sum(skipna=True)),'latestYear':latest}}
    attach_analysis_geometry_provenance(repo, out)
    merge_region_provider(repo,out,{'id':'eea-industrial-waste','label':'EEA Industrial Emissions Portal / E-PRTR waste transfers','license':'EEA reuse policy / EU open data; cite dataset release','url':'https://industry.eea.europa.eu/industrial-emissions/dataset','caveat':'Facility waste transfers are regulated reporting flows, not a map of all contaminated land or illegal dumping.'})
    recompute_peer_percentiles(repo); print(f'Updated {len(out)} regions from European industrial waste-transfer reporting.')
if __name__=='__main__':main()
