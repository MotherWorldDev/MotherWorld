import { createSpeciesDataService } from "./speciesDataService.js?v=20260906-species1";
import { createSpeciesCatalog, KINGDOM_LABELS, recordCount } from "./speciesCatalog.js?v=20260907-species2";
import { createSpeciesProfileService } from "./speciesProfileService.js?v=20260907-species2";
import { renderSpeciesProfile } from "./speciesProfileView.js?v=20260907-species2";

const formatNumber = value => new Intl.NumberFormat("en-US").format(Number(value)||0);
const escapeHtml = value => String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
const defaults = () => ({kingdom:"",class:"",family:"",minimum:"0",sort:"records",query:""});
function displayName(item) {return item.taxonomyResolved===false ? `Unresolved GBIF species ${item.speciesKey}` : item.scientificName||"Unresolved species";}
function iucnCode(value) {return ({CRITICALLY_ENDANGERED:"CR",ENDANGERED:"EN",VULNERABLE:"VU",NEAR_THREATENED:"NT",LEAST_CONCERN:"LC",DATA_DEFICIENT:"DD"})[value]||value||"";}

export function createSpeciesPanel(dataConfig = {}) {
  const ids={tab:"sidebar-tab-tertiary",shell:"species-data-shell",status:"species-data-status",retry:"species-retry",browser:"species-browser",
    toolbar:"species-data-toolbar",kingdoms:"species-kingdom-tabs",search:"species-search-input",lookup:"species-lookup",lookupStatus:"species-lookup-status",
    filtersToggle:"species-filters-toggle",filters:"species-filters",classFilter:"species-class-filter",familyFilter:"species-family-filter",recordFilter:"species-record-filter",sort:"species-sort",clear:"species-clear-filters",
    count:"species-data-count",list:"species-list",results:"species-results-panel",more:"species-show-more",source:"species-data-source",
    profile:"species-profile",profileBack:"species-profile-back",profileTitle:"species-profile-title",profileSubtitle:"species-profile-subtitle",profileContext:"species-profile-context",
    profileStatus:"species-profile-status",profileRetry:"species-profile-retry",profileContent:"species-profile-content",about:"species-inventory-about"};
  const els=Object.fromEntries(Object.entries(ids).map(([key,id])=>[key,document.getElementById(id)]));
  const dataService=createSpeciesDataService(dataConfig), profileService=createSpeciesProfileService(dataConfig);
  let regionId=null, data=null, catalog=null, filters=defaults(), lookupIds=new Set(), visibleLimit=60;
  let selectionVersion=0, searchTimer, lookupController=null, lookupVersion=0, profileController=null, profileVersion=0, selectedRow=null;
  let returnFocusIndex=null, savedScroll=0;
  const isActive=()=>els.tab?.classList.contains("is-active");
  const text=(el,value)=>{if(el)el.textContent=value;};
  const hidden=(el,value)=>{if(el)el.hidden=value;};
  function cancelLookup() {lookupVersion++;lookupController?.abort();lookupController=null;if(els.lookup)els.lookup.disabled=false;}
  function closeProfile({focus=false}={}) {
    profileVersion++;profileController?.abort();profileController=null;selectedRow=null;
    hidden(els.profile,true);hidden(els.browser,false);hidden(els.about,false);
    text(els.profileStatus,"");if(els.profileContent)els.profileContent.innerHTML="";
    if(focus) {
      const container=els.shell?.closest?.(".sidebar-panel");if(container)container.scrollTop=savedScroll;
      els.list?.querySelector?.(`[data-species-index="${returnFocusIndex}"]`)?.focus?.({preventScroll:true});
    }
  }
  function reset(message="Open this tab to load recorded species for the selected region.") {
    clearTimeout(searchTimer);cancelLookup();closeProfile();data=null;catalog=null;filters=defaults();lookupIds=new Set();visibleLimit=60;
    if(els.search)els.search.value="";text(els.lookupStatus,"");text(els.status,message);hidden(els.status,false);hidden(els.retry,true);hidden(els.toolbar,true);
    if(els.kingdoms)els.kingdoms.innerHTML="";if(els.list)els.list.innerHTML="";hidden(els.more,true);text(els.source,"");
    hidden(els.filters,true);els.filtersToggle?.setAttribute?.("aria-expanded","false");
  }
  function syncChoices() {
    if(!catalog)return;
    for(const [field,element,label] of [["class",els.classFilter,"All classes"],["family",els.familyFilter,"All families"]]) {
      if(!element)continue;
      const choices=catalog.choices(field,filters);
      if(filters[field] && !choices.some(([value])=>value===filters[field]))filters[field]="";
      element.innerHTML=`<option value="">${label}</option>`+choices.map(([value,count])=>`<option value="${escapeHtml(value)}">${escapeHtml(value)} (${formatNumber(count)})</option>`).join("");
      element.value=filters[field];element.disabled=!choices.length;
    }
    if(els.recordFilter)els.recordFilter.value=filters.minimum;if(els.sort)els.sort.value=filters.sort;
    const count=[filters.class,filters.family,Number(filters.minimum)>0].filter(Boolean).length;
    text(els.filtersToggle,count?`Filters · ${count}`:"Filters");
  }
  function renderKingdoms() {
    if(!els.kingdoms||!catalog)return;
    const options=[["",catalog.rows.length],...catalog.kingdoms];
    els.kingdoms.innerHTML=options.map(([kingdom,count],index)=>`<button type="button" id="species-kingdom-${index}" role="tab" aria-selected="${filters.kingdom===kingdom}" aria-controls="species-results-panel" tabindex="${filters.kingdom===kingdom?0:-1}" data-kingdom="${escapeHtml(kingdom)}" title="${escapeHtml(kingdom||"All kingdoms")}" class="species-kingdom-tab${filters.kingdom===kingdom?" is-active":""}"><span class="species-kingdom-label">${escapeHtml(kingdom?KINGDOM_LABELS[kingdom]||kingdom:"All")}</span><span class="species-kingdom-count">${formatNumber(count)}</span></button>`).join("");
    const selected=options.findIndex(([kingdom])=>kingdom===filters.kingdom);
    els.results?.setAttribute?.("aria-labelledby",`species-kingdom-${selected}`);
  }
  function renderList() {
    if(!catalog||!els.list)return;
    const found=catalog.query(filters,lookupIds), visible=found.slice(0,visibleLimit), total=catalog.rows.length;
    const narrowed=filters.query||filters.kingdom||filters.class||filters.family||Number(filters.minimum)>0;
    text(els.count,narrowed?`${formatNumber(found.length)} matching / ${formatNumber(total)} recorded species`:`${formatNumber(total)} recorded species`);
    els.list.innerHTML=visible.map(row=>{
      const item=row.item, code=iucnCode(item.iucnRedListCategory), name=displayName(item);
      const taxonomy=[item.class,item.family].filter(Boolean).join(" · ")||item.kingdom||"Taxonomy unavailable";
      return `<button class="species-row" type="button" data-species-index="${row.index}" aria-label="Open profile for ${escapeHtml(name)}"><span class="species-row-main"><span class="species-name">${escapeHtml(name)}</span><span class="species-taxonomy">${escapeHtml(taxonomy)}</span></span><span class="species-row-stats">${code?`<span class="species-iucn">${escapeHtml(code)}</span>`:""}<span class="species-records" title="Occurrence records in this region">${formatNumber(recordCount(item))} rec.</span><span aria-hidden="true">↗</span></span></button>`;
    }).join("");
    if(!visible.length)els.list.innerHTML=`<div class="species-empty">${total?"No species match these filters. Try clearing filters or looking up a common name.":"No species records were found with this inventory's data filters. This does not establish absence."}</div>`;
    hidden(els.more,found.length<=visibleLimit);
    text(els.more,`Show ${formatNumber(Math.min(60,Math.max(0,found.length-visibleLimit)))} more`);
  }
  function applyFilters() {visibleLimit=60;syncChoices();renderList();if(els.list)els.list.scrollTop=0;}
  function setKingdom(kingdom,focus=false) {
    filters.kingdom=kingdom;filters.class="";filters.family="";renderKingdoms();applyFilters();
    if(focus)els.kingdoms?.querySelector?.('[aria-selected="true"]')?.focus?.();
  }
  async function lookupName() {
    clearTimeout(searchTimer);filters.query=els.search?.value||"";
    cancelLookup();lookupIds=new Set();renderList();
    if(filters.query.trim().length<2){text(els.lookupStatus,"Enter at least two characters to look up a name.");return;}
    const version=lookupVersion,selection=selectionVersion,query=filters.query;
    lookupController=new AbortController();if(els.lookup)els.lookup.disabled=true;text(els.lookupStatus,"Looking up names in GBIF…");
    try {
      const result=await profileService.lookupName(query,{signal:lookupController.signal});
      if(version!==lookupVersion||selection!==selectionVersion)return;
      lookupIds=catalog.matchLookup(result.results);applyFilters();
      text(els.lookupStatus,`${formatNumber(lookupIds.size)} species in this region matched the GBIF lookup. ${result.more?"Showing matches from the first 100 results; use a more specific name to narrow the lookup. ":""}Your filters still apply.`);
    } catch(error) {
      if(version!==lookupVersion||selection!==selectionVersion)return;
      text(els.lookupStatus,"Name lookup is unavailable. Local search still works; try the lookup again.");
    } finally {if(version===lookupVersion&&els.lookup)els.lookup.disabled=false;}
  }
  function loadProfilePhotos(version) {
    const photos=Array.from(els.profileContent?.querySelectorAll?.("img[data-species-photo]")||[]);
    function next(index) {
      if(version!==profileVersion || index>=photos.length)return;
      const image=photos[index];
      image.addEventListener("load",()=>next(index+1),{once:true});
      image.addEventListener("error",()=>{image.hidden=true;next(index+1);},{once:true});
      image.src=image.dataset.src;
    }
    next(0);
  }
  async function openProfile(row,{refresh=false}={}) {
    if(!row||!data)return;
    profileController?.abort();const version=++profileVersion,selection=selectionVersion;
    profileController=new AbortController();selectedRow=row;returnFocusIndex=row.index;
    if(!refresh)savedScroll=els.shell?.closest?.(".sidebar-panel")?.scrollTop||0;
    hidden(els.browser,true);hidden(els.about,true);hidden(els.profile,false);hidden(els.profileRetry,true);
    text(els.profileTitle,displayName(row.item));text(els.profileSubtitle,[row.item.family,row.item.kingdom].filter(Boolean).join(" · "));
    text(els.profileContext,`${formatNumber(recordCount(row.item))} occurrence records in ${data.regionName||regionId} · ${data.source||"Regional inventory"}. Counts are records, not population size.`);
    text(els.profileStatus,"Loading names, ecology, photographs and source records…");hidden(els.profileStatus,false);
    if(els.profileContent)els.profileContent.innerHTML=renderSpeciesProfile({record:row.item,links:[]});
    if(!refresh){els.profileTitle?.focus?.({preventScroll:true});const container=els.shell?.closest?.(".sidebar-panel");if(container)container.scrollTop=0;}
    try {
      const profile=await profileService.loadProfile(row.item,{signal:profileController.signal,refresh});
      if(version!==profileVersion||selection!==selectionVersion)return;
      if(els.profileContent)els.profileContent.innerHTML=renderSpeciesProfile(profile);
      loadProfilePhotos(version);
      text(els.profileTitle,profile.record.canonicalName||profile.record.scientificName||displayName(row.item));
      const common=profile.commonNames.find(x=>/^(eng|en|english)$/i.test(x.language||""))||profile.commonNames[0];
      text(els.profileSubtitle,common?.name||[row.item.family,row.item.kingdom].filter(Boolean).join(" · "));
      catalog.enrich(row.id,profile.commonNames.map(x=>x.name));
      if(profile.errors.length) {text(els.profileStatus,`Some details could not be loaded (${profile.errors.join(", ")}). Available source information is shown below.`);hidden(els.profileRetry,false);}
      else {text(els.profileStatus,"");hidden(els.profileStatus,true);}
    } catch(error) {
      if(version!==profileVersion||selection!==selectionVersion)return;
      text(els.profileStatus,"Profile sources could not be reached. The regional inventory is still available. Try again to load details.");hidden(els.profileRetry,false);
    }
  }
  function renderData(inventory) {
    data=inventory;catalog=createSpeciesCatalog(inventory.species);hidden(els.status,true);hidden(els.toolbar,false);
    renderKingdoms();syncChoices();renderList();
    const date=inventory.generatedAt?new Date(inventory.generatedAt).toLocaleDateString():null;
    text(els.source,[`${inventory.source||"Public biodiversity data"} · regional occurrence inventory`,`${formatNumber(inventory.stats?.occurrenceCount)} records`,date?`generated ${date}`:null].filter(Boolean).join(" · "));
  }
  async function ensureLoaded() {
    if(!regionId||regionId==="moon"||!isActive()||data)return;
    hidden(els.shell,false);const version=selectionVersion;reset("Loading recorded species…");
    try {
      const inventory=await dataService.loadRegion(regionId);if(version!==selectionVersion)return;
      if(!inventory){reset("A recorded-species inventory is not available for this region yet.");return;}
      renderData(inventory);
    } catch(error) {
      if(version!==selectionVersion)return;reset("The species inventory could not be loaded. Please try again.");hidden(els.retry,false);
    }
  }
  function setRegion(id) {selectionVersion++;regionId=id||null;reset();text(els.about?.querySelector?.("summary"),regionId==="moon"?"About lunar events":"About this inventory");hidden(els.shell,!regionId||regionId==="moon");if(regionId&&regionId!=="moon"&&isActive())ensureLoaded();}
  els.retry?.addEventListener("click",ensureLoaded);
  els.tab?.addEventListener("click",()=>window.requestAnimationFrame(ensureLoaded));
  els.kingdoms?.addEventListener("click",event=>{const tab=event.target.closest?.("[data-kingdom]");if(tab)setKingdom(tab.dataset.kingdom,true);});
  els.kingdoms?.addEventListener("keydown",event=>{
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)||!catalog)return;
    event.preventDefault();const values=["",...catalog.kingdoms.map(([key])=>key)],index=values.indexOf(filters.kingdom);
    setKingdom(values[event.key==="Home"?0:event.key==="End"?values.length-1:(index+(event.key==="ArrowRight"?1:-1)+values.length)%values.length],true);
  });
  els.search?.addEventListener("input",()=>{
    clearTimeout(searchTimer);cancelLookup();lookupIds=new Set();text(els.lookupStatus,"");
    searchTimer=setTimeout(()=>{filters.query=els.search.value||"";applyFilters();},120);
  });
  els.search?.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();lookupName();}});
  els.lookup?.addEventListener("click",lookupName);
  els.filtersToggle?.addEventListener("click",()=>{if(!els.filters)return;els.filters.hidden=!els.filters.hidden;els.filtersToggle.setAttribute("aria-expanded",String(!els.filters.hidden));});
  for(const [element,field] of [[els.classFilter,"class"],[els.familyFilter,"family"],[els.recordFilter,"minimum"],[els.sort,"sort"]])element?.addEventListener("change",()=>{filters[field]=element.value;if(field==="class")filters.family="";applyFilters();});
  els.clear?.addEventListener("click",()=>{clearTimeout(searchTimer);cancelLookup();filters=defaults();lookupIds=new Set();if(els.search)els.search.value="";text(els.lookupStatus,"");renderKingdoms();applyFilters();});
  els.more?.addEventListener("click",()=>{visibleLimit+=60;renderList();});
  els.list?.addEventListener("click",event=>{const row=event.target.closest?.("[data-species-index]");if(row)openProfile(catalog?.rows[Number(row.dataset.speciesIndex)]);});
  els.profileBack?.addEventListener("click",()=>{renderList();closeProfile({focus:true});});
  els.profileRetry?.addEventListener("click",()=>openProfile(selectedRow,{refresh:true}));
  els.profile?.addEventListener("keydown",event=>{if(event.key==="Escape"){event.preventDefault();renderList();closeProfile({focus:true});}});
  hidden(els.shell,true);
  return {setRegion,ensureLoaded};
}
