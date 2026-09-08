#!/usr/bin/env python
from __future__ import annotations
import argparse, math
from pathlib import Path
from env_common import read_json, write_json, load_metadata, region_path, finite, clamp, geometric_mean, percentile_rank, utc_now_iso

def component(cid,label,score,weight,raw=None,unit=None,source=None,note=None):
 return {'id':cid,'label':label,'score':round(score,4) if score is not None else None,'weight':weight,'raw':raw,'unit':unit,'source':source,'note':note}
def family(fid,label,components,context=None,min_cov=.35):
 valid=[c for c in components if finite(c.get('score')) is not None]; total=sum(c['weight'] for c in components); avail=sum(c['weight'] for c in valid); cov=avail/total if total else 0; sc=geometric_mean([(c['score'],c['weight']) for c in valid]) if valid and cov>=min_cov else None
 return {'id':fid,'label':label,'score':round(sc,4) if sc is not None else None,'coverage':round(cov,6),'components':components,'context':context or {}}
def get(repo,fam,kind,rid): return read_json(region_path(repo,fam,kind,rid),{})
def biodiversity(repo,kind,rid,extinction_p):
 d=get(repo,'biodiversity',kind,rid); s=d.get('summary') or {}; comps=[]
 intact=finite((s.get('intactness') or {}).get('valuePct')); comps.append(component('intactness','Biodiversity intactness',intact,.5,intact,'%', 'NHM BII'))
 h=s.get('historicalMammals') or {}; fr=finite(h.get('faunalRetentionPct')); rr=finite(h.get('rangeOccupancyRetentionPct')); comps.append(component('mammal_faunal_retention','Mammal faunal retention',fr,.2,fr,'%','PHYLACINE')); comps.append(component('mammal_range_retention','Mammal range retention',rr,.2,rr,'%','PHYLACINE'))
 ex=(s.get('extinctionRisk') or {}).get('mappedPost1500ExtinctionCount'); ep=extinction_p.get(rid); esc=100-ep if ep is not None else None; comps.append(component('mapped_extinction_security','Mapped post-1500 extinction security',esc,.1,ex,'mapped species','IUCN', 'Peer-relative; high mapped extinction evidence lowers score.'))
 ctx={'rarity':s.get('rarity'),'extinctionRisk':s.get('extinctionRisk'),'recorded':s.get('recorded'),'providerOrder':d.get('providerOrder')}; return family('biodiversity','Biodiversity',comps,ctx)
def vegetation(repo,kind,rid):
 if kind!='land': return None
 d=get(repo,'vegetation',kind,rid); latest=d.get('latest') or {}; base=d.get('baseline') or {}; comps=[]
 lv=finite(latest.get('vegetationPct')); bv=finite(base.get('vegetationPct')); lt=finite(latest.get('treePct')); bt=finite(base.get('treePct')); tr=finite((d.get('trend') or {}).get('vegetationPpPerDecade'))
 vr=clamp(100*lv/bv) if lv is not None and bv and bv>0 else None; tree=clamp(100*lt/bt) if lt is not None and bt and bt>0 else None; ts=clamp(100+(tr or 0)*20) if tr is not None else None
 comps=[component('cover_retention','Vegetation cover vs baseline',vr,.55,lv,'%','MOD44B'),component('tree_retention','Tree cover vs baseline',tree,.25,lt,'%','MOD44B'),component('long_term_trend','Vegetation trend',ts,.2,tr,'percentage points/decade','MOD44B')]; return family('vegetation','Vegetation condition',comps,{'baseline':base,'latest':latest,'trend':d.get('trend')})
def air_score(repo,kind,rid):
 d=get(repo,'air_pollution',kind,rid); comps=[]
 for mid,m in (d.get('metrics') or {}).items():
  p=finite(m.get('regionalPercentile')); sc=100-p if p is not None else None; latest=m.get('latest') or {}; comps.append(component(mid,m.get('label') or mid,sc,1,latest.get('mean'),m.get('unit'),'CAMS / Sentinel-5P'))
 return geometric_mean([(c['score'],1) for c in comps if c['score'] is not None]), comps
def water_score(repo,kind,rid):
 if kind not in {'marine','lakes'}: return None,[]
 d=get(repo,'water_pollution',kind,rid); comps=[]
 for mid,m in (d.get('metrics') or {}).items():
  p=finite(m.get('regionalStressPercentile')); sc=100-p if p is not None else None; latest=m.get('latest') or {}; comps.append(component(mid,m.get('label') or mid,sc,1,latest.get('mean'),m.get('unit'),(d.get('method') or {}).get('provider')))
 return geometric_mean([(c['score'],1) for c in comps if c['score'] is not None]), comps
