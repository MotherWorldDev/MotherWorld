import test from "node:test";
import assert from "node:assert/strict";
import { createSpeciesDataService } from "../frontend/public/js/speciesDataService.js";

const json = (data) => ({ok: true, json: async () => data});
const data = (id) => ({regionId: id, species: [{scientificName: "Test species", occurrenceCount: 3}]});
const manifest = (ids) => ({regions: Object.fromEntries(ids.map(id => [id, {url: `land/${id}.json`, generatedAt: "2026-09-06"}]))});

test("species data stays lazy, deduplicates requests and reuses a cached region", async () => {
  const calls = [];
  const service = createSpeciesDataService({}, async url => {
    calls.push(url); await new Promise(resolve => setTimeout(resolve, 5));
    return json(url.includes("index") ? manifest(["eco_1"]) : data("eco_1"));
  });
  assert.equal(calls.length, 0);
  assert.equal(await service.loadRegion("moon"), null);
  assert.equal(await service.loadRegion(null), null);
  assert.equal(calls.length, 0);
  const [a,b] = await Promise.all([service.loadRegion("eco_1"),service.loadRegion("eco_1")]);
  assert.equal(a, b); assert.equal(calls.length, 2);
  assert.equal(await service.loadRegion("eco_1"), a);
  assert.equal(calls.length, 2);
  assert.match(calls[1], /\?v=2026-09-06$/);
});

test("failed index and region requests can both be retried", async () => {
  let indexAttempts = 0, regionAttempts = 0;
  const service = createSpeciesDataService({}, async url => {
    if (url.includes("index")) {
      if (++indexAttempts === 1) return {ok:false,status:503};
      return json(manifest(["eco_1"]));
    }
    if (++regionAttempts === 1) throw new Error("offline");
    return json(data("eco_1"));
  });
  await assert.rejects(service.loadRegion("eco_1"), /503/);
  await assert.rejects(service.loadRegion("eco_1"), /offline/);
  assert.equal((await service.loadRegion("eco_1")).regionId, "eco_1");
  assert.equal(indexAttempts, 2); assert.equal(regionAttempts, 2);
});

test("missing inventories are distinct from valid empty inventories", async () => {
  const calls = [];
  const service = createSpeciesDataService({}, async url => {
    calls.push(url); return json(url.includes("index") ? manifest(["eco_1"]) : {regionId:"eco_1", species:[]});
  });
  assert.equal(await service.loadRegion("eco_missing"), null);
  assert.equal(calls.length, 1);
  assert.deepEqual((await service.loadRegion("eco_1")).species, []);
});

test("cache evicts the least recently used inventory", async () => {
  const counts = new Map();
  const service = createSpeciesDataService({speciesCacheLimit:2}, async url => {
    if (url.includes("index")) return json(manifest(["eco_1","eco_2","eco_3"]));
    const id = url.match(/(eco_\d+)\.json/)[1]; counts.set(id,(counts.get(id)||0)+1); return json(data(id));
  });
  for (const id of ["eco_1","eco_2","eco_1","eco_3","eco_1","eco_2"]) await service.loadRegion(id);
  assert.equal(counts.get("eco_1"), 1); assert.equal(counts.get("eco_2"), 2);
});

test("mismatched payloads and unsafe manifest paths never enter the cache", async () => {
  const service = createSpeciesDataService({}, async url => json(url.includes("index") ? manifest(["eco_1"]) : data("eco_2")));
  await assert.rejects(service.loadRegion("eco_1"), /Invalid species inventory/);
  const unsafe = createSpeciesDataService({}, async () => json({regions:{eco_1:{url:"https://example.com/data.json"}}}));
  await assert.rejects(unsafe.loadRegion("eco_1"), /Invalid inventory path/);
});


test("compressed inventories load from static hosts and predecoded responses", async () => {
  const { gzipSync } = await import("node:zlib");
  for (const compressed of [true, false]) {
    const service = createSpeciesDataService({}, async url => {
      if (url.includes("index")) return json({regions:{eco_1:{url:"land/eco_1.json.gz"}}});
      const body = JSON.stringify(data("eco_1"));
      return new Response(compressed ? gzipSync(body) : body);
    });
    assert.equal((await service.loadRegion("eco_1")).species[0].scientificName, "Test species");
  }
});
