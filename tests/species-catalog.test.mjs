import test from "node:test";
import assert from "node:assert/strict";
import {
  createSpeciesCatalog,
  kingdomOf,
  normalize,
  recordCount,
  speciesIdentity,
} from "../frontend/public/js/speciesCatalog.js";

const ids = rows => rows.map(row => row.id);

const species = [
  {
    speciesKey: 1001,
    scientificName: "Hylobates muelleri",
    commonName: "Müller gibbon",
    kingdom: "Animalia",
    phylum: "Chordata",
    class: "Mammalia",
    order: "Primates",
    family: "Hylobatidae",
    genus: "Hylobates",
    occurrenceCount: 42,
  },
  {
    speciesKey: 1002,
    scientificName: "Hylobates agilis",
    commonName: "Agile gibbon",
    kingdom: "Animalia",
    phylum: "Chordata",
    class: "Mammalia",
    order: "Primates",
    family: "Hylobatidae",
    genus: "Hylobates",
    occurrenceCount: 8,
  },
  {
    speciesKey: 1003,
    scientificName: "Symphalangus syndactylus",
    commonName: "Siamang",
    kingdom: "Animalia",
    phylum: "Chordata",
    class: "Mammalia",
    order: "Primates",
    family: "Hylobatidae",
    genus: "Symphalangus",
    occurrenceCount: 21,
  },
  {
    speciesKey: 1004,
    scientificName: "Aquila heliaca",
    commonName: "Imperial eagle",
    kingdom: "Animalia",
    phylum: "Chordata",
    class: "Aves",
    order: "Accipitriformes",
    family: "Accipitridae",
    genus: "Aquila",
    occurrenceCount: 21,
  },
  {
    speciesKey: 2001,
    scientificName: "Quercus robur",
    commonName: "English oak",
    kingdom: "Plantae",
    phylum: "Tracheophyta",
    class: "Magnoliopsida",
    order: "Fagales",
    family: "Fagaceae",
    genus: "Quercus",
    occurrenceCount: 14,
  },
  {
    scientificName: "Pelagia demo",
    commonName: "Demo jellyfish",
    kingdom: "Animalia",
    phylum: "Cnidaria",
    class: "Scyphozoa",
    order: "Semaeostomeae",
    family: "Pelagiidae",
    genus: "Pelagia",
    aphiaId: 7001,
    occurrenceCount: 4,
  },
  {
    // The same name is deliberate: an OBIS match must still respect kingdom.
    aphiaId: 7002,
    scientificName: "Pelagia demo",
    commonName: "Demo sea plant",
    kingdom: "Plantae",
    phylum: "Chlorophyta",
    class: "Ulvophyceae",
    order: "Ulvales",
    family: "Ulvaceae",
    genus: "Pelagia",
    occurrenceCount: 2,
  },
  {
    scientificName: "Mystery taxon",
    commonName: "Unknown organism",
    kingdom: "incertae sedis",
    class: "Unresolved",
    family: "Unresolved family",
  },
  {
    scientificName: "Unclassified moss",
    commonName: "A moss without a kingdom field",
    class: "Bryopsida",
    family: "Bryaceae",
    occurrenceCount: null,
    records: 3,
  },
  {
    scientificName: "Candida example",
    commonName: "Example yeast",
    kingdom: "Fungi",
    class: "Saccharomycetes",
    family: "Saccharomycetaceae",
    occurrenceCount: -5,
  },
];

test("kingdom inventory counts retain missing and incertae sedis records", () => {
  const catalog = createSpeciesCatalog(species);

  assert.deepEqual(catalog.kingdoms, [
    ["Animalia", 5],
    ["Plantae", 2],
    ["Fungi", 1],
    ["Unknown", 2],
  ]);
  assert.equal(kingdomOf({ kingdom: "incertae sedis" }), "Unknown");
  assert.equal(kingdomOf({}), "Unknown");
});

test("search uses all tokens across names, taxonomy and identifiers with accent folding", () => {
  const catalog = createSpeciesCatalog(species);

  assert.equal(normalize("MÜLLER GIBBÓN"), "muller gibbon");
  assert.deepEqual(
    ids(catalog.query({ query: "müller mammalia 1001" })),
    ["gbif:1001"],
  );
  assert.deepEqual(
    ids(catalog.query({ query: "chordata primates", sort: "name" })),
    ["gbif:1002", "gbif:1001", "gbif:1003"],
  );
});

