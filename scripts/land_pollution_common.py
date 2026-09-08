from __future__ import annotations

import json, math, re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload, *, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':')) if compact else json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ('' if compact else '\n'), encoding='utf-8')


def norm(value):
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def find_column(columns: Iterable[str], aliases: Iterable[str]):
    lookup = {norm(c): c for c in columns}
    for alias in aliases:
        key = norm(alias)
        if key in lookup:
            return lookup[key]
    return None


def to_float(value):
    if value is None: return None
    try:
        out=float(str(value).strip().replace(',',''))
        return out if math.isfinite(out) else None
    except Exception:
        return None


def year_from(value):
    m=re.search(r'(19|20)\d{2}', str(value or ''))
    return int(m.group(0)) if m else None


def load_land_regions(repo: Path):
    out={}
    shp=repo/'Ecoregions2017/Ecoregions2017.shp'
    if shp.exists():
        gdf=gpd.read_file(shp)
        gdf=gdf.set_crs('EPSG:4326') if gdf.crs is None else gdf.to_crs('EPSG:4326')
        eco=find_column(gdf.columns,('ECO_ID','ECOID','eco_id'))
        if eco:
            for _,row in gdf.iterrows():
                try: rid=f"eco_{int(float(row[eco]))}"
                except Exception: continue
                if row.geometry is not None and not row.geometry.is_empty: out[rid]=row.geometry
    if not out:
        files=sorted((repo/'frontend/public/data/lod1_realms').glob('*.topojson')) or [repo/'frontend/public/data/lod0/ecoregions_lod0.topojson']
        for p in files:
            if not p.exists(): continue
            gdf=gpd.read_file(p)
            gdf=gdf.set_crs('EPSG:4326') if gdf.crs is None else gdf.to_crs('EPSG:4326')
            for idx,row in gdf.iterrows():
                rid=str(idx) if str(idx).startswith('eco_') else str(row.get('id') or '')
                if rid.startswith('eco_') and row.geometry is not None and not row.geometry.is_empty: out[rid]=row.geometry
    return out


def land_metadata(repo: Path):
    return read_json(repo/'frontend/public/data/regions.index.json',{}).get('regions',{})


def region_gdf(repo: Path):
    geoms=load_land_regions(repo); meta=land_metadata(repo)
    rows=[]
    for rid,geom in geoms.items():
        rows.append({'regionId':rid,'name':meta.get(rid,{}).get('name') or rid,'areaKm2':meta.get(rid,{}).get('areaKm2'),'geometry':geom})
    return gpd.GeoDataFrame(rows,geometry='geometry',crs='EPSG:4326')


def assign_points(df: pd.DataFrame, repo: Path, lat='lat', lon='lon'):
    regions=region_gdf(repo)
    pts=gpd.GeoDataFrame(df.copy(),geometry=gpd.points_from_xy(df[lon],df[lat]),crs='EPSG:4326')
    joined=gpd.sjoin(pts,regions[['regionId','geometry']],how='left',predicate='within')
    return joined


def percentile_rank_map(values: dict[str,float]):
    valid={k:float(v) for k,v in values.items() if v is not None and math.isfinite(float(v))}
    arr=np.asarray(list(valid.values()),dtype=np.float64)
    if not len(arr): return {}
    return {k: float(np.mean(arr <= v)*100.0) for k,v in valid.items()}


def merge_region_provider(repo: Path, updates: dict[str,dict], source: dict):
    meta=land_metadata(repo)
    index_path=repo/'frontend/public/data/land-pollution/land-pollution.index.json'
    index=read_json(index_path,{'schemaVersion':1,'generatedAt':None,'regions':{},'sources':{}})
    for rid,provider_payload in updates.items():
        path=repo/'frontend/public/data/land-pollution/land'/f'{rid}.land-pollution.json'
        existing=read_json(path,{
            'schemaVersion':1,'regionId':rid,'regionName':meta.get(rid,{}).get('name') or rid,
            'regionKind':'land','generatedAt':utc_now_iso(),'providers':{},
            'interpretation':'Known/mapped land-pollution pressure. Data coverage differs by provider; absence of mapped sites does not imply clean land.'
        })
        existing['generatedAt']=utc_now_iso()
        existing.setdefault('providers',{})[source['id']]=provider_payload
        write_json(path,existing,compact=True)
        index.setdefault('regions',{})[rid]={'url':f'land/{rid}.land-pollution.json','providerIds':sorted(existing['providers']),'generatedAt':existing['generatedAt']}
    index.setdefault('sources',{})[source['id']]=source
    index['generatedAt']=utc_now_iso(); write_json(index_path,index)


def recompute_peer_percentiles(repo: Path):
    root=repo/'frontend/public/data/land-pollution/land'
    files=list(root.glob('*.land-pollution.json'))
    payloads={p.name.split('.land-pollution.json')[0]: read_json(p,{}) for p in files}
    # provider metric key => rid:value
    maps=defaultdict(dict)
    for rid,p in payloads.items():
        for provider_id,provider in p.get('providers',{}).items():
            for key,value in (provider.get('metrics') or {}).items():
                low=key.lower()
                if 'year' in low or 'reportcount' in low:
                    continue
                if isinstance(value,(int,float)) and math.isfinite(float(value)):
                    maps[(provider_id,key)][rid]=float(value)
    ranks={k:percentile_rank_map(v) for k,v in maps.items()}
    for rid,p in payloads.items():
        for provider_id,provider in p.get('providers',{}).items():
            peer={}
            for key in (provider.get('metrics') or {}):
                val=ranks.get((provider_id,key),{}).get(rid)
                if val is not None: peer[key]=val
            provider['peerPercentiles']=peer
        write_json(root/f'{rid}.land-pollution.json',p,compact=True)


def convert_mass_to_kg(value, unit):
    v=to_float(value)
    if v is None: return None
    u=norm(unit)
    if u in {'kg','kilogram','kilograms'}: return v
    if u in {'g','gram','grams'}: return v/1000
    if u in {'lb','lbs','pound','pounds'}: return v*0.45359237
    if u in {'ton','tons','shortton','shorttons'}: return v*907.18474
    if u in {'tonne','tonnes','metricton','metrictons','t'}: return v*1000
    return None
