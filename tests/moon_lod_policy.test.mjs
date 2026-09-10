import test from "node:test";
import assert from "node:assert/strict";
import { getMoonShadowPolicy, chooseMoonSource } from "../frontend/public/js/moonLodPolicy.js";

test("Moon shadow policy endpoints and midpoint", () => {
  assert.equal(getMoonShadowPolicy({ cameraMoonDistance: 1.5, displayRadius: 1 }).ambient, 1);
  assert.ok(Math.abs(getMoonShadowPolicy({ cameraMoonDistance: 3, displayRadius: 1 }).ambient - 0.025) < 1e-9);
  const mid = getMoonShadowPolicy({ cameraMoonDistance: 2.25, displayRadius: 1 });
  assert(mid.ambient < 1 && mid.ambient > 0.025);
});

test("invalid thresholds fall back safely", () => {
  const policy = getMoonShadowPolicy({ cameraMoonDistance: 2, displayRadius: 1, config: { moonLightingDayOnlyAltitudeRadii: -1, moonLightingFullShadowAltitudeRadii: "bad" } });
  assert.equal(policy.dayOnlyAltitudeRadii, 0.5);
  assert.equal(policy.fullShadowAltitudeRadii, 2);
});

test("source choice selects built in tiled pyramids", () => {
  const natural = chooseMoonSource();
  assert.match(natural.urlTemplate, /natural\/\{z\}/);
  const geology = chooseMoonSource({ mode: "geology", config: { moonGeologyTextureUrl: "./assets/moon/selenology/moon-geology-4k.webp" } });
  assert.equal(geology.maxLevel, 3);
  const custom = chooseMoonSource({ mode: "geology", custom: { base: "custom.webp" } });
  assert.equal(custom.urlTemplate, null);
});

test('custom lunar maps retain their own high-resolution URL and never match bundled tiles by basename', () => {
  const source = chooseMoonSource({mode: 'geology', anchorMode: 'moon', custom: {base: 'https://example.org/base.webp', hiRes: 'https://example.org/moon-geology-8k.webp'}});
  assert.equal(source.baseTextureUrl, 'https://example.org/moon-geology-8k.webp');
  assert.equal(source.urlTemplate, null);
});
