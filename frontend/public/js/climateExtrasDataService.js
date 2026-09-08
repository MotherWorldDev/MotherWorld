export function createClimateExtrasDataService(dataConfig = {}, fetcher = (...args) => fetch(...args)) {
  const indexUrl = dataConfig.climateExtrasIndexUrl || "./data/climate-extras/climate-extras.index.json";
  const baseUrl = dataConfig.climateExtrasBaseUrl || "./data/climate-extras/";
  const cacheLimit = Math.max(1, Number(dataConfig.climateExtrasCacheLimit || 6));
  let indexPromise = null;
  const cache = new Map();

  async function loadJson(url) {
    const response = await fetcher(url, { cache: "default" });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function absoluteFromBase(rel) {
    if (!rel) return null;
    if (/^(?:https?:)?\/\//.test(rel) || rel.startsWith("./") || rel.startsWith("../") || rel.startsWith("/")) return rel;
    return `${baseUrl.replace(/\/?$/, "/")}${rel}`;
  }

  async function getIndex() {
    if (!indexPromise) indexPromise = loadJson(indexUrl).catch((error) => {
      indexPromise = null;
      throw error;
    });
    return indexPromise;
  }

  async function loadRegion(regionId) {
    if (!regionId) return null;
    if (cache.has(regionId)) {
      const data = cache.get(regionId);
      cache.delete(regionId);
      cache.set(regionId, data);
      return data;
    }
    const index = await getIndex();
    const entry = index?.regions?.[regionId];
    if (!entry?.url) return null;
    const promise = loadJson(absoluteFromBase(entry.url)).catch((error) => {
      cache.delete(regionId);
      throw error;
    });
    cache.set(regionId, promise);
    while (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
    return promise;
  }

  return { getIndex, loadRegion };
}
