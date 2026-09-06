import test from "node:test";
import assert from "node:assert/strict";
import { renderSpeciesProfile } from "../frontend/public/js/speciesProfileView.js";

test("escapes identity/source text and rejects unsafe links", () => {
  const html = renderSpeciesProfile({
    record: {
      scientificName: '<script>alert("name")</script>',
      acceptedName: "Safe accepted name",
      kingdom: "Animalia",
    },
    descriptions: [{
      type: "Summary",
      text: '<p>Readable <strong>source</strong></p><script>alert("x")</script>',
      source: "Archive & Data",
      url: "https://source.example/summary",
    }],
    links: [
      { label: "Unsafe", url: "javascript:alert(1)" },
      { label: "Data", url: "data:text/html,alert(1)" },
      { label: "Userinfo", url: "https://user:pass@example.com/profile" },
      { label: "Safe", url: "https://example.com/profile" },
    ],
  });

  assert.match(html, /&lt;script&gt;alert\(&quot;name&quot;\)&lt;\/script&gt;/);
  assert.match(html, /Readable source/);
  assert.match(html, /Archive &amp; Data/);
  assert.doesNotMatch(html, /Archive &amp;amp; Data/);
  assert.doesNotMatch(html, /<script|<img|onerror=/i);
  assert.doesNotMatch(html, /href="(?:javascript|data):/i);
  assert.doesNotMatch(html, /user:pass@/i);
  assert.match(html, /href="https:\/\/example\.com\/profile"[^>]*target="_blank"[^>]*rel="noopener noreferrer"/);
});

test("keeps image attribution, license metadata, and safe media links", () => {
  const html = renderSpeciesProfile({
    record: { scientificName: "Example species" },
    media: [{
      url: "https://images.example/species.jpg",
      sourceUrl: "https://archive.example/item/1",
      licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
      license: "CC BY 4.0",
      credit: "Photographer & Co.",
      title: "Adult specimen",
      date: "2026-09-01T14:57:00Z",
      country: "Croatia",
    }],
  });

  assert.match(html, /class="species-profile-gallery"/);
  assert.match(html, /<figure>[\s\S]*<img[^>]+data-species-photo[^>]+data-src="https:\/\/images\.example\/species\.jpg"[^>]+loading="lazy"[^>]+decoding="async"[^>]+referrerpolicy="no-referrer"/);
  assert.doesNotMatch(html, /<img[^>]+\ssrc=/);
  assert.ok(html.indexOf("species-profile-gallery") < html.indexOf("species-profile-identity"));
  assert.match(html, /Photo: Photographer &amp; Co\./);
  assert.match(html, /Croatia · <time[^>]*>Sep 1, 2026<\/time>/);
  assert.match(html, /<a href="https:\/\/creativecommons\.org\/licenses\/by\/4\.0\/"[^>]*>CC BY 4\.0<\/a>/);
  assert.match(html, /<a href="https:\/\/archive\.example\/item\/1"[^>]*>Source<\/a>/);
  assert.doesNotMatch(html, /Source: <a/);
  assert.doesNotMatch(html, /<img[^>]*javascript/i);
});

test("preserves numeric zero and false values in facts, traits, and distributions", () => {
  const html = renderSpeciesProfile({
    record: { scientificName: "Example species", rank: 0, status: false },
    traits: [{
      label: "Body size",
      value: 0,
      qualifiers: ["Unit: cm", "Type: maximum", "Dimension: length", "Sex: male/unsexed"],
      inherited: false,
      quality: 0,
      source: "Trait source",
    }],
    distributions: [{
      location: 0,
      status: false,
      establishment: 0,
      source: "Range source",
      quality: false,
    }],
    conservation: [{ category: "LEAST_CONCERN", area: 0, source: "Assessment source" }],
  });

  assert.match(html, /<dt>Rank<\/dt><dd>0<\/dd>/);
  assert.match(html, /Source status<\/dt><dd><span class="species-profile-badge">false<\/span>/);
  assert.match(html, /<strong>Body size<\/strong><p>0<\/p>/);
  for (const qualifier of ["Unit: cm", "Type: maximum", "Dimension: length", "Sex: male/unsexed"]) {
    assert.match(html, new RegExp(qualifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(html, /Inherited from higher taxon: false/);
  assert.match(html, /Quality check: 0/);
  assert.match(html, /<strong>0<\/strong>[\s\S]*Status: false[\s\S]*Establishment: 0/);
  assert.match(html, /Quality check: false/);
  assert.match(html, /Area: 0/);
  assert.match(html, /Least concern/);
});

test("renders accepted-name differences, citations, and compact detail sections", () => {
  const html = renderSpeciesProfile({
    record: {
      scientificName: "Old name",
      canonicalName: "Old name",
      acceptedName: "Current name",
      authorship: "(Author, 1900)",
      rank: "SPECIES",
      family: "Exampleidae",
      modified: "2026-09-06T10:20:30Z",
    },
    descriptions: [{ type: "Ecology", text: "A description.", source: "Description source", url: "https://source.example/description" }],
    traits: [{ label: "Length", value: 250, qualifiers: ["Unit: cm"], source: "Trait source", inherited: "Genus", quality: "checked" }],
    synonyms: [{ name: "Earlier name", source: "Name source" }],
    references: [{ citation: "A reference", source: "Reference source", url: "https://source.example/reference" }],
    fetchedAt: "2026-09-06T10:20:30Z",
  });

  assert.match(html, /species-profile-badge">Accepted/);
  assert.match(html, /Current name/);
  assert.match(html, /Taxonomy and name history/);
  assert.doesNotMatch(html, /<dt>Canonical name<\/dt>/);
  assert.match(html, /<dt>Taxonomy record date<\/dt><dd><time[^>]*>Sep 6, 2026<\/time>/);
  assert.match(html, /class="species-profile-sources"/);
  assert.match(html, /Source: <a href="https:\/\/source\.example\/description"/);
  assert.match(html, /250/);
  assert.match(html, /Inherited from higher taxon: Genus/);
  assert.match(html, /Quality check: checked/);
  assert.match(html, /<details[^>]*>[\s\S]*Synonyms/);
  assert.match(html, /<details[^>]*>[\s\S]*References/);
  assert.match(html, /Fetched: <time datetime="2026-09-06T10:20:30Z">Sep 6, 2026, 10:20 AM UTC<\/time>/);
  assert.doesNotMatch(html, /2026-09-07|new Date|current date/i);
});

test("does not invent descriptions or images for missing supplemental arrays", () => {
  const html = renderSpeciesProfile({ record: { scientificName: "Only taxonomy" } });
  assert.match(html, /Only taxonomy/);
  assert.match(html, /No supplemental profile content is available/);
  assert.doesNotMatch(html, /<img|Descriptions|Traits|Distribution reported|Conservation reports/);
});

test("keeps long trait arrays complete behind a native disclosure", () => {
  const html = renderSpeciesProfile({
    record: { scientificName: "Example species" },
    traits: Array.from({ length: 7 }, (_, index) => ({
      label: `Trait ${index + 1}`,
      value: index + 1,
      source: "Trait source",
    })),
  });

  assert.equal((html.match(/class="species-profile-trait"/g) || []).length, 7);
  assert.match(html, /More traits \(1\)/);
  const moreIndex = html.indexOf("More traits (1)");
  assert.ok(html.indexOf("Trait 7") > moreIndex);
});

test("keeps long description arrays complete behind a native disclosure", () => {
  const html = renderSpeciesProfile({
    record: { scientificName: "Example species" },
    descriptions: Array.from({ length: 4 }, (_, index) => ({
      type: `Description ${index + 1}`,
      text: `Text ${index + 1}`,
      source: "Description source",
    })),
  });

  assert.equal((html.match(/class="species-profile-description"/g) || []).length, 4);
  assert.match(html, /More descriptions \(1\)/);
  assert.ok(html.indexOf("Description 4") > html.indexOf("More descriptions (1)"));
});
