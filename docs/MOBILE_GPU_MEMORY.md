# Mobile GPU memory policy

## Report and scope

On 2026-09-10, the user reported that MotherWorld crashed after several minutes on a Samsung A17 running Chrome. GPU memory pressure is a plausible cause, not a confirmed diagnosis from that physical device. The previous defaults retained up to 1,200 Earth quadtree tiles and up to 40 sky / 48 Moon detail textures, including unused celestial textures for 15 seconds.

WebGL does not expose a portable query for available VRAM. We use bounded caches and explicitly delete unused resources instead of waiting for a memory-pressure signal. Deletion releases application ownership; the graphics driver controls when pending GPU work permits the physical allocation to be reclaimed. See [MDN's WebGL memory guidance](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API/WebGL_best_practices#delete_objects_eagerly).

## Implemented behavior

- Mobile detection uses UAData, Android/iOS user agents, iPadOS desktop identification, or a coarse pointer with a small viewport. A large desktop touchscreen keeps desktop limits.
- Sky and Moon retain at most 24 detail tiles each on the GPU, with two simultaneous load/decode jobs per body. Unselected textures and geometry are destroyed on the next visibility update (normally within 160 ms). Already fetched images may finish decoding into the compressed cache, but are not uploaded if no longer needed.
- Each celestial controller has a 16 MiB encoded Blob LRU. Returning to a cached tile decodes its compressed image and uploads a fresh GPU texture without another download. Source switches retain encoded images but release old GPU resources. Decoded images are not kept as the offscreen cache.
- The sky retains the existing sharp 4096×2048 fallback while needed. Its full-resolution patches remain view-dependent. Moon and Earth occlusion remove hidden sky detail; an offscreen Moon releases its overview as well as detail.
- Earth has a 24-tile quadtree cache target, with sibling/ancestor preloading disabled. Cesium can exceed this target for visible tiles, required ancestors and active loads. A post-render trim releases eligible unused tiles even when the network queue is idle. This preserves visible detail instead of lowering display resolution.
- Hidden Earth imagery layers release their imagery references through Cesium's normal layer visibility handling. When Earth's bounding sphere leaves the view, all its imagery layers are hidden. Local Earth detail providers share another 16 MiB Blob cache and preserve Cesium request scheduling. Each provider permits at most four active fetch/decode jobs. Completed decoded-image deduplication used on desktop is bypassed on mobile.
- Backgrounding stops the render loop, clears celestial GPU resources, flushes Earth layer visibility changes, and trims unloadable Earth resources immediately. Returning restores resources on demand. Optional constellation overlays release their resources when disabled or backgrounded.
- The three compressed caches total at most 48 MiB. This is not a cap on the whole application's RAM: region data, active decoded images, Cesium overview images, framebuffers and other browser resources are additional. Earth overview providers and optional overlay images also benefit from the browser's own HTTP cache.
- Viewer destruction detaches the new listeners, stops its realtime clock, destroys the celestial controllers and clears the Earth Blob cache.

Visible images keep their previous resolution and the 2× display-density cap. The performance panel stays behind its existing button and now reports the mobile policy, Earth tile count, estimated celestial texture bytes, and observed WebGL context-loss events. These are application diagnostics, not a measurement of the device's remaining VRAM. Abrupt browser process termination cannot reliably emit a context-loss event.

## Validation

- Unit tests cover device detection, encoded byte limits and LRU eviction, image reuse, cancellation, request throttling, decode failure retries and object-URL revocation.
- `tests/celestial_loading.browser.cjs` runs actual Cesium 1.118 with delayed responses and rapid turns. Mobile checks require no inactive GPU detail entries, zero white flashes, and zero additional network requests after releasing and restoring the same view. One measured sky release removed 48,464,768 bytes of estimated RGBA texture allocations while retaining approximately 5.2 MiB of compressed images.
- `tests/mobile_gpu_memory.browser.cjs` runs a three-minute mobile-profile soak. It tracks actual WebGL texture creation/deletion, navigates Earth and natural/geology Moon views, backgrounds/resumes repeatedly, checks cache bounds, and requires every WebGL texture to be deleted when the viewer is destroyed.
- Final results: 80/80 unit tests passed. Desktop and mobile loading checks had zero white flashes; mobile restoration required zero extra downloads. The close-up soak completed 50 cycles in 183 seconds, with no context losses or console errors. Background GPU texture counts stayed at 15 Cesium/system textures throughout; sky/Moon texture allocations were zero. Viewer destruction left zero WebGL textures. Peak live texture count was 324 during Earth loading; visible and in-progress resources are not evicted prematurely.
- Browser tests run in desktop Chromium with an Android Chrome profile, a 390×650 viewport and DPR 2. They exercise the actual GPU resource lifecycle but cannot reproduce the Samsung A17's driver, available memory or OS process-killing policy. Physical-device validation remains necessary.

Run the browser tests with `MOTHERWORLD_PLAYWRIGHT` pointing to Playwright and `MOTHERWORLD_BROWSER` to a Chromium executable. They use a local Cesium 1.118 bundle at `.cache/render-quality-check/Cesium.js`. Set `TEMP` and `TMP` to `F:/BiomeSummary/.cache/tmp`. Set `MOTHERWORLD_TEST_MOBILE=1` and `MOTHERWORLD_TEST_DPR=2` for the loading regression. The soak defaults to 180,000 ms; `MOTHERWORLD_TEST_SOAK_MS` overrides it.

## Cesium upgrade checks

The project uses Cesium 1.118. The eager idle trim intentionally calls the internal `globe._surface._tileReplacementQueue.trimTiles(0)`, whose implementation protects tiles marked in the current frame and tiles ineligible for unloading. Background cleanup marks a new replacement frame before trimming, since nothing is being drawn. `imageryLayers._update()` flushes pending show/hide events while the tab's render loop is paused. Revalidate these internal hooks against the browser tests when upgrading Cesium. The public cache-size and show/hide settings remain in place if an internal hook is unavailable.

Release cache marker: `20260910-memory1`.
