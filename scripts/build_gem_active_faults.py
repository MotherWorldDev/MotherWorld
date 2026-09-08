#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from geology_common import *
def main():
    ap=argparse.ArgumentParser(description='Aggregate GEM Global Active Faults by MotherWorld region.');ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();faults=read_vector(a.input).to_crs(AREA_CRS);regs=load_regions(repo,globalize_open_ocean=True).to_crs(AREA_CRS);sidx=faults.sindex
    name=find_col(faults,['name','fault_name','faultname']);kin=find_col(faults,['slip_type','kinematics','sense','fault_type']);slip=find_col(faults,['slip_rate','sliprate','net_slip_rate','pref_rate','slip_rate_mm_yr'])
    source=source_obj('gem_active_faults','GEM Global Active Faults Database',license='CC BY-SA 4.0')
    for rr in regs.itertuples():
        items=[];length=0;rates=[]
        for i in sidx.query(rr.geometry,predicate='intersects'):
            row=faults.iloc[int(i)]; inter=rr.geometry.intersection(row.geometry)
            if inter.is_empty:continue
            km=float(inter.length)/1000;length+=km;rate=as_float(row.get(slip)) if slip else None
            if rate is not None:rates.append(rate)
            items.append({'name':row.get(name) if name else None,'kinematics':row.get(kin) if kin else None,'slipRateMmYr':rate,'intersectionLengthKm':km})
        if not items:continue
        items.sort(key=lambda x:x['intersectionLengthKm'],reverse=True)
        sec={'tectonics':{'faults':{'count':len(items),'intersectionLengthKm':length,'medianSlipRateMmYr':float(np.median(rates)) if rates else None,'items':items[:30],'source':source}}}
        write_fragment(repo,'gem_active_faults',{'regionId':rr.regionId,'regionName':rr.regionName,'kind':rr.kind},sec,source)
    print('GEM fault aggregation complete')
if __name__=='__main__':main()
