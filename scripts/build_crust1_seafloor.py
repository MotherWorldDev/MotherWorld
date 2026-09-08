#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
import xarray as xr
from geology_common import source_obj, write_fragment
from seafloor_common import Grid, detect_coord_names, load_marine_analysis_regions, regional_values, stats, _native_step

def main():
    ap=argparse.ArgumentParser(description='Aggregate CRUST1.0 crystalline crust thickness beneath MotherWorld marine regions.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--target-step-deg',type=float,default=1.0);ap.add_argument('--region',action='append');a=ap.parse_args();repo=a.repo.resolve();ds=xr.open_dataset(a.input,mask_and_scale=True)
    try:
        names=['upper_crust_thickness','middle_crust_thickness','lower_crust_thickness']
        missing=[x for x in names if x not in ds]
        if missing:raise KeyError(f'CRUST1.0 file missing {missing}; use an EMC NetCDF containing layer thickness variables')
        da=sum((ds[x] for x in names)).squeeze(drop=True);latn,lonn=detect_coord_names(da);da=da.sortby(lonn).sortby(latn,ascending=False).load();lat=np.asarray(da[latn].values,float);lon=np.asarray(da[lonn].values,float);vals=np.asarray(da.values,float);grid=Grid(vals,lat,lon,'crystalline_crust_thickness','km',max(_native_step(lat),_native_step(lon)),max(_native_step(lat),_native_step(lon)))
    finally:ds.close()
    regs=load_marine_analysis_regions(repo);regs=regs if not a.region else regs[regs.regionId.isin(a.region)];source=source_obj('crust1_0','CRUST1.0 global crustal model via EarthScope EMC',version='CRUST1.0 / EMC r0.1',license='EarthScope/model attribution terms')
    n=0
    for r in regs.itertuples():
        v,w,c=regional_values(grid,r.geometry,valid=lambda x:(x>0)&(x<100));st=stats(v,w)
        if not st:continue
        sec={'seafloor':{'crust':{'meanCrystallineThicknessKm':st['mean'],'medianCrystallineThicknessKm':st['median'],'p10CrystallineThicknessKm':st['p10'],'p90CrystallineThicknessKm':st['p90'],'coveragePct':c,'source':source}}}
        write_fragment(repo,'crust1_0',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},sec,source);n+=1
    print(f'CRUST1.0 seafloor stats built for {n} marine regions')
if __name__=='__main__':main()
