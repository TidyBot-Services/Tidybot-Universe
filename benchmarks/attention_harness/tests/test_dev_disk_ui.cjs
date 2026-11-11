const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
function block(name){const start=source.indexOf('  function '+name+'(');return source.slice(start,source.indexOf('\n  function ',start+1));}
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const api=vm.runInNewContext(block('operatorOutcome')+block('devStartButton')+'\n({operatorOutcome,devStartButton})',{esc});
const detail='磁盘剩余 14.0 GB，低于 15 GB，磁盘不足，已暂停';
function snapshot(extra={}){return {dev_v2:{run_controls:{state:'paused',pause_reason:'disk_low',pause_detail:detail,can_resume:false,resume_blocked_reason:'磁盘剩余 14.0 GB，低于 40 GB，未启动',...extra}}};}
test('the run page shows the disk reason and blocked Resume',()=>{
  assert.ok(api.operatorOutcome(snapshot()).includes(detail));
  const controls=api.devStartButton(snapshot());
  assert.ok(controls.includes(detail));assert.match(controls,/data-dev-resume disabled/);assert.match(controls,/低于 40 GB/);
});
test('Resume is enabled once space returns',()=>{
  const value=snapshot({can_resume:true,resume_blocked_reason:null});
  assert.match(api.operatorOutcome(value),/Press Resume/);
  assert.doesNotMatch(api.devStartButton(value),/data-dev-resume disabled/);
});
test('pausing shows the disk reason while the current step finishes',()=>{
  const value=snapshot({state:'pausing',can_keep_running:true,current_step:'running v1 on the robot'});
  assert.match(api.operatorOutcome(value),/will finish normally/);
  assert.ok(api.devStartButton(value).includes(detail));
});
// 11-11: agent runs and Remote runs offered no Pause / Resume.
const running={state:'running',can_pause:true,can_resume:false,can_keep_running:false};
const paused={state:'paused',can_pause:false,can_resume:true,can_keep_running:false};
test('a running agent run shows Pause, not a blank Start button',()=>{
  const controls=api.devStartButton({dev_v2:{lifecycle:{phase:'model',can_stop:true},run_controls:running}});
  assert.match(controls,/data-dev-pause/);assert.doesNotMatch(controls,/undefined cells/);
  assert.match(api.devStartButton({dev_v2:{lifecycle:{phase:'prepared',can_start:true,start_label:'Start run',scope_count:1},run_controls:{state:'created'}}}),/>Start run</);
});
test('on Remote only the owner of a real run gets Pause and Resume',()=>{
  const mine={real:true,mine:true,state:'running'};
  assert.match(api.devStartButton({lobby:mine,dev_v2:{lifecycle:{phase:'model',can_stop:true},run_controls:running}}),/data-dev-pause/);
  assert.match(api.devStartButton({lobby:mine,dev_v2:{run_controls:paused}}),/data-dev-resume >▶ Resume/);
  assert.equal(api.devStartButton({lobby:{...mine,mine:false},dev_v2:{run_controls:running}}),'');
  assert.equal(api.devStartButton({lobby:{...mine,real:false},dev_v2:{run_controls:running}}),'');
  // A run waiting in line, or one that ended, shows no dead button there.
  assert.equal(api.devStartButton({lobby:mine,dev_v2:{lifecycle:{phase:'prepared',can_start:true,start_label:'Start run',scope_count:1},run_controls:{state:'created',can_start:true}}}),'');
  assert.equal(api.devStartButton({lobby:mine,dev_v2:{run_controls:{state:'ended',can_resume:false}}}),'');
});
