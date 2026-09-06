/* global Cesium */

const SYNODIC_MONTH_DAYS = 29.530588853;
const DEFAULT_COARSE_STEP_HOURS = 6;
const DEFAULT_REFINE_STEP_MINUTES = 10;
const DEFAULT_CACHE_MAX_AGE_MS = 60 * 60 * 1000;
const DEFAULT_SEARCH_LUNATIONS = 18;
const EARTH_RADIUS_KM = 6378.137;
const MOON_RADIUS_KM = 1737.4;
const SUN_RADIUS_KM = 695700;
const OBLIQUITY_RAD = (23.43929111 * Math.PI) / 180;
const OBLIQUITY_COS = Math.cos(OBLIQUITY_RAD);
const OBLIQUITY_SIN = Math.sin(OBLIQUITY_RAD);

let cachedForecast = null;

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
  return {
    coarseStepHours: clampNumber(options.coarseStepHours, 1, 24, DEFAULT_COARSE_STEP_HOURS),
    refineStepMinutes: clampNumber(options.refineStepMinutes, 1, 60, DEFAULT_REFINE_STEP_MINUTES),
    cacheMaxAgeMs: clampNumber(options.cacheMaxAgeMs, 60000, 24 * 60 * 60 * 1000, DEFAULT_CACHE_MAX_AGE_MS),
    searchLunations: clampNumber(options.searchLunations, 6, 36, DEFAULT_SEARCH_LUNATIONS),
  };
}

function getVectorsAt(date) {
  const jd = Cesium.JulianDate.fromDate(date);
  const moon = Cesium.Simon1994PlanetaryPositions.computeMoonPositionInEarthInertialFrame(jd, new Cesium.Cartesian3());
  const sun = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(jd, new Cesium.Cartesian3());
  return { moon, sun };
}

function eclipticLongitude(cartesian) {
  const y = cartesian.y * OBLIQUITY_COS + cartesian.z * OBLIQUITY_SIN;
  return Math.atan2(y, cartesian.x);
}

function fullMoonErrorDegAt(date) {
  const { moon, sun } = getVectorsAt(date);
  const deltaLambdaDeg = (wrapToPi(eclipticLongitude(moon) - eclipticLongitude(sun)) * 180) / Math.PI;
  return Math.abs(wrapDeg180(deltaLambdaDeg + 180));
}

function moonDistanceKmAt(date) {
  const { moon } = getVectorsAt(date);
  return Cesium.Cartesian3.magnitude(moon) / 1000;
}

function classifyLunarEclipseAt(date) {
  const { moon, sun } = getVectorsAt(date);
  const sunDistanceKm = Cesium.Cartesian3.magnitude(sun) / 1000;
  const moonDistanceKm = Cesium.Cartesian3.magnitude(moon) / 1000;
  const antiSunDir = Cesium.Cartesian3.normalize(Cesium.Cartesian3.negate(sun, new Cesium.Cartesian3()), new Cesium.Cartesian3());
  const projectionKm = Cesium.Cartesian3.dot(
    Cesium.Cartesian3.divideByScalar(moon, 1000, new Cesium.Cartesian3()),
    antiSunDir
  );
  if (projectionKm <= 0) return null;

  const moonKm = Cesium.Cartesian3.divideByScalar(moon, 1000, new Cesium.Cartesian3());
  const axisPoint = Cesium.Cartesian3.multiplyByScalar(antiSunDir, projectionKm, new Cesium.Cartesian3());
  const offsetKm = Cesium.Cartesian3.distance(moonKm, axisPoint);

  const umbraLengthKm = (EARTH_RADIUS_KM * sunDistanceKm) / Math.max(1, SUN_RADIUS_KM - EARTH_RADIUS_KM);
  const umbraRadiusKm = Math.max(0, EARTH_RADIUS_KM * (1 - projectionKm / umbraLengthKm));
  const penumbraRadiusKm = EARTH_RADIUS_KM + (projectionKm * (SUN_RADIUS_KM + EARTH_RADIUS_KM)) / sunDistanceKm;

  if (offsetKm > penumbraRadiusKm + MOON_RADIUS_KM) {
    return null;
  }
  if (offsetKm + MOON_RADIUS_KM <= umbraRadiusKm) {
    return {
      type: offsetKm <= umbraRadiusKm * 0.35 ? "Central total" : "Total",
      offsetKm,
      moonDistanceKm,
    };
  }
  if (offsetKm < umbraRadiusKm + MOON_RADIUS_KM) {
    return {
      type: "Partial",
      offsetKm,
      moonDistanceKm,
    };
  }
  if (offsetKm + MOON_RADIUS_KM <= penumbraRadiusKm) {
    return {
      type: "Total penumbral",
      offsetKm,
      moonDistanceKm,
    };
  }
  return {
    type: "Penumbral",
    offsetKm,
    moonDistanceKm,
  };
}

function refineFullMoonTime(approxDate, refineStepMinutes) {
  const halfWindowMs = 10 * 60 * 60 * 1000;
  const stepMs = refineStepMinutes * 60 * 1000;
  let bestDate = approxDate;
  let bestError = Number.POSITIVE_INFINITY;

  for (let offsetMs = -halfWindowMs; offsetMs <= halfWindowMs; offsetMs += stepMs) {
    const candidate = new Date(approxDate.getTime() + offsetMs);
    const error = fullMoonErrorDegAt(candidate);
    if (error < bestError) {
      bestError = error;
      bestDate = candidate;
    }
  }

  return {
    date: bestDate,
    errorDeg: bestError,
  };
}

