import test from 'node:test';
import assert from 'node:assert/strict';
import { spherePoint, tileDescriptor, tileBehindHorizon, tileCoveredByOccluder } from '../frontend/public/js/sphericalTileLod.js';
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-10,`${a} != ${b}`);
test('tile coordinates preserve Cesium sphere orientation and close the dateline',()=>{
  for(const [u,p] of [[0,[1,0,0]],[.25,[0,1,0]],[.5,[-1,0,0]],[.75,[0,-1,0]],[1,[1,0,0]]])spherePoint(u,.5).forEach((v,i)=>near(v,p[i]));
  near(spherePoint(.37,0)[2],1);near(spherePoint(.37,1)[2],-1);
  const a=tileDescriptor(3,7,2),b=tileDescriptor(3,8,2);assert.equal(a.u1,b.u0);assert.equal(a.v0,b.v0);
});
test('Moon horizon removes hidden back tiles but retains intersecting limbs',()=>{
  const front=tileDescriptor(3,0,4),back=tileDescriptor(3,8,4);
  assert.equal(tileBehindHorizon(front,[3,0,0]),false);
  assert.equal(tileBehindHorizon(back,[3,0,0]),true);
  const limb=tileDescriptor(3,2,4);assert.equal(tileBehindHorizon(limb,[3,0,0]),false);
});
test('sky occlusion only removes patches completely inside an occluding disk',()=>{
  const tile=tileDescriptor(3,0,3);
  assert.equal(tileCoveredByOccluder(tile,tile.center,tile.angle+.1),true);
  assert.equal(tileCoveredByOccluder(tile,tile.center,tile.angle-.01),false);
  assert.equal(tileCoveredByOccluder(tile,tile.center.map(v=>-v),Math.PI/3),false);
});
