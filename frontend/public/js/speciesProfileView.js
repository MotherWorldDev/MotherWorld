/*
 * Render the body of a normalized species profile.
 *
 *
 * Profile content is produced outside the browser and is therefore treated as
 * untrusted at this boundary.  This module deliberately returns a string so
 * the caller can decide where to insert it; it never touches the DOM and never
 * fetches source content.
 */

const BLOCK_TAG_PATTERN = /<(?:br|\/p|\/div|\/li|\/tr|\/h[1-6]|\/section|\/article|\/blockquote|\/ul|\/ol)\b[^>]*>/gi;
const HTML_TAG_PATTERN = /<\/?[a-z][^>]*>/gi;
const ENTITY_PATTERN = /&(#(?:x[\da-f]+|\d+)|[a-z][a-z\d]+);/gi;

const NAMED_ENTITIES = Object.freeze({
  amp: "&",
  apos: "'",
  gt: ">",
  hellip: "…",
  ldquo: "“",
  ldquor: "„",
  lsquo: "‘",
  lsquor: "‚",
  nbsp: " ",
  ndash: "–",
  mdash: "—",
  laquo: "«",
  lt: "<",
  quot: '"',
  raquo: "»",
  rdquo: "”",
  reg: "®",
  rsquo: "’",
  trade: "™",
});

const CONSERVATION_CATEGORY_LABELS = Object.freeze({
  LEAST_CONCERN: "Least concern",
  NOT_EVALUATED: "Not evaluated",
});

function scalar(value) {
  if (value === null || value === undefined) return "";
  try {
    return String(value);
  } catch {
    return "";
  }
}

function hasValue(value) {
  if (value === null || value === undefined) return false;
  return typeof value === "string" ? value.trim().length > 0 : true;
}

function escapeHtml(value) {
  return scalar(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function decodeEntity(match, entity) {
  const lower = entity.toLowerCase();
  if (Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, lower)) {
    return NAMED_ENTITIES[lower];
  }
  if (lower.startsWith("#x")) {
    const codePoint = Number.parseInt(lower.slice(2), 16);
    if (Number.isFinite(codePoint) && codePoint > 0 && codePoint <= 0x10ffff) {
      try {
        return String.fromCodePoint(codePoint);
      } catch {
        return match;
      }
    }
    return match;
  }
  if (lower.startsWith("#")) {
    const codePoint = Number.parseInt(lower.slice(1), 10);
    if (Number.isFinite(codePoint) && codePoint > 0 && codePoint <= 0x10ffff) {
      try {
        return String.fromCodePoint(codePoint);
      } catch {
        return match;
      }
    }
  }
  return match;
}

/**
 * Convert source-supplied HTML to readable text before escaping it for HTML.
 * Script/style content is discarded, block boundaries become line breaks, and
 * any remaining tags are removed.  This is intentionally not an HTML parser:
 * no source markup is ever returned to the browser as markup.
 */
function stripSourceHtml(value) {
  let text = scalar(value);
  if (!text) return "";

  text = text
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<script\b[^>]*>[\s\S]*?(?:<\/script\s*>|$)/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?(?:<\/style\s*>|$)/gi, " ")
    .replace(BLOCK_TAG_PATTERN, "\n")
    .replace(HTML_TAG_PATTERN, "")
    .replace(ENTITY_PATTERN, decodeEntity)
    .replace(/\r\n?/g, "\n")
    .replace(/[\t\f\v ]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text;
}

function displayRaw(value) {
  return escapeHtml(value);
}

function displaySourceText(value) {
  return escapeHtml(stripSourceHtml(value));
}

function displaySourceTextWithBreaks(value) {
  return displaySourceText(value).replaceAll("\n", "<br />");
}

function sameName(left, right) {
  return scalar(left).trim().replace(/\s+/g, " ").toLowerCase() ===
    scalar(right).trim().replace(/\s+/g, " ").toLowerCase();
}

/** Return a normalized https URL, rejecting schemes and userinfo. */
function safeHttpsUrl(value) {
  const raw = scalar(value).trim();
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol.toLowerCase() !== "https:" || !parsed.hostname) return null;

    // URL.username/password do not distinguish an empty userinfo prefix.  An
    // explicit authority check rejects that case too.
    const schemeEnd = raw.indexOf("://");
    const authority = schemeEnd >= 0 ? raw.slice(schemeEnd + 3).split(/[/?#]/, 1)[0] : "";
    if (authority.includes("@") || parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function linkMarkup(label, url) {
  const safeUrl = safeHttpsUrl(url);
  if (!safeUrl) return "";
  const linkLabel = hasValue(label) ? displayRaw(label) : "Source";
  return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${linkLabel}</a>`;
}

function sourceReference(source, url) {
  const sourceText = hasValue(source) ? displaySourceText(source) : "";
  // linkMarkup escapes its label itself; pass the stripped raw text here so
  // ampersands and entities are not double-encoded in source links.
  const sourceLabel = hasValue(source) ? stripSourceHtml(source) : "Source";
  const sourceLink = linkMarkup(sourceLabel || "Source", url);
  return sourceLink || sourceText;
}

function sourceList(source, url, label = "Source") {
  const reference = sourceReference(source, url);
  if (!reference) return "";
  return `<ul class="species-profile-sources"><li>${label}: ${reference}</li></ul>`;
}

function sourceCitation(source, url, label = "Source") {
  const reference = sourceReference(source, url);
  if (!reference) return "";
  return `<small class="species-profile-citation">${label}: ${reference}</small>`;
}

function valueWithLabel(label, value, rich = true) {
  if (!hasValue(value)) return "";
  const rendered = rich ? displaySourceText(value) : displayRaw(value);
  return rendered ? `${label}: ${rendered}` : "";
}

function renderFactRows(rows) {
  return rows
    .filter((row) => row && row.length >= 2 && hasValue(row[1]))
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
}

function formatRecordDate(value) {
  const raw = scalar(value).trim();
  if (!raw) return "";

  // The normalized schema supplies ISO dates. Restrict formatting to ISO-like
  // strings so numeric/boolean values such as 0 and false remain visible.
  if (!/^\d{4}-\d{2}-\d{2}(?:[Tt ][\s\S]*)?$/.test(raw)) return displayRaw(raw);
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return displayRaw(raw);
  const readable = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
  return `<time datetime="${escapeHtml(raw)}">${displayRaw(readable)}</time>`;
}

function renderIdentity(record) {
  const scientificName = record.scientificName;
  const canonicalName = record.canonicalName;
  const acceptedName = record.acceptedName;
  const comparisonName = hasValue(scientificName) ? scientificName : canonicalName;
  const acceptedDiffers = hasValue(acceptedName) &&
    hasValue(comparisonName) && !sameName(acceptedName, comparisonName);

  const scientificValue = hasValue(scientificName)
    ? `<em>${displayRaw(scientificName)}</em>`
    : "—";
  const canonicalValue = hasValue(canonicalName)
    ? `<em>${displayRaw(canonicalName)}</em>`
    : "";
  const acceptedValue = hasValue(acceptedName)
    ? `${acceptedDiffers ? '<span class="species-profile-badge">Accepted</span> ' : ""}<em>${displayRaw(acceptedName)}</em>`
    : "";
  const statusValue = hasValue(record.status)
    ? `<span class="species-profile-badge">${displaySourceText(record.status)}</span>`
    : "";

  const rows = [
    ["Scientific name", scientificValue],
    ["Authorship", hasValue(record.authorship) ? displayRaw(record.authorship) : ""],
    ["Source status", statusValue],
    ["Rank", hasValue(record.rank) ? displayRaw(record.rank) : ""],
    ["Kingdom", hasValue(record.kingdom) ? displayRaw(record.kingdom) : ""],
    ["Phylum", hasValue(record.phylum) ? displayRaw(record.phylum) : ""],
    ["Class", hasValue(record.class) ? displayRaw(record.class) : ""],
    ["Order", hasValue(record.order) ? displayRaw(record.order) : ""],
    ["Family", hasValue(record.family) ? displayRaw(record.family) : ""],
    ["Genus", hasValue(record.genus) ? displayRaw(record.genus) : ""],
    ["Taxonomy record date", hasValue(record.modified) ? formatRecordDate(record.modified) : ""],
  ];
  const nameRows = [];
  if (hasValue(canonicalName) && !(hasValue(scientificName) && sameName(canonicalName, scientificName))) {
    nameRows.push(["Canonical name", canonicalValue]);
  }
  if (hasValue(acceptedName) && !(hasValue(scientificName) && sameName(acceptedName, scientificName))) {
    nameRows.push(["Accepted name", acceptedValue]);
  }
  rows.splice(1, 0, ...nameRows);

  return `<details class="species-profile-section species-profile-identity"><summary>Taxonomy and name history</summary><dl class="species-profile-facts">${renderFactRows(rows)}</dl></details>`;
}

function englishRank(item) {
  const language = scalar(item?.language).trim().toLowerCase();
  if (["en", "eng", "english"].includes(language)) return 0;
  if (!language) return 1;
  return 2;
}

function renderCommonNames(items) {
  const names = items
    .filter((item) => item && hasValue(item.name))
    .map((item, index) => ({ item, index }))
    .sort((left, right) => englishRank(left.item) - englishRank(right.item) || left.index - right.index)
    .map(({ item }) => {
      const metadata = [];
      if (hasValue(item.language)) metadata.push(`Language: ${displaySourceText(item.language)}`);
      const reference = sourceReference(item.source, item.url);
      if (reference) metadata.push(`Source: ${reference}`);
      return `<span>${displayRaw(item.name)}${metadata.length ? `<small>${metadata.join(" · ")}</small>` : ""}</span>`;
    });
  if (!names.length) return "";
  return `<details class="species-profile-section species-profile-common-names"><summary>Common names (${names.length})</summary><div class="species-profile-chips">${names.join("")}</div></details>`;
}

function renderDescriptions(items) {
  const descriptions = items.filter((item) => item && hasValue(item.text));
  if (!descriptions.length) return "";
  const articles = descriptions.map((item) => {
    const type = hasValue(item.type) ? displaySourceText(item.type) : "Description";
    const language = hasValue(item.language)
      ? `<small>Language: ${displaySourceText(item.language)}</small>`
      : "";
    return `<article class="species-profile-description"><h5>${type}</h5><p>${displaySourceTextWithBreaks(item.text)}</p>${language}${sourceCitation(item.source, item.url)}</article>`;
  });
  const previewLimit = 3;
  const preview = descriptions.length > previewLimit ? articles.slice(0, previewLimit) : articles;
  const remainder = descriptions.length > previewLimit ? articles.slice(previewLimit) : [];
  const more = remainder.length
    ? `<details class="species-profile-descriptions-more"><summary>More descriptions (${remainder.length})</summary>${remainder.join("")}</details>`
    : "";
  return `<section class="species-profile-section"><h4>Descriptions</h4>${preview.join("")}${more}</section>`;
}

function renderChips(values) {
  const chips = values
    .filter(hasValue)
    .map((value) => `<span>${displaySourceText(value)}</span>`)
    .join("");
  return chips ? `<div class="species-profile-chips">${chips}</div>` : "";
}

function renderTraits(items) {
  const traits = items.filter((item) => item && (
    hasValue(item.label) || hasValue(item.value) || hasValue(item.source) || hasValue(item.url) ||
    (Array.isArray(item.qualifiers) && item.qualifiers.some(hasValue)) || hasValue(item.inherited) || hasValue(item.quality)
  ));
  if (!traits.length) return "";

  const articles = traits.map((item) => {
    const label = hasValue(item.label) ? displaySourceText(item.label) : "Trait";
    const value = hasValue(item.value) ? displaySourceTextWithBreaks(item.value) : "—";
    const qualifiers = Array.isArray(item.qualifiers) ? renderChips(item.qualifiers) : "";
    const inherited = hasValue(item.inherited)
      ? `<small>Inherited from higher taxon: ${displaySourceText(item.inherited)}</small>`
      : "";
    const quality = hasValue(item.quality)
      ? `<small>Quality check: ${displaySourceText(item.quality)}</small>`
      : "";
    return `<article class="species-profile-trait"><strong>${label}</strong><p>${value}</p>${qualifiers}${inherited}${quality}${sourceCitation(item.source, item.url)}</article>`;
  });
  const previewLimit = 6;
  const preview = traits.length > previewLimit ? articles.slice(0, previewLimit) : articles;
  const remainder = traits.length > previewLimit ? articles.slice(previewLimit) : [];
  const more = remainder.length
    ? `<details class="species-profile-traits-more"><summary>More traits (${remainder.length})</summary>${remainder.join("")}</details>`
    : "";
  return `<section class="species-profile-section"><h4>Traits</h4>${preview.join("")}${more}</section>`;
}

function hasDistributionContent(item) {
  return item && ["location", "status", "establishment", "source", "url", "quality"].some((key) => hasValue(item[key]));
}

function renderDistributions(items) {
  const distributions = items.filter(hasDistributionContent);
  if (!distributions.length) return "";

  const rows = distributions.map((item) => {
    const location = hasValue(item.location) ? displaySourceText(item.location) : "Locality not specified";
    const facts = [
      valueWithLabel("Status", item.status),
      valueWithLabel("Establishment", item.establishment),
    ].filter(Boolean);
    const quality = hasValue(item.quality)
      ? `<small>Quality check: ${displaySourceText(item.quality)}</small>`
      : "";
    return `<li><strong>${location}</strong>${facts.length ? `<div class="species-profile-chips">${facts.map((fact) => `<span>${fact}</span>`).join("")}</div>` : ""}${quality}${sourceList(item.source, item.url)}</li>`;
  });

  const previewLimit = 12;
  const preview = distributions.length > previewLimit ? rows.slice(0, previewLimit) : rows;
  const remainder = distributions.length > previewLimit ? rows.slice(previewLimit) : [];
  const more = remainder.length
    ? `<details class="species-profile-distribution-more"><summary>More distribution records (${remainder.length})</summary><ul class="species-profile-distribution">${remainder.join("")}</ul></details>`
    : "";
  return `<section class="species-profile-section"><h4>Distribution reported by sources</h4><p class="species-profile-note">These records are species-wide and are not proof of presence in selected region.</p><ul class="species-profile-distribution">${preview.join("")}</ul>${more}</section>`;
}

function hasConservationContent(item) {
  return item && ["category", "area", "source", "url"].some((key) => hasValue(item[key]));
}

function renderConservation(items) {
  const reports = items.filter(hasConservationContent);
  if (!reports.length) return "";

  const rows = reports.map((item) => {
    const rawCategory = hasValue(item.category) ? stripSourceHtml(item.category) : "";
    const categoryLabel = CONSERVATION_CATEGORY_LABELS[rawCategory.trim().toUpperCase()] || rawCategory;
    const category = categoryLabel ? displayRaw(categoryLabel) : "Report";
    const area = valueWithLabel("Area", item.area);
    return `<li><strong>${category}</strong>${area ? `<span>${area}</span>` : ""}${sourceList(item.source, item.url)}</li>`;
  });
  return `<section class="species-profile-section"><h4>Conservation reports</h4><p class="species-profile-note">Source records may have different scopes and dates; categories are shown as reported by each source.</p><ul class="species-profile-sources">${rows.join("")}</ul></section>`;
}

function renderMedia(items) {
  const media = items
    .map((item) => {
      if (!item || !safeHttpsUrl(item.url)) return null;
      return { item, url: safeHttpsUrl(item.url) };
    })
    .filter(Boolean);
  if (!media.length) return "";

  const figures = media.map(({ item, url }) => {
    const alt = hasValue(item.title)
      ? displayRaw(item.title)
      : hasValue(item.credit)
        ? displayRaw(item.credit)
        : "Species media";
    const caption = [];
    const placeDate = [
      hasValue(item.country) ? displaySourceText(item.country) : "",
      hasValue(item.date) ? formatRecordDate(item.date) : "",
    ].filter(Boolean);
    if (placeDate.length) caption.push(`<div>${placeDate.join(" · ")}</div>`);
    if (hasValue(item.credit)) caption.push(`<div>Photo: ${displaySourceText(item.credit)}</div>`);

    const mediaLinks = [];
    if (hasValue(item.license)) {
      const licenseText = stripSourceHtml(item.license);
      mediaLinks.push(safeHttpsUrl(item.licenseUrl)
        ? linkMarkup(licenseText || "License", item.licenseUrl)
        : displaySourceText(item.license));
    } else {
      const licenseLink = linkMarkup("License", item.licenseUrl);
      if (licenseLink) mediaLinks.push(licenseLink);
    }
    const sourceLink = linkMarkup("Source", item.sourceUrl);
    if (sourceLink) mediaLinks.push(sourceLink);
    if (mediaLinks.length) caption.push(`<div>${mediaLinks.join(" · ")}</div>`);
    if (!caption.length) caption.push("<div>Species media</div>");
    // The owning panel intentionally assigns src one image at a time. Keep
    // the validated URL available in data-src without starting concurrent
    // image-cache requests while this body is being rendered.
    return `<figure><img data-species-photo data-src="${escapeHtml(url)}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><figcaption>${caption.join("")}</figcaption></figure>`;
  });
  return `<section class="species-profile-section"><h4>Media</h4><div class="species-profile-gallery">${figures.join("")}</div></section>`;
}

function renderSynonyms(items) {
  const synonyms = items.filter((item) => item && hasValue(item.name));
  if (!synonyms.length) return "";
  const rows = synonyms.map((item) => {
    const reference = sourceReference(item.source, item.url);
    return `<li>${displayRaw(item.name)}${reference ? ` <small>Source: ${reference}</small>` : ""}</li>`;
  });
  return `<details class="species-profile-section"><summary>Synonyms (${synonyms.length})</summary><ul class="species-profile-sources">${rows.join("")}</ul></details>`;
}

function renderReferences(items) {
  const references = items.filter((item) => item && (
    hasValue(item.citation) || hasValue(item.source) || hasValue(item.url)
  ));
  if (!references.length) return "";
  const rows = references.map((item) => {
    const citation = hasValue(item.citation) ? displaySourceText(item.citation) : "Reference";
    const reference = sourceReference(item.source, item.url);
    return `<li>${citation}${reference ? ` <small>Source: ${reference}</small>` : ""}</li>`;
  });
  return `<details class="species-profile-section"><summary>References (${references.length})</summary><ol class="species-profile-sources">${rows.join("")}</ol></details>`;
}

function formatFetchedAt(value) {
  const raw = scalar(value).trim();
  if (!raw) return "";
  if (!/^\d{4}-\d{2}-\d{2}(?:[Tt ][\s\S]*)?$/.test(raw)) return displayRaw(raw);
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return displayRaw(raw);
  const readable = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
  return `<time datetime="${escapeHtml(raw)}">${displayRaw(readable)}</time>`;
}

function renderFooter(items, fetchedAt) {
  const links = items
    .map((item) => {
      if (!item || !safeHttpsUrl(item.url)) return null;
      return linkMarkup(hasValue(item.label) ? item.label : "Source link", item.url);
    })
    .filter(Boolean);
  const fetched = hasValue(fetchedAt) ? scalar(fetchedAt).trim() : "";
  if (!links.length && !fetched) return "";
  const linkList = links.length
    ? `<h4>More source links</h4><ul class="species-profile-sources">${links.map((link) => `<li>${link}</li>`).join("")}</ul>`
    : "";
  const fetchedLine = fetched
    ? `<p class="species-profile-note">Fetched: ${formatFetchedAt(fetched)}</p>`
    : "";
  return `<footer class="species-profile-section species-profile-rich-links">${linkList}${fetchedLine}</footer>`;
}

/**
 * Render normalized species profile content as safe HTML.
 * The caller owns the surrounding title, selected-region context, controls,
 * loading state, and error state.
 */
export function renderSpeciesProfile(profile = {}) {
  const input = profile && typeof profile === "object" ? profile : {};
  const record = input.record && typeof input.record === "object" ? input.record : {};
  const commonNames = Array.isArray(input.commonNames) ? input.commonNames : [];
  const descriptions = Array.isArray(input.descriptions) ? input.descriptions : [];
  const traits = Array.isArray(input.traits) ? input.traits : [];
  const distributions = Array.isArray(input.distributions) ? input.distributions : [];
  const synonyms = Array.isArray(input.synonyms) ? input.synonyms : [];
  const references = Array.isArray(input.references) ? input.references : [];
  const media = Array.isArray(input.media) ? input.media : [];
  const conservation = Array.isArray(input.conservation) ? input.conservation : [];
  const links = Array.isArray(input.links) ? input.links : [];

  const mediaContent = renderMedia(media);
  const descriptionsContent = renderDescriptions(descriptions);
  const traitsContent = renderTraits(traits);
  const commonNamesContent = renderCommonNames(commonNames);
  const distributionsContent = renderDistributions(distributions);
  const conservationContent = renderConservation(conservation);
  const synonymsContent = renderSynonyms(synonyms);
  const referencesContent = renderReferences(references);
  const footer = renderFooter(links, input.fetchedAt);
  const supplemental = [
    mediaContent,
    descriptionsContent,
    traitsContent,
    commonNamesContent,
    distributionsContent,
    conservationContent,
    synonymsContent,
    referencesContent,
    links.some((item) => item && safeHttpsUrl(item.url)) ? footer : "",
  ].filter(Boolean);
  const content = [
    descriptionsContent,
    traitsContent,
    renderIdentity(record),
    commonNamesContent,
    distributionsContent,
    conservationContent,
    synonymsContent,
    referencesContent,
  ].filter(Boolean);

  if (!supplemental.length) {
    content.push('<p class="species-profile-note">No supplemental profile content is available from the supplied sources.</p>');
  }

  if (footer) content.push(footer);
  // Media is intentionally first so the owning panel's title and region
  // context are followed by the gallery before long taxonomy/source lists.
  return `${mediaContent}${content.join("")}`;
}