function findUpcomingFullMoons(nowDate, options) {
  const coarseStepMs = options.coarseStepHours * 60 * 60 * 1000;
  const searchMs = options.searchLunations * SYNODIC_MONTH_DAYS * 24 * 60 * 60 * 1000;
  const events = [];
  let previousDate = new Date(nowDate.getTime());
  let previousError = fullMoonErrorDegAt(previousDate);
  let currentDate = new Date(previousDate.getTime() + coarseStepMs);
  let currentError = fullMoonErrorDegAt(currentDate);
  let lastAcceptedMs = -Infinity;

  while (currentDate.getTime() <= nowDate.getTime() + searchMs) {
    const nextDate = new Date(currentDate.getTime() + coarseStepMs);
    const nextError = fullMoonErrorDegAt(nextDate);

    if (currentError <= previousError && currentError <= nextError && currentError <= 18) {
      const refined = refineFullMoonTime(currentDate, options.refineStepMinutes);
      if (refined.date.getTime() - lastAcceptedMs > 20 * 24 * 60 * 60 * 1000) {
        const eclipse = classifyLunarEclipseAt(refined.date);
        events.push({
          date: refined.date,
          timeMs: refined.date.getTime(),
          errorDeg: refined.errorDeg,
          distanceKm: moonDistanceKmAt(refined.date),
          eclipse,
        });
        lastAcceptedMs = refined.date.getTime();
      }
    }

    previousDate = currentDate;
    previousError = currentError;
    currentDate = nextDate;
    currentError = nextError;
  }

  return events;
}

function buildForecast(nowDate, options) {
  if (typeof Cesium === "undefined" || !Cesium.Simon1994PlanetaryPositions) {
    return null;
  }

  const fullMoons = findUpcomingFullMoons(nowDate, options);
  if (!fullMoons.length) return null;

  const comparisonWindow = fullMoons.slice(0, Math.min(fullMoons.length, 18));
  let nextPerigeeFullMoon = comparisonWindow[0];
  let nextApogeeFullMoon = comparisonWindow[0];
  for (const event of comparisonWindow) {
    if (event.distanceKm < nextPerigeeFullMoon.distanceKm) {
      nextPerigeeFullMoon = event;
    }
    if (event.distanceKm > nextApogeeFullMoon.distanceKm) {
      nextApogeeFullMoon = event;
    }
  }

  const nextLunarEclipse = fullMoons.find((event) => event.eclipse) || null;

  return {
    generatedAtMs: Date.now(),
    nextPerigeeFullMoon,
    nextApogeeFullMoon,
    nextLunarEclipse,
  };
}

function getForecast(nowDate = new Date(), options = {}) {
  const normalized = normalizeOptions(options);
  const cacheKey = `${normalized.coarseStepHours}:${normalized.refineStepMinutes}:${normalized.searchLunations}`;
  const isFresh =
    cachedForecast &&
    cachedForecast.key === cacheKey &&
    Date.now() - cachedForecast.generatedAtMs <= normalized.cacheMaxAgeMs;
  if (isFresh) {
    return cachedForecast.data;
  }

  const data = buildForecast(nowDate, normalized);
  if (!data) return null;
  cachedForecast = {
    key: cacheKey,
    generatedAtMs: Date.now(),
    data,
  };
  return data;
}

function formatCountdown(ms) {
  if (!Number.isFinite(ms)) return "--";
  if (ms <= 0) return "Now";
  const totalSeconds = Math.floor(ms / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  return `${minutes}m ${seconds}s`;
}

function formatShortUtc(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date);
}

function renderTimerCard(title, event, nowDate, secondary) {
  if (!event?.date) {
    return `
      <div class="moon-timer-card">
        <div class="moon-timer-label">${title}</div>
        <div class="moon-timer-countdown">Unavailable</div>
        <div class="moon-timer-subtle">No event found in current search window.</div>
      </div>
    `;
  }

  const countdown = formatCountdown(event.date.getTime() - nowDate.getTime());
  return `
    <div class="moon-timer-card">
      <div class="moon-timer-label">${title}</div>
      <div class="moon-timer-countdown">${countdown}</div>
      <div class="moon-timer-subtle">${formatShortUtc(event.date)} UTC</div>
      <div class="moon-timer-meta">${secondary}</div>
    </div>
  `;
}

export function renderMoonTimers(root, nowDate = new Date(), options = {}) {
  if (!root) return;
  const forecast = getForecast(nowDate, options);
  if (!forecast) {
    root.innerHTML = `<div class="moon-timer-card"><div class="moon-timer-countdown">Cesium ephemeris unavailable.</div></div>`;
    return;
  }

  const perigeeEvent = forecast.nextPerigeeFullMoon
    ? {
        date: new Date(forecast.nextPerigeeFullMoon.timeMs),
      }
    : null;
  const apogeeEvent = forecast.nextApogeeFullMoon
    ? {
        date: new Date(forecast.nextApogeeFullMoon.timeMs),
      }
    : null;
  const eclipseEvent = forecast.nextLunarEclipse
    ? {
        date: new Date(forecast.nextLunarEclipse.timeMs),
      }
    : null;

  root.innerHTML =
    renderTimerCard(
      "Next full moon at perigee",
      perigeeEvent,
      nowDate,
      forecast.nextPerigeeFullMoon
        ? `${Math.round(forecast.nextPerigeeFullMoon.distanceKm).toLocaleString()} km Earth-Moon distance`
        : "—"
    ) +
    renderTimerCard(
      "Next full moon at apogee",
      apogeeEvent,
      nowDate,
      forecast.nextApogeeFullMoon
        ? `${Math.round(forecast.nextApogeeFullMoon.distanceKm).toLocaleString()} km Earth-Moon distance`
        : "—"
    ) +
    renderTimerCard(
      "Next lunar eclipse",
      eclipseEvent,
      nowDate,
      forecast.nextLunarEclipse?.eclipse?.type || "No eclipse found in current forecast window"
    );
}
