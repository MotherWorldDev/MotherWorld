function normalizeRegionSummary(raw) {
  if (!raw) return null;
  return {
    id: raw.id,
    ecoId: raw.ecoId,
    name: raw.name,
    biomeNum: raw.biomeNum,
    biome: raw.biome || "Unknown",
    realm: raw.realm || "Unknown",
    ecoBiomeCode: raw.ecoBiomeCode || "",
    nnhCode: raw.nnhCode ?? null,
    nnhName: raw.nnhName || "",
    areaKm2: Number(raw.areaKm2 ?? 0),
    center: raw.center || null,
    isMarine: raw.isMarine === true,
    isLake: raw.isLake === true,
    climateSummary: raw.climateSummary || "",
    tertiarySummary: raw.tertiarySummary || "",
    plotsSummary: raw.plotsSummary || "",
  };
}

function getMoonSummary() {
  return normalizeRegionSummary({
    id: "moon",
    ecoId: "MOON",
    name: "Moon",
    biomeNum: null,
    biome: "Natural satellite",
    realm: "Earth-Moon system",
    ecoBiomeCode: "LUNA",
    nnhCode: null,
    nnhName: "Lunar body",
    areaKm2: 37_932_300,
    center: null,
    climateSummary:
      "The Moon has no atmosphere to circulate heat. Insolation dominates surface conditions, with extreme temperature swings between sunlit regolith and shadowed terrain.",
    tertiarySummary:
      "Key lunar timers are the 27.32-day sidereal orbit, 29.53-day synodic phase cycle, and the 8.85-year apsidal cycle that shifts perigee and apogee timing.",
    plotsSummary:
      "This view tracks the current apsidal cycle and marks full-moon intervals using geocentric Earth-Moon-Sun geometry from the live Cesium ephemeris.",
  });
}

function getOpenOceanSummary() {
  return normalizeRegionSummary({
    id: "open_ocean",
    ecoId: "OCEAN",
    name: "Open Ocean",
    biomeNum: null,
    biome: "Open ocean",
    realm: "Global ocean",
    ecoBiomeCode: "OCEAN",
    nnhCode: null,
    nnhName: "Pelagic waters beyond mapped ecoregions",
    areaKm2: 361_132_000,
    center: null,
    isMarine: true,
    climateSummary:
      "This is the unmapped ocean remainder outside the marine ecoregion layer. It acts as a generic pelagic fallback, not a formal biogeographic unit.",
    tertiarySummary:
      "Use this as a catch-all ocean selection when the click lands on Earth water but does not intersect a named land or marine region polygon.",
    plotsSummary:
      "This fallback region does not carry dedicated plots yet. It is a synthetic UI convenience layer rather than a source dataset feature.",
  });
}

export function createRegionDataService(config) {
  const mode = config.regionSummaryMode || "local";
  let indexPromise = null;
  let cachedIndex = null;
  let marineIndexPromise = null;
  let cachedMarineIndex = null;
  let lakesIndexPromise = null;
  let cachedLakesIndex = null;

  async function fetchJson(url, label) {
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`${label} request failed (${res.status})`);
    }
    return res.json();
  }

  async function loadIndex() {
    if (cachedIndex) return cachedIndex;
    if (!indexPromise) {
      indexPromise = fetchJson(config.metadataUrl, "Metadata")
        .then((json) => {
          cachedIndex = json;
          return json;
        });
    }
    return indexPromise;
  }

  async function loadMarineIndex() {
    if (!config.marineMetadataUrl) return null;
    if (cachedMarineIndex) return cachedMarineIndex;
    if (!marineIndexPromise) {
      marineIndexPromise = fetchJson(config.marineMetadataUrl, "Marine metadata")
        .then((json) => {
          cachedMarineIndex = json;
          return json;
        })
        .catch((err) => {
          console.warn("Marine metadata fetch failed:", err);
          cachedMarineIndex = null;
          return null;
        });
    }
    return marineIndexPromise;
  }

  async function loadLakesIndex() {
    if (!config.lakesMetadataUrl) return null;
    if (cachedLakesIndex) return cachedLakesIndex;
    if (!lakesIndexPromise) {
      lakesIndexPromise = fetchJson(config.lakesMetadataUrl, "Lakes metadata")
        .then((json) => {
          cachedLakesIndex = json;
          return json;
        })
        .catch((err) => {
          console.warn("Lakes metadata fetch failed:", err);
          cachedLakesIndex = null;
          return null;
        });
    }
    return lakesIndexPromise;
  }

  async function getRegionSummary(regionId) {
    if (!regionId) return null;
    if (regionId === "moon") return getMoonSummary();
    if (regionId === "open_ocean") return getOpenOceanSummary();

    if (mode === "api") {
      try {
        const res = await fetch(`${config.apiBaseUrl}/regions/${encodeURIComponent(regionId)}`);
        if (!res.ok) {
          throw new Error(`API request failed (${res.status})`);
        }
        return normalizeRegionSummary(await res.json());
      } catch (err) {
        console.warn("API summary fetch failed, falling back to local index:", err);
      }
    }

    const [index, marineIndex, lakesIndex] = await Promise.all([loadIndex(), loadMarineIndex(), loadLakesIndex()]);
    return normalizeRegionSummary(
      index?.regions?.[regionId] || marineIndex?.regions?.[regionId] || lakesIndex?.regions?.[regionId] || null
    );
  }

  async function getIndex() {
    return loadIndex();
  }

  async function getMarineIndex() {
    return loadMarineIndex();
  }

  async function getLakesIndex() {
    return loadLakesIndex();
  }

  return {
    getIndex,
    getMarineIndex,
    getLakesIndex,
    getRegionSummary,
  };
}
