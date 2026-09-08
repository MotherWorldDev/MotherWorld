import { createGeologyDataService } from "./geologyDataService.js?v=20260908-env8-selenology";

function esc(value){return String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));}
function finite(v){if((typeof v!=="number"&&typeof v!=="string")||(typeof v==="string"&&!v.trim()))return null;const n=Number(v);return Number.isFinite(n)?n:null;}
function fmt(v,d=1,s=""){const n=finite(v);return n==null?"—":`${n.toFixed(d)}${s}`;}
function count(v){const n=finite(v);return n==null?"—":Math.round(n).toLocaleString();}
function meter(v){return fmt(v,0," mW/m²");}
function rows(items, valueKey="percent", unit="%"){
  const vals=(items||[]).map(x=>Math.max(0,Number(x?.[valueKey])||0)); const max=Math.max(...vals,0.001);
  return `<div class="geology-bars">${(items||[]).map((x,i)=>`<div class="geology-bar-row"><span><b>${esc(x.label||x.name||x.code||"Unknown")}</b>${x.code?`<small>${esc(x.code)}</small>`:""}</span><span class="geology-bar-track"><i style="width:${Math.min(100,vals[i]/max*100).toFixed(2)}%"></i></span><strong>${fmt(x?.[valueKey],1,unit)}</strong></div>`).join("")}</div>`;
}
function kv(items){return `<div class="geology-kv">${items.map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}</div>`;}
function chips(values){return `<div class="geology-chips">${(values||[]).filter(Boolean).map(v=>`<span>${esc(v)}</span>`).join("")}</div>`;}
function sourceLine(source){if(!source)return ""; const label=source.label||source.providerId||source.id; return label?`<p class="geology-source">Source: ${esc(label)}${source.version?` · ${esc(source.version)}`:""}</p>`:"";}
function details(title, body, open=false){if(!body)return "";return `<details class="geology-section" ${open?"open":""}><summary><span>${esc(title)}</span></summary><div class="geology-section-body">${body}</div></details>`;}
function listCards(items, renderer){return `<div class="geology-list">${(items||[]).map(renderer).join("")}</div>`;}


