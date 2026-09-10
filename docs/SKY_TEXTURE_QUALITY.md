# Sky texture quality

## Issue and correction (2026-09-10)

The sky used `starmap_2020_8k.jpg`, an 8192×4096, 8-bit, quality-92 JPEG with 4:4:4 chroma sampling. Its sparse high-contrast stars and faint Milky Way detail expose lossy block/ringing artifacts. The deployed JPEG differs from the source at a measured normalized RMSE of about 0.00421 (PSNR 47.52 dB); this is source-image loss, not evidence of runtime resizing.

The configuration now uses `starmap_2020_8k.lossless.webp`, regenerated from the existing `Starmap/starmap_2020_8k.exr`. It keeps the existing level/color conversion and 8192×4096 orientation. No resizing, sharpening, denoising or invented stars were introduced. It is lossless relative to the converted 8-bit image, not the original HDR precision.

Reproduction with ImageMagick (set `MAGICK_TEMPORARY_PATH`, `TEMP` and `TMP` to a scratch directory on the project drive):

```powershell
magick ./Starmap/starmap_2020_8k.exr -auto-level -colorspace sRGB -depth 8 -define webp:lossless=true -define webp:method=4 ./frontend/public/assets/sky/starmap_2020_8k.lossless.webp
```

## Verification

- Decoded WebP pixel signature equals the source passed through the same level/color/8-bit conversion: `ecbf090aac020c86bd009739b68a1116b547cfca2af7cdfd7f036d4fbaa7831e` (ImageMagick `%#`).
- Actual Cesium 1.118 browser rendering uploaded both old/new textures at 8192×4096, with linear minification and magnification. The old texture was not silently downgraded or nearest-neighbor sampled.
- The corrected asset rendered successfully at DPR 1 and 2 with 1100×750 and 2200×1500 drawing buffers; the updated sky screenshot was visually inspected. This was a local headless Edge test, not a physical-phone test.
- JPEG transfer size: 2,601,747 bytes. Lossless WebP: 9,913,534 bytes. Dimensions and decoded GPU texture allocation remain unchanged. Network startup cost increases; the image is fetched once and browser-cached.
- Entry/config cache markers use `20260910-sky1`; the new asset has a distinct URL so old JPEG cache entries cannot substitute for it.

This removes JPEG encoding artifacts. The underlying 8K map and 8-bit conversion still have finite spatial precision and dark tonal steps; this change does not add astronomical detail or solve every possible display/zoom artifact. EXR and legacy JPEG files are retained as source/comparison assets. Publication is tracked separately from local validation.
