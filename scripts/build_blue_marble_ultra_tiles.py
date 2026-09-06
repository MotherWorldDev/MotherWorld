#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


QUADRANT_LAYOUT = {
    "A1": (0, 0),
    "B1": (1, 0),
    "C1": (2, 0),
    "D1": (3, 0),
    "A2": (0, 1),
    "B2": (1, 1),
    "C2": (2, 1),
    "D2": (3, 1),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Cesium geographic tiles from NASA Blue Marble 8-quadrant 21600x21600 TIFF set."
    )
    parser.add_argument("--source-dir", default="Bluemarble/february")
    parser.add_argument("--prefix", default="world.200402.3x21600x21600.")
    parser.add_argument("--suffix", default="_geo.tif")
    parser.add_argument("--out-dir", default="frontend/public/assets/imagery/blue-marble-ultra-tiles")
    parser.add_argument("--levels", default="6,7", help="Comma-separated Cesium geographic levels, e.g. '6,7'")
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--quality", type=int, default=82)
    return parser.parse_args()


def level_dims(level: int, tile_size: int) -> tuple[int, int, int, int]:
    tiles_x = 2 ** (level + 1)
    tiles_y = 2**level
    return tiles_x, tiles_y, tiles_x * tile_size, tiles_y * tile_size


def quadrant_path(source_dir: Path, prefix: str, suffix: str, code: str) -> Path:
    return source_dir / f"{prefix}{code}{suffix}"


def clear_level(level_dir: Path) -> None:
    if not level_dir.exists():
        return
    for stale in level_dir.rglob("*.jpg"):
        stale.unlink()


def main() -> None:
    args = parse_args()
    Image.MAX_IMAGE_PIXELS = None

    project_root = Path(__file__).resolve().parents[1]
    source_dir = (project_root / args.source_dir).resolve()
    out_root = (project_root / args.out_dir).resolve()
    levels = sorted({int(x.strip()) for x in str(args.levels).split(",") if x.strip() != ""})
    if not levels:
        raise ValueError("No levels specified")
    if any(level < 1 for level in levels):
        raise ValueError("Levels must be >= 1 for 8-quadrant world layout")

    print(f"Using source dir: {source_dir}")
    for code in QUADRANT_LAYOUT:
        path = quadrant_path(source_dir, args.prefix, args.suffix, code)
        if not path.exists():
            raise FileNotFoundError(f"Missing quadrant file: {path}")

    out_root.mkdir(parents=True, exist_ok=True)

    for level in levels:
        tiles_x, tiles_y, world_w, world_h = level_dims(level, args.tile_size)
        if tiles_x % 4 != 0 or tiles_y % 2 != 0:
            raise ValueError(f"Level {level} does not align cleanly with 4x2 quadrant layout")

        q_tiles_x = tiles_x // 4
        q_tiles_y = tiles_y // 2
        q_w = q_tiles_x * args.tile_size
        q_h = q_tiles_y * args.tile_size

        level_dir = out_root / str(level)
        clear_level(level_dir)
        level_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"\nLevel {level}: world={world_w}x{world_h}, "
            f"quadrant target={q_w}x{q_h}, tiles={tiles_x}x{tiles_y}"
        )

        for code, (qx, qy) in QUADRANT_LAYOUT.items():
            src_path = quadrant_path(source_dir, args.prefix, args.suffix, code)
            print(f"  Processing {code}: {src_path.name}")
            src = Image.open(src_path)
            if src.mode != "RGB":
                src = src.convert("RGB")
            resized = src.resize((q_w, q_h), resample=Image.Resampling.LANCZOS)

            x_offset = qx * q_tiles_x
            y_offset = qy * q_tiles_y
            for local_x in range(q_tiles_x):
                global_x = x_offset + local_x
                col_dir = level_dir / str(global_x)
                col_dir.mkdir(parents=True, exist_ok=True)
                left = local_x * args.tile_size
                right = left + args.tile_size
                for local_y in range(q_tiles_y):
                    global_y = y_offset + local_y
                    top = local_y * args.tile_size
                    bottom = top + args.tile_size
                    tile = resized.crop((left, top, right, bottom))
                    tile.save(
                        col_dir / f"{global_y}.jpg",
                        quality=int(args.quality),
                        optimize=True,
                        progressive=True,
                    )

        print(f"  Wrote tiles to {level_dir}")

    print("\nDone.")


if __name__ == "__main__":
    main()
