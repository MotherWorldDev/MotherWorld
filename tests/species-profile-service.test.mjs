import test from "node:test";
import assert from "node:assert/strict";
import {
  createSpeciesProfileService,
  exactGbifMatch,
  normalizeMedia,
  normalizeWormsTraits,
} from "../frontend/public/js/speciesProfileService.js";

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

const gbifRecord = key => ({
  key,
  rank: "SPECIES",
  scientificName: `Species ${key}`,
  canonicalName: `Species ${key}`,
  accepted: `Species ${key}`,
});

const genericGbifFetcher = async url => {
  const parsed = new URL(url);
  const directRecord = parsed.pathname.match(/^\/v1\/species\/(\d+)$/);
  return directRecord ? response(gbifRecord(Number(directRecord[1]))) : response({ results: [] });
};

test("profile details are lazy, deduplicated by taxon, and cached across regional counts", async () => {
  const calls = [];
  const fetcher = async url => {
    calls.push(url);
    await wait(3);
    return genericGbifFetcher(url);
  };
  const service = createSpeciesProfileService({}, fetcher);
  assert.equal(calls.length, 0, "constructing the service must not fetch provider details");

  const firstItem = { speciesKey: 41, scientificName: "Species 41", kingdom: "Animalia", occurrenceCount: 2 };
  const secondItem = { ...firstItem, occurrenceCount: 99 };
  const [first, concurrent] = await Promise.all([
    service.loadProfile(firstItem),
    service.loadProfile(secondItem),
  ]);

  assert.strictEqual(first, concurrent, "concurrent regional rows for one taxon share one request set");
  assert.equal(calls.length, 8, "one GBIF profile consists of one record plus seven detail requests");
  assert.strictEqual(
    await service.loadProfile({ ...firstItem, occurrenceCount: 17 }),
    first,
    "a later regional count reuses the taxon profile cache",
  );
  assert.equal(calls.length, 8);
});

test("profile requests never exceed four active provider calls", async () => {
  let active = 0;
  let maximum = 0;
  const fetcher = async url => {
    active += 1;
    maximum = Math.max(maximum, active);
    await wait(4);
    active -= 1;
    return genericGbifFetcher(url);
  };
  const service = createSpeciesProfileService({}, fetcher);

  await Promise.all([
    service.loadProfile({ speciesKey: 51, scientificName: "Species 51", kingdom: "Animalia" }),
    service.loadProfile({ speciesKey: 52, scientificName: "Species 52", kingdom: "Plantae" }),
  ]);
  assert.equal(maximum, 4);
});

test("partial provider failures retain successful content and are retried on reopening", async () => {
  const attempts = new Map();
  const fetcher = async url => {
    const parsed = new URL(url);
    const path = parsed.pathname;
    const count = (attempts.get(path) || 0) + 1;
    attempts.set(path, count);
    if (path.endsWith("/speciesProfiles") && count === 1) return response({}, 503);
    if (path.endsWith("/synonyms") && count === 1) throw new Error("temporary synonyms outage");
    if (path.match(/^\/v1\/species\/73$/)) return response(gbifRecord(73));
    if (path.endsWith("/vernacularNames")) return response({ results: [{ vernacularName: "River runner", language: "eng" }] });
    if (path.endsWith("/descriptions")) return response({ results: [{ type: "Summary", description: "A river species." }] });
    if (path.endsWith("/distributions")) return response({ results: [{ country: "Croatia", threatStatus: "Least Concern" }] });
    if (path.endsWith("/synonyms")) return response({ results: [{ scientificName: "Species antiquus" }] });
    if (path.endsWith("/references")) return response({ results: [{ citation: "A useful field guide" }] });
    if (path.endsWith("/speciesProfiles")) return response({ results: [{ habitat: "wetland", size: 0 }] });
    return response({ results: [] });
  };
  const service = createSpeciesProfileService({}, fetcher);
  const item = { speciesKey: 73, scientificName: "Species 73", kingdom: "Animalia" };

  const first = await service.loadProfile(item);
  assert.ok(first.errors.includes("Habitat and traits"));
  assert.ok(first.errors.includes("Synonyms"));
  assert.deepEqual(first.commonNames.map(row => row.name), ["River runner"]);
  assert.deepEqual(first.descriptions.map(row => row.text), ["A river species."]);
  assert.deepEqual(first.distributions.map(row => row.location), ["Croatia"]);
  assert.deepEqual(first.references.map(row => row.citation), ["A useful field guide"]);
  assert.equal(first.synonyms.length, 0);

  const second = await service.loadProfile(item);
  assert.deepEqual(second.errors, []);
  assert.equal(second.traits[0].value, "wetland");
  assert.deepEqual(second.synonyms.map(row => row.name), ["Species antiquus"]);
  assert.equal(attempts.get("/v1/species/73/speciesProfiles"), 2);
  assert.equal(attempts.get("/v1/species/73/synonyms"), 2);
});

