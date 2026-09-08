#!/usr/bin/env python
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

def run(cmd): print('+',' '.join(map(str,cmd))); subprocess.run(list(map(str,cmd)),check=True)
def main():
 p=argparse.ArgumentParser(description='Run the global Earth-Engine-backed providers that need no staged third-party downloads.'); p.add_argument('--repo',type=Path,default=Path.cwd()); p.add_argument('--project',required=True); p.add_argument('--skip-vegetation',action='store_true'); p.add_argument('--skip-habitat',action='store_true'); p.add_argument('--skip-air',action='store_true'); a=p.parse_args(); d=Path(__file__).resolve().parent; py=sys.executable
 if not a.skip_vegetation:run([py,d/'build_vegetation_coverage_ee.py','--repo',a.repo,'--project',a.project])
 if not a.skip_habitat:run([py,d/'build_habitat_metrics_ee.py','--repo',a.repo,'--project',a.project])
 if not a.skip_air:run([py,d/'build_pollution_metrics_ee.py','--repo',a.repo,'--project',a.project])
if __name__=='__main__':main()
