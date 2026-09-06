/* global Cesium */

const APSIDAL_PERIOD_YEARS = 8.8504;
const PERIOD_DAYS = APSIDAL_PERIOD_YEARS * 365.2422;
const PERIOD_MS = PERIOD_DAYS * 86400000;
const FULLNESS_DEG = 6.0;
const DEFAULT_APSIDAL_REFERENCE_UTC = "2024-06-30T00:00:00Z";
const DEFAULT_SAMPLE_STEP_HOURS = 6;
const DEFAULT_CACHE_MAX_AGE_MS = 60 * 60 * 1000;
const OBLIQUITY_RAD = (23.43929111 * Math.PI) / 180;
const OBLIQUITY_COS = Math.cos(OBLIQUITY_RAD);
const OBLIQUITY_SIN = Math.sin(OBLIQUITY_RAD);

let cachedCycleTrace = null;

function positiveModulo(value, mod) {
  return ((value % mod) + mod) % mod;
}

function wrapToPi(rad) {
  return positiveModulo(rad + Math.PI, Math.PI * 2) - Math.PI;
}

function wrapDeg180(deg) {
  return positiveModulo(deg + 180, 360) - 180;
}

function clampNumber(value, min, max, fallback) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, numeric));
}

function normalizeOptions(options = {}) {
  const referenceDateMs = Date.parse(options.referenceDateUtc || DEFAULT_APSIDAL_REFERENCE_UTC);
  const safeReferenceDateMs = Number.isFinite(referenceDateMs)
    ? referenceDateMs
    : Date.parse(DEFAULT_APSIDAL_REFERENCE_UTC);
  const sampleStepHours = clampNumber(options.sampleStepHours, 1, 24, DEFAULT_SAMPLE_STEP_HOURS);
  const cacheMaxAgeMs = clampNumber(options.cacheMaxAgeMs, 60000, 24 * 60 * 60 * 1000, DEFAULT_CACHE_MAX_AGE_MS);
  const fullnessDeg = clampNumber(options.fullnessDeg, 0.5, 25, FULLNESS_DEG);

  return {
    referenceDateMs: safeReferenceDateMs,
    sampleStepHours,
    cacheMaxAgeMs,
    fullnessDeg,
  };
}

function getCycleIndex(nowMs, referenceDateMs) {
  return Math.floor((nowMs - referenceDateMs) / PERIOD_MS);
}

function buildCycleBounds(cycleIndex, referenceDateMs) {
  const cycleStartMs = referenceDateMs + cycleIndex * PERIOD_MS;
  return {
    cycleStartMs,
    cycleEndMs: cycleStartMs + PERIOD_MS,
  };
}

function eclipticLongitude(cartesian) {
  const y = cartesian.y * OBLIQUITY_COS + cartesian.z * OBLIQUITY_SIN;
  return Math.atan2(y, cartesian.x);
}

function buildCycleTrace(cycleStartMs, sampleStepHours, fullnessDeg) {
  if (typeof Cesium === "undefined" || !Cesium.Simon1994PlanetaryPositions) {
    return null;
  }

  const stepMs = sampleStepHours * 60 * 60 * 1000;
  const sampleCount = Math.max(2, Math.ceil(PERIOD_MS / stepMs) + 1);
  const cycleStartDate = new Date(cycleStartMs);
  const cycleStartJd = Cesium.JulianDate.fromDate(cycleStartDate);
  const stepDays = sampleStepHours / 24;
  const scratchMoon = new Cesium.Cartesian3();
  const scratchSun = new Cesium.Cartesian3();
  const distances = new Float64Array(sampleCount);
  const fullMask = new Uint8Array(sampleCount);
  const minMax = {
    minDistance: Number.POSITIVE_INFINITY,
    maxDistance: Number.NEGATIVE_INFINITY,
  };

  for (let i = 0; i < sampleCount; i += 1) {
    const sampleJd = Cesium.JulianDate.addDays(cycleStartJd, stepDays * i, new Cesium.JulianDate());
    const moon = Cesium.Simon1994PlanetaryPositions.computeMoonPositionInEarthInertialFrame(sampleJd, scratchMoon);
    const sun = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(sampleJd, scratchSun);

    const distanceKm = Cesium.Cartesian3.magnitude(moon) / 1000;
    distances[i] = distanceKm;
    minMax.minDistance = Math.min(minMax.minDistance, distanceKm);
    minMax.maxDistance = Math.max(minMax.maxDistance, distanceKm);

    const deltaLambdaDeg = (wrapToPi(eclipticLongitude(moon) - eclipticLongitude(sun)) * 180) / Math.PI;
    const fullnessErrorDeg = Math.abs(wrapDeg180(deltaLambdaDeg + 180));
    fullMask[i] = fullnessErrorDeg <= fullnessDeg ? 1 : 0;
  }

  const extrema = [];
  for (let i = 1; i < sampleCount - 1; i += 1) {
    const prev = distances[i - 1];
    const current = distances[i];
    const next = distances[i + 1];
    if (current < prev && current < next) {
      extrema.push({
        type: "perigee",
        timeMs: cycleStartMs + i * stepMs,
        distanceKm: current,
      });
    } else if (current > prev && current > next) {
      extrema.push({
        type: "apogee",
        timeMs: cycleStartMs + i * stepMs,
        distanceKm: current,
      });
    }
  }

  return {
    cycleStartMs,
    cycleEndMs: cycleStartMs + PERIOD_MS,
    sampleStepHours,
    stepMs,
    distances,
    fullMask,
    extrema,
    minDistance: minMax.minDistance,
    maxDistance: minMax.maxDistance,
    generatedAtMs: Date.now(),
  };
}

