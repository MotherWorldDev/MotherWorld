import test from "node:test";
import assert from "node:assert/strict";
import { createClimateExtrasDataService } from "../frontend/public/js/climateExtrasDataService.js";
import { createBiodiversityDataService } from "../frontend/public/js/biodiversityDataService.js";
import { createBiodiversityPanel } from "../frontend/public/js/biodiversityPanel.js";
import { createContaminantsDataService } from "../frontend/public/js/contaminantsDataService.js";
import { createContaminantsPanel } from "../frontend/public/js/contaminantsPanel.js";
import { createEnvironmentHealthPanel } from "../frontend/public/js/environmentHealthPanel.js";
import { createGeologyDataService } from "../frontend/public/js/geologyDataService.js";
import { createEarthHealth } from "../frontend/public/js/earthHealth.js";

const json = (data) => ({ ok: true, status: 200, json: async () => data });
const failure = (status) => ({ ok: false, status, statusText: `status ${status}` });
const tick = () => new Promise((resolve) => setImmediate(resolve));

function climateIndex(ids) {
  return { regions: Object.fromEntries(ids.map((id) => [id, { url: `land/${id}.climate.json` }])) };
}

function geologyIndex(ids) {
  return { regions: Object.fromEntries(ids.map((id) => [id, { url: `land/${id}.geology.json` }])) };
}

function biodiversityIndex(ids) {
  return { regions: Object.fromEntries(ids.map((id) => [id, { url: `land/${id}.biodiversity.json` }])) };
}

function contaminantsIndex(ids) {
  return { regions: Object.fromEntries(ids.map((id) => [id, { url: `marine/${id}.contaminants.json` }])) };
}

test("climate and geology services retry failures, deduplicate, and bound regional caches", async () => {
  let climateIndexAttempts = 0;
  let climateRegionAttempts = 0;
  const climateCalls = [];
  const climate = createClimateExtrasDataService({ climateExtrasCacheLimit: 2, climateExtrasBaseUrl: "./data/climate-extras/" }, async (url) => {
    climateCalls.push(url);
    if (url.includes("index")) {
      if (++climateIndexAttempts === 1) throw new Error("offline");
      return json(climateIndex(["eco_1", "eco_2", "eco_3"]));
    }
    if (url.includes("eco_1")) {
      if (++climateRegionAttempts === 1) return failure(503);
      return json({ regionId: "eco_1" });
    }
    return json({ regionId: url.match(/eco_\d+/)?.[0] });
  });
  await assert.rejects(climate.loadRegion("eco_1"), /offline/);
  await assert.rejects(climate.loadRegion("eco_1"), /503/);
  assert.equal((await climate.loadRegion("eco_1")).regionId, "eco_1");
  await climate.loadRegion("eco_2");
  await climate.loadRegion("eco_3");
  await climate.loadRegion("eco_1");
  assert.equal(climateCalls.filter((url) => url.includes("eco_1")).length, 3);

  let geologyIndexAttempts = 0;
  let geologyRegionAttempts = 0;
  const geologyCalls = [];
  const geology = createGeologyDataService({ geologyCacheLimit: 1, geologyBaseUrl: "./data/geology/" }, async (url) => {
    geologyCalls.push(url);
    if (url.includes("index")) {
      if (++geologyIndexAttempts === 1) return failure(503);
      return json(geologyIndex(["eco_1", "eco_2"]));
    }
    if (url.includes("eco_1")) {
      if (++geologyRegionAttempts === 1) return failure(503);
      return json({ regionId: "eco_1" });
    }
    return json({ regionId: "eco_2" });
  });
  await assert.rejects(geology.loadRegion("eco_1"), /503/);
  await assert.rejects(geology.loadRegion("eco_1"), /503/);
  assert.equal((await geology.loadRegion("eco_1")).regionId, "eco_1");
  await geology.loadRegion("eco_2");
  await geology.loadRegion("eco_1");
  assert.equal(geologyCalls.filter((url) => url.includes("eco_1")).length, 3);
});

