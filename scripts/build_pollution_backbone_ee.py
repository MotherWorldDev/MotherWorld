#!/usr/bin/env python
from __future__ import annotations
import argparse
from datetime import date
from pathlib import Path
import ee
from backbone_common import HISTORY_START_YEAR, family_payload, write_family

MERRA='NASA/GSFC/MERRA/aer/2'; WATER='MODIS/MOD44W/MOD44W_005_2000_02_24'

def init(project,auth):
    if auth: ee.Authenticate()
    ee.Initialize(project=project)

def pm25(img):
    # NASA MERRA-2 FAQ reconstruction. Surface mass concentrations are kg/m3; ×1e9 => µg/m3.
    return (img.select('DUSMASS25').add(img.select('OCSMASS')).add(img.select('BCSMASS')).add(img.select('SSSMASS25')).add(img.select('SO4SMASS').multiply(132.14/96.06))).multiply(1e9).rename('pm25')

def score(v,healthy,critical): return max(0.0,min(100.0,100.0*(critical-v)/(critical-healthy)))

def main():
    p=argparse.ArgumentParser(description='Build 1993+ global Pollution Earth Health backbone from MERRA-2 reconstructed surface PM2.5.')
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]); p.add_argument('--project',required=True); p.add_argument('--authenticate',action='store_true'); p.add_argument('--start-year',type=int,default=HISTORY_START_YEAR); p.add_argument('--end-year',type=int,default=date.today().year-1); p.add_argument('--healthy-ugm3',type=float,default=5.0); p.add_argument('--critical-ugm3',type=float,default=50.0); p.add_argument('--scale',type=float,default=60000); a=p.parse_args(); init(a.project,a.authenticate); repo=a.repo.resolve()
    coll=ee.ImageCollection(MERRA); water=ee.Image(WATER).select('water_mask'); land=water.eq(0); area=ee.Image.pixelArea(); geom=ee.Geometry.Rectangle([-180,-60,180,85],None,False)
    land_area=float(ee.Number(area.updateMask(land).reduceRegion(ee.Reducer.sum(),geom,a.scale,bestEffort=True,maxPixels=1e10,tileScale=4).get('area')).getInfo())
    rows=[]
    for y in range(a.start_year,a.end_year+1):
        annual=coll.filterDate(f'{y}-01-01',f'{y+1}-01-01').map(pm25).mean(); valid=land.And(annual.mask())
        vals=ee.Image.cat([annual.multiply(area).rename('weighted'),area.rename('area')]).updateMask(valid).reduceRegion(ee.Reducer.sum(),geom,a.scale,bestEffort=True,maxPixels=1e10,tileScale=4).getInfo(); va=float(vals.get('area') or 0); w=float(vals.get('weighted') or 0)
        if va>0:
            mean=w/va; rows.append({'year':y,'score':score(mean,a.healthy_ugm3,a.critical_ugm3),'coverage':min(1,va/land_area) if land_area else 0,'raw':mean,'unit':'µg/m³ reconstructed surface PM2.5'})
    payload=family_payload(family_id='pollution',label='Pollution',series=rows,component={'id':'merra2_pm25','label':'Global ambient particulate burden','weight':1.0,'source':'NASA MERRA-2 aerosol reanalysis'},source={'id':'nasa-merra2-aerosol','label':'NASA MERRA-2 aerosol reanalysis','collection':MERRA},method={'pm25Formula':'DUSMASS25 + OCSMASS + BCSMASS + SSSMASS25 + SO4SMASS*(132.14/96.06)','spatialAggregation':'land-area-weighted global annual mean','normalization':{'type':'linear','healthyUgM3':a.healthy_ugm3,'criticalUgM3':a.critical_ugm3},'scoreDirection':'100 = lowest particulate burden under configured reference','diagnosticPolicy':'CAMS, Sentinel-5P/TROPOMI, land/water pollution and contaminants remain present-day diagnostics only'},context={'caveat':'MERRA-2 PM2.5 is a reanalysis proxy; its standard reconstruction does not include nitrate aerosol and includes natural aerosol contributions.'})
    out=write_family(repo,'pollution',payload); print(f'Pollution backbone latest={payload["latestBackboneYear"]} score={payload["score"]} -> {out}')
if __name__=='__main__': main()
