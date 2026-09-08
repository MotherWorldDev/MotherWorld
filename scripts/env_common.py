from __future__ import annotations

import json, math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd


def utc_now_iso(): return datetime.now(timezone.utc).isoformat()

def read_json(path: Path, default=None):
    if not path.exists(): return {} if default is None else default
    return json.loads(path.read_text(encoding='utf-8'))

def write_json(path: Path, payload, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text=json.dumps(payload, ensure_ascii=False, separators=(',',':')) if compact else json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ('' if compact else '\n'), encoding='utf-8')

def finite(v):
    try: x=float(v)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None

def clamp(v,lo=0.0,hi=100.0): return max(lo,min(hi,float(v)))

def geometric_mean(items: Iterable[tuple[float,float]]):
    pairs=[(finite(s),finite(w)) for s,w in items]
    pairs=[(s,w) for s,w in pairs if s is not None and w is not None and w>0]
    if not pairs: return None
    if any(s<=0 for s,_ in pairs): return 0.0
    sw=sum(w for _,w in pairs)
    return math.exp(sum(w*math.log(clamp(s,1e-9,100)) for s,w in pairs)/sw)

def weighted_mean(items: Iterable[tuple[float,float]]):
    pairs=[(finite(v),finite(w)) for v,w in items]
    pairs=[(v,w) for v,w in pairs if v is not None and w is not None and w>0]
    if not pairs: return None
    sw=sum(w for _,w in pairs); return sum(v*w for v,w in pairs)/sw

def percentile_rank(values: dict[str,float], rid: str):
    x=finite(values.get(rid))
    vals=sorted(finite(v) for v in values.values() if finite(v) is not None)
    if x is None or not vals: return None
    below=sum(v<x for v in vals); equal=sum(v==x for v in vals)
    return 100*(below+0.5*equal)/len(vals)

def load_metadata(repo: Path):
    out={}
    for kind,fn in [('land','regions.index.json'),('marine','marine.index.json'),('lakes','lakes.index.json')]:
        data=read_json(repo/'frontend/public/data'/fn,{})
        for rid,meta in data.get('regions',{}).items(): out[rid]=(kind,meta)
    return out

def _read_topo(path: Path, kind: str):
    out={}
    if not path.exists(): return out
    g=gpd.read_file(path); g=g.set_crs('EPSG:4326') if g.crs is None else g.to_crs('EPSG:4326')
    for idx,row in g.iterrows():
        rid=None
        for k in ('id','region_id','regionId'):
            if k in row and row[k] is not None:
                s=str(row[k]);
                if s: rid=s; break
        if rid is None and str(idx).startswith(('eco_','marine_','lake_')): rid=str(idx)
        if rid and row.geometry is not None and not row.geometry.is_empty: out[rid]=row.geometry
    return out

def load_geometries(repo: Path):
    out={}
    shp=repo/'Ecoregions2017/Ecoregions2017.shp'
    if shp.exists():
        g=gpd.read_file(shp); g=g.set_crs('EPSG:4326') if g.crs is None else g.to_crs('EPSG:4326')
        eco=next((c for c in g.columns if str(c).lower() in {'eco_id','ecoid'}),None)
        if eco:
            for _,row in g.iterrows():
                try: rid=f"eco_{int(float(row[eco]))}"
                except Exception: continue
                if row.geometry is not None and not row.geometry.is_empty: out[rid]=row.geometry
    if not any(k.startswith('eco_') for k in out):
        files=sorted((repo/'frontend/public/data/lod1_realms').glob('*.topojson')) or [repo/'frontend/public/data/lod0/ecoregions_lod0.topojson']
        for p in files: out.update(_read_topo(p,'land'))
    out.update(_read_topo(repo/'frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson','marine'))
    out.update(_read_topo(repo/'frontend/public/data/lakes/lod0/lakes_lod0.topojson','lakes'))
    return out

def region_path(repo:Path, family:str, kind:str, rid:str):
    patterns={
      'vegetation': repo/'frontend/public/data/vegetation/land'/f'{rid}.vegetation.json',
      'biodiversity': repo/'frontend/public/data/biodiversity'/kind/f'{rid}.biodiversity.json',
      'air_pollution': repo/'frontend/public/data/pollution'/kind/f'{rid}.pollution.json',
      'water_pollution': repo/'frontend/public/data/water-pollution'/('marine' if kind=='marine' else 'lakes')/f'{rid}.water-pollution.json',
      'contaminants': repo/'frontend/public/data/contaminants'/kind/f'{rid}.contaminants.json',
      'land_pollution': repo/'frontend/public/data/land-pollution/land'/f'{rid}.land-pollution.json',
      'habitat': repo/'frontend/public/data/habitat/land'/f'{rid}.habitat.json',
    }
    return patterns[family]