function getCycleTrace(nowDate = new Date(), options = {}) {
  const normalized = normalizeOptions(options);
  const nowMs = nowDate.getTime();
  const cycleIndex = getCycleIndex(nowMs, normalized.referenceDateMs);
  const { cycleStartMs } = buildCycleBounds(cycleIndex, normalized.referenceDateMs);
  const cacheKey = [cycleIndex, normalized.referenceDateMs, normalized.sampleStepHours, normalized.fullnessDeg].join(":");

  const cacheIsFresh =
    cachedCycleTrace &&
    cachedCycleTrace.key === cacheKey &&
    Date.now() - cachedCycleTrace.generatedAtMs <= normalized.cacheMaxAgeMs;

  if (cacheIsFresh) {
    return cachedCycleTrace.data;
  }

  const data = buildCycleTrace(cycleStartMs, normalized.sampleStepHours, normalized.fullnessDeg);
  if (!data) return null;

  cachedCycleTrace = {
    key: cacheKey,
    generatedAtMs: Date.now(),
    data,
  };

  return data;
}

function findNearestEvent(extrema, eventType, nowMs) {
  let nearest = null;
  for (const event of extrema) {
    if (event.type !== eventType) continue;
    const deltaMs = Math.abs(event.timeMs - nowMs);
    if (!nearest || deltaMs < nearest.deltaMs) {
      nearest = {
        ...event,
        deltaMs,
      };
    }
  }
  return nearest;
}

function getCurrentDistanceKm(nowDate = new Date()) {
  if (typeof Cesium === "undefined" || !Cesium.Simon1994PlanetaryPositions) {
    return Number.NaN;
  }
  const nowJd = Cesium.JulianDate.fromDate(nowDate);
  const moon = Cesium.Simon1994PlanetaryPositions.computeMoonPositionInEarthInertialFrame(
    nowJd,
    new Cesium.Cartesian3()
  );
  return Cesium.Cartesian3.magnitude(moon) / 1000;
}

function createRuntimeSnapshot(nowDate = new Date(), options = {}) {
  const trace = getCycleTrace(nowDate, options);
  if (!trace) return null;

  const nowMs = nowDate.getTime();
  const phaseMs = positiveModulo(nowMs - trace.cycleStartMs, PERIOD_MS);
  return {
    ...trace,
    currentDistanceKm: getCurrentDistanceKm(nowDate),
    phaseYears: (phaseMs / PERIOD_MS) * APSIDAL_PERIOD_YEARS,
    phasePct: (phaseMs / PERIOD_MS) * 100,
    nearestPerigee: findNearestEvent(trace.extrema, "perigee", nowMs),
    nearestApogee: findNearestEvent(trace.extrema, "apogee", nowMs),
  };
}

function drawGrid(ctx, left, top, width, height) {
  ctx.save();
  ctx.strokeStyle = "rgba(255,255,255,0.07)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = top + (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(left + width, y);
    ctx.stroke();
  }
  for (let i = 0; i <= 6; i += 1) {
    const x = left + (width / 6) * i;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, top + height);
    ctx.stroke();
  }
  ctx.restore();
}