function depth(v){return fmt(v,0," m");}
function km(v,d=1){return fmt(v,d," km");}
function ma(v,d=1){return fmt(v,d," Ma");}
function seafloorHtml(s){
  if(!s)return ""; let body="";
  if(s.bathymetry){
    const b=s.bathymetry;
    body+=`<h4>Bathymetry</h4>${kv([["Mean depth",depth(b.meanDepthM)],["Median depth",depth(b.medianDepthM)],["10th–90th percentile",`${depth(b.p10DepthM)} – ${depth(b.p90DepthM)}`],["Deepest sampled",depth(b.deepestM)],["Mapped coverage",fmt(b.coveragePct,1,"%")]])}${b.depthZones?.length?rows(b.depthZones):""}${sourceLine(b.source)}`;
  }
  if(s.age){
    const a=s.age;
    body+=`<h4>Oceanic crust age</h4>${kv([["Mean age",ma(a.meanAgeMa)],["Median age",ma(a.medianAgeMa)],["10th–90th percentile",`${ma(a.p10AgeMa)} – ${ma(a.p90AgeMa)}`],["Oldest sampled crust",ma(a.oldestAgeMa)],["Age-grid coverage",fmt(a.coveragePct,1,"%")]])}${a.ageBands?.length?rows(a.ageBands):""}`;
    if(a.spreadingRate) body+=kv([["Median spreading rate",fmt(a.spreadingRate.medianMmYr,1," mm/yr")],["10th–90th spreading rate",`${fmt(a.spreadingRate.p10MmYr,1," mm/yr")} – ${fmt(a.spreadingRate.p90MmYr,1," mm/yr")}`]]);
    body+=sourceLine(a.source);
  }
  if(s.sediment){
    const d=s.sediment;
    body+=`<h4>Sediment cover</h4>${kv([["Mean total thickness",km(d.meanThicknessKm,2)],["Median total thickness",km(d.medianThicknessKm,2)],["90th percentile",km(d.p90ThicknessKm,2)],["Maximum sampled",km(d.maxThicknessKm,2)],["Grid coverage",fmt(d.coveragePct,1,"%")]])}${d.thicknessBands?.length?rows(d.thicknessBands):""}${sourceLine(d.source)}`;
  }
  if(s.crust){
    const c=s.crust;
    body+=`<h4>Crystalline crust</h4>${kv([["Mean thickness",km(c.meanCrystallineThicknessKm,1)],["Median thickness",km(c.medianCrystallineThicknessKm,1)],["10th–90th percentile",`${km(c.p10CrystallineThicknessKm,1)} – ${km(c.p90CrystallineThicknessKm,1)}`],["Model coverage",fmt(c.coveragePct,1,"%")]])}${sourceLine(c.source)}`;
  }
  if(s.tectonics){
    const t=s.tectonics; const pairs=[];
    if(t.ridges){pairs.push(["Spreading-ridge length",fmt(t.ridges.intersectionLengthKm,0," km")],["Ridge segments",count(t.ridges.featureCount)]);}
    if(t.trenches){pairs.push(["Trench / subduction length",fmt(t.trenches.intersectionLengthKm,0," km")],["Trench segments",count(t.trenches.featureCount)]);}
    body+=`<h4>Seafloor tectonics</h4>${pairs.length?kv(pairs):""}${sourceLine(t.source)}`;
  }
  if(s.hydrothermal){
    const h=s.hydrothermal;
    body+=`<h4>Hydrothermal vents</h4>${kv([["Known vent fields",count(h.ventFieldCount)],["Confirmed / inferred active",count(h.confirmedOrInferredActive)],["Median reported depth",h.medianDepthM==null?"—":depth(h.medianDepthM)],["Maximum reported temperature",h.maxReportedTemperatureC==null?"—":fmt(h.maxReportedTemperatureC,0," °C")]])}`;
    if(h.tectonicSettings?.length) body+=chips(h.tectonicSettings.slice(0,8).map(x=>`${x.label} · ${x.count}`));
    if(h.fields?.length) body+=listCards(h.fields.slice(0,24),x=>`<div class="geology-item"><div><b>${esc(x.name||"Hydrothermal vent field")}</b><small>${esc([x.tectonicSetting,x.activity].filter(Boolean).join(" · "))}</small></div><span>${x.depthM!=null?depth(x.depthM):""}</span></div>`);
    body+=sourceLine(h.source)+`<p class="geology-note">Known vent fields reflect exploration effort; absence of a record does not mean hydrothermal activity is absent.</p>`;
  }
  if(body) body+=`<p class="geology-note">Seafloor grids are summarized at a coarser statistics resolution than their native source grids. Global Ocean uses the whole ocean, even though its selectable geometry is the residual open ocean.</p>`;
  return body;
}

