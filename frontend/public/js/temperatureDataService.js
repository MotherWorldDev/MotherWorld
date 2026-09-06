const MODES = ["regionalDailyMean", "spaceTime"];

export function validateTemperatureData(data, regionId) {
  const edges = data?.bins?.edgesC;
  const baseline = data?.baseline;
  if (data?.schemaVersion !== 1 || !["2m_air_temperature", "sea_surface_temperature"].includes(data?.variable) ||
      !Number.isInteger(baseline?.startYear) || !Number.isInteger(baseline?.endYear) || baseline.endYear < baseline.startYear) {
    throw new Error("Invalid climate metadata");
  }
  if (data?.regionId !== regionId || data.unit !== "degC" || !Array.isArray(edges) || edges.length < 3 ||
      !edges.every((v, i) => Number.isFinite(v) && (i === 0 || v > edges[i - 1]))) {
    throw new Error("Invalid temperature inventory");
  }
  for (const mode of MODES) {
    const dist = data.distributions?.[mode];
    const percent = dist?.percent;
    if (!["meanC", "medianC", "p05C", "p95C", "stdC"].every(key => Number.isFinite(dist?.stats?.[key]))) {
      throw new Error("Invalid temperature statistics");
    }
    if (!Array.isArray(percent) || percent.length !== edges.length - 1 ||
        !percent.every(v => Number.isFinite(v) && v >= 0) ||
        Math.abs(percent.reduce((a, b) => a + b, 0) - 100) > 0.05) {
      throw new Error("Invalid temperature distribution");
    }
  }
  return data;
}

export function createTemperatureDataService(config = {}, fetcher = (...args) => fetch(...args)) {
  const indexUrl = config.climateIndexUrl || "./data/climate/climate.index.json";
  const baseUrl = (config.climateBaseUrl || "./data/climate/").replace(/\/?$/, "/");
  const cache = new Map(), pending = new Map();
  let indexPromise;
  async function getJson(url) {
    const res = await fetcher(url);
    if (!res.ok) throw new Error(`Temperature request failed (${res.status})`);
    return res.json();
  }
  function getIndex() {
    if (!indexPromise) indexPromise = getJson(indexUrl).then(index => {
      if (!index?.regions || typeof index.regions !== "object" || Array.isArray(index.regions)) throw new Error("Invalid climate index");
      return index;
    }).catch(error => { indexPromise = null; throw error; });
    return indexPromise;
  }
  async function loadRegion(id) {
    if (!id || id === "moon") return null;
    if (cache.has(id)) { const data = cache.get(id); cache.delete(id); cache.set(id, data); return data; }
    if (pending.has(id)) return pending.get(id);
    const request = (async () => {
      const index = await getIndex(), entry = index.regions[id];
      let data = null;
      if (entry?.url) {
        if (!/^(land|lakes|marine)\/[a-zA-Z0-9_-]+\.temperature\.json$/.test(entry.url)) throw new Error("Invalid climate inventory path");
        const revision = entry.generatedAt || index.generatedAt;
        data = validateTemperatureData(await getJson(baseUrl + entry.url + (revision ? `?v=${encodeURIComponent(revision)}` : "")), id);
      }
      cache.set(id, data);
      while (cache.size > 8) cache.delete(cache.keys().next().value);
      return data;
    })();
    pending.set(id, request);
    try { return await request; } finally { pending.delete(id); }
  }
  return { loadRegion };
}