test("an aborted profile is rejected and its partial result is not cached", async () => {
  const calls = [];
  const fetcher = (url, { signal }) => new Promise((resolve, reject) => {
    calls.push(url);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      genericGbifFetcher(url).then(resolve, reject);
    }, 25);
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
  const service = createSpeciesProfileService({}, fetcher);
  const controller = new AbortController();
  const item = { speciesKey: 81, scientificName: "Species 81", kingdom: "Animalia" };
  const pending = service.loadProfile(item, { signal: controller.signal });
  await wait(1);
  controller.abort();
  await assert.rejects(pending, error => error.name === "AbortError");
  const callsAfterAbort = calls.length;

  const retry = await service.loadProfile(item);
  assert.deepEqual(retry.errors, []);
  assert.ok(calls.length > callsAfterAbort, "a cancelled profile must be fetched again");
});

test("marine GBIF matching accepts only exact species matches and WoRMS still supplies unmatched traits", async () => {
  const item = { aphiaId: 555, scientificName: "Marineus exactus", kingdom: "Animalia" };
  const exact = {
    matchType: "EXACT",
    rank: "SPECIES",
    confidence: 99,
    canonicalName: "Marineus exactus",
    kingdom: "Animalia",
    usageKey: 9001,
  };
  assert.equal(exactGbifMatch(exact, item), "9001");
  assert.equal(exactGbifMatch({ ...exact, matchType: "FUZZY" }, item), null);
  assert.equal(exactGbifMatch({ ...exact, rank: "GENUS" }, item), null);
  assert.equal(exactGbifMatch({ ...exact, kingdom: "Plantae" }, item), null);
  assert.equal(exactGbifMatch({ ...exact, canonicalName: "Marineus" }, item), null);

  const calls = [];
  const fetcher = async url => {
    calls.push(url);
    const parsed = new URL(url);
    const path = parsed.pathname;
    if (path.endsWith("/AphiaRecordByAphiaID/555")) {
      return response({
        AphiaID: 555,
        scientificname: "Marineus exactus",
        valid_name: "Marineus exactus",
        authority: "Author",
        status: "accepted",
        rank: "Species",
        modified: "2026-01-01",
      });
    }
    if (path.endsWith("/AphiaAttributesByAphiaID/555")) {
      return response([{ measurementType: "Body length", measurementValue: 0, reference: "WoRMS trait source" }]);
    }
    if (path.endsWith("/species/match")) {
      return response({
        matchType: "FUZZY",
        rank: "GENUS",
        confidence: 98,
        canonicalName: "Marineus",
        kingdom: "Animalia",
        usageKey: 9001,
      });
    }
    return response([]);
  };
  const service = createSpeciesProfileService({}, fetcher);
  const profile = await service.loadProfile(item);

  assert.deepEqual(profile.errors, []);
  assert.equal(profile.traits[0].label, "Body length");
  assert.equal(profile.traits[0].value, "0");
  assert.equal(profile.traits[0].source, "WoRMS trait source");
  assert.ok(calls.some(url => new URL(url).pathname.endsWith("/species/match")));
  assert.equal(calls.some(url => /\/v1\/species\/9001$/.test(new URL(url).pathname)), false);
});

test("WoRMS traits preserve numeric zero, nested qualifiers, source, quality and inheritance", () => {
  const traits = normalizeWormsTraits([
    {
      measurementType: "Body length",
      measurementValue: 0,
      reference: "Trait paper",
      source_id: 77,
      AphiaID_Inherited: 12,
      qualitystatus: "estimated",
      children: [
        {
          measurementType: "Sex",
          measurementValue: "female",
          children: [{ measurementType: "Unit", measurementValue: "cm" }],
        },
      ],
    },
    { measurementType: "Dropped", measurementValue: null },
  ], 555);

  assert.deepEqual(traits, [{
    label: "Body length",
    value: "0",
    qualifiers: ["Sex: female", "Unit: cm"],
    source: "Trait paper",
    url: "https://www.marinespecies.org/aphia.php?p=sourcedetails&id=77",
    inherited: "Aphia 12",
    quality: "estimated",
  }]);
});

