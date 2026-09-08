#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
def run(cmd): print('+',' '.join(map(str,cmd))); subprocess.run([str(x) for x in cmd],check=True)
def main():
 p=argparse.ArgumentParser(description='Rebuild rich regional indices and present-day diagnostics without overwriting Earth Health backbones.'); p.add_argument('--repo',type=Path,default=Path.cwd()); p.add_argument('--merge-biodiversity',action='store_true'); p.add_argument('--exclude-biodiversity-provider',action='append',default=[]); p.add_argument('--skip-earth-health',action='store_true'); a=p.parse_args(); d=Path(__file__).resolve().parent; py=sys.executable
 if a.merge_biodiversity:
  cmd=[py,d/'merge_biodiversity_metrics.py','--repo',a.repo]
  for x in a.exclude_biodiversity_provider: cmd += ['--exclude',x]
  run(cmd)
 run([py,d/'merge_habitat_supplements.py','--repo',a.repo]); run([py,d/'build_regional_family_indices.py','--repo',a.repo]); run([py,d/'build_global_family_indices.py','--repo',a.repo])
 if not a.skip_earth_health: run([py,d/'build_earth_health_index.py','--repo',a.repo])
if __name__=='__main__': main()
