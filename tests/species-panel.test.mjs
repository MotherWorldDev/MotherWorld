import test from "node:test";
import assert from "node:assert/strict";
import { createSpeciesPanel } from "../frontend/public/js/speciesPanel.js";

const turn = () => new Promise(resolve => setImmediate(resolve));
function dom() {
  const nodes = new Map();
  for (const id of ["sidebar-tab-tertiary","species-data-shell","species-data-status","species-data-toolbar","species-search-input","species-data-count","species-list","species-show-more","species-data-source","species-retry"]) {
    const classes = new Set();
    nodes.set(id,{hidden:false,value:"",textContent:"",innerHTML:"",listeners:{},classList:{contains:c=>classes.has(c),add:c=>classes.add(c),remove:c=>classes.delete(c)},addEventListener(name,fn){this.listeners[name]=fn;}});
  }
  return {getElementById:id=>nodes.get(id),node:id=>nodes.get(id)};
}

test("late inventory errors cannot replace a newer selection or Moon state", async () => {
  const original = {document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch};
  const document = dom();globalThis.document=document;globalThis.window={requestAnimationFrame:fn=>fn()};
  let rejectFirst, resolveSecond;
  const requests=[];
  globalThis.fetch=async url=>{
    requests.push(url);
    if(url.includes("index"))return {ok:true,json:async()=>({regions:{eco_1:{url:"land/eco_1.json"},eco_2:{url:"land/eco_2.json"}}})};
    if(url.includes("eco_1"))return new Promise((_,reject)=>{rejectFirst=reject;});
    return new Promise(resolve=>{resolveSecond=data=>resolve({ok:true,json:async()=>data});});
  };
  try {
    const panel=createSpeciesPanel();panel.setRegion("eco_1");
    assert.equal(requests.length,0,"selecting a region must not load species while the tab is inactive");
    document.node("sidebar-tab-tertiary").classList.add("is-active");
    const first=panel.ensureLoaded();await turn();
    panel.setRegion("eco_2");await turn();
    resolveSecond({regionId:"eco_2",source:"GBIF",species:[{scientificName:'Species <img src=x>',family:"Test family",occurrenceCount:3}],stats:{occurrenceCount:3}});await turn();
    assert.equal(document.node("species-data-count").textContent,"1 recorded species");
    assert.match(document.node("species-list").innerHTML,/Species &lt;img/);
    rejectFirst(new Error("old request failed"));await first;
    assert.equal(document.node("species-data-status").hidden,true);
    assert.equal(document.node("species-retry").hidden,true);
    assert.match(document.node("species-list").innerHTML,/Species &lt;img/);
    panel.setRegion("moon");
    assert.equal(document.node("species-data-shell").hidden,true);
    assert.equal(document.node("species-list").innerHTML,"");
    await panel.ensureLoaded();assert.equal(requests.length,3);
  } finally {Object.assign(globalThis,original);}
});
