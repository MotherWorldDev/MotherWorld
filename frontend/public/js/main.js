import { APP_CONFIG } from "./config.js?v=20260907-temperature1";
import { createRegionDataService } from "./regionDataService.js?v=20260906-content1";
import { createUI } from "./ui.js?v=20260906-species1";
import { createTemperaturePanel } from "./temperaturePanel.js?v=20260907-temperature1";
import { createSpeciesPanel } from "./speciesPanel.js?v=20260907-species2";
import { getBiomeColor, paletteMapFromIndex } from "./biomePalette.js";
import { createGlobeExplorer } from "./globe.js?v=20260906-regions";

function toPublicDataUrl(relPath, fallback) {
  if (!relPath) return fallback;
  if (relPath.startsWith("./")) return relPath;
  if (relPath.startsWith("data/")) return `./${relPath}`;
  return `./data/${relPath}`;
}

function getDebugBootstrapOptions() {
  return {
    enabled: false,
    regionId: "",
    fillOnly: false,
    outlineOnly: false,
    lod0Only: false,
    lod1Only: false,
    disableLodSwap: false,
  };
}

function createPerfHud() {
  const root = document.getElementById("perf-hud");
  const text = document.getElementById("perf-hud-text");
  const g = APP_CONFIG.globe || {};
  const detailInKm = Number.isFinite(g.realmDetailEnterHeight) ? (g.realmDetailEnterHeight / 1000).toFixed(0) : "-";
  const detailOutKm = Number.isFinite(g.realmDetailExitHeight) ? (g.realmDetailExitHeight / 1000).toFixed(0) : "-";
  return {
    set(report) {
      if (!root || !text || !report) return;
      const km = Number.isFinite(report.cameraHeightMeters) ? (report.cameraHeightMeters / 1000).toFixed(0) : "-";
      const res = Number.isFinite(report.resolutionScale) ? report.resolutionScale.toFixed(2) : "-";
      const bmTiles = report.blueMarbleDetailTilesVisible ? "on" : "off";
      const bmAlpha = Number.isFinite(report.blueMarbleDetailTilesAlpha)
        ? report.blueMarbleDetailTilesAlpha.toFixed(2)
        : "0.00";
      const bmUltra = report.blueMarbleUltraTilesVisible ? "on" : "off";
      const bmUltraAlpha = Number.isFinite(report.blueMarbleUltraTilesAlpha)
        ? report.blueMarbleUltraTilesAlpha.toFixed(2)
        : "0.00";
      const bmNight = report.blackMarbleNightVisible ? "on" : "off";
      const bmNightDetail = report.blackMarbleNightDetailTilesVisible ? "on" : "off";
      const bmNightDetailAlpha = Number.isFinite(report.blackMarbleNightDetailTilesAlpha)
        ? report.blackMarbleNightDetailTilesAlpha.toFixed(2)
        : "0.00";
      const bmNightUltra = report.blackMarbleNightUltraTilesVisible ? "on" : "off";
      const bmNightUltraAlpha = Number.isFinite(report.blackMarbleNightUltraTilesAlpha)
        ? report.blackMarbleNightUltraTilesAlpha.toFixed(2)
        : "0.00";
      const dayUltraReq = Number.isFinite(report.blueMarbleUltraRequestCount) ? report.blueMarbleUltraRequestCount : 0;
      const dayUltraUnique = Number.isFinite(report.blueMarbleUltraUniqueRequestCount)
        ? report.blueMarbleUltraUniqueRequestCount
        : 0;
      const dayUltraDedup = Number.isFinite(report.blueMarbleUltraDedupHitCount)
        ? report.blueMarbleUltraDedupHitCount
        : 0;
      const nightUltraReq = Number.isFinite(report.blackMarbleNightUltraRequestCount)
        ? report.blackMarbleNightUltraRequestCount
        : 0;
      const nightUltraUnique = Number.isFinite(report.blackMarbleNightUltraUniqueRequestCount)
        ? report.blackMarbleNightUltraUniqueRequestCount
        : 0;
      const nightUltraDedup = Number.isFinite(report.blackMarbleNightUltraDedupHitCount)
        ? report.blackMarbleNightUltraDedupHitCount
        : 0;
      const solarDot = Number.isFinite(report.imagerySolarDot) ? report.imagerySolarDot.toFixed(2) : "-";
      const moonDistanceKm = Number.isFinite(report.moonDistanceKm) ? report.moonDistanceKm.toFixed(0) : "-";
      const moonEclipseNow = report.moonTotalEclipseNow ? "yes" : "no";
      const moonEclipseTex = report.moonEclipseTextureActive ? "on" : "off";
      const skyState = report.skyDome || {};
      text.textContent =
        `zoom(km): ${km}\n` +
        `res scale: ${res}\n` +
        `bm detail: ${bmTiles} (${bmAlpha})\n` +
        `bm ultra: ${bmUltra} (${bmUltraAlpha})\n` +
        `bm night: ${bmNight}\n` +
        `bm night detail: ${bmNightDetail} (${bmNightDetailAlpha})\n` +
        `bm night ultra: ${bmNightUltra} (${bmNightUltraAlpha})\n` +
        `solar dot: ${solarDot} | gate: ${report.imagerySolarGatingEnabled ? "on" : "off"}\n` +
        `hi side: ${report.imageryHighDetailSide ?? "-"} | night dbg: ${report.nightDiagnosticsEnabled ? "on" : "off"}\n` +
        `light/base: ${report.lightingEnabled ? "on" : "off"}/${report.nightDiagnosticsEnabled ? "flat" : "blend"}\n` +
        `ultra req d/n: ${dayUltraReq}/${nightUltraReq}\n` +
        `ultra uniq d/n: ${dayUltraUnique}/${nightUltraUnique}\n` +
        `ultra dedup d/n: ${dayUltraDedup}/${nightUltraDedup}\n` +
        `realm: ${report.activeRealmSlug ?? "-"}\n` +
        `detail LOD: ${report.activeRealmLodLevel ?? "none"}\n` +
        `preferred: ${report.preferredRealmLodLevel ?? "-"}\n` +
        `moon: ${report.moonVisible ? "in-view" : "off-view"} | ${moonDistanceKm} km\n` +
        `moon eclipse/tex: ${moonEclipseNow}/${moonEclipseTex}\n` +
        `moon body: ${report.moonProxyEnabled ? "proxy" : "builtin"}\n` +
        `moon marker: ${report.moonDebugMarkerEnabled ? "on" : "off"}\n` +
        `anchor: ${report.cameraAnchorMode ?? "earth"}\n` +
        `ecoregions: ${report.ecoregionsVisible ? "on" : "hidden"}\n` +
        `sky fig/bnd: ${(skyState.showFigures ? "on" : "off")}/${(skyState.showBoundaries ? "on" : "off")}\n` +
        `sky orient: ${skyState.orientationMode ?? "-"}\n` +
        `detail on/off: ${detailInKm}/${detailOutKm} km\n` +
        `visible b/d: ${report.baseVisibleCount}/${report.detailVisibleCount}\n` +
        `dups: ${report.duplicateVisible} | cull: ${report.cameraCullingEnabled ? "on" : "off"}\n` +
        `move factor: ${g.movingResolutionScaleFactor ?? 0.75}`;
    },
  };
}

