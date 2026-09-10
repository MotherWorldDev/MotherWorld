# Sky and Moon tile LOD

## Behavior (2026-09-10)

The sky uses a 4096×2048 lossless overview (half the 8K source width and height); the default Moon proxy uses a 1024×512 overview. Both add visible equirectangular detail tiles. Whole 8K sky / 4K Moon / 8K geology images are no longer requested by these default render paths. An overview remains available during tile loading and failed requests. The sky skips detail outside the camera frustum and behind the Earth's angular disk. Moon tiles additionally skip the hidden hemisphere. The Moon remains visible during partial Earth occultation; only full angular containment by a nearer Earth hides it.

Moon detail selection follows projected pixel size, including drawing-buffer density. Sky detail uses level 3 over the permanent 4K fallback, so a coarser tile can never obscure a sharper overview and zoom changes do not churn through sky parent/child levels. Sky/geology reach the existing 8192×4096 source detail (level 3); natural/eclipse Moon reach their existing 4096×2048 source detail (level 2). A distant Moon uses only the overview. LOD does not invent finer source detail.

Each tile has a 512×512 interior and two-pixel gutters (516×516 texture). Longitude gutters wrap; polar gutters clamp. Geology downsampling uses nearest neighbors to preserve categorical palette colors; natural imagery uses Lanczos. Every stored tile is lossless WebP. Sphere coordinates match Cesium's existing mapping: u=0 at +X, u=0.25 at +Y, image-top at the north pole.

The renderer limits each body to four concurrent requests. Sky caches at most 40 detail textures and Moon at most 48; changing the Moon view aborts stale requests. Sky requests already in flight finish into the bounded cache for quick turn reversals; queued requests track the current view. Hiding either body or changing its source still cancels pending work. Hidden detail is released after 15 seconds, with materials/GPU textures explicitly destroyed. Errors retry after a 10-second backoff on a subsequent render. Source generations prevent old natural/geology/eclipse responses from overwriting the current mode. Resident parents remain fallback until child branches are ready; the overview covers missing/evicted regions. Sky tiles render opaque with depth writes to prevent translucent overview stars ghosting through detail. Detail sits at 0.995 of the dome radius, safely inside the coarser overview facets; this prevents the overview mesh from obscuring patches. Optional constellation overlays sit closer than the tiled dome and retain their existing on-demand visibility.

## Moon illumination

The phase is unchanged at lunar surface altitudes of six display radii or greater (about 10,424 km at natural scale). Between six radii and 1.5 radii (about 2,606 km), a smoothstep increases ambient illumination from 0.025 to 1. Close inspection is fully lit, matching Earth's close-up inspection behavior. Both surface-altitude thresholds are three times the original values. The thresholds scale with the displayed Moon radius and are configurable in `config.js`. Natural, eclipse and geology tiles share the same policy. The optional legacy Cesium Moon path uses the same near/far cutoff as a boolean lighting switch; smooth fading is provided by the default proxy renderer.

Custom geology URLs remain supported as whole-image fallbacks; only the bundled maps have prebuilt tile pyramids. The custom high-resolution URL is retained when the Moon is selected. Habitat/Earth Health and climate workers are independent of these rendering changes.

## Build and verification

Generate tiles from the existing source images:

```powershell
python scripts/build_celestial_tiles.py --root F:/BiomeSummary --set all
```

Each tile root includes a manifest with source SHA-256, dimensions, levels and tile count. Sky/geology each contain 170 tiles; natural/eclipse each contain 42, plus an overview per set. The old 1K sky overview remains as an unused compatibility asset; new builds and the runtime select `overview-4k.webp`. Source images are retained for reproducible rebuilding.

Validation includes 67 frontend tests, plus three Python asset tests. The new checks cover Moon fade thresholds, custom map selection, original sphere orientation, conservative horizon/occlusion rules, dateline/pole gutters and highest-level source-pixel identity. Local actual Cesium 1.118 browser checks established:

- A representative 800×600 sky view requested one overview and 16 level-3 tiles; pointing at an Earth-filled view selected zero sky detail tiles.
- A distant Moon requested only its overview. A close-up test loaded 12 visible level-2 tiles within its configured 12-tile test budget; hidden detail was released after the expiry interval.
- The full globe module's Moon anchor worked; close-up ambient reached 1 with 10 natural detail tiles. Switching to geology loaded 14 level-3 tiles and switching back restored natural imagery. No whole high-resolution sky/Moon/geology textures were fetched.
- Browser shader compilation completed without errors, and sky/Moon output was visually inspected. The browser harness exercised network loading and material destruction, not only pure mocks. A 390×650, DPR-2 mobile viewport also passed the full Moon anchor/zoom/geology-switch checks (10 visible close-up natural tiles and 10 geology tiles). Physical mobile hardware performance remains unmeasured.

## White-flash regression and correction

The first tiled release exposed decoded images before Cesium 1.118 had uploaded their GPU textures. The first material update queues an HTML image; the next uploads it. Showing the primitive between those steps rendered Cesium's white default texture. End-state screenshots missed the transient fault.

The controller now performs both material updates while hidden and verifies the uploaded texture identity and dimensions before adding a visible primitive. This check uses Cesium's internal `_textures.image` slot and must be revalidated when upgrading Cesium. The same correction applies to sky, natural Moon, eclipse and geology images.

`tests/celestial_loading.browser.cjs` exercises actual Cesium with staggered network responses and repeated rotations. It samples every rendered frame for broad white areas and visible placeholder textures, and checks cache/concurrency limits. The original implementation reproduced 24 white frames and 65 visible placeholder-material observations in a short spin. Run with `MOTHERWORLD_PLAYWRIGHT` set to an installed Playwright package and `MOTHERWORLD_BROWSER` to a Chromium executable; it uses the local `.cache/render-quality-check/Cesium.js` (1.118). `MOTHERWORLD_TEST_DPR=2` exercises a denser canvas; combine it with `MOTHERWORLD_TEST_MOBILE=1` for a 390×650 phone viewport. After the correction, desktop and phone runs each passed 309 sampled frames with zero white flashes or visible placeholders, at most 40 cached tiles and four simultaneous requests. Temporary browser data must remain on F: via `TEMP`/`TMP`.

The sky's 4K fallback costs approximately 32 MiB of GPU RGBA texture memory, versus 128 MiB for a resident 8K texture. Its detail cache remains bounded at 40 tiles. This deliberate baseline keeps rapid turns sharp without loading the full-resolution sky everywhere.

Cache markers: `20260910-celestial2`.
