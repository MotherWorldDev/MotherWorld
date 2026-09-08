import { createSelenologyDataService } from "./selenologyDataService.js?v=20260907-env8-selenology";
import { MOON_IMPACT_STATS } from "./moonImpactStats.js?v=20260907-env8-selenology";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}
function details(title, body, open = false) {
  return `<details class="selenology-section" ${open ? "open" : ""}><summary>${esc(title)}</summary><div class="selenology-section-body">${body}</div></details>`;
}
function statusBody(section, label) {
  if (section?.status === "ready" && section?.summary) return `<p>${esc(section.summary)}</p>`;
  return `<p class="selenology-note">${esc(section?.note || `${label} data contract is ready; the provider build has not been run yet.`)}</p>`;
}
function legendHtml(data) {
  const items = data?.legend || [];
  if (!items.length) return `<p class="selenology-note">The geology texture works without a legend; optionally pass the official QGIS SLD to the builder to generate one.</p>`;
  return `<div class="selenology-legend">${items.slice(0, 80).map((x) => `<div><i style="--selenology-swatch:${esc(x.color || "#777")}"></i><span><b>${esc(x.label || x.code || "Mapped unit")}</b>${x.code ? `<small>${esc(x.code)}</small>` : ""}${x.age ? `<small>${esc(x.age)}</small>` : ""}</span></div>`).join("")}</div>`;
}
function textureHtml(data) {
  const tex = data?.texture || {};
  const ready = tex.available === true;
  return `<div class="selenology-map-status ${ready ? "is-ready" : "is-missing"}"><div><b>${ready ? "Geologic Moon enabled" : "Geologic texture not built yet"}</b><span>${ready ? "The Moon sphere uses the USGS Unified Geologic Map while this tab is active." : "Run the bundled texture builder; the natural LROC surface remains active until then."}</span></div><strong>${ready ? "LIVE" : "NATURAL"}</strong></div>${legendHtml(data)}<p class="selenology-source">Source: USGS Astrogeology · Unified Geologic Map of the Moon, 1:5M (Fortezzo, Spudis & Harrel, 2020) · CC0</p>`;
}
function impactsHtml() {
  return `<div class="selenology-kv"><div><span>Catalogued craters ≥1 km</span><strong>${MOON_IMPACT_STATS.cratersGe1Km.toLocaleString()}</strong></div><div><span>Catalogued craters ≥20 km</span><strong>${MOON_IMPACT_STATS.cratersGe20Km.toLocaleString()}</strong></div><div><span>Approx. catalogue completeness</span><strong>~1–2 km+</strong></div></div><p class="selenology-note">The Moon has vastly more sub-kilometre craters. These are catalogue-qualified counts, not the literal total number of lunar craters.</p>`;
}
function render(data) {
  const s = data?.sections || {};
  return [
    details("Geologic surface map", textureHtml(data), true),
    details("Surface geology", statusBody(s.surfaceGeology, "Surface geology"), true),
    details("Geologic age", statusBody(s.geologicAge, "Geologic age")),
    details("Topography & relief", statusBody(s.topography, "Topography")),
    details("Crust & gravity", statusBody(s.crustGravity, "Crust and gravity")),
    details("Mineralogy & elements", statusBody(s.mineralogy, "Mineralogy")),
    details("Volcanism", statusBody(s.volcanism, "Volcanism")),
    details("Tectonics & moonquakes", statusBody(s.tectonics, "Tectonics")),
    details("Polar volatiles", statusBody(s.polarVolatiles, "Polar volatiles")),
    details("Impact history", impactsHtml()),
    details("Returned samples", statusBody(s.returnedSamples, "Returned samples")),
  ].join("");
}

export function createSelenologyPanel(config = {}) {
  const els = {
    tab: document.getElementById("sidebar-tab-selenology"),
    panel: document.getElementById("sidebar-selenology-panel"),
    status: document.getElementById("selenology-status"),
    content: document.getElementById("selenology-content"),
    retry: document.getElementById("selenology-retry"),
  };
  const service = createSelenologyDataService(config);
  let region = null;
  let data = null;
  let serial = 0;
  const active = () => els.tab?.classList.contains("is-active") && els.panel && !els.panel.hidden;
  function emitSurface(detail) { document.dispatchEvent(new CustomEvent("motherworld:moon-surface", { detail })); }
  function natural() { emitSurface({ mode: "natural" }); }
  function applySurface() {
    if (!active() || region?.id !== "moon") return natural();
    const tex = data?.texture || {};
    if (tex.available === true) {
      emitSurface({ mode: "geology", base: tex.url4k || config.moonGeologyTextureUrl, hiRes: tex.url8k || config.moonGeologyTextureHiResUrl });
    } else {
      natural();
    }
  }
  function reset(message = "Open Selenology to load lunar geology.") {
    data = null;
    if (els.content) { els.content.hidden = true; els.content.innerHTML = ""; }
    if (els.retry) els.retry.hidden = true;
    if (els.status) { els.status.hidden = false; els.status.textContent = message; }
    natural();
  }
  function draw() {
    if (!data || !active() || !els.content) return;
    els.content.innerHTML = render(data);
    els.content.hidden = false;
    if (els.status) els.status.hidden = true;
    applySurface();
  }
  async function load() {
    if (region?.id !== "moon" || !active()) return;
    if (data) { draw(); return; }
    const n = ++serial;
    if (els.status) { els.status.hidden = false; els.status.textContent = "Loading Selenology…"; }
    try {
      const d = await service.load();
      if (n !== serial) return;
      data = d || { schemaVersion: 1, texture: { available: false }, sections: {} };
      draw();
    } catch (err) {
      if (n !== serial) return;
      reset(`Selenology unavailable: ${err.message}`);
      if (els.retry) els.retry.hidden = false;
    }
  }
  function setRegion(next) {
    serial += 1;
    region = next || null;
    if (region?.id !== "moon") reset();
    else {
      data = null;
      if (els.content) { els.content.hidden = true; els.content.innerHTML = ""; }
      if (els.status) { els.status.hidden = false; els.status.textContent = "Open Selenology to load lunar geology."; }
      if (active()) load();
    }
  }
  els.tab?.addEventListener("click", () => setTimeout(load, 0));
  for (const tab of document.querySelectorAll(".sidebar-tab")) if (tab !== els.tab) tab.addEventListener("click", natural);
  els.retry?.addEventListener("click", load);
  return { setRegion, load };
}
