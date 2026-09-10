const { chromium } = require(process.env.MOTHERWORLD_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve('frontend/public');
// Run with MOTHERWORLD_PLAYWRIGHT pointing at an installed Playwright package and
// MOTHERWORLD_BROWSER at a Chromium executable. All generated artifacts stay on F:.
(async () => {
  const requests = [];
  const server = http.createServer((req, res) => {
    const url = req.url.split('?')[0]; requests.push(url);
    if (url === '/') { res.setHeader('Content-Type', 'text/html'); return res.end('<style>html,body{margin:0}#globe{width:100vw;height:100vh}.cesium-widget,.cesium-widget canvas{width:100%;height:100%}</style><div id="globe"></div>'); }
    const file = url === '/Cesium.js' ? path.resolve('.cache/render-quality-check/Cesium.js')
      : url.startsWith('/js/sphericalTileLod.js') && process.env.MOTHERWORLD_LOD_BASELINE ? path.resolve(process.env.MOTHERWORLD_LOD_BASELINE)
      : path.join(root, decodeURIComponent(url));
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.webp') ? 'image/webp' : 'application/octet-stream');
    const send = () => fs.createReadStream(file).on('error', () => { res.statusCode = 404; res.end(); }).pipe(res);
    // Stagger responses so decoded tiles arrive on different animation frames.
    if (url.includes('/tiles/')) setTimeout(send, 80 + (requests.length % 5) * 60); else send();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: process.env.MOTHERWORLD_BROWSER, headless: true, args: ['--enable-unsafe-swiftshader'] });
  try {
    const page = await browser.newPage({ viewport: process.env.MOTHERWORLD_TEST_MOBILE ? { width: 390, height: 650 } : { width: 800, height: 600 }, deviceScaleFactor: Number(process.env.MOTHERWORLD_TEST_DPR || 1) });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error' && !m.text().includes('404')) errors.push(m.text()); });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(() => window.CESIUM_BASE_URL = 'https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/');
    await page.addScriptTag({ url: '/Cesium.js' });
    await page.evaluate(async () => {
      const { createSkyDomeController } = await import('/js/skyDome.js');
      const { APP_CONFIG } = await import('/js/config.js');
      window.v = new Cesium.Viewer('globe', { baseLayer: false, animation: false, timeline: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false, fullscreenButton: false, useBrowserRecommendedResolution: false, contextOptions: { webgl: { preserveDrawingBuffer: true } } });
      v.scene.globe.show = v.scene.skyBox.show = v.scene.skyAtmosphere.show = v.scene.sun.show = v.scene.moon.show = false;
      v.camera.frustum.far = 2e9;
      v.clock.currentTime = Cesium.JulianDate.fromIso8601('2026-09-10T08:00:00Z'); v.clock.shouldAnimate = false;
      window.turn = angle => v.camera.setView({ destination: new Cesium.Cartesian3(7000000, 0, 0), orientation: { direction: new Cesium.Cartesian3(Math.sin(angle), Math.cos(angle), 0), up: new Cesium.Cartesian3(0, 0, 1) } });
      turn(0);
      window.samples = { frames: 0, whiteFrames: 0, placeholderFrames: 0, maxWhiteFraction: 0, maxCached: 0, maxInFlight: 0 };
      const canvas = document.createElement('canvas'); canvas.width = 160; canvas.height = 120;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      v.scene.postRender.addEventListener(() => {
        samples.frames++;
        ctx.drawImage(v.scene.canvas, 0, 0, 160, 120);
        const pixels = ctx.getImageData(0, 0, 160, 120).data; let white = 0;
        for (let i = 0; i < pixels.length; i += 4) if (pixels[i] > 240 && pixels[i+1] > 240 && pixels[i+2] > 240) white++;
        const fraction = white / (pixels.length / 4); samples.maxWhiteFraction = Math.max(samples.maxWhiteFraction, fraction);
        if (fraction > .05) samples.whiteFrames++;
        function inspect(collection) {
          if (collection.show === false) return;
          for (let i = 0; i < collection.length; i++) {
            const p = collection.get(i); if (p.show === false) continue;
            if (p instanceof Cesium.PrimitiveCollection) inspect(p);
            const m = p.appearance?.material;
            if (m?.uniforms.image instanceof HTMLImageElement && m._textures.image === v.scene.context.defaultTexture) samples.placeholderFrames++;
          }
        }
        inspect(v.scene.primitives);
        if (window.sky) { const s = sky.getStateForDebug().tiles; samples.maxCached = Math.max(samples.maxCached, s.cachedTiles); samples.maxInFlight = Math.max(samples.maxInFlight, s.inFlight); }
      });
      window.sky = createSkyDomeController({ viewer: v, appConfig: APP_CONFIG, requestRender: () => v.scene.requestRender() });
    });
    await page.waitForFunction(() => sky.getStateForDebug().tiles.overviewReady, null, { timeout: 45000 });
    for (let i = 0; i < 24; i++) { await page.evaluate(i => turn(i * Math.PI / 6), i); await page.waitForTimeout(170); }
    await page.evaluate(() => turn(0));
    await page.waitForFunction(() => { const s = sky.getStateForDebug().tiles; return s.visibleTiles > 0 && s.inFlight === 0 && s.queued === 0; }, null, { timeout: 45000 });
    const result = await page.evaluate(() => ({ ...samples, final: sky.getStateForDebug().tiles }));
    console.log(JSON.stringify(result));
    assert.ok(result.frames > 20); assert.equal(result.placeholderFrames, 0, 'Visible materials must never use Cesium white placeholders');
    assert.equal(result.whiteFrames, 0, 'No broad white flashes while loading or turning');
    assert.ok(result.maxCached <= 40); assert.ok(result.maxInFlight <= 4); assert.deepEqual(errors, []);
    assert.ok(requests.includes('/assets/sky/tiles/overview-4k.webp'));
    assert.ok(!requests.some(url => url.includes('starmap_2020_8k')));
    await page.evaluate(() => { sky.destroy(); v.destroy(); });
  } finally { await browser.close(); server.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
