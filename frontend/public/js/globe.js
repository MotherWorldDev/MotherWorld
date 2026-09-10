/* global Cesium */
import { createSkyDomeController } from "./skyDome.js?v=20260910-celestial1";
import { createSphericalTileLod } from "./sphericalTileLod.js?v=20260910-celestial1";
import { chooseMoonSource, getMoonShadowPolicy } from "./moonLodPolicy.js?v=20260910-celestial1";

function colorFromCss(css) {
  return Cesium.Color.fromCssColorString(css);
}

function colorVec3FromCss(css) {
  const color = colorFromCss(css);
  return new Cesium.Cartesian3(color.red, color.green, color.blue);
}

function colorWithAlpha(color, alpha) {
  const out = Cesium.Color.clone(color);
  out.alpha = alpha;
  return out;
}

function brighten(color, amount) {
  const out = Cesium.Color.clone(color);
  out.brighten(amount, out);
  return out;
}

function blend(a, b, t) {
  return Cesium.Color.lerp(a, b, Cesium.Math.clamp(t, 0, 1), new Cesium.Color());
}

function stringHash(value) {
  const text = String(value || "");
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) {
    hash = (hash * 31 + text.charCodeAt(i)) | 0;
  }
  return hash;
}

function getMarineRegionBaseColor(regionMeta) {
  const low = colorFromCss("#0f766e");
  const high = colorFromCss("#5eead4");
  const mix = (Math.abs(stringHash(regionMeta?.realm || regionMeta?.province || "")) % 1000) / 1000;
  return blend(low, high, 0.18 + mix * 0.42);
}

function getLakeRegionBaseColor(regionMeta) {
  const low = colorFromCss("#0f4c81");
  const high = colorFromCss("#38bdf8");
  const mix = (Math.abs(stringHash(regionMeta?.name || regionMeta?.country || "")) % 1000) / 1000;
  return blend(low, high, 0.22 + mix * 0.38);
}

function getEntityRegionId(entity) {
  const now = Cesium.JulianDate.now();
  const regionIdProp = entity?.properties?.regionId;
  if (regionIdProp && typeof regionIdProp.getValue === "function") {
    const value = regionIdProp.getValue(now);
    if (value != null && value !== "") return value;
  }
  const idProp = entity?.properties?.id;
  if (idProp && typeof idProp.getValue === "function") {
    const value = idProp.getValue(now);
    if (value != null && value !== "") return value;
  }
  const fidProp = entity?.properties?.FID;
  if (fidProp && typeof fidProp.getValue === "function") {
    const value = fidProp.getValue(now);
    if (value != null && value !== "") return value;
  }
  if (entity?._regionId) {
    return entity._regionId;
  }
  if (typeof entity?.id === "string" && entity.id) {
    return entity.id;
  }
  return null;
}

function getEntityRealmSlug(entity) {
  const now = Cesium.JulianDate.now();
  const prop = entity?.properties?.realmSlug;
  if (prop && typeof prop.getValue === "function") {
    return prop.getValue(now);
  }
  return entity?._realmSlug || null;
}

function polygonHierarchyTouchesDateline(hierarchy, thresholdDeg = 0.25) {
  if (!hierarchy?.positions?.length) return false;
  for (const position of hierarchy.positions) {
    const cartographic = Cesium.Cartographic.fromCartesian(position);
    if (!cartographic) continue;
    const lonDeg = Cesium.Math.toDegrees(cartographic.longitude);
    if (Math.abs(Math.abs(lonDeg) - 180.0) <= thresholdDeg) {
      return true;
    }
  }
  if (Array.isArray(hierarchy.holes)) {
    for (const hole of hierarchy.holes) {
      if (polygonHierarchyTouchesDateline(hole, thresholdDeg)) {
        return true;
      }
    }
  }
  return false;
}

function collectHierarchyPositions(hierarchy, out = []) {
  if (!hierarchy) return out;
  if (Array.isArray(hierarchy.positions)) {
    for (const position of hierarchy.positions) {
      if (position) out.push(position);
    }
  }
  if (Array.isArray(hierarchy.holes)) {
    for (const hole of hierarchy.holes) {
      collectHierarchyPositions(hole, out);
    }
  }
  return out;
}

function getEntityPropertyValue(entity, key, time = Cesium.JulianDate.now()) {
  const prop = entity?.properties?.[key];
  if (prop && typeof prop.getValue === "function") {
    return prop.getValue(time);
  }
  return undefined;
}

function normalizeLongitudeNear(reference, value) {
  let out = value;
  while (out - reference > Math.PI) out -= Cesium.Math.TWO_PI;
  while (out - reference < -Math.PI) out += Cesium.Math.TWO_PI;
  return out;
}

function pointInRingRadians(pointLon, pointLat, positions) {
  if (!Array.isArray(positions) || positions.length < 3) return false;
  const points = positions.map((position) => Cesium.Cartographic.fromCartesian(position));
  if (points.some((point) => !point)) return false;
  // Unwrap the ring continuously, not around the click: otherwise a narrow
  // dateline polygon can appear to contain a point on the opposite meridian.
  let previousLon = points[0].longitude;
  const ring = points.map((point) => {
    const longitude = normalizeLongitudeNear(previousLon, point.longitude);
    previousLon = longitude;
    return { longitude, latitude: point.latitude };
  });
  const localLon = normalizeLongitudeNear(ring[0].longitude, pointLon);
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const a = ring[i], b = ring[j];
    const intersects =
      (a.latitude > pointLat) !== (b.latitude > pointLat) &&
      localLon < ((b.longitude - a.longitude) * (pointLat - a.latitude)) /
        ((b.latitude - a.latitude) || 1e-12) + a.longitude;
    if (intersects) inside = !inside;
  }
  return inside;
}

function polygonHierarchyContainsPoint(hierarchy, pointLon, pointLat) {
  if (!hierarchy?.positions?.length) return false;
  const insideOuter = pointInRingRadians(pointLon, pointLat, hierarchy.positions);
  if (!insideOuter) return false;
  for (const hole of hierarchy.holes || []) {
    if (polygonHierarchyContainsPoint(hole, pointLon, pointLat)) {
      return false;
    }
  }
  return true;
}

function pickManagedEntity(viewer, screenPosition, baseDataSource, detailDataSource, marineDataSource, lakesDataSource, singlePick = false) {
  let detailEntity = null;
  let baseEntity = null;
  let marineEntity = null;
  let lakesEntity = null;

  const considerEntity = (rawEntity) => {
    if (!rawEntity) return;
    const entity =
      rawEntity?._selectionOverlay && rawEntity?._sourceEntity ? rawEntity._sourceEntity : rawEntity;
    if (!detailEntity && detailDataSource?.entities?.contains(entity)) {
      detailEntity = entity;
      return;
    }
    if (!baseEntity && baseDataSource?.entities?.contains(entity)) {
      baseEntity = entity;
      return;
    }
    if (!marineEntity && marineDataSource?.entities?.contains(entity)) {
      marineEntity = entity;
      return;
    }
    if (!lakesEntity && lakesDataSource?.entities?.contains(entity)) {
      lakesEntity = entity;
    }
  };

  const picks = singlePick
    ? [viewer.scene.pick(screenPosition)]
    : viewer.scene.drillPick?.(screenPosition, 128) || [];
  if (picks.length) {
    for (const picked of picks) {
      if (!picked?.id) continue;
      considerEntity(picked.id);
      if (detailEntity && baseEntity && marineEntity && lakesEntity) break;
    }
  } else {
    const picked = viewer.scene.pick(screenPosition);
    if (Cesium.defined(picked) && picked.id) {
      considerEntity(picked.id);
    }
  }

  return detailEntity || baseEntity || marineEntity || lakesEntity || null;
}

function makeDefaultDebugOptions() {
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

const MOON_SELECTION_ID = "moon";
const OPEN_OCEAN_SELECTION_ID = "open_ocean";
const moonOrientationAxes = new Cesium.IauOrientationAxes();
const moonIcrfToFixedScratch = new Cesium.Matrix3();
const moonRotationScratch = new Cesium.Matrix3();
const moonTranslationScratch = new Cesium.Cartesian3();
const moonModelMatrixScratch = new Cesium.Matrix4();
const moonOrientationMatrixScratch = new Cesium.Matrix3();
const moonInverseMatrixScratch = new Cesium.Matrix4();
const moonLocalRayOriginScratch = new Cesium.Cartesian3();
const moonLocalRayDirectionScratch = new Cesium.Cartesian3();
const moonWorldPositionScratch = new Cesium.Cartesian3();
const moonQuaternionScratch = new Cesium.Quaternion();
const moonSunInertialScratch = new Cesium.Cartesian3();
const moonSunFixedScratch = new Cesium.Cartesian3();
const moonSunDirScratch = new Cesium.Cartesian3();
const moonCameraDestinationScratch = new Cesium.Cartesian3();
const moonCameraDirectionScratch = new Cesium.Cartesian3();
const moonCameraRightScratch = new Cesium.Cartesian3();
const moonCameraUpScratch = new Cesium.Cartesian3();
const moonFallbackUpScratch = new Cesium.Cartesian3();
const moonScaleVectorScratch = new Cesium.Cartesian3();
const moonScaleMatrixScratch = new Cesium.Matrix4();
const moonScaledModelMatrixScratch = new Cesium.Matrix4();
const moonSunDirectionEyeScratch = new Cesium.Cartesian3();
const moonTextureOffsetRotationScratch = new Cesium.Matrix3();
const moonSkyDomePositionScratch = new Cesium.Cartesian3();
const sunScreenPositionScratch = new Cesium.Cartesian3();
const earthTagAnchorScratch = new Cesium.Cartesian3();
const earthTagDirectionScratch = new Cesium.Cartesian3();
const moonTagAnchorScratch = new Cesium.Cartesian3();
const moonTagDirectionScratch = new Cesium.Cartesian3();
const moonAnchorTransformScratch = new Cesium.Matrix4();
const screenPositionScratch = new Cesium.Cartesian2();
const moonAnchorLocalPositionScratch = new Cesium.Cartesian3();
const moonAnchorLocalDirectionScratch = new Cesium.Cartesian3();
const moonAnchorLocalUpScratch = new Cesium.Cartesian3();
const moonAnchorLocalRightScratch = new Cesium.Cartesian3();
const nightUltraStableDirectionScratch = new Cesium.Cartesian3();
let moonPrimeMeridianOffsetRad = Math.PI;
const EARTH_RADIUS_KM = 6378.137;
const MOON_RADIUS_KM = 1737.4;
const SUN_RADIUS_KM = 695700;

function computeMoonModelMatrix(time, result = new Cesium.Matrix4()) {
  if (!Cesium.Transforms.computeIcrfToFixedMatrix(time, moonIcrfToFixedScratch)) {
    Cesium.Transforms.computeTemeToPseudoFixedMatrix(time, moonIcrfToFixedScratch);
  }

  const rotation = moonOrientationAxes.evaluate(time, moonRotationScratch);
  Cesium.Matrix3.transpose(rotation, rotation);
  Cesium.Matrix3.multiply(moonIcrfToFixedScratch, rotation, rotation);
  if (Math.abs(moonPrimeMeridianOffsetRad) > 1e-8) {
    const textureOffsetRotation = Cesium.Matrix3.fromRotationZ(
      moonPrimeMeridianOffsetRad,
      moonTextureOffsetRotationScratch
    );
    Cesium.Matrix3.multiply(rotation, textureOffsetRotation, rotation);
  }

  const translation = Cesium.Simon1994PlanetaryPositions.computeMoonPositionInEarthInertialFrame(
    time,
    moonTranslationScratch
  );
  Cesium.Matrix3.multiplyByVector(moonIcrfToFixedScratch, translation, translation);

  return Cesium.Matrix4.fromRotationTranslation(rotation, translation, result);
}

function screenPositionHitsMoon(viewer, screenPosition) {
  if (!screenPosition) return false;
  if (!Cesium.IntersectionTests?.rayEllipsoid) return false;

  const ray = viewer.camera.getPickRay(screenPosition);
  if (!ray) return false;

  const modelMatrix = computeMoonModelMatrix(viewer.clock.currentTime, moonModelMatrixScratch);
  const inverse = Cesium.Matrix4.inverseTransformation(modelMatrix, moonInverseMatrixScratch);
  const localOrigin = Cesium.Matrix4.multiplyByPoint(inverse, ray.origin, moonLocalRayOriginScratch);
  const localDirection = Cesium.Matrix4.multiplyByPointAsVector(inverse, ray.direction, moonLocalRayDirectionScratch);
  Cesium.Cartesian3.normalize(localDirection, localDirection);

  const moonInterval = Cesium.IntersectionTests.rayEllipsoid(
    new Cesium.Ray(localOrigin, localDirection),
    Cesium.Ellipsoid.MOON
  );
  if (!moonInterval || !Number.isFinite(moonInterval.stop) || moonInterval.stop <= 0) {
    return false;
  }

  const moonDistance = moonInterval.start > 0 ? moonInterval.start : moonInterval.stop;
  const earthInterval = Cesium.IntersectionTests.rayEllipsoid(ray, viewer.scene.globe?.ellipsoid || Cesium.Ellipsoid.WGS84);
  if (earthInterval && Number.isFinite(earthInterval.stop) && earthInterval.stop > 0) {
    const earthDistance = earthInterval.start > 0 ? earthInterval.start : earthInterval.stop;
    if (earthDistance < moonDistance) {
      return false;
    }
  }

  return true;
}

function screenPositionHitsEarth(viewer, screenPosition) {
  if (!screenPosition) return false;
  if (!Cesium.IntersectionTests?.rayEllipsoid) return false;

  const ray = viewer.camera.getPickRay(screenPosition);
  if (!ray) return false;

  const earthInterval = Cesium.IntersectionTests.rayEllipsoid(
    ray,
    viewer.scene.globe?.ellipsoid || Cesium.Ellipsoid.WGS84
  );
  if (!earthInterval || !Number.isFinite(earthInterval.stop) || earthInterval.stop <= 0) {
    return false;
  }

  const earthDistance = earthInterval.start > 0 ? earthInterval.start : earthInterval.stop;
  const modelMatrix = computeMoonModelMatrix(viewer.clock.currentTime, moonModelMatrixScratch);
  const inverse = Cesium.Matrix4.inverseTransformation(modelMatrix, moonInverseMatrixScratch);
  const localOrigin = Cesium.Matrix4.multiplyByPoint(inverse, ray.origin, moonLocalRayOriginScratch);
  const localDirection = Cesium.Matrix4.multiplyByPointAsVector(inverse, ray.direction, moonLocalRayDirectionScratch);
  Cesium.Cartesian3.normalize(localDirection, localDirection);
  const moonInterval = Cesium.IntersectionTests.rayEllipsoid(
    new Cesium.Ray(localOrigin, localDirection),
    Cesium.Ellipsoid.MOON
  );
  if (moonInterval && Number.isFinite(moonInterval.stop) && moonInterval.stop > 0) {
    const moonDistance = moonInterval.start > 0 ? moonInterval.start : moonInterval.stop;
    if (moonDistance < earthDistance) {
      return false;
    }
  }

  return true;
}

function getMoonWorldPositionForTime(time, result = new Cesium.Cartesian3()) {
  const modelMatrix = computeMoonModelMatrix(time, moonModelMatrixScratch);
  return Cesium.Matrix4.getTranslation(modelMatrix, result);
}

function getMoonOrientationQuaternionForTime(time, result = new Cesium.Quaternion()) {
  const modelMatrix = computeMoonModelMatrix(time, moonModelMatrixScratch);
  const rotation = Cesium.Matrix4.getMatrix3(modelMatrix, moonOrientationMatrixScratch);
  return Cesium.Quaternion.fromRotationMatrix(rotation, result);
}

function cloneViewState(camera) {
  return {
    destination: Cesium.Cartesian3.clone(camera.positionWC),
    direction: Cesium.Cartesian3.clone(camera.directionWC),
    up: Cesium.Cartesian3.clone(camera.upWC),
  };
}

function getSunDirectionFixedForTime(time, result = new Cesium.Cartesian3()) {
  const sunInertial = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(
    time,
    moonSunInertialScratch
  );
  const icrfToFixed = Cesium.Transforms.computeIcrfToFixedMatrix(time);
  let sunFixed = sunInertial;
  if (Cesium.defined(icrfToFixed)) {
    sunFixed = Cesium.Matrix3.multiplyByVector(icrfToFixed, sunInertial, moonSunFixedScratch);
  }
  return Cesium.Cartesian3.normalize(sunFixed, result);
}

function getMoonCameraPoseForTime(time, rangeMultiplier = 5.5) {
  const moonPosition = getMoonWorldPositionForTime(time, moonWorldPositionScratch);
  const sunDir = getSunDirectionFixedForTime(time, moonSunDirScratch);
  const range = Cesium.Ellipsoid.MOON.maximumRadius * rangeMultiplier;

  const destination = Cesium.Cartesian3.multiplyByScalar(sunDir, range, moonCameraDestinationScratch);
  Cesium.Cartesian3.add(moonPosition, destination, destination);

  const direction = Cesium.Cartesian3.subtract(moonPosition, destination, moonCameraDirectionScratch);
  Cesium.Cartesian3.normalize(direction, direction);

  let up = Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.UNIT_Z, moonCameraRightScratch);
  if (Cesium.Cartesian3.magnitudeSquared(up) < 1e-6) {
    up = Cesium.Cartesian3.cross(direction, Cesium.Cartesian3.UNIT_Y, moonFallbackUpScratch);
  }
  Cesium.Cartesian3.normalize(up, up);
  Cesium.Cartesian3.cross(up, direction, up);
  Cesium.Cartesian3.normalize(up, up);

  return {
    destination: Cesium.Cartesian3.clone(destination),
    direction: Cesium.Cartesian3.clone(direction),
    up: Cesium.Cartesian3.clone(up),
  };
}

function getSunWorldPositionForTime(viewer, time, skyDome, distanceScale, result = new Cesium.Cartesian3()) {
  const sunInertial = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(
    time,
    moonSunInertialScratch
  );
  const icrfToFixed = Cesium.Transforms.computeIcrfToFixedMatrix(time);
  let sunFixed = sunInertial;
  if (Cesium.defined(icrfToFixed)) {
    sunFixed = Cesium.Matrix3.multiplyByVector(icrfToFixed, sunInertial, moonSunFixedScratch);
  }
  Cesium.Cartesian3.normalize(sunFixed, moonSunDirScratch);
  const fallbackDistance = viewer.scene.globe.ellipsoid.maximumRadius * distanceScale;
  const skyDomeRadius = Number(skyDome?.getStateForDebug?.()?.radiusMeters) || 0;
  const distance =
    skyDomeRadius > 0
      ? Math.max(viewer.scene.globe.ellipsoid.maximumRadius * 6.0, skyDomeRadius * 0.72)
      : fallbackDistance;
  Cesium.Cartesian3.multiplyByScalar(moonSunDirScratch, distance, result);
  Cesium.Cartesian3.add(viewer.camera.positionWC, result, result);
  return result;
}

