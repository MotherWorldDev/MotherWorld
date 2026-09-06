#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Cesium GeographicTilingScheme JPG tiles from a global equirectangular Blue Marble image."
    )
    parser.add_argument(
        "--source",
        default="Bluemarble/february/world.200402.3x21600x10800_geo.tif",
        help="Path to global equirectangular source image (JPG/PNG/TIF).",
    )
    parser.add_argument(
        "--out-dir",
        default="frontend/public/assets/imagery/blue-marble-tiles",
        help="Output tile root directory.",
    )
    parser.add_argument(
        "--levels",
        default="5",
        help="Comma-separated Cesium geographic tile levels to generate (e.g. '4,5').",
    )
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--quality", type=int, default=84)
    return parser.parse_args()


def geographic_level_dims(level: int, tile_size: int) -> tuple[int, int, int, int]:
    # Cesium GeographicTilingScheme defaults: level 0 = 2 x 1 tiles
    tiles_x = 2 ** (level + 1)
    tiles_y = 2**level
    return tiles_x, tiles_y, tiles_x * tile_size, tiles_y * tile_size


def main() -> None:
    args = parse_args()
    Image.MAX_IMAGE_PIXELS = None

    project_root = Path(__file__).resolve().parents[1]
    source_path = (project_root / args.source).resolve()
    out_root = (project_root / args.out_dir).resolve()

    if not source_path.exists():
      raise FileNotFoundError(f"Source image not found: {source_path}")

    levels = sorted({int(x.strip()) for x in str(args.levels).split(",") if x.strip() != ""})
    if not levels:
        raise ValueError("No levels specified")
    if any(level < 0 for level in levels):
        raise ValueError("Levels must be >= 0")

    print(f"Loading source image: {source_path}")
    src = Image.open(source_path).convert("RGB")
    print(f"Source size: {src.width} x {src.height}")

    out_root.mkdir(parents=True, exist_ok=True)

    for level in levels:
        tiles_x, tiles_y, target_w, target_h = geographic_level_dims(level, args.tile_size)
        print(
            f"\nLevel {level}: {tiles_x}x{tiles_y} tiles "
            f"({target_w}x{target_h} raster target)"
        )

        # Resize once per level, then crop tiles. Lanczos keeps the source alignment crisp enough.
        level_img = src.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
        level_dir = out_root / str(level)
        if level_dir.exists():
            for stale in level_dir.rglob("*.jpg"):
                stale.unlink()
        level_dir.mkdir(parents=True, exist_ok=True)

        for x in range(tiles_x):
            col_dir = level_dir / str(x)
            col_dir.mkdir(parents=True, exist_ok=True)
            left = x * args.tile_size
            right = left + args.tile_size
            for y in range(tiles_y):
                top = y * args.tile_size
                bottom = top + args.tile_size
                tile = level_img.crop((left, top, right, bottom))
                tile.save(
                    col_dir / f"{y}.jpg",
                    quality=int(args.quality),
                    optimize=True,
                    progressive=True,
                )

        print(f"  Wrote tiles to {level_dir}")

    print("\nDone.")


if __name__ == "__main__":
    main()
