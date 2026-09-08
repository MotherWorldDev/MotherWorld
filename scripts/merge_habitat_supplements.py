#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
from env_common import load_metadata, read_json, write_json, finite, utc_now_iso

def main():
 p=argparse.ArgumentParser(description='Merge plantation/mining/waste footprint metrics into habitat payloads without double-counting them.'); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); repo=a.repo.resolve(); meta=load_metadata(repo); count=0
 for rid,(kind,m) in meta.items():
  if kind!='land': continue
  hp=repo/'frontend/public/data/habitat/land'/f'{rid}.habitat.json'; h=read_json(hp,{})
  if not h: continue
  supp=h.setdefault('supplemental',{})
  lp=read_json(repo/'frontend/public/data/land-pollution/land'/f'{rid}.land-pollution.json',{})
  for pid,prov in lp.get('providers',{}).items():
   metrics=prov.get('metrics') or {}
   if pid=='global-mining-polygons-v2':
    for k,v in metrics.items():
     if 'pct' in k.lower() or 'areakm2' in k.lower(): supp[f'mining.{k}']=v
   if pid=='osm-waste':
    for k in ('mappedLandfillPctOfRegion','mappedLandfillAreaKm2'):
     if k in metrics: supp[f'waste.{k}']=metrics[k]
  h['generatedAt']=utc_now_iso(); write_json(hp,h,compact=True); count+=1
 print(f'Updated {count} habitat payloads with supplemental land-pressure metrics.')
if __name__=='__main__': main()
