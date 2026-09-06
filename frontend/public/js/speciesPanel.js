import { createSpeciesDataService } from "./speciesDataService.js?v=20260906-species1";

function formatNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat("en-US").format(n) : "0";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function iucnClass(category) {
  const c = String(category || "").trim().toUpperCase();
  if (["CR", "CRITICALLY_ENDANGERED"].includes(c)) return "is-critical";
  if (["EN", "ENDANGERED"].includes(c)) return "is-endangered";
  if (["VU", "VULNERABLE"].includes(c)) return "is-vulnerable";
  if (["NT", "NEAR_THREATENED"].includes(c)) return "is-near-threatened";
  return "";
}

function humanIucn(category) {
  const c = String(category || "").trim().toUpperCase();
  const labels = {
    CR: "CR",
    CRITICALLY_ENDANGERED: "CR",
    EN: "EN",
    ENDANGERED: "EN",
    VU: "VU",
    VULNERABLE: "VU",
    NT: "NT",
    NEAR_THREATENED: "NT",
    LC: "LC",
    LEAST_CONCERN: "LC",
    DD: "DD",
    DATA_DEFICIENT: "DD",
  };
  return labels[c] || (c || "");
}

export function createSpeciesPanel(dataConfig = {}) {
  const els = {
    tab: document.getElementById("sidebar-tab-tertiary"),
    shell: document.getElementById("species-data-shell"),
    status: document.getElementById("species-data-status"),
    toolbar: document.getElementById("species-data-toolbar"),
    search: document.getElementById("species-search-input"),
    count: document.getElementById("species-data-count"),
    list: document.getElementById("species-list"),
    more: document.getElementById("species-show-more"),
    source: document.getElementById("species-data-source"),
    retry: document.getElementById("species-retry"),
  };

  const dataService = createSpeciesDataService(dataConfig);
  let currentRegionId = null;
  let currentData = null;
  let currentQuery = "";
  let visibleLimit = 100;
  let selectionVersion = 0;
  let searchTimer = null;

  function hide() {
    if (els.shell) els.shell.hidden = true;
  }

  function show() {
    if (els.shell) els.shell.hidden = false;
  }

  function reset(message = "Open this tab to load recorded species for the selected region.") {
    clearTimeout(searchTimer);
    if (els.retry) els.retry.hidden = true;
    currentData = null;
    currentQuery = "";
    visibleLimit = 100;
    if (els.search) els.search.value = "";
    if (els.status) {
      els.status.textContent = message;
      els.status.hidden = false;
    }
    if (els.toolbar) els.toolbar.hidden = true;
    if (els.list) els.list.innerHTML = "";
    if (els.more) els.more.hidden = true;
    if (els.source) els.source.textContent = "";
  }

  function filteredSpecies() {
    const species = Array.isArray(currentData?.species) ? currentData.species : [];
    const q = currentQuery.trim().toLowerCase();
    if (!q) return species;
    return species.filter((item) => {
      const haystack = [
        item.scientificName,
        item.commonName,
        item.kingdom,
        item.phylum,
        item.class,
        item.order,
        item.family,
        item.genus,
        item.speciesKey,
        item.aphiaId,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }

  function renderList() {
    if (!currentData || !els.list) return;
    const filtered = filteredSpecies();
    const visible = filtered.slice(0, visibleLimit);
    const total = currentData.species.length;
    if (els.count) {
      els.count.textContent = currentQuery
        ? `${formatNumber(filtered.length)} matching / ${formatNumber(total)} recorded species`
        : `${formatNumber(total)} recorded species`;
    }
    els.list.innerHTML = visible
      .map((item) => {
        const iucn = humanIucn(item.iucnRedListCategory);
        const name = item.taxonomyResolved === false ? `Unresolved GBIF species ${item.speciesKey}` : item.scientificName || item.name || "Unresolved species name";
        const taxonomy = [item.family, item.kingdom].filter(Boolean).join(" • ");
        return `
          <article class="species-row">
            <div class="species-row-main">
              <div class="species-name">${escapeHtml(name)}</div>
              ${taxonomy ? `<div class="species-taxonomy">${escapeHtml(taxonomy)}</div>` : ""}
            </div>
            <div class="species-row-stats">
              ${iucn ? `<span class="species-iucn ${iucnClass(item.iucnRedListCategory)}">${escapeHtml(iucn)}</span>` : ""}
              <span class="species-records" title="Occurrence records inside this region">${formatNumber(item.occurrenceCount ?? item.records ?? 0)} rec.</span>
            </div>
          </article>`;
      })
      .join("");
    if (!visible.length) {
      els.list.innerHTML = `<div class="species-empty">${total ? "No species match this filter." : "No species records were found for this region with the selected data filters. This does not mean species are absent."}</div>`;
    }
    if (els.more) {
      els.more.hidden = filtered.length <= visibleLimit;
      if (!els.more.hidden) {
        els.more.textContent = `Show ${formatNumber(Math.min(100, filtered.length - visibleLimit))} more`;
      }
    }
  }

  function renderData(data) {
    currentData = data;
    if (els.status) els.status.hidden = true;
    if (els.toolbar) els.toolbar.hidden = false;
    const source = data.source || "Public biodiversity data";
    const occurrenceCount = data?.stats?.occurrenceCount;
    const generatedAt = data.generatedAt ? new Date(data.generatedAt).toLocaleDateString() : null;
    if (els.source) {
      els.source.textContent = [
        `${source} • recorded occurrence inventory`,
        Number.isFinite(Number(occurrenceCount)) ? `${formatNumber(occurrenceCount)} occurrence records` : null,
        generatedAt ? `generated ${generatedAt}` : null,
        "Counts are records, not abundance.",
      ]
        .filter(Boolean)
        .join(" • ");
    }
    renderList();
  }

  async function ensureLoaded() {
    if (!currentRegionId || currentRegionId === "moon" || !els.tab?.classList.contains("is-active")) return;
    if (currentData) return;
    show();
    const version = selectionVersion;
    reset("Loading recorded species…");
    try {
      const data = await dataService.loadRegion(currentRegionId);
      if (version !== selectionVersion) return;
      if (!data) {
        reset("A recorded-species inventory is not available for this region yet.");
        return;
      }
      renderData(data);
    } catch (err) {
      if (version !== selectionVersion) return;
      console.warn("Species inventory load failed:", err);
      reset("The species inventory could not be loaded. Please try again.");
      if (els.retry) els.retry.hidden = false;
    }
  }

  function setRegion(regionId) {
    selectionVersion += 1;
    currentRegionId = regionId || null;
    reset();
    if (!currentRegionId || currentRegionId === "moon") { hide(); return; }
    show();
    if (els.tab?.classList.contains("is-active")) ensureLoaded();
  }

  els.retry?.addEventListener("click", ensureLoaded);
  els.tab?.addEventListener("click", () => {
    window.requestAnimationFrame(() => ensureLoaded());
  });
  els.search?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      currentQuery = els.search.value || "";
      visibleLimit = 100;
      renderList();
    }, 100);
  });
  els.more?.addEventListener("click", () => {
    visibleLimit += 100;
    renderList();
  });

  hide();
  return { setRegion, ensureLoaded };
}