function createDebugPanelController(globe, initialOptions) {
  const panel = document.getElementById("debug-panel");
  const els = {
    panel,
    regionId: document.getElementById("debug-region-id"),
    fillOnly: document.getElementById("debug-fill-only"),
    outlineOnly: document.getElementById("debug-outline-only"),
    lod0Only: document.getElementById("debug-lod0-only"),
    lod1Only: document.getElementById("debug-lod1-only"),
    disableLodSwap: document.getElementById("debug-disable-lod-swap"),
    applyBtn: document.getElementById("debug-apply-btn"),
    clearBtn: document.getElementById("debug-clear-btn"),
    status: document.getElementById("debug-status"),
  };

  if (!initialOptions.enabled || !panel) {
    return {
      enabled: false,
      setStatus: () => {},
    };
  }

  panel.hidden = false;

  function syncInputsFromOptions(opts) {
    els.regionId.value = opts.regionId || "";
    els.fillOnly.checked = Boolean(opts.fillOnly);
    els.outlineOnly.checked = Boolean(opts.outlineOnly);
    els.lod0Only.checked = Boolean(opts.lod0Only);
    els.lod1Only.checked = Boolean(opts.lod1Only);
    els.disableLodSwap.checked = Boolean(opts.disableLodSwap);
  }

  function collectOptions() {
    return {
      enabled: true,
      regionId: els.regionId.value.trim(),
      fillOnly: els.fillOnly.checked,
      outlineOnly: els.outlineOnly.checked,
      lod0Only: els.lod0Only.checked,
      lod1Only: els.lod1Only.checked,
      disableLodSwap: els.disableLodSwap.checked,
    };
  }

  function enforceMutualExclusion(changed) {
    if (changed === "fillOnly" && els.fillOnly.checked) {
      els.outlineOnly.checked = false;
    }
    if (changed === "outlineOnly" && els.outlineOnly.checked) {
      els.fillOnly.checked = false;
    }
    if (changed === "lod0Only" && els.lod0Only.checked) {
      els.lod1Only.checked = false;
    }
    if (changed === "lod1Only" && els.lod1Only.checked) {
      els.lod0Only.checked = false;
    }
  }

  function apply() {
    globe.setDebugOptions(collectOptions());
  }

  function clear() {
    const cleared = {
      enabled: true,
      regionId: "",
      fillOnly: false,
      outlineOnly: false,
      lod0Only: false,
      lod1Only: false,
      disableLodSwap: false,
    };
    syncInputsFromOptions(cleared);
    globe.setDebugOptions(cleared);
  }

  for (const [key, input] of [
    ["fillOnly", els.fillOnly],
    ["outlineOnly", els.outlineOnly],
    ["lod0Only", els.lod0Only],
    ["lod1Only", els.lod1Only],
    ["disableLodSwap", els.disableLodSwap],
  ]) {
    input.addEventListener("change", () => {
      enforceMutualExclusion(key);
      apply();
    });
  }

  els.regionId.addEventListener("keydown", (event) => {
    if (event.key === "Enter") apply();
  });
  els.applyBtn.addEventListener("click", apply);
  els.clearBtn.addEventListener("click", clear);

  syncInputsFromOptions(initialOptions);
  globe.setDebugOptions(initialOptions);

  return {
    enabled: true,
    setStatus(report) {
      if (!els.status) return;
      els.status.textContent =
        `reason=${report.reason}\n` +
        `activeRealm=${report.activeRealmSlug ?? "-"}\n` +
        `visible(base/detail)=${report.baseVisibleCount}/${report.detailVisibleCount}\n` +
        `duplicates=${report.duplicateVisible}\n` +
        `flatViolations=${report.flatViolationCount}\n` +
        `selected=${report.selectedRegionId ?? "-"}\n` +
        `filter=${(report.debugOptions?.regionId || "") || "-"}`;
    },
  };
}

