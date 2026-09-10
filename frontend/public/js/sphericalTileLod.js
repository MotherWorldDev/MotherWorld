/* global Cesium */
import { getGpuMemoryPolicy, BoundedBlobCache } from "./gpuMemoryPolicy.js?v=20260910-memory1";
// Equirectangular tiles: 512 pixels + two-pixel gutters; y=0 is the north pole.
export function tileDescriptor(z, x, y) {
  const columns = 2 ** (z + 1), rows = 2 ** z;
  const u0 = x / columns, u1 = (x + 1) / columns;
  const v0 = y / rows, v1 = (y + 1) / rows;
  const lon = Math.PI * (u0 + u1), lat = Math.PI / 2 - Math.PI * (v0 + v1) / 2;
  const center = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  let minDot = 1;
  for (const u of [u0, (u0 + u1) / 2, u1]) for (const v of [v0, (v0 + v1) / 2, v1]) {
    const p = spherePoint(u, v);
    minDot = Math.min(minDot, dot(center, p));
  }
  const angle = Math.min(Math.PI, Math.acos(Math.max(-1, minDot)) + 0.002);
  return { key: `${z}/${x}/${y}`, z, x, y, u0, u1, v0, v1, center, angle };
}
export function spherePoint(u, imageV) {
  const lon = 2 * Math.PI * u, lat = Math.PI / 2 - Math.PI * imageV;
  return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
}
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export function tileBehindHorizon(tile, camera) {
  const distance = Math.hypot(...camera);
  if (distance <= 1) return false;
  const separation = Math.acos(Math.max(-1, Math.min(1, dot(tile.center, camera) / distance)));
  return separation > Math.acos(1 / distance) + tile.angle;
}
export function tileCoveredByOccluder(tile, direction, angularRadius) {
  const d = Math.hypot(...direction);
  if (!d || angularRadius <= tile.angle) return false;
  const separation = Math.acos(Math.max(-1, Math.min(1, dot(tile.center, direction) / d)));
  return separation + tile.angle < angularRadius;
}
export function desiredTileLevel(projectedDiameter, maxLevel, targetPixels = 512) {
  return Math.max(0, Math.min(maxLevel, Math.ceil(Math.log2(Math.max(1, projectedDiameter / targetPixels)))));
}

function patchGeometry(tile, inside) {
  const n = 16, positions = [], normals = [], st = [], indices = [];
  // Parent/base sphere stays behind detail; matched gutters hide linear-filter seams.
  const radius = inside ? 0.995 : 1.0002;
  for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) {
    const p = spherePoint(tile.u0 + (tile.u1 - tile.u0) * i / n, tile.v0 + (tile.v1 - tile.v0) * j / n);
    positions.push(...p.map(v => v * radius)); normals.push(...p);
    st.push((2 + 512 * i / n) / 516, (2 + 512 * (1 - j / n)) / 516);
  }
  for (let j = 0; j < n; j++) for (let i = 0; i < n; i++) {
    const a = j * (n + 1) + i, b = a + 1, c = a + n + 1, d = c + 1;
    indices.push(a, c, b, b, c, d);
  }
  return new Cesium.Geometry({ attributes: {
    position: new Cesium.GeometryAttribute({componentDatatype: Cesium.ComponentDatatype.DOUBLE, componentsPerAttribute: 3, values: new Float64Array(positions)}),
    normal: new Cesium.GeometryAttribute({componentDatatype: Cesium.ComponentDatatype.FLOAT, componentsPerAttribute: 3, values: new Float32Array(normals)}),
    st: new Cesium.GeometryAttribute({componentDatatype: Cesium.ComponentDatatype.FLOAT, componentsPerAttribute: 2, values: new Float32Array(st)}),
  }, indices: new Uint16Array(indices), primitiveType: Cesium.PrimitiveType.TRIANGLES, boundingSphere: Cesium.BoundingSphere.fromVertices(positions) });
}