test("media normalization uses each MEDIA license, requires matching species and safe HTTPS URLs", () => {
  const rows = [
    {
      key: 1,
      speciesKey: 42,
      license: "https://creativecommons.org/licenses/by/4.0/",
      media: [{ type: "StillImage", identifier: "https://images.example/cc-by.jpg", license: "https://creativecommons.org/licenses/by/4.0/", references: "https://source.example/1", creator: "A. Author" }],
    },
    {
      key: 2,
      speciesKey: 42,
      license: "https://creativecommons.org/licenses/by/4.0/",
      media: [{ type: "StillImage", identifier: "https://images.example/bad-media-license.jpg", license: "https://example.org/license" }],
    },
    {
      key: 3,
      speciesKey: 42,
      license: "https://example.org/no-occurrence-license",
      media: [{ type: "StillImage", identifier: "https://images.example/cc-by-sa.jpg", license: "https://creativecommons.org/licenses/by-sa/4.0/", rightsHolder: "Rights Holder" }],
    },
    {
      key: 4,
      speciesKey: 42,
      license: "https://example.org/no-occurrence-license",
      media: [{ type: "StillImage", identifier: "https://images.example/cc0.jpg", license: "https://creativecommons.org/publicdomain/zero/1.0/", publisher: "Publisher" }],
    },
    {
      key: 5,
      speciesKey: 99,
      media: [{ type: "StillImage", identifier: "https://images.example/wrong-species.jpg", license: "https://creativecommons.org/licenses/by/4.0/" }],
    },
    {
      key: 6,
      speciesKey: 42,
      media: [{ type: "StillImage", identifier: "http://images.example/http.jpg", license: "https://creativecommons.org/licenses/by/4.0/" }],
    },
    {
      key: 7,
      speciesKey: 42,
      media: [{ type: "StillImage", identifier: "https://user:password@images.example/credentials.jpg", license: "https://creativecommons.org/licenses/by/4.0/" }],
    },
    {
      key: 8,
      speciesKey: 42,
      media: [{ type: "StillImage", identifier: "https://images.example/fallback-source.jpg", license: "https://creativecommons.org/licenses/by/4.0/", references: "http://source.example/unsafe" }],
    },
  ];
  const images = normalizeMedia(rows, 42);
  const byOriginalUrl = new Map(images.map(image => [image.originalUrl || image.url, image]));

  assert.deepEqual([...byOriginalUrl.keys()], [
    "https://images.example/cc-by.jpg",
    "https://images.example/cc-by-sa.jpg",
    "https://images.example/cc0.jpg",
    "https://images.example/fallback-source.jpg",
  ]);
  for (const image of images) assert.match(image.url, /^https:\/\//);
  assert.equal(byOriginalUrl.get("https://images.example/cc-by.jpg").license, "CC BY 4.0");
  assert.equal(byOriginalUrl.get("https://images.example/cc-by-sa.jpg").license, "CC BY-SA 4.0");
  assert.equal(byOriginalUrl.get("https://images.example/cc0.jpg").license, "CC0");
  assert.equal(byOriginalUrl.get("https://images.example/cc-by.jpg").credit, "A. Author");
  assert.equal(byOriginalUrl.get("https://images.example/cc-by-sa.jpg").credit, "Rights Holder");
  assert.equal(byOriginalUrl.get("https://images.example/cc0.jpg").credit, "Publisher");
  assert.equal(byOriginalUrl.get("https://images.example/fallback-source.jpg").sourceUrl, "https://www.gbif.org/occurrence/8");
});

test("name lookup is explicit, cached after success, and retryable after failure or abort", async () => {
  const calls = [];
  const attempts = new Map();
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    const parsed = new URL(url);
    const query = parsed.searchParams.get("q");
    const count = (attempts.get(query) || 0) + 1;
    attempts.set(query, count);
    if (query === "oak" && count === 1) return response({}, 503);
    return response({
      endOfRecords: false,
      results: [{ key: 7, nubKey: 8, canonicalName: "Quercus robur", species: "Quercus robur", kingdom: "Plantae", ignored: "field" }],
    });
  };
  const service = createSpeciesProfileService({}, fetcher);

  assert.deepEqual(await service.lookupName("x"), { results: [], more: false });
  assert.equal(calls.length, 0);
  await assert.rejects(service.lookupName("oak"), /503/);
  assert.equal(calls.length, 1);

  const oak = await service.lookupName("Oak");
  assert.deepEqual(oak, {
    results: [{ key: 7, nubKey: 8, canonicalName: "Quercus robur", species: "Quercus robur", kingdom: "Plantae" }],
    more: true,
  });
  const oakRequest = new URL(calls[1].url);
  assert.equal(oakRequest.pathname, "/v1/species/search");
  assert.equal(oakRequest.searchParams.get("q"), "Oak");
  assert.equal(oakRequest.searchParams.get("rank"), "SPECIES");
  assert.equal(oakRequest.searchParams.get("datasetKey"), "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c");
  assert.equal(oakRequest.searchParams.get("limit"), "100");
  assert.ok(oakRequest && calls[1].options.signal);
  assert.strictEqual(await service.lookupName("oak"), oak);
  assert.equal(calls.length, 2, "successful normalized queries reuse lookup cache");

  const aborted = new AbortController();
  aborted.abort();
  await assert.rejects(
    service.lookupName("birch", { signal: aborted.signal }),
    error => error.name === "AbortError",
  );
  assert.equal(calls.length, 2, "an already-aborted signal must prevent a provider request");
  const birch = await service.lookupName("birch");
  assert.deepEqual(birch.results[0].canonicalName, "Quercus robur");
  assert.equal(calls.length, 3, "an aborted lookup can be retried and then cached");
});