test("climate and geology cold-index concurrent loads issue one regional request", async () => {
  let releaseClimateIndex;
  const climateIndexGate = new Promise((resolve) => { releaseClimateIndex = resolve; });
  let climateIndexRequests = 0;
  let climateRegionRequests = 0;
  const climate = createClimateExtrasDataService({}, async (url) => {
    if (url.includes("index")) {
      climateIndexRequests += 1;
      await climateIndexGate;
      return json(climateIndex(["eco_1"]));
    }
    climateRegionRequests += 1;
    return json({ regionId: "eco_1" });
  });
  const climateFirst = climate.loadRegion("eco_1");
  const climateSecond = climate.loadRegion("eco_1");
  await tick();
  assert.equal(climateIndexRequests, 1);
  assert.equal(climateRegionRequests, 0);
  releaseClimateIndex();
  assert.deepEqual(await Promise.all([climateFirst, climateSecond]), [{ regionId: "eco_1" }, { regionId: "eco_1" }]);
  assert.equal(climateRegionRequests, 1);

  let releaseGeologyIndex;
  const geologyIndexGate = new Promise((resolve) => { releaseGeologyIndex = resolve; });
  let geologyIndexRequests = 0;
  let geologyRegionRequests = 0;
  const geology = createGeologyDataService({}, async (url) => {
    if (url.includes("index")) {
      geologyIndexRequests += 1;
      await geologyIndexGate;
      return json(geologyIndex(["eco_1"]));
    }
    geologyRegionRequests += 1;
    return json({ regionId: "eco_1" });
  });
  const geologyFirst = geology.loadRegion("eco_1");
  const geologySecond = geology.loadRegion("eco_1");
  await tick();
  assert.equal(geologyIndexRequests, 1);
  assert.equal(geologyRegionRequests, 0);
  releaseGeologyIndex();
  assert.deepEqual(await Promise.all([geologyFirst, geologySecond]), [{ regionId: "eco_1" }, { regionId: "eco_1" }]);
  assert.equal(geologyRegionRequests, 1);
});

test("biodiversity and contaminant services retry, deduplicate, and bound regional caches", async () => {
  let biodiversityIndexAttempts = 0;
  let biodiversityRegionAttempts = 0;
  const biodiversityCalls = [];
  const biodiversity = createBiodiversityDataService({ biodiversityCacheLimit: 2, biodiversityBaseUrl: "./data/biodiversity/" }, async (url) => {
    biodiversityCalls.push(url);
    if (url.includes("index")) {
      if (++biodiversityIndexAttempts === 1) throw new Error("offline");
      return json(biodiversityIndex(["eco_1", "eco_2", "eco_3"]));
    }
    if (url.includes("eco_1")) {
      if (++biodiversityRegionAttempts === 1) return failure(503);
      return json({ regionId: "eco_1" });
    }
    return json({ regionId: url.match(/eco_\d+/)?.[0] });
  });
  await assert.rejects(biodiversity.loadRegion("eco_1"), /offline/);
  await assert.rejects(biodiversity.loadRegion("eco_1"), /503/);
  assert.equal((await biodiversity.loadRegion("eco_1")).regionId, "eco_1");
  await biodiversity.loadRegion("eco_2");
  await biodiversity.loadRegion("eco_3");
  await biodiversity.loadRegion("eco_1");
  assert.equal(biodiversityCalls.filter((url) => url.includes("eco_1")).length, 3);

  let contaminantsIndexRequests = 0;
  let contaminantsRegionRequests = 0;
  let releaseIndex;
  const indexGate = new Promise((resolve) => { releaseIndex = resolve; });
  const contaminants = createContaminantsDataService({}, async (url) => {
    if (url.includes("index")) {
      contaminantsIndexRequests += 1;
      await indexGate;
      return json(contaminantsIndex(["marine_1"]));
    }
    contaminantsRegionRequests += 1;
    return json({ regionId: "marine_1" });
  });
  const first = contaminants.loadRegion("marine_1");
  const second = contaminants.loadRegion("marine_1");
  await tick();
  assert.equal(contaminantsIndexRequests, 1);
  assert.equal(contaminantsRegionRequests, 0);
  releaseIndex();
  assert.deepEqual(await Promise.all([first, second]), [{ regionId: "marine_1" }, { regionId: "marine_1" }]);
  assert.equal(contaminantsRegionRequests, 1);
});

function diagnosticDom() {
  const nodes = new Map();
  let activePanel = "biodiversity";
  const make = (id) => {
    const listeners = {};
    return {
      id,
      hidden: false,
      textContent: "",
      innerHTML: "",
      dataset: {},
      listeners,
      addEventListener(name, fn) { listeners[name] = fn; },
      setAttribute() {},
      classList: {
        contains: (name) => name === "is-active" && activePanel === id.replace("sidebar-tab-", ""),
        add() {},
        toggle() {},
      },
    };
  };
  for (const id of [
    "sidebar-tab-biodiversity", "sidebar-biodiversity-panel", "biodiversity-region-title", "biodiversity-status", "biodiversity-retry", "biodiversity-content",
    "sidebar-tab-contaminants", "sidebar-contaminants-panel", "contaminants-region-title", "contaminants-status", "contaminants-retry", "contaminants-content",
  ]) nodes.set(id, make(id));
  nodes.get("sidebar-biodiversity-panel").hidden = false;
  nodes.get("sidebar-contaminants-panel").hidden = false;
  return {
    getElementById: (id) => nodes.get(id),
    setActive: (name) => { activePanel = name; },
    node: (id) => nodes.get(id),
  };
}

