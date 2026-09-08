import { MOON_IMPACT_STATS } from "./moonImpactStats.js?v=20260908-env8-selenology";
import { renderMoonApsidalPlot } from "./moonPlot.js";
import { renderMoonTimers } from "./moonTimers.js";

function formatArea(areaKm2) {
  if (!Number.isFinite(areaKm2) || areaKm2 <= 0) return "Unavailable";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(areaKm2)} km²`;
}

function formatLatLon(center) {
  if (!center || !Number.isFinite(center.lat) || !Number.isFinite(center.lon)) {
    return "Unavailable";
  }
  const lat = `${Math.abs(center.lat).toFixed(2)}°${center.lat >= 0 ? "N" : "S"}`;
  const lon = `${Math.abs(center.lon).toFixed(2)}°${center.lon >= 0 ? "E" : "W"}`;
  return `${lat}, ${lon}`;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function createUI(appConfig = null) {
  const els = {
    loadingOverlay: document.getElementById("loading-overlay"),
    loadingText: document.getElementById("loading-text"),
    errorBanner: document.getElementById("error-banner"),
    hoverPill: document.getElementById("hover-pill"),
    earthTag: document.getElementById("earth-tag"),
    moonTag: document.getElementById("moon-tag"),
    sunTag: document.getElementById("sun-tag"),
    datasetSummary: document.getElementById("dataset-summary"),
    regionDatasetToggleBtn: document.getElementById("region-dataset-toggle-btn"),
    regionFillToggleBtn: document.getElementById("region-fill-toggle-btn"),
    cameraAnchorToggleBtn: document.getElementById("camera-anchor-toggle-btn"),
    resetViewBtn: document.getElementById("reset-view-btn"),
    skyControlsToggleBtn: document.getElementById("sky-controls-toggle-btn"),
    nightDebugToggleBtn: document.getElementById("night-debug-toggle-btn"),
    skyControlsPanel: document.getElementById("sky-controls-panel"),
    skyFiguresToggle: document.getElementById("sky-figures-toggle"),
    skyFiguresOpacity: document.getElementById("sky-figures-opacity"),
    skyFiguresBrightness: document.getElementById("sky-figures-brightness"),
    skyBoundariesToggle: document.getElementById("sky-boundaries-toggle"),
    skyBoundariesOpacity: document.getElementById("sky-boundaries-opacity"),
    skyBoundariesBrightness: document.getElementById("sky-boundaries-brightness"),
    sidebar: document.getElementById("sidebar"),
    sidebarCloseBtn: document.getElementById("sidebar-close-btn"),
    sidebarResizeHandle: document.getElementById("sidebar-resize-handle"),
    sidebarTabOverview: document.getElementById("sidebar-tab-overview"),
    sidebarTabClimate: document.getElementById("sidebar-tab-climate"),
    sidebarTabHealth: document.getElementById("sidebar-tab-health"),
    sidebarTabGeology: document.getElementById("sidebar-tab-geology"),
    sidebarTabSelenology: document.getElementById("sidebar-tab-selenology"),
    sidebarTabTertiary: document.getElementById("sidebar-tab-tertiary"),
    sidebarTabQuaternary: document.getElementById("sidebar-tab-quaternary"),
    sidebarEmpty: document.getElementById("sidebar-empty"),
    sidebarLoading: document.getElementById("sidebar-loading"),
    sidebarContent: document.getElementById("sidebar-content"),
    moonOverviewGrid: document.getElementById("moon-overview-grid"),
    metaSection: document.getElementById("meta-section"),
    sidebarClimatePanel: document.getElementById("sidebar-climate-panel"),
    sidebarHealthPanel: document.getElementById("sidebar-health-panel"),
    sidebarGeologyPanel: document.getElementById("sidebar-geology-panel"),
    sidebarSelenologyPanel: document.getElementById("sidebar-selenology-panel"),
    sidebarTertiaryPanel: document.getElementById("sidebar-tertiary-panel"),
    sidebarQuaternaryPanel: document.getElementById("sidebar-quaternary-panel"),
    sidebarClimateCopy: document.getElementById("sidebar-climate-copy"),
    sidebarTertiaryOverline: document.getElementById("sidebar-tertiary-overline"),
    sidebarTertiaryTitle: document.getElementById("sidebar-tertiary-title"),
    sidebarTertiaryCopy: document.getElementById("sidebar-tertiary-copy"),
    moonTimerShell: document.getElementById("moon-timer-shell"),
    moonTimerGrid: document.getElementById("moon-timer-grid"),
    sidebarQuaternaryOverline: document.getElementById("sidebar-quaternary-overline"),
    sidebarQuaternaryTitle: document.getElementById("sidebar-quaternary-title"),
    sidebarQuaternaryCopy: document.getElementById("sidebar-quaternary-copy"),
    moonPlotShell: document.getElementById("moon-plot-shell"),
    moonPlotCanvas: document.getElementById("moon-plot-canvas"),
    moonPlotStats: document.getElementById("moon-plot-stats"),
    moonPlotOverlay: document.getElementById("moon-plot-overlay"),
    moonPlotOverlayClose: document.getElementById("moon-plot-overlay-close"),
    moonPlotOverlayCanvas: document.getElementById("moon-plot-overlay-canvas"),
    moonPlotOverlayStats: document.getElementById("moon-plot-overlay-stats"),
    regionIdLabel: document.getElementById("region-id-label"),
    regionName: document.getElementById("region-name"),
    biomeBadge: document.getElementById("biome-badge"),
    realmBadge: document.getElementById("realm-badge"),
    regionArea: document.getElementById("region-area"),
    regionCenter: document.getElementById("region-center"),
    metadataList: document.getElementById("metadata-list"),
  };
  const sidebarTabs = [
    els.sidebarTabOverview,
    els.sidebarTabClimate,
    els.sidebarTabHealth,
    els.sidebarTabGeology,
    els.sidebarTabSelenology,
    els.sidebarTabTertiary,
    els.sidebarTabQuaternary,
  ].filter(Boolean);
  const sidebarPanes = [
    els.sidebarContent,
    els.sidebarClimatePanel,
    els.sidebarHealthPanel,
    els.sidebarGeologyPanel,
    els.sidebarSelenologyPanel,
    els.sidebarTertiaryPanel,
    els.sidebarQuaternaryPanel,
  ].filter(Boolean);
  let activeSidebarPanel = "overview";
  let sidebarResizeCleanup = null;
  let moonPanelTimer = 0;
  let lastMoonPlotRenderAt = 0;
  let currentSidebarRegion = null;
  const moonPlotOptions = {
    referenceDateUtc: appConfig?.globe?.moonApsidalReferenceDateUtc,
    sampleStepHours: appConfig?.globe?.moonPlotSampleStepHours,
    cacheMaxAgeMs: appConfig?.globe?.moonPlotCacheMaxAgeMs,
    fullnessDeg: appConfig?.globe?.moonPlotFullnessDeg,
  };
  const moonTimerOptions = {
    coarseStepHours: appConfig?.globe?.moonTimerCoarseStepHours,
    refineStepMinutes: appConfig?.globe?.moonTimerRefineStepMinutes,
    cacheMaxAgeMs: appConfig?.globe?.moonTimerCacheMaxAgeMs,
    searchLunations: appConfig?.globe?.moonTimerSearchLunations,
  };

  function stopMoonPanelTimer() {
    if (!moonPanelTimer) return;
    window.clearInterval(moonPanelTimer);
    moonPanelTimer = 0;
  }

  function renderMoonPanelsIfNeeded(region) {
    const isMoon = region?.id === "moon";
    if (!isMoon) {
      stopMoonPanelTimer();
      if (els.moonTimerShell) els.moonTimerShell.hidden = true;
      if (els.moonPlotShell) els.moonPlotShell.hidden = true;
      if (els.moonPlotOverlay) els.moonPlotOverlay.hidden = true;
      if (els.sidebarTertiaryCopy) els.sidebarTertiaryCopy.parentElement.hidden = false;
      if (els.sidebarQuaternaryCopy) els.sidebarQuaternaryCopy.parentElement.hidden = false;
      return;
    }

    if (els.sidebarTertiaryCopy) els.sidebarTertiaryCopy.parentElement.hidden = true;
    if (els.moonTimerShell) els.moonTimerShell.hidden = false;
    if (els.sidebarQuaternaryCopy) els.sidebarQuaternaryCopy.parentElement.hidden = true;
    if (els.moonPlotShell) els.moonPlotShell.hidden = false;

    const renderTick = (forcePlot = false) => {
      const now = new Date();
      renderMoonTimers(els.moonTimerGrid, now, moonTimerOptions);
      const nowMs = now.getTime();
      if (forcePlot || nowMs - lastMoonPlotRenderAt >= 60000) {
        renderMoonApsidalPlot(els.moonPlotCanvas, els.moonPlotStats, now, moonPlotOptions);
        lastMoonPlotRenderAt = nowMs;
      }
      if (els.moonPlotOverlay && els.moonPlotOverlay.hidden === false) {
        renderMoonApsidalPlot(els.moonPlotOverlayCanvas, els.moonPlotOverlayStats, now, moonPlotOptions);
      }
    };

    renderTick(true);
    stopMoonPanelTimer();
    moonPanelTimer = window.setInterval(() => {
      renderTick(false);
    }, 1000);
  }

  function openMoonPlotOverlay() {
    if (!els.moonPlotOverlay || currentSidebarRegion?.id !== "moon") return;
    els.moonPlotOverlay.hidden = false;
    const now = new Date();
    renderMoonApsidalPlot(els.moonPlotOverlayCanvas, els.moonPlotOverlayStats, now, moonPlotOptions);
  }

  function closeMoonPlotOverlay() {
    if (!els.moonPlotOverlay) return;
    els.moonPlotOverlay.hidden = true;
  }

  function setActiveSidebarPanel(panelName = "overview") {
    activeSidebarPanel = panelName;
    for (const tab of sidebarTabs) {
      const isActive = tab.dataset.panel === panelName;
      tab.classList.toggle("is-active", isActive);
      if (isActive) {
        tab.setAttribute("aria-current", "page");
      } else {
        tab.removeAttribute("aria-current");
      }
    }
    for (const pane of sidebarPanes) {
      const isActive = pane.dataset.panel === panelName;
      pane.hidden = !isActive;
    }
    if (panelName === "quaternary" && currentSidebarRegion?.id === "moon") {
      window.requestAnimationFrame(() => {
        const now = new Date();
        renderMoonApsidalPlot(els.moonPlotCanvas, els.moonPlotStats, now, moonPlotOptions);
        lastMoonPlotRenderAt = now.getTime();
      });
    }
    if (panelName === "tertiary" && currentSidebarRegion?.id === "moon") {
      window.requestAnimationFrame(() => {
        renderMoonTimers(els.moonTimerGrid, new Date(), moonTimerOptions);
      });
    }
  }

  function setSummaryContext(copy, region, field) {
    if (!copy) return;
    const card = copy.parentElement;
    const label = card.querySelector(".detail-label");
    const level = region?.contentLevels?.[field];
    if (label) {
      label.textContent = level === "biome" ? `Biome overview · ${region.biome}`
        : level === "marine" ? "Marine zone overview"
        : level === "lake" ? "General lake overview"
        : level === "region" ? "Regional summary" : "Summary";
    }
    card.querySelector(".content-sources")?.remove();
    if (level !== "region" || !region?.contentSources?.length) return;
    const references = document.createElement("p");
    references.className = "content-sources";
    references.append("Sources: ");
    let count = 0;
    for (const source of region.contentSources) {
      let url;
      try { url = new URL(source.url); } catch { continue; }
      if (url.protocol !== "https:" && url.protocol !== "http:") continue;
      if (count++) references.append(" · ");
      const link = document.createElement("a");
      link.href = url.href;
      link.textContent = source.publisher || source.name || url.hostname;
      link.title = source.name || "Editorial reference";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      references.append(link);
    }
    if (count) card.append(references);
  }

  function setSidebarTabLabels(region) {
    const isMoon = region?.id === "moon";
    setSummaryContext(els.sidebarClimateCopy, region, "climateSummary");
    setSummaryContext(els.sidebarQuaternaryCopy, region, "plotsSummary");
    if (els.sidebarTabClimate) {
      els.sidebarTabClimate.hidden = isMoon;
    }
    if (els.sidebarClimatePanel) {
      els.sidebarClimatePanel.hidden = isMoon || activeSidebarPanel !== "climate";
    }
    if (els.sidebarTabHealth) els.sidebarTabHealth.hidden = isMoon;
    if (els.sidebarTabGeology) els.sidebarTabGeology.hidden = isMoon;
    if (els.sidebarTabSelenology) els.sidebarTabSelenology.hidden = !isMoon;
    if (els.sidebarSelenologyPanel) els.sidebarSelenologyPanel.hidden = !isMoon || activeSidebarPanel !== "selenology";
    if (els.sidebarGeologyPanel) els.sidebarGeologyPanel.hidden = isMoon || activeSidebarPanel !== "geology";
    if (els.sidebarHealthPanel) els.sidebarHealthPanel.hidden = isMoon || activeSidebarPanel !== "health";
    if (isMoon && activeSidebarPanel === "climate") {
      activeSidebarPanel = "overview";
    }
    if (isMoon && activeSidebarPanel === "health") activeSidebarPanel = "overview";
    if (isMoon && activeSidebarPanel === "geology") activeSidebarPanel = "overview";
    if (!isMoon && activeSidebarPanel === "selenology") activeSidebarPanel = "overview";
    if (els.sidebarTabTertiary) {
      els.sidebarTabTertiary.textContent = isMoon ? "Timers" : "Species";
    }
    if (els.sidebarTabQuaternary) {
      els.sidebarTabQuaternary.textContent = isMoon ? "Plots" : "Threats";
    }
    if (els.sidebarClimateCopy) {
      els.sidebarClimateCopy.textContent =
        region?.climateSummary ||
        (isMoon
          ? "No atmosphere, no weather. Surface temperatures swing from about -173 C at night to 127 C in daylight. Illumination geometry and local solar angle drive nearly everything."
          : "Climate summary not available yet.");
    }
    if (els.sidebarTertiaryOverline) {
      els.sidebarTertiaryOverline.textContent = isMoon ? "Timers" : `Species · ${region?.name || "Selected region"}`;
    }
    if (els.sidebarTertiaryTitle) {
      els.sidebarTertiaryTitle.textContent = isMoon ? "Lunar Timers" : "Recorded species";
    }
    if (els.sidebarTertiaryCopy) {
      els.sidebarTertiaryCopy.textContent = isMoon ? region?.tertiarySummary || "" :
        "Recorded occurrences from public biodiversity datasets. Sampling is uneven; this is not a complete species list. Counts represent records, not population sizes.";
    }
    if (els.sidebarQuaternaryOverline) {
      els.sidebarQuaternaryOverline.textContent = isMoon ? "Plots" : "Threats";
    }
    if (els.sidebarQuaternaryTitle) {
      els.sidebarQuaternaryTitle.textContent = isMoon ? "Lunar Plots" : "Threat Profile";
    }
    if (els.sidebarQuaternaryCopy) {
      els.sidebarQuaternaryCopy.textContent =
        region?.plotsSummary ||
        (isMoon
          ? "The plots tab tracks live Earth-Moon distance across one apsidal cycle, with full-moon windows highlighted directly from the Cesium ephemeris."
          : "Threat summary not available yet.");
    }
  }

  for (const tab of sidebarTabs) {
    tab.addEventListener("click", () => {
      setActiveSidebarPanel(tab.dataset.panel || "overview");
    });
  }
  setActiveSidebarPanel(activeSidebarPanel);

  function bindSidebarResize() {
    if (!els.sidebar || !els.sidebarResizeHandle || typeof window === "undefined") return;

    const minWidth = 520;
    const getMaxWidth = () => Math.max(minWidth + 20, window.innerWidth - 28);

    const stopExisting = () => {
      if (typeof sidebarResizeCleanup === "function") {
        sidebarResizeCleanup();
        sidebarResizeCleanup = null;
      }
    };

    els.sidebarResizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 980) return;
      stopExisting();
      event.preventDefault();

      const startX = event.clientX;
      const startWidth = els.sidebar.getBoundingClientRect().width;
      els.sidebar.classList.add("is-resizing");
      els.sidebarResizeHandle.setPointerCapture?.(event.pointerId);

      const onMove = (moveEvent) => {
        const deltaX = startX - moveEvent.clientX;
        const nextWidth = Math.max(minWidth, Math.min(getMaxWidth(), startWidth + deltaX));
        els.sidebar.style.width = `${Math.round(nextWidth)}px`;
      };

      const endResize = () => {
        els.sidebar.classList.remove("is-resizing");
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", endResize);
        window.removeEventListener("pointercancel", endResize);
        sidebarResizeCleanup = null;
      };

      sidebarResizeCleanup = endResize;
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", endResize);
      window.addEventListener("pointercancel", endResize);
    });

    window.addEventListener("resize", () => {
      if (!els.sidebar.style.width) return;
      const currentWidth = parseFloat(els.sidebar.style.width);
      if (!Number.isFinite(currentWidth)) return;
      const clamped = Math.max(minWidth, Math.min(getMaxWidth(), currentWidth));
      els.sidebar.style.width = `${Math.round(clamped)}px`;
    });
  }
  bindSidebarResize();

  els.moonPlotCanvas?.addEventListener("click", () => {
    openMoonPlotOverlay();
  });
  els.moonPlotOverlayClose?.addEventListener("click", () => {
    closeMoonPlotOverlay();
  });
  els.moonPlotOverlay?.addEventListener("click", (event) => {
    if (event.target === els.moonPlotOverlay || event.target?.classList?.contains("moon-plot-overlay-backdrop")) {
      closeMoonPlotOverlay();
    }
  });

  function setLoading(message, visible = true) {
    els.loadingText.textContent = message;
    els.loadingOverlay.classList.toggle("hidden", !visible);
    els.loadingOverlay.hidden = !visible;
  }

  function hideLoading() {
    setLoading("", false);
  }

  function showError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.hidden = false;
  }

  function clearError() {
    els.errorBanner.hidden = true;
    els.errorBanner.textContent = "";
  }

  function setDatasetSummary(text) {
    els.datasetSummary.textContent = text;
  }

  function setRegionDatasetMode(mode) {
    if (!els.regionDatasetToggleBtn) return;
    let label = "Dataset: Land";
    if (mode === "marine") {
      label = "Dataset: Marine";
    } else if (mode === "combined") {
      label = "Dataset: Combined";
    }
    els.regionDatasetToggleBtn.textContent = label;
    els.regionDatasetToggleBtn.setAttribute("aria-pressed", String(mode !== "land"));
  }

  function setRegionFillEnabled(enabled) {
    if (!els.regionFillToggleBtn) return;
    els.regionFillToggleBtn.textContent = enabled ? "Filled Regions" : "Outlines Only";
    els.regionFillToggleBtn.setAttribute("aria-pressed", String(Boolean(enabled)));
  }

  function setCameraAnchorMode(mode) {
    if (!els.cameraAnchorToggleBtn) return;
    const isMoon = mode === "moon";
    els.cameraAnchorToggleBtn.textContent = isMoon ? "Anchor: Moon" : "Anchor: Earth";
    els.cameraAnchorToggleBtn.setAttribute("aria-pressed", String(isMoon));
  }

  function setNightDiagnosticsEnabled(enabled) {
    if (!els.nightDebugToggleBtn) return;
    const isOn = Boolean(enabled);
    els.nightDebugToggleBtn.textContent = isOn ? "Night Debug: On" : "Night Debug";
    els.nightDebugToggleBtn.setAttribute("aria-pressed", String(isOn));
  }

  function setSidebarVisible(visible) {
    if (!els.sidebar) return;
    els.sidebar.hidden = !visible;
  }

  function setHoverPreview(region, screenPosition) {
    if (!region || !screenPosition) {
      els.hoverPill.hidden = true;
      return;
    }
    els.hoverPill.textContent = `${region.name} • ${region.biome}`;
    els.hoverPill.style.transform = `translate(${Math.round(screenPosition.x + 12)}px, ${Math.round(screenPosition.y + 12)}px)`;
    els.hoverPill.hidden = false;
  }

  function setBodyTag(el, screenPosition) {
    if (!el || !screenPosition) {
      if (el) el.hidden = true;
      return;
    }
    el.style.transform = `translate(${Math.round(screenPosition.x + 14)}px, ${Math.round(screenPosition.y - 30)}px)`;
    el.hidden = false;
  }

  function setMoonTag(screenPosition) {
    setBodyTag(els.moonTag, screenPosition);
  }

  function setEarthTag(screenPosition) {
    setBodyTag(els.earthTag, screenPosition);
  }

  function setBodyTags(tags = {}) {
    setEarthTag(tags.earth || null);
    setMoonTag(tags.moon || null);
    setBodyTag(els.sunTag, tags.sun || null);
  }

  function showSidebarEmpty() {
    currentSidebarRegion = null;
    setSidebarVisible(false);
    setActiveSidebarPanel("overview");
    if (els.sidebarTabClimate) els.sidebarTabClimate.hidden = false;
    if (els.sidebarTabSelenology) els.sidebarTabSelenology.hidden = true;
    if (els.sidebarTabHealth) els.sidebarTabHealth.hidden = false;
    if (els.sidebarTabGeology) els.sidebarTabGeology.hidden = false;
    if (els.metaSection) els.metaSection.hidden = false;
    if (els.moonOverviewGrid) {
      els.moonOverviewGrid.hidden = true;
      els.moonOverviewGrid.innerHTML = "";
    }
    stopMoonPanelTimer();
    if (els.moonTimerShell) els.moonTimerShell.hidden = true;
    if (els.moonPlotShell) els.moonPlotShell.hidden = true;
    if (els.moonPlotOverlay) els.moonPlotOverlay.hidden = true;
    if (els.sidebarTertiaryCopy) els.sidebarTertiaryCopy.parentElement.hidden = false;
    if (els.sidebarQuaternaryCopy) els.sidebarQuaternaryCopy.parentElement.hidden = false;
    els.sidebarEmpty.hidden = false;
    els.sidebarLoading.hidden = true;
    for (const pane of sidebarPanes) {
      pane.hidden = true;
    }
  }

  function showSidebarLoading() {
    currentSidebarRegion = null;
    setSidebarVisible(true);
    setActiveSidebarPanel(activeSidebarPanel || "overview");
    if (els.sidebarTabClimate) els.sidebarTabClimate.hidden = false;
    if (els.sidebarTabSelenology) els.sidebarTabSelenology.hidden = true;
    if (els.sidebarTabHealth) els.sidebarTabHealth.hidden = false;
    if (els.sidebarTabGeology) els.sidebarTabGeology.hidden = false;
    if (els.metaSection) els.metaSection.hidden = false;
    if (els.moonOverviewGrid) {
      els.moonOverviewGrid.hidden = true;
      els.moonOverviewGrid.innerHTML = "";
    }
    stopMoonPanelTimer();
    if (els.moonTimerShell) els.moonTimerShell.hidden = true;
    if (els.moonPlotShell) els.moonPlotShell.hidden = true;
    if (els.moonPlotOverlay) els.moonPlotOverlay.hidden = true;
    if (els.sidebarTertiaryCopy) els.sidebarTertiaryCopy.parentElement.hidden = false;
    if (els.sidebarQuaternaryCopy) els.sidebarQuaternaryCopy.parentElement.hidden = false;
    els.sidebarEmpty.hidden = true;
    els.sidebarLoading.hidden = false;
    for (const pane of sidebarPanes) {
      pane.hidden = true;
    }
  }

  function renderRegionSummary(region, biomeColor = "#8b949e") {
    if (!region) {
      showSidebarEmpty();
      return;
    }
    currentSidebarRegion = region;
    setSidebarVisible(true);
    setSidebarTabLabels(region);
    renderMoonPanelsIfNeeded(region);
    els.sidebarEmpty.hidden = true;
    els.sidebarLoading.hidden = true;
    setActiveSidebarPanel(activeSidebarPanel || "overview");

    els.regionIdLabel.textContent = region.id;
    els.regionName.textContent = region.name;
    els.biomeBadge.textContent = region.biome || "Unknown biome";
    els.biomeBadge.style.setProperty("--badge-color", biomeColor);
    els.realmBadge.textContent = region.realm || "Unknown realm";
    els.regionArea.textContent = formatArea(region.areaKm2);
    els.regionCenter.textContent = formatLatLon(region.center);

    if (region.id === "moon") {
      if (els.metaSection) els.metaSection.hidden = true;
      if (els.moonOverviewGrid) {
        els.moonOverviewGrid.hidden = false;
        const cards = [
          ["Body", "Moon (Luna)"],
          ["Type", "Natural satellite"],
          ["Mean Radius", "1,737.4 km"],
          ["Mean Distance", "384,400 km"],
          ["Surface Gravity", "1.62 m/s^2"],
          ["Sidereal Rotation", "27.32 days"],
          ["Synodic Phase Cycle", "29.53 days"],
          ["Catalogued Craters ≥1 km", MOON_IMPACT_STATS.cratersGe1Km.toLocaleString()],
          ["Catalogued Craters ≥20 km", MOON_IMPACT_STATS.cratersGe20Km.toLocaleString()],
          ["Crater Catalog Completeness", "≈1–2 km and larger"],
          ["Escape Velocity", "2.38 km/s"],
        ];
        els.moonOverviewGrid.innerHTML = cards
          .map(
            ([label, value]) => `
              <div class="detail-card">
                <div class="detail-label">${escapeHtml(label)}</div>
                <div class="detail-value">${escapeHtml(value)}</div>
              </div>
            `
          )
          .join("");
      }
      els.metadataList.innerHTML = "";
      return;
    }

    if (els.metaSection) els.metaSection.hidden = false;
    if (els.moonOverviewGrid) {
      els.moonOverviewGrid.hidden = true;
      els.moonOverviewGrid.innerHTML = "";
    }

    const rows = [
      ["Ecoregion ID", region.ecoId ?? "Unknown"],
      ["Biome Number", region.biomeNum ?? "Unknown"],
      ["Realm", region.realm || "Unknown"],
      ["ECO_BIOME_", region.ecoBiomeCode || "—"],
      ["NNH Code", region.nnhCode ?? "—"],
      ["NNH Label", region.nnhName || "—"],
    ];

    els.metadataList.innerHTML = rows
      .map(([label, value]) => `<div class="row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
      .join("");
  }

  function setSkyControlsOpen(open) {
    if (!els.skyControlsPanel || !els.skyControlsToggleBtn) return;
    els.skyControlsPanel.hidden = !open;
    els.skyControlsToggleBtn.setAttribute("aria-expanded", String(open));
  }

  function setSkyControlsState(state) {
    if (!state) return;
    if (els.skyFiguresToggle) els.skyFiguresToggle.checked = Boolean(state.showFigures);
    if (els.skyBoundariesToggle) els.skyBoundariesToggle.checked = Boolean(state.showBoundaries);
    if (els.skyFiguresOpacity && Number.isFinite(state.figuresOpacity)) {
      els.skyFiguresOpacity.value = String(state.figuresOpacity);
    }
    if (els.skyBoundariesOpacity && Number.isFinite(state.boundariesOpacity)) {
      els.skyBoundariesOpacity.value = String(state.boundariesOpacity);
    }
    if (els.skyFiguresBrightness && Number.isFinite(state.figuresBrightness)) {
      els.skyFiguresBrightness.value = String(state.figuresBrightness);
    }
    if (els.skyBoundariesBrightness && Number.isFinite(state.boundariesBrightness)) {
      els.skyBoundariesBrightness.value = String(state.boundariesBrightness);
    }

    if (els.skyFiguresToggle) els.skyFiguresToggle.disabled = state.hasFiguresLayer === false;
    if (els.skyFiguresOpacity) els.skyFiguresOpacity.disabled = state.hasFiguresLayer === false;
    if (els.skyFiguresBrightness) els.skyFiguresBrightness.disabled = state.hasFiguresLayer === false;
    if (els.skyBoundariesToggle) els.skyBoundariesToggle.disabled = state.hasBoundariesLayer === false;
    if (els.skyBoundariesOpacity) els.skyBoundariesOpacity.disabled = state.hasBoundariesLayer === false;
    if (els.skyBoundariesBrightness) els.skyBoundariesBrightness.disabled = state.hasBoundariesLayer === false;
  }

  function bindSkyControls(onChange) {
    if (typeof onChange !== "function") return;
    const readState = () => ({
      showFigures: Boolean(els.skyFiguresToggle?.checked),
      showBoundaries: Boolean(els.skyBoundariesToggle?.checked),
      figuresOpacity: Number(els.skyFiguresOpacity?.value),
      boundariesOpacity: Number(els.skyBoundariesOpacity?.value),
      figuresBrightness: Number(els.skyFiguresBrightness?.value),
      boundariesBrightness: Number(els.skyBoundariesBrightness?.value),
    });

    els.skyControlsToggleBtn?.addEventListener("click", () => {
      const nextOpen = els.skyControlsPanel?.hidden !== false;
      setSkyControlsOpen(nextOpen);
    });

    for (const el of [
      els.skyFiguresToggle,
      els.skyBoundariesToggle,
      els.skyFiguresOpacity,
      els.skyBoundariesOpacity,
      els.skyFiguresBrightness,
      els.skyBoundariesBrightness,
    ]) {
      el?.addEventListener("input", () => onChange(readState()));
      el?.addEventListener("change", () => onChange(readState()));
    }
  }

  function bindRegionFillToggle(onToggle) {
    if (typeof onToggle !== "function" || !els.regionFillToggleBtn) return;
    els.regionFillToggleBtn.addEventListener("click", () => {
      const nextEnabled = els.regionFillToggleBtn.getAttribute("aria-pressed") !== "true";
      setRegionFillEnabled(nextEnabled);
      onToggle(nextEnabled);
    });
  }

  function bindRegionDatasetToggle(onToggle) {
    if (typeof onToggle !== "function" || !els.regionDatasetToggleBtn) return;
    els.regionDatasetToggleBtn.addEventListener("click", () => onToggle());
  }

  function bindCameraAnchorToggle(onToggle) {
    if (typeof onToggle !== "function" || !els.cameraAnchorToggleBtn) return;
    els.cameraAnchorToggleBtn.addEventListener("click", () => onToggle());
  }

  function bindBodyTagActions(actions = {}) {
    if (typeof actions.onEarth === "function" && els.earthTag) {
      els.earthTag.addEventListener("click", () => actions.onEarth());
    }
    if (typeof actions.onMoon === "function" && els.moonTag) {
      els.moonTag.addEventListener("click", () => actions.onMoon());
    }
  }

  function bindNightDiagnosticsToggle(onToggle) {
    if (typeof onToggle !== "function" || !els.nightDebugToggleBtn) return;
    els.nightDebugToggleBtn.addEventListener("click", () => onToggle());
  }

  function bindSidebarClose(onClose) {
    if (typeof onClose !== "function" || !els.sidebarCloseBtn) return;
    els.sidebarCloseBtn.addEventListener("click", () => onClose());
  }

  return {
    elements: els,
    setLoading,
    hideLoading,
    showError,
    clearError,
    setDatasetSummary,
    setRegionDatasetMode,
    setRegionFillEnabled,
    setCameraAnchorMode,
    setNightDiagnosticsEnabled,
    setHoverPreview,
    setEarthTag,
    setMoonTag,
    setBodyTags,
    showSidebarEmpty,
    showSidebarLoading,
    renderRegionSummary,
    setSkyControlsOpen,
    setSkyControlsState,
    bindSkyControls,
    bindRegionFillToggle,
    bindRegionDatasetToggle,
    bindCameraAnchorToggle,
    bindBodyTagActions,
    bindNightDiagnosticsToggle,
    bindSidebarClose,
    setActiveSidebarPanel,
  };
}
