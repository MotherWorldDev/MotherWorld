import { createClimateExtrasDataService } from "./climateExtrasDataService.js?v=20260908-env8-selenology";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

function esc(value) {
  return String(value ?? "").replace(/[&<>\"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
}
function finite(value) { const n = Number(value); return Number.isFinite(n) ? n : null; }
function fmt(value, digits = 1, suffix = "") { const n = finite(value); return n == null ? "—" : `${n.toFixed(digits)}${suffix}`; }
function pct(value, digits = 1) { return fmt(value, digits, "%"); }
function sourceText(data) {
  const source = data?.source || {};
  const baseline = data?.baseline || {};
  const range = Number.isFinite(baseline.startYear) && Number.isFinite(baseline.endYear) ? `${baseline.startYear}–${baseline.endYear}` : "baseline unavailable";
  return [source.label || source.id, source.spatialResolution, range, data?.aggregation?.distributionWeighting].filter(Boolean).join(" • ");
}
function statCards(rows) {
  return `<div class="climate-extra-stats">${rows.map(([label, value]) => `<div class="climate-extra-stat"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`;
}
function distributionRows(edges, values, formatter) {
  const vals = Array.isArray(values) ? values : [];
  const max = Math.max(...vals.map((v) => Number(v) || 0), 0.001);
  return `<div class="climate-extra-dist">${vals.map((v, i) => {
    const lo = edges?.[i]; const hi = edges?.[i + 1];
    const label = formatter(lo, hi, i);
    const width = Math.max(0, Math.min(100, (Number(v) || 0) / max * 100));
    return `<div class="climate-extra-dist-row"><span class="climate-extra-dist-label">${esc(label)}</span><span class="climate-extra-dist-track"><i style="width:${width.toFixed(2)}%"></i></span><strong>${pct(v)}</strong></div>`;
  }).join("")}</div>`;
}
function monthlyBars(values, formatter) {
  const vals = MONTHS.map((_, i) => finite(values?.[i]) ?? 0);
  const max = Math.max(...vals, 0.001);
  return `<div class="climate-extra-months" role="img" aria-label="Monthly climatology">${vals.map((v, i) => `<div class="climate-extra-month" title="${MONTHS[i]}: ${esc(formatter(v))}"><span class="climate-extra-month-bar"><i style="height:${Math.max(2, v/max*100).toFixed(2)}%"></i></span><small>${MONTHS[i]}</small></div>`).join("")}</div>`;
}
function windRoseSvg(sectors, values) {
  const vals = COMPASS.map((_, i) => Math.max(0, Number(values?.[i]) || 0));
  const max = Math.max(...vals, 0.001);
  const cx = 120, cy = 120, r0 = 18, rMax = 82;
  const bars = vals.map((v, i) => {
    const angle = (i * 22.5 - 90) * Math.PI / 180;
    const length = r0 + (v / max) * (rMax - r0);
    const x1 = cx + Math.cos(angle) * r0, y1 = cy + Math.sin(angle) * r0;
    const x2 = cx + Math.cos(angle) * length, y2 = cy + Math.sin(angle) * length;
    return `<line x1="${x1.toFixed(2)}" y1="${y1.toFixed(2)}" x2="${x2.toFixed(2)}" y2="${y2.toFixed(2)}" class="climate-wind-spoke" style="--wind-p:${(v/max).toFixed(3)}"><title>${esc(sectors?.[i] || COMPASS[i])}: ${pct(v)}</title></line>`;
  }).join("");
  const rings = [0.25, 0.5, 0.75, 1].map((f) => `<circle cx="120" cy="120" r="${(r0 + (rMax-r0)*f).toFixed(1)}" class="climate-wind-ring"/>`).join("");
  return `<svg class="climate-wind-rose" viewBox="0 0 240 240" role="img" aria-label="Wind direction frequency rose">${rings}${bars}<circle cx="120" cy="120" r="4" class="climate-wind-center"/><text x="120" y="20">N</text><text x="220" y="124">E</text><text x="120" y="230">S</text><text x="20" y="124">W</text></svg>`;
}

function ensureMarkup() {
  const climate = document.getElementById("sidebar-climate-panel");
  if (!climate) return null;
  let root = document.getElementById("climate-extras-shell");
  if (root) return root;
  root = document.createElement("section");
  root.id = "climate-extras-shell";
  root.className = "climate-extras-shell";
  root.hidden = true;
  root.innerHTML = `
    <div class="climate-extras-head">
      <div><p class="climate-extras-kicker">Climate normals</p><h3>Precipitation, humidity &amp; wind</h3></div>
      <span class="climate-extras-baseline">1991–2020</span>
    </div>
    <div class="climate-extras-tabs" role="tablist" aria-label="Additional climate variables">
      <button type="button" role="tab" data-climate-extra="precipitation" class="is-active" aria-selected="true">Precipitation</button>
      <button type="button" role="tab" data-climate-extra="humidity" aria-selected="false">Humidity</button>
      <button type="button" role="tab" data-climate-extra="wind" aria-selected="false">Wind</button>
    </div>
    <p id="climate-extras-status" class="climate-extras-status"></p>
    <div id="climate-extras-content" class="climate-extras-content" hidden></div>
    <button id="climate-extras-retry" class="ghost-btn climate-extras-retry" type="button" hidden>Retry climate data</button>`;
  climate.appendChild(root);
  return root;
}

export function createClimateExtrasPanel(dataConfig = {}) {
  const root = ensureMarkup();
  const tab = document.getElementById("sidebar-tab-climate");
  const status = document.getElementById("climate-extras-status");
  const content = document.getElementById("climate-extras-content");
  const retry = document.getElementById("climate-extras-retry");
  const service = createClimateExtrasDataService(dataConfig);
  let currentRegion = null;
  let currentData = null;
  let currentVariable = "precipitation";
  let version = 0;

  const active = () => tab?.classList.contains("is-active") && root && !root.hidden;
  function setStatus(message = "", visible = true) { if (status) { status.textContent = message; status.hidden = !visible; } }
  function setVariable(next) {
    currentVariable = next;
    root?.querySelectorAll("[data-climate-extra]").forEach((button) => {
      const yes = button.dataset.climateExtra === next;
      button.classList.toggle("is-active", yes);
      button.setAttribute("aria-selected", String(yes));
    });
    render();
  }

  function precipitationHtml(data) {
    const p = data.precipitation || {}, s = p.stats || {};
    const edges = p.distribution?.edgesMm || [];
    return `
      ${statCards([
        ["Annual precipitation", fmt(s.annualMeanMm, 0, " mm")],
        ["Wet area-days / yr", fmt(s.wetAreaDaysPerYear, 1)],
        ["Heavy-rain area-days / yr", fmt(s.heavyRainAreaDaysPerYear, 1)],
        ["Daily P95 (approx.)", fmt(s.p95DailyMmApprox, 1, " mm")],
      ])}
      <h4>Monthly precipitation</h4>${monthlyBars(p.monthlyClimatologyMm, (v) => `${v.toFixed(0)} mm`)}
      <h4>Daily precipitation distribution</h4>${distributionRows(edges, p.distribution?.percent, (lo, hi) => {
        if (hi == null) return `≥${lo} mm`;
        if (lo === 0 && hi === 0.1) return "Dry / trace (<0.1 mm)";
        return `${lo}–${hi} mm`;
      })}
      <p class="climate-extra-definition">Percentages are physical-area × day weighted across the region. Wet days use ≥1 mm/day; heavy-rain days use ≥10 mm/day.</p>`;
  }
  function humidityHtml(data) {
    const h = data.humidity || {}, s = h.stats || {};
    return `
      ${statCards([
        ["Mean RH", pct(s.meanPct)],
        ["Median RH (approx.)", pct(s.medianPctApprox)],
        ["10th percentile", pct(s.p10PctApprox)],
        ["90th percentile", pct(s.p90PctApprox)],
      ])}
      <h4>Monthly relative humidity</h4>${monthlyBars(h.monthlyClimatologyPct, (v) => `${v.toFixed(0)}%`)}
      <h4>Relative-humidity distribution</h4>${distributionRows(h.distribution?.edgesPct || [], h.distribution?.percent, (lo, hi) => hi == null ? `≥${lo}%` : `${lo}–${hi}%`)}
      <p class="climate-extra-definition">Relative humidity is derived from daily-mean 2 m air temperature and dew point. It is a daily-mean-state estimate rather than the exact mean of hourly RH.</p>`;
  }
  function windHtml(data) {
    const w = data.wind || {}, s = w.stats || {};
    return `
      ${statCards([
        ["Mean wind", fmt(s.meanMs, 1, " m/s")],
        ["Median (approx.)", fmt(s.medianMsApprox, 1, " m/s")],
        ["P90 (approx.)", fmt(s.p90MsApprox, 1, " m/s")],
        ["Prevailing direction", s.prevailingDirection || "—"],
        ["Calm conditions", pct(s.calmPct)],
        ["Strong-wind area-days / yr", fmt(s.strongWindAreaDaysPerYear, 1)],
      ])}
      <div class="climate-wind-grid"><div><h4>Wind direction</h4>${windRoseSvg(w.rose?.sectors, w.rose?.percent)}</div><div><h4>Monthly mean speed</h4>${monthlyBars(w.monthlyClimatologyMs, (v) => `${v.toFixed(1)} m/s`)}</div></div>
      <h4>Wind-speed distribution</h4>${distributionRows(w.distribution?.edgesMs || [], w.distribution?.percent, (lo, hi) => hi == null ? `≥${lo} m/s` : `${lo}–${hi} m/s`)}
      <p class="climate-extra-definition">Wind uses daily-mean 10 m u/v components. The rose shows where the wind blows from; gusts are not included.</p>`;
  }

  function render() {
    if (!content || !currentData || !active()) return;
    const body = currentVariable === "humidity" ? humidityHtml(currentData) : currentVariable === "wind" ? windHtml(currentData) : precipitationHtml(currentData);
    content.innerHTML = `${body}<p class="climate-extra-source">${esc(sourceText(currentData))}</p>`;
    content.hidden = false;
    setStatus("", false);
  }

  async function ensureLoaded() {
    const rid = currentRegion?.id;
    if (!rid || rid === "moon" || !active()) return;
    if (currentData) { render(); return; }
    const localVersion = version;
    setStatus("Loading precipitation, humidity and wind normals…");
    if (retry) retry.hidden = true;
    try {
      const data = await service.loadRegion(rid);
      if (localVersion !== version) return;
      if (!data) {
        setStatus("Precipitation, humidity and wind normals are not built for this region yet.");
        return;
      }
      currentData = data;
      render();
    } catch (error) {
      if (localVersion !== version) return;
      console.warn("Climate extras load failed", error);
      setStatus("Additional climate charts could not be loaded.");
      if (retry) retry.hidden = false;
    }
  }

  function setRegion(region) {
    version += 1;
    currentRegion = region || null;
    currentData = null;
    if (content) { content.innerHTML = ""; content.hidden = true; }
    if (retry) retry.hidden = true;
    if (root) root.hidden = !currentRegion || currentRegion.id === "moon";
    if (currentRegion && currentRegion.id !== "moon") setStatus("Open Climate to load precipitation, humidity and wind normals.");
    else setStatus("", false);
    if (active()) ensureLoaded();
  }

  root?.querySelectorAll("[data-climate-extra]").forEach((button) => button.addEventListener("click", () => setVariable(button.dataset.climateExtra)));
  retry?.addEventListener("click", ensureLoaded);
  tab?.addEventListener("click", () => { window.setTimeout(ensureLoaded, 0); });
  return { setRegion, ensureLoaded };
}
