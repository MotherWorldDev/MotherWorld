/* global Cesium */
import { createSphericalTileLod } from "./sphericalTileLod.js?v=20260910-celestial2";

function clamp01(value, fallback = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Cesium.Math.clamp(n, 0, 1);
}

function clampBrightness(value, fallback = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Cesium.Math.clamp(n, 0, 4);
}

function normalizeAngleRad(rad) {
  return Cesium.Math.zeroToTwoPi(rad);
}

function gmstRadiansFromJulian(jd) {
  const date = Cesium.JulianDate.toDate(jd);
  const unixMs = date.getTime();
  const julianDay = unixMs / 86400000 + 2440587.5;
  const t = (julianDay - 2451545.0) / 36525.0;
  let gmstDeg =
    280.46061837 +
    360.98564736629 * (julianDay - 2451545.0) +
    0.000387933 * t * t -
    (t * t * t) / 38710000;
  gmstDeg = ((gmstDeg % 360) + 360) % 360;
  return Cesium.Math.toRadians(gmstDeg);
}

function createLayerPrimitive(viewer, { textureUrl, additiveBlend = false }) {
  if (!textureUrl) return null;

  const material = Cesium.Material.fromType("Image", {
    image: textureUrl,
    color: Cesium.Color.WHITE,
    repeat: new Cesium.Cartesian2(1, 1),
  });

  const geometry = new Cesium.SphereGeometry({
    radius: 1.0,
    vertexFormat: Cesium.MaterialAppearance.MaterialSupport.TEXTURED.vertexFormat,
    slicePartitions: 128,
    stackPartitions: 64,
  });

  const geometryInstance = new Cesium.GeometryInstance({
    geometry,
  });

  const appearance = new Cesium.MaterialAppearance({
    material,
    translucent: true,
    closed: false,
    flat: true,
    faceForward: true,
    renderState: {
      cull: {
        enabled: true,
        face: Cesium.CullFace.FRONT,
      },
      depthTest: {
        enabled: true,
      },
      depthMask: false,
      blending: additiveBlend ? Cesium.BlendingState.ADDITIVE_BLEND : Cesium.BlendingState.ALPHA_BLEND,
    },
  });

  const primitive = new Cesium.Primitive({
    geometryInstances: geometryInstance,
    appearance,
    asynchronous: false,
    allowPicking: false,
    releaseGeometryInstances: true,
    compressVertices: true,
  });

  viewer.scene.primitives.add(primitive);
  return { primitive, material, textureUrl };
}

