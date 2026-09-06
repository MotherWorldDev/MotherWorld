import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRegionDataService } from "../frontend/public/js/regionDataService.js";

const readJson = async (file) => JSON.parse(await readFile(new URL(`../frontend/public/data/${file}`, import.meta.url), "utf8"));
const [land, marine, lakes, content] = await Promise.all([
  readJson("regions.index.json"), readJson("marine.index.json"), readJson("lakes.index.json"), readJson("region-content.json"),
]);
const fields = ["climateSummary", "tertiarySummary", "plotsSummary"];

function fixture(t, replacements = {}, options = {}) {
  const responses = {"/land":land,"/marine":marine,"/lakes":lakes,"/content":content,...replacements};
  const calls = new Map();
  t.mock.method(globalThis, "fetch", async (url) => {
    const path = new URL(url, "http://test.local").pathname;
    const count = (calls.get(path) || 0) + 1;
    calls.set(path, count);
    const value = responses[path];
    if (typeof value === "function") return value(count);
    return value === undefined ? new Response(null, {status:404}) : Response.json(value);
  });
  t.mock.method(console, "warn", () => {});
  const service = createRegionDataService({metadataUrl:"/land",marineMetadataUrl:"/marine",lakesMetadataUrl:"/lakes",contentUrl:"/content",...options});
  return {service,calls};
}

test("every mapped region has content, correct coverage, and unchanged geography", async (t) => {
  const {service,calls} = fixture(t);
  const regions = Object.values({...land.regions,...marine.regions,...lakes.regions});
  assert.equal(regions.length,1100);
  assert.equal(Object.keys(content.regions).length,13);
  assert.deepEqual(Object.keys(content.biomeFallbacks).map(Number).sort((a,b)=>a-b),Array.from({length:14},(_,i)=>i+1));
  const summaries = await Promise.all(regions.map(region=>service.getRegionSummary(region.id)));
  for (let i=0;i<regions.length;i++) {
    const raw=regions[i], summary=summaries[i];
    for (const field of fields) assert.ok(summary[field]?.trim(),`${raw.id}: ${field}`);
    for (const field of ["id","name","ecoId","biomeNum","realm","areaKm2","center"]) assert.deepEqual(summary[field],raw[field],`${raw.id}: ${field}`);
    if (content.regions[raw.id]) {
      for (const field of fields) assert.equal(summary[field],content.regions[raw.id][field]);
      assert.deepEqual(summary.contentSources,content.regions[raw.id].contentSources);
    } else if (raw.isLake) {
      for (const field of fields) {assert.equal(summary[field],content.lakeFallback[field]);assert.equal(summary.contentLevels[field],"lake");}
    } else if (raw.isMarine) {
      for (const field of fields) assert.equal(summary[field],content.marineFallbacks[raw.latZone][field]);
    } else {
      for (const field of fields) assert.equal(summary[field],content.biomeFallbacks[raw.biomeNum][field]);
    }
  }
  assert.deepEqual([...calls.values()],[1,1,1,1],"concurrent selections share each fetch");
  for (const [id, entry] of Object.entries(content.regions)) {
    assert.ok(land.regions[id],`unknown override ${id}`);
    for (const source of entry.contentSources) assert.equal(new URL(source.url).protocol,"https:");
  }
});

test("content stays off the startup path and is cached after selection", async (t) => {
  const {service,calls}=fixture(t);
  await Promise.all([service.getIndex(),service.getMarineIndex(),service.getLakesIndex()]);
  assert.equal(calls.get("/content"),undefined);
  await service.getRegionSummary("eco_689");
  await service.getRegionSummary("eco_1");
  assert.equal(calls.get("/content"),1);
});

test("explicit content beats authored metadata, blanks use fallback, and IDs are protected", async (t) => {
  const raw={...land.regions.eco_689,id:"custom",climateSummary:"Authored climate",tertiarySummary:"",plotsSummary:"Authored threats"};
  const customContent={...content,regions:{custom:{id:"wrong",center:{lat:0,lon:0},climateSummary:"Editorial climate",flagshipSpecies:["Example"],threats:["Pressure"]}}};
  const {service}=fixture(t,{"/land":{regions:{custom:raw}},"/content":customContent});
  const summary=await service.getRegionSummary("custom");
  assert.equal(summary.id,"custom");assert.deepEqual(summary.center,raw.center);
  assert.equal(summary.climateSummary,"Editorial climate");
  assert.equal(summary.tertiarySummary,content.biomeFallbacks[raw.biomeNum].tertiarySummary);
  assert.equal(summary.plotsSummary,"Authored threats");
  assert.deepEqual(summary.contentLevels,{climateSummary:"region",tertiarySummary:"biome",plotsSummary:"region"});
  assert.deepEqual(summary.flagshipSpecies,["Example"]);assert.deepEqual(summary.threats,["Pressure"]);
});

test("missing content preserves metadata and retries on the next selection", async (t) => {
  const {service,calls}=fixture(t,{"/content":count=>count===1?new Response(null,{status:503}):Response.json(content)});
  const first=await service.getRegionSummary("eco_1");
  assert.equal(first.name,land.regions.eco_1.name);assert.equal(first.climateSummary,"");
  const second=await service.getRegionSummary("eco_1");
  assert.equal(second.climateSummary,content.regions.eco_1.climateSummary);
  assert.equal(calls.get("/content"),2);
});

test("Moon and open ocean remain specialized and unknown selections stay empty", async (t) => {
  const {service,calls}=fixture(t);
  assert.match((await service.getRegionSummary("moon")).tertiarySummary,/27\.32/);
  assert.equal((await service.getRegionSummary("open_ocean")).name,"Open Ocean");
  assert.equal(await service.getRegionSummary(null),null);
  assert.equal(calls.size,0);
  assert.equal(await service.getRegionSummary("missing_region"),null);
});

test("API summaries receive the same enrichment and API failures fall back locally", async (t) => {
  const {service}=fixture(t,{"/api/regions/eco_689":land.regions.eco_689},{regionSummaryMode:"api",apiBaseUrl:"/api"});
  assert.equal((await service.getRegionSummary("eco_689")).climateSummary,content.regions.eco_689.climateSummary);
  assert.equal((await service.getRegionSummary("eco_1")).climateSummary,content.regions.eco_1.climateSummary);
});

test("marine fallback accepts biome labels when latitude zone is absent", async (t) => {
  const raw={...Object.values(marine.regions)[0],latZone:undefined,biome:"Polar marine ecoregion"};
  const {service}=fixture(t,{"/marine":{regions:{[raw.id]:raw}}});
  assert.equal((await service.getRegionSummary(raw.id)).climateSummary,content.marineFallbacks.Polar.climateSummary);
});
