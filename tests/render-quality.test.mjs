import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { APP_CONFIG } from '../frontend/public/js/config.js';

const source = fs.readFileSync(new URL('../frontend/public/js/globe.js', import.meta.url), 'utf8');
const constructor = source.slice(source.indexOf('  const viewer = new Cesium.Viewer('), source.indexOf('  viewer.targetFrameRate'));
const scaling = source.slice(source.indexOf('  viewer.imageryLayers.removeAll();'), source.indexOf('  viewer.scene.backgroundColor'));
const initialize = new Function('Cesium', 'appConfig', 'containerId', 'window', `${constructor}\n${scaling}\nreturn { viewer, idleResolutionScale, movingResolutionScale };`);

test('display density is applied exactly once and movement preserves image quality', () => {
  class Viewer {
    constructor(_id, options) { this.options = options; this.resolutionScale = 1; this.imageryLayers = { removeAll() {} }; }
  }
  const Cesium = { Viewer, EllipsoidTerrainProvider: class {} };
  for (const devicePixelRatio of [1, 1.25, 1.5, 2, 2.8125, 3, 4]) {
    const { viewer, idleResolutionScale, movingResolutionScale } = initialize(Cesium, APP_CONFIG, 'globe', { devicePixelRatio });
    // Cesium's default ignores DPR. Our device-pixel cap requires native pixels.
    assert.equal(viewer.options.useBrowserRecommendedResolution, false);
    assert.equal(idleResolutionScale * devicePixelRatio, Math.min(devicePixelRatio, 2));
    assert.equal(movingResolutionScale, idleResolutionScale);
    assert.ok(movingResolutionScale * devicePixelRatio >= 1);
  }
});
