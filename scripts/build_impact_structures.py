#!/usr/bin/env python
from __future__ import annotations
import argparse, math, re
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import *

def yes(v): return str(v or '').strip().lower() in {'1','true','yes','y','confirmed'}

def _field(df, names): return find_col(df, names)
def _flt(r, col): return as_float(r.get(col)) if col else None
def _text(r, col):
    if not col: return None
    v=r.get(col)
    try:
        import pandas as pd
        if pd.isna(v): return None
    except Exception: pass
    s=str(v).strip()
    return s or None

def _parse_age_text(value):
    """Best-effort parse of source-provided age text; never manufactures an age."""
    if value is None: return (None, None, None, None)
    text=str(value).strip().replace('−','-').replace('–','-')
    if not text: return (None,None,None,None)
    # 35.7 ± 0.4 Ma
    m=re.search(r'(-?\d+(?:\.\d+)?)\s*(?:±|\+/-)\s*(\d+(?:\.\d+)?)', text)
    if m:
        age=float(m.group(1)); unc=abs(float(m.group(2)))
        return age, age-unc, age+unc, unc
    # 35.3 - 36.1 Ma (accept "to")
    m=re.search(r'(-?\d+(?:\.\d+)?)\s*(?:-|\bto\b)\s*(-?\d+(?:\.\d+)?)', text, re.I)
    if m:
        a,b=sorted((float(m.group(1)),float(m.group(2))))
        return (a+b)/2.0, a, b, (b-a)/2.0
    try: return (float(re.search(r'-?\d+(?:\.\d+)?', text).group(0)),None,None,None)
    except Exception: return (None,None,None,None)

