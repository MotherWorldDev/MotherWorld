function baseDir(url) {
  const clean = String(url || "./data/biodiversity/biodiversity.index.json").split("?")[0];
  return clean.slice(0, clean.lastIndexOf("/") + 1);
}

function resolveUrl(baseUrl, entryUrl) {
  const value = String(entryUrl || "");
  if (/^(?:https?:)?\/\//i.test(value) || value.startsWith("/") || value.startsWith("./")) return value;
  return `${baseUrl}${value}`;
}

export function createBiodiversityDataService(config = {}, fetcher = (...args) => fetch(...args)) {
  const indexUrl = config.biodiversityIndexUrl || "./data/biodiversity/biodiversity.index.json";
  const baseUrl = String(config.biodiversityBaseUrl || baseDir(indexUrl)).replace(/\/?$/, "/");
  const cacheLimit = Math.max(1, Number(config.biodiversityCacheLimit || 6));
  let indexPromise = null;
  const cache = new Map();

  async function getIndex() {
    if (!indexPromise) {
      indexPromise = fetcher(indexUrl).then((response) => {
        if (!response.ok) throw new Error(`biodiversity index request failed (${response.status})`);
        return response.json();
      }).catch((error) => {
        indexPromise = null;
        throw error;
      });
    }
    return indexPromise;
  }

  async function loadRegion(regionId) {
    if (!regionId) return null;

    if (cache.has(regionId)) {
      const value = cache.get(regionId);
      cache.delete(regionId);
      cache.set(regionId, value);
      return value;
    }

    const index = await getIndex();
    if (cache.has(regionId)) {
      const value = cache.get(regionId);
      cache.delete(regionId);
      cache.set(regionId, value);
      return value;
    }

    const entry = index?.regions?.[regionId];
    if (!entry?.url) return null;
    const url = resolveUrl(baseUrl, entry.url);
    const promise = fetcher(url).then((response) => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error(`biodiversity region request failed (${response.status})`);
      return response.json();
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
