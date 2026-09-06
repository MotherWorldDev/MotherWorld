import { occurrenceImageUrl } from "./speciesMedia.js?v=20260907-species2";
import { normalize, speciesIdentity } from "./speciesCatalog.js?v=20260907-species2";

const GBIF = "https://api.gbif.org/v1";
const WORMS = "https://www.marinespecies.org/rest";
const BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c";
const numericId = value => /^[1-9]\d*$/.test(String(value ?? "")) ? String(value) : null;
const gbifPage = key => `https://www.gbif.org/species/${key}`;
const wormsPage = key => `https://www.marinespecies.org/aphia.php?p=taxdetails&id=${key}`;
const ranks = ["kingdom","phylum","class","order","family","genus"];
const list = value => value == null ? [] : Array.isArray(value) ? value : Array.isArray(value.results) ? value.results : [];
const unique = (items, key) => [...new Map(items.map(item=>[key(item),item])).values()];
export function safeExternalUrl(value) {
  try { const url=new URL(value); return url.protocol === "https:" && !url.username && !url.password ? url.href : null; } catch {return null;}
}
function sourceUrl(row, fallback) {return numericId(row.sourceTaxonKey) ? gbifPage(row.sourceTaxonKey) : fallback;}
function licenseInfo(value) {
  try {
    const url=new URL(String(value));
    if(!["http:","https:"].includes(url.protocol) || !["creativecommons.org","www.creativecommons.org"].includes(url.hostname))return null;
    const cc=url.pathname.match(/^\/licenses\/(by|by-sa)\/(\d\.\d)(?:\/|$)/);
    if(cc)return {license:`CC ${cc[1].toUpperCase()} ${cc[2]}`,licenseUrl:`https://creativecommons.org/licenses/${cc[1]}/${cc[2]}/`};
    if(/^\/publicdomain\/(zero|mark)\/1\.0(?:\/|$)/.test(url.pathname))return {license:url.pathname.includes("zero")?"CC0":"Public domain",licenseUrl:`https://creativecommons.org${url.pathname}`};
  } catch {}
  return null;
}
export function normalizeMedia(rows, taxonKey) {
  const images=[];
  for(const occurrence of rows) {
    if(String(occurrence.speciesKey)!==String(taxonKey))continue;
    for(const media of occurrence.media || []) {
      const license=licenseInfo(media.license);
      const url=safeExternalUrl(media.identifier);
      if(media.type!=="StillImage" || !license || !url)continue;
      images.push({url:occurrenceImageUrl(occurrence.key,media.identifier)||url,originalUrl:url,...license,sourceUrl:safeExternalUrl(media.references)||`https://www.gbif.org/occurrence/${occurrence.key}`,
        credit:media.creator||media.rightsHolder||media.publisher||"Contributor via GBIF",title:occurrence.scientificName,
        date:occurrence.eventDate||null,country:occurrence.country||null});
      break; // Prefer different observations over several photos from the same sighting.
    }
  }
  return unique(images,x=>x.url).slice(0,4);
}
export function normalizeWormsTraits(attributes, speciesId) {
  return attributes.filter(row=>row.measurementType && row.measurementValue != null).map(row=>{
    const qualifiers=[];
    function visit(children, depth=0) {
      if(depth>8)return;
      for(const child of children || []) {
        if(child.measurementType && child.measurementValue!=null)qualifiers.push(`${child.measurementType}: ${child.measurementValue}`);
        visit(child.children,depth+1);
      }
    }
    visit(row.children);
    return {label:row.measurementType,value:String(row.measurementValue),qualifiers,
      source:row.reference||"WoRMS",url:numericId(row.source_id)?`https://www.marinespecies.org/aphia.php?p=sourcedetails&id=${row.source_id}`:wormsPage(speciesId),
      inherited:row.AphiaID_Inherited && String(row.AphiaID_Inherited)!==String(speciesId)?`Aphia ${row.AphiaID_Inherited}`:null,quality:row.qualitystatus||null};
  });
}
export function exactGbifMatch(match, item) {
  return match?.matchType==="EXACT" && match.rank==="SPECIES" && match.confidence>=95 &&
    normalize(match.canonicalName)===normalize(item.scientificName) &&
    (!item.kingdom || match.kingdom===item.kingdom) ? numericId(match.usageKey) : null;
}