test("combined kingdom, class, family and minimum filters honor requested sorting", () => {
  const catalog = createSpeciesCatalog(species);

  assert.deepEqual(
    ids(catalog.query({
      kingdom: "Animalia",
      class: "Mammalia",
      family: "Hylobatidae",
      minimum: 10,
      sort: "name",
    })),
    ["gbif:1001", "gbif:1003"],
  );
  assert.deepEqual(
    ids(catalog.query({ kingdom: "Animalia", sort: "records" })),
    ["gbif:1001", "gbif:1004", "gbif:1003", "gbif:1002", "worms:7001"],
  );
  assert.deepEqual(
    ids(catalog.query({ kingdom: "Animalia", sort: "taxonomy" })),
    ["gbif:1004", "gbif:1002", "gbif:1001", "gbif:1003", "worms:7001"],
  );
});

test("class and family choices become narrower with kingdom and class selections", () => {
  const catalog = createSpeciesCatalog(species);

  assert.deepEqual(catalog.choices("class", { kingdom: "Animalia" }), [
    ["Aves", 1],
    ["Mammalia", 3],
    ["Scyphozoa", 1],
  ]);
  assert.deepEqual(catalog.choices("family", { kingdom: "Animalia", class: "Mammalia" }), [
    ["Hylobatidae", 3],
  ]);
  assert.deepEqual(catalog.choices("family", { kingdom: "Plantae", class: "Ulvophyceae" }), [
    ["Ulvaceae", 1],
  ]);
});

test("lookup matches GBIF keys and OBIS names only with exact scientific name and kingdom", () => {
  const catalog = createSpeciesCatalog(species);

  assert.deepEqual(
    [...catalog.matchLookup([{ nubKey: 1001, canonicalName: "A different label" }])],
    ["gbif:1001"],
  );
  assert.deepEqual(
    [...catalog.matchLookup([{ key: 2001, canonicalName: "Quercus robur" }])],
    ["gbif:2001"],
  );
  assert.deepEqual(
    [...catalog.matchLookup([{ canonicalName: "Pelagia demo", kingdom: "Animalia" }])],
    ["worms:7001"],
  );
  assert.deepEqual(
    [...catalog.matchLookup([{ canonicalName: "Pelagia demo", kingdom: "Plantae" }])],
    ["worms:7002"],
  );
  assert.deepEqual(
    [...catalog.matchLookup([{ canonicalName: "Pelagia demo", kingdom: "Fungi" }])],
    [],
  );
  assert.deepEqual(
    [...catalog.matchLookup([{ canonicalName: "Pelagia dem", kingdom: "Animalia" }])],
    [],
  );
});

test("profile vernacular names enrich the existing row and become searchable", () => {
  const catalog = createSpeciesCatalog(species);

  catalog.enrich("gbif:1001", ["Canopy dancer", "Borneo forest ape"]);
  assert.deepEqual(ids(catalog.query({ query: "canopy dancer" })), ["gbif:1001"]);
  assert.deepEqual(ids(catalog.query({ query: "borneo forest" })), ["gbif:1001"]);
});

test("identities remain stable and missing, fallback, and negative counts are safe", () => {
  assert.equal(speciesIdentity({ speciesKey: 17, aphiaId: 88, scientificName: "X" }), "gbif:17");
  assert.equal(speciesIdentity({ aphiaId: 88, scientificName: "X" }), "worms:88");
  assert.equal(speciesIdentity({ scientificName: "  PÉTAL  " }), "name:petal");
  assert.equal(recordCount({}), 0);
  assert.equal(recordCount({ occurrenceCount: null, records: 3 }), 3);
  assert.equal(recordCount({ occurrenceCount: -5 }), 0);

  const catalog = createSpeciesCatalog(species);
  assert.equal(catalog.byId.get("gbif:1001").id, "gbif:1001");
  assert.equal(catalog.byId.get("name:unclassified moss").item.scientificName, "Unclassified moss");
  assert.deepEqual(ids(catalog.query({ minimum: 3, sort: "name" })), [
    "gbif:1004",
    "gbif:1002",
    "gbif:1001",
    "worms:7001",
    "gbif:2001",
    "gbif:1003",
    "name:unclassified moss",
  ]);
});