def land_score(repo,kind,rid):
 if kind!='land': return None,[]
 d=get(repo,'land_pollution',kind,rid); comps=[]
 for pid,p in (d.get('providers') or {}).items():
  for key,rank in (p.get('peerPercentiles') or {}).items():
   q=finite(rank); sc=100-q if q is not None else None; raw=(p.get('metrics') or {}).get(key); comps.append(component(f'{pid}.{key}',f"{p.get('label') or pid} · {key}",sc,1,raw,None,pid))
 return geometric_mean([(c['score'],1) for c in comps if c['score'] is not None]), comps
def pollution(repo,kind,rid):
 a,ac=air_score(repo,kind,rid); w,wc=water_score(repo,kind,rid); l,lc=land_score(repo,kind,rid); comps=[]
 if kind=='land': specs=[('air','Air quality',a,.5),('land','Land pollution',l,.5)]
 else: specs=[('air','Air quality',a,.4),('water','Water quality',w,.6)]
 for cid,label,sc,wt in specs: comps.append(component(cid,label,sc,wt,None,'index points','multiple providers'))
 cont=get(repo,'contaminants',kind,rid); ctx={'airMetrics':ac,'waterMetrics':wc,'landMetrics':lc,'contaminants':cont.get('categories'),'contaminantCoverageWarning':cont.get('coverageWarning')}; return family('pollution','Pollution',comps,ctx,min_cov=.4)
def habitat(repo,kind,rid):
 if kind!='land': return None
 d=get(repo,'habitat',kind,rid); latest=d.get('latest') or {}; forest=d.get('forest') or {}; trends=d.get('trends') or {}; dc=finite(latest.get('directCropBuiltPct')); fl=finite(forest.get('treeLossPctOfBaselineForest')); ct=finite(trends.get('croplandPpPerDecade')); bt=finite(trends.get('builtPpPerDecade'))
 comps=[component('crop_built_retention','Land not classified as crop/built',100-dc if dc is not None else None,.4,dc,'% direct crop+built','Dynamic World'),component('forest_retention','Baseline forest retained',100-fl if fl is not None else None,.3,fl,'% of 2000 forest lost','Hansen GFC'),component('cropland_expansion','Cropland expansion pressure',clamp(100-(max(0,ct)*20)) if ct is not None else None,.15,ct,'pp/decade','Dynamic World'),component('urban_expansion','Built-up expansion pressure',clamp(100-(max(0,bt)*20)) if bt is not None else None,.15,bt,'pp/decade','Dynamic World')]; return family('habitat','Habitat',comps,{'latest':latest,'forest':forest,'supplemental':d.get('supplemental')})
def main():
 p=argparse.ArgumentParser(); p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); a=p.parse_args(); repo=a.repo.resolve(); meta=load_metadata(repo)
 counts={}
 for rid,(kind,m) in meta.items():
  b=get(repo,'biodiversity',kind,rid); ex=finite(((b.get('summary') or {}).get('extinctionRisk') or {}).get('mappedPost1500ExtinctionCount'))
  if ex is not None: counts[rid]=ex
 extp={rid:percentile_rank(counts,rid) for rid in counts}; root=repo/'frontend/public/data/indices/regions'; root.mkdir(parents=True,exist_ok=True); n=0
 for rid,(kind,m) in meta.items():
  fams=[]
  for f in (biodiversity(repo,kind,rid,extp),habitat(repo,kind,rid),vegetation(repo,kind,rid),pollution(repo,kind,rid)):
   if f and (f['score'] is not None or any(c.get('raw') is not None for c in f['components']) or f.get('context')): fams.append(f)
  if not fams: continue
  payload={'schemaVersion':1,'regionId':rid,'regionName':m.get('name') or rid,'regionKind':kind,'areaKm2':m.get('areaKm2'),'families':fams,'generatedAt':utc_now_iso(),'method':{'scoreDirection':'100 = best condition / lowest pressure','missingData':'coverage is explicit; missing metrics are not treated as healthy'}}; write_json(root/f'{rid}.json',payload,compact=True); n+=1
 # open_ocean is a visual residual, but non-species metrics deliberately resolve to the whole-ocean aggregate.
 ocean=read_json(repo/'frontend/public/data/indices/diagnostics/ocean.diagnostics.json',{}) or read_json(repo/'frontend/public/data/indices/families/ocean.index.json',{})
 if ocean:
  of={'id':'ocean','label':'Global Ocean','score':ocean.get('score'),'coverage':ocean.get('coverage',0),'components':ocean.get('components',[]),'context':{'scope':ocean.get('scope')}}
  write_json(root/'open_ocean.json',{'schemaVersion':1,'regionId':'open_ocean','regionName':'Global Ocean','regionKind':'marine','areaKm2':None,'families':[of],'generatedAt':utc_now_iso(),'method':{'selectionGeometry':'open-ocean residual','metricScope':'entire ocean (residual + all marine/coastal regions)','speciesScope':'residual only'}},compact=True); n+=1
 write_json(repo/'frontend/public/data/indices/regions.index.json',{'schemaVersion':1,'generatedAt':utc_now_iso(),'regionCount':n,'baseUrl':'./regions/'}); print(f'Built regional family indices for {n} regions.')
if __name__=='__main__': main()
