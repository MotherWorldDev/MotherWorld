from __future__ import annotations
import csv, io, math, re
from pathlib import Path
from typing import Iterable
import requests
from env_common import clamp, finite, utc_now_iso, write_json

HISTORY_START_YEAR = 1993

def rows_by_year(rows: Iterable[dict], start_year: int | None = None):
    out={}
    for row in rows:
        try: y=int(float(row.get('year')))
        except Exception: continue
        if start_year is not None and y < start_year: continue
        score=finite(row.get('score')); coverage=finite(row.get('coverage'))
        if score is None: continue
        r=dict(row); r['year']=y; r['score']=round(clamp(score),4); r['coverage']=round(clamp(coverage if coverage is not None else 1.0,0,1),6)
        out[y]=r
    return [out[y] for y in sorted(out)]

def family_payload(*, family_id, label, series, component, source, method, scope='global_only', context=None):
    series=rows_by_year(series)
    latest=series[-1] if series else None
    comp=dict(component)
    if latest:
        comp['score']=latest['score']
        for key in ('raw','value','unit','year'):
            if key in latest: comp[key if key!='value' else 'raw']=latest[key]
    return {
        'schemaVersion':2,
        'familyId':family_id,
        'label':label,
        'scope':scope,
        'earthHealthRole':'backbone',
        'score': latest['score'] if latest else None,
        'coverage': latest['coverage'] if latest else 0.0,
        'latestBackboneYear': latest['year'] if latest else None,
        'components':[comp],
        'series':series,
        'context':context or {},
        'generatedAt':utc_now_iso(),
        'method':method,
        'sources':[source],
    }

def write_family(repo: Path, family_id: str, payload: dict):
    out=repo/'frontend/public/data/indices/families'/f'{family_id}.index.json'
    write_json(out,payload); return out

def write_diagnostic(repo: Path, family_id: str, payload: dict):
    p=dict(payload); p['earthHealthRole']='diagnostic_only'; p['scoredInEarthHealth']=False
    out=repo/'frontend/public/data/indices/diagnostics'/f'{family_id}.diagnostics.json'
    write_json(out,p); return out

def download_text(url: str, timeout: int = 90) -> str:
    r=requests.get(url,timeout=timeout); r.raise_for_status(); return r.text

def parse_year_value_text(text: str):
    out=[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith(('#',';')): continue
        vals=re.findall(r'[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?',line)
        if len(vals)<2: continue
        try: y=int(float(vals[0])); v=float(vals[1])
        except Exception: continue
        if 1900 <= y <= 2200 and math.isfinite(v): out.append({'year':y,'value':v})
    return out
