#!/usr/bin/env python
from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
import ee
from backbone_common import HISTORY_START_YEAR, family_payload, write_family

AVHRR='NOAA/CDR/AVHRR/NDVI/V5'; VIIRS='NOAA/CDR/VIIRS/NDVI/V1'

def init(project,auth):
    if auth: ee.Authenticate()
    ee.Initialize(project=project)

def ndvi(img):
    qa=img.select('QA')
    good=(qa.bitwiseAnd(1<<1).eq(0).And(qa.bitwiseAnd(1<<2).eq(0)).And(qa.bitwiseAnd(1<<3).eq(0)).And(qa.bitwiseAnd(1<<6).eq(0)).And(qa.bitwiseAnd(1<<8).eq(0)).And(qa.bitwiseAnd(1<<9).eq(0)))
    return img.select('NDVI').multiply(0.0001).updateMask(good).rename('ndvi')
def yearly(coll, year): return coll.filterDate(f'{year}-01-01',f'{year+1}-01-01').map(ndvi).median()

def main():
    p=argparse.ArgumentParser(description='Build 1993+ Vegetation Earth Health backbone from NOAA AVHRR/VIIRS NDVI climate data records.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--project',required=True); p.add_argument('--authenticate',action='store_true'); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); p.add_argument('--end-year',type=int,default=date.today().year-1); p.add_argument('--baseline-start',type=int,default=1982); p.add_argument('--baseline-end',type=int,default=1992); p.add_argument('--vegetated-threshold',type=float,default=.10); p.add_argument('--scale',type=float,default=10000); a=p.parse_args(); init(a.project,a.authenticate); repo=a.repo.resolve()
    av=ee.ImageCollection(AVHRR); vv=ee.ImageCollection(VIIRS)
    baseline=ee.ImageCollection([yearly(av,y) for y in range(a.baseline_start,a.baseline_end+1)]).median().rename('baseline')
    base_mask=baseline.gt(a.vegetated_threshold); area=ee.Image.pixelArea(); geom=ee.Geometry.Rectangle([-180,-60,180,85],None,False)
    base_area=area.updateMask(base_mask).reduceRegion(ee.Reducer.sum(),geom,a.scale,bestEffort=True,maxPixels=1e10,tileScale=4).get('area')
    rows=[]
    for y in range(a.start_year,a.end_year+1):
        coll=av if y<=2013 else vv; current=yearly(coll,y); valid=base_mask.And(current.mask())
        score_img=current.divide(baseline).clamp(0,1).multiply(100).rename('score')
        sums=ee.Image.cat([score_img.multiply(area).rename('weighted'),area.rename('area')]).updateMask(valid).reduceRegion(ee.Reducer.sum(),geom,a.scale,bestEffort=True,maxPixels=1e10,tileScale=4)
        vals=ee.Dictionary(sums).combine(ee.Dictionary({'weighted':0,'area':0}),False).getInfo(); ba=float(ee.Number(base_area).getInfo()) if y==a.start_year else base_area_value
        if y==a.start_year: base_area_value=ba
        va=float(vals.get('area') or 0); weighted=float(vals.get('weighted') or 0)
        if va>0: rows.append({'year':y,'score':weighted/va,'coverage':min(1.0,va/base_area_value) if base_area_value>0 else 0,'raw':weighted/va,'unit':'% baseline-relative NDVI retention','sensor':'AVHRR' if y<=2013 else 'VIIRS'})
    payload=family_payload(family_id='vegetation',label='Vegetation condition',series=rows,component={'id':'ndvi_retention','label':'Baseline-relative vegetation greenness retention','weight':1.0,'source':'NOAA NDVI Climate Data Record'},source={'id':'noaa-ndvi-cdr','label':'NOAA AVHRR/VIIRS NDVI Climate Data Record','collections':[AVHRR,VIIRS]},method={'baseline':f'{a.baseline_start}-{a.baseline_end} per-pixel median annual NDVI','normalization':'annual NDVI / fixed baseline NDVI, clipped 0..1, area-weighted over pixels with baseline NDVI above threshold','vegetatedThreshold':a.vegetated_threshold,'scoreDirection':'100 = baseline vegetation greenness retained or exceeded','sensorTransition':'AVHRR through 2013; VIIRS from 2014; both are NOAA CDR products','diagnosticPolicy':'MODIS VCF cover/composition remains present-day diagnostics only'})
    out=write_family(repo,'vegetation',payload); print(f'Vegetation backbone latest={payload["latestBackboneYear"]} score={payload["score"]} -> {out}')
if __name__=='__main__': main()
