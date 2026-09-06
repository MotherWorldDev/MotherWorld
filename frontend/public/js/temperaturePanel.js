import { createTemperatureDataService } from "./temperatureDataService.js?v=20260907-temperature1";

/* MotherWorld empirical temperature distribution panel */

function formatC(value, digits = 1) {
  const n = value == null || value === "" ? NaN : Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)} °C` : "—";
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function gaussianSmooth(values, sigma = 1.25) {
  const input = Array.from(values || [], Number);
  if (!input.length || sigma <= 0) return input;
  const radius = Math.max(1, Math.ceil(sigma * 3));
  const kernel = [];
  let kernelSum = 0;
  for (let k = -radius; k <= radius; k++) {
    const w = Math.exp(-(k * k) / (2 * sigma * sigma));
    kernel.push(w);
    kernelSum += w;
  }
  return input.map((_, i) => {
    let sum = 0;
    let norm = 0;
    for (let k = -radius; k <= radius; k++) {
      const j = i + k;
      if (j < 0 || j >= input.length) continue;
      const w = kernel[k + radius] / kernelSum;
      sum += input[j] * w;
      norm += w;
    }
    return norm > 0 ? sum / norm : 0;
  });
}

function sourceLabel(data) {
  return data?.source?.label || data?.source?.id || "Climate dataset";
}

function distributionSentence(data, mode) {
  const dist = data?.distributions?.[mode];
  const s = dist?.stats || {};
  if (s.medianC == null || !Number.isFinite(Number(s.medianC))) return "Temperature distribution unavailable.";
  const low = Number(s.p05C);
  const high = Number(s.p95C);
  const median = Number(s.medianC);
  if (mode === "spaceTime") {
    return `Across space and time, the middle 90% of weighted temperatures falls roughly between ${formatC(low)} and ${formatC(high)}, with a median near ${formatC(median)}.`;
  }
  return `Across the baseline period, 90% of daily region-wide mean temperatures falls roughly between ${formatC(low)} and ${formatC(high)}, with a median near ${formatC(median)}.`;
}

export function createTemperaturePanel(dataConfig = {}) {
  const els = {
    tab: document.getElementById("sidebar-tab-climate"),
    shell: document.getElementById("temperature-data-shell"),
    status: document.getElementById("temperature-data-status"),
    canvas: document.getElementById("temperature-distribution-canvas"),
    tooltip: document.getElementById("temperature-tooltip"),
    source: document.getElementById("temperature-data-source"),
    summary: document.getElementById("temperature-summary"),
    content: document.getElementById("temperature-content"),
    heading: document.getElementById("temperature-heading"),
    coverage: document.getElementById("temperature-coverage-note"),
    retry: document.getElementById("temperature-retry"),
    axis: document.getElementById("temperature-axis-label"),
    temporalBtn: document.getElementById("temperature-mode-temporal"),
    spacetimeBtn: document.getElementById("temperature-mode-spacetime"),
    stats: document.getElementById("temperature-stats-grid"),
    definition: document.getElementById("temperature-definition"),
  };

  const service = createTemperatureDataService(dataConfig);
  let currentRegion = null, currentData = null;
  let mode = "regionalDailyMean";
  let selectionVersion = 0;
  let hoverBin = 0;
  let resizeObserver = null;
  const isActive = () => els.tab?.classList.contains("is-active") && !els.shell?.hidden;

  function setStatus(message, visible = true) {
    if (!els.status) return;
    els.status.textContent = message || "";
    els.status.hidden = !visible;
  }

  function updateButtons() {
    const temporal = mode === "regionalDailyMean";
    els.temporalBtn?.classList.toggle("is-active", temporal);
    els.spacetimeBtn?.classList.toggle("is-active", !temporal);
    els.temporalBtn?.setAttribute("aria-pressed", String(temporal));
    els.spacetimeBtn?.setAttribute("aria-pressed", String(!temporal));
  }

  function renderStats() {
    if (!els.stats) return;
    const dist = currentData?.distributions?.[mode];
    const stats = dist?.stats || {};
    const rows = [
      ["Mean", stats.meanC],
      ["Median", stats.medianC],
      ["5th pct.", stats.p05C],
      ["95th pct.", stats.p95C],
      ["Std. dev.", stats.stdC],
    ];
    els.stats.innerHTML = rows
      .map(
        ([label, value]) => `
          <div class="temperature-stat-card">
            <div class="temperature-stat-label">${label}</div>
            <div class="temperature-stat-value">${formatC(value)}</div>
          </div>`
      )
      .join("");
    if (els.definition) els.definition.textContent = dist?.definition || "";
    if (els.summary) els.summary.textContent = distributionSentence(currentData, mode);
  }

  function canvasMetrics(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(180, Math.round(rect.width || 560));
    const height = Math.max(220, Math.round(rect.height || 280));
    const pxWidth = Math.round(width * dpr);
    const pxHeight = Math.round(height * dpr);
    if (canvas.width !== pxWidth || canvas.height !== pxHeight) {
      canvas.width = pxWidth;
      canvas.height = pxHeight;
    }
    const ctx = canvas.getContext("2d");
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, width, height };
  }

  function renderChart() {
    if (!els.canvas || !currentData || !isActive()) return;
    const dist = currentData.distributions?.[mode];
    const edges = currentData.bins?.edgesC || [];
    const pct = dist?.percent || [];
    if (!pct.length || edges.length !== pct.length + 1) {
      setStatus("Temperature histogram is malformed or empty.");
      return;
    }
    setStatus("", false);
    const { ctx, width, height } = canvasMetrics(els.canvas);
    if (!ctx) return;
    const pad = { left: 48, right: 14, top: 18, bottom: 38 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    // Both modes share an axis covering their observed ranges, with a little padding.
    const populated = pct.map((_, i) => currentData.distributions.regionalDailyMean.percent[i] > 0 || currentData.distributions.spaceTime.percent[i] > 0);
    const first = Math.max(0, populated.indexOf(true) - 2);
    const last = Math.min(pct.length, populated.lastIndexOf(true) + 3);
    const minX = Number(edges[first]);
    const maxX = Number(edges[last]);
    const rawMax = Math.max(...pct, 0.001);
    const maxY = rawMax * 1.15;
    const centers = pct.map((_, i) => (Number(edges[i]) + Number(edges[i + 1])) / 2);
    const smooth = gaussianSmooth(pct, 1.4);

    ctx.clearRect(0, 0, width, height);
    const textColor = getComputedStyle(document.documentElement).getPropertyValue("--text")?.trim() || "#dbe7f3";
    const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted")?.trim() || "#8b9bab";
    const border = "rgba(160, 180, 200, 0.22)";
    const fill = "rgba(120, 185, 230, 0.34)";
    const line = "rgba(205, 236, 255, 0.92)";
    const meanLine = "rgba(255, 210, 120, 0.9)";
    const medianLine = "rgba(190, 255, 180, 0.9)";

    const xToPx = (x) => pad.left + ((x - minX) / (maxX - minX)) * plotW;
    const yToPx = (y) => pad.top + plotH - (y / maxY) * plotH;

    ctx.strokeStyle = border;
    ctx.lineWidth = 1;
    ctx.font = "11px IBM Plex Mono, monospace";
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const yVal = (maxY * i) / 4;
      const y = yToPx(yVal);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
      ctx.fillText(`${yVal.toFixed(yVal < 2 ? 1 : 0)}%`, pad.left - 7, y);
    }

    const barW = plotW / (last - first);
    ctx.fillStyle = fill;
    for (let i = first; i < last; i++) {
      if (pct[i] <= 0) continue;
      const x = pad.left + (i - first) * barW;
      const y = yToPx(pct[i]);
      ctx.fillRect(x, y, barW, pad.top + plotH - y);
    }

    ctx.strokeStyle = line;
    ctx.lineWidth = 2;
    ctx.beginPath();
    smooth.slice(first, last).forEach((v, offset) => {
      const i = first + offset;
      const x = xToPx(centers[i]);
      const y = yToPx(v);
      if (offset === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    const stats = dist.stats || {};
    for (const [value, stroke] of [
      [stats.meanC == null ? NaN : Number(stats.meanC), meanLine],
      [stats.medianC == null ? NaN : Number(stats.medianC), medianLine],
    ]) {
      if (!Number.isFinite(value)) continue;
      const x = clamp(xToPx(value), pad.left, width - pad.right);
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, pad.top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = stroke;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";

    }

    ctx.strokeStyle = border;
    ctx.beginPath();
    ctx.moveTo(pad.left, pad.top + plotH);
    ctx.lineTo(width - pad.right, pad.top + plotH);
    ctx.stroke();

    const desiredTicks = width < 350 ? 4 : 6;
    const span = maxX - minX;
    const rough = span / desiredTicks;
    const step = rough <= 2 ? 2 : rough <= 5 ? 5 : rough <= 10 ? 10 : 20;
    const start = Math.ceil(minX / step) * step;
    ctx.fillStyle = textColor;
    ctx.font = "11px IBM Plex Mono, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let xVal = start; xVal <= maxX; xVal += step) {
      const x = xToPx(xVal);
      ctx.fillText(`${xVal}°`, x, pad.top + plotH + 9);
    }

    els.canvas.dataset.firstBin = String(first);
    els.canvas.dataset.lastBin = String(last);
    els.canvas.dataset.chartMinX = String(minX);
    els.canvas.dataset.chartMaxX = String(maxX);
    els.canvas.dataset.plotLeft = String(pad.left);
    els.canvas.dataset.plotWidth = String(plotW);
  }

  function renderData() {
    if (els.content) els.content.hidden = false;
    if (els.retry) els.retry.hidden = true;
    updateButtons();
    renderStats();
    if (els.axis) els.axis.textContent = mode === "spaceTime" ? "Area × days (%)" : "Days (%)";
    const baseline = currentData.baseline;
    const preview = baseline.endYear - baseline.startYear + 1 < 30;
    if (els.coverage) {
      els.coverage.hidden = !preview;
      els.coverage.textContent = "Preview with available years. The planned climate baseline is 1991–2020; the dates below show this chart's actual coverage.";
    }
    const variable = currentData.variable === "sea_surface_temperature" ? "Sea-surface temperature" : "2 m air temperature";
    if (els.heading) els.heading.textContent = `${variable} · ${currentRegion?.name || currentData.regionName || "Selected region"}`;
    if (els.canvas) els.canvas.setAttribute("aria-label", `${variable}. ${distributionSentence(currentData, mode)} Use left and right arrow keys to inspect temperature bins.`);
    renderChart();
    if (els.source) {
      const range = baseline.startYear === baseline.endYear ? String(baseline.startYear) : `${baseline.startYear}–${baseline.endYear}`;
      const days = currentData.distributions.regionalDailyMean.sampleCountDays;
      els.source.textContent = [sourceLabel(currentData), currentData.source?.spatialResolution,
        `${range} ${preview ? "preview" : "daily baseline"}`, Number.isFinite(days) ? `${days.toLocaleString()} valid days` : null,
        currentData.quality?.boundaryWeighting].filter(Boolean).join(" • ");
    }
  }

  function reset() {
    currentData = null;
    if (els.content) els.content.hidden = true;
    if (els.retry) els.retry.hidden = true;
    if (els.tooltip) els.tooltip.hidden = true;
    if (els.summary) els.summary.textContent = "";
    if (els.source) els.source.textContent = "";
    if (els.stats) els.stats.innerHTML = "";
    if (els.definition) els.definition.textContent = "";
    const ctx = els.canvas?.getContext("2d");
    ctx?.clearRect(0, 0, els.canvas.width, els.canvas.height);
  }

  async function ensureLoaded() {
    const rid = currentRegion?.id;
    if (!rid || rid === "moon" || !isActive()) return;
    if (currentData) { renderData(); return; }
    const version = selectionVersion;
    setStatus("Loading temperature distributions…");
    if (els.retry) els.retry.hidden = true;
    try {
      const data = await service.loadRegion(rid);
      if (version !== selectionVersion) return;
      if (!data) {
        setStatus("Temperature distributions are not available for this region yet.");
        return;
      }
      currentData = data;
      if (isActive()) renderData();
    } catch (err) {
      if (version !== selectionVersion) return;
      console.warn("Temperature distribution load failed:", err);
      setStatus("The temperature chart could not be loaded. Please try again.");
      if (els.retry) els.retry.hidden = false;
    }
  }

  function setRegion(region) {
    selectionVersion += 1;
    currentRegion = region || null;
    reset();
    mode = "regionalDailyMean";
    updateButtons();
    if (els.shell) els.shell.hidden = !currentRegion || currentRegion.id === "moon";
    if (els.heading) els.heading.textContent = `Temperature · ${currentRegion?.name || "Selected region"}`;
    setStatus("Open Climate to load this region's temperature distributions.");
    if (isActive()) ensureLoaded();
  }

  els.retry?.addEventListener("click", ensureLoaded);
  function setMode(next) {
    if (!currentData?.distributions?.[next]) return;
    mode = next;
    if (els.tooltip) els.tooltip.hidden = true;
    renderData();
  }

  els.temporalBtn?.addEventListener("click", () => setMode("regionalDailyMean"));
  els.spacetimeBtn?.addEventListener("click", () => setMode("spaceTime"));
  els.tab?.addEventListener("click", () => window.requestAnimationFrame(ensureLoaded));

  els.canvas?.addEventListener("pointermove", (event) => {
    if (!currentData || !els.tooltip) return;
    const rect = els.canvas.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const left = Number(els.canvas.dataset.plotLeft || 0);
    const plotW = Number(els.canvas.dataset.plotWidth || 1);
    const ratio = (px - left) / plotW;
    if (ratio < 0 || ratio > 1) {
      els.tooltip.hidden = true;
      return;
    }
    const pct = currentData.distributions?.[mode]?.percent || [];
    const edges = currentData.bins?.edgesC || [];
    const first = Number(els.canvas.dataset.firstBin || 0);
    const last = Number(els.canvas.dataset.lastBin || pct.length);
    const i = clamp(first + Math.floor(ratio * (last - first)), first, last - 1);
    hoverBin = i;
    els.tooltip.textContent = `${Number(edges[i]).toFixed(1)} to ${Number(edges[i + 1]).toFixed(1)} °C: ${Number(pct[i]).toFixed(2)}%`;
    els.tooltip.style.left = `${clamp(px + 10, 8, Math.max(8, rect.width - 210))}px`;
    els.tooltip.style.top = `${Math.max(8, event.clientY - rect.top - 34)}px`;
    els.tooltip.hidden = false;
  });
  els.canvas?.addEventListener("pointerleave", () => {
    if (els.tooltip) els.tooltip.hidden = true;
  });

  els.canvas?.addEventListener("keydown", event => {
    if (!currentData || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const pct = currentData.distributions[mode].percent, edges = currentData.bins.edgesC;
    const first = Number(els.canvas.dataset.firstBin || 0), last = Number(els.canvas.dataset.lastBin || pct.length);
    hoverBin = clamp(hoverBin + (event.key === "ArrowRight" ? 1 : -1), first, last - 1);
    if (els.tooltip) {
      els.tooltip.textContent = `${edges[hoverBin].toFixed(1)} to ${edges[hoverBin + 1].toFixed(1)} °C: ${pct[hoverBin].toFixed(2)}%`;
      els.tooltip.style.left = "12px"; els.tooltip.style.top = "12px"; els.tooltip.hidden = false;
    }
  });

  if (els.canvas && "ResizeObserver" in window) {
    resizeObserver = new ResizeObserver(() => {
      if (currentData && isActive()) renderChart();
    });
    resizeObserver.observe(els.canvas);
  }

  if (els.shell) els.shell.hidden = true;
  return { setRegion, ensureLoaded, setMode };
}