export function createSpeciesProfileService(config = {}, fetcher = (...args)=>fetch(...args)) {
  const cache=new Map(), pending=new Map(), lookupCache=new Map();
  const maxProfiles=24, ttl=6*60*60*1000;
  const timeoutMs=config.speciesProfileTimeoutMs || 12000;
  let active=0; const queue=[];
  async function getJson(url, signal) {
    if(signal?.aborted)throw new DOMException("Cancelled","AbortError");
    // Four requests across all profiles/lookups, including work queued before cancellation.
    if(active>=4)await new Promise(resolve=>queue.push(resolve));
    else active++;
    const controller=new AbortController();
    const abort=()=>controller.abort(); signal?.addEventListener("abort",abort,{once:true});
    const timer=setTimeout(()=>controller.abort(),timeoutMs);
    try {
      if(signal?.aborted)throw new DOMException("Cancelled","AbortError");
      const response=await fetcher(url,{signal:controller.signal,credentials:"omit",headers:{Accept:"application/json"}});
      if(response.status===204 || response.status===404)return null;
      if(!response.ok)throw new Error(`Source request failed (${response.status})`);
      return await response.json();
    } finally {
      clearTimeout(timer);signal?.removeEventListener("abort",abort);
      const next=queue.shift();if(next)next();else active--;
    }
  }
  async function buildProfile(item, signal) {
    const profile={record:{scientificName:item.scientificName,canonicalName:item.scientificName,...Object.fromEntries(ranks.map(rank=>[rank,item[rank]]))},
      commonNames:[],descriptions:[],traits:[],distributions:[],synonyms:[],references:[],media:[],conservation:[],links:[],errors:[],fetchedAt:new Date().toISOString()};
    const attempt=async(label,fn)=>{
      try{return await fn();} catch(error){if(signal?.aborted)throw error;profile.errors.push(label);return null;}
    };
    const wormsId=numericId(item.aphiaId), suppliedGbifId=numericId(item.speciesKey);
    const addLink=(label,url)=>profile.links.push({label,url});
    let wormsRecord=null;
    const wormsTask=wormsId ? attempt("WoRMS", async()=>{
      addLink("WoRMS species record",wormsPage(wormsId));
      const record=await getJson(`${WORMS}/AphiaRecordByAphiaID/${wormsId}`,signal);
      if(!record || String(record.AphiaID)!==wormsId)throw new Error("WoRMS record unavailable");
      wormsRecord=record;
      Object.assign(profile.record,{scientificName:record.scientificname,canonicalName:record.scientificname,acceptedName:record.valid_name,
        authorship:record.authority,status:record.status,rank:record.rank,modified:record.modified,
        ...Object.fromEntries(ranks.map(rank=>[rank,record[rank]||item[rank]]))});
      if(record.citation)profile.references.push({citation:record.citation,source:"WoRMS",url:wormsPage(wormsId)});
      const acceptedId=numericId(record.valid_AphiaID)||wormsId;
      for(const [field,label] of [["isMarine","Marine"],["isBrackish","Brackish water"],["isFreshwater","Freshwater"],["isTerrestrial","Terrestrial"],["isExtinct","Extinct"]]) {
        if(record[field]===0 || record[field]===1)profile.traits.push({label,value:record[field]===1?"Yes":"No",source:"WoRMS",url:wormsPage(wormsId),qualifiers:[]});
      }
      const tasks=[
        ["WoRMS common names",`AphiaVernacularsByAphiaID/${acceptedId}`,data=>list(data).forEach(row=>profile.commonNames.push({name:row.vernacular,language:row.language||row.language_code,source:"WoRMS",url:wormsPage(acceptedId)}))],
        ["WoRMS traits",`AphiaAttributesByAphiaID/${acceptedId}`,data=>profile.traits.push(...normalizeWormsTraits(list(data),acceptedId))],
        ["WoRMS distribution",`AphiaDistributionsByAphiaID/${acceptedId}`,data=>list(data).forEach(row=>profile.distributions.push({location:row.locality||row.higherGeography,status:row.occurrence||row.recordStatus,establishment:row.establishmentMeans,quality:row.qualityStatus,source:"WoRMS",url:wormsPage(acceptedId)}))],
        ["WoRMS synonyms",`AphiaSynonymsByAphiaID/${acceptedId}`,data=>list(data).forEach(row=>profile.synonyms.push({name:row.scientificname,source:"WoRMS",url:wormsPage(row.AphiaID)}))],
        ["WoRMS references",`AphiaSourcesByAphiaID/${acceptedId}`,data=>list(data).forEach(row=>profile.references.push({citation:row.reference,source:"WoRMS",url:safeExternalUrl(row.url)||wormsPage(acceptedId)}))]
      ];
      await Promise.all(tasks.map(([label,path,accept])=>attempt(label,async()=>accept(await getJson(`${WORMS}/${path}`,signal)))));
    }) : Promise.resolve();
    const gbifTask=attempt("GBIF",async()=>{
      let key=suppliedGbifId;
      if(!key && wormsId && item.scientificName) {
        const params=new URLSearchParams({name:item.scientificName,strict:"true",...(item.kingdom?{kingdom:item.kingdom}:{})});
        key=exactGbifMatch(await getJson(`${GBIF}/species/match?${params}`,signal),item);
      }
      if(!key)return;
      addLink("GBIF species record",gbifPage(key));
      const record=await getJson(`${GBIF}/species/${key}`,signal);
      if(!record || String(record.key)!==key || record.rank!=="SPECIES")throw new Error("GBIF species record unavailable");
      if(!wormsId)Object.assign(profile.record,{scientificName:record.scientificName,canonicalName:record.canonicalName,acceptedName:record.accepted,
        authorship:record.authorship,status:record.taxonomicStatus,rank:record.rank,modified:record.lastInterpreted,
        ...Object.fromEntries(ranks.map(rank=>[rank,record[rank]||item[rank]]))});
      if(record.vernacularName)profile.commonNames.push({name:record.vernacularName,language:"",source:"GBIF",url:gbifPage(key)});
      const acceptedKey=numericId(record.acceptedKey)||key;
      const tasks=[
        ["Common names","vernacularNames?limit=100",data=>list(data).forEach(row=>profile.commonNames.push({name:row.vernacularName,language:row.language,source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))}))],
        ["Descriptions","descriptions?limit=40",data=>list(data).forEach(row=>profile.descriptions.push({type:row.type||"Description",text:row.description,language:row.language,source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))}))],
        ["Habitat and traits","speciesProfiles?limit=40",data=>list(data).forEach(row=>{
          for(const field of ["habitat","lifeForm","livingPeriod","size","mass","age","marine","freshwater","terrestrial","extinct"]) {
            if(row[field]!=null && row[field]!=="")profile.traits.push({label:field.replace(/([A-Z])/g," $1"),value:typeof row[field]==="boolean"?(row[field]?"Yes":"No"):String(row[field]),qualifiers:[],source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))});
          }
        })],
        ["Distribution and conservation","distributions?limit=100",data=>list(data).forEach(row=>{
          if(row.locality||row.country)profile.distributions.push({location:row.locality||row.country,status:row.status,establishment:row.establishmentMeans,source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))});
          if(row.threatStatus)profile.conservation.push({category:row.threatStatus,area:row.locality||row.country||"Scope not supplied",source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))});
        })],
        ["Synonyms","synonyms?limit=30",data=>list(data).forEach(row=>profile.synonyms.push({name:row.scientificName,source:"GBIF",url:gbifPage(row.key)}))],
        ["References","references?limit=20",data=>list(data).forEach(row=>profile.references.push({citation:row.citation,source:row.source||"GBIF",url:sourceUrl(row,gbifPage(acceptedKey))}))]
      ];
      await Promise.all([
        ...tasks.map(([label,path,accept])=>attempt(label,async()=>accept(await getJson(`${GBIF}/species/${acceptedKey}/${path}`,signal)))),
        attempt("Photographs",async()=>{
          const data=await getJson(`${GBIF}/occurrence/search?taxonKey=${acceptedKey}&mediaType=StillImage&license=CC_BY_4_0&license=CC0_1_0&limit=12`,signal);
          profile.media=normalizeMedia(list(data),acceptedKey);
        })
      ]);
    });
    await Promise.all([wormsTask,gbifTask]);
    if(signal?.aborted)throw new DOMException("Cancelled","AbortError");
    // GBIF supplements marine profiles; the WoRMS record remains their taxonomic reference.
    if(wormsId && !wormsRecord && !profile.errors.includes("WoRMS"))profile.errors.push("WoRMS");
    profile.commonNames=unique(profile.commonNames.filter(row=>row.name),row=>normalize(row.name)+normalize(row.language)).sort((a,b)=>Number(!/^(eng|en|english)$/i.test(a.language||""))-Number(!/^(eng|en|english)$/i.test(b.language||"")));
    profile.descriptions=unique(profile.descriptions.filter(row=>row.text),row=>row.type+row.text+row.source);
    profile.traits=unique(profile.traits,row=>JSON.stringify([row.label,row.value,row.qualifiers,row.source]));
    const traitPriority=row=>/body size|feeding|trophic|reproduction|life span|longevity|habitat|environment|depth|life form|temperature|salinity/i.test(row.label)?0:1;
    profile.traits.sort((a,b)=>traitPriority(a)-traitPriority(b)||a.label.localeCompare(b.label)||a.value.localeCompare(b.value));
    profile.distributions=unique(profile.distributions.filter(row=>row.location),row=>JSON.stringify([row.location,row.status,row.establishment,row.source]));
    profile.synonyms=unique(profile.synonyms.filter(row=>row.name),row=>row.name);
    profile.references=unique(profile.references.filter(row=>row.citation),row=>row.citation);
    profile.conservation=unique(profile.conservation,row=>JSON.stringify(row));
    return profile;
  }
  async function loadProfile(item, {signal,refresh=false} = {}) {
    const id=speciesIdentity(item), cached=cache.get(id);
    if(!refresh && cached && Date.now()-cached.time<ttl) {cache.delete(id);cache.set(id,cached);return cached.data;}
    const existing=pending.get(id);
    if(existing && !existing.signal?.aborted)return existing.promise;
    const promise=buildProfile(item,signal).then(data=>{
      if(!data.errors.length) {cache.set(id,{time:Date.now(),data});while(cache.size>maxProfiles)cache.delete(cache.keys().next().value);}
      return data;
    });
    pending.set(id,{promise,signal});
    try{return await promise;} finally {if(pending.get(id)?.promise===promise)pending.delete(id);}
  }
  async function lookupName(query, {signal} = {}) {
    const key=normalize(query);if(key.length<2)return {results:[],more:false};
    if(lookupCache.has(key))return lookupCache.get(key);
    const params=new URLSearchParams({q:query,rank:"SPECIES",datasetKey:BACKBONE,limit:"100"});
    const payload=await getJson(`${GBIF}/species/search?${params}`,signal);
    if(!payload || !Array.isArray(payload.results))throw new Error("Name lookup unavailable");
    const data={results:payload.results.map(row=>({key:row.key,nubKey:row.nubKey,canonicalName:row.canonicalName,species:row.species,kingdom:row.kingdom})),more:!payload.endOfRecords};
    lookupCache.set(key,data);while(lookupCache.size>20)lookupCache.delete(lookupCache.keys().next().value);
    return data;
  }
  return {loadProfile,lookupName};
}