test("biodiversity with no native cells stays unavailable and contaminants preserve detection semantics", async () => {
  const original = { document: globalThis.document, window: globalThis.window };
  const document = diagnosticDom();
  globalThis.document = document;
  globalThis.window = { requestAnimationFrame: (fn) => fn(), addEventListener() {} };
  const noNative = {
    regionId: "eco_112",
    regionName: "Mangroves",
    summary: {
      historicalMammals: {
        presentNaturalSpeciesCount: 0,
        currentSpeciesCount: 0,
        locallyLostSpeciesCount: 0,
        faunalRetentionPct: null,
        rangeOccupancyRetentionPct: null,
        nativeRasterCellCount: 0,
        coverageStatus: "no_native_grid_cell",
      },
    },
    providers: { phylacine: { source: { label: "PHYLACINE", repository: "https://example.test/phylacine" } } },
  };
  const covered = {
    regionId: "eco_0",
    summary: {
      historicalMammals: {
        presentNaturalSpeciesCount: 362,
        currentSpeciesCount: 341,
        locallyLostSpeciesCount: 24,
        faunalRetentionPct: 94.2,
        rangeOccupancyRetentionPct: 101.1,
        nativeRasterCellCount: 198,
        coverageStatus: "covered",
        exampleLocallyLostSpecies: ["Example mammal"],
      },
    },
    providers: { phylacine: { source: { label: "PHYLACINE", repository: "https://example.test/phylacine" } } },
  };
  const biodiversityPanel = createBiodiversityPanel({ biodiversityIndexUrl: "./biodiversity.index.json", biodiversityBaseUrl: "./data/biodiversity/" }, async (url) => {
    if (url.includes("index")) return json(biodiversityIndex(["eco_112", "eco_0"]));
    return json(url.includes("eco_112") ? noNative : covered);
  });
  try {
    biodiversityPanel.setRegion({ id: "eco_112", name: "Mangroves" });
    await tick();
    const biodiversityHtml = document.node("biodiversity-content").innerHTML;
    assert.match(biodiversityHtml, /Insufficient native spatial resolution/);
    assert.doesNotMatch(biodiversityHtml, /0[^<]{0,12}mammal/i);

    biodiversityPanel.setRegion({ id: "eco_0", name: "Tropical forest" });
    await tick();
    assert.match(document.node("biodiversity-content").innerHTML, /362/);
    assert.match(document.node("biodiversity-content").innerHTML, /Present natural species/);

    document.setActive("contaminants");
    const contaminantsPanel = createContaminantsPanel({ contaminantsIndexUrl: "./contaminants.index.json", contaminantsBaseUrl: "./data/contaminants/" }, async (url) => {
      if (url.includes("index")) return json(contaminantsIndex(["marine_1"]));
      return json({
        regionId: "marine_1",
        coverageWarning: "Station records are uneven.",
        categories: {
          microplastics: {
            label: "Microplastics",
            type: "measurements",
            sampleCount: 3,
            quantifiedCount: 3,
            detectedCount: 1,
            stationCount: 2,
            detectionBasis: "Positive reported concentration; NOAA export has no explicit nondetect flag.",
            protocolFields: { marineSetting: "coastal", meshSizeMm: 333, samplingMethod: "net tow" },
            analytes: [{ name: "Plastic particles", unit: "items/m³", sampleCount: 3, quantifiedCount: 3, detectedCount: 1 }],
          },
        },
        sources: [{ label: "NOAA", url: "https://example.test/noaa" }],
      });
    });
    contaminantsPanel.setRegion({ id: "marine_1", name: "Open ocean", isMarine: true });
    await tick();
    const contaminantsHtml = document.node("contaminants-content").innerHTML;
    assert.match(contaminantsHtml, /Quantified values/);
    assert.match(contaminantsHtml, /Positive detections/);
    assert.match(contaminantsHtml, /no explicit nondetect flag/);
    assert.match(contaminantsHtml, /Sampling method/);
  } finally {
    Object.assign(globalThis, original);
  }
});

function healthDom() {
  const nodes = new Map();
  const make = () => {
    const listeners = {};
    return {
      hidden: false,
      textContent: "",
      innerHTML: "",
      dataset: {},
      listeners,
      addEventListener(name, fn) { listeners[name] = fn; },
      setAttribute() {},
      classList: { contains: () => false, add() {}, toggle() {} },
      querySelector: () => null,
      scrollIntoView() {},
      style: { setProperty() {} },
    };
  };
  for (const id of ["sidebar-tab-health", "sidebar-health-panel", "regional-health-status", "regional-health-title", "regional-health-cards", "regional-health-note", "earth-health-root", "earth-health-button", "earth-health-score", "earth-health-status", "earth-health-coverage", "earth-health-panel", "earth-health-panel-score", "earth-health-panel-status", "earth-health-families", "earth-health-trend", "earth-health-method", "earth-health-close"]) nodes.set(id, make());
  nodes.get("sidebar-health-panel").hidden = false;
  return {
    getElementById: (id) => nodes.get(id),
    addEventListener() {},
    createElement: () => {
      const node = make();
      node.querySelector = () => make();
      return node;
    },
    node: (id) => nodes.get(id),
  };
}

