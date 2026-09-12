import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../frontend/public/js/startupLoading.js',import.meta.url),'utf8');
const {waitForInitialView}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
function setup(){
 const skip=new EventTarget();skip.hidden=true;
 const progress={value:0},status={textContent:''};
 const doc={hidden:false,getElementById:id=>({'startup-progress':progress,'startup-continue':skip,'loading-text':status}[id])};
 let state={earthReady:false,skyReady:false};
 const globe={getInitialViewState:()=>state,viewer:{scene:{requestRender(){}}}};
 return {doc,skip,progress,globe,set:s=>state=s};
}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
test('waits for both imagery systems and restarts settling after new work',async()=>{
 const f=setup();let finished=false;
 const done=waitForInitialView(f.globe,{doc:f.doc,pollMs:2,stableMs:25}).then(()=>finished=true);
 f.set({earthReady:true,skyReady:false});await pause(12);assert.equal(finished,false);
 f.set({earthReady:true,skyReady:true});await pause(8);
 f.set({earthReady:false,skyReady:true});await pause(8);assert.equal(finished,false);
 f.set({earthReady:true,skyReady:true});await done;assert.equal(f.progress.value,100);
});
test('stalled loading offers an explicit exit and removes its listener',async()=>{
 const f=setup();const done=waitForInitialView(f.globe,{doc:f.doc,pollMs:2,offerContinueMs:5});
 await pause(15);assert.equal(f.skip.hidden,false);assert.ok(f.progress.value<100);
 f.skip.dispatchEvent(new Event('click'));await done;assert.equal(f.skip.hidden,true);
});