function isWorldPositionOccludedByEarth(viewer, targetPosition) {
  if (!targetPosition) return false;
  const cameraPosition = viewer.camera.positionWC;
  const toTarget = Cesium.Cartesian3.subtract(targetPosition, cameraPosition, moonCameraDirectionScratch);
  const targetDistance = Cesium.Cartesian3.magnitude(toTarget);
  if (!Number.isFinite(targetDistance) || targetDistance <= 1) {
    return false;
  }
  Cesium.Cartesian3.normalize(toTarget, toTarget);
  const ray = new Cesium.Ray(cameraPosition, toTarget);
  const earthInterval = Cesium.IntersectionTests.rayEllipsoid(
    ray,
    viewer.scene.globe?.ellipsoid || Cesium.Ellipsoid.WGS84
  );
  if (!earthInterval || !Number.isFinite(earthInterval.stop) || earthInterval.stop <= 0) {
    return false;
  }
  const earthDistance = earthInterval.start > 0 ? earthInterval.start : earthInterval.stop;
  return earthDistance < targetDistance;
}

function isWorldPositionOccludedByEarthOrMoon(viewer, targetPosition) {
  if (!targetPosition) return false;
  const cameraPosition = viewer.camera.positionWC;
  const toTarget = Cesium.Cartesian3.subtract(targetPosition, cameraPosition, moonCameraDirectionScratch);
  const targetDistance = Cesium.Cartesian3.magnitude(toTarget);
  if (!Number.isFinite(targetDistance) || targetDistance <= 1) {
    return false;
  }
  Cesium.Cartesian3.normalize(toTarget, toTarget);
  const ray = new Cesium.Ray(cameraPosition, toTarget);

  const earthInterval = Cesium.IntersectionTests.rayEllipsoid(
    ray,
    viewer.scene.globe?.ellipsoid || Cesium.Ellipsoid.WGS84
  );
  if (earthInterval && Number.isFinite(earthInterval.stop) && earthInterval.stop > 0) {
    const earthDistance = earthInterval.start > 0 ? earthInterval.start : earthInterval.stop;
    if (earthDistance < targetDistance) {
      return true;
    }
  }

  const modelMatrix = computeMoonModelMatrix(viewer.clock.currentTime, moonModelMatrixScratch);
  const inverse = Cesium.Matrix4.inverseTransformation(modelMatrix, moonInverseMatrixScratch);
  const localOrigin = Cesium.Matrix4.multiplyByPoint(inverse, ray.origin, moonLocalRayOriginScratch);
  const localDirection = Cesium.Matrix4.multiplyByPointAsVector(inverse, ray.direction, moonLocalRayDirectionScratch);
  Cesium.Cartesian3.normalize(localDirection, localDirection);
  const moonInterval = Cesium.IntersectionTests.rayEllipsoid(
    new Cesium.Ray(localOrigin, localDirection),
    Cesium.Ellipsoid.MOON
  );
  if (moonInterval && Number.isFinite(moonInterval.stop) && moonInterval.stop > 0) {
    const moonDistance = moonInterval.start > 0 ? moonInterval.start : moonInterval.stop;
    if (moonDistance < targetDistance) {
      return true;
    }
  }

  return false;
}

function getCurrentLunarEclipseTintFactor(time, appConfig) {
  if (appConfig?.globe?.moonEclipseTintEnabled === false) return 0;
  const moon = Cesium.Simon1994PlanetaryPositions.computeMoonPositionInEarthInertialFrame(
    time,
    moonTranslationScratch
  );
  const sun = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(
    time,
    moonSunInertialScratch
  );
  const sunDistanceKm = Cesium.Cartesian3.magnitude(sun) / 1000;
  const moonDistanceKm = Cesium.Cartesian3.magnitude(moon) / 1000;
  const antiSunDir = Cesium.Cartesian3.normalize(
    Cesium.Cartesian3.negate(sun, moonSunDirScratch),
    moonSunDirScratch
  );
  const moonKm = Cesium.Cartesian3.divideByScalar(moon, 1000, moonLocalRayOriginScratch);
  const projectionKm = Cesium.Cartesian3.dot(moonKm, antiSunDir);
  if (projectionKm <= 0) return 0;

  const axisPoint = Cesium.Cartesian3.multiplyByScalar(antiSunDir, projectionKm, moonLocalRayDirectionScratch);
  const offsetKm = Cesium.Cartesian3.distance(moonKm, axisPoint);
  const umbraLengthKm = (EARTH_RADIUS_KM * sunDistanceKm) / Math.max(1, SUN_RADIUS_KM - EARTH_RADIUS_KM);
  const umbraRadiusKm = Math.max(0, EARTH_RADIUS_KM * (1 - projectionKm / umbraLengthKm));

  if (offsetKm + MOON_RADIUS_KM > umbraRadiusKm) {
    return 0;
  }

  const totalityMarginKm = Math.max(1, umbraRadiusKm - MOON_RADIUS_KM);
  const centeredness = Cesium.Math.clamp(1 - offsetKm / totalityMarginKm, 0, 1);
  const baseStrength = Cesium.Math.clamp(appConfig?.globe?.moonEclipseTintStrength ?? 0.58, 0, 1);
  return Cesium.Math.clamp(baseStrength * (0.7 + centeredness * 0.3), 0, 1);
}

function worldToCssScreenPosition(viewer, worldPosition) {
  const scene = viewer.scene;
  const windowPosition =
    scene.cartesianToCanvasCoordinates?.(worldPosition, screenPositionScratch) ||
    Cesium.SceneTransforms.worldToWindowCoordinates?.(scene, worldPosition);
  if (!windowPosition) return null;

  const drawingBufferWidth = scene.canvas?.width ?? scene.canvas?.clientWidth ?? 0;
  const drawingBufferHeight = scene.canvas?.height ?? scene.canvas?.clientHeight ?? 0;
  const canvasWidth = scene.canvas?.clientWidth ?? drawingBufferWidth;
  const canvasHeight = scene.canvas?.clientHeight ?? drawingBufferHeight;
  if (drawingBufferWidth <= 0 || drawingBufferHeight <= 0 || canvasWidth <= 0 || canvasHeight <= 0) {
    return null;
  }

  let x = windowPosition.x;
  let y = windowPosition.y;
  const looksLikeDrawingBuffer = x > canvasWidth || y > canvasHeight;
  if (looksLikeDrawingBuffer) {
    x *= canvasWidth / drawingBufferWidth;
    y *= canvasHeight / drawingBufferHeight;
  }

  if (x < 0 || x > canvasWidth || y < 0 || y > canvasHeight) {
    return null;
  }

  return { x, y };
}

function getBodyTagScreenPosition(viewer, center, radius, anchorScratch, directionScratch) {
  const toCamera = Cesium.Cartesian3.subtract(viewer.camera.positionWC, center, directionScratch);
  if (Cesium.Cartesian3.magnitudeSquared(toCamera) > 1e-6) {
    Cesium.Cartesian3.normalize(toCamera, toCamera);
    Cesium.Cartesian3.multiplyByScalar(toCamera, radius * 1.04, anchorScratch);
    Cesium.Cartesian3.add(center, anchorScratch, anchorScratch);
    const anchored = worldToCssScreenPosition(viewer, anchorScratch);
    if (anchored) return anchored;
  }
  return worldToCssScreenPosition(viewer, center);
}

