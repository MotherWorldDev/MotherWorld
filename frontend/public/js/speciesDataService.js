// Inventories are fetched on demand. Keep only a small number in memory.
export function createSpeciesDataService(config = {}, fetcher = (...args) => fetch(...args)) {
  const indexUrl = config.speciesIndexUrl || "./data/species/species.index.json";
  const baseUrl = (config.speciesBaseUrl || "./data/species/").replace(/\/?$/, "/");
  const cacheLimit = Math.max(1, config.speciesCacheLimit || 6);
  const cache = new Map();
  const pending = new Map();
  let indexPromise;

  async function getJson(url) {
    const response = await fetcher(url);
    if (!response.ok) throw new Error(`Species request failed (${response.status})`);
    if (url.split("?")[0].endsWith(".gz")) {
      const buffer = await response.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      // Some hosts decode gzip during transport; accept either representation.
      if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
        const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
        return new Response(stream).json();
      }
      return JSON.parse(new TextDecoder().decode(buffer));
    }
    return response.json();
  }

  function getIndex() {
    if (!indexPromise) {
      indexPromise = getJson(indexUrl).then((index) => {
        if (!index || typeof index.regions !== "object" || !index.regions) throw new Error("Invalid species index");
        return index;
      }).catch((error) => { indexPromise = null; throw error; });
    }
    return indexPromise;
  }

  async function loadRegion(id) {
    if (!id || id === "moon" || id === "open_ocean") return null;
    if (cache.has(id)) {
      const data = cache.get(id);
      cache.delete(id); cache.set(id, data);
      return data;
    }
    if (pending.has(id)) return pending.get(id);
    const request = (async () => {
      const index = await getIndex();
      const entry = index.regions[id];
      let data = null;
      if (entry?.url) {
        // Manifest paths are relative to the species directory.
        if (!/^(land|lakes|marine)\/[a-zA-Z0-9_-]+\.json(?:\.gz)?$/.test(entry.url)) throw new Error("Invalid inventory path");
        const revision = entry.generatedAt || index.generatedAt;
        data = await getJson(baseUrl + entry.url + (revision ? `?v=${encodeURIComponent(revision)}` : ""));
        if (data?.regionId !== id || !Array.isArray(data.species)) throw new Error("Invalid species inventory");
      }
      cache.set(id, data);
      while (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
      return data;
    })();
    pending.set(id, request);
    try { return await request; } finally { pending.delete(id); }
  }
  return { loadRegion };
}