def main():
    ap=argparse.ArgumentParser(description='Aggregate a staged confirmed terrestrial-impact catalog by MotherWorld region, preserving source-supplied age uncertainty and impactor estimates.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);ap.add_argument('--provider-label',default='Staged confirmed-impact catalog')
    a=ap.parse_args();repo=a.repo.resolve();df=read_table(a.input)
    lat=find_col(df,['latitude','lat'],True);lon=find_col(df,['longitude','lon','long'],True)
    name=_field(df,['name','crater','structure','crater_name','structure_name'])
    diam=_field(df,['diameter_km','diameter','rim_diameter_km','crater_diameter_km'])
    age=_field(df,['age_ma','age','age_myr','age_mya'])
    age_min=_field(df,['age_min_ma','minimum_age_ma','min_age_ma','age_lower_ma','age_low_ma'])
    age_max=_field(df,['age_max_ma','maximum_age_ma','max_age_ma','age_upper_ma','age_high_ma'])
    age_unc=_field(df,['age_uncertainty_ma','age_error_ma','age_sigma_ma','age_plus_minus_ma','age_unc_ma'])
    country=_field(df,['country']);target=_field(df,['target_type','target','target_rocks','target_lithology'])
    confirmed=_field(df,['confirmed','status','is_confirmed'])
    impactor_name=_field(df,['impactor_name','projectile_name','meteor_name','meteorite_name'])
    impactor_type=_field(df,['impactor_type','projectile_type','impactor_class','projectile_class'])
    impactor_diam=_field(df,['impactor_diameter_km','estimated_impactor_diameter_km','projectile_diameter_km','projectile_size_km'])
    impactor_min=_field(df,['impactor_diameter_min_km','impactor_min_km','projectile_diameter_min_km'])
    impactor_max=_field(df,['impactor_diameter_max_km','impactor_max_km','projectile_diameter_max_km'])
    impactor_unc=_field(df,['impactor_diameter_uncertainty_km','impactor_diameter_error_km','projectile_diameter_uncertainty_km'])
    reference=_field(df,['reference','citation','source_reference','source'])
    rows=[]
    for _,r in df.iterrows():
        if confirmed and not yes(r.get(confirmed)):continue
        la=_flt(r,lat);lo=_flt(r,lon)
        if la is None or lo is None:continue
        age_v=_flt(r,age); amin=_flt(r,age_min); amax=_flt(r,age_max); aunc=_flt(r,age_unc)
        if age and (age_v is None or (amin is None and amax is None and aunc is None)):
            parsed=_parse_age_text(r.get(age))
            if age_v is None: age_v=parsed[0]
            if amin is None: amin=parsed[1]
            if amax is None: amax=parsed[2]
            if aunc is None: aunc=parsed[3]
        if amin is not None and amax is not None and amin>amax: amin,amax=amax,amin
        if age_v is None and amin is not None and amax is not None: age_v=(amin+amax)/2.0
        if aunc is not None: aunc=abs(aunc)
        imin=_flt(r,impactor_min); imax=_flt(r,impactor_max)
        if imin is not None and imax is not None and imin>imax: imin,imax=imax,imin
        rows.append({
            'name':_text(r,name),'diameterKm':_flt(r,diam),'ageMa':age_v,
            'ageMinMa':amin,'ageMaxMa':amax,'ageUncertaintyMa':aunc,
            'country':_text(r,country),'targetType':_text(r,target),
            'impactorName':_text(r,impactor_name),
            'impactorType':_text(r,impactor_type),
            'impactorDiameterKm':_flt(r,impactor_diam),'impactorDiameterMinKm':imin,'impactorDiameterMaxKm':imax,
            'impactorDiameterUncertaintyKm':abs(_flt(r,impactor_unc)) if _flt(r,impactor_unc) is not None else None,
            'reference':_text(r,reference),'geometry':Point(lo,la)
        })
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_regions(repo,globalize_open_ocean=True);j=assign_points(pts,regs);source=source_obj('impact_confirmed_staged',a.provider_label,license='Review provider terms before redistribution')
    for rid,g in j.groupby('regionId'):
        meta=g.iloc[0];structures=[]
        for x in g.itertuples():
            structures.append({
                'name': None if pd.isna(getattr(x,'name')) else getattr(x,'name'),
                'diameterKm': as_float(getattr(x,'diameterKm')),
                'ageMa': as_float(getattr(x,'ageMa')),
                'ageMinMa': as_float(getattr(x,'ageMinMa')),
                'ageMaxMa': as_float(getattr(x,'ageMaxMa')),
                'ageUncertaintyMa': as_float(getattr(x,'ageUncertaintyMa')),
                'country': None if pd.isna(getattr(x,'country')) else getattr(x,'country'),
                'targetType': None if pd.isna(getattr(x,'targetType')) else getattr(x,'targetType'),
                'impactorName': None if pd.isna(getattr(x,'impactorName')) else getattr(x,'impactorName'),
                'impactorType': None if pd.isna(getattr(x,'impactorType')) else getattr(x,'impactorType'),
                'impactorDiameterKm': as_float(getattr(x,'impactorDiameterKm')),
                'impactorDiameterMinKm': as_float(getattr(x,'impactorDiameterMinKm')),
                'impactorDiameterMaxKm': as_float(getattr(x,'impactorDiameterMaxKm')),
                'impactorDiameterUncertaintyKm': as_float(getattr(x,'impactorDiameterUncertaintyKm')),
                'reference': None if pd.isna(getattr(x,'reference')) else getattr(x,'reference'),
            })
        structures.sort(key=lambda x:x['diameterKm'] if x['diameterKm'] is not None else -1,reverse=True)
        enriched=sum(1 for x in structures if any(x.get(k) is not None and str(x.get(k)).strip() for k in ('ageMinMa','ageMaxMa','ageUncertaintyMa','impactorName','impactorType','impactorDiameterKm','impactorDiameterMinKm','impactorDiameterMaxKm')))
        sec={'impacts':{'count':len(g),'maxDiameterKm':max([x['diameterKm'] for x in structures if x['diameterKm'] is not None],default=None),'structuresWithExtendedContext':enriched,'structures':structures[:30],'source':source}}
        write_fragment(repo,'impact_confirmed_staged',{'regionId':rid,'regionName':meta.regionName,'kind':meta.kind},sec,source)
    print(f'Impact catalog matched {len(j)} structure-region records')
if __name__=='__main__':main()