test("regional health preserves null scores, retries, bounds cache, and ignores stale 404 responses", async () => {
  const original = { document: globalThis.document, window: globalThis.window, CSS: globalThis.CSS };
  const document = healthDom();
  globalThis.document = document;
  globalThis.window = { requestAnimationFrame: (fn) => fn(), addEventListener() {} };
  globalThis.CSS = { escape: (value) => value };
  const calls = [];
  let resolveFirst;
  const panel = createEnvironmentHealthPanel({ regionalHealthCacheLimit: 1, regionalHealthBaseUrl: "./data/indices/regions/" }, async (url) => {
    calls.push(url);
    if (url.includes("eco_1")) return new Promise((resolve) => { resolveFirst = resolve; });
    if (url.includes("eco_2")) return json({ regionName: "Second", families: [{ id: "air", score: null, coverage: null, components: [{ id: "raw", score: null, raw: null }] }] });
    return json({ regionName: "Third", families: [] });
  });
  try {
    panel.setRegion({ id: "eco_1", name: "First" });
    await tick();
    panel.setRegion({ id: "eco_2", name: "Second" });
    await tick();
    resolveFirst({ ok: false, status: 404 });
    await tick();
    assert.equal(document.node("regional-health-title").textContent, "Second");
    assert.equal(document.node("regional-health-status").hidden, true);
    assert.match(document.node("regional-health-cards").innerHTML, /coverage —/);
    assert.match(document.node("regional-health-cards").innerHTML, />—<\/strong>/);
    assert.doesNotMatch(document.node("regional-health-cards").innerHTML, />0<\/strong>/);
    panel.setRegion({ id: "eco_3", name: "Third" });
    await tick();
    panel.setRegion({ id: "eco_2", name: "Second" });
    await tick();
    assert.equal(calls.filter((url) => url.includes("eco_2")).length, 2);
  } finally {
    Object.assign(globalThis, original);
  }
});

function earthHealthDom() {
  const nodes = new Map();
  const make = () => ({
    hidden: false,
    textContent: "",
    innerHTML: "",
    parentNode: null,
    style: { setProperty() {} },
    addEventListener() {},
    setAttribute() {},
    remove() {},
    querySelector: () => ({ addEventListener() {}, setAttribute() {}, textContent: "", value: "", min: "", max: "" }),
  });
  for (const id of ["earth-health-root", "earth-health-button", "earth-health-score", "earth-health-status", "earth-health-coverage", "earth-health-panel", "earth-health-panel-score", "earth-health-panel-status", "earth-health-families", "earth-health-trend", "earth-health-method", "earth-health-close"]) nodes.set(id, make());
  return { getElementById: (id) => nodes.get(id), addEventListener() {}, createElement: () => make(), node: (id) => nodes.get(id) };
}

test("Earth Health keeps the empty scaffold unavailable and renders only a complete ready timeline", async () => {
  const original = { document: globalThis.document, fetch: globalThis.fetch };
  const document = earthHealthDom();
  globalThis.document = document;
  let attempt = 0;
  const ready = {
    schemaVersion: 2,
    score: 72,
    status: "Stable",
    historyStartYear: 1993,
    historyEndYear: 1994,
    coverage: { sufficient: true, effectiveDataCoverage: 0.91 },
    families: [{ id: "biodiversity", label: "Biodiversity" }],
    series: [
      { year: 1993, score: 70, coverage: 0.90, families: [{ id: "biodiversity", score: 70, coverage: 0.90 }] },
      { year: 1994, score: 72, coverage: 0.91, families: [{ id: "biodiversity", score: 72, coverage: 0.91 }] },
    ],
  };
  globalThis.fetch = async () => json(attempt++ === 0 ? { schemaVersion: 2, status: "Not generated", historyStartYear: null, historyEndYear: null, score: null, coverage: { sufficient: false }, families: [], series: [] } : ready);
  try {
    const earthHealth = createEarthHealth({ earthHealthUrl: "./data/indices/earth-health.json" });
    await tick();
    assert.equal(document.node("earth-health-score").textContent, "—");
    assert.equal(earthHealth.getPayload(), null);
    await earthHealth.reload();
    assert.equal(document.node("earth-health-score").textContent, "72");
    assert.equal(earthHealth.getYear(), 1994);
    assert.equal(earthHealth.getPayload().historyStartYear, 1993);
  } finally {
    Object.assign(globalThis, original);
  }
});
