#!/usr/bin/env python
from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
import ee
from backbone_common import HISTORY_START_YEAR, family_payload, write_family

COLLECTION='ECMWF/ERA5_LAND/MONTHLY_AGGR'
def init(project,auth):
    if auth: ee.Authenticate()
    ee.Initialize(project=project)
def rz(img): return img.select('volumetric_soil_water_layer_1').multiply(.07).add(img.select('volumetric_soil_water_layer_2').multiply(.21)).add(img.select('volumetric_soil_water_layer_3').multiply(.72)).rename('rz')

def main():
    p=argparse.ArgumentParser(description='Build global-only Freshwater Earth Health backbone from ERA5-Land root-zone soil-moisture stability.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--project',required=True); p.add_argument('--authenticate',action='store_true'); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); p.add_argument('--end-year',type=int,default=date.today().year-1); p.add_argument('--baseline-start',type=int,default=1991); p.add_argument('--baseline-end',type=int,default=2020); p.add_argument('--scale',type=float,default=25000); a=p.parse_args(); init(a.project,a.authenticate); repo=a.repo.resolve(); coll=ee.ImageCollection(COLLECTION); baseline=coll.filterDate(f'{a.baseline_start}-01-01',f'{a.baseline_end+1}-01-01').map(rz)
    pct={m:baseline.filter(ee.Filter.calendarRange(m,m,'month')).reduce(ee.Reducer.percentile([10,90])) for m in range(1,13)}
    geom=ee.Geometry.Rectangle([-180,-60,180,90],None,False).difference(ee.Geometry.Rectangle([-75,58,-10,85],None,False),1000); area=ee.Image.pixelArea(); feats=[]
    for y in range(a.start_year,a.end_year+1):
        for m in range(1,13):
            start=ee.Date.fromYMD(y,m,1); cur=rz(coll.filterDate(start,start.advance(1,'month')).mean()); p10=pct[m].select('rz_p10'); p90=pct[m].select('rz_p90'); valid=cur.mask().And(p10.mask()).And(p90.mask()); normal=cur.gte(p10).And(cur.lte(p90)).And(valid)
            sums=ee.Image.cat([area.updateMask(valid).rename('valid'),area.updateMask(normal).rename('normal')]).reduceRegion(ee.Reducer.sum(),geom,a.scale,bestEffort=True,maxPixels=1e10,tileScale=4); feats.append(ee.Feature(None,sums).set({'year':y,'month':m}))
    rows=ee.FeatureCollection(feats).getInfo().get('features',[]); props=[x.get('properties',{}) for x in rows]; series=[]
    for y in range(a.start_year,a.end_year+1):
        yr=[r for r in props if int(r.get('year') or -1)==y]; va=sum(float(r.get('valid') or 0) for r in yr); na=sum(float(r.get('normal') or 0) for r in yr); months=sum(float(r.get('valid') or 0)>0 for r in yr)
        if va>0:
            frac=na/va; series.append({'year':y,'score':min(100,100*frac/.80),'coverage':months/12,'raw':frac*100,'unit':'% land-area-months inside fixed historical P10–P90 envelope'})
    payload=family_payload(family_id='freshwater',label='Freshwater stability',series=series,component={'id':'root_zone_soil_moisture_stability','label':'Root-zone soil-moisture stability','weight':1.0,'source':'ERA5-Land'},source={'id':'era5land-soil-moisture','label':'Copernicus ERA5-Land Monthly Aggregated','collection':COLLECTION},method={'baseline':f'{a.baseline_start}-{a.baseline_end} calendar-month per-pixel P10–P90 envelope','normalization':'80% of land-area-months in envelope = 100; lower fractions scale linearly','scoreDirection':'100 = global root-zone soil moisture remains within historical local regimes','scope':'global only; Antarctica and Greenland ice-sheet box excluded','diagnosticPolicy':'GRACE terrestrial water storage and JRC surface-water retention remain present-day diagnostics only'},context={'regionalFreshwaterScoring':False,'rationale':'hydrology crosses ecoregion boundaries; basin/lake scoring is deferred'})
    out=write_family(repo,'freshwater',payload); print(f'Freshwater backbone latest={payload["latestBackboneYear"]} score={payload["score"]} -> {out}')
if __name__=='__main__': main()