async function bootstrap() {
  const ui = createUI(APP_CONFIG);
  const dataService = createRegionDataService(APP_CONFIG.data);
  const speciesPanel = createSpeciesPanel(APP_CONFIG.data);
  const temperaturePanel = createTemperaturePanel(APP_CONFIG.data);
  let biomePalette = { default: APP_CONFIG.styling.defaultBiomeColor };
  const debugOptions = getDebugBootstrapOptions();
  let debugPanelController = { enabled: false, setStatus: () => {} };
  const perfHud = createPerfHud();

  ui.showSidebarEmpty();
  ui.setLoading("Loading ecoregion metadata...", true);

  try {
    const [index, marineIndex, lakesIndex] = await Promise.all([
      dataService.getIndex(),
      dataService.getMarineIndex(),
      dataService.getLakesIndex(),
    ]);
    biomePalette = {
      ...biomePalette,
      ...paletteMapFromIndex(index),
    };

    const total = index?.stats?.featureCount ?? Object.keys(index?.regions || {}).length;
    const marineTotal = marineIndex?.stats?.featureCount ?? Object.keys(marineIndex?.regions || {}).length;
    const lakesTotal = lakesIndex?.stats?.featureCount ?? Object.keys(lakesIndex?.regions || {}).length;
    const overviewRetain = index?.geometry?.overviewLod?.retainPct;
    const lod0Retain = index?.geometry?.startupLod?.retainPct;
    const lod1Retain = index?.geometry?.realmDetailLod?.retainPct;
    const singleLayer = APP_CONFIG.globe?.enableRealmDetailLod === false;
    const datasetParts = [`${total} land`];
    if (marineTotal > 0) datasetParts.push(`${marineTotal} marine`);
    if (lakesTotal > 0) datasetParts.push(`${lakesTotal} lakes`);
    const datasetLabel = datasetParts.join(" + ");
    ui.setDatasetSummary(
      singleLayer
        ? `${datasetLabel} | Overview ${overviewRetain ?? "?"}% | Global ${lod0Retain ?? "?"}%`
        : `${datasetLabel} | Overview ${overviewRetain ?? "?"}% | LOD0 ${lod0Retain ?? "?"}% | LOD1 ${lod1Retain ?? "?"}%`
    );

    let selectionSummaryVersion = 0;
    const globe = createGlobeExplorer({
      containerId: "globe",
      appConfig: APP_CONFIG,
      getBiomeColor: (biomeNum) => getBiomeColor(biomeNum, biomePalette),
      onHover: (region, screenPosition) => {
        ui.setHoverPreview(region, screenPosition);
      },
      onBodyTagsUpdate: (tags) => {
        ui.setBodyTags(tags);
      },
      onSelect: async (regionId) => {
        const summaryVersion = ++selectionSummaryVersion;
        speciesPanel.setRegion(null);
        temperaturePanel.setRegion(null);
        ui.setHoverPreview(null, null);
        if (!regionId) {
          ui.showSidebarEmpty();
          return;
        }
        ui.showSidebarLoading();
        try {
          const summary = await dataService.getRegionSummary(regionId);
          if (summaryVersion !== selectionSummaryVersion) return;
          ui.renderRegionSummary(summary, getBiomeColor(summary?.biomeNum, biomePalette));
          speciesPanel.setRegion(summary?.id);
          temperaturePanel.setRegion(summary);
        } catch (err) {
          if (summaryVersion !== selectionSummaryVersion) return;
          ui.showError(`Failed to load region summary: ${err.message}`);
          ui.showSidebarEmpty();
        }
      },
      onDebugReport: (report) => {
        debugPanelController.setStatus(report);
        perfHud.set(report);
      },
    });

    ui.setRegionFillEnabled(globe.getRegionFillEnabled?.() ?? (APP_CONFIG.styling.fillEnabled !== false));
    ui.setRegionDatasetMode(globe.getRegionDatasetMode?.() ?? "combined");
    ui.bindRegionDatasetToggle(async () => {
      const nextMode = await globe.toggleRegionDatasetMode?.();
      if (nextMode) {
        ui.setRegionDatasetMode(nextMode);
      }
    });
    ui.bindRegionFillToggle((enabled) => {
      globe.setRegionFillEnabled?.(enabled);
    });
    ui.setCameraAnchorMode(globe.getCameraAnchorMode?.() ?? "earth");
    ui.bindCameraAnchorToggle(() => {
      const nextMode = globe.toggleCameraAnchor?.();
      if (nextMode) ui.setCameraAnchorMode(nextMode);
    });
    ui.bindBodyTagActions({
      onEarth: () => {
        const nextMode = globe.activateEarthAnchorTarget?.();
        if (nextMode) ui.setCameraAnchorMode(nextMode);
      },
      onMoon: () => {
        const nextMode = globe.activateMoonAnchorTarget?.();
        if (nextMode) ui.setCameraAnchorMode(nextMode);
      },
    });
    ui.setNightDiagnosticsEnabled(globe.getNightDiagnosticsEnabled?.() ?? false);
    ui.bindNightDiagnosticsToggle(() => {
      const nextEnabled = globe.toggleNightDiagnostics?.();
      ui.setNightDiagnosticsEnabled(nextEnabled);
    });
    ui.bindSidebarClose(() => {
      globe.clearSelection?.().catch((err) => {
        console.warn("Failed to clear selection:", err);
      });
    });

    const initialSkySettings = globe.getSkySettings?.();
    if (initialSkySettings?.enabled) {
      ui.setSkyControlsState(initialSkySettings);
      ui.setSkyControlsOpen(false);
      ui.bindSkyControls((nextSkySettings) => {
        globe.setSkySettings?.(nextSkySettings);
      });
    } else {
      ui.setSkyControlsOpen(false);
      if (ui.elements.skyControlsToggleBtn) ui.elements.skyControlsToggleBtn.hidden = true;
    }

    debugPanelController = createDebugPanelController(globe, debugOptions);

    ui.elements.resetViewBtn.addEventListener("click", () => {
      globe.resetView();
      ui.setCameraAnchorMode(globe.getCameraAnchorMode?.() ?? "earth");
    });

    const overviewGeometryUrl = toPublicDataUrl(
      index?.geometry?.overviewLod?.url,
      APP_CONFIG.data.overviewGeometryUrl
    );
    const startupGeometryUrl = toPublicDataUrl(index?.geometry?.startupLod?.url, APP_CONFIG.data.startupGeometryUrl);
    const marineOverviewGeometryUrl = toPublicDataUrl(
      marineIndex?.geometry?.overviewLod?.url,
      APP_CONFIG.data.marineOverviewGeometryUrl
    );
    const marineStartupGeometryUrl = toPublicDataUrl(
      marineIndex?.geometry?.startupLod?.url,
      APP_CONFIG.data.marineStartupGeometryUrl
    );
    const lakesOverviewGeometryUrl = toPublicDataUrl(
      lakesIndex?.geometry?.overviewLod?.url,
      APP_CONFIG.data.lakesOverviewGeometryUrl
    );
    const lakesStartupGeometryUrl = toPublicDataUrl(
      lakesIndex?.geometry?.startupLod?.url,
      APP_CONFIG.data.lakesStartupGeometryUrl
    );
    const realmDetailFiles = Object.fromEntries(
      Object.entries(index?.geometry?.realmDetailLod?.files || {}).map(([realmSlug, relPath]) => [
        realmSlug,
        toPublicDataUrl(relPath),
      ])
    );
    ui.setLoading("Loading startup globe geometry...", true);
    await globe.loadGeometryLods({
      overviewUrl: overviewGeometryUrl,
      startupUrl: startupGeometryUrl,
      marineOverviewUrl: marineOverviewGeometryUrl,
      marineStartupUrl: marineStartupGeometryUrl,
      lakesOverviewUrl: lakesOverviewGeometryUrl,
      lakesStartupUrl: lakesStartupGeometryUrl,
      realmDetailFiles,
      regionIndex: index?.regions || {},
      marineRegionIndex: marineIndex?.regions || {},
      lakesRegionIndex: lakesIndex?.regions || {},
    });
    ui.setRegionDatasetMode(globe.getRegionDatasetMode?.() ?? "combined");
    ui.hideLoading();
  } catch (err) {
    console.error(err);
    ui.showError(
      `Initialization failed. Confirm the data files exist in ./frontend/public/data and run preprocessing first. (${err.message})`
    );
    ui.setLoading("Initialization failed", false);
  }
}

bootstrap();
