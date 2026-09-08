#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

p=argparse.ArgumentParser(description='Run available MotherWorld contaminant source builders.')
p.add_argument('--repo',type=Path,default=Path.cwd())
p.add_argument('--gemstat',type=Path)
p.add_argument('--microplastics',type=Path)
p.add_argument('--ucmr5',type=Path)
p.add_argument('--zcta',type=Path)
p.add_argument('--skip-noaa-incidents',action='store_true')
a=p.parse_args(); scripts=Path(__file__).resolve().parent

def run(name,*extra):
    cmd=[sys.executable,str(scripts/name),'--repo',str(a.repo),*map(str,extra)]
    print('+',' '.join(cmd)); subprocess.run(cmd,check=True)

if a.gemstat: run('build_gemstat_contaminants.py','--archive',a.gemstat)
if a.microplastics: run('build_noaa_microplastics.py','--input',a.microplastics)
if a.ucmr5:
    extra=['--ucmr-zip',a.ucmr5]
    if a.zcta: extra += ['--zcta',a.zcta]
    run('build_ucmr5_pfas.py',*extra)
if not a.skip_noaa_incidents: run('build_noaa_incidents.py','--download')
