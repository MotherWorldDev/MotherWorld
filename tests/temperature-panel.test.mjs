import test from "node:test";
import assert from "node:assert/strict";
import { createTemperaturePanel } from "../frontend/public/js/temperaturePanel.js";
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(id) {
  const stats={meanC:10,medianC:10,p05C:1,p95C:19,stdC:4};
  return {schemaVersion:1,regionId:id,variable:"sea_surface_temperature",unit:"degC",baseline:{startYear:2019,endYear:2019},bins:{edgesC:[0,10,20]},
    distributions:{regionalDailyMean:{percent:[50,50],sampleCountDays:365,stats},spaceTime:{percent:[25,75],stats}}};
}
function dom() {
  const nodes=new Map();
  const context=new Proxy({}, {get:(target,key)=>target[key] || (()=>{}),set:(target,key,value)=>(target[key]=value,true)});
  for(const id of ["sidebar-tab-climate","temperature-data-shell","temperature-data-status","temperature-distribution-canvas","temperature-tooltip","temperature-data-source","temperature-summary","temperature-content","temperature-heading","temperature-coverage-note","temperature-retry","temperature-axis-label","temperature-mode-temporal","temperature-mode-spacetime","temperature-stats-grid","temperature-definition"]) {
    const classes=new Set();
    nodes.set(id,{hidden:false,textContent:"",innerHTML:"",dataset:{},style:{},listeners:{},attributes:{},
      classList:{contains:c=>classes.has(c),add:c=>classes.add(c),toggle:(c,on)=>on?classes.add(c):classes.delete(c)},
      addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},
      getContext:()=>context,getBoundingClientRect:()=>({width:310,height:280,left:0,top:0})});
  }
  return {getElementById:id=>nodes.get(id),node:id=>nodes.get(id),documentElement:{}};
}

test("climate panel loads on demand, ignores stale responses, and clears charts for missing regions and Moon", async () => {
  const original={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch,getComputedStyle:globalThis.getComputedStyle};
  const document=dom(); globalThis.document=document;globalThis.window={requestAnimationFrame:fn=>fn(),devicePixelRatio:1};
  globalThis.getComputedStyle=()=>({getPropertyValue:()=>"#fff"});
  let rejectFirst,resolveSecond;const calls=[];
  globalThis.fetch=async url=>{
    calls.push(url);
    if(url.includes("index"))return {ok:true,json:async()=>({regions:{eco_1:{url:"land/eco_1.temperature.json"},eco_2:{url:"land/eco_2.temperature.json"}}})};
    if(url.includes("eco_1"))return new Promise((_,reject)=>{rejectFirst=reject;});
    return new Promise(resolve=>{resolveSecond=()=>resolve({ok:true,json:async()=>fixture("eco_2")});});
  };
  try {
    const panel=createTemperaturePanel();panel.setRegion({id:"eco_1",name:"First"});
    assert.equal(calls.length,0);
    document.node("sidebar-tab-climate").classList.add("is-active");
    const first=panel.ensureLoaded();await tick();
    panel.setRegion({id:"eco_2",name:"Second"});await tick();resolveSecond();await tick();
    assert.equal(document.node("temperature-content").hidden,false);
    assert.match(document.node("temperature-heading").textContent,/Second/);
    assert.match(document.node("temperature-data-source").textContent,/2019 preview/);
    rejectFirst(new Error("old failure"));await first;
    assert.equal(document.node("temperature-retry").hidden,true);
    panel.setMode("spaceTime");assert.equal(document.node("temperature-axis-label").textContent,"Area × days (%)");
    document.node("temperature-distribution-canvas").listeners.keydown({key:"ArrowRight",preventDefault(){}});
    assert.match(document.node("temperature-tooltip").textContent,/75.00%/);
    panel.setRegion({id:"eco_missing"});await tick();
    assert.equal(document.node("temperature-content").hidden,true);
    assert.match(document.node("temperature-data-status").textContent,/not available/);
    panel.setRegion({id:"moon"});await panel.ensureLoaded();
    assert.equal(document.node("temperature-data-shell").hidden,true);
    assert.equal(document.node("temperature-summary").textContent,"");
    assert.equal(calls.length,3);
  } finally {Object.assign(globalThis,original);}
});