function surfaceHtml(s){if(!s)return "";return `${kv([["Rock-mapped coverage",fmt(s.rockCoveragePct,1,"%")],["Dominant lithology",s.dominantClass?.label||"—"]])}${rows(s.classes)}${(s.excluded||[]).length?`<p class="geology-note">Excluded from rock shares: ${(s.excluded||[]).map(x=>`${esc(x.label)} ${fmt(x.percentOfRegion,1,"%")}`).join(" · ")}</p>`:""}${sourceLine(s.source)}`;}
function ageHtml(a){if(!a)return ""; const units=(a.units||[]).slice(0,20); return `${a.eras?.length?rows(a.eras):""}${units.length?`<h4>Mapped units</h4>${listCards(units,u=>`<div class="geology-item"><div><b>${esc(u.name||u.lith||"Mapped unit")}</b><small>${esc(u.age||[u.bestTopAgeMa,u.bestBottomAgeMa].filter(v=>v!=null).join("–")+(u.bestTopAgeMa!=null||u.bestBottomAgeMa!=null?" Ma":""))}</small></div>${u.lith?`<span>${esc(u.lith)}</span>`:""}</div>`)}`:""}<p class="geology-note">Macrostrat coverage and source-map resolution vary by place. Unit ages are context, not an Earth Health input.</p>${sourceLine(a.source)}`;}
function mineralHtml(m){if(!m)return ""; const sites=(m.sites||[]).slice(0,24); return `${kv([["Known occurrences / deposits",count(m.occurrenceCount)],["Distinct commodities",count(m.commodityCount)]])}${rows((m.commodities||[]).map(x=>({...x,percent:x.sharePct})),"percent","%")}${sites.length?`<h4>Selected known sites</h4>${listCards(sites,s=>`<div class="geology-item"><div><b>${esc(s.name||"Unnamed occurrence")}</b><small>${esc((s.commodities||[]).join(", ")||"Commodity not listed")}</small></div>${s.depositType?`<span>${esc(s.depositType)}</span>`:""}</div>`)}`:""}<p class="geology-note">Known occurrences are not the same thing as economically recoverable reserves.</p>${sourceLine(m.source)}`;}
function tectonicsHtml(t){if(!t)return ""; let body=""; if(t.faults){body+=kv([["Intersecting active faults",count(t.faults.count)],["Mapped fault length",fmt(t.faults.intersectionLengthKm,0," km")],["Median slip rate",t.faults.medianSlipRateMmYr==null?"—":fmt(t.faults.medianSlipRateMmYr,1," mm/yr")]]); if(t.faults.items?.length)body+=listCards(t.faults.items.slice(0,20),f=>`<div class="geology-item"><div><b>${esc(f.name||"Mapped active fault")}</b><small>${f.kinematics?esc(f.kinematics):"Active fault"}</small></div>${f.slipRateMmYr!=null?`<span>${fmt(f.slipRateMmYr,1," mm/yr")}</span>`:""}</div>`); body+=sourceLine(t.faults.source);} if(t.seismicity){body+=`<h4>Recorded seismicity</h4>`+kv([[`M${t.seismicity.minMagnitude}+ events`,count(t.seismicity.eventCount)],["M6+",count(t.seismicity.m6Plus)],["M7+",count(t.seismicity.m7Plus)],["Largest",t.seismicity.maxMagnitude==null?"—":`M${fmt(t.seismicity.maxMagnitude,1)}`]]); body+=sourceLine(t.seismicity.source);} return body;}
function volcanoHtml(v){if(!v)return ""; return `${kv([["Holocene volcanoes",count(v.volcanoCount)],["Confirmed eruptions",count(v.confirmedEruptionCount)],["Historically active",count(v.historicallyActiveCount)],["Largest known VEI",v.maxVEI==null?"—":String(v.maxVEI)],["Most recent confirmed eruption",v.latestConfirmedYear==null?"—":String(v.latestConfirmedYear)]])}${listCards((v.volcanoes||[]).slice(0,30),x=>`<div class="geology-item"><div><b>${esc(x.name||"Unnamed volcano")}</b><small>${esc([x.type,x.country].filter(Boolean).join(" · "))}</small>${x.rocks?.length?`<small>${esc(x.rocks.join(", "))}</small>`:""}</div><span>${x.latestEruptionYear?esc(String(x.latestEruptionYear)):esc(x.lastKnownEruption||"")}</span></div>`)}${sourceLine(v.source)}`;}
function heatHtml(g){if(!g)return ""; return `${kv([["Measurements",count(g.measurementCount)],["Median heat flow",meter(g.medianMwM2)],["10th percentile",meter(g.p10MwM2)],["90th percentile",meter(g.p90MwM2)]])}${g.bins?.length?rows(g.bins.map(x=>({label:x.label,percent:x.percent}))):""}<p class="geology-note">Heat flow is not the same thing as commercially exploitable geothermal power.</p>${sourceLine(g.source)}`;}
function ageText(x){
  const lo=finite(x?.ageMinMa), hi=finite(x?.ageMaxMa), age=finite(x?.ageMa), unc=finite(x?.ageUncertaintyMa);
  if(lo!=null&&hi!=null)return `${fmt(lo,2)}–${fmt(hi,2)} Ma`;
  if(age!=null&&unc!=null)return `${fmt(age,2)} ± ${fmt(Math.abs(unc),2)} Ma`;
  return age!=null?`${fmt(age,2)} Ma`:"";
}
function impactorText(x){
  const name=String(x?.impactorName||"").trim(), type=String(x?.impactorType||"").trim();
  const d=finite(x?.impactorDiameterKm), lo=finite(x?.impactorDiameterMinKm), hi=finite(x?.impactorDiameterMaxKm), unc=finite(x?.impactorDiameterUncertaintyKm);
  let size="";
  if(lo!=null&&hi!=null)size=`${fmt(lo,1)}–${fmt(hi,1)} km`;
  else if(d!=null&&unc!=null)size=`${fmt(d,1)} ± ${fmt(Math.abs(unc),1)} km`;
  else if(d!=null)size=`~${fmt(d,1)} km`;
  const identity=[name,type].filter(Boolean).join(" · ");
  return [identity,size].filter(Boolean).join(" · ");
}
function impactsHtml(i){if(!i)return ""; const cards=(i.structures||[]).slice(0,24); return `${kv([["Confirmed structures",count(i.count)],["Largest diameter",i.maxDiameterKm==null?"—":fmt(i.maxDiameterKm,1," km")],["With age/impactor detail",count(i.structuresWithExtendedContext||0)]])}${listCards(cards,x=>{const imp=impactorText(x),age=ageText(x);return `<div class="geology-item geology-impact-item"><div><b>${esc(x.name||"Confirmed impact structure")}</b><small>${esc([x.country,x.targetType].filter(Boolean).join(" · "))}</small>${imp?`<small><strong>Impactor:</strong> ${esc(imp)}</small>`:""}${x.reference?`<small>${esc(x.reference)}</small>`:""}</div><span>${[x.diameterKm!=null?fmt(x.diameterKm,1," km"):"",age].filter(Boolean).join(" · ")}</span></div>`;})}${sourceLine(i.source)}<p class="geology-note">Impact ages and impactor properties are shown only when the source provides them. Unknown ancient impactors are never assigned invented names or sizes. Only confirmed structures should be published by the default pipeline; candidate craters are excluded.</p>`;}

