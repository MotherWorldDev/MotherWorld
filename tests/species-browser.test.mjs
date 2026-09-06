import test from "node:test";
import assert from "node:assert/strict";
import { createSpeciesPanel } from "../frontend/public/js/speciesPanel.js";
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const ids=["sidebar-tab-tertiary","species-data-shell","species-data-status","species-retry","species-browser","species-data-toolbar","species-kingdom-tabs","species-search-input","species-lookup","species-lookup-status","species-filters-toggle","species-filters","species-class-filter","species-family-filter","species-record-filter","species-sort","species-clear-filters","species-data-count","species-list","species-results-panel","species-show-more","species-data-source","species-profile","species-profile-back","species-profile-title","species-profile-subtitle","species-profile-context","species-profile-status","species-profile-retry","species-profile-content","species-inventory-about"];
function dom() {
 const nodes=new Map(ids.map(id=>{
  const classes=new Set();
  return [id,{hidden:false,value:"",textContent:"",innerHTML:"",listeners:{},attributes:{},dataset:{},classList:{contains:key=>classes.has(key),add:key=>classes.add(key)},
   addEventListener(name,fn){this.listeners[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},focus(){},closest(){return null;},querySelector(){return null;},querySelectorAll(){return [];}}];
 }));
 return {getElementById:id=>nodes.get(id),node:id=>nodes.get(id)};
}
const species=[
 {speciesKey:11,scientificName:"Avis rubra",kingdom:"Animalia",class:"Aves",family:"Birdidae",occurrenceCount:100},
 {speciesKey:12,scientificName:"Avis alba",kingdom:"Animalia",class:"Aves",family:"Birdidae",occurrenceCount:2},
 {speciesKey:13,scientificName:"Planta viridis",kingdom:"Plantae",class:"Magnoliopsida",family:"Plantidae",occurrenceCount:25}
];
const json=data=>({ok:true,json:async()=>data});
function context(fetcher) {
 const old={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch};
 const document=dom();globalThis.document=document;globalThis.window={requestAnimationFrame:fn=>fn()};globalThis.fetch=fetcher;
 return {document,restore:()=>Object.assign(globalThis,old)};
}
const clickKingdom=(document,kingdom)=>document.node("species-kingdom-tabs").listeners.click({target:{closest:()=>({dataset:{kingdom}})}});
const change=(document,id,value)=>{document.node(id).value=value;document.node(id).listeners.change();};

test("kingdom/filter navigation stays local, lookup is explicit, and profiles load only after opening",async()=>{
 const calls=[];
 const {document,restore}=context(async url=>{
  calls.push(url);
  if(url.includes("species.index"))return json({regions:{eco_1:{url:"land/eco_1.json"}}});
  if(url.includes("land/eco_1"))return json({regionId:"eco_1",regionName:"Test region",source:"GBIF",species});
  if(url.includes("/species/search?"))return json({endOfRecords:true,results:[{key:11,canonicalName:"Avis rubra",kingdom:"Animalia"}]});
  if(url.endsWith("/species/11"))return json({key:11,rank:"SPECIES",canonicalName:"Avis rubra",scientificName:"Avis rubra",vernacularName:"Red bird"});
  return json({results:[]});
 });
 try {
  const panel=createSpeciesPanel();panel.setRegion("eco_1");assert.equal(calls.length,0);
  document.node("sidebar-tab-tertiary").classList.add("is-active");await panel.ensureLoaded();
  assert.match(document.node("species-kingdom-tabs").innerHTML,/Animals/);assert.match(document.node("species-kingdom-tabs").innerHTML,/Plants/);
  clickKingdom(document,"Animalia");change(document,"species-class-filter","Aves");change(document,"species-record-filter","10");
  assert.match(document.node("species-data-count").textContent,/1 matching/);assert.doesNotMatch(document.node("species-list").innerHTML,/Avis alba|Planta/);
  assert.equal(calls.length,2,"browsing local filters must not load any profiles");
  document.node("species-search-input").value="red bird";
  await document.node("species-lookup").listeners.click();
  assert.match(document.node("species-data-count").textContent,/1 matching/);assert.equal(calls.length,3);
  document.node("species-list").listeners.click({target:{closest:()=>({dataset:{speciesIndex:"0"}})}});
  await tick();await tick();
  assert.equal(document.node("species-profile").hidden,false);assert.equal(document.node("species-browser").hidden,true);
  assert.match(document.node("species-profile-context").textContent,/100 occurrence records in Test region/);
  assert.equal(document.node("species-profile-subtitle").textContent,"Red bird");
  const afterProfile=calls.length;assert.ok(afterProfile>3);
  document.node("species-profile-back").listeners.click();
  assert.equal(document.node("species-browser").hidden,false);assert.equal(document.node("species-profile").hidden,true);
  assert.equal(document.node("species-search-input").value,"red bird");
  document.node("species-list").listeners.click({target:{closest:()=>({dataset:{speciesIndex:"0"}})}});await tick();
  assert.equal(calls.length,afterProfile,"reopening uses cached details");
 } finally {restore();}
});

test("a late profile response cannot replace another region or reopen a closed profile",async()=>{
 let release;
 const {document,restore}=context(async url=>{
  if(url.includes("species.index"))return json({regions:{eco_1:{url:"land/eco_1.json"},eco_2:{url:"land/eco_2.json"}}});
  if(url.includes("land/eco_1"))return json({regionId:"eco_1",regionName:"First",species});
  if(url.includes("land/eco_2"))return json({regionId:"eco_2",regionName:"Second",species:[species[2]]});
  if(url.endsWith("/species/11"))return new Promise(resolve=>{release=()=>resolve(json({key:11,rank:"SPECIES",canonicalName:"OLD DETAIL"}));});
  return json({results:[]});
 });
 try {
  const panel=createSpeciesPanel();document.node("sidebar-tab-tertiary").classList.add("is-active");panel.setRegion("eco_1");await tick();
  document.node("species-list").listeners.click({target:{closest:()=>({dataset:{speciesIndex:"0"}})}});await tick();
  panel.setRegion("eco_2");await tick();release();await tick();await tick();
  assert.equal(document.node("species-profile").hidden,true);assert.equal(document.node("species-profile-content").innerHTML,"");
  assert.match(document.node("species-list").innerHTML,/Planta viridis/);assert.doesNotMatch(document.node("species-list").innerHTML,/OLD DETAIL/);
  panel.setRegion("moon");assert.equal(document.node("species-data-shell").hidden,true);
 } finally {restore();}
});
