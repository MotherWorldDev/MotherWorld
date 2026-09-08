#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
from index_engine import clamp, finite_number, geometric_mean, linear_trend_per_decade, read_json, score_level, status_label, utc_now_iso, write_json

def parse_args():
    p=argparse.ArgumentParser(description='Build fixed-composition Earth Health history from long-running family backbones.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--config',type=Path); p.add_argument('--output',type=Path); return p.parse_args()
def fam_file(repo,spec): return repo/'frontend/public/data/indices'/(spec.get('file') or f'families/{spec["id"]}.index.json')
def diag_file(repo,fid): return repo/'frontend/public/data/indices/diagnostics'/f'{fid}.diagnostics.json'
def by_year(payload):
    out={}
    for row in payload.get('series',[]) or []:
        try: y=int(row.get('year')); score=finite_number(row.get('score')); cov=finite_number(row.get('coverage'))
        except Exception: continue
        if score is not None: out[y]={**row,'year':y,'score':clamp(score,0,100),'coverage':clamp(cov if cov is not None else 1,0,1)}
    return out

def point(year,specs,maps,mincov,thresholds):
    fam=[]; inputs=[]; eff=0.0; total=sum(float(s.get('weight',0)) for s in specs)
    for s in specs:
        row=maps[s['id']][year]; sc=float(row['score']); cv=float(row['coverage']); wt=float(s.get('weight',0)); inputs.append((sc,wt)); eff += wt*cv
        fam.append({'id':s['id'],'label':s.get('label') or s['id'],'score':round(sc,4),'level':score_level(sc),'coverage':round(cv,6),'configuredWeight':wt,'included':cv>=mincov,'backboneRaw':row.get('raw'),'backboneUnit':row.get('unit')})
    score=geometric_mean(inputs); effective=eff/total if total else 0
    return {'year':year,'score':score,'level':score_level(score),'status':status_label(score,thresholds),'coverage':effective,'families':fam}

def effective_coverage(year,specs,maps):
    total=sum(float(s.get('weight',0)) for s in specs)
    if total <= 0: return 0.0
    return sum(float(s.get('weight',0))*clamp(float(maps[s['id']][year].get('coverage',0)),0,1) for s in specs)/total

def eligible_common_years(specs,maps,start,mincov,min_effective_coverage,require_all=True):
    eligible_sets=[]
    for s in specs:
        years={y for y,r in maps[s['id']].items() if y>=start and float(r.get('coverage',0))>=mincov}
        if not years: raise SystemExit(f'{s["id"]} has no usable backbone years >= {start}')
        eligible_sets.append(years)
    common=sorted(set.intersection(*eligible_sets)) if eligible_sets else []
    if require_all and start not in common:
        availability={s['id']:(min(maps[s['id']]) if maps[s['id']] else None,max(maps[s['id']]) if maps[s['id']] else None) for s in specs}
        raise SystemExit(f'Requested Earth Health start year {start} is not available in every enabled backbone. Availability: {availability}')
    effective_common=[y for y in common if effective_coverage(y,specs,maps)>=min_effective_coverage]
    if require_all and start not in effective_common:
        coverage=effective_coverage(start,specs,maps) if start in common else 0.0
        raise SystemExit(f'Requested Earth Health start year {start} has effective data coverage {coverage:.1%}, below configured minimum {min_effective_coverage:.1%}.')
    continuous=[]
    if effective_common:
        y=max(start,effective_common[0]); end=max(effective_common)
        while y<=end and y in effective_common: continuous.append(y); y+=1
    return continuous

def main():
    a=parse_args(); repo=a.repo.resolve(); cfg=read_json(a.config or repo/'frontend/public/data/indices/earth-health.config.json',{}); out=a.output or repo/'frontend/public/data/indices/earth-health.json'
    specs=[s for s in cfg.get('families',[]) if s.get('enabled',True)]; start=int(cfg.get('historyStartYear',1993)); mincov=float(cfg.get('minimumFamilyCoverage',.45)); min_effective_coverage=float(cfg.get('minimumEffectiveDataCoverage',.55)); thresholds=cfg.get('statusThresholds')
    if not 0 <= min_effective_coverage <= 1: raise SystemExit(f'minimumEffectiveDataCoverage must be between 0 and 1, got {min_effective_coverage}')
    payloads={}; maps={}; missing=[]
    for s in specs:
        p=fam_file(repo,s)
        if not p.exists(): missing.append(f'{s["id"]}: missing {p}') ; continue
        q=read_json(p,{}); payloads[s['id']]=q; maps[s['id']]=by_year(q)
    if missing: raise SystemExit('Cannot build fixed-composition Earth Health:\n  '+'\n  '.join(missing))
    # Enforce the family floor and weighted effective-coverage floor before selecting
    # a truly continuous annual slider; stop before the first invalid year or gap.
    common=eligible_common_years(specs,maps,start,mincov,min_effective_coverage,cfg.get('requireAllFamiliesForHistory', True))
    if not common: raise SystemExit(f'No continuous common Earth Health years starting at/after {start}.')
    points=[point(y,specs,maps,mincov,thresholds) for y in common]; current=points[-1]; latest=current['year']
    # Present-day diagnostics are deliberately separate and never alter a point score.
    current_families=[]
    for fr in current['families']:
        fid=fr['id']; bp=payloads[fid]; diag=read_json(diag_file(repo,fid),{})
        fr={**fr,'backbone':{'label':(bp.get('components') or [{}])[0].get('label') or fid,'components':bp.get('components',[]),'method':bp.get('method',{}),'sources':bp.get('sources',[])},'diagnostics':diag if diag else None}
        current_families.append(fr)
    series=[{'year':p['year'],'score':round(p['score'],4),'level':p['level'],'status':p['status'],'coverage':round(p['coverage'],6),'families':p['families']} for p in points]
    trend=linear_trend_per_decade(series)
    result={'schemaVersion':2,'generatedAt':utc_now_iso(),'name':cfg.get('name','Earth Health'),'year':latest,'historyStartYear':common[0],'historyEndYear':latest,'requestedHistoryStartYear':start,'fixedComposition':True,'score':round(current['score'],4),'level':current['level'],'status':current['status'],'coverage':{'familyWeightCoverage':1.0,'effectiveDataCoverage':round(current['coverage'],6),'minimumFamilyCoverage':mincov,'minimumEffectiveDataCoverage':min_effective_coverage,'sufficient':True},'families':current_families,'series':series,'trend':{'pointsPerDecade':round(trend,4) if trend is not None else None,'seriesStartYear':common[0],'seriesEndYear':latest},'method':{'crossFamilyAggregation':'weighted_geometric_mean','historicalPolicy':'same enabled backbone families, weights and normalization rules in every displayed year','sliderPolicy':'intersection of annual backbone years with family and weighted effective-coverage floors; continuous from requested start until first invalid year','currentPolicy':'headline Earth Health equals latest common historical year, never a mixed-year latest score','diagnosticPolicy':'younger/richer datasets are present-day diagnostics only and have zero Earth Health weight','scoreDirection':'100 = best condition / lowest pressure','weightsAre':'configurable governance weights, not scientific constants'}}
    write_json(out,result); print(f'Earth Health {latest}: {result["score"]:.2f}/100; comparable timeline {common[0]}–{latest}; {len(common)} annual snapshots -> {out}')
if __name__=='__main__': main()
