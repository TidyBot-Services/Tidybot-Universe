// User 11-08: switching tasks in the left sidebar often needed several clicks. Refresh rebuilt the
// sidebar between press and release, and the browser drops a click whose press and release land on
// different elements. The rebuild now waits until the press ends.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
const start=source.indexOf('  function sidebarPressHeld('),end=source.indexOf('  // End press guard.');
assert.ok(start>0&&end>start);

function target(){const handlers={};return {handlers,addEventListener:(type,fn)=>{(handlers[type]||=[]).push(fn);},fire:(type,e={})=>(handlers[type]||[]).forEach(fn=>fn(e))};}
function setup(){
  const area=target(),win=target(),later=[],renders=[];
  const api=vm.runInNewContext(source.slice(start,end)+'\n({sidebarPressHeld,bindSidebarPress})',{});
  api.bindSidebarPress(area,win,f=>later.push(f));
  const render=()=>{if(api.sidebarPressHeld(render))return;renders.push('render');};
  return {area,win,later,renders,render};
}

test('a rebuild during a press waits for the release, then runs once',()=>{
  const {area,win,later,renders,render}=setup();
  area.fire('pointerdown',{button:0});
  render();render();
  assert.deepEqual(renders,[]);
  win.fire('pointerup');
  assert.deepEqual(renders,[],'the click must reach the old card before the rebuild');
  later.forEach(f=>f());
  assert.deepEqual(renders,['render']);
});

test('without a press, and after a cancelled press or lost focus, rebuilds run right away',()=>{
  const {area,win,later,renders,render}=setup();
  render();assert.deepEqual(renders,['render']);
  area.fire('pointerdown',{button:2});render();assert.equal(renders.length,2,'right button does not hold');
  for(const type of ['pointercancel','blur']){
    area.fire('pointerdown',{button:0});render();win.fire(type);later.splice(0).forEach(f=>f());
  }
  assert.equal(renders.length,4);
  render();assert.equal(renders.length,5);
});

test('the real sidebar renderer goes through the guard',()=>{
  assert.match(source,/function renderSidebar\(\)\{\n    if\(sidebarPressHeld\(renderSidebar\)\)return;/);
  assert.match(source,/bindSidebarPress\(sidebar,window,f=>setTimeout\(f,0\)\);/);
});
