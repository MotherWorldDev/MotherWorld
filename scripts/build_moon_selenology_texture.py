#!/usr/bin/env python
from __future__ import annotations
import argparse, json, shutil, tempfile, urllib.request, zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_RASTER_URL = "https://repository.hou.usra.edu/bitstream/handle/20.500.11753/1598/Unified_Geologic_Map_of_the_Moon_RASTER.zip?sequence=2&isAllowed=y"

def utcnow(): return datetime.now(timezone.utc).isoformat()
def download(url: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "MotherWorld/selenology-builder"})
    with urllib.request.urlopen(req, timeout=120) as r, dst.open("wb") as f: shutil.copyfileobj(r, f)
def extract_if_zip(path: Path, temp: Path) -> Path:
    if not zipfile.is_zipfile(path): return path
    with zipfile.ZipFile(path) as zf: zf.extractall(temp)
    return temp
def find_raster(root: Path) -> Path:
    if root.is_file(): return root
    for pat in ("*Geology*wStructure*32ppd*.tif*", "*Geolog*.tif*", "*.tif", "*.tiff"):
        candidates = list(root.rglob(pat))
        if candidates: return sorted(candidates, key=lambda p: p.stat().st_size, reverse=True)[0]
    raise FileNotFoundError("No GeoTIFF found in Moon raster package")
def load_rgb(path: Path):
    from PIL import Image
    import numpy as np
    import rasterio
    with rasterio.open(path) as ds:
        ratio = ds.width / ds.height
        if ratio < 1.85 or ratio > 2.15: raise ValueError(f"Expected a global ~2:1 lunar raster, got {ds.width}x{ds.height}")
        if ds.count >= 3:
            arr = np.moveaxis(ds.read([1,2,3]), 0, 2)
            if arr.dtype != np.uint8:
                finite = arr[np.isfinite(arr)]; lo=float(np.percentile(finite,1)); hi=float(np.percentile(finite,99))
                arr = np.clip((arr-lo)/(hi-lo or 1)*255,0,255).astype(np.uint8)
            return Image.fromarray(arr, "RGB")
        band = ds.read(1); cmap = ds.colormap(1)
        rgba = np.zeros((ds.height,ds.width,4),dtype=np.uint8)
        for key,val in cmap.items(): rgba[band==key]=val
        return Image.fromarray(rgba,"RGBA").convert("RGB")
def parse_sld(path: Path | None):
    if not path or not path.exists(): return []
    tree=ET.parse(path); out=[]
    for rule in tree.findall('.//{*}Rule'):
        title=rule.findtext('{*}Title') or rule.findtext('{*}Name') or 'Mapped unit'; color=None
        for css in rule.findall('.//{*}CssParameter') + rule.findall('.//{*}SvgParameter'):
            if css.attrib.get('name')=='fill' and (css.text or '').strip(): color=css.text.strip(); break
        if color: out.append({'label':title,'color':color})
    seen=set(); unique=[]
    for item in out:
        key=(item['label'],item['color'])
        if key not in seen: unique.append(item); seen.add(key)
    return unique
def parse_size(text):
    a,b=text.lower().split('x',1); return int(a),int(b)
def main():
    ap=argparse.ArgumentParser(description="Build web-ready USGS lunar geology textures and the MotherWorld Selenology index.")
    ap.add_argument('--repo',type=Path,default=Path.cwd()); ap.add_argument('--source',type=Path)
    ap.add_argument('--download',action='store_true'); ap.add_argument('--url',default=DEFAULT_RASTER_URL); ap.add_argument('--sld',type=Path)
    ap.add_argument('--low-size',default='4096x2048'); ap.add_argument('--high-size',default='8192x4096'); ap.add_argument('--quality',type=int,default=92)
    args=ap.parse_args(); repo=args.repo.resolve(); raw=repo/'selenology_raw'; raw.mkdir(exist_ok=True); src=args.source
    if src is None:
        if not args.download: raise SystemExit('Pass --source PATH or use --download')
        src=raw/'Unified_Geologic_Map_of_the_Moon_RASTER.zip'
        if not src.exists(): print(f'Downloading {args.url}'); download(args.url,src)
    with tempfile.TemporaryDirectory(prefix='motherworld_selenology_') as td:
        raster=find_raster(extract_if_zip(src.resolve(),Path(td))); image=load_rgb(raster)
        outdir=repo/'frontend/public/assets/moon/selenology'; outdir.mkdir(parents=True,exist_ok=True)
        from PIL import Image
        outputs=[]
        for name,size in (("moon-geology-4k.webp",parse_size(args.low_size)),("moon-geology-8k.webp",parse_size(args.high_size))):
            dst=outdir/name; image.resize(size,Image.Resampling.LANCZOS).save(dst,"WEBP",quality=max(1,min(100,args.quality)),method=6); outputs.append(dst)
    idx=repo/'frontend/public/data/moon/selenology.json'; data=json.loads(idx.read_text()) if idx.exists() else {'schemaVersion':1,'sections':{}}
    data['generatedAt']=utcnow(); data['texture']={'available':True,'url4k':'./assets/moon/selenology/moon-geology-4k.webp','url8k':'./assets/moon/selenology/moon-geology-8k.webp','source':'usgs-unified-geologic-map-moon-v2','sourceRaster':raster.name}; data['legend']=parse_sld(args.sld)
    idx.parent.mkdir(parents=True,exist_ok=True); idx.write_text(json.dumps(data,indent=2)+'\n')
    print('Wrote:'); [print(' ',p) for p in outputs]; print(' ',idx)
if __name__=='__main__': main()