export function createSkyDomeController({ viewer, appConfig, requestRender, getMinimumRadiusForTime }) {
  const globeCfg = appConfig?.globe || {};
  const enabled = globeCfg.skyDomeEnabled !== false;
  if (!enabled) {
    return {
      getSettings: () => ({
        enabled: false,
        showFigures: false,
        showBoundaries: false,
        figuresOpacity: 0,
        boundariesOpacity: 0,
        figuresBrightness: 1,
        boundariesBrightness: 1,
      }),
      setSettings: () => {},
      getStateForDebug: () => ({
        enabled: false,
        showFigures: false,
        showBoundaries: false,
      }),
    };
  }

  const state = {
    showFigures: globeCfg.skyDomeFiguresEnabled !== false,
    showBoundaries: globeCfg.skyDomeBoundariesEnabled === true,
    figuresOpacity: clamp01(globeCfg.skyDomeFiguresOpacity ?? 0.72),
    boundariesOpacity: clamp01(globeCfg.skyDomeBoundariesOpacity ?? 0.55),
    figuresBrightness: clampBrightness(globeCfg.skyDomeFiguresBrightness ?? 1.0),
    boundariesBrightness: clampBrightness(globeCfg.skyDomeBoundariesBrightness ?? 1.0),
  };

  const baseTextureUrl = globeCfg.skyDomeBaseTextureUrl;
  const figuresTextureUrl = globeCfg.skyDomeFiguresTextureUrl;
  const boundariesTextureUrl = globeCfg.skyDomeBoundariesTextureUrl;
  const configuredRadiusMeters = Math.max(
    1_000_000,
    Number(globeCfg.skyDomeRadiusMeters) || viewer.scene.globe.ellipsoid.maximumRadius * 1.8
  );
  const minimumCelestialRadiusMeters = Math.max(
    configuredRadiusMeters,
    Number(globeCfg.skyDomeMinCelestialRadiusMeters) || 550_000_000,
    550_000_000
  );
  const autoRadiusPaddingMeters = Math.max(0, Number(globeCfg.skyDomeRadiusPaddingMeters) || 1_000_000);
  const ellipsoidMaxRadius = viewer.scene.globe.ellipsoid.maximumRadius;
  const useIcrfOrientation = globeCfg.skyDomeUseIcrfOrientation !== false;
  const gmstOffsetRad = Cesium.Math.toRadians(Number(globeCfg.skyDomeGmstOffsetDeg) || 0);

  const tiledBase = globeCfg.skyDomeTilesEnabled === false ? null : createSphericalTileLod({
    viewer, inside: true, baseTextureUrl: globeCfg.skyDomeOverviewTextureUrl || "./assets/sky/tiles/overview-4k.webp",
    urlTemplate: globeCfg.skyDomeTilesUrlTemplate || "./assets/sky/tiles/{z}/{x}/{y}.webp",
    maxLevel: globeCfg.skyDomeTilesMaxLevel ?? 3, cacheLimit: globeCfg.skyDomeTileCacheLimit ?? 40,
    maxConcurrent: globeCfg.celestialTileMaxConcurrent ?? 4, requestRender,
  });
  const layers = {
    base: tiledBase ? null : createLayerPrimitive(viewer, { textureUrl: baseTextureUrl, additiveBlend: false }),
    figures: createLayerPrimitive(viewer, { textureUrl: figuresTextureUrl, additiveBlend: true }),
    boundaries: createLayerPrimitive(viewer, { textureUrl: boundariesTextureUrl, additiveBlend: true }),
  };

  const scratchTranslation = new Cesium.Matrix4();
  const scratchRotation3 = new Cesium.Matrix3();
  const scratchRotationOffset3 = new Cesium.Matrix3();
  const scratchFinalRotation3 = new Cesium.Matrix3();
  const scratchRotation4 = new Cesium.Matrix4();
  const scratchScale = new Cesium.Matrix4();
  const scratchModel = new Cesium.Matrix4();
  const scratchModelFinal = new Cesium.Matrix4();
  const scratchOverlayModel = new Cesium.Matrix4();
  const scaleVec = new Cesium.Cartesian3(configuredRadiusMeters, configuredRadiusMeters, configuredRadiusMeters);
  let lastResolvedRadiusMeters = configuredRadiusMeters;
  let lastOrientationMode = "gmst";

  function resolveSkyOrientation(time, outMatrix3) {
    if (useIcrfOrientation) {
      const icrfToFixed = Cesium.Transforms.computeIcrfToFixedMatrix(time, outMatrix3);
      if (Cesium.defined(icrfToFixed)) {
        lastOrientationMode = "icrf";
        return icrfToFixed;
      }
      const temeToFixed = Cesium.Transforms.computeTemeToPseudoFixedMatrix(time, outMatrix3);
      if (Cesium.defined(temeToFixed)) {
        lastOrientationMode = "teme";
        return temeToFixed;
      }
    }
    const gmst = normalizeAngleRad(gmstRadiansFromJulian(time));
    lastOrientationMode = "gmst";
    return Cesium.Matrix3.fromRotationZ(gmst, outMatrix3);
  }

  function applyLayerVisuals() {
    if (layers.base) {
      const baseOpacity = clamp01(globeCfg.skyDomeBaseOpacity ?? 1.0);
      layers.base.primitive.show = baseOpacity > 0.001;
      layers.base.material.uniforms.color = new Cesium.Color(1, 1, 1, baseOpacity);
    }
    if (layers.figures) {
      const show = state.showFigures && state.figuresOpacity > 0.001;
      layers.figures.primitive.show = show;
      const b = state.figuresBrightness;
      layers.figures.material.uniforms.color = new Cesium.Color(b, b, b, state.figuresOpacity);
    }
    if (layers.boundaries) {
      const show = state.showBoundaries && state.boundariesOpacity > 0.001;
      layers.boundaries.primitive.show = show;
      const b = state.boundariesBrightness;
      layers.boundaries.material.uniforms.color = new Cesium.Color(b, b, b, state.boundariesOpacity);
    }
    requestRender?.();
  }

  function updateModelMatrixForTime(time) {
    const camPos = viewer.camera.positionWC;
    const camDistanceFromEarthCenter = Cesium.Cartesian3.magnitude(camPos);
    const requiredRadius =
      camDistanceFromEarthCenter + ellipsoidMaxRadius + autoRadiusPaddingMeters;
    const externalMinimumRadius = Number(getMinimumRadiusForTime?.(time)) || 0;
    const resolvedRadiusMeters = Math.max(minimumCelestialRadiusMeters, requiredRadius);
    const finalRadiusMeters = Math.max(resolvedRadiusMeters, externalMinimumRadius);
    scaleVec.x = finalRadiusMeters;
    scaleVec.y = finalRadiusMeters;
    scaleVec.z = finalRadiusMeters;
    lastResolvedRadiusMeters = finalRadiusMeters;

    const rotation3 = resolveSkyOrientation(time, scratchRotation3);
    const offsetRotation3 = Cesium.Matrix3.fromRotationZ(gmstOffsetRad, scratchRotationOffset3);
    Cesium.Matrix3.multiply(rotation3, offsetRotation3, scratchFinalRotation3);
    const translation = Cesium.Matrix4.fromTranslation(camPos, scratchTranslation);
    const rotation4 = Cesium.Matrix4.fromRotationTranslation(
      scratchFinalRotation3,
      Cesium.Cartesian3.ZERO,
      scratchRotation4
    );
    const scale = Cesium.Matrix4.fromScale(scaleVec, scratchScale);
    const model = Cesium.Matrix4.multiply(translation, rotation4, scratchModel);
    const modelFinal = Cesium.Matrix4.multiply(model, scale, scratchModelFinal);

    if (tiledBase) {
      const opacity = clamp01(globeCfg.skyDomeBaseOpacity ?? 1);
      tiledBase.update({ modelMatrix: modelFinal, show: opacity > 0.001, opacity,
        occluders: [{center: Cesium.Cartesian3.ZERO, radius: ellipsoidMaxRadius}] });
    }
    if (layers.base?.primitive) {
      Cesium.Matrix4.clone(modelFinal, layers.base.primitive.modelMatrix);
    }
    Cesium.Matrix4.multiplyByUniformScale(modelFinal, tiledBase ? 0.990 : 1, scratchOverlayModel);
    if (layers.figures?.primitive) {
      Cesium.Matrix4.clone(scratchOverlayModel, layers.figures.primitive.modelMatrix);
    }
    if (layers.boundaries?.primitive) {
      Cesium.Matrix4.clone(scratchOverlayModel, layers.boundaries.primitive.modelMatrix);
    }
  }

  function onPreRender(scene, time) {
    updateModelMatrixForTime(time);
  }

  viewer.scene.preRender.addEventListener(onPreRender);
  applyLayerVisuals();

  function setSettings(next = {}) {
    if (typeof next.showFigures === "boolean") state.showFigures = next.showFigures;
    if (typeof next.showBoundaries === "boolean") state.showBoundaries = next.showBoundaries;
    if (next.figuresOpacity != null) state.figuresOpacity = clamp01(next.figuresOpacity, state.figuresOpacity);
    if (next.boundariesOpacity != null) state.boundariesOpacity = clamp01(next.boundariesOpacity, state.boundariesOpacity);
    if (next.figuresBrightness != null) {
      state.figuresBrightness = clampBrightness(next.figuresBrightness, state.figuresBrightness);
    }
    if (next.boundariesBrightness != null) {
      state.boundariesBrightness = clampBrightness(next.boundariesBrightness, state.boundariesBrightness);
    }
    applyLayerVisuals();
  }

  function getSettings() {
    return {
      enabled: true,
      showFigures: state.showFigures,
      showBoundaries: state.showBoundaries,
      figuresOpacity: state.figuresOpacity,
      boundariesOpacity: state.boundariesOpacity,
      figuresBrightness: state.figuresBrightness,
      boundariesBrightness: state.boundariesBrightness,
      hasFiguresLayer: Boolean(layers.figures),
      hasBoundariesLayer: Boolean(layers.boundaries),
    };
  }

  return {
    getSettings,
    setSettings,
    destroy: () => {
      viewer.scene.preRender.removeEventListener(onPreRender);
      tiledBase?.destroy();
      for (const layer of Object.values(layers)) if (layer) {
        viewer.scene.primitives.remove(layer.primitive);
        if (!layer.material.isDestroyed()) layer.material.destroy();
      }
    },
    getStateForDebug: () => ({
      enabled: true,
      showFigures: state.showFigures,
      showBoundaries: state.showBoundaries,
      hasBaseLayer: Boolean(tiledBase || layers.base),
      tiles: tiledBase?.getStateForDebug() || null,
      hasFiguresLayer: Boolean(layers.figures),
      hasBoundariesLayer: Boolean(layers.boundaries),
      figuresOpacity: state.figuresOpacity,
      boundariesOpacity: state.boundariesOpacity,
      figuresBrightness: state.figuresBrightness,
      boundariesBrightness: state.boundariesBrightness,
      radiusMeters: lastResolvedRadiusMeters,
      orientationMode: lastOrientationMode,
      usingIcrfOrientation: useIcrfOrientation,
    }),
  };
}
