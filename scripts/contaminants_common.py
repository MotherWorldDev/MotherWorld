from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from analysis_geometry import analysis_geometry_cache_identity, analysis_geometry_metadata, apply_land_geometry_overrides


_ANALYSIS_GEOMETRY_CACHE: dict[str, dict[str, tuple[str, object]]] = {}


def _repo_cache_key(repo: Path) -> str:
    return str(Path(repo).resolve())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':')) if compact else json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + ('' if compact else '\n'), encoding='utf-8')


def norm_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').strip().lower())


def find_column(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    lookup = {norm_name(c): c for c in columns}
    for alias in aliases:
        key = norm_name(alias)
        if key in lookup:
            return lookup[key]
    return None


def year_from(value) -> int | None:
    if value is None:
        return None
    text = str(value)
    m = re.search(r'(19|20)\d{2}', text)
    return int(m.group(0)) if m else None


def to_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(',', '')
    if text.lower() in {'', 'nan', 'na', 'null', 'none'}:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def canonical_unit(unit: str) -> str:
    u = str(unit or '').strip().replace('μ', 'µ').replace('ug', 'µg').replace('UG', 'µg')
    u = re.sub(r'\s+', '', u)
    replacements = {
        'ng/l': 'ng/L', 'ngL-1': 'ng/L', 'ngl': 'ng/L',
        'µg/l': 'µg/L', 'mcg/l': 'µg/L', 'ugl': 'µg/L',
        'mg/l': 'mg/L', 'mgl': 'mg/L',
        'pg/l': 'pg/L', 'pgl': 'pg/L',
        'cfu/100ml': 'CFU/100mL', 'cfu100ml': 'CFU/100mL',
        'mpn/100ml': 'MPN/100mL', 'mpn100ml': 'MPN/100mL',
    }
    return replacements.get(u.lower(), str(unit or '').strip())


def convert_mass_per_l(value: float, unit: str, target: str) -> tuple[float, str] | None:
    unit = canonical_unit(unit)
    factors_to_ng = {'pg/L': 0.001, 'ng/L': 1.0, 'µg/L': 1000.0, 'mg/L': 1_000_000.0}
    if unit not in factors_to_ng:
        return None
    ng = float(value) * factors_to_ng[unit]
    if target == 'ng/L':
        return ng, 'ng/L'
    if target == 'µg/L':
        return ng / 1000.0, 'µg/L'
    if target == 'mg/L':
        return ng / 1_000_000.0, 'mg/L'
    return None


PFAS_RE = re.compile(r'\b(pfas|pfos|pfoa|pfna|pfhx|pfbs|genx|hfpo|perfluoro|polyfluoro)', re.I)
PATHOGEN_RE = re.compile(r'(e\.?\s*coli|escherichia|fecal coliform|faecal coliform|enterococc|fecal streptococc|faecal streptococc)', re.I)
MERCURY_RE = re.compile(r'\b(mercury|methylmercury|hg)\b', re.I)
PESTICIDE_RE = re.compile(r'(pesticid|herbicid|insecticid|fungicid|atrazine|glyphosate|ddt|dieldrin|aldrin|endrin|endosulfan|chlorpyrifos|diuron|simazine|terbuthylazine|isoproturon|linuron|trifluralin|alachlor|mecoprop|bentazone|toxaphene)', re.I)
PETROLEUM_RE = re.compile(r'(petroleum|hydrocarbon|oil|btex|benzene|toluene|ethylbenzene|xylene|naphthalene)', re.I)
TOXIC_RE = re.compile(r'(lead|arsenic|cadmium|chromium|nickel|copper|zinc|selenium|cyanide|pcb|pbde|pah|polyaromatic|benzo\[|phenol|vinyl chloride|chloroform|carbon tetrachloride|uranium|antimony|thallium|beryllium|silver|tin)', re.I)


def classify_parameter(meta: dict) -> str | None:
    text = ' '.join(str(meta.get(k) or '') for k in (
        'name', 'long_name', 'group1', 'group2', 'group3', 'description', 'code'
    ))
    if PFAS_RE.search(text): return 'pfas'
    if MERCURY_RE.search(text): return 'mercury'
    if PATHOGEN_RE.search(text): return 'pathogens'
    if PESTICIDE_RE.search(text): return 'pesticides'
    if PETROLEUM_RE.search(text): return 'petroleum_hydrocarbons'
    if TOXIC_RE.search(text): return 'other_toxics'
    return None


CATEGORY_LABELS = {
    'pfas': 'PFAS',
    'mercury': 'Mercury',
    'pathogens': 'Pathogen indicators',
    'pesticides': 'Pesticides',
    'petroleum_hydrocarbons': 'Petroleum / hydrocarbons',
    'other_toxics': 'Other toxic chemicals',
    'microplastics': 'Microplastics',
    'oil_incidents': 'Oil-spill incidents',
    'chemical_incidents': 'Chemical-spill incidents',
}


def target_unit_for(category: str, unit: str) -> str:
    if category == 'pfas': return 'ng/L'
    if category in {'mercury','pesticides','petroleum_hydrocarbons','other_toxics'}: return 'µg/L'
    return canonical_unit(unit)


@dataclass
class SeriesAccumulator:
    name: str
    category: str
    unit: str
    max_values: int = 10000
    sample_count: int = 0
    detected_count: int = 0
    station_ids: set[str] = field(default_factory=set)
    first_year: int | None = None
    last_year: int | None = None
    values: list[float] = field(default_factory=list)
    yearly: dict[int, dict] = field(default_factory=lambda: defaultdict(lambda: {'samples':0,'detected':0,'sumDetected':0.0}))
    _rng: random.Random = field(default_factory=lambda: random.Random(48721), repr=False)

    def add(self, *, value: float | None, detected: bool, station_id: str | None, year: int | None) -> None:
        self.sample_count += 1
        if station_id: self.station_ids.add(str(station_id))
        if year is not None:
            self.first_year = year if self.first_year is None else min(self.first_year, year)
            self.last_year = year if self.last_year is None else max(self.last_year, year)
            self.yearly[year]['samples'] += 1
        if detected and value is not None and math.isfinite(value):
            self.detected_count += 1
            if year is not None:
                self.yearly[year]['detected'] += 1
                self.yearly[year]['sumDetected'] += float(value)
            if len(self.values) < self.max_values:
                self.values.append(float(value))
            else:
                j = self._rng.randint(0, self.detected_count - 1)
                if j < self.max_values:
                    self.values[j] = float(value)

    def payload(self) -> dict:
        arr = np.asarray(self.values, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        annual = []
        for year in sorted(self.yearly):
            row = self.yearly[year]
            annual.append({
                'year': year,
                'sampleCount': row['samples'],
                'detectedCount': row['detected'],
                'detectionRatePct': (100.0*row['detected']/row['samples']) if row['samples'] else None,
                'meanDetected': (row['sumDetected']/row['detected']) if row['detected'] else None,
            })
        return {
            'name': self.name,
            'unit': self.unit,
            'sampleCount': self.sample_count,
            'detectedCount': self.detected_count,
            'detectionRatePct': 100.0*self.detected_count/self.sample_count if self.sample_count else None,
            'stationCount': len(self.station_ids),
            'firstYear': self.first_year,
            'lastYear': self.last_year,
            'medianDetected': float(np.median(arr)) if arr.size else None,
            'p90Detected': float(np.percentile(arr,90)) if arr.size else None,
            'maxDetected': float(np.max(arr)) if arr.size else None,
            'quantileValueCount': int(arr.size),
            'quantilesReservoirSampled': self.detected_count > self.max_values,
            'annual': annual,
        }


def load_region_geometries(repo: Path) -> dict[str, tuple[str, object]]:
    out: dict[str, tuple[str, object]] = {}
    # Land
    shp = repo / 'Ecoregions2017/Ecoregions2017.shp'
    if shp.exists():
        gdf = gpd.read_file(shp).to_crs('EPSG:4326')
        eco_col = find_column(gdf.columns, ('ECO_ID','eco_id','ECOID'))
        if eco_col:
            for _, row in gdf.iterrows():
                try: rid = f"eco_{int(row[eco_col])}"
                except Exception: continue
                if row.geometry is not None and not row.geometry.is_empty: out[rid]=('land',row.geometry)
    if not any(k.startswith('eco_') for k in out):
        for path in sorted((repo/'frontend/public/data/lod1_realms').glob('*.topojson')) or [repo/'frontend/public/data/lod0/ecoregions_lod0.topojson']:
            if not path.exists(): continue
            gdf=gpd.read_file(path)
            if gdf.crs is None: gdf=gdf.set_crs('EPSG:4326')
            else: gdf=gdf.to_crs('EPSG:4326')
            for idx,row in gdf.iterrows():
                rid=str(idx) if str(idx).startswith('eco_') else str(row.get('id') or '')
                if rid.startswith('eco_') and row.geometry is not None and not row.geometry.is_empty: out[rid]=('land',row.geometry)
    for kind, path, prefix in (
        ('lakes',repo/'frontend/public/data/lakes/lod0/lakes_lod0.topojson','lake_'),
        ('marine',repo/'frontend/public/data/marine/lod0/marine_ecoregions_lod0.topojson','marine_'),
    ):
        if not path.exists(): continue
        gdf=gpd.read_file(path)
        if gdf.crs is None: gdf=gdf.set_crs('EPSG:4326')
        else: gdf=gdf.to_crs('EPSG:4326')
        for idx,row in gdf.iterrows():
            rid=str(idx) if str(idx).startswith(prefix) else str(row.get('id') or '')
            if rid.startswith(prefix) and row.geometry is not None and not row.geometry.is_empty: out[rid]=(kind,row.geometry)
    land_geometries = {rid: geom for rid, (kind, geom) in out.items() if kind == 'land'}
    corrected_land = apply_land_geometry_overrides(land_geometries)
    for rid, geom in corrected_land.items():
        out[rid] = ('land', geom)
    _ANALYSIS_GEOMETRY_CACHE[_repo_cache_key(repo)] = dict(out)
    return out


def point_region_map(points: pd.DataFrame, repo: Path, *, kinds: set[str]) -> dict[int, list[str]]:
    geoms = load_region_geometries(repo)
    region_rows=[]
    for rid,(kind,geom) in geoms.items():
        if kind in kinds: region_rows.append({'regionId':rid,'kind':kind,'geometry':geom})
    if not region_rows: return {}
    regions=gpd.GeoDataFrame(region_rows,geometry='geometry',crs='EPSG:4326')
    pts=gpd.GeoDataFrame(points.copy(),geometry=gpd.points_from_xy(points['lon'],points['lat']),crs='EPSG:4326')
    joined=gpd.sjoin(pts,regions,how='left',predicate='within')
    out=defaultdict(list)
    for idx,row in joined.iterrows():
        rid=row.get('regionId')
        if isinstance(rid,str) and rid: out[int(idx)].append(rid)
    return dict(out)


def region_metadata(repo: Path) -> dict[str, dict]:
    out={}
    for fn in ('regions.index.json','lakes.index.json','marine.index.json'):
        data=read_json(repo/'frontend/public/data'/fn,{})
        out.update(data.get('regions',{}))
    out.setdefault('open_ocean',{'name':'Open Ocean'})
    return out


def attach_analysis_geometry_provenance(repo: Path, updates: dict[str, dict]):
    """Attach producer-side identity for corrected land regions only."""
    geometries=_ANALYSIS_GEOMETRY_CACHE.get(_repo_cache_key(repo))
    if geometries is None:
        return updates
    for rid,payload in updates.items():
        raw_kind=payload.get('_kind','land')
        entry=geometries.get(rid)
        if raw_kind != 'land' or entry is None or entry[0] != 'land':
            continue
        geometry=entry[1]
        # Preserve any producer-supplied claim so a stale/mismatched claim is
        # rejected by the merge instead of being relabelled with today's geometry.
        if '_analysisGeometry' in payload or '_analysisGeometryCacheIdentity' in payload:
            continue
        metadata=analysis_geometry_metadata(rid, geometry)
        if metadata.get('overrideApplied'):
            payload['_analysisGeometry']=metadata
            payload['_analysisGeometryCacheIdentity']=analysis_geometry_cache_identity(rid, geometry)
    return updates


def merge_region_categories(repo: Path, region_updates: dict[str, dict], source_entry: dict | None = None) -> None:
    meta=region_metadata(repo)
    geometries=load_region_geometries(repo)
    index_path=repo/'frontend/public/data/contaminants/contaminants.index.json'
    index=read_json(index_path,{'schemaVersion':1,'generatedAt':None,'regions':{},'sources':{}})
    for rid, raw_update in region_updates.items():
        update=dict(raw_update)
        kind=update.pop('_kind','land')
        provided_metadata=update.pop('_analysisGeometry',None)
        provided_identity=update.pop('_analysisGeometryCacheIdentity',None)
        rel_kind='lakes' if kind=='lakes' else 'marine' if kind=='marine' else 'land'
        path=repo/'frontend/public/data/contaminants'/rel_kind/f'{rid}.contaminants.json'
        existing=read_json(path,{}) if path.exists() else None
        geometry_entry=geometries.get(rid)
        geometry=geometry_entry[1] if geometry_entry and geometry_entry[0] == kind else None
        expected_metadata=analysis_geometry_metadata(rid, geometry) if kind == 'land' else None
        guarded=bool(expected_metadata and expected_metadata.get('geometryOverride'))
        corrected=bool(guarded and expected_metadata.get('overrideApplied'))
        expected_identity=(analysis_geometry_cache_identity(rid, geometry) if corrected else None)
        if guarded:
            if geometry is None:
                index.setdefault('regions',{}).pop(rid,None)
                if existing is not None:
                    existing['categories']={}
                    existing['sources']={}
                    existing['analysisGeometryStatus']='withheld'
                    existing['analysisGeometryReason']='expected_analysis_geometry_unavailable'
                    write_json(path,existing,compact=True)
                continue
            valid=(corrected
                   and provided_identity == expected_identity
                   and isinstance(provided_metadata, dict)
                   and provided_metadata.get('regionId') == str(rid)
                   and provided_metadata.get('overrideApplied') is True
                   and provided_metadata.get('geometryFingerprint') == expected_metadata.get('geometryFingerprint'))
            if not valid:
                existing_is_current=bool(
                    existing
                    and corrected
                    and existing.get('analysisGeometryCacheIdentity') == expected_identity
                    and (existing.get('analysisGeometry') or {}).get('geometryFingerprint') == expected_metadata.get('geometryFingerprint')
                )
                if existing_is_current:
                    # A late stale provider must not erase newer verified data.
                    continue
                index.setdefault('regions',{}).pop(rid,None)
                if existing is not None:
                    existing['categories']={}
                    existing['sources']={}
                    existing['analysisGeometryStatus']='withheld'
                    existing['analysisGeometryReason']='Producer geometry identity/provenance is missing or does not match the maintained corrected footprint.'
                    write_json(path,existing,compact=True)
                continue
        existing=existing or read_json(path,{
            'schemaVersion':1,'regionId':rid,'regionName':meta.get(rid,{}).get('name') or rid,
            'regionKind':kind,'generatedAt':utc_now_iso(),'categories':{},'sources':{},
            'coverageWarning':'Observation coverage is incomplete and uneven. No data does not mean no contamination.'
        })
        if expected_identity and existing.get('analysisGeometryCacheIdentity') != expected_identity:
            existing['categories']={}
            existing['sources']={}
        existing['generatedAt']=utc_now_iso()
        if corrected:
            existing['analysisGeometry']=expected_metadata
            existing['analysisGeometryCacheIdentity']=expected_identity
            existing.pop('analysisGeometryStatus',None)
            existing.pop('analysisGeometryReason',None)
        existing.setdefault('categories',{}).update(update.get('categories',{}))
        existing.setdefault('sources',{}).update(update.get('sources',{}))
        write_json(path,existing,compact=True)
        rel=f'{rel_kind}/{rid}.contaminants.json'
        index.setdefault('regions',{})[rid]={'url':rel,'kind':kind,'categoryKeys':sorted(existing['categories']),'generatedAt':existing['generatedAt']}
    if source_entry:
        index.setdefault('sources',{})[source_entry['id']]=source_entry
    index['generatedAt']=utc_now_iso()
    write_json(index_path,index)