export function createGlobeExplorer({
  containerId,
  appConfig,
  getBiomeColor,
  onHover,
  onBodyTagsUpdate,
  onMoonTagUpdate,
  onEarthTagUpdate,
  onSelect,
  onDebugReport,
}) {
  const viewer = new Cesium.Viewer(containerId, {
    animation: false,
    timeline: false,
    geocoder: false,
    homeButton: false,
    baseLayerPicker: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false,
    shouldAnimate: false,
    requestRenderMode: Boolean(appConfig.globe.requestRenderMode),
    maximumRenderTimeChange: Infinity,
    terrainProvider: new Cesium.EllipsoidTerrainProvider(),
    baseLayer: false,
    scene3DOnly: true,
    // The scale below caps device pixels; Cesium must honor devicePixelRatio.
    useBrowserRecommendedResolution: false,
    shadows: false,
    orderIndependentTranslucency: true,
  });
  viewer.targetFrameRate = appConfig.globe.targetFrameRate ?? 30;
  viewer.clock.currentTime = Cesium.JulianDate.now();
  moonPrimeMeridianOffsetRad = Cesium.Math.toRadians(appConfig.globe.moonPrimeMeridianOffsetDeg ?? 180);

  viewer.imageryLayers.removeAll();
  if (typeof window !== "undefined" && Number.isFinite(window.devicePixelRatio)) {
    const maxDpr = appConfig.globe.maxDevicePixelRatio ?? window.devicePixelRatio;
    viewer.resolutionScale = Math.min(1, maxDpr / window.devicePixelRatio);
  }
  const idleResolutionScale = viewer.resolutionScale || 1;
  const movingResolutionScale = Math.max(
    0.35,
    Math.min(
      idleResolutionScale,
      idleResolutionScale * (appConfig.globe.movingResolutionScaleFactor ?? 0.75)
    )
  );
  viewer.scene.backgroundColor = colorFromCss("#04070d");
  viewer.scene.pickTranslucentDepth = true;
  viewer.scene.skyBox.show = false;
  viewer.scene.skyAtmosphere.show = false;
  viewer.scene.sun.show = appConfig.globe.sunDotEnabled === true ? false : true;
  viewer.scene.logarithmicDepthBuffer = true;
  if ("logarithmicDepthFarToNearRatio" in viewer.scene) {
    viewer.scene.logarithmicDepthFarToNearRatio = 1e9;
  }
  if (viewer.scene.moon) {
    viewer.scene.moon.show = appConfig.globe.moonEnabled !== false;
  }
  viewer.scene.fog.enabled = false;
  viewer.scene.globe.showGroundAtmosphere = false;
  viewer.scene.globe.enableLighting = true;
  const ssc = viewer.scene.screenSpaceCameraController;
  const earthMinimumZoomDistance = Math.max(1000, appConfig.globe.maxZoomInHeight ?? 1000);
  ssc.minimumZoomDistance = earthMinimumZoomDistance;
  ssc.maximumZoomDistance = 2.0e9;
  if (typeof appConfig.globe.imageryPreloadSiblings === "boolean") {
    viewer.scene.globe.preloadSiblings = appConfig.globe.imageryPreloadSiblings;
  }
  if (typeof appConfig.globe.imageryPreloadAncestors === "boolean") {
    viewer.scene.globe.preloadAncestors = appConfig.globe.imageryPreloadAncestors;
  }
  if (Number.isFinite(appConfig.globe.globeTileCacheSize)) {
    viewer.scene.globe.tileCacheSize = Math.max(100, Math.round(appConfig.globe.globeTileCacheSize));
  }
  viewer.scene.globe.baseColor = colorFromCss("#08111c");
  viewer.scene.globe.depthTestAgainstTerrain = false;
  if (viewer.scene.postProcessStages?.fxaa) {
    viewer.scene.postProcessStages.fxaa.enabled = true;
  }
  viewer.cesiumWidget.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  // Imagery setup reads selection state synchronously.
  let selectedRegionId = null;
  let baseImageryLayer = null;
  let openOceanBathymetryImageryLayer = null;
  let blueMarbleDetailImageryLayer = null;
  let blueMarbleUltraImageryLayer = null;
  let blackMarbleNightBaseImageryLayer = null;
  let blackMarbleNightDetailImageryLayer = null;
  let blackMarbleNightUltraImageryLayers = [];
  let sunDotEntity = null;
  let sunGlowEntity = null;
  let moonProxyPrimitive = null;
  let moonAnchorEntity = null;
  let moonDebugMarkerEntity = null;
  let skyDome = null;
  let imageryBlendTimer = 0;
  let lastImageryBlendRunAt = 0;
  let lastSolarDot = null;
  let solarHighDetailSide = "day";
  let nightDiagnosticsEnabled = false;
  let blueMarbleUltraRequestCount = 0;
  let blackMarbleNightUltraRequestCount = 0;
  let blueMarbleUltraUniqueRequestCount = 0;
  let blackMarbleNightUltraUniqueRequestCount = 0;
  let blueMarbleUltraDedupHitCount = 0;
  let blackMarbleNightUltraDedupHitCount = 0;
  let nightUltraIdleTimer = 0;
  let nightUltraIdleReady = true;
  let nightUltraStableHeight = Number.NaN;

  function getMaxTextureSize() {
    try {
      const gl = viewer.scene?.context?._gl;
      if (!gl) return Number.POSITIVE_INFINITY;
      const maxSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      return Number.isFinite(maxSize) ? maxSize : Number.POSITIVE_INFINITY;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }

  function loadImageDimensions(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        resolve({
          width: img.naturalWidth || img.width || 0,
          height: img.naturalHeight || img.height || 0,
        });
      };
      img.onerror = () => reject(new Error(`Image load failed: ${url}`));
      img.src = url;
    });
  }

  async function attachBaseImagery() {
    const primaryUrl = appConfig.globe.baseImageryUrl;
    const fallbackUrl = appConfig.globe.baseImageryFallbackUrl;
    const candidates = [...new Set([primaryUrl, fallbackUrl].filter(Boolean))];
    if (!candidates.length) return;
    const rectangle = Cesium.Rectangle.fromDegrees(-180, -90, 180, 90);
    const maxTextureSize = getMaxTextureSize();

    for (const url of candidates) {
      try {
        const { width, height } = await loadImageDimensions(url);
        if (width > maxTextureSize || height > maxTextureSize) {
          console.warn(
            `Skipping base imagery ${url}: ${width}x${height} exceeds MAX_TEXTURE_SIZE ${maxTextureSize}`
          );
          continue;
        }

        let provider;
        if (Cesium.SingleTileImageryProvider?.fromUrl) {
          provider = await Cesium.SingleTileImageryProvider.fromUrl(url, { rectangle });
        } else {
          provider = new Cesium.SingleTileImageryProvider({ url, rectangle });
        }
        const layer = viewer.imageryLayers.addImageryProvider(provider, 0);
        layer.alpha = appConfig.globe.baseImageryAlpha ?? 1.0;
        baseImageryLayer = layer;
        requestRender();
        return;
      } catch (err) {
        console.warn("Failed to load base imagery candidate:", url, err);
      }
    }
    console.warn("No compatible base imagery could be loaded.");
  }
  attachBaseImagery();

  async function attachOpenOceanBathymetryImagery() {
    const url = appConfig.globe.openOceanBathymetryImageryUrl;
    if (!url) return;
    if (openOceanBathymetryImageryLayer) return;
    const rectangle = Cesium.Rectangle.fromDegrees(-180, -90, 180, 90);
    const maxTextureSize = getMaxTextureSize();
    try {
      const { width, height } = await loadImageDimensions(url);
      if (width > maxTextureSize || height > maxTextureSize) {
        console.warn(
          `Skipping open-ocean bathymetry imagery ${url}: ${width}x${height} exceeds MAX_TEXTURE_SIZE ${maxTextureSize}`
        );
        return;
      }

      let provider;
      if (Cesium.SingleTileImageryProvider?.fromUrl) {
        provider = await Cesium.SingleTileImageryProvider.fromUrl(url, { rectangle });
      } else {
        provider = new Cesium.SingleTileImageryProvider({ url, rectangle });
      }

      const layer = viewer.imageryLayers.addImageryProvider(provider, 6);
      layer.alpha = 0.0;
      layer.show = false;
      openOceanBathymetryImageryLayer = layer;
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to load open-ocean bathymetry imagery:", err);
    }
  }
  attachOpenOceanBathymetryImagery();

  function getBlueMarbleDetailTilesBlendFactor() {
    if (appConfig.globe.detailImageryTilesEnabled === false) return 0;
    const h = getCameraHeightMeters();
    let fullOn = appConfig.globe.detailImageryEnterHeight ?? 5_500_000;
    let fullOff = appConfig.globe.detailImageryExitHeight ?? Math.round(fullOn * 1.18);

    if (fullOff < fullOn) {
      const tmp = fullOff;
      fullOff = fullOn;
      fullOn = tmp;
    }

    if (h >= fullOff) return 0;
    if (h <= fullOn) return 1;
    return Cesium.Math.clamp((fullOff - h) / (fullOff - fullOn), 0, 1);
  }

  function getBlueMarbleUltraTilesBlendFactor() {
    if (appConfig.globe.ultraDetailImageryEnabled === false) return 0;
    const h = getCameraHeightMeters();
    let start = appConfig.globe.ultraDetailImageryBlendStartHeight ?? 5_500_000;
    let full = appConfig.globe.ultraDetailImageryFullOnHeight ?? 1_000_000;
    if (start < full) {
      const tmp = start;
      start = full;
      full = tmp;
    }

    if (h >= start) return 0;
    if (h <= full) return 1;
    return Cesium.Math.clamp((start - h) / (start - full), 0, 1);
  }

  function getNightDetailTilesBlendFactor() {
    if (appConfig.globe.nightImageryEnabled === false) return 0;
    if (appConfig.globe.nightDetailImageryEnabled === false) return 0;
    const h = getCameraHeightMeters();
    let fullOn = appConfig.globe.nightDetailImageryEnterHeight ?? appConfig.globe.detailImageryEnterHeight ?? 5_500_000;
    let fullOff =
      appConfig.globe.nightDetailImageryExitHeight ??
      appConfig.globe.detailImageryExitHeight ??
      Math.round(fullOn * 1.18);
    if (fullOff < fullOn) {
      const tmp = fullOff;
      fullOff = fullOn;
      fullOn = tmp;
    }
    if (h >= fullOff) return 0;
    if (h <= fullOn) return 1;
    return Cesium.Math.clamp((fullOff - h) / (fullOff - fullOn), 0, 1);
  }

  function getNightUltraTilesBlendFactor() {
    if (appConfig.globe.nightImageryEnabled === false) return 0;
    if (appConfig.globe.nightUltraDetailImageryEnabled === false) return 0;
    const h = getCameraHeightMeters();
    let start =
      appConfig.globe.nightUltraDetailImageryBlendStartHeight ??
      appConfig.globe.ultraDetailImageryBlendStartHeight ??
      5_500_000;
    let full =
      appConfig.globe.nightUltraDetailImageryFullOnHeight ??
      appConfig.globe.ultraDetailImageryFullOnHeight ??
      1_000_000;
    if (start < full) {
      const tmp = start;
      start = full;
      full = tmp;
    }
    if (h >= start) return 0;
    if (h <= full) return 1;
    return Cesium.Math.clamp((start - h) / (start - full), 0, 1);
  }

  function getNightOverviewBlendFactor() {
    if (appConfig.globe.nightImageryEnabled === false) return 0;
    const h = getCameraHeightMeters();
    let dayOnly = appConfig.globe.nightOverviewDayOnlyHeight ?? 15_000_000;
    let fullNight = appConfig.globe.nightOverviewFullNightHeight ?? 20_000_000;
    if (fullNight < dayOnly) {
      const tmp = fullNight;
      fullNight = dayOnly;
      dayOnly = tmp;
    }
    if (h <= dayOnly) return 0;
    if (h >= fullNight) return 1;
    return Cesium.Math.clamp((h - dayOnly) / (fullNight - dayOnly), 0, 1);
  }

  function getSunDirectionInEarthFixed(time) {
    if (!Cesium.Simon1994PlanetaryPositions?.computeSunPositionInEarthInertialFrame) return null;
    const scratchInertial = new Cesium.Cartesian3();
    const scratchFixed = new Cesium.Cartesian3();
    const scratchDir = new Cesium.Cartesian3();

    const sunInertial = Cesium.Simon1994PlanetaryPositions.computeSunPositionInEarthInertialFrame(
      time,
      scratchInertial
    );
    const icrfToFixed = Cesium.Transforms.computeIcrfToFixedMatrix(time);
    const sunFixed = Cesium.defined(icrfToFixed)
      ? Cesium.Matrix3.multiplyByVector(icrfToFixed, sunInertial, scratchFixed)
      : sunInertial;
    return Cesium.Cartesian3.normalize(sunFixed, scratchDir);
  }

  function getViewportCenterSurfaceDirection() {
    const canvas = viewer.scene.canvas;
    const width = canvas?.clientWidth ?? canvas?.width ?? 0;
    const height = canvas?.clientHeight ?? canvas?.height ?? 0;
    const ellipsoid = viewer.scene.globe.ellipsoid;
    const scratchDir = new Cesium.Cartesian3();

    if (width > 0 && height > 0) {
      const center = new Cesium.Cartesian2(width / 2, height / 2);
      const picked = viewer.camera.pickEllipsoid(center, ellipsoid);
      if (picked) return Cesium.Cartesian3.normalize(picked, scratchDir);
    }

    // Fallback to camera subpoint approximation.
    return Cesium.Cartesian3.normalize(viewer.camera.positionWC, scratchDir);
  }

  function smoothStepGate(value, edgeWidth) {
    const w = Number(edgeWidth) || 0;
    if (w <= 0) return value >= 0 ? 1 : 0;
    return Cesium.Math.clamp((value + w) / (2 * w), 0, 1);
  }

  function getSolarSideBlendFactors() {
    if (appConfig.globe.imagerySolarSideGatingEnabled === false) {
      return { dayFactor: 1, nightFactor: 1, solarDot: null, highDetailDayFactor: 1, highDetailNightFactor: 1 };
    }
    const now = viewer.clock.currentTime || Cesium.JulianDate.now();
    const sunDir = getSunDirectionInEarthFixed(now);
    if (!sunDir) {
      return { dayFactor: 1, nightFactor: 1, solarDot: null, highDetailDayFactor: 1, highDetailNightFactor: 1 };
    }
    const viewDir = getViewportCenterSurfaceDirection();
    const solarDot = Cesium.Cartesian3.dot(viewDir, sunDir);
    const edge = appConfig.globe.imagerySolarTerminatorBlendDotWidth ?? 0.0;
    const dayFactor = smoothStepGate(solarDot, edge);
    const nightFactor = 1 - dayFactor;
    const hysteresis = Math.max(0, appConfig.globe.imagerySolarTerminatorHysteresisDot ?? 0.08);
    if (solarHighDetailSide === "day") {
      if (solarDot < -hysteresis) {
        solarHighDetailSide = "night";
      }
    } else if (solarDot > hysteresis) {
      solarHighDetailSide = "day";
    }
    return {
      dayFactor,
      nightFactor,
      solarDot,
      highDetailDayFactor: solarHighDetailSide === "day" ? 1 : 0,
      highDetailNightFactor: solarHighDetailSide === "night" ? 1 : 0,
    };
  }

  function applyImageryLayerAlpha(layer, targetAlpha, threshold = 0.01) {
    if (!layer) return false;
    const shouldShow = targetAlpha > threshold;
    const alphaChanged = Math.abs((layer.alpha ?? 0) - targetAlpha) > 0.003;
    const showChanged = layer.show !== shouldShow;
    if (!alphaChanged && !showChanged) return false;
    layer.alpha = targetAlpha;
    layer.show = shouldShow;
    return true;
  }

  function applyImageryLayerGroupAlpha(layers, targetAlpha, threshold = 0.01) {
    if (!Array.isArray(layers) || layers.length === 0) return false;
    let changed = false;
    for (const layer of layers) {
      changed = applyImageryLayerAlpha(layer, targetAlpha, threshold) || changed;
    }
    return changed;
  }

  function captureNightUltraStableView() {
    Cesium.Cartesian3.clone(viewer.camera.directionWC, nightUltraStableDirectionScratch);
    nightUltraStableHeight = getCameraHeightMeters();
  }

  function setNightUltraIdleReady(nextReady) {
    if (nightUltraIdleReady === nextReady) {
      if (nightUltraIdleReady) {
        captureNightUltraStableView();
      }
      return;
    }
    nightUltraIdleReady = nextReady;
    if (nightUltraIdleReady) {
      captureNightUltraStableView();
    }
    scheduleImageryBlendSync(true);
  }

  function hasNightUltraSignificantViewChange() {
    if (!Number.isFinite(nightUltraStableHeight)) return true;

    const headingLimitDeg = Math.max(0.05, appConfig.globe.nightUltraSignificantHeadingDeg ?? 0.35);
    const heightLimit = Math.max(1000, appConfig.globe.nightUltraSignificantHeightMeters ?? 25_000);

    const directionDot = Cesium.Math.clamp(
      Cesium.Cartesian3.dot(viewer.camera.directionWC, nightUltraStableDirectionScratch),
      -1,
      1
    );
    const directionDeltaDeg = Cesium.Math.toDegrees(Math.acos(directionDot));
    const heightDelta = Math.abs(getCameraHeightMeters() - nightUltraStableHeight);

    if (directionDeltaDeg > headingLimitDeg) return true;
    if (heightDelta > heightLimit) return true;

    return false;
  }

  function clearNightUltraIdleTimer() {
    if (!nightUltraIdleTimer) return;
    clearTimeout(nightUltraIdleTimer);
    nightUltraIdleTimer = 0;
  }

  function armNightUltraIdleActivation() {
    clearNightUltraIdleTimer();
    const delayMs = Math.max(0, appConfig.globe.nightUltraIdleDelayMs ?? 220);
    if (delayMs === 0) {
      setNightUltraIdleReady(true);
      return;
    }
    nightUltraIdleTimer = window.setTimeout(() => {
      nightUltraIdleTimer = 0;
      setNightUltraIdleReady(true);
    }, delayMs);
  }

  function syncImageryLayerVisibility() {
    const solar = getSolarSideBlendFactors();
    lastSolarDot = solar.solarDot;
    const openOceanMode = selectedRegionId === OPEN_OCEAN_SELECTION_ID;
    const nightOverviewBlend = getNightOverviewBlendFactor();
    const fullDayMode = nightOverviewBlend <= 0.001;
    const highDetailDayFactor = fullDayMode ? 1 : solar.highDetailDayFactor;

    const detailBlend = getBlueMarbleDetailTilesBlendFactor();
    const ultraBlend = getBlueMarbleUltraTilesBlendFactor();
    const effectiveDetailBlend = detailBlend * (1 - ultraBlend);
    let detailTargetAlpha =
      (appConfig.globe.detailImageryAlpha ?? 1.0) * effectiveDetailBlend * highDetailDayFactor;
    let ultraTargetAlpha =
      (appConfig.globe.ultraDetailImageryAlpha ?? 1.0) * ultraBlend * highDetailDayFactor;
    const nightDetailBlend = getNightDetailTilesBlendFactor();
    const nightUltraBlend = getNightUltraTilesBlendFactor();
    const nightUltraFrozenVisible =
      appConfig.globe.nightUltraLoadOnIdleOnly !== false &&
      !nightUltraIdleReady &&
      blackMarbleNightUltraImageryLayers.some((layer) => layer?.show === true && (layer?.alpha ?? 0) > 0.01);
    const effectiveNightUltraBlend =
      appConfig.globe.nightUltraLoadOnIdleOnly !== false && !nightUltraIdleReady && !nightUltraFrozenVisible
        ? 0
        : nightUltraBlend;
    const suppressNightDetail = appConfig.globe.nightDetailSuppressWhenUltraActive !== false;
    const suppressThreshold = appConfig.globe.nightDetailSuppressUltraBlendThreshold ?? 0.08;
    const nightEffectiveDetailBlend =
      nightUltraFrozenVisible
        ? 0
        : suppressNightDetail && effectiveNightUltraBlend >= suppressThreshold
        ? 0
        : nightDetailBlend * (1 - effectiveNightUltraBlend);
    let nightDetailTargetAlpha =
      (appConfig.globe.nightDetailImageryAlpha ?? 1.0) * nightEffectiveDetailBlend * solar.highDetailNightFactor;
    let nightUltraTargetAlpha =
      (appConfig.globe.nightUltraDetailImageryAlpha ?? 1.0) *
      effectiveNightUltraBlend *
      solar.highDetailNightFactor;
    let nightBaseTargetAlpha = (appConfig.globe.nightBaseImageryAlpha ?? 1.0) * nightOverviewBlend;
    let baseTargetAlpha = appConfig.globe.baseImageryAlpha ?? 1.0;
    let openOceanBathymetryTargetAlpha = 0.0;

    if (openOceanMode) {
      baseTargetAlpha = 0.0;
      detailTargetAlpha = 0.0;
      ultraTargetAlpha = 0.0;
      nightBaseTargetAlpha = 0.0;
      nightDetailTargetAlpha = 0.0;
      nightUltraTargetAlpha = 0.0;
      openOceanBathymetryTargetAlpha = appConfig.globe.openOceanBathymetryImageryAlpha ?? 1.0;
    }

    let changed = false;
    changed = applyImageryLayerAlpha(baseImageryLayer, baseTargetAlpha) || changed;
    changed = applyImageryLayerAlpha(openOceanBathymetryImageryLayer, openOceanBathymetryTargetAlpha) || changed;
    changed = applyImageryLayerAlpha(blueMarbleDetailImageryLayer, detailTargetAlpha) || changed;
    changed = applyImageryLayerAlpha(blueMarbleUltraImageryLayer, ultraTargetAlpha) || changed;
    changed = applyImageryLayerAlpha(blackMarbleNightBaseImageryLayer, nightBaseTargetAlpha) || changed;
    changed = applyImageryLayerAlpha(blackMarbleNightDetailImageryLayer, nightDetailTargetAlpha) || changed;
    if (!nightUltraFrozenVisible) {
      changed = applyImageryLayerGroupAlpha(blackMarbleNightUltraImageryLayers, nightUltraTargetAlpha) || changed;
    }
    const shouldEnableLighting = !openOceanMode && !nightDiagnosticsEnabled && nightOverviewBlend > 0.001;
    if (viewer.scene.globe.enableLighting !== shouldEnableLighting) {
      viewer.scene.globe.enableLighting = shouldEnableLighting;
      changed = true;
    }

    if (!changed) return;
    requestRender();
  }

  function scheduleImageryBlendSync(force = false) {
    const run = () => {
      imageryBlendTimer = 0;
      lastImageryBlendRunAt = performance.now();
      syncImageryLayerVisibility();
    };

    if (force) {
      if (imageryBlendTimer) {
        clearTimeout(imageryBlendTimer);
        imageryBlendTimer = 0;
      }
      run();
      return;
    }

    const minMs = Math.max(16, appConfig.globe.imageryBlendUpdateMs ?? 90);
    const now = performance.now();
    if (now - lastImageryBlendRunAt >= minMs && !imageryBlendTimer) {
      run();
      return;
    }
    if (imageryBlendTimer) return;
    imageryBlendTimer = window.setTimeout(run, minMs);
  }

  async function attachBlueMarbleDetailTilesImagery() {
    if (appConfig.globe.detailImageryTilesEnabled === false) return;
    if (blueMarbleDetailImageryLayer) return;
    const url = appConfig.globe.detailImageryTilesUrlTemplate;
    if (!url) return;

    const level = appConfig.globe.detailImageryTileLevel ?? 5;
    const tileSize = appConfig.globe.detailImageryTileSize ?? 256;
    try {
      const provider = new Cesium.UrlTemplateImageryProvider({
        url,
        tilingScheme: new Cesium.GeographicTilingScheme(),
        rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90),
        minimumLevel: level,
        maximumLevel: level,
        tileWidth: tileSize,
        tileHeight: tileSize,
        hasAlphaChannel: false,
      });
      provider.errorEvent?.addEventListener?.((err) => {
        console.warn("Blue Marble detail tile imagery error:", err);
      });
      const layer = viewer.imageryLayers.addImageryProvider(provider, 1);
      layer.alpha = 0.0;
      layer.show = false;
      blueMarbleDetailImageryLayer = layer;
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to create Blue Marble detail tile imagery provider:", err);
    }
  }
  attachBlueMarbleDetailTilesImagery();

  async function attachBlueMarbleUltraTilesImagery() {
    if (appConfig.globe.ultraDetailImageryEnabled === false) return;
    if (blueMarbleUltraImageryLayer) return;
    const url = appConfig.globe.ultraDetailImageryTilesUrlTemplate;
    if (!url) return;

    const minLevel = appConfig.globe.ultraDetailImageryMinLevel ?? 7;
    const maxLevel = appConfig.globe.ultraDetailImageryMaxLevel ?? minLevel;
    const tileSize = appConfig.globe.ultraDetailImageryTileSize ?? 256;
    try {
      const provider = new Cesium.UrlTemplateImageryProvider({
        url,
        tilingScheme: new Cesium.GeographicTilingScheme(),
        rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90),
        minimumLevel: minLevel,
        maximumLevel: maxLevel,
        tileWidth: tileSize,
        tileHeight: tileSize,
        hasAlphaChannel: false,
      });
      provider.errorEvent?.addEventListener?.((err) => {
        console.warn("Blue Marble ultra tile imagery error:", err);
      });
      instrumentImageryProvider(provider, "dayUltra");
      const layer = viewer.imageryLayers.addImageryProvider(provider, 2);
      layer.alpha = 0.0;
      layer.show = false;
      blueMarbleUltraImageryLayer = layer;
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to create Blue Marble ultra tile imagery provider:", err);
    }
  }
  attachBlueMarbleUltraTilesImagery();

  function applyNightLayerSettings(layer, layerType = "base") {
    if (!layer) return;
    const useDayNightAlphaOnHighDetail = appConfig.globe.nightHighDetailUseDayNightAlpha === true;
    const isHighDetail = layerType === "detail" || layerType === "ultra";
    if (nightDiagnosticsEnabled && layerType === "base") {
      layer.dayAlpha = 1.0;
      layer.nightAlpha = 1.0;
      layer.nightBrightness = 1.0;
      layer.nightGamma = 1.0;
      return;
    }
    if (isHighDetail && !useDayNightAlphaOnHighDetail) {
      layer.dayAlpha = 1.0;
      layer.nightAlpha = 1.0;
    } else {
      layer.dayAlpha = appConfig.globe.nightImageryDayAlpha ?? 0.0;
      layer.nightAlpha = appConfig.globe.nightImageryNightAlpha ?? 1.0;
    }

    const applyToneToBaseOnly = appConfig.globe.nightImageryApplyToneToBaseOnly !== false;
    const baseBrightness = appConfig.globe.nightImageryNightBrightness ?? 1.0;
    const baseGamma = appConfig.globe.nightImageryNightGamma ?? 1.0;
    const detailBrightness = appConfig.globe.nightDetailImageryNightBrightness ?? 1.0;
    const detailGamma = appConfig.globe.nightDetailImageryNightGamma ?? 1.0;
    const ultraBrightness = appConfig.globe.nightUltraDetailImageryNightBrightness ?? 1.0;
    const ultraGamma = appConfig.globe.nightUltraDetailImageryNightGamma ?? 1.0;

    let brightness = baseBrightness;
    let gamma = baseGamma;
    if (layerType === "detail") {
      brightness = applyToneToBaseOnly ? 1.0 : detailBrightness;
      gamma = applyToneToBaseOnly ? 1.0 : detailGamma;
    } else if (layerType === "ultra") {
      brightness = applyToneToBaseOnly ? 1.0 : ultraBrightness;
      gamma = applyToneToBaseOnly ? 1.0 : ultraGamma;
    }

    if (Number.isFinite(brightness)) layer.nightBrightness = brightness;
    if (Number.isFinite(gamma)) layer.nightGamma = gamma;
  }

  function instrumentImageryProvider(provider, counterName) {
    if (!provider || provider._debugInstrumentedRequestImage) return;
    const originalRequestImage = provider.requestImage?.bind(provider);
    if (typeof originalRequestImage !== "function") return;
    const inflight = new Map();
    const recent = new Map();
    const recentResults = new Map();
    const dedupWindowMs = Math.max(50, appConfig.globe.imageryRequestDedupWindowMs ?? 800);
    provider._debugInstrumentedRequestImage = true;
    provider.requestImage = function instrumentedRequestImage(...args) {
      const [x, y, level] = args;
      const tileKey = `${level}/${x}/${y}`;
      const now = performance.now();
      if (recentResults.size) {
        for (const [key, entry] of recentResults) {
          if (now - entry.at > dedupWindowMs) {
            recentResults.delete(key);
            recent.delete(key);
          }
        }
      }
      if (counterName === "dayUltra") {
        blueMarbleUltraRequestCount += 1;
        if (inflight.has(tileKey) || recentResults.has(tileKey)) {
          blueMarbleUltraDedupHitCount += 1;
          return inflight.get(tileKey) ?? recentResults.get(tileKey).promise;
        }
        blueMarbleUltraUniqueRequestCount += 1;
      } else if (counterName === "nightUltra") {
        blackMarbleNightUltraRequestCount += 1;
        if (inflight.has(tileKey) || recentResults.has(tileKey)) {
          blackMarbleNightUltraDedupHitCount += 1;
          return inflight.get(tileKey) ?? recentResults.get(tileKey).promise;
        }
        blackMarbleNightUltraUniqueRequestCount += 1;
      }
      const result = originalRequestImage(...args);
      if (typeof result === "undefined") {
        return result;
      }
      const wrapped = Promise.resolve(result)
        .then((image) => {
          const completedAt = performance.now();
          recent.set(tileKey, completedAt);
          recentResults.set(tileKey, {
            at: completedAt,
            promise: Promise.resolve(image),
          });
          return image;
        })
        .finally(() => {
          inflight.delete(tileKey);
        });
      inflight.set(tileKey, wrapped);
      return wrapped;
    };
  }

  async function attachBlackMarbleNightBaseImagery() {
    if (appConfig.globe.nightImageryEnabled === false) return;
    if (blackMarbleNightBaseImageryLayer) return;
    const url = appConfig.globe.nightBaseImageryUrl;
    if (!url) return;

    const rectangle = Cesium.Rectangle.fromDegrees(-180, -90, 180, 90);
    const maxTextureSize = getMaxTextureSize();
    try {
      const { width, height } = await loadImageDimensions(url);
      if (width > maxTextureSize || height > maxTextureSize) {
        console.warn(
          `Skipping night base imagery ${url}: ${width}x${height} exceeds MAX_TEXTURE_SIZE ${maxTextureSize}`
        );
        return;
      }

      let provider;
      if (Cesium.SingleTileImageryProvider?.fromUrl) {
        provider = await Cesium.SingleTileImageryProvider.fromUrl(url, { rectangle });
      } else {
        provider = new Cesium.SingleTileImageryProvider({ url, rectangle });
      }

      blackMarbleNightBaseImageryLayer = viewer.imageryLayers.addImageryProvider(provider, 3);
      blackMarbleNightBaseImageryLayer.alpha = appConfig.globe.nightBaseImageryAlpha ?? 1.0;
      blackMarbleNightBaseImageryLayer.show = true;
      applyNightLayerSettings(blackMarbleNightBaseImageryLayer, "base");
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to create Black Marble night base provider:", err);
    }
  }
  attachBlackMarbleNightBaseImagery();

  async function attachBlackMarbleNightDetailTilesImagery() {
    if (appConfig.globe.nightImageryEnabled === false) return;
    if (appConfig.globe.nightDetailImageryEnabled === false) return;
    if (blackMarbleNightDetailImageryLayer) return;

    const url = appConfig.globe.nightDetailImageryTilesUrlTemplate;
    if (!url) return;
    const level = appConfig.globe.nightDetailImageryTileLevel ?? 5;
    const tileSize = appConfig.globe.nightDetailImageryTileSize ?? 256;

    try {
      const provider = new Cesium.UrlTemplateImageryProvider({
        url,
        tilingScheme: new Cesium.GeographicTilingScheme(),
        rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90),
        minimumLevel: level,
        maximumLevel: level,
        tileWidth: tileSize,
        tileHeight: tileSize,
        hasAlphaChannel: false,
      });
      provider.errorEvent?.addEventListener?.((err) => {
        console.warn("Black Marble night detail tile imagery error:", err);
      });
      blackMarbleNightDetailImageryLayer = viewer.imageryLayers.addImageryProvider(provider, 4);
      blackMarbleNightDetailImageryLayer.alpha = 0.0;
      blackMarbleNightDetailImageryLayer.show = false;
      applyNightLayerSettings(blackMarbleNightDetailImageryLayer, "detail");
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to create Black Marble night detail tile provider:", err);
    }
  }
  attachBlackMarbleNightDetailTilesImagery();

  async function attachBlackMarbleNightUltraTilesImagery() {
    if (appConfig.globe.nightImageryEnabled === false) return;
    if (appConfig.globe.nightUltraDetailImageryEnabled === false) return;
    if (blackMarbleNightUltraImageryLayers.length) return;

    const url = appConfig.globe.nightUltraDetailImageryTilesUrlTemplate;
    if (!url) return;
    const minLevel = appConfig.globe.nightUltraDetailImageryMinLevel ?? 7;
    const maxLevel = appConfig.globe.nightUltraDetailImageryMaxLevel ?? minLevel;
    const tileSize = appConfig.globe.nightUltraDetailImageryTileSize ?? 256;
    const regions = [
      { code: "A1", rectangle: Cesium.Rectangle.fromDegrees(-180, 0, -90, 90) },
      { code: "B1", rectangle: Cesium.Rectangle.fromDegrees(-90, 0, 0, 90) },
      { code: "C1", rectangle: Cesium.Rectangle.fromDegrees(0, 0, 90, 90) },
      { code: "D1", rectangle: Cesium.Rectangle.fromDegrees(90, 0, 180, 90) },
      { code: "A2", rectangle: Cesium.Rectangle.fromDegrees(-180, -90, -90, 0) },
      { code: "B2", rectangle: Cesium.Rectangle.fromDegrees(-90, -90, 0, 0) },
      { code: "C2", rectangle: Cesium.Rectangle.fromDegrees(0, -90, 90, 0) },
      { code: "D2", rectangle: Cesium.Rectangle.fromDegrees(90, -90, 180, 0) },
    ];

    try {
      for (const region of regions) {
        const provider = new Cesium.UrlTemplateImageryProvider({
          url,
          tilingScheme: new Cesium.GeographicTilingScheme(),
          rectangle: region.rectangle,
          minimumLevel: minLevel,
          maximumLevel: maxLevel,
          tileWidth: tileSize,
          tileHeight: tileSize,
          hasAlphaChannel: false,
        });
        provider.errorEvent?.addEventListener?.((err) => {
          console.warn(`Black Marble night ultra tile imagery error (${region.code}):`, err);
        });
        instrumentImageryProvider(provider, "nightUltra");
        const layer = viewer.imageryLayers.addImageryProvider(provider, 5);
        layer.alpha = 0.0;
        layer.show = false;
        applyNightLayerSettings(layer, "ultra");
        blackMarbleNightUltraImageryLayers.push(layer);
      }
      scheduleImageryBlendSync(true);
      requestRender();
    } catch (err) {
      console.warn("Failed to create Black Marble night ultra tile provider:", err);
    }
  }
  attachBlackMarbleNightUltraTilesImagery();

  function attachSunDot() {
    if (appConfig.globe.sunDotEnabled === false) return;
    if (!Cesium.Simon1994PlanetaryPositions?.computeSunPositionInEarthInertialFrame) return;
    const distanceScale = appConfig.globe.sunDotDistanceScale ?? 8.0;
    const scratchOut = new Cesium.Cartesian3();
    const scratchGlowOut = new Cesium.Cartesian3();

    sunDotEntity = viewer.entities.add({
      show: true,
      position: new Cesium.CallbackProperty(
        (time, result) => getSunWorldPositionForTime(viewer, time, skyDome, distanceScale, result || scratchOut),
        false
      ),
      billboard: {
        image: "./assets/sun/pia26681.png",
        width: 12,
        height: 12,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        color: Cesium.Color.fromBytes(255, 255, 255, 255),
      },
    });
    sunGlowEntity = viewer.entities.add({
      show: true,
      position: new Cesium.CallbackProperty(
        (time, result) => getSunWorldPositionForTime(viewer, time, skyDome, distanceScale, result || scratchGlowOut),
        false
      ),
      billboard: {
        image: "./assets/sun/halo.png",
        width: 72,
        height: 72,
        verticalOrigin: Cesium.VerticalOrigin.CENTER,
        horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
        color: Cesium.Color.fromBytes(255, 235, 168, 180),
      },
    });
    viewer.clock.onTick.addEventListener(() => {
      if (viewer.clock.shouldAnimate) {
        requestRender();
      }
    });
    requestRender();
  }
  attachSunDot();

  function attachMoon() {
    if (appConfig.globe.moonEnabled === false) return;
    if (appConfig.globe.moonUseProxyBody === true) {
      if (viewer.scene.moon) {
        viewer.scene.moon.show = false;
      }
      return;
    }
    if (!Cesium.Moon) return;

    try {
      viewer.scene.moon = new Cesium.Moon({
        show: true,
        textureUrl: appConfig.globe.moonTextureUrl,
        onlySunLighting: appConfig.globe.moonOnlySunLighting !== false,
      });
      requestRender();
    } catch (err) {
      console.warn("Failed to create Moon:", err);
    }
  }
  attachMoon();

  function ensureMoonAnchorEntity() {
    if (moonAnchorEntity) return;
    moonAnchorEntity = viewer.entities.add({
      show: false,
      position: new Cesium.CallbackProperty((time, result) => getMoonWorldPositionForTime(time, result), false),
    });
  }
  ensureMoonAnchorEntity();

  function attachMoonProxyBody() {
    if (appConfig.globe.moonEnabled === false || appConfig.globe.moonUseProxyBody !== true) return;
    if (typeof createSphericalTileLod !== "function") return;
    const initialSource = chooseMoonSource({ config: appConfig.globe });
    moonProxyPrimitive = createSphericalTileLod({
      viewer,
      inside: false,
      baseTextureUrl: initialSource.baseTextureUrl,
      urlTemplate: initialSource.urlTemplate,
      maxLevel: initialSource.maxLevel,
      cacheLimit: appConfig.globe.moonTileCacheLimit ?? 48,
      maxConcurrent: appConfig.globe.celestialTileMaxConcurrent ?? 4,
      lit: true,
      requestRender,
    });
    requestRender();
  }
  attachMoonProxyBody();

  function attachMoonDebugMarker() {
    moonDebugMarkerEntity = null;
    requestRender();
  }
  attachMoonDebugMarker();

  let moonSurfaceMode = "natural";
  let moonGeologyTextureUrl = appConfig.globe.moonGeologyTextureUrl || null;
  let moonGeologyTextureHiResUrl = appConfig.globe.moonGeologyTextureHiResUrl || moonGeologyTextureUrl;
  let moonProxySourceKey = null;
  function setMoonSurfaceMode(mode = "natural") {
    moonSurfaceMode = mode === "geology" ? "geology" : "natural";
    requestRender();
    return moonSurfaceMode;
  }
  function setMoonGeologyTextureUrls(next = {}) {
    if (typeof next.base === "string" && next.base) moonGeologyTextureUrl = next.base;
    if (typeof next.hiRes === "string" && next.hiRes) moonGeologyTextureHiResUrl = next.hiRes;
    else if (next.base) moonGeologyTextureHiResUrl = next.base;
    requestRender();
    return { base: moonGeologyTextureUrl, hiRes: moonGeologyTextureHiResUrl };
  }
  document.addEventListener("motherworld:moon-surface", (event) => {
    const detail = event?.detail || {};
    if (detail.base || detail.hiRes) setMoonGeologyTextureUrls(detail);
    setMoonSurfaceMode(detail.mode);
  });

  viewer.scene.preRender.addEventListener((scene, time) => {
    const eclipseActive = getCurrentLunarEclipseTintFactor(time, appConfig) > 0.001;
    const naturalMoonTextureUrl = eclipseActive
      ? (appConfig.globe.moonEclipseTextureHiResUrl || appConfig.globe.moonTextureHiResUrl || appConfig.globe.moonTextureUrl)
      : (cameraAnchorMode === "moon"
          ? (appConfig.globe.moonTextureHiResUrl || appConfig.globe.moonTextureUrl)
          : appConfig.globe.moonTextureUrl);
    const geologyTextureUrl = cameraAnchorMode === "moon"
      ? (moonGeologyTextureHiResUrl || moonGeologyTextureUrl)
      : moonGeologyTextureUrl;
    const targetMoonTextureUrl = moonSurfaceMode === "geology" && geologyTextureUrl
      ? geologyTextureUrl
      : naturalMoonTextureUrl;
    const moonDisplayScale =
      cameraAnchorMode === "moon"
        ? (appConfig.globe.moonMoonViewScale ?? 1.0)
        : (appConfig.globe.moonEarthViewScale ?? 4.0);
    const moonPosition = getMoonWorldPositionForTime(time, moonWorldPositionScratch);
    const earthRadius = viewer.scene.globe?.ellipsoid?.maximumRadius || Cesium.Ellipsoid.WGS84.maximumRadius;
    const earthTagPosition =
      cameraAnchorMode !== "earth"
        ? getBodyTagScreenPosition(
            viewer,
            Cesium.Cartesian3.ZERO,
            earthRadius,
            earthTagAnchorScratch,
            earthTagDirectionScratch
          )
        : null;

    const moonRadiusForDisplay = Cesium.Ellipsoid.MOON.maximumRadius * moonDisplayScale;
    const moonDistance = Cesium.Cartesian3.distance(viewer.camera.positionWC, moonPosition);
    const moonOccludedByEarth = isWorldPositionOccludedByEarth(viewer, moonPosition);
    const moonTagPosition =
      cameraAnchorMode !== "moon" && !moonOccludedByEarth
        ? getBodyTagScreenPosition(
            viewer,
            moonPosition,
            moonRadiusForDisplay,
            moonTagAnchorScratch,
            moonTagDirectionScratch
          )
        : null;
    let sunTagPosition = null;
    if (appConfig.globe.sunDotEnabled !== false) {
      const sunWorldPosition = getSunWorldPositionForTime(
        viewer,
        time,
        skyDome,
        appConfig.globe.sunDotDistanceScale ?? 8.0,
        sunScreenPositionScratch
      );
      const sunOccluded = isWorldPositionOccludedByEarthOrMoon(viewer, sunWorldPosition);
      if (sunDotEntity) {
        sunDotEntity.show = !sunOccluded;
      }
      if (sunGlowEntity) {
        sunGlowEntity.show = !sunOccluded;
      }
      sunTagPosition = sunOccluded ? null : worldToCssScreenPosition(viewer, sunWorldPosition);
    }

    lastMoonTagVisible = Boolean(moonTagPosition);
    onBodyTagsUpdate?.({
      earth: earthTagPosition,
      moon: moonTagPosition,
      sun: sunTagPosition,
    });
    onEarthTagUpdate?.(earthTagPosition);
    onMoonTagUpdate?.(moonTagPosition);
    const frustum = viewer.camera.frustum;
    if (frustum && Number.isFinite(frustum.far)) {
      const skyDomeRadius = Number(skyDome?.getStateForDebug?.()?.radiusMeters) || 0;
      const requiredFar = Math.max(
        2.0e9,
        moonDistance + Cesium.Ellipsoid.MOON.maximumRadius * Math.max(1, moonDisplayScale) * 12,
        Cesium.Cartesian3.magnitude(viewer.camera.positionWC) + earthRadius + 10_000_000,
        skyDomeRadius + 10_000_000
      );
      frustum.far = requiredFar;
      if (typeof frustum.near === "number") {
        frustum.near = Math.max(frustum.near, 1.0);
      }
    }
    if (viewer.scene.moon && appConfig.globe.moonUseProxyBody !== true) {
      if (viewer.scene.moon.textureUrl !== targetMoonTextureUrl) viewer.scene.moon.textureUrl = targetMoonTextureUrl;
      const builtinShadow = getMoonShadowPolicy({ cameraMoonDistance: moonDistance, displayRadius: Cesium.Ellipsoid.MOON.maximumRadius * moonDisplayScale, config: appConfig.globe });
      viewer.scene.moon.onlySunLighting = appConfig.globe.moonOnlySunLighting !== false && builtinShadow.shadowFade >= 0.999;
      viewer.scene.moon.show = appConfig.globe.moonEnabled !== false;
    }
    if (moonProxyPrimitive?.update) {
      const source = chooseMoonSource({
        mode: moonSurfaceMode,
        eclipse: eclipseActive,
        anchorMode: cameraAnchorMode,
        config: appConfig.globe,
        custom: { base: moonGeologyTextureUrl, hiRes: moonGeologyTextureHiResUrl },
      });
      if (source.key !== moonProxySourceKey) {
        moonProxySourceKey = source.key;
        moonProxyPrimitive.setSource?.(source);
      }
      const sunDirectionFixed = getSunDirectionFixedForTime(time, moonSunDirScratch);
      Cesium.Matrix4.multiplyByPointAsVector(viewer.camera.viewMatrix, sunDirectionFixed, moonSunDirectionEyeScratch);
      Cesium.Cartesian3.normalize(moonSunDirectionEyeScratch, moonSunDirectionEyeScratch);
      const moonRadius = Cesium.Ellipsoid.MOON.maximumRadius * moonDisplayScale;
      const shadow = getMoonShadowPolicy({ cameraMoonDistance: moonDistance, displayRadius: moonRadius, config: appConfig.globe });
      moonScaleVectorScratch.x = moonRadius;
      moonScaleVectorScratch.y = moonRadius;
      moonScaleVectorScratch.z = moonRadius;
      Cesium.Matrix4.fromScale(moonScaleVectorScratch, moonScaleMatrixScratch);
      Cesium.Matrix4.multiply(computeMoonModelMatrix(time, moonModelMatrixScratch), moonScaleMatrixScratch, moonScaledModelMatrixScratch);
      const cameraToMoon = Cesium.Cartesian3.subtract(moonPosition, viewer.camera.positionWC, moonCameraDirectionScratch);
      const cameraToEarth = Cesium.Cartesian3.negate(viewer.camera.positionWC, moonCameraRightScratch);
      const cameraMoonDistance = Cesium.Cartesian3.magnitude(cameraToMoon);
      const cameraEarthDistance = Cesium.Cartesian3.magnitude(cameraToEarth);
      const angularSeparation = Cesium.Cartesian3.angleBetween(cameraToMoon, cameraToEarth);
      const earthAngularRadius = Math.asin(Math.min(1, earthRadius / Math.max(earthRadius, cameraEarthDistance)));
      const moonAngularRadius = Math.asin(Math.min(1, moonRadius / Math.max(moonRadius, cameraMoonDistance)));
      const wholeMoonOccluded = cameraEarthDistance < cameraMoonDistance && earthAngularRadius >= angularSeparation + moonAngularRadius;
      moonProxyPrimitive.update({
        modelMatrix: moonScaledModelMatrixScratch,
        show: appConfig.globe.moonEnabled !== false && appConfig.globe.moonUseProxyBody === true && !wholeMoonOccluded,
        sunDirectionEC: moonSunDirectionEyeScratch,
        ambient: shadow.ambient,
      });
    }
    if (cameraAnchorMode === "moon" && moonAnchorLockActive) {
      const localPosition = Cesium.Cartesian3.clone(viewer.camera.position, moonAnchorLocalPositionScratch);
      const localRange = Cesium.Cartesian3.magnitude(localPosition);
      const clampedRange = Math.max(ssc.minimumZoomDistance, localRange || moonAnchorRange);
      if (Math.abs(clampedRange - localRange) > 1) {
        if (localRange > 1e-3) {
          Cesium.Cartesian3.normalize(localPosition, localPosition);
          Cesium.Cartesian3.multiplyByScalar(localPosition, clampedRange, localPosition);
        } else {
          localPosition.x = 0;
          localPosition.y = 0;
          localPosition.z = clampedRange;
        }
      }
      // Use the Moon's own body-fixed frame, not an Earth-derived ENU frame.
      // ENU at the Moon's world position introduces artificial axis constraints
      // because its "up" is based on the Earth-centered radial vector.
      const moonTransform = computeMoonModelMatrix(time, moonAnchorTransformScratch);
      viewer.camera.lookAtTransform(moonTransform, localPosition);
    }
  });

  function startRealtimeClock() {
    if (appConfig.globe.realtimeClockEnabled === false) return;
    const intervalMs = Math.max(250, appConfig.globe.realtimeClockUpdateMs ?? 1000);
    const syncClock = () => {
      viewer.clock.currentTime = Cesium.JulianDate.now();
      requestRender();
    };
    syncClock();
    window.setInterval(syncClock, intervalMs);
  }
  startRealtimeClock();

  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(
      appConfig.globe.initialView.lon,
      appConfig.globe.initialView.lat,
      appConfig.globe.initialView.height
    ),
  });

  const selectionAccent = colorFromCss("#7dd3fc");
  const selectionAccent2 = colorFromCss("#80ed99");

  let marineOverviewDataSource = null;
  let marineDataSource = null;
  let lakesOverviewDataSource = null;
  let lakesDataSource = null;
  let landOverviewDataSource = null;
  let baseDataSource = null;
  let activeRealmDataSource = null;
  let activeRealmSlug = null;
  let activeRealmLodLevel = null;
  let overviewLodActive = false;
  let regionDatasetMode = "land";
  let startupLandOverviewUrl = null;
  let startupLandUrl = null;
  let startupMarineOverviewUrl = null;
  let startupMarineUrl = null;
  let startupLakesOverviewUrl = null;
  let startupLakesUrl = null;
  let hoveredEntity = null;
  let hoverRaf = 0;
  let hoverTimer = 0;
  let lastHoverAt = -Infinity;
  let cameraMoving = false;
  let pointerDown = false;
  let pendingHoverPosition = null;
  let visibleRegionSources = [];
  let globalGeometryReady = false;
  let globalLayerGeneration = 0;
  const globalLayerLoads = new Map();
  let realmLoadToken = 0;
  let pulseFrame = 0;
  let pulse = null;
  let selectionOverlayEntities = [];
  let lastPickedPosition = null;
  let selectionDetailLoadRaf = 0;
  let cameraCullTimer = 0;
  let lastCullRunAt = 0;
  let viewCullStats = {
    marineTotal: 0,
    marineVisible: 0,
    baseTotal: 0,
    baseVisible: 0,
    detailTotal: 0,
    detailVisible: 0,
  };

  function isMarineOnlyDatasetMode() {
    return regionDatasetMode === "marine";
  }

  function isCombinedDatasetMode() {
    return regionDatasetMode === "combined";
  }

  function isMarineVisibleDatasetMode() {
    return regionDatasetMode === "marine" || regionDatasetMode === "combined";
  }

  function isLandVisibleDatasetMode() {
    return regionDatasetMode === "land" || regionDatasetMode === "combined";
  }

  function syncOverviewLodState() {
    const enterHeight = appConfig.globe.overviewLodEnterHeight ?? 7_000_000;
    const exitHeight = appConfig.globe.overviewLodExitHeight ?? Math.round(enterHeight * 0.9);
    const h = getCameraHeightMeters();
    if (overviewLodActive) {
      if (h <= exitHeight) overviewLodActive = false;
    } else if (h >= enterHeight) {
      overviewLodActive = true;
    }
    return overviewLodActive;
  }

  function getActiveLandGlobalDataSource() {
    return overviewLodActive
      ? landOverviewDataSource || baseDataSource
      : baseDataSource || landOverviewDataSource;
  }

  function getActiveMarineGlobalDataSource() {
    return overviewLodActive
      ? marineOverviewDataSource || marineDataSource
      : marineDataSource || marineOverviewDataSource;
  }

  function getActiveLakesGlobalDataSource() {
    return overviewLodActive
      ? lakesOverviewDataSource || lakesDataSource
      : lakesDataSource || lakesOverviewDataSource;
  }
  let lastDuplicateVisible = 0;
  let lastFlatViolationCount = 0;
  let lastMoonVisible = false;
  let lastMoonDistanceKm = null;
  let lastMoonTagVisible = false;
  let cameraAnchorMode = "earth";
  let earthAnchorViewState = null;
  let anchorReleaseRaf = 0;
  let moonAnchorRange = Cesium.Ellipsoid.MOON.maximumRadius * Math.max(2.5, appConfig.globe.moonAnchorRangeMultiplier ?? 7.0);
  let moonAnchorLockActive = false;

  const regionIndexCache = new Map();
  const regionNameCache = new Map();
  const realmFilesLod1 = new Map();
  const marineEntitiesById = new Map();
  const baseEntitiesById = new Map();
  const baseEntitiesByRealm = new Map();
  const debugOptions = makeDefaultDebugOptions();
  let regionFillEnabled = appConfig.styling.fillEnabled !== false;

  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

  function requestRender() {
    viewer.scene.requestRender?.();
  }

  function setRenderResolutionScale(scale) {
    const next = Math.max(0.35, Math.min(1, scale));
    if (Math.abs((viewer.resolutionScale ?? 1) - next) < 0.001) return;
    viewer.resolutionScale = next;
    requestRender();
  }

  skyDome = createSkyDomeController({
    viewer,
    appConfig,
    requestRender,
    getMinimumRadiusForTime: (time) => {
      if (appConfig.globe.skyDomeEnabled === false || appConfig.globe.moonEnabled === false) {
        return 0;
      }
      const moonPosition = getMoonWorldPositionForTime(time, moonSkyDomePositionScratch);
      const moonDistance = Cesium.Cartesian3.distance(viewer.camera.positionWC, moonPosition);
      const moonDisplayScale = Math.max(
        1,
        appConfig.globe.moonEarthViewScale ?? 1,
        appConfig.globe.moonMoonViewScale ?? 1
      );
      const moonRadius = Cesium.Ellipsoid.MOON.maximumRadius * moonDisplayScale;
      return moonDistance + moonRadius + 5_000_000;
    },
  });

  function loadedDataSources() {
    const out = [];
    if (isLandVisibleDatasetMode()) {
      if (landOverviewDataSource) out.push(landOverviewDataSource);
      if (baseDataSource) out.push(baseDataSource);
      if (activeRealmDataSource) out.push(activeRealmDataSource);
    }
    if (isMarineVisibleDatasetMode()) {
      if (marineOverviewDataSource) out.push(marineOverviewDataSource);
      if (marineDataSource) out.push(marineDataSource);
    }
    if (lakesOverviewDataSource) out.push(lakesOverviewDataSource);
    if (lakesDataSource) out.push(lakesDataSource);
    return out;
  }

  // Keep inactive LODs cached, but leave them out of camera and visibility scans.
  function activeDataSources() {
    const sources = [];
    if (isLandVisibleDatasetMode()) {
      sources.push(getActiveLandGlobalDataSource());
      if (!debugOptions.lod0Only && !debugOptions.disableLodSwap) sources.push(activeRealmDataSource);
    }
    if (isMarineVisibleDatasetMode()) sources.push(getActiveMarineGlobalDataSource());
    sources.push(getActiveLakesGlobalDataSource());
    return sources.filter(Boolean);
  }

  function buildFallbackRegionMeta(entity, sourceTag, explicitRegionId, now = Cesium.JulianDate.now()) {
    const rawName =
      (typeof entity?.name === "string" && entity.name) ||
      getEntityPropertyValue(entity, "name", now) ||
      null;
    const sourceLabel =
      sourceTag === "marine" || sourceTag === "marineOverview"
        ? "Marine"
        : sourceTag === "lakes" || sourceTag === "lakesOverview"
          ? "Lake"
          : "Region";
    const safeName = rawName || `${sourceLabel} ${entity?.id || "unknown"}`;
    const fallbackId =
      explicitRegionId ||
      getEntityPropertyValue(entity, "FID", now) ||
      `${sourceTag}_${String(safeName).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
    const centerLon = Number(getEntityPropertyValue(entity, "centerLon", now));
    const centerLat = Number(getEntityPropertyValue(entity, "centerLat", now));
    const areaKm2 = Number(getEntityPropertyValue(entity, "areaKm2", now));
    const realm = getEntityPropertyValue(entity, "realm", now) || "Unknown";
    const layerType = getEntityPropertyValue(entity, "layerType", now) || "";
    const biome = getEntityPropertyValue(entity, "biome", now) || "Unknown";
    const province = getEntityPropertyValue(entity, "province", now) || "";

    return {
      id: String(fallbackId),
      ecoId: getEntityPropertyValue(entity, "ecoId", now) ?? fallbackId,
      name: safeName,
      biomeNum: Number(getEntityPropertyValue(entity, "biomeNum", now)),
      biome,
      realm,
      ecoBiomeCode: layerType || getEntityPropertyValue(entity, "ecoBiomeCode", now) || "",
      nnhCode: getEntityPropertyValue(entity, "nnhCode", now) ?? null,
      nnhName: getEntityPropertyValue(entity, "nnhName", now) || province || "",
      areaKm2: Number.isFinite(areaKm2) ? areaKm2 : 0,
      center:
        Number.isFinite(centerLon) && Number.isFinite(centerLat)
          ? { lon: centerLon, lat: centerLat }
          : null,
      realmSlug: getEntityPropertyValue(entity, "realmSlug", now) || null,
      layerType,
      province,
      isMarine: sourceTag === "marine" || sourceTag === "marineOverview",
      isLake: sourceTag === "lakes" || sourceTag === "lakesOverview",
    };
  }

  function shouldRenderEcoregions() {
    if (cameraAnchorMode === "moon") return false;
    const hideAboveHeight = appConfig.globe.hideEcoregionsAboveHeight ?? 250_000_000;
    return getCameraHeightMeters() < hideAboveHeight;
  }

  function buildEntityIndex(dataSource, sourceTag) {
    const byId = new Map();
    const byRegionId = new Map();
    const byRealm = new Map();
    const now = Cesium.JulianDate.now();

    for (const entity of dataSource.entities.values) {
      if (!entity.polygon) continue;
      let regionId = getEntityRegionId(entity);
      const biomeNum = Number(getEntityPropertyValue(entity, "biomeNum", now));
      const realmSlug = getEntityRealmSlug(entity);
      let regionMeta = regionId ? regionIndexCache.get(regionId) : null;
      if (!regionMeta) {
        const entityName =
          (typeof entity?.name === "string" && entity.name) ||
          getEntityPropertyValue(entity, "name", now) ||
          null;
        if (entityName && regionNameCache.has(entityName)) {
          regionMeta = regionNameCache.get(entityName);
          regionId = regionMeta?.id || regionId;
        }
      }
      if (!regionMeta) {
        regionMeta = buildFallbackRegionMeta(entity, sourceTag, regionId, now);
        regionId = regionMeta.id;
        regionIndexCache.set(regionId, regionMeta);
        if (regionMeta.name && !regionNameCache.has(regionMeta.name)) {
          regionNameCache.set(regionMeta.name, regionMeta);
        }
      }
      const isMarine = regionMeta?.isMarine === true || sourceTag === "marine" || sourceTag === "marineOverview";
      const isLake = regionMeta?.isLake === true || sourceTag === "lakes" || sourceTag === "lakesOverview";
      const baseColor = isLake
        ? getLakeRegionBaseColor(regionMeta)
        : isMarine
          ? getMarineRegionBaseColor(regionMeta)
          : colorFromCss(getBiomeColor(biomeNum));

      entity._regionId = regionId;
      entity._regionMeta = regionMeta || null;
      entity._realmSlug = realmSlug;
      entity._sourceTag = sourceTag;
      entity._isMarine = isMarine;
      entity._isLake = isLake;
      entity._displayHeight =
        sourceTag === "marine" || sourceTag === "marineOverview"
          ? 2_500
          : sourceTag === "lakes" || sourceTag === "lakesOverview"
            ? 500
            : sourceTag === "detail"
              ? 900
              : 750;
      entity._baseColor = baseColor;
      entity._outlineColor = isLake
        ? Cesium.Color.fromCssColorString("#dbeafe").withAlpha(0.68)
        : isMarine
          ? Cesium.Color.fromCssColorString("#dbeafe").withAlpha(0.42)
          : Cesium.Color.WHITE.withAlpha(0.65);
      entity._forceOutlineOnly = false;
      const hierarchy =
        entity.polygon.hierarchy?.getValue?.(Cesium.JulianDate.now()) ?? entity.polygon.hierarchy ?? null;
      const hierarchyPositions = collectHierarchyPositions(hierarchy, []);
      entity._suppressOutline = isMarine && polygonHierarchyTouchesDateline(hierarchy, 0.25);
      entity._fillAlpha = isLake
        ? Math.max(appConfig.styling.fillAlpha ?? 0.25, 0.68)
        : isMarine
          ? Math.max(appConfig.styling.fillAlpha ?? 0.25, 0.58)
          : Math.max(appConfig.styling.fillAlpha ?? 0.25, 0.42);
      entity._cameraVisible = true;
      if (regionMeta?.center && Number.isFinite(regionMeta.center.lon) && Number.isFinite(regionMeta.center.lat)) {
        entity._centerCartesian = Cesium.Cartesian3.fromDegrees(regionMeta.center.lon, regionMeta.center.lat, 0);
      } else {
        entity._centerCartesian = null;
      }
      entity._areaKm2 = Number.isFinite(Number(regionMeta?.areaKm2)) ? Number(regionMeta.areaKm2) : null;
      entity._cullAnchorCartesian = entity._centerCartesian;
      let cullRadiusMeters = Number.isFinite(entity._areaKm2)
        ? Math.max(25_000, Math.sqrt(Math.max(1, entity._areaKm2)) * 1_000)
        : 25_000;
      if (hierarchyPositions.length >= 3) {
        const average = new Cesium.Cartesian3();
        for (const position of hierarchyPositions) {
          Cesium.Cartesian3.add(average, position, average);
        }
        Cesium.Cartesian3.multiplyByScalar(average, 1 / hierarchyPositions.length, average);
        const geodeticAnchor =
          viewer.scene.globe.ellipsoid.scaleToGeodeticSurface(average, new Cesium.Cartesian3()) ?? average;
        entity._cullAnchorCartesian = geodeticAnchor;
        let maxDistance = 0;
        for (const position of hierarchyPositions) {
          maxDistance = Math.max(maxDistance, Cesium.Cartesian3.distance(geodeticAnchor, position));
        }
        if (Number.isFinite(maxDistance) && maxDistance > 0) {
          cullRadiusMeters = Math.max(cullRadiusMeters, maxDistance * 1.18);
        }
      }
      entity._cullRadiusMeters = cullRadiusMeters;

      entity.polygon.material = colorWithAlpha(entity._baseColor, entity._fillAlpha);
      entity.polygon.fill = true;
      entity.polygon.outline = entity._suppressOutline !== true;
      entity.polygon.outlineColor = entity._outlineColor;
      entity.polygon.perPositionHeight = false;
      entity.polygon.height = entity._displayHeight;
      entity.polygon.extrudedHeight = undefined;
      entity.polygon.closeTop = true;
      entity.polygon.closeBottom = false;
      entity.polygon.arcType = Cesium.ArcType.GEODESIC;

      byId.set(regionId, entity);
      if (regionId) {
        if (!byRegionId.has(regionId)) byRegionId.set(regionId, []);
        byRegionId.get(regionId).push(entity);
      }
      if (realmSlug) {
        if (!byRealm.has(realmSlug)) byRealm.set(realmSlug, []);
        byRealm.get(realmSlug).push(entity);
      }
    }

    dataSource._entityIndex = { byId, byRegionId, byRealm };
  }

  function getRegionRecord(entity) {
    if (entity?._regionMeta) return entity._regionMeta;
    const regionId = getEntityRegionId(entity);
    return regionIndexCache.get(regionId) || null;
  }

  function getMoonRecord() {
    return {
      id: MOON_SELECTION_ID,
      ecoId: "MOON",
      name: "Moon",
      biome: "Natural satellite",
      biomeNum: null,
      realm: "Earth-Moon system",
      ecoBiomeCode: "LUNA",
      nnhCode: null,
      nnhName: "Lunar body",
      areaKm2: 37_932_300,
      center: null,
      realmSlug: null,
    };
  }

  function getOpenOceanRecord() {
    return {
      id: OPEN_OCEAN_SELECTION_ID,
      ecoId: "OCEAN",
      name: "Open Ocean",
      biome: "Open ocean",
      biomeNum: null,
      realm: "Global ocean",
      ecoBiomeCode: "OCEAN",
      nnhCode: null,
      nnhName: "Pelagic waters beyond mapped ecoregions",
      areaKm2: 361_132_000,
      center: null,
      realmSlug: null,
      isMarine: true,
      isSynthetic: true,
    };
  }

  function getSelectedRegionRecord() {
    if (selectedRegionId === MOON_SELECTION_ID) return getMoonRecord();
    if (selectedRegionId === OPEN_OCEAN_SELECTION_ID) return getOpenOceanRecord();
    if (!selectedRegionId) return null;
    return regionIndexCache.get(selectedRegionId) || null;
  }

  function getEntitiesForRegionId(regionId) {
    if (!regionId) return [];
    if (isLandVisibleDatasetMode() && activeRealmDataSource?._entityIndex?.byRegionId?.has(regionId)) {
      return activeRealmDataSource._entityIndex.byRegionId.get(regionId);
    }
    const activeLandDataSource = isLandVisibleDatasetMode() ? getActiveLandGlobalDataSource() : null;
    if (activeLandDataSource?._entityIndex?.byRegionId?.has(regionId)) {
      return activeLandDataSource._entityIndex.byRegionId.get(regionId);
    }
    const activeMarineDataSource = isMarineVisibleDatasetMode() ? getActiveMarineGlobalDataSource() : null;
    if (activeMarineDataSource?._entityIndex?.byRegionId?.has(regionId)) {
      return activeMarineDataSource._entityIndex.byRegionId.get(regionId);
    }
    const activeLakesDataSource = getActiveLakesGlobalDataSource();
    if (activeLakesDataSource?._entityIndex?.byRegionId?.has(regionId)) {
      return activeLakesDataSource._entityIndex.byRegionId.get(regionId);
    }
    return [];
  }

  function getSelectedEntities() {
    return getEntitiesForRegionId(selectedRegionId);
  }

  function pulseStrength() {
    if (!pulse || !selectedRegionId || pulse.regionId !== selectedRegionId) return 0;
    const elapsed = performance.now() - pulse.startedAt;
    const t = elapsed / pulse.durationMs;
    if (t >= 1) return 0;
    // single bump
    return Math.sin(Math.PI * t) * (1 - t) * 0.55;
  }

  function isEntityFilteredOutByDebug(entity) {
    const targetId = (debugOptions.regionId || "").trim();
    return Boolean(targetId) && getEntityRegionId(entity) !== targetId;
  }

  function shouldShowBaseEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveLandGlobalDataSource() !== baseDataSource) return false;
    if (activeRealmSlug && !debugOptions.disableLodSwap && !debugOptions.lod0Only) {
      return getEntityRealmSlug(entity) !== activeRealmSlug;
    }
    return true;
  }

  function shouldShowLandOverviewEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveLandGlobalDataSource() !== landOverviewDataSource) return false;
    if (activeRealmSlug && !debugOptions.disableLodSwap && !debugOptions.lod0Only) return false;
    return true;
  }

  function shouldShowDetailEntity(entity) {
    if (!activeRealmDataSource || activeRealmDataSource.show === false) return false;
    if (debugOptions.lod0Only) return false;
    if (debugOptions.disableLodSwap) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    return true;
  }

  function shouldShowMarineEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveMarineGlobalDataSource() !== marineDataSource) return false;
    return true;
  }

  function shouldShowMarineOverviewEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveMarineGlobalDataSource() !== marineOverviewDataSource) return false;
    return true;
  }

  function shouldShowLakesEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveLakesGlobalDataSource() !== lakesDataSource) return false;
    return true;
  }

  function shouldShowLakesOverviewEntity(entity) {
    if (debugOptions.lod1Only) return false;
    if (isEntityFilteredOutByDebug(entity)) return false;
    if (getActiveLakesGlobalDataSource() !== lakesOverviewDataSource) return false;
    return true;
  }

  function applyEntityVisibility(entity) {
    if (!entity) return;
    if (entity._sourceTag === "lakesOverview") {
      entity.show = shouldShowLakesOverviewEntity(entity);
      return;
    }
    if (entity._sourceTag === "lakes") {
      entity.show = shouldShowLakesEntity(entity);
      return;
    }
    if (entity._sourceTag === "marineOverview") {
      entity.show = shouldShowMarineOverviewEntity(entity);
      return;
    }
    if (entity._sourceTag === "marine") {
      entity.show = shouldShowMarineEntity(entity);
      return;
    }
    if (entity._sourceTag === "landOverview") {
      entity.show = shouldShowLandOverviewEntity(entity);
      return;
    }
    if (entity._sourceTag === "base") {
      entity.show = shouldShowBaseEntity(entity);
      return;
    }
    if (entity._sourceTag === "detail") {
      entity.show = shouldShowDetailEntity(entity);
    }
  }

  function applyAllVisibility() {
    syncOverviewLodState();
    const sources = shouldRenderEcoregions() ? activeDataSources() : [];
    const sourcesChanged = sources.length !== visibleRegionSources.length ||
      sources.some((ds, index) => ds !== visibleRegionSources[index]);
    // These sources are lookup collections, never attached to Cesium's renderer.
    for (const ds of [landOverviewDataSource, baseDataSource, activeRealmDataSource,
      marineOverviewDataSource, marineDataSource, lakesOverviewDataSource, lakesDataSource]) {
      if (ds) ds.show = sources.includes(ds);
    }
    if (sourcesChanged) {
      visibleRegionSources = sources;
      clearHover();
      syncSelectionOverlay();
    }
    for (const overlay of selectionOverlayEntities) {
      applyEntityVisibility(overlay._sourceEntity);
      overlay.show = sources.length > 0 && overlay._sourceEntity.show !== false;
    }
    viewCullStats = { marineTotal: 0, marineVisible: 0, baseTotal: 0, baseVisible: 0, detailTotal: 0, detailVisible: 0 };
    for (const overlay of selectionOverlayEntities) {
      const tag = overlay._sourceEntity?._sourceTag;
      const category = tag === "detail" ? "detail" : tag?.startsWith("marine") ? "marine" : "base";
      viewCullStats[`${category}Total`] += 1;
      if (overlay.show) viewCullStats[`${category}Visible`] += 1;
    }
    requestRender();
  }

  function applyEntityStyle(entity) {
    if (!entity?.polygon) return;

    const entityRegionId = getEntityRegionId(entity);
    const isSelected = Boolean(selectedRegionId && entityRegionId === selectedRegionId);
    const hoverHighlightEnabled = appConfig.styling.hoverHighlightEnabled !== false;
    const isHovered = hoverHighlightEnabled && hoveredEntity === entity && !isSelected;
    const selectionActive = Boolean(selectedRegionId);
    const selectionAffectsBase = appConfig.styling.selectionOverlayEnabled === false;
    const fillAlphaBase = entity._fillAlpha ?? (appConfig.styling.fillAlpha ?? 0.78);
    const hoverAlpha = appConfig.styling.hoverAlpha ?? 0.94;
    const selectedAlpha = appConfig.styling.selectedAlpha ?? 0.98;
    const p = pulseStrength();

    let fillColor = Cesium.Color.clone(entity._baseColor);
    let fillAlpha = fillAlphaBase;

    if (selectionActive && selectionAffectsBase) {
      if (isSelected) {
        fillColor = blend(fillColor, selectionAccent, 0.28 + p * 0.3);
        fillColor = blend(fillColor, selectionAccent2, 0.10 + p * 0.15);
        fillColor = brighten(fillColor, 0.12 + p * 0.35);
        fillAlpha = selectedAlpha;
      }
    } else if (isHovered) {
      fillColor = brighten(fillColor, 0.16);
      fillAlpha = hoverAlpha;
    }

    const outlineOnly = Boolean(debugOptions.outlineOnly);
    const fillOnly = Boolean(debugOptions.fillOnly);
    const showFill = regionFillEnabled && !outlineOnly && entity._forceOutlineOnly !== true;
    const showOutline = !fillOnly && entity._suppressOutline !== true;
    let outlineColor = Cesium.Color.clone(entity._outlineColor ?? Cesium.Color.WHITE.withAlpha(0.35));

    if (isHovered) {
      outlineColor = Cesium.Color.WHITE.withAlpha(entity._isMarine ? 0.6 : 0.7);
    } else if (isSelected && !showFill) {
      outlineColor = selectionAccent.withAlpha(0.92);
    }

    entity.polygon.material = colorWithAlpha(fillColor, showFill ? fillAlpha : 0.0);
    entity.polygon.outline = showOutline;
    entity.polygon.outlineColor = outlineColor;
  }

  function refreshAllStyles() {
    syncSelectionOverlay();
    requestRender();
  }

  function refreshChangedStyles(previousHovered) {
    const changed = new Set([previousHovered, hoveredEntity]);
    for (const entity of getSelectedEntities()) {
      changed.add(entity);
    }
    for (const entity of changed) {
      if (entity) applyEntityStyle(entity);
    }
    requestRender();
  }

  function refreshRegionStylesById(...regionIds) {
    const changed = new Set();
    for (const regionId of regionIds) {
      for (const entity of getEntitiesForRegionId(regionId)) {
        if (entity) changed.add(entity);
      }
    }
    for (const entity of changed) {
      applyEntityStyle(entity);
    }
    if (changed.size > 0) requestRender();
  }

  function clearSelectionOverlay() {
    if (!selectionOverlayEntities.length) return;
    for (const overlayEntity of selectionOverlayEntities) {
      viewer.entities.remove(overlayEntity);
    }
    selectionOverlayEntities = [];
    requestRender();
  }

  function syncSelectionOverlay() {
    clearSelectionOverlay();
    if (!shouldRenderEcoregions()) return;
    if (!selectedRegionId) return;
    if (selectedRegionId === MOON_SELECTION_ID) return;

    const sourceEntities = getEntitiesForRegionId(selectedRegionId);
    if (!sourceEntities.length) return;

    for (const sourceEntity of sourceEntities) {
      if (!sourceEntity?.polygon) continue;
      applyEntityVisibility(sourceEntity);
      if (!sourceEntity.show) continue;
      const baseColor = Cesium.Color.clone(sourceEntity._baseColor ?? selectionAccent);
      let fillColor = blend(baseColor, selectionAccent, 0.32);
      fillColor = blend(fillColor, selectionAccent2, 0.12);
      fillColor = brighten(fillColor, 0.14);
      fillColor = colorWithAlpha(fillColor, appConfig.styling.selectedAlpha ?? 0.92);

      const overlay = viewer.entities.add({
        show: sourceEntity.show !== false,
        polygon: {
          hierarchy: sourceEntity.polygon.hierarchy,
          fill: regionFillEnabled && !debugOptions.outlineOnly,
          material: fillColor,
          outline: !debugOptions.fillOnly,
          outlineColor: selectionAccent.withAlpha(0.95),
          perPositionHeight: false,
          height:
            (sourceEntity.polygon.height?.getValue?.(Cesium.JulianDate.now()) ??
              sourceEntity._displayHeight ??
              0) + 10,
          extrudedHeight: undefined,
          closeTop: true,
          closeBottom: false,
          arcType: sourceEntity.polygon.arcType ?? Cesium.ArcType.GEODESIC,
        },
      });
      overlay._selectionOverlay = true;
      overlay._regionId = selectedRegionId;
      overlay._sourceEntity = sourceEntity;
      selectionOverlayEntities.push(overlay);
    }
    requestRender();
  }

  function updateSelectionLabel() {
    // Selection label removed; sidebar is the only selection info surface.
  }

  function disposeRegionDataSource(dataSource) {
    if (!dataSource) return;
    dataSource.entities.removeAll();
    dataSource._entityIndex = null;
  }

  function clearActiveRealmLayer() {
    realmLoadToken += 1;
    if (activeRealmDataSource) {
      disposeRegionDataSource(activeRealmDataSource);
    }
    activeRealmDataSource = null;
    activeRealmSlug = null;
    activeRealmLodLevel = null;
    applyAllVisibility();
    refreshAllStyles();
  }

  async function ensureGlobalLayerLoaded(sourceTag, url, getCurrent, attach) {
    const existing = getCurrent();
    if (existing || !url) return existing;
    if (globalLayerLoads.has(sourceTag)) return globalLayerLoads.get(sourceTag);
    const generation = globalLayerGeneration;
    const loading = (async () => {
      const ds = await Cesium.GeoJsonDataSource.load(url, { clampToGround: false });
      if (generation !== globalLayerGeneration) {
        disposeRegionDataSource(ds);
        return null;
      }
      ds.show = false;
      buildEntityIndex(ds, sourceTag);
      if (generation !== globalLayerGeneration) {
        disposeRegionDataSource(ds);
        return null;
      }
      attach(ds);
      return ds;
    })();
    globalLayerLoads.set(sourceTag, loading);
    try {
      return await loading;
    } finally {
      // Failed requests may be retried on the next zoom or dataset change.
      if (globalLayerLoads.get(sourceTag) === loading) globalLayerLoads.delete(sourceTag);
    }
  }

  function ensureMarineOverviewLayerLoaded() {
    return ensureGlobalLayerLoaded("marineOverview", startupMarineOverviewUrl,
      () => marineOverviewDataSource, (ds) => { marineOverviewDataSource = ds; });
  }

  function ensureLakesOverviewLayerLoaded() {
    return ensureGlobalLayerLoaded("lakesOverview", startupLakesOverviewUrl,
      () => lakesOverviewDataSource, (ds) => { lakesOverviewDataSource = ds; });
  }

  function ensureMarineLayerLoaded() {
    return ensureGlobalLayerLoaded("marine", startupMarineUrl, () => marineDataSource, (ds) => {
      marineDataSource = ds;
      for (const [id, entity] of ds._entityIndex.byId.entries()) marineEntitiesById.set(id, entity);
    });
  }

  function ensureLakesLayerLoaded() {
    return ensureGlobalLayerLoaded("lakes", startupLakesUrl,
      () => lakesDataSource, (ds) => { lakesDataSource = ds; });
  }

  function ensureLandOverviewLayerLoaded() {
    return ensureGlobalLayerLoaded("landOverview", startupLandOverviewUrl,
      () => landOverviewDataSource, (ds) => { landOverviewDataSource = ds; });
  }

  function ensureLandLayerLoaded() {
    return ensureGlobalLayerLoaded("base", startupLandUrl, () => baseDataSource, (ds) => {
      baseDataSource = ds;
      baseEntitiesById.clear();
      baseEntitiesByRealm.clear();
      for (const [id, entity] of ds._entityIndex.byId.entries()) baseEntitiesById.set(id, entity);
      for (const [slug, entities] of ds._entityIndex.byRealm.entries()) baseEntitiesByRealm.set(slug, entities);
    });
  }

  async function ensureGlobalLayersForCurrentView() {
    syncOverviewLodState();
    if (!shouldRenderEcoregions()) return;
    const generation = globalLayerGeneration;
    const loads = [ensureLakesOverviewLayerLoaded()];
    if (isLandVisibleDatasetMode()) loads.push(ensureLandOverviewLayerLoaded());
    if (isMarineVisibleDatasetMode()) loads.push(ensureMarineOverviewLayerLoaded());
    if (!overviewLodActive) {
      loads.push(ensureLakesLayerLoaded());
      if (isLandVisibleDatasetMode()) loads.push(ensureLandLayerLoaded());
      if (isMarineVisibleDatasetMode()) loads.push(ensureMarineLayerLoaded());
    }
    try {
      const results = await Promise.allSettled(loads);
      const failed = results.find((result) => result.status === "rejected");
      if (failed) throw failed.reason;
    } finally {
      // A late response uses the current camera/mode; it cannot switch the user back.
      if (generation === globalLayerGeneration) updateCameraCulling(true);
    }
  }

  function getCameraHeightMeters() {
    return viewer.camera.positionCartographic?.height ?? Number.POSITIVE_INFINITY;
  }

  function getPreferredRealmLodLevel() {
    return getPreferredRealmLodLevelForRealm(getSelectedRegionRecord()?.realmSlug || activeRealmSlug);
  }

  function getPreferredRealmLodLevelForRealm(realmSlug) {
    if (appConfig.globe.enableRealmDetailLod === false) return "none";
    if (!realmSlug) return "none";
    const hasLod1 = realmFilesLod1.has(realmSlug);
    if (!hasLod1) return "none";

    const detailEnter = appConfig.globe.realmDetailEnterHeight ?? 11_500_000;
    const detailExit = appConfig.globe.realmDetailExitHeight ?? Math.round(detailEnter * 1.2);
    const h = getCameraHeightMeters();
    const isActiveForRealm = Boolean(activeRealmDataSource && activeRealmSlug === realmSlug);
    if (isActiveForRealm) {
      if (h >= detailExit) return "none";
    } else if (h > detailEnter) {
      return "none";
    }

    return "lod1";
  }

  function clamp01(value) {
    return Cesium.Math.clamp(value, 0, 1);
  }

  function lerpNumber(a, b, t) {
    return a + (b - a) * clamp01(t);
  }

  function getEntityCullProfile(entity) {
    const sourceTag = entity?._sourceTag || "";
    const isDetailEntity = sourceTag === "detail";
    const isMarineEntity = sourceTag === "marine" || sourceTag === "marineOverview";
    const isLakeEntity = sourceTag === "lakes" || sourceTag === "lakesOverview";
    const h = getCameraHeightMeters();
    const nearHeight = Math.max(1, appConfig.globe.maxZoomInHeight ?? 900_000);
    const farHeight = isDetailEntity
      ? Math.max(nearHeight + 1, appConfig.globe.realmDetailExitHeight ?? 3_100_000)
      : Math.max(nearHeight + 1, appConfig.globe.overviewLodEnterHeight ?? 7_000_000);
    const t = clamp01((h - nearHeight) / Math.max(1, farHeight - nearHeight));

    const baseNearAreaKm2 = appConfig.globe.cameraCullZoomedSkipAboveAreaKm2 ?? 250_000;
    const nearAreaKm2 = isMarineEntity
      ? Math.max(baseNearAreaKm2, 400_000)
      : isLakeEntity
        ? Math.max(baseNearAreaKm2, 50_000)
        : baseNearAreaKm2;
    const farAreaKm2 = isDetailEntity
      ? (appConfig.globe.cameraCullDetailSkipAboveAreaKm2 ?? 1_200_000)
      : isMarineEntity
        ? Math.max(appConfig.globe.cameraCullSkipAboveAreaKm2 ?? 900_000, 1_500_000)
        : isLakeEntity
          ? Math.max(appConfig.globe.cameraCullSkipAboveAreaKm2 ?? 900_000, 500_000)
          : (appConfig.globe.cameraCullSkipAboveAreaKm2 ?? 900_000);
    const skipLargeAreaKm2 = lerpNumber(nearAreaKm2, farAreaKm2, t);

    const baseNearPadPx = appConfig.globe.cameraCullZoomedScreenPaddingPx ?? 80;
    const nearPadPx = isDetailEntity
      ? Math.max(baseNearPadPx, 260)
      : isMarineEntity
        ? Math.max(baseNearPadPx, 220)
        : isLakeEntity
          ? Math.max(baseNearPadPx, 180)
          : Math.max(baseNearPadPx, 180);
    const farPadPx = isDetailEntity
      ? (appConfig.globe.cameraCullDetailScreenPaddingPx ?? 360)
      : isMarineEntity
        ? Math.max(appConfig.globe.cameraCullScreenPaddingPx ?? 240, 320)
        : isLakeEntity
          ? Math.max(appConfig.globe.cameraCullScreenPaddingPx ?? 240, 180)
          : (appConfig.globe.cameraCullScreenPaddingPx ?? 240);
    const screenPaddingPx = lerpNumber(nearPadPx, farPadPx, t);

    const frustumCullingEnabled = isDetailEntity
      ? appConfig.globe.cameraFrustumCullingDetail !== false
      : appConfig.globe.cameraFrustumCullingBase !== false;

    return {
      isDetailEntity,
      isMarineEntity,
      isLakeEntity,
      zoomT: t,
      skipLargeAreaKm2,
      screenPaddingPx,
      frustumCullingEnabled,
    };
  }

  function estimateEntityScreenRadiusPx(entity, width, height) {
    const radiusMeters = Number(entity?._cullRadiusMeters);
    if (!Number.isFinite(radiusMeters) || radiusMeters <= 0) return 0;
    const cullAnchor = entity?._cullAnchorCartesian || entity?._centerCartesian;
    if (!cullAnchor) return 0;
    const cameraDistance = Cesium.Cartesian3.distance(viewer.camera.positionWC, cullAnchor);
    if (!Number.isFinite(cameraDistance) || cameraDistance <= radiusMeters) return Math.max(width, height);
    const frustum = viewer.camera.frustum;
    const fovy = Number(frustum?.fovy);
    if (!Number.isFinite(fovy) || fovy <= 0 || height <= 0) return 0;
    const angularRadius = Math.asin(Math.min(0.999999, radiusMeters / cameraDistance));
    const pixelRadius = (angularRadius / fovy) * height;
    if (!Number.isFinite(pixelRadius)) return 0;
    return Math.min(Math.max(pixelRadius, 0), Math.max(width, height));
  }

  function entityPassesCameraCulling(entity, occluder) {
    if (appConfig.globe.cameraCulling === false) return true;
    const cullAnchor = entity?._cullAnchorCartesian || entity?._centerCartesian;
    if (!cullAnchor) return true;
    if (selectedRegionId && getEntityRegionId(entity) === selectedRegionId) return true;
    const cullProfile = getEntityCullProfile(entity);
    if (Number.isFinite(entity._areaKm2) && entity._areaKm2 >= cullProfile.skipLargeAreaKm2) return true;

    if (!occluder.isPointVisible(cullAnchor)) return false;
    if (!cullProfile.frustumCullingEnabled) return true;

    const windowPos = Cesium.SceneTransforms.wgs84ToWindowCoordinates(viewer.scene, cullAnchor);
    if (!windowPos) return false;
    const width = viewer.scene.canvas?.clientWidth ?? viewer.scene.canvas?.width ?? 0;
    const height = viewer.scene.canvas?.clientHeight ?? viewer.scene.canvas?.height ?? 0;
    if (width <= 0 || height <= 0) return true;
    const pad = cullProfile.screenPaddingPx;
    const footprintPad = estimateEntityScreenRadiusPx(entity, width, height);
    const totalPad = pad + footprintPad;

    return !(
      windowPos.x < -totalPad ||
      windowPos.x > width + totalPad ||
      windowPos.y < -totalPad ||
      windowPos.y > height + totalPad
    );
  }

  function updateCameraCulling(force = false) {
    const now = performance.now();
    const minMs = appConfig.globe.cameraCullUpdateMs ?? 120;
    if (!force && now - lastCullRunAt < minMs) return;
    lastCullRunAt = now;
    // Cesium culls the selected overlay. Unselected lookup geometry needs no
    // per-frame horizon/frustum checks or style updates.
    applyAllVisibility();
    debugReport("camera-cull-update");
  }

  function scheduleCameraCulling(force = false) {
    if (force) {
      if (cameraCullTimer) {
        clearTimeout(cameraCullTimer);
        cameraCullTimer = 0;
      }
      updateCameraCulling(true);
      return;
    }
    if (cameraCullTimer) return;
    const delay = Math.max(16, appConfig.globe.cameraCullUpdateMs ?? 120);
    cameraCullTimer = window.setTimeout(() => {
      cameraCullTimer = 0;
      updateCameraCulling(false);
    }, delay);
  }

  function debugReport(reason) {
    const isHeavyReason =
      debugOptions.enabled ||
      reason === "startup-load" ||
      reason === "debug-options" ||
      reason.startsWith("realm-detail-load");
    const isFastPath = !isHeavyReason;
    let flatViolationCount = lastFlatViolationCount;
    let duplicateVisible = lastDuplicateVisible;

    if (!isFastPath) {
      const flatViolations = [];
      for (const ds of loadedDataSources()) {
        for (const entity of ds.entities.values) {
          if (!entity.polygon) continue;
          const p = entity.polygon;
          const perPositionHeight = p.perPositionHeight?.getValue?.(Cesium.JulianDate.now()) ?? p.perPositionHeight;
          const height = p.height?.getValue?.(Cesium.JulianDate.now()) ?? p.height;
          const extrudedHeight = p.extrudedHeight?.getValue?.(Cesium.JulianDate.now()) ?? p.extrudedHeight;
          if (perPositionHeight === true || (height != null && height !== 0) || extrudedHeight != null) {
            flatViolations.push(getEntityRegionId(entity));
          }
        }
      }
      flatViolationCount = flatViolations.length;
      lastFlatViolationCount = flatViolationCount;

      const baseVisible = new Set();
      const detailVisible = new Set();
      if (baseDataSource) {
        for (const entity of baseDataSource.entities.values) {
          if (entity.show) baseVisible.add(getEntityRegionId(entity));
        }
      }
      if (activeRealmDataSource && activeRealmDataSource.show !== false) {
        for (const entity of activeRealmDataSource.entities.values) {
          if (entity.show) detailVisible.add(getEntityRegionId(entity));
        }
      }
      duplicateVisible = 0;
      for (const id of detailVisible) {
        if (baseVisible.has(id)) duplicateVisible += 1;
      }
      lastDuplicateVisible = duplicateVisible;
    }

    const moonPosition = getMoonWorldPositionForTime(viewer.clock.currentTime, moonWorldPositionScratch);
    const moonDistanceKm = Cesium.Cartesian3.distance(viewer.camera.positionWC, moonPosition) / 1000;
    const moonTotalEclipseNow = getCurrentLunarEclipseTintFactor(viewer.clock.currentTime, appConfig) > 0.001;
    let moonVisible = false;
    const moonWindowPosition = Cesium.SceneTransforms.worldToWindowCoordinates?.(viewer.scene, moonPosition);
    if (moonWindowPosition) {
      const width = viewer.scene.canvas?.clientWidth ?? viewer.scene.canvas?.width ?? 0;
      const height = viewer.scene.canvas?.clientHeight ?? viewer.scene.canvas?.height ?? 0;
      moonVisible =
        width > 0 &&
        height > 0 &&
        moonWindowPosition.x >= 0 &&
        moonWindowPosition.x <= width &&
        moonWindowPosition.y >= 0 &&
        moonWindowPosition.y <= height;
    }
    lastMoonVisible = moonVisible;
    lastMoonDistanceKm = moonDistanceKm;

    const report = {
      reason,
      debugOptions: { ...debugOptions },
      activeRealmSlug,
      activeRealmLodLevel,
      preferredRealmLodLevel: getPreferredRealmLodLevel(),
      cameraHeightMeters: getCameraHeightMeters(),
      resolutionScale: viewer.resolutionScale ?? 1,
      renderWidth: viewer.scene.canvas.width,
      renderHeight: viewer.scene.canvas.height,
      renderPixelRatio: viewer.scene.canvas.width / Math.max(1, viewer.scene.canvas.clientWidth),
      blueMarbleDetailTilesVisible: blueMarbleDetailImageryLayer?.show === true,
      blueMarbleDetailTilesAlpha: blueMarbleDetailImageryLayer?.alpha ?? 0,
      blueMarbleUltraTilesVisible: blueMarbleUltraImageryLayer?.show === true,
      blueMarbleUltraTilesAlpha: blueMarbleUltraImageryLayer?.alpha ?? 0,
      blackMarbleNightVisible:
        blackMarbleNightBaseImageryLayer?.show === true ||
        blackMarbleNightDetailImageryLayer?.show === true ||
        blackMarbleNightUltraImageryLayers.some((layer) => layer?.show === true),
      blackMarbleNightDetailTilesVisible: blackMarbleNightDetailImageryLayer?.show === true,
      blackMarbleNightDetailTilesAlpha: blackMarbleNightDetailImageryLayer?.alpha ?? 0,
      blackMarbleNightUltraTilesVisible: blackMarbleNightUltraImageryLayers.some((layer) => layer?.show === true),
      blackMarbleNightUltraTilesAlpha:
        blackMarbleNightUltraImageryLayers.reduce((max, layer) => Math.max(max, layer?.alpha ?? 0), 0),
      imagerySolarDot: lastSolarDot,
      imagerySolarGatingEnabled: appConfig.globe.imagerySolarSideGatingEnabled !== false,
      imageryHighDetailSide: solarHighDetailSide,
      nightDiagnosticsEnabled,
      lightingEnabled: viewer.scene.globe.enableLighting === true,
      blueMarbleUltraRequestCount,
      blackMarbleNightUltraRequestCount,
      blueMarbleUltraUniqueRequestCount,
      blackMarbleNightUltraUniqueRequestCount,
      blueMarbleUltraDedupHitCount,
      blackMarbleNightUltraDedupHitCount,
      cameraCullingEnabled: appConfig.globe.cameraCulling !== false,
      moonVisible,
      moonDistanceKm,
      moonTotalEclipseNow,
      moonEclipseTextureActive: moonTotalEclipseNow && Boolean(appConfig.globe.moonEclipseTextureHiResUrl),
      moonProxyEnabled: moonProxyPrimitive?.getStateForDebug?.()?.show === true,
      moonLod: moonProxyPrimitive?.getStateForDebug?.() ?? null,
      moonDebugMarkerEnabled: lastMoonTagVisible,
      cameraAnchorMode,
      ecoregionsVisible: shouldRenderEcoregions(),
      regionFillEnabled,
      duplicateVisible,
      flatViolationCount,
      selectedRegionId,
      marineVisibleCount: viewCullStats.marineVisible,
      baseVisibleCount: viewCullStats.baseVisible,
      detailVisibleCount: viewCullStats.detailVisible,
      marineTotalCount: viewCullStats.marineTotal,
      baseTotalCount: viewCullStats.baseTotal,
      detailTotalCount: viewCullStats.detailTotal,
      skyDome: skyDome.getStateForDebug?.() ?? null,
    };

    if (debugOptions.enabled) {
      const flatMsg = report.flatViolationCount === 0 ? "flat=OK" : `flat=${report.flatViolationCount} violations`;
      const dupMsg = report.duplicateVisible === 0 ? "dup=OK" : `dup=${report.duplicateVisible}`;
      console.info(`[geometry-debug] ${reason} | ${dupMsg} | ${flatMsg}`, report);
    }
    onDebugReport?.(report);
    return report;
  }

  function startSelectionPulse(regionId) {
    if (appConfig.styling.selectionPulseEnabled === false) return;
    if (!regionId) return;
    pulse = { regionId, startedAt: performance.now(), durationMs: 520 };
    if (pulseFrame) cancelAnimationFrame(pulseFrame);

    const tick = () => {
      if (!pulse || pulse.regionId !== selectedRegionId) {
        pulseFrame = 0;
        return;
      }
      const active = pulseStrength() > 0.001;
      const selectedEntities = getSelectedEntities();
      for (const selectedEntity of selectedEntities) {
        applyEntityStyle(selectedEntity);
      }
      requestRender();
      if (!active) {
        pulse = null;
        pulseFrame = 0;
        for (const selectedEntity of selectedEntities) {
          applyEntityStyle(selectedEntity);
        }
        requestRender();
        return;
      }
      pulseFrame = requestAnimationFrame(tick);
    };

    pulseFrame = requestAnimationFrame(tick);
  }

  async function ensureRealmDetail(realmSlug) {
    if (isMarineOnlyDatasetMode()) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    if (appConfig.globe.enableRealmDetailLod === false) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    if (!realmSlug) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    if (debugOptions.disableLodSwap) return;

    const preferredLod = getPreferredRealmLodLevelForRealm(realmSlug);
    if (preferredLod === "none") {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    if (!realmFilesLod1.has(realmSlug)) return;
    return ensureRealmDetailWithLevel(realmSlug, "lod1");
  }

  async function ensureRealmDetailWithLevel(realmSlug, lodLevel) {
    if (!realmSlug || lodLevel !== "lod1" || !realmFilesLod1.has(realmSlug)) return;
    if (debugOptions.disableLodSwap) return;

    if (activeRealmSlug === realmSlug && activeRealmLodLevel === lodLevel && activeRealmDataSource) {
      activeRealmDataSource.show = true;
      applyAllVisibility();
      refreshAllStyles();
      debugReport("realm-detail-reuse");
      return;
    }

    const token = ++realmLoadToken;
    const previousDs = activeRealmDataSource;

    const url = realmFilesLod1.get(realmSlug);
    const ds = await Cesium.GeoJsonDataSource.load(url, { clampToGround: false });
    if (token !== realmLoadToken) {
      try {
        disposeRegionDataSource(ds);
      } catch {
        // no-op: loaded but not added
      }
      return;
    }
    buildEntityIndex(ds, "detail");
    activeRealmDataSource = ds;
    activeRealmSlug = realmSlug;
    activeRealmLodLevel = lodLevel;
    ds.show = true;
    if (previousDs && previousDs !== ds) {
      disposeRegionDataSource(previousDs);
    }
    updateCameraCulling(true);
    applyAllVisibility();
    refreshAllStyles();
    debugReport(`realm-detail-load-${lodLevel}`);
  }

  function setRegionFillEnabled(enabled) {
    const next = Boolean(enabled);
    if (regionFillEnabled === next) return;
    regionFillEnabled = next;
    syncSelectionOverlay();
    debugReport("fill-toggle");
  }

  async function selectTarget(region, clickPosition) {
    const previousSelectedRegionId = selectedRegionId;
    if (selectionDetailLoadRaf) {
      cancelAnimationFrame(selectionDetailLoadRaf);
      selectionDetailLoadRaf = 0;
    }
    selectedRegionId = region?.id || null;
    hoveredEntity = null;
    pulse = null;
    if (pulseFrame) {
      cancelAnimationFrame(pulseFrame);
      pulseFrame = 0;
    }
    if (selectedRegionId && selectedRegionId !== MOON_SELECTION_ID) {
      startSelectionPulse(selectedRegionId);
    }
    updateSelectionLabel();
    const useSelectionOverlay =
      selectedRegionId && selectedRegionId !== MOON_SELECTION_ID;
    if (!useSelectionOverlay) {
      clearSelectionOverlay();
      refreshRegionStylesById(previousSelectedRegionId, selectedRegionId);
    } else {
      syncSelectionOverlay();
    }
    onHover?.(null, null);
    onSelect?.(selectedRegionId);
    scheduleImageryBlendSync(true);

    if (!region?.realmSlug) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
      }
    } else if (appConfig.globe.lazyLoadRealmDetailOnSelect !== false && !debugOptions.disableLodSwap) {
      const pendingRegionId = region.id;
      selectionDetailLoadRaf = requestAnimationFrame(() => {
        selectionDetailLoadRaf = 0;
        if (selectedRegionId !== pendingRegionId) return;
        ensureRealmDetail(region.realmSlug).catch((err) => {
          console.warn("Failed to load realm detail layer:", region.realmSlug, err);
        });
      });
    }

    if (clickPosition) {
      lastPickedPosition = clickPosition;
    }
    debugReport("selection-change");
  }

  async function selectRegionByEntity(entity, clickPosition) {
    return selectTarget(getRegionRecord(entity), clickPosition);
  }

  async function selectMoon(clickPosition) {
    return selectTarget(getMoonRecord(), clickPosition);
  }

  async function selectOpenOcean(clickPosition) {
    return selectTarget(getOpenOceanRecord(), clickPosition);
  }

  function activateMoonAnchorTarget() {
    if (appConfig.globe.moonEnabled === false) return cameraAnchorMode;
    const nextMode = setCameraAnchorMode("moon");
    selectMoon(null).catch((err) => {
      console.warn("Moon selection handling failed:", err);
    });
    return nextMode;
  }

  function activateEarthAnchorTarget() {
    const nextMode = setCameraAnchorMode("earth");
    selectedRegionId = null;
    clearSelectionOverlay();
    refreshAllStyles();
    onHover?.(null, null);
    onSelect?.(null);
    debugReport("selection-change");
    return nextMode;
  }

  function setCameraAnchorMode(nextMode) {
    const targetMode = nextMode === "moon" ? "moon" : "earth";
    if (cameraAnchorMode === targetMode) return cameraAnchorMode;

    if (targetMode === "moon") {
      if (anchorReleaseRaf) {
        cancelAnimationFrame(anchorReleaseRaf);
        anchorReleaseRaf = 0;
      }
      moonAnchorLockActive = false;
      earthAnchorViewState = cloneViewState(viewer.camera);
      cameraAnchorMode = "moon";
      applyAllVisibility();
      const moonAnchorRangeMultiplier = Math.max(2.5, appConfig.globe.moonAnchorRangeMultiplier ?? 7.0);
      moonAnchorRange = Cesium.Ellipsoid.MOON.maximumRadius * moonAnchorRangeMultiplier;
      const moonMinimumZoomDistance = Math.max(
        Cesium.Ellipsoid.MOON.maximumRadius + (appConfig.globe.moonAnchorMinimumAltitude ?? 450_000),
        Cesium.Ellipsoid.MOON.maximumRadius * 1.08
      );
      ssc.minimumZoomDistance = moonMinimumZoomDistance;
      const pose = getMoonCameraPoseForTime(viewer.clock.currentTime, moonAnchorRangeMultiplier);
      viewer.camera.flyTo({
        destination: pose.destination,
        orientation: {
          direction: pose.direction,
          up: pose.up,
        },
        duration: 1.1,
        complete: () => {
          if (cameraAnchorMode !== "moon") return;
          const moonTransform = computeMoonModelMatrix(viewer.clock.currentTime, moonAnchorTransformScratch);
          viewer.camera.lookAtTransform(
            moonTransform,
            new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-18), moonAnchorRange)
          );
          moonAnchorLockActive = true;
          requestRender();
        },
      });
      requestRender();
      return cameraAnchorMode;
    }

    viewer.trackedEntity = undefined;
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    moonAnchorLockActive = false;
    cameraAnchorMode = "earth";
    ssc.minimumZoomDistance = earthMinimumZoomDistance;
    applyAllVisibility();
    if (earthAnchorViewState) {
      viewer.camera.flyTo({
        destination: earthAnchorViewState.destination,
        orientation: {
          direction: earthAnchorViewState.direction,
          up: earthAnchorViewState.up,
        },
        duration: 1.1,
      });
    } else {
      requestRender();
    }
    return cameraAnchorMode;
  }

  function toggleCameraAnchor() {
    return setCameraAnchorMode(cameraAnchorMode === "earth" ? "moon" : "earth");
  }

  function clearHover(clearPosition = false) {
    if (hoverTimer) window.clearTimeout(hoverTimer);
    if (hoverRaf) cancelAnimationFrame(hoverRaf);
    hoverTimer = 0;
    hoverRaf = 0;
    if (clearPosition) pendingHoverPosition = null;
    const previous = hoveredEntity;
    hoveredEntity = null;
    if (previous && appConfig.styling.hoverHighlightEnabled !== false) refreshChangedStyles(previous);
    onHover?.(null, null);
  }

  function scheduleHover() {
    if (cameraMoving || pointerDown || !pendingHoverPosition || hoverRaf || hoverTimer || !shouldRenderEcoregions()) return;
    const delay = Math.max(0, (appConfig.styling.hoverUpdateMs ?? 100) - (performance.now() - lastHoverAt));
    if (delay > 0) {
      hoverTimer = window.setTimeout(() => {
        hoverTimer = 0;
        scheduleHover();
      }, delay);
    } else {
      hoverRaf = requestAnimationFrame(processHover);
    }
  }

  function processHover() {
    hoverRaf = 0;
    if (cameraMoving || pointerDown || !shouldRenderEcoregions()) return;
    if (
      (!getActiveLandGlobalDataSource() && !getActiveMarineGlobalDataSource() && !getActiveLakesGlobalDataSource()) ||
      !pendingHoverPosition
    ) {
      return;
    }
    lastHoverAt = performance.now();
    const previous = hoveredEntity;
    hoveredEntity = pickManagedEntity(
      viewer,
      pendingHoverPosition,
      isLandVisibleDatasetMode() ? getActiveLandGlobalDataSource() : null,
      isLandVisibleDatasetMode() ? activeRealmDataSource : null,
      isMarineVisibleDatasetMode() ? getActiveMarineGlobalDataSource() : null,
      getActiveLakesGlobalDataSource(),
      true // Hover needs only the top object; clicks keep the complete region lookup.
    );
    if (appConfig.styling.hoverHighlightEnabled !== false) {
      refreshChangedStyles(previous);
    }
    onHover?.(getRegionRecord(hoveredEntity), pendingHoverPosition);
  }

  function getViewportCenterScreenPosition() {
    const canvas = viewer.scene.canvas;
    const width = canvas?.clientWidth ?? canvas?.width ?? 0;
    const height = canvas?.clientHeight ?? canvas?.height ?? 0;
    if (width <= 0 || height <= 0) return null;
    return new Cesium.Cartesian2(width / 2, height / 2);
  }

  function getCameraFocusRegionRecord() {
    const centerPos = getViewportCenterScreenPosition();
    return centerPos ? getRegionRecord(findEntityByGlobeHit(centerPos)) : null;
  }

  function findEntityByGlobeHit(screenPosition) {
    if (!shouldRenderEcoregions()) return null;
    const ray = viewer.camera.getPickRay(screenPosition);
    if (!ray) return null;
    const world = viewer.scene.globe.pick(ray, viewer.scene);
    if (!world) return null;
    const cartographic = Cesium.Cartographic.fromCartesian(world);
    if (!cartographic) return null;
    const pointLon = cartographic.longitude;
    const pointLat = cartographic.latitude;
    const worldNormal = Cesium.Cartesian3.normalize(world, new Cesium.Cartesian3());
    const time = Cesium.JulianDate.now();

    const candidateSources = isMarineOnlyDatasetMode()
      ? [getActiveMarineGlobalDataSource(), getActiveLakesGlobalDataSource()]
      : isCombinedDatasetMode()
        ? [activeRealmDataSource, getActiveLandGlobalDataSource(), getActiveMarineGlobalDataSource(), getActiveLakesGlobalDataSource()]
        : [activeRealmDataSource, getActiveLandGlobalDataSource(), getActiveLakesGlobalDataSource()];

    for (const dataSource of candidateSources) {
      if (!dataSource || dataSource.show === false) continue;
      for (const entity of dataSource.entities.values) {
        if (!entity?.polygon) continue;
        applyEntityVisibility(entity);
        if (entity.show === false) continue;
        const cullAnchor = entity._cullAnchorCartesian || entity._centerCartesian;
        const cullRadius = Number(entity._cullRadiusMeters);
        if (cullAnchor && Number.isFinite(cullRadius) && cullRadius > 0) {
          const anchorNormal = Cesium.Cartesian3.normalize(cullAnchor, new Cesium.Cartesian3());
          // Never allow opposite-hemisphere candidates in fallback hit-testing.
          if (Cesium.Cartesian3.dot(worldNormal, anchorNormal) < -0.05) {
            continue;
          }
          // Guard against antipodal false positives from spherical ring winding.
          const anchorDistance = Cesium.Cartesian3.distance(cullAnchor, world);
          if (anchorDistance > cullRadius * 1.15) {
            continue;
          }
        }
        const hierarchy = entity.polygon.hierarchy?.getValue?.(time) ?? entity.polygon.hierarchy;
        if (polygonHierarchyContainsPoint(hierarchy, pointLon, pointLat)) {
          return entity;
        }
      }
    }
    return null;
  }

  function findNearestMarineEntityByCenter(screenPosition) {
    const activeMarineDataSource = getActiveMarineGlobalDataSource();
    if (!isMarineVisibleDatasetMode() || !activeMarineDataSource || activeMarineDataSource.show === false) return null;
    const ray = viewer.camera.getPickRay(screenPosition);
    if (!ray) return null;
    const world = viewer.scene.globe.pick(ray, viewer.scene);
    if (!world) return null;
    const clickedNormal = Cesium.Cartesian3.normalize(world, new Cesium.Cartesian3());
    let bestEntity = null;
    let bestDot = -1;

    for (const entity of activeMarineDataSource.entities.values) {
      if (!entity?.polygon || entity.show === false || !entity._centerCartesian) continue;
      const centerNormal = Cesium.Cartesian3.normalize(entity._centerCartesian, new Cesium.Cartesian3());
      const dot = Cesium.Cartesian3.dot(clickedNormal, centerNormal);
      if (dot > bestDot) {
        bestDot = dot;
        bestEntity = entity;
      }
    }

    return bestEntity;
  }

  async function syncRealmDetailToCurrentView() {
    if (appConfig.globe.enableRealmDetailLod === false) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    if (debugOptions.disableLodSwap) return;
    const selectedRegion = getSelectedRegionRecord();
    const focusRegion =
      selectedRegion || (appConfig.globe.autoLoadRealmDetailOnZoom === false ? null : getCameraFocusRegionRecord());
    if (!focusRegion?.realmSlug) {
      if (activeRealmDataSource) {
        clearActiveRealmLayer();
        debugReport("realm-detail-clear");
      }
      return;
    }
    await ensureRealmDetail(focusRegion.realmSlug);
  }

  handler.setInputAction((movement) => {
    pendingHoverPosition = Cesium.Cartesian2.clone(movement.endPosition, pendingHoverPosition || new Cesium.Cartesian2());
    scheduleHover();
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

  for (const type of [Cesium.ScreenSpaceEventType.LEFT_DOWN, Cesium.ScreenSpaceEventType.MIDDLE_DOWN, Cesium.ScreenSpaceEventType.RIGHT_DOWN]) {
    handler.setInputAction(() => {
      pointerDown = true;
      clearHover();
    }, type);
  }
  for (const type of [Cesium.ScreenSpaceEventType.LEFT_UP, Cesium.ScreenSpaceEventType.MIDDLE_UP, Cesium.ScreenSpaceEventType.RIGHT_UP]) {
    handler.setInputAction(() => {
      pointerDown = false;
      scheduleHover();
    }, type);
  }
  viewer.scene.canvas.addEventListener("pointerleave", () => {
    pointerDown = false;
    clearHover(true);
  });

  handler.setInputAction((click) => {
    if (!getActiveLandGlobalDataSource() && !getActiveMarineGlobalDataSource() && !getActiveLakesGlobalDataSource()) {
      return;
    }
    // Geographic lookup works even though unselected regions have no GPU primitive.
    const entity = findEntityByGlobeHit(click.position);
    if (!entity && screenPositionHitsMoon(viewer, click.position)) {
      lastPickedPosition = click.position;
      activateMoonAnchorTarget();
      return;
    }
    if (!entity && cameraAnchorMode === "moon" && screenPositionHitsEarth(viewer, click.position)) {
      lastPickedPosition = click.position;
      activateEarthAnchorTarget();
      return;
    }
    if (!entity && screenPositionHitsEarth(viewer, click.position)) {
      lastPickedPosition = click.position;
      selectOpenOcean(click.position).catch((err) => {
        console.warn("Open-ocean selection handling failed:", err);
      });
      return;
    }
    if (entity && cameraAnchorMode === "moon") {
      setCameraAnchorMode("earth");
    }
    lastPickedPosition = click.position;
    selectRegionByEntity(entity, click.position).catch((err) => {
      console.warn("Selection handling failed:", err);
    });
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

  viewer.camera.moveStart.addEventListener(() => {
    cameraMoving = true;
    clearHover();
    setRenderResolutionScale(movingResolutionScale);
  });

  viewer.camera.moveEnd.addEventListener(() => {
    cameraMoving = false;
    setRenderResolutionScale(idleResolutionScale);
    scheduleCameraCulling(true);
    scheduleHover();
    if (globalGeometryReady) {
      ensureGlobalLayersForCurrentView().catch((err) => console.warn("Global detail loading failed; keeping available boundaries:", err));
    }
    if (appConfig.globe.nightUltraLoadOnIdleOnly !== false) {
      if (nightUltraIdleReady) {
        setNightUltraIdleReady(true);
      } else {
        armNightUltraIdleActivation();
      }
    }
    scheduleImageryBlendSync(true);
    syncRealmDetailToCurrentView().catch((err) => console.warn("LOD sync on camera move failed:", err));
  });
  viewer.camera.changed.addEventListener(() => {
    scheduleCameraCulling(false);
    if (
      appConfig.globe.nightUltraLoadOnIdleOnly !== false &&
      nightUltraIdleReady &&
      hasNightUltraSignificantViewChange()
    ) {
      clearNightUltraIdleTimer();
      setNightUltraIdleReady(false);
    }
    scheduleImageryBlendSync(false);
  });

  async function loadGeometryLods({
    overviewUrl,
    startupUrl,
    marineOverviewUrl,
    marineStartupUrl,
    lakesOverviewUrl,
    lakesStartupUrl,
    realmDetailFiles,
    regionIndex,
    marineRegionIndex,
    lakesRegionIndex,
  }) {
    globalGeometryReady = false;
    globalLayerGeneration += 1;
    globalLayerLoads.clear();
    clearHover(true);
    regionIndexCache.clear();
    regionNameCache.clear();
    realmFilesLod1.clear();
    marineEntitiesById.clear();
    baseEntitiesById.clear();
    baseEntitiesByRealm.clear();
    regionFillEnabled = appConfig.styling.fillEnabled !== false;
    regionDatasetMode = "combined";
    startupLandOverviewUrl = overviewUrl || null;
    startupLandUrl = startupUrl || null;
    startupMarineOverviewUrl = marineOverviewUrl || null;
    startupMarineUrl = marineStartupUrl || null;
    startupLakesOverviewUrl = lakesOverviewUrl || null;
    startupLakesUrl = lakesStartupUrl || null;
    overviewLodActive = false;
    selectedRegionId = null;
    hoveredEntity = null;
    clearActiveRealmLayer();
    clearSelectionOverlay();
    if (landOverviewDataSource) {
      disposeRegionDataSource(landOverviewDataSource);
      landOverviewDataSource = null;
    }
    if (baseDataSource) {
      disposeRegionDataSource(baseDataSource);
      baseDataSource = null;
    }
    if (marineOverviewDataSource) {
      disposeRegionDataSource(marineOverviewDataSource);
      marineOverviewDataSource = null;
    }
    if (marineDataSource) {
      disposeRegionDataSource(marineDataSource);
      marineDataSource = null;
    }
    if (lakesOverviewDataSource) {
      disposeRegionDataSource(lakesOverviewDataSource);
      lakesOverviewDataSource = null;
    }
    if (lakesDataSource) {
      disposeRegionDataSource(lakesDataSource);
      lakesDataSource = null;
    }

    for (const [id, record] of Object.entries(regionIndex || {})) {
      regionIndexCache.set(id, record);
      if (record?.name && !regionNameCache.has(record.name)) {
        regionNameCache.set(record.name, record);
      }
    }
    for (const [id, record] of Object.entries(marineRegionIndex || {})) {
      regionIndexCache.set(id, record);
      if (record?.name && !regionNameCache.has(record.name)) {
        regionNameCache.set(record.name, record);
      }
    }
    for (const [id, record] of Object.entries(lakesRegionIndex || {})) {
      regionIndexCache.set(id, record);
      if (record?.name && !regionNameCache.has(record.name)) {
        regionNameCache.set(record.name, record);
      }
    }
    if (appConfig.globe.enableRealmDetailLod !== false) {
      for (const [slug, path] of Object.entries(realmDetailFiles || {})) {
        realmFilesLod1.set(slug, path);
      }
    }

    await Promise.all([
      ensureLandOverviewLayerLoaded(),
      ensureMarineOverviewLayerLoaded(),
      ensureLakesOverviewLayerLoaded(),
    ]);
    globalGeometryReady = true;
    try {
      await ensureGlobalLayersForCurrentView();
    } catch (err) {
      console.warn("Global detail loading failed; keeping available boundaries:", err);
    }

    syncOverviewLodState();
    applyAllVisibility();
    updateCameraCulling(true);
    refreshAllStyles();
    debugReport("startup-load");
  }

  async function setRegionDatasetMode(nextMode) {
    const targetMode =
      nextMode === "marine" ? "marine" : nextMode === "combined" ? "combined" : "land";
    if (regionDatasetMode === targetMode) return regionDatasetMode;

    clearHover(true);
    if (selectionDetailLoadRaf) {
      cancelAnimationFrame(selectionDetailLoadRaf);
      selectionDetailLoadRaf = 0;
    }
    regionDatasetMode = targetMode;
    await selectTarget(null, null);
    if (targetMode === "marine") clearActiveRealmLayer();
    updateCameraCulling(true);
    try {
      await ensureGlobalLayersForCurrentView();
    } catch (err) {
      console.warn("Dataset detail loading failed; keeping available boundaries:", err);
    }
    refreshAllStyles();
    syncRealmDetailToCurrentView().catch((err) => console.warn("LOD sync on dataset switch failed:", err));
    debugReport(`dataset-${regionDatasetMode}`);
    return regionDatasetMode;
  }

  function toggleRegionDatasetMode() {
    const nextMode =
      regionDatasetMode === "land"
        ? "marine"
        : regionDatasetMode === "marine"
          ? "combined"
          : "land";
    return setRegionDatasetMode(nextMode);
  }

  async function ensureRealmDetailForRegion(regionId) {
    const region = regionIndexCache.get(regionId);
    if (!region?.realmSlug) return;
    await ensureRealmDetail(region.realmSlug);
  }

  function setDebugOptions(nextOptions = {}) {
    Object.assign(debugOptions, nextOptions);

    // Keep mutually-exclusive visual mode toggles sane.
    if (debugOptions.fillOnly && debugOptions.outlineOnly) {
      debugOptions.outlineOnly = false;
    }
    if (debugOptions.lod0Only && debugOptions.lod1Only) {
      debugOptions.lod1Only = false;
    }

    if (debugOptions.disableLodSwap) {
      if (activeRealmDataSource) activeRealmDataSource.show = false;
      activeRealmDataSource = activeRealmDataSource || null;
      activeRealmSlug = activeRealmSlug || null;
    }

    applyAllVisibility();
    refreshAllStyles();
    updateSelectionLabel();
    debugReport("debug-options");
  }

  function setNightDiagnosticsEnabled(enabled) {
    const next = Boolean(enabled);
    if (nightDiagnosticsEnabled === next) {
      debugReport("night-diagnostics");
      return nightDiagnosticsEnabled;
    }
    nightDiagnosticsEnabled = next;
    if (blackMarbleNightBaseImageryLayer) {
      applyNightLayerSettings(blackMarbleNightBaseImageryLayer, "base");
    }
    scheduleImageryBlendSync(true);
    requestRender();
    debugReport("night-diagnostics");
    return nightDiagnosticsEnabled;
  }

  function toggleNightDiagnostics() {
    return setNightDiagnosticsEnabled(!nightDiagnosticsEnabled);
  }

  async function clearSelection() {
    await selectTarget(null, null);
  }

  function resetView() {
    if (anchorReleaseRaf) {
      cancelAnimationFrame(anchorReleaseRaf);
      anchorReleaseRaf = 0;
    }
    if (viewer.trackedEntity === moonAnchorEntity) {
      viewer.trackedEntity = undefined;
    }
    moonAnchorLockActive = false;
    ssc.minimumZoomDistance = earthMinimumZoomDistance;
    cameraAnchorMode = "earth";
    applyAllVisibility();
    selectedRegionId = null;
    hoveredEntity = null;
    pulse = null;
    if (pulseFrame) cancelAnimationFrame(pulseFrame);
    pulseFrame = 0;
    if (selectionDetailLoadRaf) cancelAnimationFrame(selectionDetailLoadRaf);
    selectionDetailLoadRaf = 0;
    clearActiveRealmLayer();
    clearSelectionOverlay();
    onHover?.(null, null);
    onSelect?.(null);
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(
        appConfig.globe.initialView.lon,
        appConfig.globe.initialView.lat,
        appConfig.globe.initialView.height
      ),
      duration: 1.0,
    });
    debugReport("reset-view");
  }

  return {
    viewer,
    loadGeometryLods,
    ensureRealmDetailForRegion,
    getSkySettings: () => skyDome.getSettings?.(),
    setSkySettings: (next) => skyDome.setSettings?.(next),
    activateMoonAnchorTarget,
    activateEarthAnchorTarget,
    setCameraAnchorMode,
    getCameraAnchorMode: () => cameraAnchorMode,
    toggleCameraAnchor,
    setMoonSurfaceMode,
    getMoonSurfaceMode: () => moonSurfaceMode,
    setMoonGeologyTextureUrls,
    setRegionDatasetMode,
    getRegionDatasetMode: () => regionDatasetMode,
    toggleRegionDatasetMode,
    setNightDiagnosticsEnabled,
    getNightDiagnosticsEnabled: () => nightDiagnosticsEnabled,
    toggleNightDiagnostics,
    clearSelection,
    setRegionFillEnabled,
    getRegionFillEnabled: () => regionFillEnabled,
    setDebugOptions,
    getDebugOptions: () => ({ ...debugOptions }),
    resetView,
  };
}
