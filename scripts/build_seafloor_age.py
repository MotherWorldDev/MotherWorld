#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
from geology_common import source_obj, write_fragment
from seafloor_common import category_percentages, load_grid, load_marine_analysis_regions, regional_values, stats

AGE_BINS=[("0–20 Ma",0,20),("20–50 Ma",20,50),("50–100 Ma",50,100),("100–150 Ma",100,150),("150–200 Ma",150,200),("200+ Ma",200,float('inf'))]

def main():
    ap=argparse.ArgumentParser(description='Aggregate EarthByte/Seton oceanic-crust age and optional spreading-rate grids.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--age-grid',type=Path,required=True);ap.add_argument('--age-variable');ap.add_argument('--rate-grid',type=Path);ap.add_argument('--rate-variable');ap.add_argument('--target-step-deg',type=float,default=0.10);ap.add_argument('--region',action='append')
    a=ap.parse_args();repo=a.repo.resolve();age=load_grid(a.age_grid,a.age_variable,candidates=('age','z','seafloor_age'),target_step_deg=a.target_step_deg);rate=load_grid(a.rate_grid,a.rate_variable,candidates=('rate','spreading_rate','z'),target_step_deg=a.target_step_deg) if a.rate_grid else None
    regs=load_marine_analysis_regions(repo);regs=regs if not a.region else regs[regs.regionId.isin(a.region)]
    source=source_obj('earthbyte_seafloor_age','Seton et al. present-day oceanic crustal age & spreading parameters',version='2020 / Zenodo v1.1',doi='10.1029/2020GC009214',license='CC BY 4.0')
    n=0
    for r in regs.itertuples():
        vals,w,cov=regional_values(age,r.geometry,valid=lambda x:(x>=0)&(x<=300));st=stats(vals,w)
        if not st:continue
        payload={'meanAgeMa':st['mean'],'medianAgeMa':st['median'],'p10AgeMa':st['p10'],'p90AgeMa':st['p90'],'oldestAgeMa':st['max'],'coveragePct':cov,'ageBands':category_percentages(vals,w,AGE_BINS),'statisticsResolutionDeg':age.sampled_step_deg,'source':source}
        if rate is not None:
            rv,rw,rcov=regional_values(rate,r.geometry,valid=lambda x:(x>=0)&(x<1000));rst=stats(rv,rw)
            if rst:payload['spreadingRate']={'meanMmYr':rst['mean'],'medianMmYr':rst['median'],'p10MmYr':rst['p10'],'p90MmYr':rst['p90'],'coveragePct':rcov}
        sec={'seafloor':{'age':payload}}
        write_fragment(repo,'earthbyte_seafloor_age',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},sec,source);n+=1
    print(f'Seafloor age built for {n} marine regions')
if __name__=='__main__':main()
