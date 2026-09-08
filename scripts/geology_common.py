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

from analysis_geometry import analysis_geometry_cache_identity, analysis_geometry_metadata, apply_land_geometry_overrides

WGS84="EPSG:4326"; AREA_CRS="EPSG:6933"
_ANALYSIS_GEOMETRY_CACHE: dict[tuple[str, str, str], object] = {}
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
    # Runtime marine TopoJSON is fragmented: `id` is a feature fragment
    # identifier while `regionId` is the canonical inventory identifier.
    for k in ('regionId','region_id','id'):
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
    out=[]; land_geometries={}
    for kind,path,index in specs:
        if not path.exists():continue
        names=_index_names(index); g=read_vector(path); grouped=defaultdict(list)
        for idx,row in g.iterrows():
            rid=_rid(row,idx)
            if rid and row.geometry is not None and not row.geometry.is_empty:grouped[rid].append(row.geometry)
        for rid,geoms in grouped.items():
            geometry=unary_union(geoms)
            if kind=='land': land_geometries[rid]=geometry
            out.append({'regionId':rid,'regionName':names.get(rid,rid),'kind':kind,'geometry':geometry})
    if land_geometries:
        corrected=apply_land_geometry_overrides(land_geometries)
        for record in out:
            if record['kind']=='land': record['geometry']=corrected[record['regionId']]
    result=gpd.GeoDataFrame(out,geometry='geometry',crs=WGS84)
    if globalize_open_ocean and 'marine' in kinds:
        # The app's open-ocean selection is a non-species whole-ocean scope:
        # residual ocean plus every mapped marine/coastal ecoregion.  Build it
        # from the world minus canonical land coverage, without adding this
        # synthetic ID to the species inventory.
        land=result[result.kind=='land']
        lakes=result[result.kind=='lakes']
        exclusion_geoms=[g for g in [*land.geometry.tolist(),*lakes.geometry.tolist()] if g is not None and not g.is_empty]
        missing_exclusion_kinds=tuple(k for k in ('land','lakes') if k not in kinds)
        if missing_exclusion_kinds:
            exclusions=load_regions(repo,kinds=missing_exclusion_kinds,globalize_open_ocean=False)
            exclusion_geoms.extend(g for g in exclusions.geometry if g is not None and not g.is_empty)
        ocean=box(-180.0,-89.999,180.0,89.999).difference(unary_union(exclusion_geoms)) if exclusion_geoms else None
        if ocean is not None and not ocean.is_empty:
            mask=result.regionId.map(_is_open_ocean_id)
            if mask.any():
                for idx in result.index[mask]:
                    result.at[idx,'regionId']='open_ocean'
                    result.at[idx,'geometry']=ocean
                    result.at[idx,'regionName']='Global Ocean'
                result=result.drop_duplicates(subset=['regionId'],keep='first').reset_index(drop=True)
            else:
                result=gpd.GeoDataFrame(
                    pd.concat([result,gpd.GeoDataFrame([{'regionId':'open_ocean','regionName':'Global Ocean','kind':'marine','geometry':ocean}],geometry='geometry',crs=WGS84)],ignore_index=True),
                    geometry='geometry',crs=WGS84,
                )
    cache_root=str(Path(repo).resolve())
    for record in result.itertuples():
        _ANALYSIS_GEOMETRY_CACHE[(cache_root,str(record.kind),str(record.regionId))]=record.geometry
    return result

def fragments_root(repo:Path,provider_id:str)->Path:
    p=repo/'.cache/motherworld/geology/providers'/provider_id;p.mkdir(parents=True,exist_ok=True);return p

def write_fragment(repo:Path,provider_id:str,region:dict,sections:dict,source:dict):
    region_id=str(region['regionId']); kind=str(region.get('kind') or 'land')
    geometry=region.get('geometry')
    if geometry is None:
        geometry=_ANALYSIS_GEOMETRY_CACHE.get((str(Path(repo).resolve()),kind,region_id))
    # If the producer did not provide a geometry and did not load one in this
    # process, leave the fragment unverified.  Looking up today's geometry here
    # would incorrectly relabel sections computed from an older footprint.
    payload={'schemaVersion':1,'providerId':provider_id,'regionId':region_id,'regionName':region.get('regionName',region_id),'kind':region.get('kind'),'generatedAt':utc_now_iso(),'sections':sections,'source':source}
    if geometry is not None:
        payload['analysisGeometry']=analysis_geometry_metadata(region_id,geometry)
        payload['analysisGeometryCacheIdentity']=analysis_geometry_cache_identity(region_id,geometry)
    p=fragments_root(repo,provider_id)/f"{region_id}.json";p.write_text(json.dumps(json_safe(payload),indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8');return p

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

def json_safe(value):
    """Convert pandas/NumPy values and non-finite numbers to JSON-safe values."""
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value

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
