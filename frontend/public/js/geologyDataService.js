function baseDir(url) {
  const s = String(url || "./data/geology/geology.index.json");
  const q = s.split("?")[0];
  return q.slice(0, q.lastIndexOf("/") + 1);
}
export function createGeologyDataService(config = {}) {
  const indexUrl = config.geologyIndexUrl || "./data/geology/geology.index.json";
  const baseUrl = String(config.geologyBaseUrl || baseDir(indexUrl)).replace(/\/?$/, "/");
  let indexPromise = null;
  const cache = new Map();
  async function getIndex() {
    if (!indexPromise) indexPromise = fetch(indexUrl).then((r) => {
      if (!r.ok) throw new Error(`geology index request failed (${r.status})`);
      return r.json();
    });
    return indexPromise;
  }
  async function loadRegion(regionId) {
    if (!regionId) return null;
    if (cache.has(regionId)) return cache.get(regionId);
    const index = await getIndex();
    const entry = index?.regions?.[regionId];
    if (!entry?.url) return null;
    const url = entry.url.startsWith("./") || entry.url.startsWith("/") ? entry.url : `${baseUrl}${entry.url}`;
    const promise = fetch(url).then((r) => {
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`geology region request failed (${r.status})`);
      return r.json();
    });
    cache.set(regionId, promise);
    return promise;
  }
  return { getIndex, loadRegion };
}
