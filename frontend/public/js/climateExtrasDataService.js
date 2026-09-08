export function createClimateExtrasDataService(dataConfig = {}) {
  const indexUrl = dataConfig.climateExtrasIndexUrl || "./data/climate-extras/climate-extras.index.json";
  const baseUrl = dataConfig.climateExtrasBaseUrl || "./data/climate-extras/";
  let indexPromise = null;
  const cache = new Map();

  async function loadJson(url) {
    const response = await fetch(url, { cache: "default" });
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
    if (cache.has(regionId)) return cache.get(regionId);
    const index = await getIndex();
    const entry = index?.regions?.[regionId];
    if (!entry?.url) return null;
    const promise = loadJson(absoluteFromBase(entry.url)).catch((error) => {
      cache.delete(regionId);
      throw error;
    });
    cache.set(regionId, promise);
    return promise;
  }

  return { getIndex, loadRegion };
}
