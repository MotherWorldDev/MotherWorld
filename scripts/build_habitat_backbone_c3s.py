#!/usr/bin/env python
from __future__ import annotations
import argparse, re
from pathlib import Path
import numpy as np, xarray as xr
from backbone_common import HISTORY_START_YEAR, family_payload, write_family

# ESA/C3S LCCS classes. Fractions deliberately represent direct conversion only.
CONVERSION={10:1.0,11:1.0,12:1.0,20:1.0,30:0.70,40:0.30,190:1.0}
EXCLUDE={0,210,220}

def year_from(path, ds, ti=None):
    if 'time' in ds.coords:
        try:
            value=ds['time'].values if ti is None else ds['time'].values[ti]
            return int(np.asarray(value).astype('datetime64[Y]').astype(int)+1970)
        except Exception: pass
    m=re.search(r'(19|20)\d{2}',path.name); return int(m.group()) if m else None

def variable(ds):
    for name in ('lccs_class','land_cover','classification','class'):
        if name in ds.data_vars: return name
    candidates=[v for v in ds.data_vars if np.issubdtype(ds[v].dtype,np.integer)]
    return candidates[0] if candidates else list(ds.data_vars)[0]

def score_da(da, lat_name, chunk_rows):
    other=[d for d in da.dims if d!=lat_name]
    if len(other)!=1: raise ValueError(f'Expected lat/lon classification, got {da.dims}')
    total=converted=0.0; shares={'cropland':0.0,'urban':0.0,'mosaic':0.0}; n=da.sizes[lat_name]
    lats=np.asarray(da[lat_name].values,float)
    for start in range(0,n,chunk_rows):
        end=min(n,start+chunk_rows); arr=np.asarray(da.isel({lat_name:slice(start,end)}).values)
        roww=np.cos(np.deg2rad(lats[start:end])).reshape((-1,1)); valid=np.ones(arr.shape,dtype=bool)
        for code in EXCLUDE: valid &= arr!=code
        weight=np.broadcast_to(roww,arr.shape)
        total += float(weight[valid].sum())
        frac=np.zeros(arr.shape,dtype=float)
        for code,f in CONVERSION.items(): frac[arr==code]=f
        converted += float((weight*frac)[valid].sum())
        crop=np.isin(arr,[10,11,12,20]); urban=arr==190; mosaic=np.isin(arr,[30,40])
        shares['cropland'] += float(weight[crop & valid].sum()); shares['urban'] += float(weight[urban & valid].sum()); shares['mosaic'] += float(weight[mosaic & valid].sum())
    if total<=0: return None
    converted_share=converted/total
    return {'score':100*(1-converted_share),'convertedShare':converted_share,'classAreaShares':{k:v/total for k,v in shares.items()}}

def main():
    p=argparse.ArgumentParser(description='Build fixed Earth Health Habitat backbone from annual C3S/ESA land-cover classification NetCDF files.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--input-dir',type=Path,required=True); p.add_argument('--glob',default='*.nc*'); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); p.add_argument('--chunk-rows',type=int,default=64); a=p.parse_args(); repo=a.repo.resolve()
    series=[]
    for path in sorted(a.input_dir.glob(a.glob)):
        ds=xr.open_dataset(path,decode_times=True)
        try:
            vn=variable(ds); da=ds[vn]; lat=next((d for d in da.dims if d.lower() in {'lat','latitude'}),None)
            if lat is None: raise ValueError(f'No latitude dimension in {path}: {da.dims}')
            if 'time' in da.dims and da.sizes['time']>1:
                items=[(year_from(path,ds,i),da.isel(time=i)) for i in range(da.sizes['time'])]
            else:
                if 'time' in da.dims: da=da.isel(time=0)
                items=[(year_from(path,ds),da)]
            for y,annual in items:
                if y is None or y<a.start_year: continue
                r=score_da(annual,lat,a.chunk_rows)
                if r: series.append({'year':y,'score':r['score'],'coverage':1.0,'raw':r['convertedShare']*100,'unit':'% ice-free non-water land directly converted','classAreaShares':r['classAreaShares']})
        finally: ds.close()
    # dedupe later file wins
    series=list({r['year']:r for r in series}.values())
    payload=family_payload(family_id='habitat',label='Habitat',series=series,component={'id':'direct_conversion_retention','label':'Land not directly converted','weight':1.0,'source':'Copernicus C3S annual land cover'},source={'id':'c3s-land-cover','label':'Copernicus Climate Change Service annual land-cover classification','inputDirectory':str(a.input_dir)},method={'backbone':'100 minus area-weighted direct anthropogenic conversion share of ice-free non-water land','conversionFractions':{str(k):v for k,v in CONVERSION.items()},'excludedClasses':sorted(EXCLUDE),'scoreDirection':'100 = no mapped direct conversion in scored classes','diagnosticPolicy':'Hansen forest loss, plantations/tree crops, mining, waste and Dynamic World remain detailed diagnostics only'},context={'caveat':'Backbone is intentionally conservative/coarse and does not claim the complement is intact habitat.'})
    out=write_family(repo,'habitat',payload); print(f'Habitat backbone years={len(payload["series"])} latest={payload["latestBackboneYear"]} -> {out}')
if __name__=='__main__': main()
