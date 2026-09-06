#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build global Black Marble assets: base image + 3km Cesium geographic tiles."
    )
    parser.add_argument("--base-source", default="blackmarble/BlackMarble_2016_01deg_geo.tif")
    parser.add_argument("--detail-source", default="blackmarble/BlackMarble_2016_3km_geo.tif")
    parser.add_argument("--out-dir", default="frontend/public/assets/imagery/black-marble-tiles")
    parser.add_argument("--levels", default="5", help="Comma-separated Cesium levels to generate")
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--quality", type=int, default=84)
    parser.add_argument("--tile-format", choices=["jpg", "webp"], default="webp")
    parser.add_argument(
        "--base-image",
        default="frontend/public/assets/imagery/black-marble-01deg.webp",
        help="Exported global 01deg base image path",
    )
    return parser.parse_args()


def level_dims(level: int, tile_size: int) -> tuple[int, int, int, int]:
    tiles_x = 2 ** (level + 1)
    tiles_y = 2**level
    return tiles_x, tiles_y, tiles_x * tile_size, tiles_y * tile_size


def save_image(img: Image.Image, path: Path, fmt: str, quality: int) -> None:
    if fmt == "webp":
        img.save(path, format="WEBP", quality=int(quality), method=6)
        return
    img.save(path, format="JPEG", quality=int(quality), optimize=True, progressive=True)


def main() -> None:
    args = parse_args()
    Image.MAX_IMAGE_PIXELS = None
    project_root = Path(__file__).resolve().parents[1]
    base_source_path = (project_root / args.base_source).resolve()
    detail_source_path = (project_root / args.detail_source).resolve()
    out_root = (project_root / args.out_dir).resolve()
    base_image_path = (project_root / args.base_image).resolve() if args.base_image else None

    if not base_source_path.exists():
        raise FileNotFoundError(f"Base source not found: {base_source_path}")
    if not detail_source_path.exists():
        raise FileNotFoundError(f"Detail source not found: {detail_source_path}")

    levels = sorted({int(x.strip()) for x in str(args.levels).split(",") if x.strip() != ""})
    if not levels:
        raise ValueError("No levels specified")
    if any(level < 0 for level in levels):
        raise ValueError("Levels must be >= 0")

    print(f"Loading base source: {base_source_path}")
    base_src = Image.open(base_source_path).convert("RGB")
    print(f"Base size: {base_src.width} x {base_src.height}")
    if base_image_path is not None:
        base_image_path.parent.mkdir(parents=True, exist_ok=True)
        base_fmt = "webp" if base_image_path.suffix.lower() == ".webp" else "jpg"
        save_image(base_src, base_image_path, base_fmt, quality=86)
        print(f"Wrote base image: {base_image_path}")

    print(f"Loading detail source: {detail_source_path}")
    src = Image.open(detail_source_path).convert("RGB")
    print(f"Detail size: {src.width} x {src.height}")

    out_root.mkdir(parents=True, exist_ok=True)

    for level in levels:
        tiles_x, tiles_y, _, _ = level_dims(level, args.tile_size)
        x_start = 0
        x_count = tiles_x
        y_start = 0
        y_count = tiles_y

        target_w = tiles_x * args.tile_size
        target_h = tiles_y * args.tile_size
        resized = src.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)

        level_dir = out_root / str(level)
        if level_dir.exists():
            for stale in level_dir.rglob("*.jpg"):
                stale.unlink()
            for stale in level_dir.rglob("*.webp"):
                stale.unlink()
        level_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"Level {level}: x[{x_start}..{tiles_x - 1}] "
            f"y[{y_start}..{tiles_y - 1}] "
            f"target={target_w}x{target_h}"
        )

        for lx in range(x_count):
            gx = x_start + lx
            col_dir = level_dir / str(gx)
            col_dir.mkdir(parents=True, exist_ok=True)
            left = lx * args.tile_size
            right = left + args.tile_size
            for ly in range(y_count):
                gy = y_start + ly
                top = ly * args.tile_size
                bottom = top + args.tile_size
                tile = resized.crop((left, top, right, bottom))
                save_image(tile, col_dir / f"{gy}.{args.tile_format}", args.tile_format, quality=args.quality)

    print("Done.")


if __name__ == "__main__":
    main()
