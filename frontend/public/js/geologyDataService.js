function baseDir(url) {
  const s = String(url || "./data/geology/geology.index.json");
  const q = s.split("?")[0];
  return q.slice(0, q.lastIndexOf("/") + 1);
}
export function createGeologyDataService(config = {}, fetcher = (...args) => fetch(...args)) {
  const indexUrl = config.geologyIndexUrl || "./data/geology/geology.index.json";
  const baseUrl = String(config.geologyBaseUrl || baseDir(indexUrl)).replace(/\/?$/, "/");
  let indexPromise = null;
  const cacheLimit = Math.max(1, Number(config.geologyCacheLimit || 6));
  const cache = new Map();
  async function getIndex() {
    if (!indexPromise) indexPromise = fetcher(indexUrl).then((r) => {
      if (!r.ok) throw new Error(`geology index request failed (${r.status})`);
      return r.json();
    }).catch((error) => {
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
    const url = entry.url.startsWith("./") || entry.url.startsWith("/") ? entry.url : `${baseUrl}${entry.url}`;
    const promise = fetcher(url).then((r) => {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`geology region request failed (${r.status})`);
      return r.json();
    }).catch((error) => {
      cache.delete(regionId);
      throw error;
    });
    cache.set(regionId, promise);
    while (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
    return promise;
  }
  return { getIndex, loadRegion };
}
