import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
const source = fs.readFileSync(new URL("../frontend/public/js/globe.js", import.meta.url), "utf8");
function extractFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing ${name}`);
  const brace = source.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`unterminated ${name}`);
}
const cartographic = (p) => p;
const Cesium = { Math: { TWO_PI: Math.PI * 2 }, Cartographic: { fromCartesian: cartographic } };
const normalizeLongitudeNear = new Function("Cesium", `${extractFunction("normalizeLongitudeNear")}; return normalizeLongitudeNear;`)(Cesium);
const pointInRingRadians = new Function("Cesium", `${extractFunction("normalizeLongitudeNear")}; ${extractFunction("pointInRingRadians")}; return pointInRingRadians;`)(Cesium);
const polygonHierarchyContainsPoint = new Function("Cesium", `${extractFunction("normalizeLongitudeNear")}; ${extractFunction("pointInRingRadians")}; ${extractFunction("polygonHierarchyContainsPoint")}; return polygonHierarchyContainsPoint;`)(Cesium);
test("selection starts with no global Cesium data source attachments", () => {
  assert.doesNotMatch(source, /viewer\.dataSources\.add\(/);
  assert.match(source, /These sources are lookup collections, never attached to Cesium's renderer/);
});

test("dateline polygon CPU lookup handles 179/-179 longitude edges", () => {
  const d = Math.PI / 180;
  const ring = [[179 * d, 10 * d], [-179 * d, 10 * d], [-179 * d, 20 * d], [179 * d, 20 * d]].map(([longitude, latitude]) => ({ longitude, latitude }));
  assert.equal(pointInRingRadians(179.5 * d, 15 * d, ring), true);
  assert.equal(pointInRingRadians(0, 15 * d, ring), false);
  const hole = [{ longitude: 179.2 * d, latitude: 13 * d }, { longitude: 179.8 * d, latitude: 13 * d }, { longitude: 179.8 * d, latitude: 17 * d }, { longitude: 179.2 * d, latitude: 17 * d }];
  const hierarchy = { positions: ring, holes: [{ positions: hole, holes: [] }] };
  assert.equal(polygonHierarchyContainsPoint(hierarchy, 179.5 * d, 15 * d), false);
});

test("selection replaces multipart overlays and retains an outline when fill is off", () => {
  const shown = [];
  const piece = () => ({ polygon: { hierarchy: { positions: [] } }, show: true });
  const regions = { first: [piece(), piece()], second: [piece()] };
  const ctx = {
    selectedRegionId: null, selectionOverlayEntities: [], regionFillEnabled: true,
    MOON_SELECTION_ID: "moon", debugOptions: {}, appConfig: { styling: {} },
    enabled: true, shouldRenderEcoregions: () => ctx.enabled,
    getEntitiesForRegionId: (id) => regions[id] || [],
    applyEntityVisibility: (entity) => { entity.show = entity.allowed !== false; },
    selectionAccent: { withAlpha: () => "outline" }, selectionAccent2: {},
    blend: (color) => color, brighten: (color) => color, colorWithAlpha: (color) => color,
    requestRender() {},
    Cesium: { Color: { clone: (color) => color }, JulianDate: { now: () => 0 }, ArcType: { GEODESIC: 0 } },
    viewer: { entities: {
      add(entity) { shown.push(entity); return entity; },
      remove(entity) { shown.splice(shown.indexOf(entity), 1); }
    } }
  };
  const sync = new Function("ctx", `with (ctx) { ${extractFunction("clearSelectionOverlay")} ${extractFunction("syncSelectionOverlay")}; return syncSelectionOverlay; }`)(ctx);
  sync(); assert.equal(shown.length, 0);
  ctx.selectedRegionId = "first"; sync(); assert.equal(shown.length, 2);
  const old = [...shown];
  ctx.selectedRegionId = "second"; sync();
  assert.equal(shown.length, 1);
  assert.ok(old.every((entity) => !shown.includes(entity)));
  assert.equal(shown[0]._sourceEntity, regions.second[0]);
  ctx.regionFillEnabled = false; sync();
  assert.equal(shown.length, 1); assert.equal(shown[0].polygon.fill, false);
  assert.equal(shown[0].polygon.outline, true);
  regions.second[0].allowed = false; sync(); assert.equal(shown.length, 0);
  ctx.selectedRegionId = "moon"; sync(); assert.equal(shown.length, 0);
  ctx.selectedRegionId = "first"; ctx.enabled = false; sync(); assert.equal(shown.length, 0);
});

test("geographic lookup selects detached eligible geometry without GPU picking", () => {
  const point = { longitude: 0.5, latitude: 0.5 };
  const entity = { show: false, polygon: { hierarchy: { positions: [
    { longitude: 0, latitude: 0 }, { longitude: 1, latitude: 0 },
    { longitude: 1, latitude: 1 }, { longitude: 0, latitude: 1 }
  ] } } };
  const ds = { show: true, entities: { values: [entity] } };
  class Cartesian3 { static normalize(value, out) { return out; } }
  const ctx = {
    Cesium: { ...Cesium, Cartesian3, JulianDate: { now: () => 0 } },
    shouldRenderEcoregions: () => true, isMarineOnlyDatasetMode: () => false,
    isCombinedDatasetMode: () => false, activeRealmDataSource: null,
    getActiveLandGlobalDataSource: () => ds, getActiveLakesGlobalDataSource: () => null,
    polygonHierarchyContainsPoint,
    applyEntityVisibility: (candidate) => { candidate.show = candidate.allowed !== false; },
    viewer: { camera: { getPickRay: () => ({}) }, scene: { globe: { pick: () => point } } }
  };
  const hit = new Function("ctx", `with (ctx) { ${extractFunction("findEntityByGlobeHit")}; return findEntityByGlobeHit; }`)(ctx);
  assert.equal(hit({}), entity);
  entity.allowed = false; assert.equal(hit({}), null);
  entity.allowed = true; point.longitude = 2; assert.equal(hit({}), null);
});
