#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
def run(cmd): print('+',' '.join(map(str,cmd))); subprocess.run([str(x) for x in cmd],check=True)
def main():
 p=argparse.ArgumentParser(description='Build direct global Earth Health backbones and optional present-day diagnostics.'); p.add_argument('--repo',type=Path,default=Path.cwd()); p.add_argument('--greenland-csv',type=Path); p.add_argument('--antarctica-csv',type=Path); p.add_argument('--glacier-csv',type=Path); p.add_argument('--ohc',type=Path); p.add_argument('--ph',type=Path); p.add_argument('--sea-level',type=Path); p.add_argument('--ozone-csv',type=Path); a=p.parse_args(); d=Path(__file__).resolve().parent; py=sys.executable
 run([py,d/'build_climate_forcing_index.py','--repo',a.repo]); cry=[py,d/'build_cryosphere_index.py','--repo',a.repo]
 for key,val in [('greenland-csv',a.greenland_csv),('antarctica-csv',a.antarctica_csv),('glacier-csv',a.glacier_csv)]:
  if val: cry += [f'--{key}',val]
 run(cry); oce=[py,d/'build_ocean_backbone.py','--repo',a.repo];
 if a.ohc: oce += ['--input',a.ohc]
 run(oce); oz=[py,d/'build_ozone_backbone.py','--repo',a.repo];
 if a.ozone_csv: oz += ['--csv',a.ozone_csv]
 run(oz)
 if any((a.ohc,a.ph,a.sea_level)):
  diag=[py,d/'build_global_ocean_index.py','--repo',a.repo]
  for k,v in [('ohc',a.ohc),('ph',a.ph),('sea-level',a.sea_level)]:
   if v: diag += [f'--{k}',v]
  run(diag)
if __name__=='__main__': main()
