#!/usr/bin/env python
from __future__ import annotations
import argparse
from pathlib import Path
from env_common import read_json, write_json, load_metadata, weighted_mean, finite, utc_now_iso
FAMILIES=[('biodiversity','Biodiversity',{'land','marine','lakes'}),('habitat','Habitat',{'land'}),('vegetation','Vegetation condition',{'land'}),('pollution','Pollution',{'land','marine','lakes'})]
def component_summary(rows,total_area):
    by={}
    for f,area in rows:
        for c in f.get('components',[]) or []:
            cid=c.get('id');
            if not cid: continue
            b=by.setdefault(cid,{'id':cid,'label':c.get('label') or cid,'scores':[],'raws':[],'weights':[],'represented':0.0,'unit':c.get('unit'),'sources':set()})
            sc=finite(c.get('score')); raw=finite(c.get('raw')); wt=finite(c.get('weight'))
            if sc is not None: b['scores'].append((sc,area)); b['represented']+=area
            if raw is not None and (b['unit']==c.get('unit') or b['unit'] is None): b['raws'].append((raw,area))
            if wt is not None: b['weights'].append((wt,area))
            if c.get('source'): b['sources'].add(str(c['source']))
    out=[]
    for b in by.values(): out.append({'id':b['id'],'label':b['label'],'score':round(weighted_mean(b['scores']),4) if b['scores'] else None,'coverage':round(b['represented']/total_area,6) if total_area else 0,'raw':round(weighted_mean(b['raws']),6) if b['raws'] else None,'unit':b['unit'],'weight':round(weighted_mean(b['weights']),6) if b['weights'] else None,'source':' / '.join(sorted(b['sources'])) if b['sources'] else None})
    return sorted(out,key=lambda x:(-(x.get('weight') or 0),x['label']))
def main():
    p=argparse.ArgumentParser(description='Build present-day global diagnostics from rich regional family scores. These files NEVER drive Earth Health.'); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); repo=a.repo.resolve(); meta=load_metadata(repo); payloads={}
    for rid,(kind,m) in meta.items():
        q=read_json(repo/'frontend/public/data/indices/regions'/f'{rid}.json',{}); payloads[rid]=q if q else None
    for fid,label,kinds in FAMILIES:
        scores=[]; covs=[]; rows=[]; total=0; eligible=0
        for rid,q in payloads.items():
            if not q: continue
            kind=q.get('regionKind'); area=float(q.get('areaKm2') or 0)
            if kind not in kinds or area<=0: continue
            total+=area; eligible+=1; f=next((x for x in q.get('families',[]) if x.get('id')==fid),None); cv=float((f or {}).get('coverage') or 0); covs.append((cv,area))
            if f: rows.append((f,area));
            if f and f.get('score') is not None: scores.append((float(f['score']),area*cv))
        out={'schemaVersion':1,'familyId':fid,'label':label,'earthHealthRole':'diagnostic_only','scoredInEarthHealth':False,'score':round(weighted_mean(scores),4) if scores else None,'coverage':round(weighted_mean(covs) or 0,6),'components':component_summary(rows,total),'context':{'eligibleRegionCount':eligible,'eligibleAreaKm2':total},'generatedAt':utc_now_iso(),'method':{'spatialAggregation':'area-weighted present-day regional diagnostic summary','earthHealth':'zero weight; fixed historical backbone is stored separately under indices/families'}}
        path=repo/'frontend/public/data/indices/diagnostics'/f'{fid}.diagnostics.json'; write_json(path,out); print(fid,'diagnostic',out['score'],path)
if __name__=='__main__': main()