function drawSeries(ctx, values, left, top, width, height, color, minValue, maxValue, mask = null) {
  const range = Math.max(1, maxValue - minValue);
  ctx.beginPath();
  let hasActiveSegment = false;

  for (let i = 0; i < values.length; i += 1) {
    if (mask && !mask[i]) {
      hasActiveSegment = false;
      continue;
    }
    const x = left + (i / (values.length - 1)) * width;
    const y = top + height - ((values[i] - minValue) / range) * height;
    if (!hasActiveSegment) {
      ctx.moveTo(x, y);
      hasActiveSegment = true;
    } else {
      ctx.lineTo(x, y);
    }
  }

  ctx.strokeStyle = color;
  ctx.stroke();
}

function formatShortUtc(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date);
}

export function renderMoonApsidalPlot(canvas, statsRoot, nowDate = new Date(), options = {}) {
  if (!canvas || !statsRoot) return;

  const data = createRuntimeSnapshot(nowDate, options);
  if (!data) {
    statsRoot.innerHTML = `<div class="moon-plot-stat">Cesium ephemeris unavailable.</div>`;
    return;
  }

  const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1;
  const cssWidth = canvas.clientWidth || 320;
  const cssHeight = canvas.clientHeight || 220;
  canvas.width = Math.round(cssWidth * dpr);
  canvas.height = Math.round(cssHeight * dpr);

  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssWidth, cssHeight);

  ctx.fillStyle = "#04070d";
  ctx.fillRect(0, 0, cssWidth, cssHeight);

  const left = 34;
  const top = 18;
  const width = Math.max(10, cssWidth - 52);
  const height = Math.max(10, cssHeight - 50);
  const minDistance = data.minDistance - 4000;
  const maxDistance = data.maxDistance + 4000;

  drawGrid(ctx, left, top, width, height);

  ctx.lineWidth = 1.25;
  drawSeries(ctx, data.distances, left, top, width, height, "rgba(142, 199, 255, 0.9)", minDistance, maxDistance);

  ctx.lineWidth = 2.2;
  drawSeries(
    ctx,
    data.distances,
    left,
    top,
    width,
    height,
    "rgba(255, 230, 0, 0.96)",
    minDistance,
    maxDistance,
    data.fullMask
  );

  const currentX = left + (data.phaseYears / APSIDAL_PERIOD_YEARS) * width;
  const currentY =
    top + height - ((data.currentDistanceKm - minDistance) / Math.max(1, maxDistance - minDistance)) * height;
  ctx.beginPath();
  ctx.fillStyle = "#ff5f57";
  ctx.arc(currentX, currentY, 4.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.7)";
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = "rgba(233, 238, 247, 0.86)";
  ctx.font = '12px "IBM Plex Mono", monospace';
  ctx.fillText("0y", left - 4, cssHeight - 12);
  ctx.fillText(`${APSIDAL_PERIOD_YEARS.toFixed(2)}y`, left + width - 42, cssHeight - 12);
  ctx.fillStyle = "rgba(150, 164, 186, 0.88)";
  ctx.fillText(`${Math.round(maxDistance).toLocaleString()} km`, left, 12);
  ctx.fillText(`${Math.round(minDistance).toLocaleString()} km`, left, top + height + 14);

  statsRoot.innerHTML = `
    <div class="moon-plot-stat"><span>Current</span><strong>${Math.round(data.currentDistanceKm).toLocaleString()} km</strong></div>
    <div class="moon-plot-stat"><span>Cycle phase</span><strong>${data.phasePct.toFixed(1)}%</strong></div>
    <div class="moon-plot-stat"><span>Nearest perigee</span><strong>${
      data.nearestPerigee ? formatShortUtc(data.nearestPerigee.timeMs) : "--"
    }</strong></div>
    <div class="moon-plot-stat"><span>Nearest apogee</span><strong>${
      data.nearestApogee ? formatShortUtc(data.nearestApogee.timeMs) : "--"
    }</strong></div>
    <div class="moon-plot-stat"><span>Curve step</span><strong>${data.sampleStepHours}h cached</strong></div>
    <div class="moon-plot-stat"><span>Cycle window</span><strong>${formatShortUtc(data.cycleStartMs)} to ${formatShortUtc(
      data.cycleEndMs
    )}</strong></div>
  `;
}
