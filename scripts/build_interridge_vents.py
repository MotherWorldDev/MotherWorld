#!/usr/bin/env python
from __future__ import annotations
import argparse,re
from collections import Counter
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point
from geology_common import WGS84, as_float, find_col, read_table, source_obj, write_fragment
from seafloor_common import is_open_ocean_id, load_marine_analysis_regions

def main():
    ap=argparse.ArgumentParser(description='Aggregate InterRidge hydrothermal vent fields by MotherWorld marine region and whole ocean.')
    ap.add_argument('--repo',type=Path,default=Path.cwd());ap.add_argument('--input',type=Path,required=True);a=ap.parse_args();repo=a.repo.resolve();df=read_table(a.input)
    lat=find_col(df,['latitude','lat'],True);lon=find_col(df,['longitude','lon'],True);name=find_col(df,['name_id','name','vent field','ventfield']);activity=find_col(df,['activity','site activity']);depth=find_col(df,['maximum_or_single_reported_depth','maximum depth','depth']);temp=find_col(df,['maximum_temperature','maximum temperature']);setting=find_col(df,['tectonic_setting','tectonic setting']);ocean=find_col(df,['ocean']);year=find_col(df,['year_and_how_discovered_if_active,_visual_confirmation_is_listed_first','year discovered','discovery year'])
    rows=[]
    for _,row in df.iterrows():
        la=as_float(row.get(lat));lo=as_float(row.get(lon));
        if la is None or lo is None:continue
        y=None
        if year:
            m=re.search(r'\b(18|19|20)\d{2}\b',str(row.get(year) or ''))
            if m:y=int(m.group(0))
        rows.append({'name':row.get(name) if name else None,'activity':row.get(activity) if activity else None,'depthM':as_float(row.get(depth)) if depth else None,'maxTemperatureC':as_float(row.get(temp)) if temp else None,'tectonicSetting':row.get(setting) if setting else None,'ocean':row.get(ocean) if ocean else None,'discoveryYear':y,'geometry':Point(lo,la)})
    pts=gpd.GeoDataFrame(rows,geometry='geometry',crs=WGS84);regs=load_marine_analysis_regions(repo)
    # Assign to ordinary marine regions with their own geometry. Open ocean uses the whole-ocean scope and therefore gets all points.
    ordinary=regs[~regs.regionId.map(is_open_ocean_id)].copy();joined=gpd.sjoin(pts,ordinary[['regionId','regionName','kind','geometry']],how='inner',predicate='intersects') if not ordinary.empty else gpd.GeoDataFrame()
    source=source_obj('interridge_vents','InterRidge Global Database of Active Submarine Hydrothermal Vent Fields',version='3.4 (2020)',doi='10.1594/PANGAEA.917894',license='CC BY-NC-SA 4.0')
    groups={rid:g.copy() for rid,g in joined.groupby('regionId')} if len(joined) else {}
    for r in regs.itertuples():
        if is_open_ocean_id(r.regionId):g=pts.copy();g['regionName']=r.regionName;g['kind']=r.kind
        else:g=groups.get(r.regionId)
        if g is None or len(g)==0:continue
        settings=Counter(str(x).strip() for x in g.tectonicSetting if x is not None and str(x).strip() and str(x).lower()!='nan');acts=Counter(str(x).strip().lower() for x in g.activity if x is not None and str(x).strip())
        depths=[x for x in g.depthM if x is not None];temps=[x for x in g.maxTemperatureC if x is not None]
        items=[]
        for x in g.itertuples():items.append({'name':x.name,'activity':x.activity,'depthM':x.depthM,'maxTemperatureC':x.maxTemperatureC,'tectonicSetting':x.tectonicSetting,'ocean':x.ocean,'discoveryYear':x.discoveryYear})
        sec={'seafloor':{'hydrothermal':{'ventFieldCount':len(g),'confirmedOrInferredActive':sum(v for k,v in acts.items() if ('active' in k or 'confirmed' in k or 'inferred' in k) and 'inactive' not in k),'inactiveCount':sum(v for k,v in acts.items() if 'inactive' in k),'medianDepthM':float(__import__('numpy').median(depths)) if depths else None,'maxReportedTemperatureC':max(temps) if temps else None,'tectonicSettings':[{'label':k,'count':v} for k,v in settings.most_common(12)],'fields':items[:40],'source':source}}}
        write_fragment(repo,'interridge_vents',{'regionId':r.regionId,'regionName':r.regionName,'kind':r.kind},sec,source)
    print(f'InterRidge vents parsed: {len(pts)} fields')
if __name__=='__main__':main()