export function createSphericalTileLod({viewer, inside = false, baseTextureUrl, urlTemplate, maxLevel = 3,
  cacheLimit = 48, maxConcurrent = 4, lit = false, requestRender, memoryPolicy = getGpuMemoryPolicy()}) {
  const scene = viewer.scene;
  const mobile = memoryPolicy.mobile;
  cacheLimit = mobile ? Math.min(cacheLimit, memoryPolicy.celestialTileLimit) : cacheLimit;
  maxConcurrent = mobile ? Math.min(maxConcurrent, memoryPolicy.maxConcurrent) : maxConcurrent;
  const blobCache = new BoundedBlobCache(memoryPolicy.encodedBlobBudget);
  let bodyVisible = true;
  let source = {baseTextureUrl, urlTemplate, maxLevel}, generation = 0, destroyed = false;
  let frame = null, lastSelection = -Infinity, serial = 0, queue = [], active = 0;
  let selected = [], base = null, wanted = new Set(), visible = new Set();
  const entries = new Map(), descriptors = new Map(), failures = new Map(), pending = new Map();
  const collection = scene.primitives.add(new Cesium.PrimitiveCollection());
  const inverse = new Cesium.Matrix4();
  const stats = {requests: 0, completed: 0, aborted: 0, failed: 0, cacheHits: 0, gpuReleases: 0};
  const render = () => { requestRender?.(); scene.requestRender(); };
  const descriptor = (z, x, y) => {
    const key = `${z}/${x}/${y}`;
    if (!descriptors.has(key)) descriptors.set(key, tileDescriptor(z, x, y));
    return descriptors.get(key);
  };
  function createEntry(image, tile) {
    const material = lit ? new Cesium.Material({translucent: false, fabric: {
      type: 'CelestialTileLitImage', uniforms: {image, sunDirectionEC: new Cesium.Cartesian3(0, 0, 1), ambient: 0.025},
      source: `uniform sampler2D image; uniform vec3 sunDirectionEC; uniform float ambient;
        czm_material czm_getMaterial(czm_materialInput materialInput) {
          czm_material m = czm_getDefaultMaterial(materialInput);
          float light = smoothstep(0.0, 0.16, max(dot(normalize(materialInput.normalEC), normalize(sunDirectionEC)), 0.0));
          m.diffuse = vec3(0.0); m.emission = texture(image, materialInput.st).rgb * mix(ambient, 1.0, light);
          m.specular = 0.0; m.alpha = 1.0; return m;
        }`
    }}) : Cesium.Material.fromType('Image', {image, color: new Cesium.Color(1, 1, 1, 1)});
    material.translucent = false;
    // Cesium 1.118 queues a decoded image on the first update and uploads it on
    // the second. Do both while hidden: exposing it earlier draws a white tile.
    try {
      material.update(scene.context);
      material.update(scene.context);
      const texture = material._textures.image;
      if (!texture || texture === scene.context.defaultTexture ||
          texture.width !== image.naturalWidth || texture.height !== image.naturalHeight) {
        throw new Error('Celestial image texture is not ready');
      }
    } catch (error) { material.destroy(); throw error; }
    const geometry = tile ? patchGeometry(tile, inside) : new Cesium.SphereGeometry({radius: 1,
      slicePartitions: 128, stackPartitions: 64, vertexFormat: Cesium.MaterialAppearance.MaterialSupport.TEXTURED.vertexFormat});
    const primitive = collection.add(new Cesium.Primitive({geometryInstances: new Cesium.GeometryInstance({geometry}),
      appearance: new Cesium.MaterialAppearance({material, flat: !lit, faceForward: inside, translucent: false, closed: !inside,
        renderState: {cull: {enabled: true, face: inside ? Cesium.CullFace.FRONT : Cesium.CullFace.BACK},
          depthTest: {enabled: true}, depthMask: true, blending: Cesium.BlendingState.ALPHA_BLEND}}),
      asynchronous: false, allowPicking: false, compressVertices: false, show: false}));
    return {primitive, material, used: ++serial, lastVisibleAt: performance.now(), image};
  }
  function dispose(entry) { if (entry) { stats.gpuReleases++; collection.remove(entry.primitive); if (!entry.material.isDestroyed()) entry.material.destroy(); entry.image.src = ''; entry.image = null; } }
  function visual(entry, show) {
    if (!entry) return;
    entry.primitive.show = show;
    Cesium.Matrix4.clone(frame.modelMatrix, entry.primitive.modelMatrix);
    if (lit) {
      entry.material.uniforms.ambient = frame.ambient ?? 0.025;
      if (frame.sunDirectionEC) Cesium.Cartesian3.clone(frame.sunDirectionEC, entry.material.uniforms.sunDirectionEC);
    } else if (entry.material.uniforms.color) entry.material.uniforms.color.alpha = frame.opacity ?? 1;
  }
  function refresh() {
    if (!frame || destroyed) return;
    collection.show = bodyVisible;
    visible = new Set();
    if (bodyVisible) for (const tile of selected) {
      let z = tile.z, x = tile.x, y = tile.y;
      while (z >= 0) {
        const key = `${z}/${x}/${y}`;
        if (entries.has(key)) { visible.add(key); break; }
        x = Math.floor(x / 2); y = Math.floor(y / 2); z--;
      }
    }
    // Never overlay an incomplete child branch on a resident parent: same depth would flicker.
    for (const key of [...visible]) {
      let [z,x,y] = key.split('/').map(Number);
      while (z > 0) {z--;x=Math.floor(x/2);y=Math.floor(y/2);if(visible.has(`${z}/${x}/${y}`)){visible.delete(key);break;}}
    }
    visual(base, bodyVisible);
    for (const [key, entry] of entries) {const show=visible.has(key);visual(entry, show);if(show){entry.used=++serial;entry.lastVisibleAt=performance.now();}}
    for (const [key,entry] of entries) if(!visible.has(key) && (mobile || performance.now()-entry.lastVisibleAt>15000)) {
      entries.delete(key);dispose(entry);
    }
    if (mobile && !bodyVisible) {dispose(base);base=null;}
    const cold = [...entries].filter(([key]) => !visible.has(key)).sort((a,b)=>a[1].used-b[1].used);
    while (entries.size > cacheLimit && cold.length) {const [key,entry]=cold.shift();entries.delete(key);dispose(entry);}
  }
  async function load(item) {
    const token = generation, abort = new AbortController(); active++; pending.set(item.key, abort);
    try {
      let blob = blobCache.get(item.url);
      if (blob) stats.cacheHits++;
      else {
        stats.requests++;
        const response = await fetch(item.url, {signal: abort.signal});
        if (!response.ok) throw new Error(`Tile HTTP ${response.status}`);
        blob = await response.blob();
        if (blob.size > 8 * 1024 * 1024) throw new Error('Unexpectedly large celestial tile');
        if (destroyed || abort.signal.aborted) return;
      }
      const objectUrl = URL.createObjectURL(blob), image = new Image();
      try {image.src=objectUrl;await image.decode();} catch(error) {image.src='';throw error;} finally {URL.revokeObjectURL(objectUrl);}
      if (!destroyed && !abort.signal.aborted) blobCache.set(item.url, blob);
      if (destroyed || token !== generation || abort.signal.aborted || !bodyVisible || (item.key !== 'base' && (mobile || !inside) && !wanted.has(item.key))) {image.src='';return;}
      const entry = createEntry(image, item.tile);
      if (item.key === 'base') {dispose(base);base=entry;} else {dispose(entries.get(item.key));entries.set(item.key,entry);}
      failures.delete(item.key);stats.completed++;refresh();render();
    } catch(error) {
      if(error.name==='AbortError'||abort.signal.aborted)stats.aborted++;
      else if(token===generation){stats.failed++;failures.set(item.key,performance.now()+10000);console.warn('Celestial tile unavailable',item.key,error.message);}
    } finally {if(pending.get(item.key)===abort)pending.delete(item.key);active--;pump();}
  }
  function pump() {
    if(destroyed||!bodyVisible)return;
    while(active<maxConcurrent&&queue.length) {
      const item=queue.shift();if(pending.has(item.key)||(item.key==='base'?base:entries.has(item.key)))continue;
      if((failures.get(item.key)||0)>performance.now())continue;
      void load(item);
    }
  }
  function selectTiles() {
    const camera=viewer.camera;
    Cesium.Matrix4.inverse(frame.modelMatrix,inverse);
    const local=Cesium.Matrix4.multiplyByPoint(inverse,camera.positionWC,new Cesium.Cartesian3());
    const localCamera=[local.x,local.y,local.z];
    const culling=camera.frustum.computeCullingVolume(camera.positionWC,camera.directionWC,camera.upWC);
    const fov=camera.frustum.fovy||Math.PI/3;
    const focal=scene.drawingBufferHeight/(2*Math.tan(fov/2));
    const scale=Cesium.Matrix4.getMaximumScale(frame.modelMatrix);
    const center=Cesium.Matrix4.getTranslation(frame.modelMatrix,new Cesium.Cartesian3());
    const localOccluders=(frame.occluders||[]).map(o=>{
      const delta=Cesium.Cartesian3.subtract(o.center,camera.positionWC,new Cesium.Cartesian3());
      const d=Cesium.Cartesian3.magnitude(delta);
      const p=Cesium.Matrix4.multiplyByPointAsVector(inverse,delta,new Cesium.Cartesian3());
      return {direction:[p.x,p.y,p.z],angle:d>o.radius?Math.asin(o.radius/d):Math.PI};
    });
    selected=[];
    function visit(tile) {
      if(!inside&&tileBehindHorizon(tile,localCamera))return;
      if(inside&&localOccluders.some(o=>tileCoveredByOccluder(tile,o.direction,o.angle)))return;
      const a=Math.min(Math.PI/2,tile.angle), c=tile.angle>=Math.PI/2?0:Math.cos(a);
      const bound=new Cesium.BoundingSphere(new Cesium.Cartesian3(...tile.center.map(v=>v*c)),Math.sin(a)+.003);
      const worldBound=Cesium.BoundingSphere.transform(bound,frame.modelMatrix,new Cesium.BoundingSphere());
      if(culling.computeVisibility(worldBound)===Cesium.Intersect.OUTSIDE)return;
      const surface=Cesium.Cartesian3.add(center,Cesium.Matrix4.multiplyByPointAsVector(frame.modelMatrix,new Cesium.Cartesian3(...tile.center),new Cesium.Cartesian3()),new Cesium.Cartesian3());
      const distance=Cesium.Cartesian3.distance(camera.positionWC,surface);
      const pixels=inside?2*tile.angle*focal:2*scale*Math.sin(a)*focal/Math.max(scale*.025,distance-worldBound.radius);
      if(!inside && tile.z===0 && pixels<=512)return;
      // Sky has a permanent half-resolution base; only add full-detail patches.
      if(tile.z<source.maxLevel&&(inside||pixels>600)) {
        for(let dy=0;dy<2;dy++)for(let dx=0;dx<2;dx++)visit(descriptor(tile.z+1,tile.x*2+dx,tile.y*2+dy));
      } else selected.push(tile);
    }
    if(source.urlTemplate){visit(descriptor(0,0,0));visit(descriptor(0,1,0));}
    // Prefer screen-central tiles if the cache budget is smaller than the current demand.
    const dir=Cesium.Matrix4.multiplyByPointAsVector(inverse,camera.directionWC,new Cesium.Cartesian3());
    selected.sort((a,b)=>dot(b.center,inside?[dir.x,dir.y,dir.z]:localCamera)-dot(a.center,inside?[dir.x,dir.y,dir.z]:localCamera));
    selected=selected.slice(0,Math.max(1,cacheLimit));
    wanted=new Set(selected.map(t=>t.key));
    if (mobile && inside && source.urlTemplate) bodyVisible = selected.length > 0;
    queue=[];
    if(bodyVisible&&!base)queue.push({key:'base',url:source.baseTextureUrl});
    for(const tile of selected) {
      // Include an immediately available parent during refinement, but don't download unseen ancestors.
      queue.push({key:tile.key,tile,url:source.urlTemplate.replace('{z}',tile.z).replace('{x}',tile.x).replace('{y}',tile.y)});
    }
    // Finish already-started sky requests into the bounded LRU for quick reversals.
    for(const [key,abort] of pending)if(!inside&&!mobile&&key!=='base'&&!wanted.has(key))abort.abort();
  }
  return {
    update(next) {
      if(destroyed)return;frame=next;
      bodyVisible = next.show !== false && !(mobile && typeof document !== 'undefined' && document.hidden);
      if (mobile && bodyVisible && !inside) {
        const bounds = Cesium.BoundingSphere.transform(new Cesium.BoundingSphere(Cesium.Cartesian3.ZERO, 1.001), next.modelMatrix, new Cesium.BoundingSphere());
        const camera = viewer.camera;
        bodyVisible = camera.frustum.computeCullingVolume(camera.positionWC, camera.directionWC, camera.upWC).computeVisibility(bounds) !== Cesium.Intersect.OUTSIDE;
      }
      if(!bodyVisible){selected=[];wanted.clear();queue=[];lastSelection=-Infinity;for(const abort of pending.values())abort.abort();refresh();return;}
      const now=performance.now();if(now-lastSelection>=160){lastSelection=now;selectTiles();}
      if (mobile && inside && source.urlTemplate && !selected.length) bodyVisible=false;
      refresh();pump();
    },
    releaseGpu() {
      bodyVisible=false;selected=[];wanted.clear();queue=[];lastSelection=-Infinity;
      for(const abort of pending.values())abort.abort();
      for(const entry of entries.values())dispose(entry);entries.clear();dispose(base);base=null;visible.clear();
    },
    setSource(next) {
      const target={...source,...next};
      if(target.baseTextureUrl===source.baseTextureUrl&&target.urlTemplate===source.urlTemplate&&target.maxLevel===source.maxLevel)return;
      source=target;generation++;for(const abort of pending.values())abort.abort();queue=[];selected=[];wanted.clear();failures.clear();
      for(const entry of entries.values())dispose(entry);entries.clear();dispose(base);base=null;lastSelection=-Infinity;render();
    },
    getStateForDebug:()=>({...stats,show:bodyVisible,mobile,encodedCacheBytes:blobCache.byteLength,encodedCacheEntries:blobCache.size,gpuTextureBytes:(base?base.image.naturalWidth*base.image.naturalHeight*4:0)+entries.size*516*516*4,ambient:frame?.ambient??null,visibleTiles:visible.size,cachedTiles:entries.size,inFlight:active,queued:queue.length,
      selectedTiles:selected.length,level:selected.length?Math.max(...selected.map(t=>t.z)):null,overviewReady:Boolean(base),maxLevel:source.maxLevel}),
    destroy(){if(destroyed)return;destroyed=true;generation++;for(const abort of pending.values())abort.abort();queue=[];blobCache.clear();for(const entry of entries.values())dispose(entry);entries.clear();dispose(base);base=null;scene.primitives.remove(collection);}
  };
}
