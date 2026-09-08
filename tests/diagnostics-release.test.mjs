import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createBiodiversityDataService } from "../frontend/public/js/biodiversityDataService.js";
import { createBiodiversityPanel } from "../frontend/public/js/biodiversityPanel.js";
import { createContaminantsPanel } from "../frontend/public/js/contaminantsPanel.js";
import { createGeologyPanel } from "../frontend/public/js/geologyPanel.js";

const tick = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ ok: true, status: 200, json: async () => data });
const publicRoot = new URL("../frontend/public/", import.meta.url);
function dom() {
  const nodes = new Map();
  return { getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, { hidden: false, innerHTML: "", textContent: "", addEventListener() {}, classList: { contains: () => true } });
    return nodes.get(id);
  }};
}
async function rendered(kind, factory, payload, region = { id: "eco_1" }, fetcher = null) {
  const saved = { document: globalThis.document, fetch: globalThis.fetch };
  const document = dom(); globalThis.document = document;
  const read = fetcher || (async url => response(url.includes("index") ? { regions: { [region.id]: { url: `land/${region.id}.json` } } } : payload));
  globalThis.fetch = read;
  try {
    const panel = factory({}, read);
    panel.setRegion(region);
    await tick(); await tick();
    return { html: document.getElementById(`${kind}-content`).innerHTML, status: document.getElementById(`${kind}-status`).textContent };
  } finally { Object.assign(globalThis, saved); }
}
function statValue(html, label) {
  const start = html.indexOf(`<span>${label}</span>`);
  assert.notEqual(start, -1, `Missing field ${label}`);
  return html.slice(start).match(/<strong>(.*?)<\/strong>/s)[1];
}

test("biodiversity resolves producer and family-relative index URLs", async () => {
  for (const entry of ["biodiversity/land/eco_1.biodiversity.json", "land/eco_1.biodiversity.json"]) {
    const urls = [];
    const service = createBiodiversityDataService({}, async url => {
      urls.push(url);
      return response(url.includes("index") ? { regions: { eco_1: { url: entry } } } : { regionId: "eco_1" });
    });
    assert.equal((await service.loadRegion("eco_1")).regionId, "eco_1");
    assert.equal(urls[1], "./data/biodiversity/land/eco_1.biodiversity.json");
  }
});

test("actual release payloads render with their source and index contracts", async () => {
  const fetcher = async url => response(JSON.parse(readFileSync(new URL(url, publicRoot), "utf8")));
  const bio = await rendered("biodiversity", createBiodiversityPanel, null, { id: "eco_1" }, fetcher);
  assert.match(bio.html, /Present natural species/);
  assert.equal(statValue(bio.html, "Present natural species"), "362");
  const tiny = await rendered("biodiversity", createBiodiversityPanel, null, { id: "eco_609" }, fetcher);
  assert.match(tiny.html, /Insufficient native spatial resolution/);
  assert.doesNotMatch(tiny.html, /Present natural species/);
  const marine = await rendered("contaminants", createContaminantsPanel, null, { id: "marine_meow_20012", isMarine: true }, fetcher);
  assert.match(marine.html, /www.ncei.noaa.gov/);
  assert.match(marine.html, /Median positive-reported concentration/);
});

test("missing and invalid numeric values never become zero in diagnostic panels", async () => {
  for (const value of [null, undefined, "", "  ", false, true, [], {}, Infinity, NaN]) {
    const bio = await rendered("biodiversity", createBiodiversityPanel, { summary: { historicalMammals: { coverageStatus: "covered", nativeRasterCellCount: 1, currentSpeciesCount: value } } });
    assert.equal(statValue(bio.html, "Current species"), "\u2014");
    const marine = await rendered("contaminants", createContaminantsPanel, { categories: { oil_incidents: { type: "incidents", eventCount: 1, maxPotentialReleaseGallons: value } } }, { id: "marine_1", isMarine: true });
    assert.equal(statValue(marine.html, "Largest reported maximum"), "\u2014");
    const geology = await rendered("geology", createGeologyPanel, { sections: { geothermal: { measurementCount: 1, medianMwM2: value }, seafloor: { bathymetry: { meanDepthM: value } }, impacts: { structures: [{ name: "Test crater", ageMa: value }] } } });
    assert.equal(statValue(geology.html, "Median heat flow"), "\u2014");
    assert.equal(statValue(geology.html, "Mean depth"), "\u2014");
    assert.doesNotMatch(geology.html, /0\.00 Ma/);
  }
  for (const value of [0, "0", 12.5]) {
    const geology = await rendered("geology", createGeologyPanel, { sections: { geothermal: { medianMwM2: value } } });
    assert.equal(statValue(geology.html, "Median heat flow"), `${Number(value).toFixed(0)} mW/m\u00b2`);
  }
});

test("all protocol groups and concentration summaries remain visible without false shared protocol", async () => {
  const analytes = Array.from({ length: 16 }, (_, i) => ({ name: `Group ${i}`, unit: "items/m3", sampleCount: 2, detectedCount: 1, medianDetected: i ? null : 0.0000123, p90Detected: null, maxDetected: null, protocol: { samplingMethod: `Method ${i}`, meshSizeMm: "not reported" } }));
  const result = await rendered("contaminants", createContaminantsPanel, { categories: { microplastics: { type: "measurements", analytes, protocolFields: ["samplingMethod", "meshSizeMm"] } }, sources: { noaa: { label: "NOAA", url: "https://example.test/noaa" } } }, { id: "marine_1", isMarine: true });
  assert.match(result.html, /Group 15/);
  assert.match(result.html, /0\.0000123 items\/m3/);
  assert.equal((result.html.match(/Method 0</g) || []).length, 1);
  assert.doesNotMatch(result.html, /not reported m/i);
  assert.match(result.html, /Not reported/);
  assert.equal(statValue(result.html, "90th percentile positive-reported concentration"), "\u2014");
});
