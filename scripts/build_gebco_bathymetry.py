#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from geology_common import source_obj, write_fragment
from seafloor_common import category_percentages, load_grid, load_marine_analysis_regions, regional_values, stats

DEPTH_ZONES=[("Shelf / epipelagic (0–200 m)",0,200),("Upper slope (200–1,000 m)",200,1000),("Bathyal (1,000–3,000 m)",1000,3000),("Abyssal (3,000–6,000 m)",3000,6000),("Hadal (>6,000 m)",6000,float('inf'))]

def main():
    ap=argparse.ArgumentParser(description='Aggregate GEBCO bathymetry into MotherWorld marine regions and whole-ocean open_ocean aggregate.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--variable');ap.add_argument('--target-step-deg',type=float,default=0.10);ap.add_argument('--region',action='append')
    a=ap.parse_args();repo=a.repo.resolve();grid=load_grid(a.input,a.variable,candidates=('elevation','z','Band1'),target_step_deg=a.target_step_deg);regs=load_marine_analysis_regions(repo)
    if a.region:regs=regs[regs.regionId.isin(a.region)]
    source=source_obj('gebco_2026','GEBCO_2026 Grid',version='2026',doi='10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa',license='Public domain; attribution requested')
    n=0
    for r in regs.itertuples():
        values,weights,cov=regional_values(grid,r.geometry,transform_value=lambda x:-x,valid=lambda x:x>=0)
        st=stats(values,weights)
        if not st:continue
        sec={'seafloor':{'bathymetry':{'meanDepthM':st['mean'],'medianDepthM':st['median'],'p10DepthM':st['p10'],'p90DepthM':st['p90'],'shallowestM':st['min'],'deepestM':st['max'],'coveragePct':cov,'depthZones':category_percentages(values,weights,DEPTH_ZONES),'sourceResolutionDeg':grid.native_step_deg,'statisticsResolutionDeg':grid.sampled_step_deg,'source':source}}}
        write_fragment(repo,'gebco_2026',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},sec,source);n+=1
    print(f'GEBCO bathymetry built for {n} marine regions')
if __name__=='__main__':main()
