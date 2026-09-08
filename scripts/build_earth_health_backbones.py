#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

def run(cmd): print('+',' '.join(map(str,cmd))); subprocess.run([str(x) for x in cmd],check=True)
def main():
    p=argparse.ArgumentParser(description='Build the fixed-composition MotherWorld Earth Health backbone family files.')
    p.add_argument('--repo',type=Path,default=Path.cwd()); p.add_argument('--earthengine-project'); p.add_argument('--authenticate',action='store_true'); p.add_argument('--rli-csv',type=Path); p.add_argument('--c3s-dir',type=Path); p.add_argument('--ohc',type=Path); p.add_argument('--ozone-csv',type=Path); p.add_argument('--skip-network',action='store_true'); p.add_argument('--skip-ee',action='store_true'); a=p.parse_args(); d=Path(__file__).resolve().parent; py=sys.executable
    if not a.skip_network:
        cmd=[py,d/'build_biodiversity_backbone.py','--repo',a.repo];
        if a.rli_csv: cmd += ['--csv',a.rli_csv]
        run(cmd); run([py,d/'build_climate_forcing_index.py','--repo',a.repo]); run([py,d/'build_cryosphere_index.py','--repo',a.repo])
        cmd=[py,d/'build_ocean_backbone.py','--repo',a.repo];
        if a.ohc: cmd += ['--input',a.ohc]
        run(cmd)
        cmd=[py,d/'build_ozone_backbone.py','--repo',a.repo];
        if a.ozone_csv: cmd += ['--csv',a.ozone_csv]
        run(cmd)
    if a.c3s_dir: run([py,d/'build_habitat_backbone_c3s.py','--repo',a.repo,'--input-dir',a.c3s_dir])
    else: print('Habitat backbone not built: provide --c3s-dir with annual C3S/ESA land-cover NetCDF files.')
    if not a.skip_ee and a.earthengine_project:
        auth=['--authenticate'] if a.authenticate else []
        run([py,d/'build_vegetation_backbone_ee.py','--repo',a.repo,'--project',a.earthengine_project,*auth]); run([py,d/'build_pollution_backbone_ee.py','--repo',a.repo,'--project',a.earthengine_project,*auth]); run([py,d/'build_freshwater_backbone_ee.py','--repo',a.repo,'--project',a.earthengine_project,*auth])
    elif not a.skip_ee: print('Earth Engine backbones not built: provide --earthengine-project.')
    print('Backbone provider pass complete. Run build_earth_health_index.py; it will only publish years shared by every enabled family.')
if __name__=='__main__': main()
