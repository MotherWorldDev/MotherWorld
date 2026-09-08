#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

def run(args):
    print('+',' '.join(map(str,args))); subprocess.run(list(map(str,args)),check=True)

def main():
    p=argparse.ArgumentParser(description='Run whatever MotherWorld land-pollution providers you have staged.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--osm',type=Path,action='append'); p.add_argument('--climate-trace',type=Path); p.add_argument('--mining',type=Path); p.add_argument('--wapha-tsf',type=Path); p.add_argument('--wapha-tdf',type=Path); p.add_argument('--epa-superfund',type=Path); p.add_argument('--epa-tri',type=Path); p.add_argument('--eea-waste',type=Path)
    a=p.parse_args(); d=Path(__file__).resolve().parent; py=sys.executable
    if a.osm: run([py,d/'build_osm_waste_sites.py','--repo',a.repo,*sum((['--input',x] for x in a.osm),[])])
    if a.climate_trace: run([py,d/'build_climatetrace_landfills.py','--repo',a.repo,'--input',a.climate_trace])
    if a.mining: run([py,d/'build_global_mining_pressure.py','--repo',a.repo,'--input',a.mining])
    if a.wapha_tsf or a.wapha_tdf:
        cmd=[py,d/'build_wapha_tailings.py','--repo',a.repo];
        if a.wapha_tsf: cmd+=['--tsf',a.wapha_tsf]
        if a.wapha_tdf: cmd+=['--tdf',a.wapha_tdf]
        run(cmd)
    if a.epa_superfund or a.epa_tri:
        cmd=[py,d/'build_epa_land_pollution.py','--repo',a.repo]
        if a.epa_superfund:cmd+=['--superfund',a.epa_superfund]
        if a.epa_tri:cmd+=['--tri',a.epa_tri]
        run(cmd)
    if a.eea_waste: run([py,d/'build_eea_industrial_waste.py','--repo',a.repo,'--input',a.eea_waste])
    run([py,d/'recompute_land_pollution_percentiles.py'])
if __name__=='__main__':main()
