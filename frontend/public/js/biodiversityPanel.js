import { createBiodiversityDataService } from "./biodiversityDataService.js?v=20260908-biodiversity-contaminants";

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
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function count(value) {
  const number = numberValue(value);
  return number == null ? "—" : Math.round(number).toLocaleString("en-US");
}

function percent(value) {
  const number = numberValue(value);
  return number == null ? "—" : `${number.toFixed(1)}%`;
}

function covered(metrics) {
  return metrics?.coverageStatus === "covered" && (numberValue(metrics.nativeRasterCellCount) || 0) > 0;
}

function normalizeMetrics(payload) {
  const summary = payload?.summary?.historicalMammals;
  const provider = payload?.providers?.phylacine;
  const providerMetrics = provider?.metrics;
  if (summary) {
    return {
      presentNaturalSpeciesCount: summary.presentNaturalSpeciesCount,
      currentSpeciesCount: summary.currentSpeciesCount,
      locallyLostSpeciesCount: summary.locallyLostSpeciesCount,
      faunalRetentionPct: summary.faunalRetentionPct,
      rangeOccupancyRetentionPct: summary.rangeOccupancyRetentionPct,
      exampleLocallyLostSpecies: summary.exampleLocallyLostSpecies,
      nativeRasterCellCount: summary.nativeRasterCellCount,
      coverageStatus: summary.coverageStatus,
    };
  }
  if (providerMetrics) {
    return {
      presentNaturalSpeciesCount: providerMetrics.presentNaturalMammalSpeciesCount,
      currentSpeciesCount: providerMetrics.currentMammalSpeciesCount,
      locallyLostSpeciesCount: providerMetrics.locallyLostMammalSpeciesCount,
      faunalRetentionPct: providerMetrics.mammalFaunalRetentionPct,
      rangeOccupancyRetentionPct: providerMetrics.mammalRangeOccupancyRetentionPct,
      exampleLocallyLostSpecies: providerMetrics.exampleLocallyLostSpecies,
      nativeRasterCellCount: providerMetrics.nativeRasterCellCount,
      coverageStatus: providerMetrics.coverageStatus,
    };
  }
  return null;
}

function stat(label, value) {
  return `<div class="diagnostic-kv-item"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}

function sourceLink(source) {
  if (!source?.repository) return "";
  let url;
  try {
    url = new URL(source.repository);
  } catch {
    return "";
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return "";
  return `<a class="regional-diagnostic-source" href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(source.label || "Provider repository")}</a>`;
}

function renderPayload(payload) {
  const metrics = normalizeMetrics(payload);
  const source = payload?.providers?.phylacine?.source;
  if (!metrics) {
    return `<div class="regional-diagnostic-unavailable"><strong>Not generated</strong><p>No regional historical-mammal diagnostic is available for this region yet.</p></div>`;
  }

  // Do this gate before formatting any numeric values. A no-native-grid-cell record
  // contains zero placeholders that must not be presented as a biological count.
  if (!covered(metrics)) {
    return `<div class="regional-diagnostic-unavailable"><strong>Insufficient native spatial resolution</strong><p>Historical-mammal counts are unavailable for this region because PHYLACINE has no native raster cell here. The zero placeholders in the source are not an observed zero-fauna result.</p><p class="regional-diagnostic-note">Coverage status: ${esc(metrics.coverageStatus || "not reported")}</p>${sourceLink(source)}</div>`;
  }

  const examples = (metrics.exampleLocallyLostSpecies || []).filter(Boolean).slice(0, 8);
  const method = source?.methodNote || "Historical-mammal context is summarized from the provider's native raster coverage; it is not a population estimate.";
  return `<div class="biodiversity-card">
    <p class="regional-diagnostic-note">Historical mammals from native PHYLACINE raster coverage.</p>
    <div class="diagnostic-kv">
      ${stat("Present natural species", count(metrics.presentNaturalSpeciesCount))}
      ${stat("Current species", count(metrics.currentSpeciesCount))}
      ${stat("Locally lost species", count(metrics.locallyLostSpeciesCount))}
      ${stat("Faunal retention", percent(metrics.faunalRetentionPct))}
      ${stat("Range occupancy retention", percent(metrics.rangeOccupancyRetentionPct))}
      ${stat("Native raster cells", count(metrics.nativeRasterCellCount))}
    </div>
    ${examples.length ? `<h3 class="diagnostic-subheading">Example locally lost species</h3><ul class="diagnostic-list">${examples.map((name) => `<li>${esc(name)}</li>`).join("")}</ul>` : ""}
    <p class="regional-diagnostic-note">${esc(method)}</p>
    ${sourceLink(source)}
  </div>`;
}

export function createBiodiversityPanel(config = {}, fetcher = (...args) => fetch(...args)) {
  const els = {
    tab: document.getElementById("sidebar-tab-biodiversity"),
    panel: document.getElementById("sidebar-biodiversity-panel"),
    title: document.getElementById("biodiversity-region-title"),
    status: document.getElementById("biodiversity-status"),
    content: document.getElementById("biodiversity-content"),
    retry: document.getElementById("biodiversity-retry"),
  };
  const service = createBiodiversityDataService(config, fetcher);
  let region = null;
  let data = null;
  let serial = 0;

  const active = () => els.tab?.classList.contains("is-active") && els.panel && !els.panel.hidden;

  function reset(message = "Open Biodiversity to load regional historical-mammal context.") {
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
    if (els.title) els.title.textContent = region?.name || "Biodiversity";
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
      reset(selected?.id === "moon" ? "Earth biodiversity datasets do not apply to the Moon." : undefined);
      return;
    }
    if (selected.isMarine || selected.isLake) {
      reset("This regional biodiversity panel is scoped to terrestrial ecoregions.");
      return;
    }
    if (!active()) return;
    if (data) {
      render();
      return;
    }
    if (els.status) {
      els.status.hidden = false;
      els.status.textContent = "Loading regional biodiversity…";
    }
    try {
      const payload = await service.loadRegion(selected.id);
      if (requestSerial !== serial) return;
      if (!payload) {
        reset("No generated regional biodiversity diagnostics are available for this region yet.");
        return;
      }
      data = payload;
      if (els.title) els.title.textContent = payload.regionName || selected.name || selected.id;
      render();
    } catch (error) {
      if (requestSerial !== serial) return;
      reset(`Regional biodiversity unavailable: ${error.message}`);
      if (els.retry) els.retry.hidden = false;
    }
  }

  function setRegion(next) {
    serial += 1;
    region = next || null;
    reset(region?.id === "moon" ? "Earth biodiversity datasets do not apply to the Moon." : undefined);
    if (active()) load();
  }

  els.tab?.addEventListener("click", load);
  els.retry?.addEventListener("click", load);
  reset();
  return { setRegion, reload: load };
}
