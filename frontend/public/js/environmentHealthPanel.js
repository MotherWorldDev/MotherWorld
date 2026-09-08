function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function num(value, digits = 1) {
  const number = finite(value);
  return number === null ? "—" : number.toFixed(digits);
}

function pct(value) {
  const number = finite(value);
  return number === null ? "—" : `${(number * 100).toFixed(0)}%`;
}

function raw(component) {
  if (component.raw === null || component.raw === undefined) return "—";
  const number = finite(component.raw);
  const value = number === null ? String(component.raw) : number.toLocaleString(undefined, { maximumFractionDigits: 3 });
  return `${value}${component.unit ? ` ${component.unit}` : ""}`;
}

function card(family) {
  const score = finite(family.score);
  const componentRows = (family.components || []).map((component) => {
    const componentScore = finite(component.score);
    return `<div class="env-component"><div><b>${esc(component.label || component.id)}</b><small>${esc(raw(component))}${component.source ? ` · ${esc(component.source)}` : ""}</small></div><div class="env-component-score">${componentScore === null ? "context" : `${num(componentScore, 0)}/100`}</div></div>`;
  }).join("");
  const context = family.context && Object.keys(family.context).length
    ? `<details class="env-context"><summary>Context / raw provider details</summary><pre>${esc(JSON.stringify(family.context, null, 2))}</pre></details>`
    : "";
  return `<details class="env-family-card" data-family-id="${esc(family.id)}"><summary><span class="env-family-name">${esc(family.label || family.id)}</span><span class="env-family-coverage">coverage ${esc(pct(family.coverage))}</span><strong>${score === null ? "—" : Math.round(score)}</strong><span class="env-family-track"><i style="width:${score === null ? 0 : Math.max(0, Math.min(100, score))}%"></i></span></summary><div class="env-family-body">${componentRows}${context}</div></details>`;
}

export function createEnvironmentHealthPanel(config = {}, fetcher = (...args) => fetch(...args)) {
  const els = {
    tab: document.getElementById("sidebar-tab-health"),
    panel: document.getElementById("sidebar-health-panel"),
    status: document.getElementById("regional-health-status"),
    title: document.getElementById("regional-health-title"),
    cards: document.getElementById("regional-health-cards"),
    note: document.getElementById("regional-health-note"),
  };
  let region = null;
  let serial = 0;
  const cacheLimit = Math.max(1, Number(config.regionalHealthCacheLimit || 6));
  const cache = new Map();
  const base = String(config.regionalHealthBaseUrl || "./data/indices/regions/").replace(/\/?$/, "/");

  function reset(message = "Select a region to inspect environmental condition indices.") {
    if (els.status) {
      els.status.hidden = false;
      els.status.textContent = message;
    }
    if (els.cards) els.cards.innerHTML = "";
    if (els.title) els.title.textContent = "Regional health";
    if (els.note) els.note.textContent = "100 = best condition / lowest pressure. Coverage is shown separately; missing data is never treated as healthy.";
  }

  function cached(id) {
    if (!cache.has(id)) return null;
    const data = cache.get(id);
    cache.delete(id);
    cache.set(id, data);
    return data;
  }

  function remember(id, data) {
    cache.delete(id);
    cache.set(id, data);
    while (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
  }

  async function load() {
    const selected = region;
    const requestSerial = ++serial;
    if (!selected || selected.id === "moon") {
      reset(selected?.id === "moon" ? "Environmental Earth indices do not apply to the Moon." : undefined);
      return;
    }
    reset("Loading regional environmental indices…");
    try {
      let data = cached(selected.id);
      if (!data) {
        const response = await fetcher(`${base}${encodeURIComponent(selected.id)}.json`);
        if (requestSerial !== serial) return;
        if (response.status === 404) {
          reset("No generated regional indices are available for this region yet.");
          return;
        }
        if (!response.ok) throw new Error(`regional index request failed (${response.status})`);
        data = await response.json();
        if (requestSerial !== serial) return;
        remember(selected.id, data);
      }
      if (requestSerial !== serial) return;
      if (els.status) els.status.hidden = true;
      if (els.title) els.title.textContent = data.regionName || selected.name || selected.id;
      if (els.cards) els.cards.innerHTML = (data.families || []).map(card).join("");
    } catch (error) {
      if (requestSerial === serial) reset(`Regional indices unavailable: ${error.message}`);
    }
  }

  function setRegion(next) {
    region = next || null;
    serial += 1;
    reset();
    if (els.panel?.hidden === false) load();
  }

  function openFamily(familyId) {
    if (!region) return;
    els.tab?.click();
    window.requestAnimationFrame(async () => {
      await load();
      const element = els.cards?.querySelector(`[data-family-id="${CSS.escape(familyId)}"]`);
      if (element) {
        element.open = true;
        element.scrollIntoView({ block: "nearest" });
      }
    });
  }

  els.tab?.addEventListener("click", () => window.requestAnimationFrame(load));
  window.addEventListener("motherworld:open-family", (event) => openFamily(event.detail?.familyId));
  reset();
  return { setRegion, reload: load, openFamily };
}
