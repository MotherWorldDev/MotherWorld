export const KINGDOM_LABELS = {Animalia:"Animals",Plantae:"Plants",Fungi:"Fungi",Chromista:"Chromista",Protozoa:"Protozoa",Bacteria:"Bacteria",Archaea:"Archaea",Viruses:"Viruses",Unknown:"Unclassified"};
export const normalize = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
export const speciesIdentity = item => item.speciesKey ? `gbif:${item.speciesKey}` : item.aphiaId ? `worms:${item.aphiaId}` : `name:${normalize(item.scientificName)}`;
export const kingdomOf = item => item.kingdom && item.kingdom !== "incertae sedis" ? item.kingdom : "Unknown";
export const recordCount = item => Math.max(0, Number(item.occurrenceCount ?? item.records) || 0);
const collator = new Intl.Collator("en", {numeric:true,sensitivity:"base"});

export function createSpeciesCatalog(species = []) {
  const rows = species.map((item, index) => ({item,index,id:speciesIdentity(item),kingdom:kingdomOf(item),
    search:normalize([item.scientificName,item.commonName,item.kingdom,item.phylum,item.class,item.order,item.family,item.genus,item.speciesKey,item.aphiaId].filter(Boolean).join(" "))}));
  const byId = new Map(rows.map(row=>[row.id,row]));
  const marineByName=new Map();
  for(const row of rows) if(row.item.aphiaId) {
    const name=normalize(row.item.scientificName);
    if(!marineByName.has(name))marineByName.set(name,[]);
    marineByName.get(name).push(row);
  }
  const kingdoms = new Map();
  rows.forEach(row=>kingdoms.set(row.kingdom,(kingdoms.get(row.kingdom)||0)+1));
  const orderedKingdoms = [...kingdoms].sort((a,b)=>{
    const order=Object.keys(KINGDOM_LABELS); return (order.indexOf(a[0])<0?99:order.indexOf(a[0]))-(order.indexOf(b[0])<0?99:order.indexOf(b[0]));
  });
  function query(filters = {}, lookupIds = new Set()) {
    const tokens=normalize(filters.query).split(/\s+/).filter(Boolean);
    const found=rows.filter(row => (!filters.kingdom || row.kingdom===filters.kingdom) &&
      (!filters.class || row.item.class===filters.class) && (!filters.family || row.item.family===filters.family) &&
      recordCount(row.item)>=Number(filters.minimum||0) &&
      (!tokens.length || tokens.every(token=>row.search.includes(token)) || lookupIds.has(row.id)));
    found.sort((a,b)=> filters.sort === "name" ? collator.compare(a.item.scientificName,b.item.scientificName) :
      filters.sort === "taxonomy" ? collator.compare(a.item.class||"~",b.item.class||"~") || collator.compare(a.item.scientificName,b.item.scientificName) :
      recordCount(b.item)-recordCount(a.item) || collator.compare(a.item.scientificName,b.item.scientificName));
    return found;
  }
  function choices(field, filters = {}) {
    const counts=new Map();
    for(const row of rows) {
      if(filters.kingdom && row.kingdom!==filters.kingdom)continue;
      if(field==="family" && filters.class && row.item.class!==filters.class)continue;
      const value=row.item[field];if(value)counts.set(value,(counts.get(value)||0)+1);
    }
    return [...counts].sort((a,b)=>collator.compare(a[0],b[0]));
  }
  function matchLookup(results) {
    const ids=new Set();
    for(const result of results) {
      const id=`gbif:${result.nubKey || result.key}`;
      if(byId.has(id)) {ids.add(id);continue;}
      const name=normalize(result.canonicalName || result.species);
      if(!name)continue;
      for(const row of marineByName.get(name)||[]) if(!result.kingdom || result.kingdom===row.item.kingdom)ids.add(row.id);
    }
    return ids;
  }
  function enrich(id, names) {
    const row=byId.get(id); if(!row)return;
    row.aliases ||= new Set();
    for(const name of names) {const value=normalize(name);if(value&&!row.aliases.has(value)){row.aliases.add(value);row.search+=` ${value}`;}}
  }
  return {rows,byId,kingdoms:orderedKingdoms,query,choices,matchLookup,enrich};
}
