import test from "node:test";
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { createTemperatureDataService, validateTemperatureData } from "../frontend/public/js/temperatureDataService.js";
const stats = {meanC: 10, medianC: 10, p05C: 1, p95C: 19, stdC: 4};
export const fixture = id => ({schemaVersion:1, regionId:id, variable:"sea_surface_temperature", unit:"degC",
  baseline:{startYear:2019,endYear:2019},bins:{edgesC:[0,10,20]},
  distributions:{regionalDailyMean:{percent:[50,50],sampleCountDays:365,stats},spaceTime:{percent:[25,75],stats}}});
const response = data => ({ok:true,json:async()=>data});

test("climate service is lazy, deduplicates requests, and supports open ocean", async () => {
  const calls=[];
  const service=createTemperatureDataService({},async url=>{
    calls.push(url);
    return response(url.includes("index") ? {generatedAt:"release 1", regions:{open_ocean:{url:"marine/open_ocean.temperature.json"}}} : fixture("open_ocean"));
  });
  assert.equal(calls.length,0);
  assert.equal(await service.loadRegion("moon"),null);
  const [a,b]=await Promise.all([service.loadRegion("open_ocean"),service.loadRegion("open_ocean")]);
  assert.strictEqual(a,b);
  assert.equal(calls.length,2);
  assert.match(calls[1],/v=release%201$/);
  await service.loadRegion("open_ocean");
  assert.equal(calls.length,2);
  assert.equal(await service.loadRegion("eco_missing"),null);
});

test("index and inventory failures remain retryable; bad values never reach the chart", async () => {
  let indexTries=0, inventoryTries=0;
  const service=createTemperatureDataService({},async url=>{
    if(url.includes("index")) {
      if(++indexTries===1) throw new Error("offline");
      return response({regions:{eco_1:{url:"land/eco_1.temperature.json"}}});
    }
    if(++inventoryTries===1) return {ok:false,status:503};
    return response(fixture("eco_1"));
  });
  await assert.rejects(service.loadRegion("eco_1"),/offline/);
  await assert.rejects(service.loadRegion("eco_1"),/503/);
  assert.equal((await service.loadRegion("eco_1")).regionId,"eco_1");
  for (const change of [d=>d.regionId="wrong",d=>d.bins.edgesC=[0,0,20],d=>d.distributions.spaceTime.percent=[10,10],d=>d.distributions.spaceTime.stats.meanC=null]) {
    const bad=structuredClone(fixture("eco_1"));change(bad);
    assert.throws(()=>validateTemperatureData(bad,"eco_1"));
  }
  const traversal=createTemperatureDataService({},async()=>response({regions:{eco_1:{url:"../../secret"}}}));
  await assert.rejects(traversal.loadRegion("eco_1"),/path/);
});

test("every generated inventory matches its manifest, baseline, histogram and source", async () => {
  const root=new URL("../frontend/public/data/climate/",import.meta.url);
  const index=JSON.parse(await readFile(new URL("climate.index.json",root),"utf8"));
  for(const [id,entry] of Object.entries(index.regions)) {
    const data=JSON.parse(await readFile(new URL(entry.url,root),"utf8"));
    validateTemperatureData(data,id);
    assert.ok(data.distributions.regionalDailyMean.sampleCountDays>0);
    assert.equal(entry.source,data.source.id);
    assert.equal(entry.queryFingerprint,data.queryFingerprint);
  }
});
