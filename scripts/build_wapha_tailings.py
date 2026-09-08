#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
import geopandas as gpd
import pandas as pd
from land_pollution_common import attach_analysis_geometry_provenance, merge_region_provider, recompute_peer_percentiles, region_gdf


def points_from_any(path: Path):
    g=gpd.read_file(path); g=g.set_crs('EPSG:4326') if g.crs is None else g.to_crs('EPSG:4326'); g=g.copy(); g['geometry']=g.geometry.representative_point(); return g


def assign(g,regions): return gpd.sjoin(g,regions[['regionId','geometry']],how='inner',predicate='within')


def main():
    ap=argparse.ArgumentParser(description='Aggregate WAPHA global tailings storage facilities and tailings dam failures by ecoregion.')
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); ap.add_argument('--tsf',type=Path); ap.add_argument('--tdf',type=Path)
    args=ap.parse_args(); repo=args.repo.resolve(); regions=region_gdf(repo); counts={}
    if args.tsf:
        j=assign(points_from_any(args.tsf),regions)
        for rid,n in j.groupby('regionId').size().items(): counts.setdefault(rid,{})['tailingsStorageFacilityCount']=int(n)
    if args.tdf:
        j=assign(points_from_any(args.tdf),regions)
        for rid,n in j.groupby('regionId').size().items(): counts.setdefault(rid,{})['tailingsDamFailureCount']=int(n)
    updates={rid:{'label':'WAPHA mine tailings','coverage':'Global compilation of known metal-mine tailings storage facilities/failures from public sources; not a complete inventory of all mining waste.','metrics':m} for rid,m in counts.items()}
    attach_analysis_geometry_provenance(repo, updates)
    merge_region_provider(repo,updates,{'id':'wapha-tailings','label':'WAPHA global metal mines / tailings database','license':'Dataset terms/citations as published by Dryad; source compilations have mixed provenance','url':'https://doi.org/10.5061/dryad.j3tx95xmg','caveat':'Known tailings counts are incomplete; absence is not evidence that no tailings or mine waste exists.'})
    recompute_peer_percentiles(repo); print(f'Updated {len(updates)} regions from WAPHA tailings data.')
if __name__=='__main__':main()
