# Rendering quality regression — 2026-09-09

The old high-density display cap divided `resolutionScale` by `devicePixelRatio`, while Cesium's default `useBrowserRecommendedResolution: true` ignored that same device pixel ratio. This applied the cap to CSS pixels instead of device pixels, producing a canvas smaller than its displayed size. Camera movement then reduced it by another 25%.

The fix explicitly enables device-pixel rendering (`useBrowserRecommendedResolution: false`), caps effective density at 2 device pixels per CSS pixel, and keeps the same density during movement. This retains a bounded GPU workload while eliminating the unintended below-CSS-resolution image. FXAA and the source JPEG imagery are unchanged.

For a 390 CSS-pixel-wide viewport at DPR 2.8125, the previous idle buffer was about 173 pixels wide. The corrected buffer is 780 pixels wide, including while moving. A DPR 1.5 display renders 585 pixels across; DPR 1 renders 390.

Validation:

- Actual Cesium 1.118 in headless Edge: checked drawing-buffer dimensions at DPR 1, 1.5 and 2.8125, at rest and while applying the movement scale.
- Regression test executes the actual viewer construction and scaling code at seven display densities.
- Blue Marble tile inventory: detail z5 has 2,048 tiles, ultra z6 has 8,192, z7 has 32,768; sampled images are 256×256 and match the GeographicTilingScheme configuration. Compression quality is 88/84/82 for base/detail/ultra.
- The screenshot with a panel titled `PERF` is from the older HTML. Current HTML starts the `Performance` panel hidden and supplies a close button. A fresh versioned URL loads the new page; already-open older documents need reloading.

Reference: [CesiumWidget resolution settings](https://cesium.com/learn/cesiumjs/ref-doc/CesiumWidget.html#useBrowserRecommendedResolution).
