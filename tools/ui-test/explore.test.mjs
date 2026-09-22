import test from 'node:test';
import assert from 'node:assert/strict';
import { exploreTree } from './explore.mjs';
import { parseHierarchy } from './hierarchy.mjs';
const xml = (body) => `<hierarchy><node package="com.android.settings" class="android.widget.FrameLayout" enabled="true" bounds="[0,0][100,200]">${body}</node></hierarchy>`;
const menu = '<node package="com.android.settings" class="android.widget.LinearLayout" clickable="true" enabled="true" bounds="[0,0][100,100]"><node package="com.android.settings" class="android.widget.TextView" resource-id="android:id/title" text="显示" enabled="true" bounds="[0,0][100,100]"/></node>';
const scope = {allowedControls:[],allowedNavigation:[],allowedPackages:['com.android.settings'],autoNavigation:true,maxActions:4,maxPages:3,maxDepth:2,visualAssertion:''};
function navigation() {
  let child=false, taps=0, backs=0;
  return {read:async()=>parseHierarchy(xml(child?'<node package="com.android.settings" text="亮度"/>':menu)),tap:async()=>{child=true;taps++;},back:async()=>{child=false;backs++;},get taps(){return taps;},get backs(){return backs;}};
}
test('scan discovers pages and returns without any model',async()=>{
  const d=navigation(), events=[];
  assert.equal(await exploreTree(null,d,{type:'scan',exploration:scope},e=>events.push(e)),'passed');
  assert.equal(events.filter(e=>e.type==='graph-page').length,2);
  assert.equal(d.taps,1);assert.equal(d.backs,1);
});
test('depth limit reports incomplete coverage without clicking',async()=>{
  const d=navigation();assert.equal(await exploreTree(null,d,{exploration:{...scope,maxDepth:0}},()=>{}),'needs_review');assert.equal(d.taps,0);
});
test('failed return stops further navigation',async()=>{
  const d=navigation();d.back=async()=>{};
  assert.equal(await exploreTree(null,d,{exploration:scope},()=>{}),'needs_review');assert.equal(d.taps,1);
});
test('checkable controls are excluded from scan menu discovery',async()=>{
  const d=navigation();d.read=async()=>parseHierarchy(xml(menu.replace('</node>','<node checkable="true"/></node>')));
  assert.equal(await exploreTree(null,d,{exploration:scope},()=>{}),'passed');assert.equal(d.taps,0);
});
test('switch test confirms change and restores with structural reads',async()=>{
  let checked=false,taps=0;
  const d={read:async()=>parseHierarchy(xml(`<node package="com.android.settings" class="android.widget.Switch" text="深色主题" enabled="true" checkable="true" checked="${checked}" bounds="[0,0][100,100]"/>`)),tap:async()=>{checked=!checked;taps++;}};
  assert.equal(await exploreTree(null,d,{exploration:{...scope,allowedControls:['深色主题']}},()=>{}),'passed');assert.equal(checked,false);assert.equal(taps,2);
});
