from __future__ import annotations
import json, math, re, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

WGS84="EPSG:4326"; AREA_CRS="EPSG:6933"
GLIM_CLASSES={
 "su":"Unconsolidated sediments","ss":"Siliciclastic sedimentary rocks","py":"Pyroclastics","sm":"Mixed sedimentary rocks","sc":"Carbonate sedimentary rocks","ev":"Evaporites","va":"Acid volcanic rocks","vi":"Intermediate volcanic rocks","vb":"Basic volcanic rocks","pa":"Acid plutonic rocks","pi":"Intermediate plutonic rocks","pb":"Basic plutonic rocks","mt":"Metamorphics","wb":"Water bodies","ig":"Ice and glaciers","nd":"No data"
}
ERA_BOUNDS=[("Cenozoic",0,66.0),("Mesozoic",66.0,252.17),("Paleozoic",252.17,538.8),("Precambrian",538.8,4600.0)]

def utc_now_iso(): return datetime.now(timezone.utc).isoformat()
def norm(s): return re.sub(r"[^a-z0-9]+","",str(s or "").lower())
def find_col(df, candidates, required=False):
    lookup={norm(c):c for c in df.columns}
    for c in candidates:
        if norm(c) in lookup:return lookup[norm(c)]
    if required: raise KeyError(f"Could not find any of {candidates}; columns={list(df.columns)}")
    return None

def _read_spreadsheetml(path:Path)->pd.DataFrame:
    root=ET.parse(path).getroot(); rows=[]
    for row in root.findall('.//{*}Row'):
        vals=[]
        for cell in row.findall('{*}Cell'):
            data=cell.find('{*}Data'); vals.append(data.text if data is not None else None)
        if any(v not in (None,"") for v in vals): rows.append(vals)
    if not rows:return pd.DataFrame()
    head=[str(x or f"col_{i}").strip() for i,x in enumerate(rows[0])]; width=len(head)
    body=[(r+[None]*width)[:width] for r in rows[1:]]
    return pd.DataFrame(body,columns=head)

def read_table(path:Path)->pd.DataFrame:
    suffix=path.suffix.lower()
    if suffix in {'.csv','.txt'}:
        return pd.read_csv(path, sep=None, engine='python')
    if suffix in {'.tsv','.tab'}: return pd.read_csv(path,sep='\t',low_memory=False)
    if suffix in {'.xlsx','.xlsm'}: return pd.read_excel(path)
    if suffix in {'.xml','.xls'}:
        try:return _read_spreadsheetml(path)
        except Exception:
            return pd.read_excel(path)
    raise ValueError(f"Unsupported table format: {path}")

def read_vector(path:Path)->gpd.GeoDataFrame:
    g=gpd.read_file(path)
    if g.crs is None:g=g.set_crs(WGS84)
    return g.to_crs(WGS84)

def _rid(row,idx=None):
    for k in ('id','regionId','region_id'):
        v=row.get(k)
        if v is not None and str(v).strip(): return str(v)
    e=row.get('ECO_ID') or row.get('eco_id')
    if e is not None and str(e).strip():return f"eco_{str(e).split('.')[0]}"
    if idx is not None and str(idx).startswith(('eco_','marine_','lake_')):return str(idx)
    return None

def _index_names(path:Path)->dict:
    if not path.exists():return {}
    try:d=json.loads(path.read_text()); regs=d.get('regions',{}); return {str(k):v.get('name',str(k)) for k,v in regs.items()}
    except Exception:return {}

def _is_open_ocean_id(region_id:str)->bool:
    rid=str(region_id or '')
    return rid in {'marine_ocean_0','open_ocean'} or rid.endswith('ocean_0')

def load_regions(repo:Path, kinds:Iterable[str]|None=None, globalize_open_ocean:bool=False)->gpd.GeoDataFrame:
    kinds=set(kinds or ('land','marine','lakes')); specs=[]
    if 'land' in kinds: specs.append(('land',repo/'frontend/public/data/lod0/ecoregions_lod0.topojson',repo/'frontend/public/data/regions.index.json'))
    if 'marine' in kinds: specs.append(('marine',repo/'frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson',repo/'frontend/public/data/marine.index.json'))
    if 'lakes' in kinds: specs.append(('lakes',repo/'frontend/public/data/lakes/lod0/lakes_lod0.topojson',repo/'frontend/public/data/lakes.index.json'))
    out=[]
    for kind,path,index in specs:
        if not path.exists():continue
        names=_index_names(index); g=read_vector(path); grouped=defaultdict(list)
        for idx,row in g.iterrows():
            rid=_rid(row,idx)
            if rid and row.geometry is not None and not row.geometry.is_empty:grouped[rid].append(row.geometry)
        for rid,geoms in grouped.items():out.append({'regionId':rid,'regionName':names.get(rid,rid),'kind':kind,'geometry':unary_union(geoms)})
    result=gpd.GeoDataFrame(out,geometry='geometry',crs=WGS84)
    if globalize_open_ocean and not result.empty and 'marine' in kinds:
        marine=result[result.kind=='marine']
        if not marine.empty:
            ocean=unary_union([g for g in marine.geometry if g is not None and not g.is_empty])
            mask=result.regionId.map(_is_open_ocean_id)
            if mask.any() and ocean is not None and not ocean.is_empty:
                for idx in result.index[mask]:
                    result.at[idx,'geometry']=ocean
                    result.at[idx,'regionName']='Global Ocean'
    return result

def fragments_root(repo:Path,provider_id:str)->Path:
    p=repo/'.cache/motherworld/geology/providers'/provider_id;p.mkdir(parents=True,exist_ok=True);return p

def write_fragment(repo:Path,provider_id:str,region:dict,sections:dict,source:dict):
    payload={'schemaVersion':1,'providerId':provider_id,'regionId':region['regionId'],'regionName':region.get('regionName',region['regionId']),'kind':region.get('kind'),'generatedAt':utc_now_iso(),'sections':sections,'source':source}
    p=fragments_root(repo,provider_id)/f"{region['regionId']}.json";p.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');return p

def region_records(regions:gpd.GeoDataFrame):
    return [dict(regionId=r.regionId,regionName=r.regionName,kind=r.kind,geometry=r.geometry) for r in regions.itertuples()]

def assign_points(points:gpd.GeoDataFrame,regions:gpd.GeoDataFrame):
    if points.crs is None:points=points.set_crs(WGS84)
    points=points.to_crs(WGS84); regs=regions[['regionId','regionName','kind','geometry']].copy()
    joined=gpd.sjoin(points,regs,how='inner',predicate='intersects')
    return joined

def region_projected(regions:gpd.GeoDataFrame):return regions.to_crs(AREA_CRS)
def source_obj(provider_id,label,**kw):
    d={'providerId':provider_id,'label':label};d.update({k:v for k,v in kw.items() if v is not None});return d

def split_multi(v):
    if v is None or (isinstance(v,float) and math.isnan(v)):return []
    return [x.strip() for x in re.split(r'[;,|]+',str(v)) if x.strip()]
def as_float(v):
    try:
        n=float(v);return n if math.isfinite(n) else None
    except Exception:return None

def era_for_age(age_ma):
    a=as_float(age_ma)
    if a is None:return None
    for name,lo,hi in ERA_BOUNDS:
        if lo<=a<hi:return name
    return 'Precambrian' if a>=538.8 else None

def percentile(arr,q):
    a=np.asarray([x for x in arr if x is not None and math.isfinite(float(x))],dtype=float)
    return float(np.percentile(a,q)) if a.size else None
