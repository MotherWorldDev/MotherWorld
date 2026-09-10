const DEFAULT_DAY_ONLY_ALTITUDE_RADII = 0.5;
const DEFAULT_FULL_SHADOW_ALTITUDE_RADII = 2;
const DEFAULT_AMBIENT = 0.025;

function finitePositive(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function smoothstep(edge0, edge1, value) {
  const t = Math.max(0, Math.min(1, (value - edge0) / Math.max(1e-9, edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** Pure close-up lunar shadow policy. Distances and radius use the same units. */
export function getMoonShadowPolicy({ cameraMoonDistance, displayRadius, config = {} } = {}) {
  const dayOnly = finitePositive(config.moonLightingDayOnlyAltitudeRadii, DEFAULT_DAY_ONLY_ALTITUDE_RADII);
  const fullShadow = Math.max(
    dayOnly,
    finitePositive(config.moonLightingFullShadowAltitudeRadii, DEFAULT_FULL_SHADOW_ALTITUDE_RADII)
  );
  const radius = Number(displayRadius);
  const distance = Number(cameraMoonDistance);
  const altitudeRadii = Number.isFinite(radius) && radius > 0 && Number.isFinite(distance)
    ? (distance - radius) / radius
    : Number.POSITIVE_INFINITY;
  const fade = smoothstep(dayOnly, fullShadow, altitudeRadii);
  const onlySunLighting = config.moonOnlySunLighting !== false;
  return {
    altitudeRadii,
    dayOnlyAltitudeRadii: dayOnly,
    fullShadowAltitudeRadii: fullShadow,
    shadowFade: onlySunLighting ? fade : 0,
    ambient: onlySunLighting ? (1 - 0.975 * fade) : 1,
    onlySunLighting,
  };
}

/** Select the source descriptor consumed by sphericalTileLod. */
export function chooseMoonSource({ mode = "natural", eclipse = false, anchorMode = "earth", config = {}, custom = {} } = {}) {
  const naturalOverview = config.moonTextureOverviewUrl || "./assets/moon/tiles/natural/overview.webp";
  const naturalTemplate = config.moonTextureTileUrl || "./assets/moon/tiles/natural/{z}/{x}/{y}.webp";
  const eclipseOverview = config.moonEclipseTextureOverviewUrl || "./assets/moon/tiles/eclipse/overview.webp";
  const eclipseTemplate = config.moonEclipseTextureTileUrl || "./assets/moon/tiles/eclipse/{z}/{x}/{y}.webp";
  const configuredWhole = anchorMode === "moon"
    ? (custom.hiRes || custom.base || config.moonGeologyTextureHiResUrl || config.moonGeologyTextureUrl)
    : (custom.base || config.moonGeologyTextureUrl);
  const isBuiltInWhole = typeof configuredWhole === "string" && /^(?:\.\/)?assets\/moon\/selenology\/moon-geology-(?:4k|8k)\.webp$/i.test(configuredWhole);
  const customWhole = isBuiltInWhole ? null : configuredWhole;
  const geologyOverview = customWhole || "./assets/moon/tiles/geology/overview.webp";
  const geologyTemplate = custom.tileTemplate || config.moonGeologyTextureTileUrl || (customWhole ? null : "./assets/moon/tiles/geology/{z}/{x}/{y}.webp");
  if (mode === "geology") {
    return { key: `geology:${geologyOverview}`, baseTextureUrl: geologyOverview, urlTemplate: geologyTemplate, maxLevel: geologyTemplate ? 3 : 0 };
  }
  if (eclipse) {
    return { key: `eclipse:${eclipseOverview}`, baseTextureUrl: eclipseOverview, urlTemplate: eclipseTemplate, maxLevel: 2 };
  }
  return { key: `natural:${naturalOverview}`, baseTextureUrl: naturalOverview, urlTemplate: naturalTemplate, maxLevel: 2 };
}

export const MOON_LOD_DEFAULTS = Object.freeze({
  dayOnlyAltitudeRadii: DEFAULT_DAY_ONLY_ALTITUDE_RADII,
  fullShadowAltitudeRadii: DEFAULT_FULL_SHADOW_ALTITUDE_RADII,
  ambientAtFullShadow: DEFAULT_AMBIENT,
});
