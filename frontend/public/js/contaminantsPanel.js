import { createContaminantsDataService } from "./contaminantsDataService.js?v=20260908-biodiversity-contaminants";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function numberValue(value) {
  if ((typeof value !== "number" && typeof value !== "string") || (typeof value === "string" && !value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function display(value, suffix = "") {
  const number = numberValue(value);
  return number == null ? "—" : `${number.toLocaleString("en-US")}${suffix}`;
}

function percent(value) {
  const number = numberValue(value);
  return number == null ? "—" : `${number.toFixed(1)}%`;
}

function stat(label, value) {
  return `<div class="diagnostic-kv-item"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function protocolValue(key, value) {
  if (value == null || !String(value).trim() || /^not reported$/i.test(String(value).trim())) return "Not reported";
  if (key === "meshSizeMm" || key === "waterSampleDepthM" || key === "sedimentSampleDepthM") {
    const numeric = numberValue(value);
    const formatted = numeric == null ? String(value) : numeric.toLocaleString("en-US", { maximumFractionDigits: 6 });
    return `${formatted} ${key.endsWith("Mm") ? "mm" : "m"}`;
  }
  return String(value);
}

function protocolHtml(protocol = {}, reportedFields = null) {
  const definitions = [
    ["Marine setting", "marineSetting"],
    ["Sampling method", "samplingMethod"],
    ["Mesh size", "meshSizeMm"],
    ["Water sample depth", "waterSampleDepthM"],
    ["Sediment sample depth", "sedimentSampleDepthM"],
  ];
  const requested = Array.isArray(reportedFields) ? new Set(reportedFields) : null;
  const reportedObject = reportedFields && !Array.isArray(reportedFields) && typeof reportedFields === "object" ? reportedFields : null;
  const values = reportedObject || protocol;
  const fields = definitions.filter(([, key]) => requested ? requested.has(key) : values[key] != null && values[key] !== "");
  if (!fields.length) return "";
  return `<h4 class="diagnostic-subheading">Protocol descriptors</h4><div class="diagnostic-kv">${fields.map(([label, key]) => stat(label, protocolValue(key, values[key]))).join("")}</div>`;
}

function concentration(value, unit) {
  const number = numberValue(value);
  return number == null ? "\u2014" : `${number.toLocaleString("en-US", { maximumSignificantDigits: 6 })} ${unit || ""}`.trim();
}

function analyteHtml(analyte) {
  const protocol = analyte?.protocol || {};
  const years = [analyte?.firstYear, analyte?.lastYear].filter((year) => year != null).join("–");
  return `<details class="contaminant-analyte">
    <summary>${esc(analyte?.name || "Unspecified analyte")} <span>${esc(analyte?.unit || "")}</span></summary>
    <div class="contaminant-analyte-body">
      <div class="diagnostic-kv">
        ${stat("Samples", display(analyte?.sampleCount))}
        ${stat("Quantified values", display(analyte?.quantifiedCount))}
        ${stat("Positive detections", display(analyte?.detectedCount))}
        ${stat("Detection rate", percent(analyte?.detectionRatePct))}
        ${stat("Stations", display(analyte?.stationCount))}
        ${stat("Reported years", years || "—")}
        ${stat("Median positive-reported concentration", concentration(analyte?.medianDetected, analyte?.unit))}
        ${stat("90th percentile positive-reported concentration", concentration(analyte?.p90Detected, analyte?.unit))}
        ${stat("Maximum positive-reported concentration", concentration(analyte?.maxDetected, analyte?.unit))}
      </div>
      ${protocolHtml(protocol)}
      ${analyte?.quantilesReservoirSampled === true ? '<p class="regional-diagnostic-note">Concentration summaries use a sampled subset of positive reported values.</p>' : ""}
      ${analyte?.detectionBasis ? `<p class="regional-diagnostic-note"><strong>Detection basis:</strong> ${esc(analyte.detectionBasis)}</p>` : ""}
    </div>
  </details>`;
}

function measurementHtml(category) {
  const analytes = (category?.analytes || []).filter(Boolean);
  const sharedProtocol = category?.protocol || (category?.protocolFields && !Array.isArray(category.protocolFields) && typeof category.protocolFields === "object" ? category.protocolFields : null);
  return `<div class="diagnostic-kv">
    ${stat("Samples", display(category?.sampleCount))}
    ${stat("Quantified values", display(category?.quantifiedCount))}
    ${stat("Positive detections", display(category?.detectedCount))}
    ${stat("Stations", display(category?.stationCount))}
  </div>
  ${category?.detectionBasis ? `<p class="regional-diagnostic-note"><strong>Detection basis:</strong> ${esc(category.detectionBasis)}</p>` : ""}
  ${sharedProtocol ? protocolHtml(sharedProtocol) : ""}
  ${analytes.length ? `<h4 class="diagnostic-subheading">Analytes</h4><div class="contaminant-analytes">${analytes.map(analyteHtml).join("")}</div>` : ""}
  ${category?.coverageNote ? `<p class="regional-diagnostic-note">${esc(category.coverageNote)}</p>` : ""}`;
}

function incidentHtml(category) {
  const events = (category?.recentEvents || []).filter(Boolean).slice(0, 8);
  return `<div class="diagnostic-kv">
    ${stat("Recorded incidents", display(category?.eventCount))}
    ${stat("First reported year", display(category?.firstYear))}
    ${stat("Last reported year", display(category?.lastYear))}
    ${stat("Known maximum potential release", display(category?.knownPotentialReleaseGallonsSum, " gal"))}
    ${stat("Largest reported maximum", display(category?.maxPotentialReleaseGallons, " gal"))}
  </div>
  <p class="regional-diagnostic-note">Maximum potential release where reported; this is not necessarily the amount actually released.</p>
  ${events.length ? `<h4 class="diagnostic-subheading">Recent reported events</h4><ul class="diagnostic-list">${events.map((event) => `<li><strong>${esc(event.name || "Unnamed incident")}</strong><span>${esc([event.date || event.year, event.location, event.commodity].filter(Boolean).join(" · "))}</span></li>`).join("")}</ul>` : ""}
  ${category?.coverageNote ? `<p class="regional-diagnostic-note">${esc(category.coverageNote)}</p>` : ""}`;
}

function categoryTitle(key, category) {
  return category?.label || key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderSource(source) {
  if (!source?.url) return "";
  let url;
  try {
    url = new URL(source.url);
  } catch {
    return "";
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return "";
  return `<a class="regional-diagnostic-source" href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(source.label || "Source")}</a>`;
}

function renderPayload(payload) {
  const categories = Object.entries(payload?.categories || {}).filter(([, category]) => category && typeof category === "object");
  if (!categories.length) {
    return `<div class="regional-diagnostic-unavailable"><strong>Not generated</strong><p>No regional contaminant observations or incident summaries are available for this region yet.</p></div>`;
  }
  const preferred = ["microplastics", "oil_incidents", "chemical_incidents"];
  const categoryOrder = (key) => {
    const index = preferred.indexOf(key);
    return index < 0 ? preferred.length : index;
  };
  categories.sort(([first], [second]) => categoryOrder(first) - categoryOrder(second));
  const cards = categories.map(([key, category], index) => {
    const body = category.type === "incidents" ? incidentHtml(category) : measurementHtml(category);
    return `<details class="contaminant-card" ${index === 0 ? "open" : ""}><summary><span>${esc(categoryTitle(key, category))}</span><small>${esc(category.type || "Regional summary")}</small></summary><div class="contaminant-card-body">${body}</div></details>`;
  }).join("");
  const warning = payload?.coverageWarning ? `<div class="regional-diagnostic-warning"><strong>Coverage caveat</strong><p>${esc(payload.coverageWarning)}</p></div>` : "";
  const sourceValues = Array.isArray(payload?.sources) ? payload.sources : Object.values(payload?.sources || {});
  const sources = sourceValues.filter(Boolean).map(renderSource).filter(Boolean).join(" · ");
  return `${warning}<div class="contaminant-cards">${cards}</div>${sources ? `<p class="regional-diagnostic-source-list">Sources: ${sources}</p>` : ""}`;
}

export function createContaminantsPanel(config = {}, fetcher = (...args) => fetch(...args)) {
  const els = {
    tab: document.getElementById("sidebar-tab-contaminants"),
    panel: document.getElementById("sidebar-contaminants-panel"),
    title: document.getElementById("contaminants-region-title"),
    status: document.getElementById("contaminants-status"),
    content: document.getElementById("contaminants-content"),
    retry: document.getElementById("contaminants-retry"),
  };
  const service = createContaminantsDataService(config, fetcher);
  let region = null;
  let data = null;
  let serial = 0;

  const active = () => els.tab?.classList.contains("is-active") && els.panel && !els.panel.hidden;

  function reset(message = "Open Contaminants to load regional observations and incidents.") {
    data = null;
    if (els.content) {
      els.content.innerHTML = "";
      els.content.hidden = true;
    }
    if (els.retry) els.retry.hidden = true;
    if (els.status) {
      els.status.hidden = false;
      els.status.textContent = message;
    }
    if (els.title) els.title.textContent = region?.name || "Contaminants";
  }

  function render() {
    if (!data || !els.content || !active()) return;
    els.content.innerHTML = renderPayload(data);
    els.content.hidden = false;
    if (els.status) els.status.hidden = true;
  }

  async function load() {
    const selected = region;
    const requestSerial = ++serial;
    if (!selected || selected.id === "moon") {
      reset(selected?.id === "moon" ? "Earth contaminant datasets do not apply to the Moon." : undefined);
      return;
    }
    if (!selected.isMarine) {
      reset("This regional contaminants panel is scoped to marine ecoregions.");
      return;
    }
    if (!active()) return;
    if (data) {
      render();
      return;
    }
    if (els.status) {
      els.status.hidden = false;
      els.status.textContent = "Loading regional contaminant diagnostics…";
    }
    try {
      const payload = await service.loadRegion(selected.id);
      if (requestSerial !== serial) return;
      if (!payload) {
        reset("No generated regional contaminant diagnostics are available for this region yet.");
        return;
      }
      data = payload;
      if (els.title) els.title.textContent = payload.regionName || selected.name || selected.id;
      render();
    } catch (error) {
      if (requestSerial !== serial) return;
      reset(`Regional contaminants unavailable: ${error.message}`);
      if (els.retry) els.retry.hidden = false;
    }
  }

  function setRegion(next) {
    serial += 1;
    region = next || null;
    reset(region?.id === "moon" ? "Earth contaminant datasets do not apply to the Moon." : undefined);
    if (active()) load();
  }

  els.tab?.addEventListener("click", load);
  els.retry?.addEventListener("click", load);
  reset();
  return { setRegion, reload: load };
}
