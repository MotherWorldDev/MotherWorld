#!/usr/bin/env python
from __future__ import annotations
import argparse, zipfile, io, math
from pathlib import Path
import geopandas as gpd
import pandas as pd
from land_pollution_common import assign_points, find_column, merge_region_provider, recompute_peer_percentiles, convert_mass_to_kg, norm


def read_table(path: Path):
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            names=[n for n in z.namelist() if n.lower().endswith(('.csv','.txt'))]
            if not names: raise SystemExit(f'No CSV/TXT in {path}')
            name=max(names,key=lambda n:z.getinfo(n).file_size)
            with z.open(name) as f:
                raw=f.read();
            try:return pd.read_csv(io.BytesIO(raw),low_memory=False)
            except Exception:return pd.read_csv(io.BytesIO(raw),sep='\t',low_memory=False)
    try:return pd.read_csv(path,low_memory=False)
    except Exception:return pd.read_csv(path,sep='\t',low_memory=False)


def superfund_updates(repo,path):
    if not path:return {}
    try:
        g=gpd.read_file(path); g=g.set_crs('EPSG:4326') if g.crs is None else g.to_crs('EPSG:4326'); g=g.copy(); g['lat']=g.geometry.representative_point().y; g['lon']=g.geometry.representative_point().x; df=pd.DataFrame(g.drop(columns='geometry'))
    except Exception:
        df=read_table(path); lat=find_column(df.columns,('latitude','lat')); lon=find_column(df.columns,('longitude','lon','lng')); 
        if not lat or not lon: raise SystemExit('Superfund input needs geometry or latitude/longitude columns.')
        df=df.rename(columns={lat:'lat',lon:'lon'})
    df['lat']=pd.to_numeric(df.lat,errors='coerce'); df['lon']=pd.to_numeric(df.lon,errors='coerce'); df=df.dropna(subset=['lat','lon']); j=assign_points(df,repo)
    out={}
    for rid,g in j.dropna(subset=['regionId']).groupby('regionId'):
        out[rid]={'label':'EPA Superfund / NPL sites','coverage':'Authoritative U.S. federal hazardous-site registry; only applicable where EPA Superfund data exist.','metrics':{'superfundSiteCount':int(len(g))}}
    return out


def tri_updates(repo,path):
    if not path:return {}
    df=read_table(path); lat=find_column(df.columns,('latitude','lat')); lon=find_column(df.columns,('longitude','lon')); fid=find_column(df.columns,('TRIFID','TRI_FACILITY_ID','facility id','facilityid')); year=find_column(df.columns,('reporting year','year')); unit=find_column(df.columns,('unit of measure','unit')); chem=find_column(df.columns,('chemical','chemical name'))
    if not lat or not lon: raise SystemExit('TRI input is missing latitude/longitude.')
    df=df.copy(); df['lat']=pd.to_numeric(df[lat],errors='coerce'); df['lon']=pd.to_numeric(df[lon],errors='coerce'); df=df.dropna(subset=['lat','lon'])
    land_cols=[]
    for c in df.columns:
        n=norm(c)
        if n.startswith('55') or any(k in n for k in ('rcraclandfills','otherlandfills','landtreatment','surfaceimpoundment','otherdisposal')):
            land_cols.append(c)
    # dedupe while preserving order
    land_cols=list(dict.fromkeys(land_cols))
    if not land_cols: raise SystemExit('Could not detect TRI land-disposal fields (5.5.x landfill/land treatment/surface impoundment).')
    def row_kg(r):
        u=r[unit] if unit else 'pounds'; total=0.0
        for c in land_cols:
            x=convert_mass_to_kg(r[c],u)
            if x is not None: total+=x
        return total
    df['landReleaseKg']=df.apply(row_kg,axis=1)
    if year: df['_year']=pd.to_numeric(df[year],errors='coerce')
    j=assign_points(df,repo); out={}
    for rid,g in j.dropna(subset=['regionId']).groupby('regionId'):
        latest=int(g['_year'].max()) if '_year' in g and g['_year'].notna().any() else None; gg=g[g._year==latest] if latest else g
        facilities=int(gg[fid].astype(str).nunique()) if fid else int(len(gg)); metrics={'triFacilityCount':facilities,'triLandReleaseKg':float(gg.landReleaseKg.sum()),'latestYear':latest,'chemicalReportCount':int(len(gg))}
        top=[]
        if chem:
            sums=gg.groupby(chem).landReleaseKg.sum().sort_values(ascending=False).head(10)
            top=[{'chemical':str(k),'landReleaseKg':float(v)} for k,v in sums.items() if v>0]
        out[rid]={'label':'EPA Toxic Release Inventory · land disposal','coverage':'U.S. TRI-reporting facilities only; reporting thresholds apply. Mass is not toxicity-weighted.','metrics':metrics,'topChemicalsByMass':top}
    return out


def main():
    ap=argparse.ArgumentParser(description='Aggregate U.S. EPA Superfund and TRI land-disposal metrics by MotherWorld ecoregion.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--superfund',type=Path); ap.add_argument('--tri',type=Path)
    args=ap.parse_args(); repo=args.repo.resolve()
    a=superfund_updates(repo,args.superfund)
    if a: merge_region_provider(repo,a,{'id':'epa-superfund','label':'US EPA Superfund Site Location Information','license':'U.S. government public data','url':'https://www.epa.gov/superfund/superfund-data-and-reports','caveat':'U.S.-only registry; site footprints and status evolve as remediation proceeds.'})
    b=tri_updates(repo,args.tri)
    if b: merge_region_provider(repo,b,{'id':'epa-tri-land','label':'US EPA Toxics Release Inventory · land disposal','license':'U.S. government public data','url':'https://www.epa.gov/toxics-release-inventory-tri-program/tri-basic-data-files-calendar-years-1987-present','caveat':'Reported chemical mass should not be read as a toxicity score; TRI has facility/chemical reporting thresholds.'})
    recompute_peer_percentiles(repo); print(f'Superfund regions: {len(a)}; TRI regions: {len(b)}')
if __name__=='__main__':main()
