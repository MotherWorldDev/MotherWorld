#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
from geology_common import source_obj, write_fragment
from seafloor_common import category_percentages, load_grid, load_marine_analysis_regions, regional_values, stats

BINS=[("<0.1 km",0,0.1),("0.1–0.5 km",0.1,0.5),("0.5–1 km",0.5,1),("1–3 km",1,3),("3–6 km",3,6),("6+ km",6,float('inf'))]

def main():
    ap=argparse.ArgumentParser(description='Aggregate GlobSed total sediment thickness by MotherWorld marine region.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--variable');ap.add_argument('--units',choices=('m','km'),default='m');ap.add_argument('--target-step-deg',type=float,default=0.10);ap.add_argument('--region',action='append')
    a=ap.parse_args();repo=a.repo.resolve();grid=load_grid(a.input,a.variable,candidates=('z','sediment_thickness','thickness'),target_step_deg=a.target_step_deg);regs=load_marine_analysis_regions(repo);regs=regs if not a.region else regs[regs.regionId.isin(a.region)]
    factor=0.001 if a.units=='m' else 1.0
    source=source_obj('globsed','GlobSed total sediment thickness, version 3 archive',version='2019 model / 2025 PANGAEA archive',doi='10.1594/PANGAEA.982339',license='CC BY 4.0')
    n=0
    for r in regs.itertuples():
        vals,w,cov=regional_values(grid,r.geometry,transform_value=lambda x:x*factor,valid=lambda x:(x>=0)&(x<50));st=stats(vals,w)
        if not st:continue
        sec={'seafloor':{'sediment':{'meanThicknessKm':st['mean'],'medianThicknessKm':st['median'],'p10ThicknessKm':st['p10'],'p90ThicknessKm':st['p90'],'maxThicknessKm':st['max'],'coveragePct':cov,'thicknessBands':category_percentages(vals,w,BINS),'statisticsResolutionDeg':grid.sampled_step_deg,'source':source}}}
        write_fragment(repo,'globsed',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},sec,source);n+=1
    print(f'GlobSed built for {n} marine regions')
if __name__=='__main__':main()