function renderPayload(d){const s=d?.sections||{}; const providerNames=(d.providers||[]).map(p=>p.label||p.id).filter(Boolean); const marine=d?.kind==="marine"; return `${providerNames.length?`<div class="geology-provider-strip"><span>Built from</span>${chips(providerNames)}</div>`:""}${details("Seafloor geology & geophysics",seafloorHtml(s.seafloor),marine)}${details("Surface geology",surfaceHtml(s.surfaceGeology),!marine)}${details("Geologic age & mapped units",ageHtml(s.geologicAge))}${details("Minerals & resources",mineralHtml(s.minerals))}${details("Tectonics & seismicity",tectonicsHtml(s.tectonics))}${details("Volcanism",volcanoHtml(s.volcanism))}${details("Geothermal / heat flow",heatHtml(s.geothermal))}${details("Impact structures",impactsHtml(s.impacts))}${!Object.keys(s).length?`<p class="geology-note">No geology providers have been built for this region yet.</p>`:""}`;}

export function createGeologyPanel(config={}){
  const els={tab:document.getElementById("sidebar-tab-geology"),panel:document.getElementById("sidebar-geology-panel"),title:document.getElementById("geology-region-title"),status:document.getElementById("geology-status"),content:document.getElementById("geology-content"),retry:document.getElementById("geology-retry")};
  const service=createGeologyDataService(config); let region=null,data=null,serial=0;
  const active=()=>els.tab?.classList.contains("is-active")&&els.panel&&!els.panel.hidden;
  function reset(message="Open Geology to load mapped geology and geophysics."){data=null;if(els.content){els.content.innerHTML="";els.content.hidden=true;}if(els.retry)els.retry.hidden=true;if(els.status){els.status.hidden=false;els.status.textContent=message;}if(els.title)els.title.textContent=region?.name||"Geology";}
  function render(){if(!data||!els.content||!active())return;els.content.innerHTML=renderPayload(data);els.content.hidden=false;if(els.status)els.status.hidden=true;}
  async function load(){if(!region||region.id==="moon"||!active())return;if(data){render();return;}const n=++serial;if(els.status){els.status.hidden=false;els.status.textContent="Loading geology…";}try{const d=await service.loadRegion(region.id);if(n!==serial)return;if(!d){reset("No geology build is available for this region yet.");return;}data=d;render();}catch(e){if(n!==serial)return;reset(`Geology unavailable: ${e.message}`);if(els.retry)els.retry.hidden=false;}}
  function setRegion(r){serial++;region=r||null;reset(region?.id==="moon"?"Earth geology datasets do not apply to the Moon.":undefined);if(active())load();}
  els.tab?.addEventListener("click",()=>setTimeout(load,0));els.retry?.addEventListener("click",load);return{setRegion,load};
}
