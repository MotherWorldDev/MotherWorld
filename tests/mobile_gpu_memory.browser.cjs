const {chromium}=require(process.env.MOTHERWORLD_PLAYWRIGHT || 'playwright');
const fs=require('node:fs'),http=require('node:http'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve('frontend/public');
(async()=>{
 const requests=[];
 const server=http.createServer((req,res)=>{
  const url=req.url.split('?')[0];requests.push(url);
  if(url==='/'){res.setHeader('Content-Type','text/html');return res.end('<meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0}#globe{width:100vw;height:100vh}.cesium-widget,.cesium-widget canvas{width:100%;height:100%}</style><div id="globe"></div>');}
  const file=url==='/Cesium.js'?path.resolve('.cache/render-quality-check/Cesium.js'):path.join(root,decodeURIComponent(url));
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.webp')?'image/webp':file.endsWith('.jpg')?'image/jpeg':'application/octet-stream');
  fs.createReadStream(file).on('error',()=>{res.statusCode=404;res.end();}).pipe(res);
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({executablePath:process.env.MOTHERWORLD_BROWSER,headless:true,args:['--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:390,height:650},deviceScaleFactor:2,isMobile:true,hasTouch:true,userAgent:'Mozilla/5.0 (Linux; Android 14; SM-A175F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36'});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('404'))errors.push(m.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(()=>window.CESIUM_BASE_URL='https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/');
  await page.addScriptTag({url:'/Cesium.js'});
  await page.evaluate(async()=>{
   window.gpu={live:new Set(),created:0,deleted:0,peak:0};
   for(const proto of [WebGLRenderingContext.prototype,WebGL2RenderingContext.prototype]){
    const create=proto.createTexture,remove=proto.deleteTexture;
    proto.createTexture=function(...args){const t=create.apply(this,args);gpu.live.add(t);gpu.created++;gpu.peak=Math.max(gpu.peak,gpu.live.size);return t;};
    proto.deleteTexture=function(t){if(gpu.live.delete(t))gpu.deleted++;return remove.call(this,t);};
   }
   const {createGlobeExplorer}=await import('/js/globe.js');const {APP_CONFIG}=await import('/js/config.js');
   const config=structuredClone(APP_CONFIG);config.globe.realtimeClockEnabled=false;
   window.app=createGlobeExplorer({containerId:'globe',appConfig:config,getBiomeColor:()=>Cesium.Color.WHITE});window.v=app.viewer;
   v.clock.currentTime=Cesium.JulianDate.fromIso8601('2026-09-10T08:00:00Z');
   window.pageHidden=false;Object.defineProperty(document,'hidden',{configurable:true,get:()=>pageHidden});
   window.background=hidden=>{pageHidden=hidden;document.dispatchEvent(new Event('visibilitychange'));};
   window.snap=()=>({textures:gpu.live.size,deleted:gpu.deleted,peak:gpu.peak,...app.getGpuMemoryState()});
  });
  await page.waitForTimeout(1800);
  assert.equal(await page.evaluate(()=>app.getGpuMemoryState().mobile),true);
  assert.equal(await page.evaluate(()=>app.getGpuMemoryState().earthTileCacheTarget),24);
  const started=Date.now(),soak=Number(process.env.MOTHERWORLD_TEST_SOAK_MS || 180000),samples=[];
  let cycle=0;
  while(Date.now()-started<soak){
   await page.evaluate(cycle=>{app.activateEarthAnchorTarget();v.camera.cancelFlight();v.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);v.camera.setView({destination:Cesium.Cartesian3.fromDegrees((cycle*70)%360-180,20,2000000)});v.scene.requestRender();},cycle);
   await page.waitForTimeout(1000);
   const earth=await page.evaluate(()=>snap());assert.equal(earth.contextLosses,0);
   await page.evaluate(()=>app.activateMoonAnchorTarget());
   await page.waitForTimeout(1300);
   await page.evaluate(()=>{const p=Cesium.Cartesian3.normalize(v.camera.position,new Cesium.Cartesian3());Cesium.Cartesian3.multiplyByScalar(p,Cesium.Ellipsoid.MOON.maximumRadius*1.3,p);v.camera.lookAtTransform(v.camera.transform,p);v.scene.requestRender();});
   await page.waitForTimeout(600);
   await page.evaluate(cycle=>app.setMoonSurfaceMode(cycle%2?'natural':'geology'),cycle);
   await page.waitForTimeout(600);
   await page.waitForFunction(()=>{const m=app.getGpuMemoryState().moon;return m.visibleTiles>0&&m.inFlight===0&&m.queued===0;},null,{timeout:20000});
   const active=await page.evaluate(()=>snap());
   assert.ok(active.moon.visibleTiles>0,'Close-up Moon must exercise detail textures');
   for(const body of [active.sky,active.moon])if(body){assert.ok(body.cachedTiles<=24);assert.equal(body.cachedTiles,body.visibleTiles);assert.ok(body.encodedCacheBytes<=16*1024*1024);}
   await page.evaluate(()=>background(true));
   const hidden=await page.evaluate(()=>snap());
   assert.equal(hidden.sky.gpuTextureBytes,0);assert.equal(hidden.moon.gpuTextureBytes,0);assert.equal(hidden.suspended,true);
   assert.ok(hidden.deleted>0);assert.equal(await page.evaluate(()=>v.useDefaultRenderLoop),false);
   samples.push({cycle,earthTextures:earth.textures,activeTextures:active.textures,hiddenTextures:hidden.textures,earthTiles:earth.earthResidentTiles,earthCache:earth.earthEncodedCacheBytes,deleted:hidden.deleted});
   await page.evaluate(()=>background(false));await page.waitForTimeout(100);
   if(cycle%10===0)console.log('memory-cycle',JSON.stringify(samples.at(-1)));
   cycle++;
  }
  const ending=await page.evaluate(()=>snap());
  assert.equal(ending.contextLosses,0);assert.deepEqual(errors,[]);
  // Cached render targets may settle during the first cycle, but empty-scene
  // texture counts must not grow with every subsequent view switch.
  const cold=samples.slice(1).map(s=>s.hiddenTextures);
  if(cold.length>2)assert.ok(Math.max(...cold)-Math.min(...cold)<=8,JSON.stringify(cold));
  await page.evaluate(()=>v.destroy());
  const afterDestroy=await page.evaluate(()=>gpu.live.size);
  assert.equal(afterDestroy,0,'Viewer destruction must delete all WebGL textures');
  console.log('mobile-memory-pass',JSON.stringify({elapsedMs:Date.now()-started,cycles:cycle,peakTextures:ending.peak,deletedTextures:ending.deleted,hiddenTextureCounts:cold,afterDestroy,errors}));
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
