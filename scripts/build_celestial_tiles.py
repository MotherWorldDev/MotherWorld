#!/usr/bin/env python3
"""Build deterministic equirectangular celestial tile pyramids."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from PIL import Image

TILE, GUTTER = 512, 2

SETS = {
    "sky": ("frontend/public/assets/sky/starmap_2020_8k.lossless.webp", 3, "frontend/public/assets/sky/tiles", "frontend/public/assets/sky/tiles"),
    "natural": ("frontend/public/assets/moon/lroc-color-4k.jpg", 2, "frontend/public/assets/moon/tiles/natural", "frontend/public/assets/moon/tiles/natural"),
    "eclipse": ("frontend/public/assets/moon/lroc-color-4k-eclipse.jpg", 2, "frontend/public/assets/moon/tiles/eclipse", "frontend/public/assets/moon/tiles/eclipse"),
    "geology": ("frontend/public/assets/moon/selenology/moon-geology-8k.webp", 3, "frontend/public/assets/moon/tiles/geology", "frontend/public/assets/moon/tiles/geology"),
}

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()

def gutter_tile(im: Image.Image, left: int, top: int) -> Image.Image:
    w, h = im.size
    out = Image.new(im.mode, (TILE + 2 * GUTTER, TILE + 2 * GUTTER))
    # 516 individual columns keeps wrapping/clamping exact and bounded per tile.
    for ox in range(-GUTTER, TILE + GUTTER):
        sx = (left + ox) % w
        col = im.crop((sx, max(0, top-GUTTER), sx+1, min(h, top+TILE+GUTTER)))
        if top-GUTTER < 0: out.paste(im.crop((sx, 0, sx+1, 1)).resize((1, -top+GUTTER)), (ox+GUTTER, 0))
        out.paste(col, (ox+GUTTER, max(0, GUTTER-top)))
        if top+TILE+GUTTER > h:
            n = top+TILE+GUTTER-h
            out.paste(im.crop((sx, h-1, sx+1, h)).resize((1,n)), (ox+GUTTER, 516-n))
    return out

def overview_spec(name: str):
    return ("overview-4k.webp", (4096, 2048)) if name == "sky" else ("overview.webp", (1024, 512))


def build(root: Path, name: str):
    rel, maxz, tile_rel, overview_rel = SETS[name]
    src = root / rel
    categorical = name == "geology"
    overview_name, overview_size = overview_spec(name)
    with Image.open(src) as original:
        original.load()
        expected = (1024 * (2**maxz), 512 * (2**maxz))
        if original.size != expected: raise ValueError(f"{name}: source {original.size}, expected {expected}")
        for z in range(maxz + 1):
            size = (1024 * (2**z), 512 * (2**z))
            if z == maxz: im = original
            else: im = original.resize(size, Image.Resampling.NEAREST if categorical else Image.Resampling.LANCZOS)
            cols, rows = 2**(z+1), 2**z
            for x in range(cols):
                for y in range(rows):
                    d = root / tile_rel / str(z) / str(x); d.mkdir(parents=True, exist_ok=True)
                    tile = gutter_tile(im, x*TILE, y*TILE)
                    tile.save(d / f"{y}.webp", "WEBP", lossless=True, method=6)
                    tile.close()
            if size == overview_size:
                im.save(root / overview_rel / overview_name, "WEBP", lossless=True, method=6)
            if z != maxz: im.close()
        manifest = {"name": name, "source": rel.replace('\\','/'), "source_sha256": sha256(src), "tile_size": 512, "gutter": 2,
                    "overview": {"path": overview_name, "width": overview_size[0], "height": overview_size[1]},
                    "levels": [{"z": z, "width": 1024*2**z, "height": 512*2**z, "cols": 2**(z+1), "rows": 2**z,
                                "tiles": 2**(2*z+1), "path": f"{tile_rel}/{z}/{{x}}/{{y}}.webp"} for z in range(maxz+1)]}
        (root / tile_rel).mkdir(parents=True, exist_ok=True)
        (root / tile_rel / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1]); ap.add_argument("--set", choices=["all", *SETS], default="all")
    a = ap.parse_args(); [build(a.root, n) for n in SETS if a.set in ("all", n)]
if __name__ == "__main__": main()
