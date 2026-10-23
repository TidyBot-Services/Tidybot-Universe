// Real Chromium DOM tests. API routes are isolated fixtures; no simulator runs.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const repo = process.env.ATTENTION_REPO || path.resolve(__dirname, '../../..');
const source = fs.readFileSync(path.join(repo, 'benchmarks/attention_harness/ui/live.js'), 'utf8');
const fullHtml = fs.readFileSync(path.join(repo, 'benchmarks/attention_harness/ui/index.html'), 'utf8');
const stylesheet = [...fullHtml.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
const englishCubeHint='Check the grasp height and lower it slightly. Close the gripper and confirm a secure grip before lifting.';

for(const width of [1280,390]) test(`CAMERA-365-SAVED: current view shows upright saved RGB at ${width}px`,async t=>{
  const snap=snapshot('old','failed');snap.run.suite='robocasa365';snap.run.task_id='pick_place_counter_to_sink';snap.requests=[];
  snap.station={status:'failed',camera_url:'/camera?run=old',camera_attempt_id:'old-a0',camera_source:'saved_evidence',camera_image_transform:'flip_y'};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
  const frame=page.locator('#ab-camera-frame');await frame.waitFor();
  assert.match(await frame.getAttribute('style'),/scaleY\(-1\)/);
  assert.match(await page.locator('.ab-station-camera').innerText(),/Saved frame/);
  assert.equal(await page.getByText('No camera feed connected',{exact:true}).count(),0);
  assert.equal(await frame.getAttribute('data-live-stream-key'),null);
  await tick();assert.match(await frame.getAttribute('src'),/camera/);
  assert.equal(state.posts.length,0);
});
for(const width of [1280,390]) test(`CAMERA-365-EMPTY: absent evidence stays honest at ${width}px`,async t=>{
  const snap=snapshot('old','failed');snap.run.suite='robocasa365';snap.requests=[];snap.station={status:'failed',camera_url:null};
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
  assert.equal(await page.locator('#ab-camera-frame').count(),0);
  assert.equal(await page.getByText('No camera feed connected',{exact:true}).count(),1);
  assert.equal(state.posts.length,0);
});

for (const width of [1280,390]) for (const taskId of ['pick_place_counter_to_sink','open_cabinet'])
test(`DAILY-365-HELP9: ${taskId} permits nine help credits and rejects ten at ${width}px`,async t=>{
  const next=path.join(repo,'openarm_bimanual/launch_configs/main-ui-365-help9-v2/catalog.json');
  const catalog=fs.existsSync(next)?next:path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/main_ui_with_robocasa365_v1/catalog.json');
  const options=JSON.parse(execFileSync('/home/truares/桌面/attentionbench-week2-clean-venv/bin/python',[
    '-c','import json,sys;from pathlib import Path;from benchmarks.attention_harness.ui_launch import load_catalog,public_options;print(json.dumps(public_options(load_catalog(Path(sys.argv[1])))))',catalog
  ],{cwd:repo,encoding:'utf8'}));
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{s.launchOptions=options;}});
  await page.locator('[data-open-launch]').click();
  const selected=`robocasa365-${taskId}-dev101`;
  await selectLaunchTask(page,'robocasa365',selected);
  const form=page.locator('#ab-launch-form'),attempts=form.locator('[name=max_attempts]');
  assert.equal(await attempts.inputValue(),'10');
  assert.equal(await attempts.getAttribute('max'),'10');
  for(const [key,value] of Object.entries({assistance_credits:9,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300})) {
    assert.equal(await form.locator(`[name=${key}]`).inputValue(),String(value));
    assert.equal(await form.locator(`[name=${key}]`).getAttribute('max'),String(value));
  }
  const credits=form.locator('[name=assistance_credits]');
  await credits.fill('10');assert.equal(await credits.evaluate(e=>e.checkValidity()),false);
  await form.locator('button[type=submit]').click();assert.equal(state.posts.length,0);
  await credits.fill('9');assert.equal(await credits.evaluate(e=>e.checkValidity()),true);
  await attempts.fill('11');
  assert.equal(await attempts.evaluate(e=>e.checkValidity()),false);
  await form.locator('button[type=submit]').click();assert.equal(state.posts.length,0);
  await attempts.fill('10');assert.equal(await attempts.evaluate(e=>e.checkValidity()),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await form.locator('button[type=submit]').click();
  assert.equal(state.posts.length,1);assert.equal(state.posts[0].payload.max_attempts,10);assert.equal(state.posts[0].payload.assistance_credits,9);
  assert.equal(state.posts[0].payload.profile_id,selected);assert.equal(state.posts[0].payload.execution_target,'robocasa365_sim');
});

for (const width of [1280,390]) for (const taskId of ['pick_place_counter_to_sink','open_cabinet'])
test(`DAILY-365-10: ${taskId} permits ten attempts and rejects eleven at ${width}px`,async t=>{
  const next=path.join(repo,'openarm_bimanual/launch_configs/main-ui-365-attempt10-v1/catalog.json');
  const catalog=fs.existsSync(next)?next:path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/main_ui_with_robocasa365_v1/catalog.json');
  const options=JSON.parse(execFileSync('/home/truares/桌面/attentionbench-week2-clean-venv/bin/python',[
    '-c','import json,sys;from pathlib import Path;from benchmarks.attention_harness.ui_launch import load_catalog,public_options;print(json.dumps(public_options(load_catalog(Path(sys.argv[1])))))',catalog
  ],{cwd:repo,encoding:'utf8'}));
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{s.launchOptions=options;}});
  await page.locator('[data-open-launch]').click();
  const selected=`robocasa365-${taskId}-dev101`;
  await selectLaunchTask(page,'robocasa365',selected);
  const form=page.locator('#ab-launch-form'),attempts=form.locator('[name=max_attempts]');
  assert.equal(await attempts.inputValue(),'10');
  assert.equal(await attempts.getAttribute('max'),'10');
  for(const [key,value] of Object.entries({assistance_credits:1,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300})) {
    assert.equal(await form.locator(`[name=${key}]`).inputValue(),String(value));
    assert.equal(await form.locator(`[name=${key}]`).getAttribute('max'),String(value));
  }
  await attempts.fill('11');
  assert.equal(await attempts.evaluate(e=>e.checkValidity()),false);
  await form.locator('button[type=submit]').click();assert.equal(state.posts.length,0);
  await attempts.fill('10');assert.equal(await attempts.evaluate(e=>e.checkValidity()),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await form.locator('button[type=submit]').click();
  assert.equal(state.posts.length,1);assert.equal(state.posts[0].payload.max_attempts,10);
  assert.equal(state.posts[0].payload.profile_id,selected);assert.equal(state.posts[0].payload.execution_target,'robocasa365_sim');
});

for (const width of [1280,390]) for (const taskId of ['pick_place_counter_to_sink','open_cabinet'])
test(`MAIN-365-01: published main catalog routes ${taskId} with frozen budgets at ${width}px`,async t=>{
  const merged=path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/main_ui_with_robocasa365_v1/catalog.json');
  // Before publication, use the primary UI's actual previous catalog for RED.
  const catalog=fs.existsSync(merged)?merged:path.join(repo,'openarm_bimanual/launch_configs/stage-one-final-1/catalog.json');
  const options=JSON.parse(execFileSync('/home/truares/桌面/attentionbench-week2-clean-venv/bin/python',[
    '-c','import json,sys;from pathlib import Path;from benchmarks.attention_harness.ui_launch import load_catalog,public_options;print(json.dumps(public_options(load_catalog(Path(sys.argv[1])))))',catalog
  ],{cwd:repo,encoding:'utf8'}));
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{s.launchOptions=options;}});
  await page.locator('[data-open-launch]').click();
  const benchmark=page.getByLabel('Benchmark',{exact:true});
  assert.deepEqual(await benchmark.locator('option').evaluateAll(es=>es.map(e=>e.value)),['','robosuite','robocasa','robocasa365']);
  await benchmark.selectOption('robocasa365');
  const task=page.getByLabel('Task',{exact:true});
  assert.deepEqual(await task.locator('option').evaluateAll(es=>es.map(e=>e.value)),[
    '', 'robocasa365-pick_place_counter_to_sink-dev101','robocasa365-open_cabinet-dev101'
  ]);
  const selected=`robocasa365-${taskId}-dev101`;
  await task.selectOption(selected);
  const form=page.locator('#ab-launch-form');
  assert.equal(await form.locator('[name=robot_model]').isVisible(),false);
  assert.equal(await form.locator('[name=execution_target]').inputValue(),'robocasa365_sim');
  for(const [key,value] of Object.entries({max_attempts:2,assistance_credits:1,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300})) {
    assert.equal(await form.locator(`[name=${key}]`).inputValue(),String(value));
    assert.equal(await form.locator(`[name=${key}]`).getAttribute('max'),String(value));
  }
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await form.locator('button[type=submit]').click();
  assert.equal(state.posts.length,1);
  assert.deepEqual(state.posts[0].payload,{profile_id:selected,seed:101,execution_target:'robocasa365_sim',attention_policy:'reactive_help',assistance_mode:'live_human_first',max_attempts:2,assistance_credits:1,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300});
});

for(const width of [1280,390])test(`RESOLUTION-01: task choices omit camera variants and submit the approved 1080p profile at ${width}px`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{
    s.launchOptions={profiles:[
      {id:'legacy-small',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101,camera_width:64,camera_height:64},
      {id:'approved-hd',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101,camera_width:1920,camera_height:1080},
      {id:'sink',suite:'robocasa',task:'counter_to_sink',execution_target:'robocasa_sim',seed:101}
    ],attention_policies:['reactive_help'],assistance_modes:['live_human_first']};
  }});
  await page.locator('[data-open-launch]').click();
  const task=page.getByLabel('Task',{exact:true});
  await page.getByLabel('Benchmark',{exact:true}).selectOption('robosuite');
  assert.deepEqual(await task.locator('option').evaluateAll(es=>es.map(e=>({value:e.value,text:e.textContent}))),[
    {value:'',text:'Select a task'},{value:'approved-hd',text:'Lift the cube'}
  ]);
  await task.selectOption({label:'Lift the cube'});
  await task.click();
  assert.equal(await task.getByRole('option',{name:'Lift the cube',exact:true}).isVisible(),true);
  await evidence(page,`RESOLUTION-task-menu-${width}`);
  await page.keyboard.press('Escape');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#ab-launch-form button[type=submit]').click();
  assert.equal(state.posts.length,1);
  assert.equal(state.posts[0].pathname,'/api/runs/start');
  assert.equal(state.posts[0].payload.profile_id,'approved-hd');
  assert.equal(state.posts[0].payload.execution_target,'robosuite_sim');
  assert.equal(state.posts[0].payload.seed,101);
});

const benchmarkTaskProfiles = [
  {id:'lift-hd',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101,camera_width:1920,camera_height:1080},
  {id:'sink',suite:'robocasa',task:'counter_to_sink',execution_target:'robocasa_sim',seed:102},
  {id:'lift-small',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101,camera_width:64,camera_height:64},
  {id:'stack',suite:'robosuite',task:'cube_stack',execution_target:'robosuite_sim',seed:101,camera_width:1920,camera_height:1080},
  {id:'cabinet',suite:'robocasa',task:'counter_to_cab',execution_target:'robocasa_sim',seed:103},
  {id:'sink365',suite:'robocasa365',task:'pick_place_counter_to_sink',execution_target:'robocasa365_sim',seed:101},
];
function benchmarkTaskOptions(_,state) {
  state.launchOptions={profiles:benchmarkTaskProfiles,attention_policies:['autonomous','reactive_help'],assistance_modes:['benchmark_proxy','live_human_first']};
}
async function selectLaunchTask(page, suite='robosuite', profileId='lift') {
  await page.locator('#ab-launch-form select[name=benchmark]').selectOption(suite);
  await page.locator('#ab-launch-form select[name=profile_id]').selectOption(profileId);
}

for(const width of [1280,390])test(`BENCHMARK-TASK-01: benchmark stays visible and only reveals its approved tasks at ${width}px`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:benchmarkTaskOptions});
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.getByLabel('Benchmark',{exact:true}).count(),1);
  const benchmark=page.getByLabel('Benchmark',{exact:true}),task=page.getByLabel('Task',{exact:true}),start=page.locator('#ab-launch-form button[type=submit]');
  assert.equal(await benchmark.isVisible(),true);
  assert.equal(await benchmark.inputValue(),'');
  assert.deepEqual(await benchmark.locator('option').evaluateAll(es=>es.map(e=>e.value)),['','robosuite','robocasa','robocasa365']);
  assert.equal(await task.isVisible(),false);
  assert.equal(await task.isEnabled(),false);
  assert.equal(await start.isEnabled(),false);
  assert.match(await page.locator('[data-launch-preview]').innerText(),/Select a benchmark/);
  await evidence(page,`BENCHMARK-TASK-initial-${width}`);
  await benchmark.selectOption('robosuite');
  assert.equal(await task.isVisible(),true);
  assert.equal(await task.isEnabled(),true);
  assert.equal(await task.inputValue(),'');
  assert.deepEqual(await task.locator('option').evaluateAll(es=>es.map(e=>e.value)),['','lift-hd','stack']);
  assert.equal(await task.locator('option[value=lift-hd]').innerText(),'Lift the cube');
  assert.doesNotMatch(await task.innerText(),/64.?64|1920.?1080/);
  assert.doesNotMatch(await task.innerText(),/robosuite\s*\/|counter_to_sink|sink365/);
  assert.equal(await start.isEnabled(),false);
  await benchmark.focus();await page.keyboard.press('Tab');
  assert.equal(await task.evaluate(e=>e===document.activeElement),true);
  await task.click();await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
  assert.equal(await task.inputValue(),'lift-hd');
  assert.equal(await page.locator('#ab-launch-form [name=execution_target]').inputValue(),'robosuite_sim');
  assert.equal(await page.locator('#ab-launch-form [name=seed]').inputValue(),'101');
  assert.equal(await start.isEnabled(),true);
  await task.selectOption('stack');
  assert.equal(await task.inputValue(),'stack');
  await evidence(page,`BENCHMARK-TASK-selected-${width}`);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await page.locator('.ab-launch-body').evaluate(e=>e.scrollWidth<=e.clientWidth),true);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`BENCHMARK-TASK-02: switching benchmarks clears stale task, preserves drafts and submits the original contract at ${width}px`,async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:benchmarkTaskOptions});
  const draft=page.locator('#ab-hint-content');
  await draft.fill('Keep my reply draft');await draft.evaluate(e=>e.setSelectionRange(5,10));
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.getByLabel('Benchmark',{exact:true}).count(),1);
  await selectLaunchTask(page,'robosuite','lift-hd');
  const form=page.locator('#ab-launch-form'),benchmark=page.getByLabel('Benchmark',{exact:true}),task=page.getByLabel('Task',{exact:true});
  await benchmark.selectOption('robocasa');
  assert.equal(await task.inputValue(),'');
  assert.deepEqual(await task.locator('option').evaluateAll(es=>es.map(e=>e.value)),['','sink','cabinet']);
  assert.equal(await form.locator('[name=execution_target]').inputValue(),'');
  assert.equal(await form.locator('button[type=submit]').isEnabled(),false);
  await task.selectOption('sink');
  assert.equal(await form.locator('[name=seed]').inputValue(),'102');
  assert.equal(await form.locator('[name=execution_target]').inputValue(),'robocasa_sim');
  await form.locator('[name=assistance_credits]').fill('3');
  await task.focus();await tick();
  assert.equal(await task.inputValue(),'sink');
  assert.equal(await task.evaluate(e=>e===document.activeElement),true);
  assert.equal(await form.locator('[name=assistance_credits]').inputValue(),'3');
  assert.deepEqual(await draft.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd]),['Keep my reply draft',5,10]);
  assert.equal(state.posts.length,0);
  await evidence(page,`BENCHMARK-TASK-casa-${width}`);
  await form.locator('button[type=submit]').click();
  assert.equal(state.posts.length,1);
  assert.equal(state.posts[0].pathname,'/api/runs/start');
  assert.deepEqual(state.posts[0].payload,{profile_id:'sink',seed:102,execution_target:'robocasa_sim',attention_policy:'reactive_help',assistance_mode:'live_human_first',max_attempts:10,assistance_credits:3,token_limit:30000,human_deadline_seconds:600,overall_deadline_seconds:300});
});

test('BENCHMARK-TASK-03: dependent selections retain RoboCasa365 frozen budgets and return to ordinary defaults',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:benchmarkTaskOptions});
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.getByLabel('Benchmark',{exact:true}).count(),1);
  await selectLaunchTask(page,'robocasa365','sink365');
  const form=page.locator('#ab-launch-form');
  for(const [key,value] of Object.entries({max_attempts:2,assistance_credits:1,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300})) {
    assert.equal(await form.locator(`[name=${key}]`).inputValue(),String(value));
    assert.equal(await form.locator(`[name=${key}]`).getAttribute('max'),String(value));
  }
  assert.match(await form.locator('[name=overall_deadline_seconds]').evaluate(e=>e.closest('label').textContent),/Whole-case deadline/);
  await selectLaunchTask(page,'robocasa','cabinet');
  assert.equal(await form.locator('[name=seed]').inputValue(),'103');
  assert.equal(await form.locator('[name=max_attempts]').inputValue(),'10');
  assert.equal(await form.locator('[name=token_limit]').getAttribute('max'),'100000');
  assert.match(await form.locator('[name=overall_deadline_seconds]').evaluate(e=>e.closest('label').textContent),/Execution deadline per attempt/);
  assert.equal(state.posts.length,0);
});

test('BENCHMARK-TASK-04: clear or mismatched choices cannot launch, and cancel/reopen resets the selection',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:benchmarkTaskOptions});
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.getByLabel('Benchmark',{exact:true}).count(),1);
  const form=page.locator('#ab-launch-form');
  await form.evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  assert.equal(state.posts.length,0);
  await selectLaunchTask(page,'robosuite','lift-hd');
  await form.evaluate(f=>{
    f.profile_id.add(new Option('Foreign task','sink'));f.profile_id.value='sink';
    f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));
  });
  assert.equal(state.posts.length,0);
  assert.match(await page.locator('#ab-launch-message').innerText(),/Choose a benchmark and task/);
  await page.getByLabel('Benchmark',{exact:true}).selectOption('');
  assert.equal(await page.getByLabel('Task',{exact:true}).isVisible(),false);
  assert.equal(await page.getByLabel('Task',{exact:true}).inputValue(),'');
  assert.equal(await form.locator('[name=execution_target]').inputValue(),'');
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(await page.locator('[data-open-launch]').evaluate(e=>e===document.activeElement),true);
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.getByLabel('Benchmark',{exact:true}).inputValue(),'');
  assert.equal(await page.getByLabel('Task',{exact:true}).isVisible(),false);
  assert.equal(state.posts.length,0);
});

test('BENCHMARK-TASK-05: changing choices during a pending launch never reenables or duplicates submission',async t=>{
  let release;
  const pending=new Promise(resolve=>{release=resolve;});
  const {page,state}=await fixture(t,{beforeNavigate:async(page,state)=>{
    benchmarkTaskOptions(page,state);
    await page.route('**/api/runs/start',async route=>{
      state.posts.push({pathname:'/api/runs/start',payload:route.request().postDataJSON()});
      await pending;
      await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Isolated launch rejection'})});
    });
  }});
  try {
    await page.locator('[data-open-launch]').click();
    await selectLaunchTask(page,'robosuite','lift-hd');
    const form=page.locator('#ab-launch-form'),start=form.locator('button[type=submit]');
    const request=page.waitForRequest(r=>r.url().endsWith('/api/runs/start'));
    await start.click();await request;
    await selectLaunchTask(page,'robocasa','sink');
    await form.locator('[name=assistance_credits]').fill('2');
    assert.equal(await start.isEnabled(),false);
    await form.evaluate(f=>f.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert.equal(state.posts.length,1);
    assert.equal(state.posts[0].payload.profile_id,'lift-hd');
    release();
    await page.waitForFunction(()=>document.querySelector('#ab-launch-message')?.textContent.includes('Isolated launch rejection'));
    assert.equal(await start.isEnabled(),true);
    assert.equal(await page.getByLabel('Task',{exact:true}).inputValue(),'sink');
    await page.getByLabel('Benchmark',{exact:true}).selectOption('');
    assert.equal(await start.isEnabled(),false);
    assert.equal(state.posts.length,1);
  } finally {release();}
});

for(const width of [1280,390])test(`SELECT-01: expanded dark menus remain readable and retain native form/keyboard behavior at ${width}px`,async t=>{
  const snap=snapshot();snap.requests=[];
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{
    s.launchOptions={profiles:[{id:'lift',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101}],attention_policies:['autonomous','demo_first','reactive_help','retry_k_then_ask','budget_matched_random','trace_aware_hints_only','full_trace_aware_attention'],assistance_modes:['benchmark_proxy','live_human_first']};
  }});
  await page.locator('[data-open-launch]').click();
  const policy=page.locator('select[name=attention_policy]');
  const menus=await page.locator('#attentionbench-clear select').evaluateAll(es=>es.map(el=>({
    name:el.name||el.id,background:getComputedStyle(el).backgroundColor,
    options:[...el.options].map(o=>({background:getComputedStyle(o).backgroundColor,color:getComputedStyle(o).color}))
  })));
  for(const menu of menus){
    assert.notEqual(menu.background,'rgba(0, 0, 0, 0)',JSON.stringify(menu));
    assert.ok(menu.options.every(o=>o.background==='rgb(29, 29, 41)'&&o.color==='rgb(245, 244, 248)'),JSON.stringify(menu));
  }
  assert.equal(await policy.evaluate(el=>getComputedStyle(el).appearance),'base-select');
  await policy.click();
  assert.equal(await policy.evaluate(el=>el.matches(':open')),true);
  const items=page.getByRole('option',{name:'Retry k times, then ask',exact:true});
  assert.equal(await items.isVisible(),true);
  assert.equal(await items.evaluate(el=>getComputedStyle(el).color),'rgb(245, 244, 248)');
  const bounds=await items.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width,JSON.stringify(bounds));
  assert.equal(await policy.inputValue(),'reactive_help');
  await evidence(page,`SELECT-expanded-${width}`);
  await items.click();
  assert.equal(await policy.inputValue(),'retry_k_then_ask');
  await policy.click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('.ab-launch-dialog').isVisible(),true);
  assert.equal(await policy.inputValue(),'retry_k_then_ask');
  assert.equal(await policy.evaluate(el=>document.activeElement===el),true);
  await policy.click();await page.keyboard.press('Home');await page.keyboard.press('Enter');
  assert.equal(await policy.inputValue(),'autonomous');
  assert.match(await page.locator('[data-launch-preview]').innerText(),/does not request help/);
  await policy.selectOption('reactive_help');
  assert.match(await page.locator('[data-launch-preview]').innerText(),/each help request waits/);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`SUBPAGE-01: illuminated configuration and numbered panels retain editable values and scroll at ${width}px`,async t=>{
  const snap=snapshot();snap.requests=[];
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{
    s.launchOptions={profiles:[{id:'lift',suite:'robosuite',task:'cube_lift',execution_target:'robosuite_sim',seed:101,camera_width:1920,camera_height:1080}],attention_policies:['autonomous','reactive_help'],assistance_modes:['benchmark_proxy','live_human_first']};
  }});
  await page.locator('[data-open-launch]').click();
  const dialog=page.locator('.ab-launch-dialog');
  const style=await dialog.evaluate(el=>({
    titleSize:parseFloat(getComputedStyle(el.querySelector('h3')).fontSize),
    glow:getComputedStyle(el.querySelector('h3')).textShadow,
    headerTexture:getComputedStyle(el.querySelector('header')).backgroundImage,
    sections:[...el.querySelectorAll('.ab-launch-section')].map(n=>({surface:getComputedStyle(n).backgroundImage,border:getComputedStyle(n).borderLeftWidth})),
    fits:el.getBoundingClientRect().left>=0&&el.getBoundingClientRect().right<=innerWidth,
    bodyFits:el.querySelector('.ab-launch-body').scrollWidth<=el.querySelector('.ab-launch-body').clientWidth
  }));
  assert.ok(style.titleSize>=(width===1280?34:26),JSON.stringify(style));
  assert.notEqual(style.glow,'none');assert.match(style.headerTexture,/gradient/);
  assert.ok(style.sections.every(s=>s.surface!=='none'&&parseFloat(s.border)>0),JSON.stringify(style));
  assert.ok(style.fits&&style.bodyFits,JSON.stringify(style));
  await selectLaunchTask(page);
  const optionFit=await page.locator('#ab-launch-form select[name=profile_id]').evaluate(el=>{
    const s=getComputedStyle(el),c=document.createElement('canvas').getContext('2d');c.font=s.font;
    return{text:el.selectedOptions[0].textContent,measured:c.measureText(el.selectedOptions[0].textContent).width,available:el.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight)-18};
  });
  assert.equal(optionFit.text,'Lift the cube');assert.ok(optionFit.measured<=optionFit.available-(width===390?15:0),JSON.stringify(optionFit));
  await page.getByLabel('Development seed',{exact:true}).fill('105');
  assert.equal(await page.getByLabel('Development seed',{exact:true}).inputValue(),'105');
  assert.notEqual(await page.getByLabel('Development seed',{exact:true}).evaluate(e=>getComputedStyle(e).boxShadow),'none');
  await evidence(page,`SUBPAGE-configuration-${width}`);
  await page.locator('#ab-launch-form input[name=overall_deadline_seconds]').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('#ab-launch-form input[name=overall_deadline_seconds]').inputValue(),'300');
  await evidence(page,`SUBPAGE-time-limits-${width}`);
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(),false);
  assert.equal(await page.locator('[data-open-launch]').evaluate(e=>e===document.activeElement),true);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`SUBPAGE-02: developer, Skill flow, feedback, result and session dialogs share readable gold surfaces at ${width}px`,async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  snap.eval={status:'completed',native_success:false,diagnosis:'The cube was not lifted.',stopped_reason:'max_attempts'};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:async page=>{
    await page.route('**/api/orchestrator/snapshot',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({graph:'test-graph',run_id:'old',entries:[],agents:[],live_sessions:[]})}));
    await page.route('**/api/orchestrator/sessions?*',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({sessions:[{run_id:'old',session_id:'session-test',agent_id:'agent-test',skill:'Session example',agent_type:'dev',timestamp:1791300000,log:[{role:'agent',text:'Raw session text unchanged.'}]}]})}));
  }});
  async function checkDialog(selector,name){
    const values=await page.locator(selector).evaluate(el=>{
      const h=el.querySelector('h3');return{titleSize:parseFloat(getComputedStyle(h).fontSize),glow:getComputedStyle(h).textShadow,surface:getComputedStyle(el).backgroundImage,bounds:{left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right,width:innerWidth}};
    });
    assert.ok(values.titleSize>=(width===1280?28:24),JSON.stringify(values));
    assert.notEqual(values.glow,'none');assert.notEqual(values.surface,'none');
    assert.ok(values.bounds.left>=0&&values.bounds.right<=values.bounds.width,JSON.stringify(values));
    await evidence(page,`SUBPAGE-${name}-${width}`);
  }
  await openMore(page);await page.locator('[data-secondary=debug]>summary').click();
  await checkDialog('[data-more-dialog]','developer');
  await page.locator('[data-session-index]').click();
  await checkDialog('dialog:has([data-close-session])','session');
  assert.equal(await page.locator('.ab-conversation pre').innerText(),'Raw session text unchanged.');
  await page.locator('[data-close-session]').click();await page.locator('[data-close-more]').click();
  await showNode(page,'attempt');await checkDialog('[data-attempt-dag-dialog]','skill-flow');
  await page.locator('[data-close-attempt-dag]').click();
  await showNode(page,'response');await checkDialog('[data-flow-detail-dialog]','feedback');
  assert.equal(await page.locator('[data-feedback-layer]').count(),4);
  await tick();assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),true);
  await page.locator('[data-close-flow-detail]').click();
  await showNode(page,'result');await checkDialog('[data-flow-detail-dialog]','results');
  assert.match(await page.locator('[data-flow-detail-dialog]').innerText(),/Native Task Outcome|Task unsuccessful/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`TYPE-01: large illuminated title and rounded gold-led typography fit ${width}px`,async t=>{
  const snap=snapshot();snap.requests=[];
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  const style=await page.locator('.brand').evaluate(el=>({
    titleSize:parseFloat(getComputedStyle(el.querySelector('b')).fontSize),
    titleFont:getComputedStyle(el.querySelector('b')).fontFamily,
    bodyFont:getComputedStyle(document.querySelector('#attentionbench-clear')).fontFamily,
    shadow:getComputedStyle(el.querySelector('b')).textShadow,
    outline:getComputedStyle(el.querySelector('b')).webkitTextStrokeWidth,
    sweep:getComputedStyle(el,'::before').animationIterationCount,
    titleFits:el.querySelector('b').scrollWidth<=el.querySelector('b').clientWidth,
    plateFits:el.scrollWidth<=el.clientWidth,
    pageFits:document.documentElement.scrollWidth<=innerWidth,
    surface:getComputedStyle(document.querySelector('#attentionbench-clear')).getPropertyValue('--panel').trim(),
    badgeFits:document.querySelector('.demo').getBoundingClientRect().right<=innerWidth
  }));
  assert.ok(style.titleSize>=(width===1280?56:38),JSON.stringify(style));
  assert.match(style.titleFont,/Ubuntu/);assert.doesNotMatch(style.titleFont,/Condensed|Narrow/);
  assert.match(style.bodyFont,/Ubuntu/);
  assert.notEqual(style.shadow,'none');assert.ok(parseFloat(style.outline)>0);
  assert.equal(style.sweep,'1');assert.equal(style.surface,'#1d1d29');
  assert.ok(style.titleFits&&style.plateFits&&style.pageFits&&style.badgeFits,JSON.stringify(style));
  await evidence(page,`TYPE-main-${width}`);
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.brand').evaluate(el=>getComputedStyle(el,'::before').animationName),'none');
  await page.locator('[data-open-launch]').click();
  assert.equal(await page.locator('.ab-launch-dialog .ab-button-primary').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 140, 0)');
  assert.match(await page.locator('.ab-launch-field').first().evaluate(el=>getComputedStyle(el).fontFamily),/Ubuntu/);
  await evidence(page,`TYPE-new-run-${width}`);
  assert.equal(state.posts.length,0);
});

for (const width of [1280,390]) test(`EVA-01: mecha title and readable gold configuration fit ${width}px`, async t => {
  const snap=snapshot(); snap.requests=[];
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  assert.equal(await page.locator('.brand').getAttribute('aria-label'),'Tidybot Attention Bench');
  assert.equal((await page.locator('.brand').innerText()).replace(/\s+/g,' ').trim(),'TIDYBOT ATTENTION BENCH');
  assert.equal(await page.locator('.brand').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
  await evidence(page,`EVA-main-${width}`);
  await page.locator('[data-open-launch]').click();
  const typography=await page.locator('.ab-launch-dialog').evaluate(dialog=>({
    title:parseFloat(getComputedStyle(dialog.querySelector('h3')).fontSize),
    labels:[...dialog.querySelectorAll('.ab-launch-field')].map(el=>parseFloat(getComputedStyle(el).fontSize)),
    numbers:[...dialog.querySelectorAll('h4 span')].map(el=>getComputedStyle(el).color),
    primary:getComputedStyle(dialog.querySelector('.ab-button-primary')).backgroundColor,
    fields:[...dialog.querySelectorAll('input,select')].every(el=>el.scrollWidth<=el.clientWidth),
    dialogFits:dialog.getBoundingClientRect().left>=0 && dialog.getBoundingClientRect().right<=innerWidth,
    pageFits:document.documentElement.scrollWidth<=innerWidth
  }));
  assert.ok(typography.title>=26,JSON.stringify(typography));
  assert.ok(typography.labels.every(size=>size>=13),JSON.stringify(typography));
  assert.deepEqual(typography.numbers,['rgb(255, 140, 0)','rgb(255, 140, 0)','rgb(255, 140, 0)']);
  assert.equal(typography.primary,'rgb(255, 140, 0)');
  assert.ok(typography.fields && typography.dialogFits && typography.pageFits,JSON.stringify(typography));
  await evidence(page,`EVA-new-run-${width}`);
  assert.equal(state.posts.length,0);
});

test('EVA-02: a new request pulses once, preserves draft/selection and permits typing through the alert',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true});
  await page.waitForTimeout(2600);
  const input=page.locator('#ab-hint-content');
  await input.fill('Keep my draft unchanged');
  await input.evaluate(el=>{el.focus();el.setSelectionRange(5,12);});
  state.snapshots.old.requests.push({...state.snapshots.old.requests[0],request_id:'new-request',created_at:Date.now()/1000});
  await tick();
  const alert=page.locator('#ab-request-alert');
  assert.equal(await alert.isVisible(),true,await page.evaluate(()=>JSON.stringify({storage:{...sessionStorage},hidden:document.querySelector('#ab-request-alert').hidden,connection:document.querySelector('#attentionbench-clear').dataset.connection,active:document.activeElement.id,message:document.querySelector('#ab-live-message').textContent})));
  assert.equal(await alert.getAttribute('role'),'status');
  assert.match(await alert.innerText(),/HUMAN INPUT REQUIRED/);
  assert.deepEqual(await input.evaluate(el=>[el.value,el.selectionStart,el.selectionEnd,document.activeElement===el]),['Keep my draft unchanged',5,12,true]);
  const effect=await alert.evaluate(el=>({
    rect:[el.getBoundingClientRect().x,el.getBoundingClientRect().y,el.getBoundingClientRect().width,el.getBoundingClientRect().height],
    viewport:[innerWidth,innerHeight],
    clicks:[el,...el.querySelectorAll('*')].every(node=>getComputedStyle(node).pointerEvents==='none'),
    pulses:getComputedStyle(el.querySelector('.ab-alert-pulse')).animationIterationCount,
    duration:getComputedStyle(el.querySelector('.ab-alert-pulse')).animationDuration
  }));
  assert.deepEqual(effect.rect,[0,0,...effect.viewport]);
  assert.equal(effect.clicks,true);assert.equal(effect.pulses,'3');assert.equal(effect.duration,'0.8s');
  await tick();assert.equal(await alert.isVisible(),true);
  await input.press('x');
  assert.equal(await alert.isVisible(),false);
  assert.equal(await input.inputValue(),'Keep xt unchanged');
  await tick();assert.equal(await alert.isVisible(),false);
  await page.reload();await page.locator('#ab-live-run').waitFor({state:'attached'});
  assert.equal(await alert.isVisible(),false);
  await page.locator('[data-live-request="old-r"]').click();
  assert.equal(await input.inputValue(),'Keep xt unchanged');
  assert.equal(state.posts.length,0);
});

test('EVA-03: alert ends automatically while the actionable request and countdown remain',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true});
  assert.equal(await page.locator('#ab-request-alert').isVisible(),true);
  await page.waitForTimeout(2600);
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  assert.equal(await page.locator('.ab-current-request').getAttribute('data-state'),'active');
  assert.match(await page.locator('[data-reply-countdown]').innerText(),/Reply time remaining/);
  await tick();assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  state.snapshots.other=snapshot('other');state.runs.push({run_id:'other',task_id:'cube_lift'});
  await tick();await switchViewingRun(page,'other');
  await page.locator('[data-answer-current="other-r"]').waitFor();
  assert.equal(await page.locator('#ab-request-alert').isVisible(),true);
  await switchViewingRun(page,'old');
  await page.locator('[data-answer-current="old-r"]').waitFor();
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  assert.equal(state.posts.length,0);
});

for(const kind of ['expired','answered','proxy','completed','failed','cancelled','stop_requested','stop_confirmed'])
test(`EVA-04: ${kind} requests never cause an actionable full-screen alert`,async t=>{
  const snap=snapshot();
  if(kind==='expired')snap.requests[0].deadline_at=Date.now()/1000-1;
  else if(kind==='answered')snap.requests[0].state='answered';
  else if(kind==='proxy')snap.requests[0].mode='benchmark_proxy';
  else if(kind.startsWith('stop_'))snap.station.interrupt={state:kind==='stop_requested'?'requested':'stopped'};
  else snap.run.status=kind;
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true});
  assert.equal(await page.locator('#ab-request-alert').count(),1);
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  assert.equal(state.posts.length,0);
});

test('EVA-05: disconnect and expiry dismiss alerts; reconnect does not repeat a seen request',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true});
  assert.equal(await page.locator('#ab-request-alert').isVisible(),true);
  state.offline=true;await tick();
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  state.offline=false;await tick();
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  state.snapshots.old.requests[0].request_id='next-request';
  state.snapshots.old.requests[0].deadline_at=Date.now()/1000+1;await tick();
  assert.equal(await page.locator('#ab-request-alert').isVisible(),true);
  await page.waitForTimeout(1500);
  await page.evaluate(()=>window.__cameraTick());
  assert.equal(await page.locator('#ab-request-alert').isVisible(),false);
  assert.equal(state.posts.length,0);
});

test('EVA-06: reduced motion gets a static notification; a modal keeps focus and stays usable',async t=>{
  const snap=snapshot();snap.requests=[];
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,beforeNavigate:page=>page.emulateMedia({reducedMotion:'reduce'})});
  await page.locator('[data-open-launch]').click();
  await page.locator('#ab-launch-form input[name=seed]').fill('102');
  const focus=await page.evaluate(()=>document.activeElement.name);
  state.snapshots.old.requests=[snapshot().requests[0]];await tick();
  const alert=page.locator('#ab-request-alert');
  assert.equal(await alert.isVisible(),true);
  assert.equal(await alert.evaluate(el=>el.matches(':popover-open')),true);
  assert.equal(await page.evaluate(()=>document.activeElement.name),focus);
  assert.equal(await alert.locator('.ab-alert-pulse').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.locator('.ab-launch-header [data-close-launch]').click();
  assert.equal(await page.locator('.ab-launch-dialog').evaluate(el=>el.open),false);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`EVA-07: all three alert pulses cover the viewport without clipping at ${width}px`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:844}});
  const alert=page.locator('#ab-request-alert');
  assert.equal(await alert.isVisible(),true);
  const geometry=await alert.evaluate(el=>({
    screen:[el.getBoundingClientRect().width,el.getBoundingClientRect().height],
    viewport:[innerWidth,innerHeight],
    messageFits:el.querySelector('.ab-alert-message').scrollWidth<=el.querySelector('.ab-alert-message').clientWidth,
    titleFits:el.querySelector('strong').scrollWidth<=el.querySelector('strong').clientWidth,
    headingNames:[...document.querySelectorAll('.panel-title h3')].map(el=>getComputedStyle(el,'::before').content)
  }));
  assert.deepEqual(geometry.screen,geometry.viewport);
  assert.ok(geometry.messageFits&&geometry.titleFits,JSON.stringify(geometry));
  // Capture each animation peak in the isolated fixture; the live service is
  // never injected with fake requests or paused animations.
  for(const [index,time] of [350,1150,1950].entries()) {
    await alert.evaluate((el,time)=>el.getAnimations({subtree:true}).forEach(animation=>{animation.pause();animation.currentTime=time;}),time);
    assert.ok(await alert.locator('.ab-alert-pulse').evaluate(el=>Number(getComputedStyle(el).opacity)>.9));
    if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`EVA-alert-${width}-${index+1}.png`)});
  }
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`EN-08: English example hint fills an English draft and keeps advanced parameters folded (${width}px)`,async t=>{
  const {page,state,tick}=await fixture(t,{fullDocument:true,styled:true,viewport:{width,height:900}});
  const format=page.locator('[data-hint-format]');
  const preset=format.locator('[data-hint-preset=en]');
  assert.equal(await preset.innerText(),'Insert example hint');
  assert.equal(await preset.getAttribute('data-preset-text'),englishCubeHint);
  assert.equal(await format.locator('[data-hint-advanced]>summary').innerText(),'Advanced parameters');
  assert.equal(await format.locator('[data-hint-advanced]').getAttribute('open'),null);
  assert.doesNotMatch(await format.innerText(),/Chinese preset|[\u3400-\u9fff]/);
  await preset.click();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),englishCubeHint);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'ab-hint-content');
  assert.equal(state.posts.length,0);
  await tick();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),englishCubeHint);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'ab-hint-content');
  await format.locator('[data-hint-advanced]>summary').click();
  assert.match(await format.innerText(),/This example hint is supported for Lift the cube/);
  assert.ok(await format.evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await evidence(page,`EN-preset-${width}`);
  await page.locator('[data-respond=hint]').click();
  assert.equal(state.posts.length,1);
  assert.equal(state.posts[0].payload.content,englishCubeHint,'Submit the exact English hint the user sees');
});

// English product copy is audited in text, accessibility names, placeholders,
// tooltips and CSS content, including folded panels. Raw content is tested
// separately; user-provided Chinese hints remain unchanged.
async function assertEnglishProduct(page) {
  const leaks=await page.locator('#attentionbench-clear').evaluate(root=>{
    const chinese=/[\u3400-\u9fff]/;
    const leaks=[];
    const walk=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    while(walk.nextNode()) {
      const node=walk.currentNode;
      if(!node.parentElement.closest('script,style')&&chinese.test(node.textContent))leaks.push(node.textContent.trim());
    }
    for(const e of root.querySelectorAll('*')) {
      for(const name of ['aria-label','alt','placeholder','title'])if(chinese.test(e.getAttribute(name)||''))leaks.push(name+': '+e.getAttribute(name));
      for(const pseudo of ['::before','::after'])if(chinese.test(getComputedStyle(e,pseudo).content))leaks.push(pseudo+': '+getComputedStyle(e,pseudo).content);
    }
    return leaks;
  });
  assert.deepEqual(leaks,[],'Product-owned copy must be English (raw content is tested in separate fixtures).');
}
for(const width of [1280,390])test(`EN-01: English workspace, accessibility, dialogs and bound descriptions fit ${width}`,async t=>{
  const snap=snapshot();snap.requests[0].reason='request_after_each_failure';
  snap.dev_evidence_gaps=[];snap.task_plan={max_attempts:4,definition:{description:'执行已批准的完整 Public SDK 策略。内部函数和 SDK 动作没有独立 Skill DAG 调度定义或子技能事件；本图仅记录整体任务节点。'}};
  snap.attempt_dags={'old-a0':dagFor('old-a0')};
  snap.attempt_dags['old-a0'].dag.nodes.find(n=>n.node_id==='grasp').description=snap.task_plan.definition.description;
  const original=JSON.stringify(snap);
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,fullDocument:true,styled:true,viewport:{width,height:900}});
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.locator('[data-run-settings] h3').innerText(),'Task & Run Settings');
  assert.equal(await page.locator('[data-core-region=status]').getAttribute('aria-label'),'Task Status & Progress');
  assert.equal(await page.locator('[data-core-region=execution] h3').innerText(),'Execution & History');
  assert.equal(await page.locator('[data-core-region=request] h3').innerText(),'Current Request & Reply');
  assert.equal(await page.locator('[data-resource-details] h3').innerText(),'Resources');
  await page.locator('[data-hint-advanced]>summary').click();await assertEnglishProduct(page);
  assert.equal(await page.locator('[data-setting=Task] dd').innerText(),'Lift the cube');
  assert.equal(await page.locator('[data-task-description]').count(),0);
  for(const legend of await page.locator('[data-resource-details] .budget-legend>span').all()) {
    assert.ok(await legend.evaluate(e=>e.scrollWidth<=e.clientWidth+1),'resource legend must fit its card');
  }
  assert.equal(await page.locator('[data-hint-preset=en]').getAttribute('data-preset-text'),englishCubeHint);
  await evidence(page,`EN-workspace-${width}`);
  await page.locator('[data-open-launch]').click();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-launch-preview]').innerText(),/Up to 10 attempts/);
  assert.ok(await page.locator('.ab-launch-shell').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  await evidence(page,`EN-launch-${width}`);await page.locator('[data-close-launch]').first().click();
  await showNode(page,'attempt');await page.locator('[data-skill-node=detect]').click();
  await page.locator('[data-skill-detail] details>summary').click();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-skill-detail]').innerText(),/Locate.*Development: Complete.*Execution: Executed/s);
  assert.ok(await page.locator('[data-attempt-dag-dialog]').evaluate(e=>e.scrollWidth<=e.clientWidth+1));
  await evidence(page,`EN-dag-${width}`);
  await page.locator('[data-close-attempt-dag]').click();await openMore(page);
  await page.locator('[data-secondary=debug]>summary').click();await assertEnglishProduct(page);
  await evidence(page,`EN-more-${width}`);await page.locator('[data-close-more]').click();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(JSON.stringify(snap),original,'display translation must not mutate the snapshot or DAG');
  await floatOverview(page);
  for(const task of ['cube_lift','cube_stack','counter_to_sink','counter_to_cab']) {
    snap.run.task_id=task;state.runs[0].task_id=task;await tick();
    assert.ok(await page.locator('[data-overview-open-sidebar]').evaluate(button=>{
      const context=document.createElement('canvas').getContext('2d');context.font=getComputedStyle(button).font;
      return context.measureText(button.textContent).width<=button.clientWidth;
    }),'English task and run label must fit the compact floating selector');
  }
  assert.equal(state.posts.length,0);
});
test('EN-02: all phases use English and distinguish submission, stopping and native outcome',async t=>{
  const snap=snapshot();snap.requests=[];snap.attempts=[];snap.run.status='created';
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,fullDocument:true,styled:true});
  await assertEnglishProduct(page);
  snap.run.status='running';snap.attempts=[{attempt_id:'old-a0',index:0,status:'running'}];await tick();await assertEnglishProduct(page);
  snap.requests=snapshot().requests;snap.attempts[0].status='failed';await tick();await assertEnglishProduct(page);
  snap.requests[0].request_type='approval';snap.requests[0].reason='irreversible_action_requires_approval';await tick();await assertEnglishProduct(page);
  assert.equal(await page.locator('[data-respond=approve]').innerText(),'Approve');
  snap.requests[0].request_type='hint';await tick();await page.locator('#ab-hint-content').fill('Keep the original reply.');
  await page.locator('[data-respond=hint]').click();await page.locator('[data-open-flow-node]').click();await assertEnglishProduct(page);
  assert.deepEqual(await page.locator('[data-feedback-layer] h4').allTextContents(),['Submission','Execution Association','Action Changes','Native Task Outcome']);
  assert.match(await page.locator('[data-feedback-layer=submission]').innerText(),/Submitted/);
  assert.doesNotMatch(await page.locator('[data-feedback-layer=submission]').innerText(),/Adopted/i);
  await page.locator('[data-close-flow-detail]').click();
  snap.requests[0].state='pending';snap.requests[0].deadline_at=Date.now()/1000-1;await tick();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-reply-countdown]').innerText(),/Reply timed out/);
  state.offline=true;await tick();await assertEnglishProduct(page);
  assert.match(await page.locator('#ab-stale-warning').innerText(),/Disconnected/);
  state.offline=false;snap.station.interrupt={state:'requested'};await tick();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-stop-state]').innerText(),/^Stop requested/);
  assert.doesNotMatch(await page.locator('[data-stop-state]').innerText(),/Stop confirmed/);
  snap.station.interrupt.state='stopped';await tick();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-stop-state]').innerText(),/^Stop confirmed/);
  snap.run.status='completed';snap.eval={status:'completed',native_success:false,stopped_reason:'max_attempts'};await tick();await showNode(page,'end');await assertEnglishProduct(page);
  assert.match(await page.locator('[data-eval-panel]').innerText(),/Diagnostic complete.*Task unsuccessful/s);
  assert.equal(state.posts.length,1);
});
test('EN-03: empty and registering workspaces use English without removed panels',async t=>{
  const {page,state}=await fixture(t,{fullDocument:true,styled:true,beforeNavigate:(_,s)=>{s.runs=[];}});
  await assertEnglishProduct(page);assert.match(await page.locator('[data-core-region=status]').innerText(),/No Attention runs/);
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page);await page.locator('#ab-launch-form').evaluate(f=>f.requestSubmit());
  await page.waitForFunction(()=>document.querySelector('[data-core-region=status]')?.dataset.phase==='launching');
  await assertEnglishProduct(page);assert.match(await page.locator('[data-core-region=status]').innerText(),/Waiting for run registration/);
  assert.equal(await page.locator('[data-launch-parameters],[data-launch-history],[data-statistics-page],[data-secondary=statistics],[data-secondary=raw]').count(),0);
  assert.equal(state.posts.length,1);
});
test('EN-04: source audit allows only display-map keys and raw-language recognizers',()=>{
  let copy=source.replace(/const displayText = \{[\s\S]*?\n  \};/,'')
    .replace(/const names=\{find_objects:.*?\};/,'')
    .replace(/const hasHypothesis = .*?;/,'');
  assert.doesNotMatch(copy,/[\u3400-\u9fff]/,'Every remaining Chinese literal must have a reviewed reason to exist.');
  assert.doesNotMatch(fullHtml,/[\u3400-\u9fff]/);
  assert.equal((source.match(/降低抓取高度，夹紧后确认抓牢/g)||[]).length,0);
});
test('EN-05: raw Hint, model diagnosis, logs, custom descriptions and legacy Chinese hint payload stay unchanged',async t=>{
  const snap=flowSnapshot({linked:false});
  const hint='用户原文：请缓慢抬起。';snap.responses['reply-1'].content=hint;
  snap.attempt_dags={'old-a0':dagFor('old-a0')};
  const custom='自定义技能说明：保持原样 <img src=x onerror=alert(1)>';
  snap.attempt_dags['old-a0'].dag.nodes[2].description=custom;
  snap.eval={status:'completed',native_success:false,diagnosis:'**Earliest observed problem:** 模型原文：没有抓牢。\n\n**Caveat:** 只记录观察。',stopped_reason:'max_attempts'};
  const original=JSON.stringify(snap);
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,fullDocument:true});
  assert.equal(await page.locator('[data-hint-preview]').innerText(),hint);
  await showNode(page,'attempt');assert.match(await page.locator('[data-skill-detail]').innerText(),/自定义技能说明：保持原样/);
  assert.equal(await page.locator('[data-skill-detail] img').count(),0);
  await page.locator('[data-close-attempt-dag]').click();await showNode(page,'result');
  await page.locator('[data-eval-details]>summary').click();
  assert.match(await page.locator('[data-eval-diagnosis]').innerText(),/Earliest observed problem.*模型原文：没有抓牢/s);
  assert.equal(await page.locator('.ab-eval-source pre').innerText(),snap.eval.diagnosis);
  assert.equal(JSON.stringify(snap),original);
  await page.locator('[data-close-flow-detail]').click();
  snap.requests[0].state='pending';delete snap.requests[0].response_id;delete snap.responses['reply-1'];await tick();
  await page.locator('#ab-hint-content').fill('降低抓取高度，夹紧后确认抓牢');await page.locator('[data-respond=hint]').click();
  assert.equal(state.posts.at(-1).payload.content,'降低抓取高度，夹紧后确认抓牢');
  assert.equal(state.posts.length,1);
});
test('EN-06: known task names, DAG gaps, budget records, system replies and final errors have English copy',async t=>{
  const snap=flowSnapshot({proxy:true,linked:false});snap.responses['reply-1'].content='System guidance from the fixture.';
  snap.budget_events=[{sequence:6,event_type:'budget.released',payload:{credits:1,reason:'run_terminal',reservation_id:'immutable-reservation'}}];
  snap.attempt_dags={'old-a0':{status:'unavailable',reason:'Skill DAG 绑定或校验失败'}};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,fullDocument:true,styled:true});
  await page.locator('[data-request-history]>summary').click();await assertEnglishProduct(page);
  await page.locator('[data-budget-events]>summary').click();await assertEnglishProduct(page);
  await showNode(page,'attempt');await assertEnglishProduct(page);
  assert.match(await page.locator('[data-dag-gap]').innerText(),/Skill DAG binding or verification failed/);
  await page.locator('[data-close-attempt-dag]').click();
  for(const [task,name] of [['cube_lift','Lift the cube'],['cube_stack','Stack the cubes'],['counter_to_sink','Move to the sink'],['counter_to_cab','Move into the cabinet']]) {
    snap.run.task_id=task;await tick();assert.equal(await page.locator('[data-run-overview] h2').innerText(),name);await assertEnglishProduct(page);
  }
  snap.run.status='failed';snap.eval={status:'evidence_invalid',native_success:null,stopped_reason:'formal_attempt_incomplete_or_evaluator_unavailable',error:'Unverified artifact'};await tick();await showNode(page,'end');
  await page.locator('[data-eval-details]>summary').click();await assertEnglishProduct(page);
  assert.match(await page.locator('[data-eval-panel]').innerText(),/Evidence verification failed.*Outcome awaiting verification/s);
  assert.equal(state.posts.length,0);
});
test('EN-07: developer graph descriptions use display mappings; session logs retain their original text',async t=>{
  const graph=JSON.parse(fs.readFileSync(path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/robocasa_counter_to_sink.json'),'utf8'));
  const logs=[{role:'user',text:'真人日志：请保持原样。'},{role:'agent',text:'模型日志：参数和 ID 不变。'}];
  const session={session_id:'original-session',agent_id:'original-agent',skill:graph.entries[0].name,agent_type:'dev',run_id:'old',in_progress:true,log:logs};
  const {page,state}=await fixture(t,{fullDocument:true,styled:true,beforeNavigate:async page=>{
    await page.route('**/api/orchestrator/snapshot',route=>route.fulfill({json:{graph:graph.graph_id,entries:graph.entries,run_id:'old',agents:[],live_sessions:[session]}}));
    await page.route('**/api/orchestrator/sessions?**',route=>route.fulfill({json:{sessions:[session]}}));
  }});
  await openMore(page);await page.locator('[data-secondary=debug]>summary').click();await assertEnglishProduct(page);
  assert.match(await page.locator('.ab-dag-detail').innerText(),/Execute the full approved Public SDK policy/);
  await page.locator('[data-session-index]').click();
  assert.deepEqual(await page.locator('.ab-conversation pre').allTextContents(),logs.map(x=>x.text));
  assert.equal(await page.getByRole('button',{name:'Close session'}).count(),1);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`STYLE-01: agent models are visible by default without Run ID or clipped columns at ${width}`,async t=>{
  const snap=snapshot();
  snap.run.run_id='run:attention-robosuite-cube_lift-seed101-with-a-long-immutable-run-identifier';
  snap.run.developer_model='formal-generated-policy-with-a-long-reviewed-version-identifier';
  snap.run.evaluator_model='native_evaluator-with-a-long-verified-receipt-identifier';
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,state)=>{
    state.runs=[{run_id:snap.run.run_id,task_id:snap.run.task_id}];
    state.snapshots={[snap.run.run_id]:snap};
  }});
  assert.equal(await page.locator('[data-settings-technical]').count(),0);
  assert.doesNotMatch(await page.locator('[data-run-settings]').innerText(),/Run ID|run:attention-/);
  const modelLabels=page.locator('[data-run-settings] .model-tag');
  assert.equal(await modelLabels.count(),2);
  for(const model of await modelLabels.all()) {
    assert.equal(await model.isVisible(),true,'both model names must be visible without expanding settings');
    assert.equal(await model.evaluate(e=>e.scrollWidth<=e.clientWidth+1),true,'model text must wrap rather than hide behind an ellipsis');
    assert.ok((await model.boundingBox()).width>100,'model should have a readable column');
  }
  assert.equal(await page.locator('[data-developer-model]').innerText(),snap.run.developer_model);
  assert.equal(await page.locator('[data-evaluator-model]').innerText(),snap.run.evaluator_model);
  await page.locator('#ab-hint-content').focus();
  snap.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('[data-settings-agents]').isVisible(),true);
  assert.equal(await page.locator('#ab-hint-content').evaluate(e=>e===document.activeElement),true);
  snap.run.developer_model='formal-generated-policy';snap.run.evaluator_model='native_evaluator';
  snap.eval={status:'failed',model:'parcc/GLM',native_success:true};await tick();
  assert.equal(await page.locator('[data-developer-model]').count(),0);
  assert.match(await page.locator('[data-agent-role=developer]').innerText(),/Using an approved policy/);
  assert.doesNotMatch(await page.locator('[data-agent-role=developer]').innerText(),/Attempt|Not recorded/);
  assert.equal(await page.locator('[data-evaluator-model]').innerText(),'parcc/GLM');
  assert.doesNotMatch(await page.locator('[data-run-settings]').innerText(),/Execution type|formal-generated-policy|native_evaluator/);
  assert.match(await page.locator('[data-agent-role=evaluator]').innerText(),/Diagnostic failed/);
  assert.doesNotMatch(await page.locator('[data-agent-role=evaluator]').innerText(),/Task unsuccessful/);
  await openMore(page);await page.locator('[data-secondary=debug]>summary').click();
  assert.match(await page.locator('[data-agent-debug]').innerText(),/formal-generated-policy.*native_evaluator.*parcc\/GLM/s);
  snap.run.developer_model='scripted';snap.eval={status:'pending'};await tick();
  assert.match(await page.locator('[data-agent-debug]').innerText(),/scripted/);
  await page.locator('[data-close-more]').click();
  assert.match(await page.locator('[data-agent-role=developer]').innerText(),/Using a scripted policy/);
  assert.match(await page.locator('[data-agent-role=evaluator]').innerText(),/Awaiting diagnosis/);
  assert.equal(await page.locator('[data-evaluator-model]').count(),0,'native evaluator identifier must not become a model name');
  snap.run.developer_model='formal-generated-policy';snap.eval={status:'failed',model:'parcc/GLM',native_success:true};await tick();
  assert.equal(await page.locator('[data-resource-details] .budget').count(),4);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);await evidence(page,`STYLE-expanded-${width}`);
});

for(const width of [1280,390])test(`PERSIST-01: settings stay above the task and resource cards below the reply at ${width}`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900}});
  assert.equal(await page.locator('[data-run-settings]').count(),1);
  assert.equal(await page.locator('[data-resource-details]').count(),1);
  assert.equal(await page.locator('[data-run-settings]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details] .budget').count(),4);
  assert.equal(await page.locator('[data-resource-details] .budget').first().isVisible(),true);
  assert.match(await page.locator('[data-run-settings]').innerText(),/Task & Run Settings.*Lift.*Benchmark.*Robosuite.*Assistance/s);
  assert.equal(await page.locator('[data-run-details]').getAttribute('open'),null);
  assert.doesNotMatch(await page.locator('[data-run-settings]').innerText(),/101/);
  await page.locator('[data-run-details]>summary').click();
  assert.match(await page.locator('[data-run-details]').innerText(),/Seed.*101/s);
  await page.locator('[data-run-details]>summary').click();
  assert.deepEqual(await page.locator('.view > [data-run-settings],.view > [data-core-region],.view > [data-resource-details]').evaluateAll(es=>es.map(e=>e.hasAttribute('data-run-settings')?'settings':e.hasAttribute('data-resource-details')?'resources':e.dataset.coreRegion)),['settings','status','execution','request','resources']);
  await openMore(page);
  assert.deepEqual(await page.locator('[data-more-dialog] [data-secondary]').evaluateAll(es=>es.map(e=>e.dataset.secondary)),['debug']);
  assert.doesNotMatch(await page.locator('[data-more-dialog]').innerText(),/Experiment statistics|Trial statistics|Raw records|Resources|Task & Run Settings/);
  await page.locator('[data-close-more]').click();
  assert.equal(await page.locator('[data-run-settings]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);await evidence(page,`PERSIST-layout-${width}`);
});
test('PERSIST-02: legacy statistics URLs render the normal workspace without removed panels',async t=>{
  const {page,state}=await fixture(t,{fullDocument:true});
  await page.goto('http://attention.test/ui/?run=old&view=statistics&debug=1');
  await page.locator('#ab-live-run').waitFor({state:'attached'});
  assert.equal(await page.locator('[data-run-settings]').count(),1);
  assert.equal(await page.locator('[data-resource-details]').count(),1);
  assert.equal(await page.locator('[data-statistics-page],[data-open-statistics],[data-secondary=statistics],[data-secondary=raw]').count(),0);
  assert.equal(await page.locator('[data-core-region=request] textarea').count(),1);
  assert.equal(state.posts.length,0);
});
test('PERSIST-03: persistent budget records keep focus on refresh and do not cross runs',async t=>{
  const snap=snapshot();snap.budget_events=[{sequence:6,event_type:'budget.released',payload:{credits:1,reason:'run_terminal',reservation_id:'old-reservation'}}];
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  assert.equal(await page.locator('[data-resource-details]').count(),1);
  await page.locator('[data-budget-events]>summary').click();
  snap.resources.tokens={limit:10,used:2,reserved:1,remaining:7};await tick();
  assert.notEqual(await page.locator('[data-budget-events]').getAttribute('open'),null);
  assert.equal(await page.locator('[data-budget-events]>summary').evaluate(e=>e===document.activeElement),true);
  assert.match(await page.locator('[data-resource-details]').innerText(),/old-reservation/);
  await page.locator('#ab-hint-content').fill('保留回复草稿');
  await page.locator('#ab-hint-content').evaluate(e=>{e.focus();e.setSelectionRange(1,4,'backward');});
  snap.resources.tokens.used=3;await tick();
  assert.deepEqual(await page.locator('#ab-hint-content').evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e.selectionDirection,e===document.activeElement]),['保留回复草稿',1,4,'backward',true]);
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new',task_id:'cube_lift'});await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.doesNotMatch(await page.locator('[data-resource-details]').innerText(),/old-reservation/);
  assert.equal(await page.locator('[data-run-settings]').getAttribute('data-settings-run'),'new');
  assert.equal(await page.locator('[data-resource-details]').getAttribute('data-resources-run'),'new');
  assert.equal(state.posts.length,0);
});
test('PERSIST-04: empty and registering runs keep explicit settings and resource placeholders',async t=>{
  const {page,state,tick}=await fixture(t,{beforeNavigate:async(_,s)=>{s.runs=[];}});
  assert.equal(await page.locator('[data-run-settings]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details] .budget').count(),0);
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page);await page.locator('#ab-launch-form').evaluate(form=>form.requestSubmit());
  await page.waitForFunction(()=>document.querySelector('[data-core-region=status]')?.dataset.phase==='launching');
  assert.match(await page.locator('[data-run-settings]').innerText(),/Launch|waiting/);
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details] .budget').count(),0);
  assert.equal(state.posts.length,1);
});
test('PERSIST-05: terminal settings and resource data remain visible and escaped',async t=>{
  const snap=snapshot('old','failed');snap.requests=[];snap.run.developer_model='<img src=x onerror=window.unescaped=true>';snap.resources.tokens={limit:100,used:25,reserved:0,remaining:75};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true});
  assert.equal(await page.locator('[data-run-settings]').count(),1);
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  assert.match(await page.locator('[data-settings-agents]').innerText(),/<img src=x/);
  assert.equal(await page.locator('[data-run-settings] img').count(),0);
  assert.equal(await page.evaluate(()=>window.unescaped),undefined);
  assert.match(await page.locator('[data-resource-details]').innerText(),/25.*100.*75/s);
});
for(const withRun of [true,false])test(`PERSIST-06: launch parameters and development history are absent with run=${withRun}`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,beforeNavigate:(_,s)=>{
    s.launches=[{launch_id:'launch-existing',run_id:'old',state:'completed',locked:{suite:'robosuite',task:'cube_lift',seed:101,internal:'launch-configuration-marker'}}];
    if(!withRun)s.runs=[];
  }});
  assert.doesNotMatch(await page.locator('.view').textContent(),/Full launch parameters|Launch records|formal_eligible|launch-configuration-marker/);
  assert.equal(await page.locator('[data-run-settings]').isVisible(),true);
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  if(withRun) {
    assert.match(await page.locator('[data-settings-agents]').innerText(),/Developer.*Evaluator/s);
    assert.doesNotMatch(await page.locator('[data-run-settings]').innerText(),/Run ID/);
    assert.equal(await page.locator('[data-resource-details] .budget').count(),4);
  } else assert.equal(await page.locator('[data-settings-agents]').count(),0);
  assert.equal(await page.locator('[data-settings-technical]').count(),0);
  assert.equal(await page.locator('[data-open-launch]').count(),1);
  assert.equal(state.launches.length,1);assert.equal(state.posts.length,0);
});
let browser, replayDirectory, replayBytes;
before(async () => {
  replayDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'attention-replay-test-'));
  const replayFile = path.join(replayDirectory, 'replay.mp4');
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'color=c=gray:s=64x36:r=25', '-t', '4', '-an', '-c:v', 'libx264',
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', replayFile]);
  replayBytes = fs.readFileSync(replayFile);
  browser = await chromium.launch({executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox']});
});
after(async () => { await browser?.close(); if (replayDirectory) fs.rmSync(replayDirectory, {recursive:true, force:true}); });
function snapshot(id = 'old', status = 'running') {
  const resource = {limit: 10, used: 0, reserved: 0, remaining: 10};
  return {run: {run_id: id, task_id: 'cube_lift', suite: 'robosuite', status, seed: 101, policy_id: 'reactive_help', assistance_mode: 'live_human_first', execution_target: 'robosuite_sim', locked: true},
    resources: {assistance: resource, tokens: resource, gpu_seconds: resource, execution_seconds: resource},
    attempts: [{attempt_id: id+'-a0', index: 0, started_at: 1791300000, status: 'failed'}],
    requests: [{request_id: id+'-r', trace_id: id+'-t0', attempt_id: id+'-a0', request_type: 'hint', state: 'pending', mode: 'live_human_first', created_at: 1791300001, deadline_at: Date.now()/1000+600}],
    traces: {[id+'-t0']: {trace_id: id+'-t0', attempt_id: id+'-a0', execution_id: id+'-e0', events: [{timestamp: 0, sequence: 0, timestamp_basis: 'unknown', operation: 'move_to_pose', status: 'completed'}]}},
    responses: {}, response_uses: {}, request_lifecycle: {}, station: {status: 'running', camera_url: '/camera?run='+id}, work_items: [], eval: {status: 'pending'}};
}

function flowSnapshot({linked=true, proxy=false}={}) {
  const snap=snapshot();
  snap.requests[0].state='answered';snap.requests[0].response_id='reply-1';
  snap.responses={'reply-1':{response_id:'reply-1',responder:proxy?'advisor_proxy':'human',content:'降低抓取高度',created_at:1791300002}};
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'running',started_at:1791300003});
  snap.response_evidence={'old-r':{submission:{status:'submitted',response_id:'reply-1'},association:linked?{status:'linked',attempt_id:'old-a1',execution_id:'old-e1'}:{status:'unconfirmed'},control_comparison:{status:'unconfirmed'},native_outcome:{status:'unconfirmed'}}};
  snap.task_plan={max_attempts:3,source:'scheduler_locked'};
  return snap;
}
function dagFor(attemptId, {revision='a', childStatus='executed'}={}) {
  return {status:'bound',run_id:'old',attempt_id:attemptId,captured_at:1791300000,dag:{graph_id:'bound-graph',revision:revision.repeat(64),root_skill:'grasp',nodes:[
    {node_id:'detect',name:'detect',label:'定位',description:'公共定位',development_status:'done',dependencies:[],execution_status:childStatus,goal_status:'unconfirmed',event_ids:['sdk-2'],trace_id:'old-t0'},
    {node_id:'open',name:'open',label:'张开',description:'夹爪张开',development_status:'done',dependencies:[],execution_status:'unknown',goal_status:'unconfirmed',event_ids:[]},
    {node_id:'grasp',name:'grasp',label:'抓取',description:'实际抓取任务',development_status:'done',dependencies:['detect','open'],execution_status:'running',goal_status:'unconfirmed',event_ids:[]}]}};
}
test('FLOW-01: separate attempts connect through persisted human reply; next attempt stays planned',async t=>{
  const {page,state}=await fixture(t,{initialSnapshot:flowSnapshot(),styled:true});
  assert.equal(await page.locator('[data-flow-kind=attempt]').count(),2);
  assert.match(await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').innerText(),/Attempt 1.*Unsuccessful/s);
  assert.match(await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').innerText(),/Attempt 2.*Running/s);
  assert.match(await page.locator('[data-flow-kind=response]').innerText(),/Your reply/);
  assert.equal(await page.locator('[data-flow-edge][data-from="response:old-r"][data-to="attempt:old-a1"]').getAttribute('data-state'),'confirmed');
  assert.match(await page.locator('[data-flow-kind=planned]').innerText(),/Attempt 3.*Possible/s);
  assert.equal(state.posts.length,0);await evidence(page,'FLOW-human-linked');
});
test('FLOW-02: proxy and unconfirmed association never become human reply or a confirmed link',async t=>{
  const {page}=await fixture(t,{initialSnapshot:flowSnapshot({linked:false,proxy:true})});
  assert.match(await page.locator('[data-flow-kind=response]').innerText(),/System reply/);
  assert.doesNotMatch(await page.locator('[data-task-flow]').innerText(),/Your reply/);
  const edge=page.locator('[data-flow-edge][data-from="response:old-r"][data-to="attempt:old-a1"]');
  assert.equal(await edge.getAttribute('data-state'),'unconfirmed');
  assert.match(await edge.textContent(),/Unconfirmed/);
});
test('FLOW-03: no-help attempts connect directly; terminal hides future plan and exposes verified result',async t=>{
  const snap=flowSnapshot();snap.requests=[];snap.responses={};snap.response_evidence={};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-flow-edge][data-from="attempt:old-a0"][data-to="attempt:old-a1"]').getAttribute('data-state'),'confirmed');
  snap.run.status='failed';snap.eval={status:'completed',native_success:false,stopped_reason:'max_attempts'};await tick();
  assert.equal(await page.locator('[data-flow-kind=planned]').count(),0);
  await page.locator('[data-flow-kind=end]').click();
  assert.match(await page.locator('[data-node-detail]').innerText(),/Task unsuccessful/);
  assert.equal(state.posts.length,0);
});
test('FLOW-04: attempt DAG keeps parallel dependencies and separates development from robot outcome',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true});
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-attempt-id'),'old-a0');
  assert.equal(await page.locator('[data-skill-edge][data-from=detect][data-to=grasp]').count(),1);
  assert.equal(await page.locator('[data-skill-edge][data-from=open][data-to=grasp]').count(),1);
  assert.equal(await page.locator('[data-skill-node=open]').getAttribute('data-runtime'),'unknown');
  assert.match(await page.locator('[data-skill-node=detect]').innerText(),/Executed.*unconfirmed/s);
  assert.doesNotMatch(await page.locator('[data-attempt-dag]').innerText(),/Grasp succeeded/);
  await page.locator('[data-skill-node=open]').click();
  assert.match(await page.locator('[data-skill-state=execution]').innerText(),/Execution.*Unconfirmed/);
  assert.match(await page.locator('[data-skill-state=goal]').innerText(),/Outcome.*Unconfirmed/);
  await evidence(page,'FLOW-dag-parallel');
});
test('FLOW-05: old attempt selection preserves its DAG version and missing DAG is an honest gap',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0'),'old-a1':dagFor('old-a1',{revision:'b'})};
  const {page,tick}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  snap.attempt_dags['old-a1']=dagFor('old-a1',{revision:'c'});await tick();
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-dag-revision'),'a'.repeat(64));
  assert.equal(await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').getAttribute('aria-pressed'),'true');
  delete snap.attempt_dags['old-a1'];
  await tick();await page.locator('[data-close-attempt-dag]').click();
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').click();
  assert.match(await page.locator('[data-attempt-dag-dialog]').innerText(),/No skill graph recorded/);
});
test('FLOW-06: skill evidence seeks only its bound attempt video and unknown timing stays unseekable',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  snap.traces['old-t0'].events=[{event_id:'sdk-2',timestamp:1,timestamp_basis:'relative',event_type:'skill.finished',operation:'find_objects',status:'completed',arguments:{skill_id:'detect'}}];
  snap.traces['old-t0'].evidence=[{media_url:'/replay.mp4',mime_type:'video/mp4',kind:'camera_recording',evidence_id:'video'}];
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true});
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  await page.locator('[data-skill-node=detect]').click();
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.paused&&Math.abs(v.currentTime-1)<.2});
  assert.match(await page.locator('[data-skill-detail]').innerText(),/Approximate position/);
  await evidence(page,'FLOW-skill-replay');
});
test('FLOW-07: active hint stays visible while inspecting past nodes; refresh keeps draft and node',async t=>{
  const snap=snapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  await page.locator('#ab-hint-content').fill('我的草稿');
  await page.locator('[data-flow-kind=attempt]').click();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'我的草稿');
  assert.equal(await page.locator('[data-core-region=request] textarea').isVisible(),true);
  await tick();assert.equal(await page.locator('[data-flow-kind=attempt]').getAttribute('aria-pressed'),'true');
  await page.reload();assert.equal(await page.locator('#ab-hint-content').inputValue(),'我的草稿');
  assert.equal(await page.locator('[data-flow-kind=attempt]').getAttribute('aria-pressed'),'true');
  assert.equal(state.posts.length,0);
});
test('FLOW-08: node details default to compact language; raw IDs and comparison JSON are folded',async t=>{
  const {page}=await fixture(t,{initialSnapshot:flowSnapshot()});
  await page.locator('[data-flow-kind=response]').click();
  const visible=await page.locator('[data-node-detail]').innerText();
  assert.doesNotMatch(visible,/old-a1|old-e1|reply-1|trace_digests|SDK/);
  assert.match(visible,/Received|Submitted/);
  assert.match(visible,/Unconfirmed|Unconfirmed/);
  await page.locator('[data-node-technical] > summary').click();
  assert.match(await page.locator('[data-node-technical]').innerText(),/reply-1|old-a1/);
});
test('FLOW-09: disconnected graph becomes stale without advancing; stop request is not confirmed stop',async t=>{
  const snap=flowSnapshot();const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  snap.station.interrupt={state:'requested'};await tick();
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Stop requested/);
  assert.doesNotMatch(await page.locator('[data-core-region=status]').innerText(),/Stop confirmed/);
  assert.equal(await page.locator('[data-flow-kind=planned]').count(),0);
  state.offline=true;await tick();
  assert.equal(await page.locator('[data-task-flow]').getAttribute('data-stale'),'true');
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Disconnected|older snapshot/);
});
for(const width of [1280,390])test(`FLOW-10: production graph and node details fit viewport ${width}`,async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:950}});
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  const paths=await page.locator('[data-flow-edge] path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
  assert.ok(paths.length>0&&paths.every(p=>p&&!p.includes('NaN')));
  await evidence(page,'FLOW-dialog-'+width);
  await page.locator('[data-close-attempt-dag]').click();
  await evidence(page,'FLOW-production-'+width);
});
test('FLOW-11: unknown skill timing never seeks and foreign attempt DAG is rejected',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  snap.traces['old-t0'].events=[{event_id:'sdk-2',timestamp:0,timestamp_basis:'unknown',status:'completed'}];
  snap.traces['old-t0'].evidence=[{media_url:'/replay.mp4',mime_type:'video/mp4',kind:'camera_recording'}];
  const {page,tick}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  await page.locator('[data-skill-node=detect]').click();
  assert.equal(await page.locator('[data-skill-replay]').count(),0);
  assert.match(await page.locator('[data-skill-detail]').innerText(),/No timestamp recorded/);
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1);
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
  snap.attempt_dags['old-a0'].run_id='foreign-run';await tick();
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  assert.equal(await page.locator('[data-dag-gap]').count(),1);
});
test('FLOW-12: long history folds, reveals all relationships, survives refresh and stays run-scoped',async t=>{
  const snap=flowSnapshot();snap.attempts=Array.from({length:5},(_,i)=>({attempt_id:`old-a${i}`,index:i,status:i===4?'running':'failed'}));snap.task_plan.max_attempts=6;
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  assert.equal(await page.locator('[data-flow-kind=attempt]').count(),2);
  await page.locator('[data-toggle-flow-history]').press('Enter');
  assert.equal(await page.locator('[data-flow-kind=attempt]').count(),5);
  assert.equal(await page.locator('[data-flow-edge][data-from="response:old-r"][data-to="attempt:old-a1"]').getAttribute('data-state'),'confirmed');
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  snap.resources.tokens.used=1;await tick();await page.reload();
  await page.locator('[data-flow-id="attempt:old-a0"]').waitFor();
  assert.equal(await page.locator('[data-flow-kind=attempt]').count(),5);
  assert.equal(await page.locator('[data-flow-id="attempt:old-a0"]').getAttribute('aria-pressed'),'true');
  state.snapshots.new={...snapshot('new'),attempts:Array.from({length:5},(_,i)=>({attempt_id:`new-a${i}`,index:i,status:'failed'}))};state.runs.push({run_id:'new'});await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-flow-kind=attempt]').count(),2);
  assert.equal(await page.locator('[data-flow-id="attempt:old-a0"]').count(),0);
  assert.equal(state.posts.length,0);
});
test('FLOW-13: keyboard selection preserves focus through refresh and isolates skill selection',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0'),'old-a1':dagFor('old-a1')};
  const {page,tick}=await fixture(t,{initialSnapshot:snap});
  const first=page.locator('[data-flow-id="attempt:old-a0"]');await first.focus();await first.press('Enter');
  assert.equal(await page.locator('[data-close-attempt-dag]').evaluate(e=>e===document.activeElement),true);
  await page.locator('[data-skill-node=detect]').press('Enter');
  snap.resources.tokens.used=2;await tick();
  assert.equal(await page.locator('[data-skill-node=detect]').evaluate(e=>e===document.activeElement),true);
  await page.locator('[data-close-attempt-dag]').click();
  assert.equal(await first.evaluate(e=>e===document.activeElement),true);
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  assert.equal(await page.locator('[data-skill-node=grasp]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-close-attempt-dag]').click();
  await first.click();assert.equal(await page.locator('[data-skill-node=detect]').getAttribute('aria-pressed'),'true');
});
test('FLOW-14: candidate attempt ID alone is not a confirmed reply association',async t=>{
  const snap=flowSnapshot();snap.response_evidence['old-r'].association.status='unconfirmed';
  const {page}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-flow-edge][data-from="response:old-r"][data-to="attempt:old-a1"]').getAttribute('data-state'),'unconfirmed');
  await page.locator('[data-flow-kind=response]').click();
  assert.match(await page.locator('[data-feedback-layer=association]').innerText(),/Not associated.*Unconfirmed/s);
  assert.equal(await page.locator('[data-feedback-layer=association] [data-node-replay]').count(),0);
  assert.equal(await page.locator('[data-request-history] [data-history-select=old-a1]').count(),0);
});
test('FLOW-15: resource blockage and nonterminal diagnosis cannot imply future execution or task completion',async t=>{
  const snap=flowSnapshot();snap.work_items=[{resource:'tokens',state:'blocked',required:1,remaining:0,reason:'resource_budget_exhausted'}];snap.eval={status:'completed',native_success:false,diagnosis:'evidence'};
  const {page}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-flow-kind=planned]').count(),0);
  assert.equal(await page.locator('[data-flow-kind=end]').count(),0);
  assert.equal(await page.locator('[data-flow-kind=result]').count(),1);
});


test('FLOW-16: DAG is absent by default; one attempt click opens only its child dialog',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0'),'old-a1':dagFor('old-a1',{revision:'b'})};
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true});
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),false);
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').click();
  assert.equal(await page.getByRole('dialog',{name:'Attempt 2 · Skill flow'}).isVisible(),true);
  assert.equal(await page.locator('[data-attempt-dag]').count(),1);
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-attempt-id'),'old-a1');
  assert.equal(await page.locator('.view [data-attempt-dag]').count(),0);
  await page.locator('[data-close-attempt-dag]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-attempt-dag-dialog]').open);
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  assert.equal(state.posts.length,0);
});
test('FLOW-17: refresh updates open dialog, never opens a closed one; page reload keeps selection without popup',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  await page.locator('[data-skill-node=open]').click();
  snap.attempt_dags['old-a1'].dag.nodes[1].execution_status='running';await tick();
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),true);
  assert.equal(await page.locator('[data-skill-node=open]').getAttribute('aria-pressed'),'true');
  assert.match(await page.locator('[data-skill-node=open]').innerText(),/Running/);
  await page.locator('[data-close-attempt-dag]').press('Escape');
  await page.waitForFunction(()=>!document.querySelector('[data-attempt-dag-dialog]').open);
  snap.resources.tokens.used=5;await tick();
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),false);
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  await page.reload();await page.locator('[data-flow-id="attempt:old-a1"]').waitFor();
  assert.equal(await page.locator('[data-flow-id="attempt:old-a1"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  await page.reload();await page.locator('[data-flow-id="attempt:old-a1"]').waitFor();
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),false);
});
for(const width of [1280,390])test(`FLOW-18: child dialog fits viewport and default page stays uncluttered ${width}`,async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:844}});
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  const bounds=await page.locator('[data-attempt-dag-dialog]').boundingBox();
  assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1&&bounds.y>=0&&bounds.y+bounds.height<=844+1,JSON.stringify(bounds));
  assert.ok(await page.locator('[data-skill-edge] path').evaluateAll(nodes=>nodes.length===2&&nodes.every(n=>n.getAttribute('d')&&!n.getAttribute('d').includes('NaN'))));
  if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`DAG-child-${width}.png`)});
  await page.locator('[data-close-attempt-dag]').click();
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`DAG-default-${width}.png`)});
});

test('FLOW-19: Skill DAG animation represents running only and freezes for reduced motion or stale evidence',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  assert.equal(await page.locator('[data-skill-node] .ab-skill-icon svg').count(),3);
  const animation=id=>page.locator(`[data-skill-node=${id}] .ab-skill-status-dot`).evaluate(e=>getComputedStyle(e).animationName);
  assert.notEqual(await animation('grasp'),'none');
  assert.equal(await animation('detect'),'none');
  assert.equal(await animation('open'),'none');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await animation('grasp'),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  assert.notEqual(await animation('grasp'),'none');
  state.offline=true;await tick();
  assert.equal(await animation('grasp'),'none');
  assert.equal(await page.locator('[data-dag-stale-warning]').isVisible(),true);
  assert.match(await page.locator('[data-skill-node=grasp]').innerText(),/Running/);
  assert.doesNotMatch(await page.locator('[data-skill-node=grasp]').innerText(),/Goal achieved/);
  state.offline=false;await tick();
  assert.notEqual(await animation('grasp'),'none');
  assert.equal(state.posts.length,0);
});
for(const width of [1280,390])test(`FLOW-20: rich branched Skill DAG remains scrollable and keyboard selectable ${width}`,async t=>{
  const snap=flowSnapshot(),bound=dagFor('old-a1');
  bound.dag.root_skill='evaluate';
  bound.dag.nodes[2].dependencies=['approach'];
  bound.dag.nodes[2].execution_status='planned';
  bound.dag.nodes.push(
    {node_id:'approach',name:'approach',label:'接近',description:'对准物体后接近抓取位置',dependencies:['detect','open'],execution_status:'running',goal_status:'unconfirmed',event_ids:[]},
    {node_id:'lift',name:'lift',label:'抬起',description:'抓稳后抬起物体',dependencies:['grasp'],execution_status:'not_executed',goal_status:'unconfirmed',event_ids:[]},
    {node_id:'evaluate',name:'evaluate',label:'检查',description:'核对原生任务结果',dependencies:['lift'],execution_status:'unknown',goal_status:'unconfirmed',event_ids:[]});
  snap.attempt_dags={'old-a1':bound};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  assert.equal(await page.locator('[data-skill-node] .ab-skill-icon svg').count(),6);
  assert.equal(await page.locator('[data-skill-edge]').count(),5);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  const scroll=page.locator('[data-attempt-dag] .ab-flow-scroll');
  await scroll.evaluate(e=>e.scrollLeft=e.scrollWidth);
  assert.ok(await scroll.evaluate(e=>e.scrollLeft>0));
  await page.locator('[data-skill-node=evaluate]').press('Enter');
  assert.equal(await page.locator('[data-skill-node=evaluate]').getAttribute('aria-pressed'),'true');
  assert.match(await page.locator('[data-skill-detail]').innerText(),/Check.*Progress unconfirmed/s);
  await page.waitForFunction(()=>[...document.querySelectorAll('[data-skill-edge] path')].every(e=>e.getAttribute('d')));
  assert.ok(await page.locator('[data-skill-edge] path').evaluateAll(ns=>ns.every(n=>n.getAttribute('d')&&!n.getAttribute('d').includes('NaN'))));
  snap.resources.tokens.used=3;await tick();
  assert.equal(await page.locator('[data-skill-node=evaluate]').evaluate(e=>e===document.activeElement),true);
  assert.ok(await scroll.evaluate(e=>e.scrollLeft>0));
  if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`DAG-style-rich-${width}.png`)});
  await page.locator('[data-skill-node=approach]').click();
  await page.locator('[data-close-attempt-dag]').press('Escape');
  await page.waitForFunction(()=>!document.querySelector('[data-attempt-dag-dialog]').open);
  assert.equal(await page.locator('[data-flow-id="attempt:old-a1"]').evaluate(e=>e===document.activeElement),true);
  assert.deepEqual(errors,[]);assert.equal(state.posts.length,0);
});

test('CURSOR-01: full page reload restores a manual playhead paused and retains the selected history',async t=>{
  const {page,state}=await fixture(t,{initialSnapshot:timelineSnapshot()});
  await page.locator('[data-history-select=old-a0]').click();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  await page.locator('video').evaluate(v=>{v.pause();v.currentTime=2.2;});
  await page.waitForFunction(()=>!document.querySelector('video').seeking&&Math.abs(document.querySelector('video').currentTime-2.2)<.02);
  await page.reload();
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.readyState>=2&&!v.seeking&&Math.abs(v.currentTime-2.2)<.02;});
  assert.equal(await page.locator('video').evaluate(v=>v.paused),true);
  assert.equal(await page.locator('[data-execution-viewer]').getAttribute('data-replay-attempt'),'old-a0');
  assert.match(await page.locator('[data-replay-status]').innerText(),/Replay position restored/);
  assert.equal(state.posts.length,0);
});
test('CURSOR-02: identical media URLs never share a playhead across attempts or runs',async t=>{
  const snap=timelineSnapshot();
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'failed'});
  snap.traces['old-t1']={...structuredClone(snap.traces['old-t0']),trace_id:'old-t1',attempt_id:'old-a1'};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  const setTime=async seconds=>{await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);await page.locator('video').evaluate((v,s)=>{v.pause();v.currentTime=s;},seconds);await page.waitForFunction(s=>!document.querySelector('video').seeking&&Math.abs(document.querySelector('video').currentTime-s)<.02,seconds);};
  await page.locator('[data-history-select=old-a0]').click();await setTime(2.2);
  await page.locator('[data-history-select=old-a1]').click();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
  await setTime(1.6);
  await page.locator('[data-history-select=old-a0]').click();
  await page.waitForFunction(()=>Math.abs(document.querySelector('video')?.currentTime-2.2)<.02);
  const other=timelineSnapshot('new');other.traces['new-t0'].evidence[0].media_url=snap.traces['old-t0'].evidence[0].media_url;
  state.snapshots.new=other;state.runs.push({run_id:'new'});await tick();
  await switchViewingRun(page,'new');
  await page.locator('[data-history-select=new-a0]').click();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
  await setTime(.8);
  await switchViewingRun(page,'old');
  await page.waitForFunction(()=>Math.abs(document.querySelector('video')?.currentTime-2.2)<.02);
  await page.locator('[data-history-select=old-a1]').click();
  await page.waitForFunction(()=>Math.abs(document.querySelector('video')?.currentTime-1.6)<.02);
  assert.equal(state.posts.length,0);
});
test('CURSOR-03: selected trace sample restores after reload and changed evidence resets it',async t=>{
  const snap=timelineSnapshot();snap.traces['old-t0'].evidence[0].sha256='a'.repeat(64);
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-history-select=old-a0]').click();
  await page.locator('summary[data-replay-event="2"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-replay-status]')?.textContent.startsWith('Located'));
  await page.reload();
  await page.waitForFunction(()=>Math.abs(document.querySelector('video')?.currentTime-1.2)<.02);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),1);
  assert.match(await page.locator('[data-replay-status]').innerText(),/Synchronized sample frame/);
  snap.traces['old-t0'].evidence[0].sha256='b'.repeat(64);await tick();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),0);
  assert.equal(state.posts.length,0);
});
test('FLOW-21: removing the selected run closes its open Skill DAG child dialog',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  state.runs=[];await tick();
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),false);
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  assert.equal(state.posts.length,0);
});
test('CURSOR-04: malformed viewing state cannot create an action association or prevent UI loading',async t=>{
  const {page,state}=await fixture(t,{initialSnapshot:timelineSnapshot()});
  await page.locator('[data-history-select=old-a0]').click();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  const key=await page.locator('video').getAttribute('data-replay-key');
  assert.ok(key);
  // Install after page unload saved the real cursor, before UI reads storage.
  await page.addInitScript(key=>{const value=sessionStorage.getItem('cursor-test-override');if(value)sessionStorage.setItem('attention.replay:'+key,value);},key);
  for(const value of ['{broken',JSON.stringify({seconds:-1}),JSON.stringify({seconds:.8,selection:{index:'1',seconds:.5,mode:'sample'}})]) {
    await page.evaluate(value=>sessionStorage.setItem('cursor-test-override',value),value);
    await page.reload();await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
    if(value.includes('.8'))await page.waitForFunction(()=>Math.abs(document.querySelector('video').currentTime-.8)<.02);
    else assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
    assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),0);
    assert.equal(await page.locator('[data-core-region=status]').isVisible(),true);
  }
  assert.equal(state.posts.length,0);
});

async function floatOverview(page) {
  await page.evaluate(()=>{
    const r=document.querySelector('[data-run-overview]').getBoundingClientRect(),target=scrollY+r.bottom+24;
    // A shorter three-region fixture needs a long-page tail to test scrolling.
    if(document.documentElement.scrollHeight-innerHeight<target) {
      const tail=document.createElement('div');tail.dataset.testScrollTail='';tail.style.height='900px';document.body.appendChild(tail);
    }
    window.scrollTo(0,target);
  });
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.floating==='true');
}
for(const width of [1280,390])test(`FLOAT-01: full progress scrolls normally; compact floating controls fit ${width}`,async t=>{
  const {page,state}=await fixture(t,{initialSnapshot:timelineSnapshot(),fullDocument:true,styled:true,viewport:{width,height:844}});
  const card=page.locator('[data-run-overview]');
  assert.equal(await card.evaluate(e=>getComputedStyle(e).position),'relative');
  await page.locator('[data-history-select=old-a0]').click();
  await floatOverview(page);
  assert.equal(await card.getAttribute('data-expanded'),'false');
  assert.equal(await page.locator('[data-task-flow]').isVisible(),false);
  assert.equal(await page.locator('[data-overview-toggle]').getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('[data-overview-open-sidebar]').isVisible(),true);
  const r=await card.boundingBox();assert.ok(r.x>=0&&r.y>=0&&r.x+r.width<=width+1&&r.y+r.height<=844+1,JSON.stringify(r));
  assert.ok(r.height<(width===390?240:210),JSON.stringify(r));
  assert.equal(await page.locator('[data-execution-viewer] video').isVisible(),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`floating-${width}.png`)});
  await page.locator('[data-overview-toggle]').click();
  assert.equal(await page.locator('[data-task-flow]').isVisible(),true);
  assert.equal(await page.locator('[data-overview-toggle]').getAttribute('aria-expanded'),'true');
  if(process.env.UI_EVIDENCE_DIR)await page.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`floating-expanded-${width}.png`)});
  await page.locator('[data-overview-toggle]').click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.floating==='false');
  assert.equal(await page.locator('[data-task-flow]').isVisible(),true);
  assert.equal(state.posts.length,0);
});
test('FLOAT-02: dragging, keyboard movement and resize remain in viewport and survive polling',async t=>{
  const {page,state,tick}=await fixture(t,{initialSnapshot:timelineSnapshot(),fullDocument:true,styled:true,viewport:{width:1280,height:844}});
  await floatOverview(page);
  const handle=page.locator('[data-overview-drag]'),card=page.locator('[data-run-overview]');
  const h=await handle.boundingBox();
  await page.mouse.move(h.x+h.width/2,h.y+h.height/2);await page.mouse.down();await page.mouse.move(45,55);await page.mouse.up();
  const moved=await card.boundingBox();assert.ok(moved.x<100&&moved.y<100,JSON.stringify(moved));
  await handle.press('ArrowRight');
  const keyed=await card.boundingBox();assert.ok(keyed.x>moved.x);
  const snap=state.snapshots.old;
  snap.resources.tokens.used=1;await tick();
  const after=await card.boundingBox();assert.ok(Math.abs(after.x-keyed.x)<1&&Math.abs(after.y-keyed.y)<1,JSON.stringify({after,keyed}));
  assert.equal(await handle.evaluate(e=>e===document.activeElement),true);
  await page.setViewportSize({width:390,height:400});
  await page.waitForFunction(()=>{const r=document.querySelector('[data-run-overview]').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1;});
  assert.equal(state.posts.length,0);
});
test('FLOAT-03: floating progress opens the bound child DAG and preserves replay through refresh',async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  snap.traces['old-t0'].evidence=[{media_url:'/replay.mp4',mime_type:'video/mp4',kind:'camera_recording'}];
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,fullDocument:true,styled:true});
  await page.locator('[data-history-select=old-a0]').first().click();
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  await page.locator('video').evaluate(v=>{v.pause();v.currentTime=1.5;});
  await page.waitForFunction(()=>!document.querySelector('video').seeking);
  await floatOverview(page);await page.locator('[data-overview-toggle]').click();
  await page.locator('[data-flow-id="attempt:old-a0"]').click();
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-attempt-id'),'old-a0');
  await page.locator('[data-close-attempt-dag]').click();
  snap.resources.tokens.used=2;await tick();
  assert.equal(await page.locator('[data-run-overview]').getAttribute('data-expanded'),'true');
  assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-1.5)<.02);
  assert.equal(state.posts.length,0);
});
test('FLOAT-04: compact floating state retains reply, stop and stale warnings without losing draft',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true});
  await page.locator('#ab-hint-content').fill('从物体中心上方接近');
  await floatOverview(page);
  assert.equal(await page.locator('[data-answer-current]').isVisible(),true);
  assert.equal(await page.locator('#ab-emergency-interrupt').isVisible(),true);
  await page.locator('[data-answer-current]').click();
  assert.equal(await page.locator('#ab-hint-content').evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'从物体中心上方接近');
  state.offline=true;await tick();
  assert.equal(await page.locator('#ab-stale-warning').isVisible(),true);
  assert.match(await page.locator('[data-action-needed]').innerText(),/Disconnected/);
  state.offline=false;state.snapshots.old.station.interrupt={state:'requested'};await tick();
  assert.match(await page.locator('[data-stop-state]').innerText(),/Stop requested/);
  assert.equal(state.posts.length,0);
});
test('FLOAT-05: manual progress collapse survives polling/reload and stays scoped to each run',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true});
  await page.locator('[data-overview-toggle]').click();
  assert.equal(await page.locator('[data-task-flow]').isVisible(),false);
  state.snapshots.old.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('[data-task-flow]').isVisible(),false);
  await page.reload();await page.locator('[data-overview-toggle]').waitFor();
  assert.equal(await page.locator('[data-task-flow]').isVisible(),false);
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new'});await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-task-flow]').isVisible(),true);
  assert.equal(state.posts.length,0);
});

async function showNode(page,kind='request') {
  if(await page.locator('[data-more-dialog]').isVisible())await page.locator('[data-close-more]').click();
  if(await page.locator('[data-flow-detail-dialog]').isVisible())await page.locator('[data-close-flow-detail]').click();
  if(await page.locator('[data-attempt-dag-dialog]').isVisible())await page.locator('[data-close-attempt-dag]').click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.floating==='false');
  if(!await page.locator('[data-task-flow]').isVisible())await page.locator('[data-overview-toggle]').click();
  await page.locator(`[data-flow-kind=${kind}]`).first().click();
}
async function openMore(page) {
  if(await page.locator('[data-flow-detail-dialog]').isVisible())await page.locator('[data-close-flow-detail]').click();
  if(await page.locator('[data-attempt-dag-dialog]').isVisible())await page.locator('[data-close-attempt-dag]').click();
  if(!await page.locator('[data-more-dialog]').isVisible())await page.locator('[data-open-more]').click();
}
for(const width of [1280,390])test(`SLIM-01: three decision regions with persistent settings/resources and on-demand developer details ${width}`,async t=>{
  const snap=flowSnapshot();snap.attempt_dags={'old-a1':dagFor('old-a1')};
  const {page,state}=await fixture(t,{initialSnapshot:snap,fullDocument:true,styled:true,viewport:{width,height:900}});
  assert.deepEqual(await page.locator('.view > [data-core-region]').evaluateAll(es=>es.map(e=>e.dataset.coreRegion)),['status','execution','request']);
  assert.equal(await page.locator('[data-node-detail],[data-secondary]').count(),0);
  assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),false);
  assert.equal(await page.locator('[data-more-dialog]').isVisible(),false);
  await evidence(page,'SLIM-default-'+width);
  await page.locator('[data-flow-id="attempt:old-a1"]').click();
  assert.equal(await page.locator('.view [data-node-detail]').count(),0);
  assert.equal(await page.locator('[data-attempt-summary]').count(),1);
  assert.equal(await page.locator('[data-node-technical]').getAttribute('open'),null);
  await evidence(page,'SLIM-attempt-'+width);
  await page.locator('[data-close-attempt-dag]').click();
  await openMore(page);
  assert.equal(await page.locator('[data-secondary]').count(),1);
  assert.equal(await page.locator('.view [data-secondary]').count(),0);
  for(const name of ['debug'])assert.equal(await page.locator(`[data-secondary=${name}]`).getAttribute('open'),null);
  await evidence(page,'SLIM-more-'+width);
  await page.locator('[data-close-more]').click();
  assert.equal(await page.locator('[data-secondary]').count(),0);
  assert.equal(await page.locator('[data-open-more]').evaluate(e=>e===document.activeElement),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);
});
test('SLIM-02: on-demand reply evidence stays separate, survives polling and closes without reopening',async t=>{
  const snap=flowSnapshot();const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  await showNode(page,'response');
  assert.equal(await page.locator('[data-feedback-layer]').count(),4);
  assert.match(await page.locator('[data-feedback-layer=association]').innerText(),/Linked to attempt 2/);
  assert.match(await page.locator('[data-feedback-layer=control]').innerText(),/Insufficient evidence/);
  assert.match(await page.locator('[data-feedback-layer=native]').innerText(),/Unconfirmed/);
  const summary=page.locator('[data-node-technical]>summary');await summary.click();
  const scroll=await page.locator('[data-flow-detail-dialog]').evaluate(e=>{e.scrollTop=40;return e.scrollTop;});
  snap.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),true);
  assert.notEqual(await page.locator('[data-node-technical]').getAttribute('open'),null);
  assert.equal(await summary.evaluate(e=>e===document.activeElement),true);
  assert.ok(Math.abs(await page.locator('[data-flow-detail-dialog]').evaluate(e=>e.scrollTop)-scroll)<2);
  state.offline=true;await tick();assert.equal(await page.locator('[data-flow-detail-dialog] [data-detail-stale-warning]').isVisible(),true);
  state.offline=false;await tick();
  await page.locator('[data-close-flow-detail]').press('Escape');
  assert.equal(await page.locator('[data-node-detail]').count(),0);
  assert.equal(await page.locator('[data-flow-kind=response]').evaluate(e=>e===document.activeElement),true);
  snap.resources.tokens.used=2;await tick();assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),false);
  await page.reload();assert.equal(await page.locator('[data-node-detail]').count(),0);
  assert.equal(state.posts.length,0);
});
test('SLIM-03: attempt metadata and native outcome share its skill child window; replay links its own video',async t=>{
  const snap=timelineSnapshot();snap.attempt_dags={'old-a0':dagFor('old-a0')};
  snap.eval={status:'completed',native_outcomes:{'old-a0':{evaluated:true,native_success:false}}};
  snap.attempts[0].started_at=1791300000;snap.attempts[0].ended_at=1791300002;snap.attempts[0].status_source='persisted_attempt';
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true});
  await showNode(page,'attempt');
  assert.match(await page.locator('[data-attempt-summary]').innerText(),/Task unsuccessful/);
  assert.doesNotMatch(await page.locator('[data-attempt-summary]').innerText(),/old-a0|persisted_attempt/);
  await page.locator('[data-node-technical]>summary').click();
  assert.match(await page.locator('[data-node-technical]').innerText(),/old-a0.*Started.*Ended.*persisted_attempt/s);
  snap.resources.tokens.used=1;await tick();assert.notEqual(await page.locator('[data-node-technical]').getAttribute('open'),null);
  await page.locator('[data-attempt-summary] [data-node-replay]').click();
  assert.equal(await page.locator('[data-attempt-dag-dialog]').isVisible(),false);
  assert.equal(await page.locator('.ab-history-tabs [data-history-select=old-a0]').getAttribute('aria-pressed'),'true');
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  assert.equal(state.posts.length,0);
});
test('SLIM-04: persistent resources and More preserve focus and draft while refreshing',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true});
  await page.locator('#ab-hint-content').fill('保留输入');
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  state.snapshots.old.budget_events=[{sequence:6,event_type:'budget.released',payload:{credits:1,reason:'run_terminal'}}];await tick();
  await page.locator('[data-budget-events]>summary').click();
  const focus=page.locator('[data-budget-events]>summary');
  state.snapshots.old.resources.assistance.used=1;state.snapshots.old.resources.assistance.remaining=9;await tick();
  assert.equal(await page.locator('[data-resource-details]').isVisible(),true);
  assert.notEqual(await page.locator('[data-budget-events]').getAttribute('open'),null);
  assert.equal(await focus.evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.locator('[data-secondary=raw]').count(),0);
  await openMore(page);
  await page.locator('[data-secondary=debug]>summary').click();
  const graphInput=page.locator('#ab-graph-form [name=graph]');await graphInput.fill('retained-graph');
  await graphInput.evaluate(e=>e.setSelectionRange(2,7,'backward'));
  state.snapshots.old.resources.tokens.used=1;await tick();
  assert.deepEqual(await graphInput.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e.selectionDirection,e===document.activeElement]),['retained-graph',2,7,'backward',true]);
  await page.locator('[data-close-more]').press('Escape');
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'保留输入');
  state.snapshots.old.resources.tokens.used=2;await tick();assert.equal(await page.locator('[data-secondary]').count(),0);
  assert.equal(state.posts.length,0);
});
test('SLIM-05: expired planned node and missing run close dialogs rather than displaying foreign details',async t=>{
  const snap=flowSnapshot();const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  await showNode(page,'planned');assert.match(await page.locator('[data-node-detail]').innerText(),/has not started/);
  snap.attempts.push({attempt_id:'old-a2',index:2,status:'running'});await tick();
  assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),false);
  await openMore(page);state.runs=[];await tick();
  assert.equal(await page.locator('[data-more-dialog]').isVisible(),false);
  assert.equal(await page.locator('[data-secondary]').count(),0);
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  assert.equal(state.posts.length,0);
});
test('SLIM-06: current request opens from the request node and a submission only adds a compact feedback link',async t=>{
  const {page,state,tick}=await fixture(t,{styled:true});
  await page.locator('#ab-hint-content').fill('保持草稿与实际提交');
  await showNode(page,'request');await page.locator('[data-open-flow-request]').click();
  assert.equal(await page.locator('#ab-hint-content').evaluate(e=>e===document.activeElement),true);
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'保持草稿与实际提交');
  await page.locator('[data-respond=hint]').click();
  await page.locator('[data-open-flow-node]').waitFor();
  assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),false);
  assert.match(await page.locator('[data-open-flow-node]').innerText(),/Reply received/);
  await page.locator('[data-open-flow-node]').click();
  assert.match(await page.locator('[data-feedback-layer=submission]').innerText(),/Submitted/);
  assert.equal(state.posts.length,1);
  state.snapshots.old.resources.tokens.used=2;await tick();assert.equal(state.posts.length,1);
});
test('SLIM-07: empty workspace More has no previous run data, and overlay closes without business actions',async t=>{
  const {page,state}=await fixture(t,{styled:true,beforeNavigate:async(_,s)=>{s.runs=[];}});
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  await openMore(page);
  assert.equal(await page.locator('[data-secondary]').count(),1);
  assert.equal(await page.locator('[data-secondary=raw]').count(),0);
  await page.mouse.click(2,2);
  assert.equal(await page.locator('[data-more-dialog]').isVisible(),false);
  assert.equal(state.posts.length,0);
});

async function fixture(t, {styled = false, viewport, initialSnapshot, beforeNavigate, fullDocument = false, operator = false, sourceText = source} = {}) {
  const page = await browser.newPage(viewport ? {viewport} : {}); t.after(() => page.close());
  if(process.env.UI_TARGET_TIMEOUT)page.setDefaultTimeout(Number(process.env.UI_TARGET_TIMEOUT));
  page.on("pageerror",error=>{if(process.env.UI_DEBUG_ERRORS)process.stderr.write("PAGE ERROR: "+error.stack+"\n");});
  const state = {snapshots: {old: initialSnapshot || snapshot()}, runs: [{run_id: 'old', task_id: 'cube_lift'}], launches: [], token: 'token-before', offline: false, posts: [], previewPosts: []};
  const html = () => fullDocument ? fullHtml.replace('data-mode="demo"','data-mode="live"').replace('data-mode="live"',`data-mode="live" data-control-token="${state.token}"`) : `<meta charset="utf-8">${styled ? `<style>${stylesheet}</style>` : ''}<div id="attentionbench-clear" data-mode="live" data-control-token="${state.token}"><div class="ribbon"></div><div class="demo"></div><div class="view"></div></div><script src="/live.js"></script>`;
  await page.addInitScript(() => {
    window.__intervals = [];
    window.setInterval = (fn, ms) => { window.__intervals.push({fn, ms}); return window.__intervals.length; };
    window.__tick = async () => { for (const item of window.__intervals.filter(item => item.ms === 3000)) await item.fn(); };
    window.__cameraTick = async () => { for (const item of window.__intervals.filter(item => item.ms === 1000)) await item.fn(); };
  });
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), pathname = url.pathname;
    if (state.offline && pathname.startsWith('/api/')) return route.abort('connectionrefused');
    const send = (json, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(json)});
    if (pathname === '/ui/') return route.fulfill({contentType: 'text/html', body: html()});
    if (pathname === '/live.js' || pathname === '/ui/live.js') return route.fulfill({contentType: 'text/javascript; charset=utf-8', body: sourceText});
    if (pathname === '/replay.mp4') return serveTestReplay(route);
    if (pathname === '/camera') return route.fulfill({contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Y9sAAAAASUVORK5CYII=', 'base64')});
    if (req.method() === 'POST') {
      if (pathname.endsWith('/guidance-preview')) {
        if (req.headers()['x-attention-token'] !== state.token) return send({error:'control token required'},403);
        const payload=req.postDataJSON(); state.previewPosts.push({pathname,payload});
        return send({request_id:decodeURIComponent(pathname.split('/')[3]),run_id:'old',
          ...(state.previewResult || {status:'unrecognized',parameters:{}})});
      }
      state.posts.push({pathname, token: req.headers()['x-attention-token'], payload: req.postDataJSON?.()});
      if (req.headers()['x-attention-token'] !== state.token) return send({error: 'control token required'}, 403);
      if (pathname === '/api/runs/start') { state.launches = [{launch_id: 'launch-new', state: 'started'}]; return send({launch_id: 'launch-new'}); }
      if (pathname.endsWith('/defer')) state.snapshots.old.request_lifecycle['old-r'] = [{event_type: 'request.deferred'}];
      if (pathname.endsWith('/respond')) state.snapshots.old.requests[0].state = 'answered';
      return send({state: 'ok'});
    }
    if (pathname === '/api/run-options') return send(state.launchOptions || {profiles: [{id: 'lift', suite: 'robosuite', task: 'cube_lift', execution_target: 'robosuite_sim', seed: 101}], attention_policies: ['autonomous', 'reactive_help'], assistance_modes: ['benchmark_proxy', 'live_human_first']});
    if (pathname === '/api/runs') return send({runs: state.runs, launches: state.launches, ...(state.devSessions ? {dev_sessions: state.devSessions} : {})});
    if (pathname.startsWith('/api/runs/')) return send(state.snapshots[decodeURIComponent(pathname.slice('/api/runs/'.length))]);
    return send({error: 'No Orchestrator'}, 503);
  });
  if (beforeNavigate) await beforeNavigate(page, state);
  await page.goto('http://attention.test/ui/?run=old'+(operator?'':'&debug=1'));
  await page.locator('.view > [data-core-region=status]').waitFor();
  if (state.runs.length && !operator) await page.locator('#ab-live-run').waitFor({state:'attached'});
  return {page, state, tick: () => page.evaluate(() => window.__tick())};
}
async function evidence(page, name) {
  if (process.env.UI_EVIDENCE_DIR) {
    fs.mkdirSync(process.env.UI_EVIDENCE_DIR, {recursive: true});
    const scroll = await page.evaluate(()=>[scrollX,scrollY]);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path: path.join(process.env.UI_EVIDENCE_DIR, name+'.png'), fullPage: true});
    await page.evaluate(([x,y])=>window.scrollTo(x,y),scroll);
  }
}
test('IA-01: three core regions replace duplicate boards and advanced content is collapsed', async t => {
  const {page,state}=await fixture(t,{styled:true});
  assert.deepEqual(await page.locator('.view > [data-core-region]').evaluateAll(nodes=>nodes.map(n=>n.dataset.coreRegion)),['status','execution','request']);
  assert.equal(await page.locator('[data-action-needed]').getAttribute('data-needed'),'yes');
  assert.match(await page.locator('[data-action-needed]').innerText(),/Action required/);
  assert.equal(await page.locator('[data-action-needed]').getAttribute('role'),'status');
  assert.doesNotMatch(await page.locator('.view').innerText(),/CURRENT RUN|RUN STATE|Attention inbox|Request detail|Autonomous work|AGENTS|Success ↔ assistance|SQLite/);
  assert.equal(await page.locator('[data-secondary]').count(),0);
  await openMore(page);
  for(const entry of ['debug']) {
    assert.equal(await page.locator(`[data-secondary="${entry}"]`).getAttribute('open'),null);
  }
  assert.equal(await page.locator('[data-sidebar] #ab-live-run').count(),1);
  assert.equal(await page.locator('[data-core-region=status] #ab-emergency-interrupt').count(),1);
  assert.equal(state.posts.length,0);
});
test('IA-02: phase switches compact the request and show four feedback layers after submission',async t=>{
  const {page,state,tick}=await fixture(t),snap=state.snapshots.old;
  assert.equal(await page.locator('[data-core-region=request] textarea').count(),1);
  await page.locator('#ab-hint-content').fill('降低抓取高度，夹紧后确认抓牢');
  await page.locator('[data-respond=hint]').click();
  await page.waitForFunction(()=>document.querySelector('[data-core-region=request]')?.dataset.state==='idle');
  assert.match(await page.locator('[data-core-region=request]').innerText(),/No action required/);
  assert.equal(await page.locator('[data-core-region=request] textarea').count(),0);
  assert.equal(await page.locator('[data-request-history]').getAttribute('open'),null);
  assert.equal(await page.locator('[data-node-detail]').count(),0);
  await page.locator('[data-open-flow-node]').click();
  assert.equal(await page.locator('[data-node-detail] [data-feedback-layer]').count(),4);
  assert.match(await page.locator('[data-feedback-layer=submission]').innerText(),/Submitted/);
  assert.doesNotMatch(await page.locator('[data-feedback-layer=submission]').innerText(),/Adopted/);
  assert.match(await page.locator('[data-feedback-layer=association]').innerText(),/Not associated|Unconfirmed/);
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'running'});await tick();
  assert.equal(await page.locator('[data-run-overview]').getAttribute('data-phase'),'running');
  assert.equal(await page.locator('[data-action-needed]').getAttribute('data-needed'),'no');
  snap.run.status='completed';snap.eval={status:'completed',native_success:false,stopped_reason:'max_attempts'};await tick();
  await showNode(page,'end');
  assert.match(await page.locator('[data-node-detail]').innerText(),/Task unsuccessful|Stop reason/);
});
test('IA-03: pending request preserves draft selection and focus across secondary entry refresh',async t=>{
  const {page,state,tick}=await fixture(t);
  assert.equal(await page.locator('[data-run-settings]').isVisible(),true);
  state.snapshots.old.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('[data-settings-agents]').isVisible(),true);
  await page.locator('#ab-hint-content').fill('保持当前选区与焦点');
  await page.locator('#ab-hint-content').evaluate(el=>{el.focus();el.setSelectionRange(2,6,'backward');});
  state.snapshots.old.resources.tokens.used=2;await tick();
  assert.deepEqual(await page.locator('#ab-hint-content').evaluate(el=>[el.value,el.selectionStart,el.selectionEnd,el.selectionDirection,el===document.activeElement]),['保持当前选区与焦点',2,6,'backward',true]);
  assert.equal(await page.locator('[data-secondary]').count(),0);
  await page.locator('[data-defer-request]').click();
  await page.waitForFunction(()=>document.querySelector('[data-defer-request]')?.textContent==='Deferred');
  assert.deepEqual(await page.locator('#ab-hint-content').evaluate(el=>[el.selectionStart,el.selectionEnd,el.selectionDirection,el===document.activeElement]),[2,6,'backward',true]);
});
test('IA-04: stop requested never means confirmed, including terminal unconfirmed receipts',async t=>{
  const {page,state,tick}=await fixture(t),snap=state.snapshots.old;
  snap.station.interrupt={state:'requested'};await tick();
  assert.match(await page.locator('[data-stop-state]').innerText(),/Stop requested.*awaiting executor confirmation/s);
  assert.equal(await page.locator('[data-core-region=request] textarea').count(),0);
  assert.doesNotMatch(await page.locator('[data-execution-viewer] .panel-title').innerText(),/Awaiting human reply/);
  snap.run.status='cancelled';snap.station.interrupt={state:'stop_unconfirmed'};await tick();
  assert.match(await page.locator('[data-stop-state]').innerText(),/unconfirmed/);
  assert.doesNotMatch(await page.locator('[data-stop-state]').innerText(),/Stop confirmed/);
  snap.station.interrupt={state:'stopped',confirmed_at:1791300100};await tick();
  assert.match(await page.locator('[data-stop-state]').innerText(),/Stop confirmed/);
  assert.equal(await page.locator('#ab-emergency-interrupt').isDisabled(),true);
  snap.run.status='running';snap.attempts[0].status='running';await tick();
  assert.doesNotMatch(await page.locator('[data-execution-viewer] .panel-title').innerText(),/Running|Awaiting human reply/);
});
test('IA-05: disconnect overrides operation guidance, stale state persists then clears without losing input',async t=>{
  const {page,state,tick}=await fixture(t),input=page.locator('#ab-hint-content');
  assert.equal(await page.locator('[data-action-needed]').count(),1);
  await input.fill('重连不丢草稿');await input.evaluate(el=>{el.focus();el.setSelectionRange(1,4);});
  state.offline=true;await tick();
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Disconnected|older snapshot/);
  assert.equal(await page.locator('[data-action-needed]').getAttribute('data-needed'),'unknown');
  assert.match(await page.locator('[data-action-needed]').innerText(),/action required is unknown/);
  state.offline=false;await tick();
  assert.equal(await page.locator('[data-action-needed]').getAttribute('data-needed'),'yes');
  assert.deepEqual(await input.evaluate(el=>[el.value,el.selectionStart,el.selectionEnd,el===document.activeElement]),['重连不丢草稿',1,4,true]);
  assert.doesNotMatch(await page.locator('#ab-live-message').innerText(),/Failed to fetch/);
});
test('IA-06: supported hint presets sit next to input, never auto-submit and stay task-scoped',async t=>{
  const {page,state,tick}=await fixture(t);
  assert.equal(await page.locator('[data-core-region=request] [data-hint-format]').count(),1);
  assert.match(await page.locator('[data-hint-format]').innerText(),/Free-text.*subsequent execution/);
  await page.locator('[data-hint-preset=en]').click();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),englishCubeHint);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'ab-hint-content');
  assert.equal(state.posts.length,0);
  await tick();assert.equal(await page.locator('#ab-hint-content').inputValue(),englishCubeHint);
  state.snapshots.old.run.suite='robocasa';state.snapshots.old.run.task_id='counter_to_sink';await tick();
  assert.equal(await page.locator('[data-hint-preset=en]').count(),0);
  await page.locator('[data-hint-advanced]>summary').click();
  assert.match(await page.locator('[data-hint-format]').innerText(),/0\.030.*0\.050|0\.03.*0\.05/s);
  await page.locator('[data-hint-preset=control]').click();
  assert.match(await page.locator('#ab-hint-content').inputValue(),/^AB_CONTROL_V1 grasp_offset_m=0\.045;/);
  assert.equal(state.posts.length,0);
});
test('IA-07: deadlines count down without a snapshot mutation and hide expired reply controls',async t=>{
  const snap=snapshot();snap.requests[0].deadline_at=Date.now()/1000+3;
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-reply-countdown]').count(),1);
  assert.match(await page.locator('[data-reply-countdown]').innerText(),/remaining.*s/);
  await page.clock.setFixedTime(new Date((snap.requests[0].deadline_at+1)*1000));
  await page.evaluate(()=>window.__cameraTick());
  assert.equal(await page.locator('[data-reply-countdown]').getAttribute('data-expired'),'true');
  assert.equal(await page.locator('[data-respond]').count(),0);
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Reply timed out/);
  assert.equal(state.posts.length,0);
});
test('IA-08: fallback and insufficient control evidence are distinct from human submission',async t=>{
  const {page,state,tick}=await fixture(t),snap=state.snapshots.old;
  snap.requests[0].state='answered';snap.requests[0].response_id='proxy';
  snap.responses={proxy:{response_id:'proxy',responder:'advisor_proxy',content:'automatic'}};
  snap.request_lifecycle={'old-r':[{event_type:'request.timeout'},{event_type:'request.fallback'}]};
  snap.response_evidence={'old-r':{submission:{response_id:'proxy'},association:{attempt_id:'old-a1'},control_comparison:{status:'insufficient_evidence'},native_outcome:{status:'unconfirmed'}}};await tick();
  await showNode(page,'response');
  assert.equal(await page.locator('[data-feedback-layer]').count(),4);
  assert.match(await page.locator('[data-node-detail]').innerText(),/fallback/);
  assert.doesNotMatch(await page.locator('[data-feedback-layer=submission]').innerText(),/Human submitted/);
  assert.match(await page.locator('[data-feedback-layer=control]').innerText(),/Insufficient evidence|Unconfirmed/);
  assert.match(await page.locator('[data-feedback-layer=native]').innerText(),/Unconfirmed/);
});
test('IA-09: history cannot displace the active request and removed raw records stay absent',async t=>{
  const {page,state,tick}=await fixture(t),snap=state.snapshots.old;
  snap.requests.push({...snap.requests[0],request_id:'older',state:'answered',created_at:1});await tick();
  assert.equal(await page.locator('[data-request-history]').count(),1);
  await page.locator('[data-request-history] > summary').click();
  await page.locator('[data-live-request=older]').click();
  assert.match(await page.locator('#ab-hint-content').getAttribute('data-draft-key'),/old:old-r/);
  assert.equal(await page.locator('[data-request-history]').getAttribute('open'),'');
  assert.equal(await page.locator('[data-secondary=raw]').count(),0);
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new'});await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-secondary=raw]').count(),0);
});
test('IA-10: resource shortage surfaces in status while releases are confined to resource details',async t=>{
  const {page,state,tick}=await fixture(t),snap=state.snapshots.old;
  snap.work_items=[{work_id:'blocked',resource:'tokens',state:'blocked',required:40,remaining:0,reason:'quota'}];
  snap.budget_events=[{event_type:'budget.released',sequence:5,payload:{credits:1,reservation_id:'r',reason:'run_terminal'}}];await tick();
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Insufficient|Insufficient resources/);
  assert.equal(await page.locator('[data-resource-details] [data-budget-events]').count(),1);
  assert.equal(await page.locator('[data-core-region=status] .budget').count(),0);
  await page.locator('[data-budget-events] > summary').click();
  assert.match(await page.locator('[data-budget-events]').innerText(),/budget.released/);
  assert.equal(state.posts.length,0);
});
test('IA-11: empty workspace and pending launch keep the three regions and never retain an old run',async t=>{
  const {page,state,tick}=await fixture(t,{initialSnapshot:snapshot(),beforeNavigate:async(_,s)=>{s.runs=[];}});
  await page.locator('[data-open-launch]').waitFor();
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  assert.match(await page.locator('[data-core-region=status]').innerText(),/No Attention runs/);
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page);await page.locator('#ab-launch-form button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('.view')?.textContent.includes('Waiting for run registration'));
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  assert.match(await page.locator('[data-core-region=request]').innerText(),/No action required/);
  state.snapshots.new=snapshot('new');state.runs=[{run_id:'new'}];state.launches[0].run_id='new';await tick();
  assert.equal(await page.locator('#ab-live-run').inputValue(),'new');
});
test('IA-12: approvals use decision controls next to evidence and never display hint presets',async t=>{
  const snap=snapshot();snap.requests[0].request_type='approval';snap.requests[0].reason='本次有限重试需要审批';
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-core-region=request]').count(),1);
  assert.equal(await page.locator('#ab-hint-content,[data-hint-format]').count(),0);
  assert.match(await page.locator('[data-core-region=request]').innerText(),/有限重试.*approve or deny/s);
  await page.locator('[data-respond=deny]').click();
  await page.waitForFunction(()=>!document.querySelector('[data-respond]'));
  assert.deepEqual(state.posts.at(-1).payload,{decision:'deny'});
  await showNode(page,'response');
  assert.match(await page.locator('[data-feedback-layer=submission]').innerText(),/Submitted/);
  assert.match(await page.locator('[data-feedback-layer=association]').innerText(),/Unconfirmed|Unconfirmed/);
});
test('IA-13: current request evidence points at history without duplicating its open timeline',async t=>{
  const {page,state}=await fixture(t,{initialSnapshot:timelineSnapshot()});
  state.snapshots.old.requests=snapshot().requests;
  await page.evaluate(()=>window.__tick());
  assert.equal(await page.locator('[data-core-region=request]').count(),1);
  await page.locator('[data-history-select=old-a0]').first().click();
  assert.equal(await page.locator('[data-core-region=request] [data-request-replay]').count(),0);
  await page.locator('[data-request-technical]>summary').click();
  assert.match(await page.locator('[data-core-region=request] .trace').innerText(),/The full action timeline is open in Execution & History/);
  assert.equal(await page.locator('[data-history-event]').count(),4);
});
for(const viewport of [{width:1280,height:900},{width:390,height:844}]) test(`IA-14: status stays visible, no overflow and idle requests are compact (${viewport.width}px)`,async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,viewport});
  assert.equal(await page.locator('[data-action-needed]').count(),1);
  const action=page.locator('[data-action-needed]');
  assert.ok(await action.evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=16));
  assert.notEqual(await action.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
  await evidence(page,`IA-waiting-${viewport.width}`);
  await page.locator('#ab-hint-content').fill('降低抓取高度，夹紧后确认抓牢');
  await page.locator('[data-respond=hint]').click();
  await page.waitForFunction(()=>document.querySelector('[data-core-region=request]')?.dataset.state==='idle');
  assert.equal(await page.locator('[data-request-reply]').count(),0,'no stored hint body may be invented');
  assert.match(await page.locator('[data-hint-content-gap]').innerText(),/No guidance content available/);
  await evidence(page,`EN-idle-check-${viewport.width}`);
  assert.ok((await page.locator('[data-core-region=request]').boundingBox()).height<170,'idle state with one missing-content line remains compact');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await evidence(page,`IA-submitted-${viewport.width}`);
  await floatOverview(page);
  const header=await page.locator('[data-core-region=status]').boundingBox();
  assert.ok(header.y>=0&&header.y+header.height<=viewport.height+1,JSON.stringify(header));
  state.snapshots.old.run.status='completed';state.snapshots.old.eval={status:'completed',native_success:false,stopped_reason:'max_attempts'};await tick();
  await page.evaluate(()=>window.scrollTo(0,0));
  await evidence(page,`IA-terminal-${viewport.width}`);
});
test('IA-15: production HTML boots the same three regions and emergency stop posts once before confirmation',async t=>{
  const {page,state,tick}=await fixture(t,{fullDocument:true,viewport:{width:1280,height:900}});
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  assert.equal(await page.locator('[data-action-needed]').getAttribute('data-needed'),'yes');
  await page.route('**/api/runs/old/interrupt',async route=>{
    state.posts.push({pathname:new URL(route.request().url()).pathname,token:route.request().headers()['x-attention-token']});
    state.snapshots.old.station.interrupt={state:'requested'};
    await route.fulfill({contentType:'application/json',body:'{"state":"requested"}'});
  });
  await evidence(page,'IA-production-waiting');
  await page.locator('#ab-emergency-interrupt').click();
  await page.waitForFunction(()=>document.querySelector('[data-stop-state]')?.textContent.includes('awaiting executor confirmation'));
  assert.equal(state.posts.length,1);assert.equal(state.posts[0].token,'token-before');
  assert.equal(await page.locator('#ab-emergency-interrupt').isDisabled(),true);
  assert.equal(await page.locator('[data-respond]').count(),0);
  state.snapshots.old.station.interrupt={state:'stopped'};state.snapshots.old.run.status='cancelled';await tick();
  assert.match(await page.locator('[data-stop-state]').innerText(),/Stop confirmed/);
  assert.equal(state.posts.length,1);
  await evidence(page,'IA-production-confirmed-stop');
});
test('IA-16: removed experiment statistics leave the comparison API data outside the workspace',async t=>{
  const {page,state,tick}=await fixture(t,{fullDocument:true});
  state.snapshots.old.comparison={comparable:false,policies:[{policy_id:'reactive_help',successes:1,runs:2,success_rate:.5,average_assistance_used:1}]};await tick();
  await openMore(page);
  assert.equal(await page.locator('[data-open-statistics],[data-secondary=statistics],[data-secondary=raw]').count(),0);
  assert.doesNotMatch(await page.locator('[data-more-dialog]').innerText(),/Success ↔ assistance|Success rate/);
  await page.locator('[data-close-more]').click();
  await page.goto('http://attention.test/ui/?run=old&view=statistics&debug=1');
  await page.locator('#ab-live-run').waitFor({state:'attached'});
  assert.equal(await page.locator('.view > [data-core-region]').count(),3);
  assert.equal(await page.locator('[data-statistics-page]').count(),0);
  assert.equal(state.posts.length,0);
});
test('UI-01: starting a run shows pending launch then selects only its linked run', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.run.status = 'failed'; await tick();
  await page.locator('[data-open-launch]').click(); await selectLaunchTask(page); await page.locator('#ab-launch-form button[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('dialog[aria-label="Configure Attention run"]').open);
  await evidence(page, 'UI-01-pending');
  assert.match(await page.locator('.view').innerText(), /launch-new.*waiting|waiting.*launch-new/s);
  state.snapshots.new = snapshot('new'); state.snapshots.other = snapshot('other');
  state.runs = [{run_id: 'other'}, {run_id: 'new'}, ...state.runs]; state.launches[0].run_id = 'new'; await tick();
  assert.equal(await page.locator('#ab-live-run').inputValue(), 'new');
  assert.match(page.url(), /run=new/); await evidence(page, 'UI-01-linked');
});
test('UI-02: draft survives defer, refresh, focus restoration and page reload', async t => {
  const {page, state, tick} = await fixture(t);
  await page.locator('#ab-hint-content').fill('自动刷新草稿：请从物体中心上方接近。');
  await page.locator('[data-defer-request]').click(); await page.waitForFunction(() => document.querySelector('[data-defer-request]')?.disabled);
  await tick(); await evidence(page, 'UI-02-defer');
  assert.equal(await page.locator('#ab-hint-content').inputValue(), '自动刷新草稿：请从物体中心上方接近。');
  await page.locator('#ab-hint-content').focus(); state.snapshots.old.attempts[0].status = 'timed_out'; await tick();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'ab-hint-content');
  await page.reload(); await page.locator('#ab-hint-content').waitFor();
  assert.equal(await page.locator('#ab-hint-content').inputValue(), '自动刷新草稿：请从物体中心上方接近。');
});
test('UI-03: old page renews rotated token and submits once after explicit 403', async t => {
  const {page, state} = await fixture(t); state.token = 'token-after-restart';
  await page.locator('#ab-hint-content').fill('请缓慢抬起。'); await page.locator('[data-respond=hint]').click();
  await page.waitForFunction(() => !document.querySelector('[data-respond=hint]') || !document.querySelector('[data-respond=hint]').disabled);
  await evidence(page, 'UI-03-restart');
  assert.equal(state.snapshots.old.requests[0].state, 'answered');
  assert.equal(state.posts.filter(p => p.token === 'token-after-restart').length, 1);
  assert.equal(state.posts.at(-1).payload.content, '请缓慢抬起。');
});
test('UI-05: unknown or relative trace time never becomes a 1970 date', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.traces['old-t0'].events.push({timestamp: 1.25, sequence: 1, timestamp_basis: 'relative', operation: 'gripper', status: 'completed'}); await tick();
  await page.locator('[data-request-technical]>summary').click();
  const trace = await page.locator('.trace').innerText(); await evidence(page, 'UI-05-clock');
  assert.doesNotMatch(trace, /1970/); assert.match(trace, /Time not recorded/); assert.match(trace, /\+1\.25\s*s/);
});
test('UI-06: latest execution belongs to latest attempt, never old request trace', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.attempts.push({attempt_id: 'old-a1', index: 1, status: 'succeeded'});
  await tick();
  await page.locator('[data-persist-detail=execution] > summary').click();
  assert.match(await page.locator('.station-tile').filter({hasText:'LATEST EXECUTION'}).innerText(), /Current attempt execution ID not recorded/);
  state.snapshots.old.station.latest_execution = {attempt_id: 'old-a1', execution_id: 'old-e1'};
  await tick(); await evidence(page, 'UI-06-latest');
  const tile = page.locator('.station-tile').filter({hasText: 'LATEST EXECUTION'});
  assert.match(await tile.innerText(), /old-e1/); assert.doesNotMatch(await tile.innerText(), /old-e0/);
});
test('UI-08: failed fetch marks store disconnected and previous camera/state stale, then recovers', async t => {
  const {page, state, tick} = await fixture(t); state.offline = true; await tick();
  await evidence(page, 'UI-08-offline');
  assert.match(await page.locator('.demo').innerText(), /DISCONNECTED/);
  assert.equal(await page.locator('#attentionbench-clear').getAttribute('data-connection'), 'disconnected');
  assert.match(await page.locator('.view').innerText(), /Stale|older snapshot/);
  state.offline = false; await tick(); assert.equal(await page.locator('#attentionbench-clear').getAttribute('data-connection'), 'connected');
});
test('drafts stay bound to each run/request when user switches runs', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.new = snapshot('new'); state.runs.push({run_id: 'new'}); await tick();
  await page.locator('#ab-hint-content').fill('仅给 old 请求');
  await switchViewingRun(page,'new');
  await page.waitForFunction(() => document.querySelector('#ab-hint-content')?.dataset.draftKey?.includes('new:new-r'));
  assert.equal(await page.locator('#ab-hint-content').inputValue(), '');
  await page.locator('#ab-hint-content').fill('仅给 new 请求');
  await switchViewingRun(page,'old');
  await page.waitForFunction(() => document.querySelector('#ab-hint-content')?.dataset.draftKey?.includes('old:old-r'));
  assert.equal(await page.locator('#ab-hint-content').inputValue(), '仅给 old 请求');
  await page.locator('[data-respond=hint]').click();
  await page.waitForFunction(() => !document.querySelector('[data-respond=hint]'));
  assert.equal(state.posts.at(-1).pathname, '/api/requests/old-r/respond');
  assert.equal(state.posts.at(-1).payload.content, '仅给 old 请求');
});
test('token renewal never retries an ambiguous 500 or loses draft', async t => {
  const {page, state} = await fixture(t);
  let calls = 0;
  await page.route('**/api/requests/*/respond', async route => {calls++; await route.fulfill({status: 500, contentType: 'application/json', body: JSON.stringify({error: 'test ambiguous failure'})});});
  await page.locator('#ab-hint-content').fill('保留草稿'); await page.locator('[data-respond=hint]').click();
  await page.waitForFunction(() => document.querySelector('#ab-live-message')?.textContent.includes('test ambiguous failure'));
  assert.equal(calls, 1); assert.equal(await page.locator('#ab-hint-content').inputValue(), '保留草稿');
});
test('submission stays single while automatic render changes the pending request', async t => {
  const {page, state, tick} = await fixture(t);
  let calls = 0, release;
  const gate = new Promise(resolve => {release = resolve;});
  await page.route('**/api/requests/*/respond', async route => { calls++; await gate; state.snapshots.old.requests[0].state='answered'; await route.fulfill({contentType: 'application/json', body: '{"state":"answered"}'}); });
  await page.locator('#ab-hint-content').fill('单次提交'); await page.locator('[data-respond=hint]').click();
  state.snapshots.old.request_lifecycle['old-r'] = [{event_type:'request.deferred'}]; await tick();
  assert.equal(await page.locator('[data-respond=hint]').isDisabled(), true); assert.equal(calls, 1);
  release(); await page.waitForFunction(() => !document.querySelector('[data-respond=hint]')); assert.equal(calls, 1);
});
test('four evidence layers remain separate with later replay while raw audits stay outside UI', async t => {
  const {page, state, tick} = await fixture(t), snap = state.snapshots.old;
  snap.attempts.push({attempt_id:'old-a1', index:1, status:'succeeded'});
  snap.response_evidence = {'old-r': {submission:{response_id:'human-response'}, association:{status:'linked',attempt_id:'old-a1', execution_id:'old-e1'}, control_comparison:{status:'visible_commands_differ', before:[{operation:'move_delta', arguments:{dx:0.01}}], after:[{operation:'move_delta', arguments:{dx:0.02}}]}, native_outcome:{status:'verified',value:true},causal_adoption:'unconfirmed'}};
  snap.waiting_windows = {'old-r':{started_at:1791300000, ended_at:1791300002, attempt_dispatches:0, sdk_motion_commands:0, robot_actions:null}};
  await tick();
  await showNode(page,'request');
  const panel=await page.locator('[data-adoption-evidence]').innerText();
  assert.match(panel,/Submitted/); assert.match(panel,/Linked to attempt 2/);
  assert.match(panel,/Visible commands changed/); assert.match(panel,/Actual action changes unconfirmed/); assert.match(panel,/Task successful/);
  assert.match(await page.locator('.ab-evidence-limit').innerText(),/caused.*needs verification/);
  assert.doesNotMatch(panel,/human-response|old-a1|Adopted/);
  await page.locator('[data-node-technical] > summary').click();
  assert.match(await page.locator('[data-node-technical]').innerText(),/human-response.*old-a1/s);
  assert.equal(await page.locator('.ab-history-tabs [data-history-select]:not([data-history-select=live])').count(),2);
  assert.equal(await page.locator('[data-secondary=raw],[data-waiting-audit]').count(),0);
  await page.locator('[data-adoption-evidence] summary').last().click(); await evidence(page,'OBS-four-layers');
});
test('UI-01: a launch immediately clears old failed state even while polling is in flight', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.run.status = 'failed'; await tick();
  let release, observed;
  const gate = new Promise(resolve => {release=resolve;});
  const received = new Promise(resolve => {observed=resolve;});
  t.after(() => release());
  await page.route('**/api/runs/old', async route => {observed(); await gate; await route.fulfill({contentType:'application/json',body:JSON.stringify(state.snapshots.old)});});
  const pendingPoll = tick(); await received;
  await page.locator('[data-open-launch]').click(); await selectLaunchTask(page); await page.locator('#ab-launch-form button[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('dialog[aria-label="Configure Attention run"]').open);
  assert.match(await page.locator('.view').innerText(),/Waiting for run registration/);
  assert.doesNotMatch(await page.locator('.view').innerText(),/CURRENT RUN.*old|FAILED/s);
  release(); await pendingPoll;
  await page.waitForFunction(() => document.querySelector('.view').textContent.includes('launch-new'));
});

test('R4-01: clicking defer restores the exact selection and textarea focus', async t => {
  const {page, tick} = await fixture(t);
  await page.locator('#ab-hint-content').fill('请检查夹爪与方块是否对齐，降低抓取高度。');
  await page.locator('#ab-hint-content').evaluate(el => {el.focus();el.setSelectionRange(3,9,'backward');});
  await page.locator('[data-defer-request]').click();
  await page.waitForFunction(() => document.querySelector('[data-defer-request]')?.textContent === 'Deferred');
  await tick(); await evidence(page,'R4-01-defer-selection');
  assert.deepEqual(await page.locator('#ab-hint-content').evaluate(el => [el.selectionStart,el.selectionEnd,el.selectionDirection,el===document.activeElement]),[3,9,'backward',true]);
});
test('R4-03: reconnect clears only the old fetch error with an unchanged business snapshot', async t => {
  const {page,state,tick} = await fixture(t);
  await page.locator('#ab-hint-content').fill('重连期间保留草稿');
  state.offline=true; await tick();
  assert.match(await page.locator('#ab-live-message').innerText(),/Could not read AttentionStore/);
  state.offline=false; await tick(); await evidence(page,'R4-03-reconnected');
  assert.doesNotMatch(await page.locator('#ab-live-message').innerText(),/Could not read AttentionStore|Failed to fetch/);
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'重连期间保留草稿');
  await page.locator('#ab-live-message').evaluate(el=>{el.textContent='提交失败：test business error';});
  await tick(); assert.match(await page.locator('#ab-live-message').innerText(),/test business error/);
});
test('R4-04: persisted budget release amount, reason and reservation are visible', async t => {
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.budget_events=[{sequence:55,event_type:'budget.released',payload:{reservation_id:'request-credit:old-r',credits:1,reason:'run_terminal'}}];
  await tick();
  const panel=page.locator('[data-budget-events]');
  assert.equal(await panel.count(),1);
  assert.equal(await panel.getAttribute('open'),null);
  assert.match(await panel.innerText(),/Run ended.*Assistance credits released: 1/);
  assert.doesNotMatch(await panel.innerText(),/budget\.released|request-credit:old-r|run_terminal/);
  await panel.locator('summary').click();
  assert.match(await panel.innerText(),/Assistance credits released: 1.*55.*budget.released.*request-credit:old-r.*run_terminal/s);
  state.snapshots.old.resources.tokens.used=1;await tick();
  assert.notEqual(await page.locator('[data-budget-events]').getAttribute('open'),null);
  assert.equal(state.posts.length,0);
  await evidence(page,'R4-04-released');
});
test('R4-05: original Chinese, compiled control and scope are exposed independently of causality', async t => {
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.response_uses={'old-r':{guidance_compilation:{status:'translated',rule:'cube_lift_zh_lower_grasp_v1',original:'降低抓取高度，夹紧后确认抓牢',compiled:'AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;close_settle_steps=30;',parameters:{grasp_offset_m:.005,approach_tolerance_m:.004,close_settle_steps:30}}}};
  await tick();
  await showNode(page,'request');
  await page.locator('[data-guidance-compilation] > summary').click();
  const panel=page.locator('[data-guidance-compilation]');
  assert.equal(await panel.count(),1);
  assert.match(await panel.innerText(),/降低抓取高度.*AB_CONTROL_V1.*close_settle_steps.*30/s);
  assert.match(await page.locator('[data-adoption-evidence]').innerText(),/Actual action changes unconfirmed/);
  assert.match(await page.locator('.ab-evidence-limit').innerText(),/caused.*needs verification/);
  await evidence(page,'R4-05-compilation');
});

test('EVAL-01: diagnostic formatting escapes executable markup and preserves the original receipt', async t => {
  const {page,state,tick}=await fixture(t,{styled:true,viewport:{width:390,height:844}});
  const diagnosis='**Earliest observed problem:** Commands **completed**, result `native_success: false`.\n\n**Citations:**\n- `<img src=x onerror="window.__diagnosticExecuted=true">`\n- <script>window.__diagnosticExecuted=true</script> [bad](javascript:alert(1))\n\n```json\n{"value":"**literal** <svg onload=alert(1)>"}\n```\n\n**Caveat:** Evidence is insufficient.';
  state.snapshots.old.eval={status:'completed',native_success:false,run_id:'old',stopped_reason:'advisor_error:ValueError: no valid Advisor JSON',approved_config_sha256:'a'.repeat(64),attempt_ids:['old-a0'],model:'test/model',diagnosis};
  await tick();
  await page.locator('[data-flow-kind=result]').click();
  const panel=page.locator('[data-eval-panel]');assert.equal(await panel.count(),1);
  assert.equal(await panel.locator('[data-eval-details]').getAttribute('open'),null);
  assert.doesNotMatch(await panel.innerText(),/a{64}/);
  await panel.locator('[data-eval-details] summary').click();
  const rendered=panel.locator('[data-eval-diagnosis]');
  assert.equal(await rendered.locator('h4').count(),3);
  assert.equal(await rendered.locator('li').count(),2);
  assert.equal(await rendered.locator('strong').innerText(),'completed');
  assert.match(await rendered.locator('pre').innerText(),/\*\*literal\*\* <svg/);
  assert.equal(await panel.locator('img,script,svg,a').count(),0);
  assert.equal(await page.evaluate(()=>window.__diagnosticExecuted),undefined);
  assert.match(await rendered.innerText(),/Evidence is insufficient/);
  assert.equal(await panel.locator('.ab-eval-source pre').innerText(),diagnosis);
  assert.match(await panel.innerText(),/a{64}/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await evidence(page,'EVAL-01-safe-receipt');
});
test('EVAL-02: completed diagnosis cannot imply task success, and refreshed details stay bound to the run', async t => {
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.eval={status:'completed',native_success:false,stopped_reason:'advisor_error:ValueError: Advisor response is not valid JSON',attempt_ids:['old-a0'],diagnosis:'Diagnostic receipt.'};
  await tick();
  await page.locator('[data-flow-kind=result]').click();
  const panel=page.locator('[data-eval-panel]');assert.equal(await panel.count(),1);
  assert.match(await panel.locator('.panel-title').innerText(),/Diagnostic complete/);
  assert.equal(await panel.locator('.ab-eval-result strong').innerText(),'Task unsuccessful');
  assert.equal(await panel.locator('.ab-eval-stop strong').innerText(),'Invalid Advisor reply format');
  await panel.locator('[data-eval-details] summary').click();
  state.snapshots.old.resources.tokens.used=1;await tick();
  assert.notEqual(await panel.locator('[data-eval-details]').getAttribute('open'),null);
  state.snapshots.old.eval={status:'pending'};await tick();
  assert.equal(await panel.count(),0);
  assert.equal(await page.locator('[data-node-detail]').count(),0);
  state.snapshots.old.eval={status:'failed',native_success:true,error:'Diagnostic unavailable'};await tick();
  await showNode(page,'result');
  assert.match(await panel.locator('.panel-title').innerText(),/Diagnostic failed/);
  assert.equal(await panel.locator('.ab-eval-result strong').innerText(),'Task successful');
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new'});await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-eval-details]').count(),0);
  assert.equal(state.posts.length,0);
});

for (const width of [1280,390]) test(`CASA-02: camera startup, unmeasured preview and saved frame have accurate labels (${width}px)`, async t => {
  const snap = preparingSnapshot();
  snap.run.suite = 'robocasa'; snap.run.task_id = 'counter_to_sink';
  snap.station.camera_url = null;
  snap.attempts = [{attempt_id:'old-a0',index:0,status:'running',status_source:'executor_dispatch'}];
  const {page,state,tick} = await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
  const viewer = page.locator('[data-execution-viewer]');
  assert.match(await viewer.innerText(),/Waiting for the execution camera/);
  assert.doesNotMatch(await viewer.innerText(),/with actual frame age|New human test runs save camera sample replays/);
  assert.equal(await viewer.locator('#ab-camera-frame').count(),0);
  assert.match(await viewer.innerText(),/The live preview will appear when the first frame arrives/);
  const cameraBounds=await viewer.locator('[data-camera-empty]').boundingBox();
  const mediaBounds=await viewer.locator('.ab-viewer-media').boundingBox();
  assert.ok(cameraBounds.x>=mediaBounds.x && cameraBounds.x+cameraBounds.width<=mediaBounds.x+mediaBounds.width+1,'Camera waiting card must fit its media column without hidden clipping');
  await evidence(page,`CASA-02-startup-${width}`);
  snap.station.camera_url = '/camera?run=old'; snap.station.camera_attempt_id = 'old-a0';
  snap.station.latest_execution = {attempt_id:'old-a0',execution_id:'old-e0'};
  await tick();
  assert.equal(await viewer.locator('#ab-camera-frame').getAttribute('alt'),'RoboCasa public RGB camera frame');
  assert.match(await viewer.innerText(),/public RGB preview.*Frame age is unavailable/s);
  assert.doesNotMatch(await viewer.innerText(),/with actual frame age/);
  await page.waitForFunction(()=>document.querySelector('#ab-camera-frame')?.naturalWidth>0);
  assert.equal(await viewer.locator('#ab-camera-frame').getAttribute('data-live-stream-key'),null);
  assert.equal(await viewer.locator('[data-preview-freshness]').getAttribute('data-state'),'unavailable');
  snap.attempts[0].status = 'failed'; snap.requests = snapshot().requests;
  snap.traces = {'old-t0':{attempt_id:'old-a0',execution_id:'old-e0',events:[{sequence:0,operation:'move_to_sink',status:'failed',arguments:{}}]}};
  await tick();
  assert.match(await viewer.innerText(),/saved static frame/);
  assert.equal(await viewer.locator('[data-preview-freshness]').count(),0);
  await page.locator('#ab-hint-content').fill('Keep this RoboCasa reply draft');
  await page.locator('[data-history-select="old-a0"]').first().click();
  assert.match(await viewer.innerText(),/No execution video recorded/);
  assert.match(await viewer.innerText(),/Execution history · Attempt 1/);
  assert.doesNotMatch(await viewer.innerText(),/History replay/);
  assert.equal(await viewer.locator('video').count(),0,'A static frame must never be presented as a replay video');
  assert.match(await viewer.innerText(),/Saved static frame for this attempt · not a video/);
  assert.match(await viewer.innerText(),/move_to_sink/);
  snap.resources.tokens.used = 1; await tick();
  assert.equal(await page.locator('[data-history-select="old-a0"]').first().getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'Keep this RoboCasa reply draft');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);
  await evidence(page,`CASA-02-history-${width}`);
});

for (const viewport of [{width:1280,height:720}, {width:390,height:844}]) {
  test(`CASA-01: live catalog offers RoboCasa with its own seed and execution target (${viewport.width}px)`, async t => {
    const catalog = JSON.parse(fs.readFileSync(path.join(repo, 'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'), 'utf8'));
    const profiles = catalog.profiles.map(profile => ({id:profile.id, suite:profile.suite, task:profile.task,
      execution_target:profile.execution_target, seed:JSON.parse(fs.readFileSync(profile.config, 'utf8')).seed,
      robot:JSON.parse(fs.readFileSync(profile.config, 'utf8')).robot || {model:'Franka',execution_arm:'single'}}));
    const {page, state} = await fixture(t, {styled:true, viewport, beforeNavigate:async(_,s) => {
      s.launchOptions = {profiles, attention_policies:['autonomous','reactive_help'], assistance_modes:['benchmark_proxy','live_human_first']};
    }});
    await page.locator('#ab-hint-content').fill('Keep my existing reply draft');
    await page.getByRole('button', {name:'New run', exact:true}).click();
    const form = page.locator('#ab-launch-form');
    await form.locator('select[name=benchmark]').selectOption('robocasa');
    const expectedCasa=profiles.filter(p=>p.suite==='robocasa' && p.task==='counter_to_sink');
    const casa = form.locator('select[name=profile_id] option').filter({hasText:'Move to the sink'});
    assert.equal(await casa.count(), new Set(expectedCasa.map(p=>p.task)).size, 'Robot variants share one task entry');
    const franka=expectedCasa.find(p=>p.robot.model==='Franka');
    assert.ok(franka,'The approved original Franka task must remain available');
    const originalCasa=form.locator(`select[name=profile_id] option[value="${franka.id}"]`);
    assert.equal(await originalCasa.count(),1);
    const profileId = await originalCasa.getAttribute('value');
    await form.locator('select[name=profile_id]').selectOption(profileId);
    assert.equal(await form.locator('[name=seed]').inputValue(), '101');
    assert.equal(await form.locator('[name=execution_target]').inputValue(), 'robocasa_sim');
    const lift = profiles.find(profile => profile.suite === 'robosuite');
    await form.locator('select[name=benchmark]').selectOption('robosuite');
    await form.locator('select[name=profile_id]').selectOption(lift.id);
    assert.equal(await form.locator('[name=execution_target]').inputValue(), 'robosuite_sim');
    await form.locator('select[name=benchmark]').selectOption('robocasa');
    await form.locator('select[name=profile_id]').selectOption(profileId);
    assert.equal(await page.locator('#ab-hint-content').inputValue(), 'Keep my existing reply draft');
    assert.equal(state.posts.length, 0, 'Choosing a task must not launch it');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await evidence(page, `CASA-01-launch-${viewport.width}`);
    await form.getByRole('button', {name:'Start human test',exact:true}).click();
    assert.equal(state.posts.length, 1);
    assert.equal(state.posts[0].pathname, '/api/runs/start');
    assert.equal(state.posts[0].payload.profile_id, profileId);
    assert.equal(state.posts[0].payload.seed, 101);
    assert.equal(state.posts[0].payload.execution_target, 'robocasa_sim');
    assert.equal(state.posts[0].payload.assistance_mode, 'live_human_first');
  });

  test(`new run configuration stays in the initial viewport with a failed old run (${viewport.width}px)`, async t => {
    const {page, state, tick} = await fixture(t, {styled:true, viewport});
    state.snapshots.old.run.status = 'failed'; await tick();
    const launch = page.getByRole('button', {name:'New run', exact:true});
    assert.equal(await launch.count(), 1);
    assert.equal(await launch.isEnabled(), true);
    const bounds = await launch.boundingBox();
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= viewport.height,
      `Configuration entry is outside the first viewport: ${JSON.stringify(bounds)}`);
    assert.equal(await page.evaluate(() => window.scrollY), 0);
    await launch.click();
    const dialog = page.getByRole('dialog', {name:'Configure Attention run'});
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await page.locator('#ab-launch-form select[name=profile_id]').inputValue(), '');
    assert.equal(await page.getByLabel('Task',{exact:true}).isVisible(),false);
    await selectLaunchTask(page);
    assert.equal(await page.locator('#ab-launch-form select[name=profile_id]').inputValue(), 'lift');
    await page.getByRole('button', {name:'Cancel',exact:true}).click();
    assert.equal(await dialog.isVisible(), false);
    assert.equal(state.snapshots.old.run.status, 'failed');
    assert.equal(state.posts.length, 0);
    await evidence(page, `launch-entry-${viewport.width}`);
  });
}

test('CLARITY-01: terminal zero-budget run states ended and no human participation', async t => {
  const {page, state, tick} = await fixture(t, {styled:true});
  const snap = state.snapshots.old;
  snap.run.status = 'failed'; snap.requests = []; snap.responses = {};
  snap.resources.assistance = {limit:0, used:0, reserved:0, remaining:0};
  snap.attempts = [0,1,2].map(index => ({attempt_id:`old-a${index}`, index, status:'failed', started_at:1791327226+index*3, ended_at:1791327228+index*3}));
  snap.eval = {status:'completed', native_success:false};
  state.launches = [{run_id:'old', launch_id:'launch-old', state:'completed', locked:{max_attempts:3, assistance_credits:0}}];
  await tick();
  const hero = page.locator('[data-run-overview]');
  assert.match(await hero.innerText(), /Run ended.*Task unsuccessful/s);
  assert.match(await hero.innerText(), /Assistance budget is 0/);
  assert.match(await hero.innerText(), /The human reply stage has not started yet/);
  assert.match(await hero.innerText(), /3 \/ 3/);
  assert.equal(await hero.locator('[data-human-stage]').getAttribute('data-phase'), 'skipped');
  assert.equal(await page.locator('[data-answer-current]').count(), 0);
  const bounds = await hero.boundingBox(); assert.ok(bounds.y+100 < 720);
  assert.doesNotMatch(await page.locator('[data-agent-role=developer]').innerText(), /Attempt unsuccessful/);
  assert.match(await hero.innerText(), /Unsuccessful/);
  await evidence(page,'CLARITY-01-ended');
});
test('CLARITY-02: pending human request is prominent and reply action focuses its own input', async t => {
  const {page, state, tick} = await fixture(t, {styled:true});
  const hero = page.locator('[data-run-overview]');
  assert.match(await hero.innerText(), /Awaiting your reply/);
  assert.match(await hero.innerText(), /Reply deadline:/);
  assert.equal(await hero.locator('[data-human-stage]').getAttribute('data-phase'), 'active');
  await page.locator('[data-answer-current]').click();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'ab-hint-content');
  assert.match(await page.locator('#ab-hint-content').getAttribute('data-draft-key'), /old:old-r/);
  assert.equal(state.posts.length,0);
});
test('CLARITY-03: running attempts are distinguished from waiting for a person', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.requests = []; state.snapshots.old.attempts[0].status='running'; await tick();
  assert.match(await page.locator('[data-run-overview]').innerText(), /Execution in progress.*Attempt 1/s);
  assert.doesNotMatch(await page.locator('[data-run-overview]').innerText(), /Awaiting your reply/);
  assert.equal(await page.locator('[data-answer-current]').count(),0);
});
test('CLARITY-04: terminal transition leaves a persistent notice until dismissed', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.run.status='failed'; state.snapshots.old.requests=[]; await tick();
  const notice=page.locator('#ab-run-notice');
  assert.equal(await notice.isVisible(),true);
  assert.match(await notice.innerText(), /Run ended/);
  await tick(); assert.equal(await notice.isVisible(),true);
  await page.getByRole('button',{name:'Close run notification',exact:true}).click();
  assert.equal(await notice.isVisible(),false);
  await tick(); assert.equal(await notice.isVisible(),false);
});
test('CLARITY-05: terminal takes priority over stale pending request, success remains evidence-based', async t => {
  const {page, state, tick} = await fixture(t);
  state.snapshots.old.run.status='completed'; state.snapshots.old.eval={status:'pending'}; await tick();
  const hero=page.locator('[data-run-overview]');
  assert.match(await hero.innerText(),/Run ended.*Native outcome needs verification/s);
  assert.doesNotMatch(await hero.innerText(),/Task successful|Awaiting your reply/);
  assert.equal(await page.locator('[data-answer-current]').count(),0);
  state.snapshots.old.eval.native_success=true; await tick();
  assert.match(await hero.innerText(),/Task successful/);
  assert.match(await hero.innerText(),/No human replies submitted yet/);
});
test('CLARITY-06: human-test defaults and zero-credit preview are visible before submission', async t => {
  const {page,state}=await fixture(t);
  await page.locator('[data-open-launch]').click();
  const form=page.locator('#ab-launch-form');
  assert.equal(await form.locator('[name=attention_policy]').inputValue(),'reactive_help');
  assert.equal(await form.locator('[name=assistance_mode]').inputValue(),'live_human_first');
  assert.equal(await form.locator('[name=max_attempts]').inputValue(),'10');
  assert.equal(await form.locator('[name=assistance_credits]').inputValue(),'9');
  assert.equal(await form.locator('[name=human_deadline_seconds]').inputValue(),'600');
  await form.locator('[name=assistance_credits]').fill('0');
  assert.match(await form.locator('[data-launch-preview]').innerText(), /Assistance budget is 0.*human reply waits/s);
  assert.match(await form.locator('button[type=submit]').innerText(),/no human reply waits/);
  assert.equal(state.posts.length,0);
  await form.locator('[name=assistance_credits]').fill('2');
  assert.match(await form.locator('[data-launch-preview]').innerText(), /help request.*600 s/s);
  await form.locator('[name=attention_policy]').selectOption('autonomous');
  assert.match(await form.locator('[data-launch-preview]').innerText(), /does not request help/);
  await form.locator('[name=attention_policy]').selectOption('reactive_help');
  await form.locator('[name=assistance_mode]').selectOption('benchmark_proxy');
  assert.match(await form.locator('[data-launch-preview]').innerText(), /automatic.*no human reply waits/s);
});
test('CLARITY-07: proxy replies and later run switching never imply a human has replied', async t => {
  const {page,state,tick}=await fixture(t);
  const snap=state.snapshots.old;
  snap.requests[0].state='fallback'; snap.responses={'proxy-r':{responder:'advisor_proxy'}};
  snap.run.status='failed'; await tick();
  assert.match(await page.locator('[data-run-overview]').innerText(), /No human replies submitted yet/);
  state.snapshots.new=snapshot('new'); state.runs.push({run_id:'new'}); await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.textContent.includes('Awaiting your reply'));
  assert.equal(await page.locator('#ab-run-notice').isVisible(),false);
  assert.match(await page.locator('[data-run-overview]').getAttribute('data-run-overview'),/new/);
});

test('CLARITY-08: a new run that ends before first poll still announces completion', async t => {
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.run.status='failed'; state.snapshots.old.requests=[]; await tick();
  await page.locator('[data-open-launch]').click();
  await selectLaunchTask(page);
  await page.locator('#ab-launch-form button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('.view')?.textContent.includes('Waiting for run registration'));
  assert.equal(state.posts.at(-1).payload.assistance_credits,9);
  assert.equal(state.posts.at(-1).payload.max_attempts,10);
  assert.equal(state.posts.at(-1).payload.human_deadline_seconds,600);
  assert.equal(state.posts.at(-1).payload.assistance_mode,'live_human_first');
  state.snapshots.new=snapshot('new','completed');
  state.snapshots.new.requests=[]; state.snapshots.new.attempts[0].status='succeeded';
  state.snapshots.new.eval={status:'completed',native_success:true};
  state.runs.unshift({run_id:'new'}); state.launches[0].run_id='new';
  await tick();
  assert.match(await page.locator('[data-run-overview]').innerText(),/Run ended.*Task successful/s);
  assert.match(await page.locator('[data-run-overview]').innerText(),/The human reply stage has not started yet/);
  assert.equal(await page.locator('#ab-run-notice').isVisible(),true);
  assert.match(await page.locator('#ab-run-notice').innerText(),/Run ended/);
  assert.equal(await page.locator('[data-answer-current]').count(),0);
  await evidence(page,'CLARITY-08-fast-end');
});

test('HISTORY-01: attempts without any request have independent history and arguments', async t => {
  const {page,state,tick}=await fixture(t), snap=state.snapshots.old;
  snap.requests=[]; snap.traces['old-t0'].events[0].arguments={position:[0.1,0.2,0.3]};
  await tick();
  await page.locator('[data-history-select="old-a0"]').first().click();
  const panel=page.locator('[data-execution-viewer]');
  assert.match(await panel.innerText(),/Attempt 1|Attempt 1/);
  assert.match(await panel.innerText(),/move_to_pose/);
  await panel.locator('[data-history-event]').first().locator('summary').click();
  assert.match(await panel.innerText(),/0.1/);
  assert.match(await panel.innerText(),/No execution video recorded/);
  assert.equal(state.posts.length,0);
});
test('HISTORY-02: selected past attempt survives new attempt, refresh and reload; run switch isolates it', async t => {
  const {page,state,tick}=await fixture(t);
  await page.locator('[data-history-select="old-a0"]').first().click();
  state.snapshots.old.attempts.push({attempt_id:'old-a1',index:1,status:'running'});
  await tick(); assert.equal(await page.locator('[data-history-select="old-a0"]').first().getAttribute('aria-pressed'),'true');
  await page.reload(); await page.locator('[data-history-select="old-a0"]').first().waitFor();
  assert.equal(await page.locator('[data-history-select="old-a0"]').first().getAttribute('aria-pressed'),'true');
  state.snapshots.new=snapshot('new'); state.runs.push({run_id:'new'}); await tick();
  await switchViewingRun(page,'new');
  await page.locator('[data-history-select="new-a0"]').first().waitFor();
  assert.equal(await page.locator('[data-history-select="live"]').getAttribute('aria-pressed'),'true');
  assert.equal(state.posts.length,0);
});
test('HISTORY-03: genuine video retains playhead after snapshot update and cannot imply native success', async t => {
  const {page,state,tick}=await fixture(t), snap=state.snapshots.old;
  snap.requests=[];
  snap.traces['old-t0'].evidence=[{kind:'advisor_replay',mime_type:'video/mp4',media_url:'/replay.mp4'}];
  await tick(); await page.locator('[data-history-select="old-a0"]').click();
  const video=page.locator('[data-execution-viewer] video'); assert.equal(await video.count(),1);
  await video.evaluate(v=>{window.__oldVideo=v;});
  snap.resources.tokens={limit:10,used:1,remaining:9}; await tick();
  assert.equal(await video.evaluate(v=>v===window.__oldVideo),true);
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/Native outcome awaiting verification/);
  snap.eval.native_outcomes={'old-a0':{evaluated:true,native_success:false}}; await tick();
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/Native task unsuccessful/);
});
test('HISTORY-04: waiting camera shows archive and source dimensions, not moving RUNNING', async t => {
  const {page}=await fixture(t);
  const panel=page.locator('[data-execution-viewer]');
  assert.match(await panel.innerText(),/Awaiting human reply/);
  assert.match(await panel.innerText(),/Saved frame|Saved frame/);
  assert.doesNotMatch(await panel.locator('.panel-title').innerText(),/RUNNING/);
  await page.waitForFunction(()=>document.querySelector('[data-camera-resolution]')?.textContent.includes('1 × 1'));
});
for(const viewport of [{width:1280,height:720},{width:390,height:844}]) test(`HISTORY-05: viewer has no horizontal overflow at ${viewport.width}`, async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,viewport});
  state.snapshots.old.traces['old-t0'].events[0].arguments={long:'x'.repeat(500)};
  await tick(); await page.locator('[data-history-select="old-a0"]').first().click();
  await page.locator('[data-history-event] summary').first().click();
  assert.equal(await page.locator('[data-execution-viewer]').evaluate(e=>e.scrollWidth<=e.clientWidth+1),true);
});

test('HISTORY-06: partial recording and source dimensions remain explicit without success claim', async t=>{
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.traces['old-t0'].recording={source_width:1920,source_height:1080,frame_count:50,sampled_seconds:10,truncated:true,error:null};
  await tick();await page.locator('[data-history-select="old-a0"]').first().click();
  await page.locator('[data-persist-detail=execution] > summary').click();
  assert.match(await page.locator('[data-recording-summary]').innerText(),/1920×1080/);
  assert.match(await page.locator('[data-recording-summary]').innerText(),/later frames were not recorded/);
  await page.locator('[data-history-event] summary').first().click();
  state.snapshots.old.resources.tokens={limit:10,used:2,remaining:8};await tick();
  assert.equal(await page.locator('[data-history-event]').first().getAttribute('open'),'');
});

test('HISTORY-07: cached camera preview is shown only for its own attempt',async t=>{
  const {page,state,tick}=await fixture(t);
  state.snapshots.old.station.camera_attempt_id='old-a0';await tick();
  await page.locator('[data-history-select="old-a0"]').first().click();
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/Saved static frame for this attempt · not a video/);
  await page.waitForFunction(()=>document.querySelector('[data-media-resolution]')?.textContent.includes('1 × 1'));
  assert.equal(await page.locator('[data-history-image]').evaluate(i=>i.getBoundingClientRect().width<=256),true);
  state.snapshots.old.attempts.push({attempt_id:'old-a1',index:1,status:'running'});
  state.snapshots.old.station.camera_attempt_id='old-a1';await tick();
  assert.equal(await page.locator('[data-execution-viewer] img').count(),0);
});


function preparingSnapshot(id = 'old') {
  const snap = snapshot(id);
  snap.attempts = []; snap.requests = []; snap.traces = {};
  snap.station.latest_execution = null;
  snap.station.camera_attempt_id = null;
  return snap;
}
test('EMPTY-01: a new run with null latest_execution and no attempts renders on its first poll', async t => {
  const {page,state,tick} = await fixture(t, {initialSnapshot:preparingSnapshot()});
  await evidence(page,'EMPTY-01-first-poll');
  const text = await page.locator('.view').innerText();
  assert.doesNotMatch(text,/Could not load run data|Could not read AttentionStore|null.*execution_id/);
  assert.equal(await page.locator('#ab-live-run').count(),1);
  await page.locator('[data-persist-detail=execution] > summary').click();
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/Current attempt execution ID not recorded/);
  assert.equal(await page.locator('#ab-emergency-interrupt').isEnabled(),true);
  state.snapshots.old.attempts = [{attempt_id:'old-a0',index:0,status:'running'}];
  state.snapshots.old.station.latest_execution = {attempt_id:'old-a0',execution_id:'old-e0'};
  await tick();
  assert.match(await page.locator('.station-tile').filter({hasText:'LATEST EXECUTION'}).innerText(),/old-e0/);
  assert.equal(state.posts.length,0);
});
test('EMPTY-02: switching to a preparing run clears the previous execution, then renders its own trace', async t => {
  const {page,state,tick} = await fixture(t);
  state.snapshots.new = preparingSnapshot('new'); state.runs.push({run_id:'new'}); await tick();
  await switchViewingRun(page,'new');
  await page.waitForFunction(() => document.querySelector('[data-execution-viewer]')?.textContent.includes('Current attempt execution ID not recorded') || document.querySelector('#ab-live-message')?.dataset.readError);
  await evidence(page,'EMPTY-02-switch');
  assert.doesNotMatch(await page.locator('.view').innerText(),/Could not read AttentionStore|old-e0/);
  assert.equal(await page.locator('[data-execution-viewer]').count(),1);
  state.snapshots.new.attempts = [{attempt_id:'new-a0',index:0,status:'failed'}];
  state.snapshots.new.traces = {'new-t0':{attempt_id:'new-a0',execution_id:'new-e0',events:[]}};
  await tick();
  await page.locator('[data-persist-detail=execution] > summary').click();
  assert.match(await page.locator('.station-tile').filter({hasText:'LATEST EXECUTION'}).innerText(),/new-e0/);
});
test('EMPTY-03: an execution without an attempt binding is never shown as the latest execution', async t => {
  const snap=preparingSnapshot();
  snap.station.latest_execution = {execution_id:'unbound-stale-execution'};
  snap.traces = {unbound:{execution_id:'unbound-stale-trace',events:[]}};
  const {page} = await fixture(t,{initialSnapshot:snap});
  await evidence(page,'EMPTY-03-unbound');
  await page.locator('[data-persist-detail=execution] > summary').click();
  const tile = await page.locator('.station-tile').filter({hasText:'LATEST EXECUTION'}).innerText();
  assert.match(tile,/Current attempt execution ID not recorded/);
  assert.doesNotMatch(tile,/unbound-stale/);
});
test('EMPTY-04: a transient render failure retries an unchanged snapshot on the next refresh', async t => {
  const {page,state,tick} = await fixture(t);
  await page.evaluate(() => {
    const view = document.querySelector('.view');
    const setter = Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').set;
    let failOnce = true;
    Object.defineProperty(view,'innerHTML',{set(value){
      if(failOnce){failOnce=false;throw new Error('test transient DOM failure');}
      setter.call(this,value);
    },get(){return Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML').get.call(this);},configurable:true});
  });
  state.snapshots.old.attempts[0].status='timed_out'; await tick();
  assert.match(await page.locator('#ab-live-message').innerText(),/Run data loaded, but rendering failed.*test transient DOM failure/);
  assert.equal(await page.locator('#attentionbench-clear').getAttribute('data-connection'),'connected');
  await tick();
  assert.match(await page.locator('.ab-history-tabs [data-history-select="old-a0"]').innerText(),/timed out|timed_out/);
  assert.doesNotMatch(await page.locator('#ab-live-message').innerText(),/test transient DOM failure/);
});

test('ERROR-01: terminal overview exposes the latest bound execution error without using an older failure', async t => {
  const {page,state,tick} = await fixture(t);
  const snap=state.snapshots.old;
  snap.run.status='failed'; snap.requests=[]; snap.eval.native_success=false;
  snap.traces['old-t0'].failure={error_type:'MemoryError',message:'MemoryError: '};
  await tick();
  assert.match(await page.locator('[data-run-overview]').innerText(),/Execution error: MemoryError/);
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'failed'});
  snap.traces['old-t1']={attempt_id:'old-a1',events:[],failure:{error_type:'TimeoutError',message:'TimeoutError: camera read'}};
  await tick();
  const overview=await page.locator('[data-run-overview]').innerText();
  assert.match(overview,/Execution error: TimeoutError: camera read/);
  assert.doesNotMatch(overview,/MemoryError/);
  await evidence(page,'ERROR-01-latest-failure');
});

function livePreviewSnapshot() {
  const snap=preparingSnapshot();
  snap.station.camera_attempt_id='old-a0';
  snap.station.camera_preview_url='/preview-frame?run=old&attempt=old-a0';
  snap.station.camera_state_url='/preview-state?run=old&attempt=old-a0';
  return snap;
}
async function previewRoutes(page, value) {
  await page.route('**/preview-frame?**',route=>route.fulfill({contentType:'image/png',headers:{'X-Frame-Sequence':'3','X-Frame-Age':String(value.age_seconds ?? 0.3)},body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Y9sAAAAASUVORK5CYII=', 'base64')}));
  await page.route('**/preview-state?**',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)}));
}
test('LIVE-01: active attempt uses continuous preview with stable image across snapshot updates',async t=>{
  const {page,state,tick}=await fixture(t,{initialSnapshot:livePreviewSnapshot()});
  await previewRoutes(page,{state:'live',age_seconds:0.3,source_width:1920,source_height:1080,sequence:3});
  await page.reload();await page.locator('#ab-live-run').waitFor({state:'attached'});
  const img=page.locator('#ab-camera-frame');
  assert.match(await img.getAttribute('data-preview-url'),/preview-frame/);
  await img.waitFor({state:'visible'});
  await img.evaluate(el=>{window.__previewImage=el;});
  state.snapshots.old.resources.tokens={limit:10,used:1,remaining:9};await tick();
  assert.equal(await img.evaluate(el=>el===window.__previewImage),true);
  await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/frame received about.*s/);
  assert.match(await page.locator('[data-run-overview]').innerText(),/Execution in progress.*Attempt 1/s);
  await evidence(page,'LIVE-01-active');
});
test('LIVE-02: stale or interrupted source cannot be labelled as fresh live video',async t=>{
  const {page}=await fixture(t,{initialSnapshot:livePreviewSnapshot()});
  const meta={state:'stale',age_seconds:8.5,source_width:1920,source_height:1080};
  await previewRoutes(page,meta);await page.reload();await page.locator('#ab-live-run').waitFor({state:'attached'});
  await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/not updated/);
  meta.state='ended';await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/has stopped|saved/);
  await page.route('**/preview-state?**',route=>route.abort('connectionrefused'));
  await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/disconnected|unavailable/);
  await evidence(page,'LIVE-02-stale');
});
test('LIVE-03: switching to history or human waiting releases the previous stream',async t=>{
  const {page,state,tick}=await fixture(t,{initialSnapshot:livePreviewSnapshot()});
  await previewRoutes(page,{state:'live',age_seconds:0.2});await page.reload();await page.locator('#ab-live-run').waitFor({state:'attached'});
  await page.locator('#ab-camera-frame').evaluate(el=>{window.__previousStream=el;});
  state.snapshots.old=snapshot();state.snapshots.old.station.camera_attempt_id='old-a0';await tick();
  assert.doesNotMatch(await page.locator('#ab-camera-frame').getAttribute('src'),/live-stream/);
  assert.equal(await page.evaluate(()=>window.__previousStream.getAttribute('src')),'');
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/Saved frame/);
});

test('LIVE-04: slow frame downloads self-pace and never overlap, even during snapshot renders',async t=>{
  let active=0,maximum=0,received=0;
  // Install the slow transport before the first navigation. A reload can leave
  // an aborted old-document route sleeping and count it as a new-page download.
  const {page,state,tick}=await fixture(t,{initialSnapshot:livePreviewSnapshot(),beforeNavigate:async page=>{
    await previewRoutes(page,{state:'live',age_seconds:.1});
    await page.route('**/preview-frame?**',async route=>{
      active++;maximum=Math.max(maximum,active);received++;
      await new Promise(resolve=>setTimeout(resolve,350));
      try {await route.fulfill({contentType:'image/png',headers:{'X-Frame-Sequence':String(received),'X-Frame-Age':'0.1'},
        body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Y9sAAAAASUVORK5CYII=', 'base64')});}
      catch (_) {} finally {active--;}
    });
  }});
  await page.locator('#ab-live-run').waitFor({state:'attached'});
  await page.waitForTimeout(450);
  const image=page.locator('#ab-camera-frame');
  await image.evaluate(el=>{window.__slowFrame=el;});
  state.snapshots.old.resources.tokens={limit:10,used:3,remaining:7};await tick();
  await page.waitForTimeout(450);
  assert.equal(maximum,1);assert.ok(received>=2);
  assert.equal(await image.evaluate(el=>el===window.__slowFrame),true);
  assert.match(await image.getAttribute('src'),/^blob:/);
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/frame received about|Frame not updated/);
});
test('LIVE-05: camera transport failure keeps the last good frame, then automatically recovers',async t=>{
  const {page}=await fixture(t,{initialSnapshot:livePreviewSnapshot()});
  await previewRoutes(page,{state:'live',age_seconds:.1});
  await page.reload();await page.locator('#ab-live-run').waitFor({state:'attached'});
  await page.waitForTimeout(250);
  const before=await page.locator('#ab-camera-frame').getAttribute('src');
  assert.match(before,/^blob:/);
  await page.route('**/preview-frame?**',route=>route.abort('connectionrefused'));
  await page.waitForTimeout(300);await page.evaluate(()=>window.__cameraTick());
  assert.equal(await page.locator('#ab-camera-frame').getAttribute('src'),before);
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/Preview disconnected/);
  await previewRoutes(page,{state:'live',age_seconds:.1});
  await page.waitForTimeout(300);await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/frame received about/);
});

function serveTestReplay(route) {
  const range=route.request().headers().range;
  const match=range?.match(/^bytes=(\d+)-(\d*)$/);
  if (!match) return route.fulfill({contentType:'video/mp4',headers:{'Accept-Ranges':'bytes'},body:replayBytes});
  const start=Number(match[1]), end=Math.min(match[2] ? Number(match[2]) : replayBytes.length-1,replayBytes.length-1);
  return route.fulfill({status:206,contentType:'video/mp4',
    headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${replayBytes.length}`},
    body:replayBytes.subarray(start,end+1)});
}
function timelineSnapshot(id='old') {
  const snap=snapshot(id), trace=snap.traces[id+'-t0'];
  snap.requests=[];
  trace.evidence=[{kind:'advisor_replay',mime_type:'video/mp4',media_url:'/replay.mp4?attempt='+id+'-a0'}];
  trace.events=[{operation:'run_policy',status:'completed',timestamp:0,timestamp_basis:'unknown'},
    {operation:'move_to_position',status:'completed',timestamp:1.3,timestamp_basis:'relative',arguments:{x:.2}},
    {operation:'close',status:'completed',timestamp:2.2,timestamp_basis:'relative'},
    {operation:'get_observation',status:'completed',timestamp:6,timestamp_basis:'relative'}];
  trace.recording={replay_timing:{basis:'relative',event_clock:'sdk_completion',
    frame_trace_seconds:[-.1,1,2,3,3.5],frame_video_seconds:[0,.5,1.2,2,3]}};
  return snap;
}
test('SEEK-01: a trace click pauses genuine video at the mapped sample, and survives refresh',async t=>{
  const {page,state,tick}=await fixture(t,{initialSnapshot:timelineSnapshot()});
  await page.locator('[data-history-select="old-a0"]').click();
  const video=page.locator('[data-history-replay]');
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.readyState>=1);
  await video.evaluate(v=>v.play());
  await page.locator('summary[data-replay-event="1"] [data-replay-link]').click();
  await page.waitForFunction(()=>document.querySelector('[data-replay-status]')?.textContent.startsWith('Located'));
  assert.ok(Math.abs(await video.evaluate(v=>v.currentTime)-.5)<.01, JSON.stringify(await video.evaluate(v=>({time:v.currentTime,duration:v.duration,ready:v.readyState,seekable:Array.from({length:v.seekable.length},(_,i)=>[v.seekable.start(i),v.seekable.end(i)]),status:document.querySelector('[data-replay-status]').textContent}))));
  assert.equal(await video.evaluate(v=>v.paused),true);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),1);
  assert.match(await page.locator('[data-replay-status]').innerText(),/Synchronized sample frame/);
  await page.locator('summary[data-replay-event="1"]').click();
  assert.match(await page.locator('[data-history-event][open]').innerText(),/0.2/);
  state.snapshots.old.resources.tokens.used=1;await tick();
  await page.waitForFunction(()=>document.querySelector('[data-replay-status]')?.textContent.startsWith('Located'));
  assert.ok(Math.abs(await video.evaluate(v=>v.currentTime)-.5)<.01, JSON.stringify(await video.evaluate(v=>({time:v.currentTime,duration:v.duration,ready:v.readyState,seekable:Array.from({length:v.seekable.length},(_,i)=>[v.seekable.start(i),v.seekable.end(i)]),status:document.querySelector('[data-replay-status]').textContent}))));
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),1);
  assert.equal(state.posts.length,0);
});
test('SEEK-02: unknown clocks and events outside synchronized samples never seek',async t=>{
  const {page}=await fixture(t,{initialSnapshot:timelineSnapshot()});
  await page.locator('[data-history-select="old-a0"]').click();
  assert.equal(await page.locator('summary[data-replay-event="0"] [data-replay-link]').count(),0);
  assert.equal(await page.locator('summary[data-replay-event="3"] [data-replay-link]').count(),0);
  await page.locator('summary[data-replay-event="0"]').click();
  assert.equal(await page.locator('[data-history-replay]').evaluate(v=>v.currentTime),0);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),0);
});
test('SEEK-03: legacy video says approximate, keyboard seeks, and beyond-duration click is refused',async t=>{
  const snap=timelineSnapshot();delete snap.traces['old-t0'].recording.replay_timing;
  const {page}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-history-select="old-a0"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.readyState>=1);
  await page.locator('summary[data-replay-event="1"] [data-replay-link]').press('Enter');
  await page.waitForFunction(()=>document.querySelector('[data-replay-status]')?.textContent.startsWith('Located'));
  assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-1.3)<.01);
  assert.match(await page.locator('[data-replay-status]').innerText(),/Approximate position/);
  await page.locator('summary[data-replay-event="3"] [data-replay-link]').click();
  assert.match(await page.locator('[data-replay-status]').innerText(),/outside/);
  assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-1.3)<.01);
});
test('SEEK-04: last click wins before metadata and selection remains scoped to its attempt',async t=>{
  const snap=timelineSnapshot();
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'failed'});
  snap.traces['old-t1']={...snap.traces['old-t0'],trace_id:'old-t1',attempt_id:'old-a1',
    evidence:[{kind:'advisor_replay',mime_type:'video/mp4',media_url:'/replay.mp4?attempt=old-a1'}]};
  const {page}=await fixture(t,{initialSnapshot:snap});
  let release;
  const blocked=new Promise(resolve=>release=resolve);
  await page.route('**/replay.mp4?attempt=old-a0',async route=>{await blocked;await serveTestReplay(route);});
  await page.locator('[data-history-select="old-a0"]').click();
  await page.locator('summary[data-replay-event="1"] [data-replay-link]').click();
  await page.locator('summary[data-replay-event="2"] [data-replay-link]').click();
  assert.match(await page.locator('[data-replay-status]').innerText(),/Loading/);
  release();
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.currentTime>1);
  assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-1.2)<.01);
  await page.locator('[data-history-select="old-a1"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.readyState>=1);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),0);
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
});
test('SEEK-05: a request trace link selects its own attempt before seeking',async t=>{
  const snap=timelineSnapshot();snap.requests=snapshot().requests;
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  if(await page.locator('[data-request-technical]').getAttribute('open')===null)await page.locator('[data-request-technical]>summary').click();
  await page.locator('[data-request-replay="1"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.currentTime>.4);
  assert.equal(await page.locator('[data-history-select="old-a0"]').first().getAttribute('aria-pressed'),'true');
  assert.ok(Math.abs(await page.locator('video').evaluate(v=>v.currentTime)-.5)<.01);
  assert.equal(state.posts.length,0);
});
test('SEEK-06: switching attempts while an older video loads cannot seek the new video',async t=>{
  const snap=timelineSnapshot();
  snap.attempts.push({attempt_id:'old-a1',index:1,status:'failed'});
  snap.traces['old-t1']={...snap.traces['old-t0'],trace_id:'old-t1',attempt_id:'old-a1',
    evidence:[{kind:'advisor_replay',mime_type:'video/mp4',media_url:'/replay.mp4?attempt=old-a1'}]};
  const {page}=await fixture(t,{initialSnapshot:snap});
  let release,delivered;
  const blocked=new Promise(resolve=>release=resolve), served=new Promise(resolve=>delivered=resolve);
  await page.route('**/replay.mp4?attempt=old-a0',async route=>{await blocked;await serveTestReplay(route);delivered();});
  await page.locator('[data-history-select="old-a0"]').click();
  await page.locator('summary[data-replay-event="1"] [data-replay-link]').click();
  assert.match(await page.locator('[data-replay-status]').innerText(),/Loading/);
  await page.locator('[data-history-select="old-a1"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.readyState>=1);
  release();await served;
  assert.equal(await page.locator('video').evaluate(v=>v.currentTime),0);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),0);
});

for (const width of [1280,390]) test(`BIND-01: current approved task renders exactly one node and keeps execution/native state independent ${width}`, async t=>{
  const crypto=require('node:crypto');
  const graphPath=path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/robosuite_cube_lift.json');
  const bytes=fs.readFileSync(graphPath), graph=JSON.parse(bytes), entry=graph.entries[0];
  const revision=crypto.createHash('sha256').update(bytes).digest('hex');
  const snap=flowSnapshot();snap.requests=[];snap.responses={};snap.response_evidence={};
  function binding(aid,execution,goal){return {status:'bound',run_id:'old',attempt_id:aid,dag:{
    graph_id:graph.graph_id,revision,root_skill:entry.name,nodes:[{node_id:entry.name,name:entry.name,label:entry.display_name,
      description:entry.description,development_status:entry.status,dependencies:entry.dependencies,
      execution_status:execution,goal_status:goal,event_ids:[]}]}};}
  snap.attempt_dags={'old-a0':binding('old-a0','executed','failed'),'old-a1':binding('old-a1','running','unconfirmed')};
  const {page,tick,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:844}});
  assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a0]').click();
  assert.equal(await page.locator('[data-skill-node]').count(),graph.entries.length);
  assert.equal(await page.locator('[data-skill-edge]').count(),0);
  assert.equal(await page.locator('[data-skill-node]').getAttribute('data-runtime'),'executed');
  assert.match(await page.locator('[data-skill-state=development]').innerText(),/Development.*Complete/);
  assert.match(await page.locator('[data-skill-state=execution]').innerText(),/Execution.*Executed/);
  assert.match(await page.locator('[data-skill-state=goal]').innerText(),/Native task.*Unsuccessful/);
  assert.match(await page.locator('[data-skill-node]').innerText(),/Goal not achieved/);
  assert.doesNotMatch(await page.locator('[data-attempt-dag]').innerText(),/Locate|Grasp succeeded/);
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-dag-revision'),revision);
  await tick();assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-attempt-id'),'old-a0');
  await page.locator('[data-close-attempt-dag]').click();
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').click();
  assert.equal(await page.locator('[data-skill-node]').getAttribute('data-runtime'),'running');
  assert.match(await page.locator('[data-skill-node]').innerText(),/Running/);
  assert.doesNotMatch(await page.locator('[data-skill-node]').innerText(),/Goal achieved/);
  await page.reload();assert.equal(await page.locator('[data-attempt-dag]').count(),0);
  await page.locator('[data-flow-kind=attempt][data-entity-id=old-a1]').click();
  assert.equal(await page.locator('[data-attempt-dag]').getAttribute('data-attempt-id'),'old-a1');
  assert.equal(state.posts.length,0);await evidence(page,`BIND-current-one-node-${width}`);
});

function requestClaritySnapshot() {
  const snap=timelineSnapshot();
  snap.run.status='running';snap.requests=snapshot().requests;
  snap.requests[0].reason='request_after_each_failure';
  snap.traces['old-t0'].hypothesis='unknown';
  snap.traces['old-t0'].memory_refs=['memory:private-debug-id'];
  return snap;
}
for(const width of [1280,390])test(`REQ-01: request panel uses concise English and keeps codes folded at ${width}`,async t=>{
  const snap=requestClaritySnapshot();
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  const panel=page.locator('[data-core-region=request]');
  await panel.scrollIntoViewIfNeeded();
  if(process.env.UI_EVIDENCE_DIR) {
    await page.setViewportSize({width,height:2600});await page.evaluate(()=>scrollTo(0,0));
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await panel.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`REQ-panel-${width}.png`)});
    await page.setViewportSize({width,height:900});
  }
  const visible=await panel.innerText();
  assert.doesNotMatch(visible,/request_after_each_failure|AB_CONTROL_V1|memory:private-debug-id|old-r|old-a0|find_objects|REQUEST EVENT|PRIORITY|unknown/);
  assert.match(visible,/Previous attempt unsuccessful/);
  assert.match(visible,/Reply time remaining:.*Reply deadline:/s);
  assert.equal(await panel.locator('[data-respond=hint]').innerText(),'Submit guidance');
  assert.equal(await panel.locator('[data-hint-advanced]').getAttribute('open'),null);
  assert.equal(await panel.locator('[data-request-technical]').getAttribute('open'),null);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);
});
test('REQ-02: unknown codes stay neutral and custom reasons remain safely readable',async t=>{
  const snap=requestClaritySnapshot();snap.requests[0].reason='new_unmapped_signal_9';
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  const reason=page.locator('[data-request-reason]');
  assert.equal(await reason.innerText(),'Your decision is needed');
  assert.doesNotMatch(await reason.innerText(),/failure|Unsuccessful|Safety/);
  await page.locator('[data-request-technical]>summary').click();
  assert.match(await page.locator('[data-request-technical]').innerText(),/new_unmapped_signal_9/);
  snap.requests[0].reason='夹爪没有抓稳，请先检查位置 <img src=x onerror=alert(1)>';
  snap.traces['old-t0'].hypothesis='夹爪可能偏高';await tick();
  assert.equal(await reason.innerText(),snap.requests[0].reason);
  assert.match(await page.locator('[data-request-hypothesis]').innerText(),/Current hypothesis.*夹爪可能偏高/s);
  assert.equal(await page.locator('.request-card img').count(),0);
  assert.equal(state.posts.length,0);
});
test('REQ-03: folded evidence keeps focus and draft across polling and seeks its bound replay',async t=>{
  const snap=requestClaritySnapshot();
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  await page.locator('#ab-hint-content').fill('调整抓取位置');
  await page.locator('#ab-hint-content').evaluate(e=>{e.focus();e.setSelectionRange(1,4,'backward');});
  snap.resources.tokens.used=1;await tick();
  assert.deepEqual(await page.locator('#ab-hint-content').evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e.selectionDirection,e===document.activeElement]),['调整抓取位置',1,4,'backward',true]);
  await page.locator('[data-request-technical]>summary').click();
  snap.resources.tokens.used=2;await tick();
  assert.notEqual(await page.locator('[data-request-technical]').getAttribute('open'),null);
  assert.equal(await page.locator('[data-request-technical]>summary').evaluate(e=>e===document.activeElement),true);
  assert.match(await page.locator('[data-request-technical]').innerText(),/request_after_each_failure.*old-r.*old-a0/s);
  if(await page.locator('[data-request-technical]').getAttribute('open')===null)await page.locator('[data-request-technical]>summary').click();
  await page.locator('[data-request-replay="1"]').click();
  const video=page.locator('[data-history-replay]');await video.waitFor();
  await page.waitForFunction(()=>Math.abs(document.querySelector('[data-history-replay]')?.currentTime-.5)<.08);
  assert.equal(await page.locator('[data-execution-viewer]').getAttribute('data-replay-attempt'),'old-a0');
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'调整抓取位置');
  assert.equal(await page.locator('[data-core-region=request] [data-request-replay]').count(),0);
  assert.equal(state.posts.length,0);
});
test('REQ-04: request history shows attempt/type/state, without leaking IDs or shifting the active draft',async t=>{
  const snap=requestClaritySnapshot();
  snap.requests.push({...snap.requests[0],request_id:'archive-request-code',state:'cancelled',priority:'normal',created_at:1});
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-request-history]>summary').click();
  const row=page.locator('[data-live-request=archive-request-code]');
  assert.match(await row.innerText(),/Attempt 1.*Help request.*Cancelled/s);
  assert.doesNotMatch(await row.innerText(),/request_after|archive-request-code|old-a0|PRIORITY|DEADLINE|REQUEST EVENT|cancelled/);
  await page.locator('#ab-hint-content').fill('属于当前请求');await row.click();
  assert.match(await page.locator('#ab-hint-content').getAttribute('data-draft-key'),/old:old-r/);
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'属于当前请求');
  assert.equal(await page.locator('[data-request-technical]').count(),2);
  assert.equal(state.posts.length,0);
});
test('REQ-05: approval and interrupt retain clear actions and urgent priority',async t=>{
  const snap=requestClaritySnapshot();snap.requests[0].request_type='approval';snap.requests[0].reason='irreversible_action_requires_approval';snap.requests[0].priority='high';
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  assert.match(await page.locator('[data-core-region=request]').innerText(),/Your approval is required/);
  assert.equal(await page.locator('[data-respond=approve]').count(),1);assert.equal(await page.locator('[data-respond=deny]').count(),1);
  assert.equal(await page.locator('#ab-hint-content').count(),0);
  snap.requests[0].request_type='interrupt';snap.requests[0].reason='trace_safety_interrupt';snap.requests[0].priority='critical';await tick();
  assert.match(await page.locator('.request-card').innerText(),/Safety check requested an interrupt.*confirm whether to interrupt/s);
  assert.match(await page.locator('[data-request-priority]').innerText(),/Urgent/);
  assert.equal(await page.locator('[data-respond=interrupt]').count(),1);
  assert.equal(state.posts.length,0);
});
test('REQ-06: advanced formats stay task-scoped and folded, preset only fills the draft',async t=>{
  const snap=requestClaritySnapshot();const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  const summary=page.locator('[data-hint-advanced]>summary');
  assert.match(await page.locator('[data-hint-format]').innerText(),/Free-text.*subsequent execution/);
  await page.locator('[data-hint-preset=en]').click();assert.equal(state.posts.length,0);
  await summary.click();assert.match(await page.locator('[data-hint-advanced]').innerText(),/AB_CONTROL_V1.*0–0\.04/s);
  snap.resources.tokens.used=1;await tick();
  assert.notEqual(await page.locator('[data-hint-advanced]').getAttribute('open'),null);
  assert.equal(await summary.evaluate(e=>e===document.activeElement),true);
  await page.locator('[data-hint-preset=control]').click();assert.match(await page.locator('#ab-hint-content').inputValue(),/^AB_CONTROL_V1/);
  assert.equal(state.posts.length,0);
  snap.run.suite='robocasa';snap.run.task_id='counter_to_sink';await tick();
  assert.equal(await page.locator('[data-hint-preset=en]').count(),0);
  assert.match(await page.locator('[data-hint-advanced]').innerText(),/0\.030–0\.050/);
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new'});await tick();await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-hint-advanced]').getAttribute('open'),null);
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`HINT-01: recorded human hint is visible in the request panel without opening feedback ${width}`,async t=>{
  const snap=flowSnapshot({linked:false});snap.responses['reply-1'].content='先降低抓取高度，再夹紧并确认抓牢。';
  const {page,state}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  const panel=page.locator('[data-core-region=request]');
  if(process.env.UI_EVIDENCE_DIR){await page.setViewportSize({width,height:2400});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await panel.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`HINT-default-${width}.png`)});await page.setViewportSize({width,height:900});}
  const reply=panel.locator('[data-request-reply]');
  assert.equal(await reply.count(),1);
  assert.match(await reply.innerText(),/Your guidance.*Attempt 1.*先降低抓取高度，再夹紧并确认抓牢。/s);
  assert.doesNotMatch(await reply.innerText(),/old-r|old-a0|reply-1|Adopted|ADOPTED/);
  assert.equal(await page.locator('[data-node-detail]').count(),0);
  assert.equal(await page.locator('[data-flow-detail-dialog]').isVisible(),false);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(state.posts.length,0);
});
test('HINT-02: history shows its own hint while current pending request keeps its own draft',async t=>{
  const snap=snapshot();
  snap.requests.push({...snap.requests[0],request_id:'older-hint',response_id:'older-reply',state:'answered',created_at:1});
  snap.responses={'older-reply':{response_id:'older-reply',responder:'human',content:'这是上一次的建议，不属于当前待答请求'}};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-request-reply]').count(),0);
  await page.locator('#ab-hint-content').fill('当前求助的草稿');
  await page.locator('[data-request-history]>summary').click();await page.locator('[data-live-request=older-hint]').click();
  assert.equal(await page.locator('[data-request-reply]').getAttribute('data-request-reply'),'older-hint');
  assert.match(await page.locator('[data-request-history]').innerText(),/这是上一次的建议/);
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'当前求助的草稿');
  assert.match(await page.locator('#ab-hint-content').getAttribute('data-draft-key'),/old:old-r/);
  snap.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('[data-request-reply]').count(),1);assert.equal(state.posts.length,0);
});
test('HINT-03: system hint is readable, safely escaped and never called a human hint',async t=>{
  const snap=flowSnapshot({proxy:true,linked:false});
  snap.responses['reply-1'].content=JSON.stringify({guidance:'夹爪靠近方块再闭合 <img src=x onerror=alert(1)>',diagnosis:'抓取位置可能偏高'});
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  const reply=page.locator('[data-request-reply]');
  assert.match(await reply.innerText(),/System guidance.*夹爪靠近方块再闭合/s);
  assert.doesNotMatch(await reply.innerText(),/Your guidance|"(?:guidance|diagnosis)"\s*:|"\{/);
  assert.equal(await reply.locator('img').count(),0);
  await reply.locator('summary').click();assert.match(await reply.innerText(),/抓取位置可能偏高/);
  snap.responses['reply-1'].responder='unknown-provider';await tick();
  assert.match(await reply.innerText(),/source unconfirmed/);assert.doesNotMatch(await reply.innerText(),/Your guidance|System guidance/);
  assert.equal(state.posts.length,0);
});
test('HINT-04: long hint expands in place, preserves focus on polling and clears across runs',async t=>{
  const snap=flowSnapshot({linked:false});snap.responses['reply-1'].content='这是逐步调整抓取位置的建议。'.repeat(30);
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true});
  const reply=page.locator('[data-request-reply]'),summary=reply.locator('summary');
  assert.ok((await reply.locator('[data-hint-preview]').innerText()).length<160);
  assert.equal(await reply.locator('[data-hint-full]').getAttribute('open'),null);
  await summary.click();assert.equal(await reply.locator('[data-hint-body]').innerText(),snap.responses['reply-1'].content);
  snap.resources.tokens.used=1;await tick();
  assert.notEqual(await reply.locator('[data-hint-full]').getAttribute('open'),null);
  assert.equal(await summary.evaluate(e=>e===document.activeElement),true);
  state.snapshots.new=snapshot('new');state.runs.push({run_id:'new'});await tick();await switchViewingRun(page,'new');
  await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.runOverview==='new');
  assert.equal(await page.locator('[data-request-reply]').count(),0);
  assert.equal(state.posts.length,0);
});
test('HINT-05: absent stored content is explicit and a cancelled request cannot inherit another hint',async t=>{
  const snap=flowSnapshot({linked:false});delete snap.responses['reply-1'];
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  assert.equal(await page.locator('[data-request-reply]').count(),0);
  assert.match(await page.locator('[data-hint-content-gap]').innerText(),/No guidance content available/);
  snap.responses['reply-1']={response_id:'reply-1',responder:'human',content:'只属于回答过的请求'};
  snap.requests.push({...snap.requests[0],request_id:'cancelled-other',response_id:null,state:'cancelled',created_at:2});await tick();
  await page.locator('[data-request-history]>summary').click();await page.locator('[data-live-request=cancelled-other]').click();
  assert.equal(await page.locator('[data-request-history] [data-request-reply]').count(),0);
  assert.equal(await page.locator('[data-core-region=request]>[data-request-reply]').getAttribute('data-request-reply'),'old-r');
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`HIST-HINT-01: answered history uses its hint instead of the generic failure heading ${width}`,async t=>{
  const snap=snapshot();snap.requests[0].reason='request_after_each_failure';
  snap.requests.push({...snap.requests[0],request_id:'past-request',response_id:'past-response',state:'answered',created_at:1});
  snap.responses={'past-response':{response_id:'past-response',responder:'human',content:'夹爪对准方块中心后，再缓慢向下移动。'}};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  await page.locator('[data-request-history]>summary').click();await page.locator('[data-live-request=past-request]').click();
  const history=page.locator('[data-request-history]');
  if(process.env.UI_EVIDENCE_DIR){await page.setViewportSize({width,height:2600});await page.evaluate(()=>scrollTo(0,0));await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await history.screenshot({path:path.join(process.env.UI_EVIDENCE_DIR,`HIST-hint-${width}.png`)});await page.setViewportSize({width,height:900});}
  const row=page.locator('[data-live-request=past-request]');
  assert.match(await row.innerText(),/Answered.*Your guidance.*夹爪对准方块中心/s);
  assert.doesNotMatch(await history.innerText(),/Previous attempt unsuccessful|request_after_each_failure/);
  assert.match(await history.locator('.ab-request-context').innerText(),/Your guidance.*Answered.*夹爪对准方块中心/s);
  assert.equal(await history.locator('.ab-request-context>[data-request-reply]').count(),1);
  assert.match(await page.locator('[data-core-region=request]>.ab-request-context [data-request-reason]').innerText(),/Previous attempt unsuccessful/);
  await page.locator('#ab-hint-content').fill('当前请求的草稿');snap.resources.tokens.used=1;await tick();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'当前请求的草稿');
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(state.posts.length,0);
});
test('HIST-HINT-02: idle history shows a matching preview without duplicating the full reply card',async t=>{
  const snap=flowSnapshot({proxy:true,linked:false});snap.requests[0].reason='request_after_each_failure';
  snap.responses['reply-1'].content=JSON.stringify({guidance:'把夹爪移到方块正上方再抓取。',diagnosis:'位置可能偏移'});
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-request-history]>summary').click();await page.locator('[data-live-request=old-r]').click();
  assert.equal(await page.locator('[data-request-reply]').count(),1);
  assert.match(await page.locator('[data-request-history]').innerText(),/System guidance.*把夹爪移到方块正上方/s);
  assert.doesNotMatch(await page.locator('[data-request-history]').innerText(),/Previous attempt unsuccessful|"(?:guidance|diagnosis)"\s*:/);
  await page.locator('[data-request-history] [data-request-technical]>summary').click();
  assert.match(await page.locator('[data-request-history] [data-request-technical]').innerText(),/request_after_each_failure/);
  assert.equal(state.posts.length,0);
});
test('HIST-HINT-03: answered request with no recorded hint keeps its reason and cannot borrow another reply',async t=>{
  const snap=flowSnapshot({linked:false});snap.requests[0].reason='request_after_each_failure';delete snap.responses['reply-1'];
  snap.responses.unrelated={response_id:'unrelated',responder:'human',content:'另一条回复的内容'};
  const {page,state}=await fixture(t,{initialSnapshot:snap});
  await page.locator('[data-request-history]>summary').click();
  assert.match(await page.locator('[data-request-history]').innerText(),/Previous attempt unsuccessful/);
  assert.doesNotMatch(await page.locator('[data-request-history]').innerText(),/另一条回复的内容/);
  assert.equal(await page.locator('[data-request-reply]').count(),0);
  assert.equal(state.posts.length,0);
});

for (const width of [1280,390]) test(`DECISION-01: successful no-failure trace never becomes an error (${width}px)`,async t=>{
  const snap=snapshot('old','completed');snap.requests=[];snap.attempts[0].status='succeeded';
  snap.eval={status:'failed',native_success:true};
  snap.traces['old-t0'].failure={stage:'none',error_type:'none',message:'No failed event in the authorized terminal execution trace.'};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Task successful/);
  assert.equal(await page.locator('[data-overview-extra]').count(),0);
  await page.locator('[data-history-select=old-a0]').first().click();
  assert.doesNotMatch(await page.locator('.ab-trace-meta summary').innerText(),/unsuccessful/i);
  assert.equal(await page.locator('.ab-history-failure').count(),0);
  assert.match(await page.locator('[data-diagnostic-note]').innerText(),/diagnosis|diagnostic/i);
  assert.match(await page.locator('[data-diagnostic-note]').innerText(),/native task outcome.*separately/i);
  await evidence(page,`DECISION-success-${width}`);
});

test('DECISION-02: genuine execution errors remain visible; native failure alone is not an execution error',async t=>{
  const snap=snapshot('old','failed');snap.requests=[];snap.eval={native_success:false};
  snap.traces['old-t0'].failure={stage:'none',error_type:'none',message:'No failed event in the authorized terminal execution trace.'};
  const {page,tick}=await fixture(t,{initialSnapshot:snap});
  assert.match(await page.locator('[data-core-region=status]').innerText(),/Task unsuccessful/);
  assert.equal(await page.locator('[data-overview-extra]').count(),0);
  snap.traces['old-t0'].failure={stage:'unknown',error_type:'TaskOutcomeFailure',message:'Execution completed, but the goal was not verified'};await tick();
  assert.match(await page.locator('[data-overview-extra]').innerText(),/^Attempt result:/);
  assert.doesNotMatch(await page.locator('[data-overview-extra]').innerText(),/Execution error/);
  snap.traces['old-t0'].failure={stage:'execution',error_type:'RuntimeError',message:'Simulator command failed'};await tick();
  assert.match(await page.locator('[data-overview-extra]').innerText(),/Execution error: Simulator command failed/);
});

for(const [phase,expected] of [
  ['running',/Guidance opens when.*help request/i],
  ['zero',/Assistance budget is 0/i],
  ['terminal',/run has ended.*New run/i],
  ['automatic',/Automatic Advisor assistance.*Human replies.*disabled/i],
  ['stopping',/Stop requested.*awaiting executor confirmation/i],
])test(`DECISION-03: idle Hint guidance explains ${phase} without adding a reply action`,async t=>{
  const snap=snapshot();snap.requests=[];
  if(phase==='zero')snap.resources.assistance={limit:0,used:0,reserved:0,remaining:0};
  if(phase==='terminal')snap.run.status='failed';
  if(phase==='automatic')snap.run.assistance_mode='benchmark_proxy';
  if(phase==='stopping')snap.station.interrupt={state:'requested'};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width:390,height:844}});
  assert.match(await page.locator('[data-idle-guidance]').innerText(),expected);
  assert.equal(await page.locator('[data-respond]').count(),0);
  assert.ok(await page.locator('[data-core-region=request]').evaluate(e=>e.getBoundingClientRect().height)<170);
});

test('DECISION-04: ended timeout reports its actual stop reason; live expiry still awaits runtime',async t=>{
  const snap=snapshot('old','failed');snap.requests[0].state='cancelled';snap.requests[0].deadline_at=Date.now()/1000-30;
  snap.request_lifecycle['old-r']=[{event_type:'request.timeout'}];
  snap.eval={status:'failed',native_success:false,stopped_reason:'advisor_error:ParccError: PARCC credential unavailable; set PARCC_API_KEY or LITELLM_KEY in the harness process'};
  const {page,tick}=await fixture(t,{initialSnapshot:snap});
  assert.match(await page.locator('[data-run-stop-reason]').innerText(),/Advisor credentials unavailable/);
  assert.match(await page.locator('[data-timeout-warning]').innerText(),/run has ended/i);
  assert.doesNotMatch(await page.locator('[data-timeout-warning]').innerText(),/decides fallback|awaiting runtime/);
  snap.run.status='running';snap.requests[0].state='pending';await tick();
  assert.match(await page.locator('[data-timeout-warning]').innerText(),/awaiting runtime handling/);
  assert.equal(await page.locator('[data-run-stop-reason]').count(),0);
});

test('DECISION-05: format preview is read-only and preserves original draft, selection and focus through polling',async t=>{
  const {page,state,tick}=await fixture(t);state.previewResult={status:'supported',parameters:{grasp_offset_m:0.005}};
  const input=page.locator('#ab-hint-content'),text='AB_CONTROL_V1 grasp_offset_m=0.005;';
  await input.fill(text);await input.evaluate(e=>{e.focus();e.setSelectionRange(3,9,'backward');});
  await page.waitForFunction(()=>document.querySelector('[data-guidance-preview]')?.textContent.includes('grasp_offset_m=0.005'));
  assert.match(await page.locator('[data-guidance-preview]').innerText(),/Recognized parameters.*does not confirm action changes/s);
  await evidence(page,'DECISION-guidance-preview');
  assert.equal(state.posts.length,0);assert.equal(state.previewPosts.at(-1).payload.content,text);
  state.snapshots.old.resources.tokens.used=2;await tick();
  assert.deepEqual(await input.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e.selectionDirection,e===document.activeElement]),[text,3,9,'backward',true]);
  state.previewResult={status:'unrecognized',parameters:{}};await input.fill('请想办法完成任务');
  await page.waitForFunction(()=>document.querySelector('[data-guidance-preview]')?.textContent.includes('No parameter rule matched'));
  assert.equal(await input.inputValue(),'请想办法完成任务');assert.equal(state.posts.length,0);
});

test('DECISION-06: delayed previews cannot overwrite a newer draft or a different run',{timeout:6000},async t=>{
  const {page,state}=await fixture(t);let release,started;
  const waiting=new Promise(resolve=>release=resolve),issued=new Promise(resolve=>started=resolve);
  await page.route('**/guidance-preview',async route=>{
    const content=route.request().postDataJSON().content;
    if(content==='older draft'){started();await waiting;}
    await route.fulfill({contentType:'application/json',body:JSON.stringify({request_id:'old-r',run_id:'old',status:'supported',parameters:{grasp_offset_m:content==='older draft'?0.04:0.005}})}).catch(()=>{});
  });
  await page.locator('#ab-hint-content').fill('older draft');
  let deadline;
  try {await Promise.race([issued,new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('No format preview request issued')),1800);})]);}
  finally {clearTimeout(deadline);}
  await page.locator('#ab-hint-content').fill('newer draft');
  await page.waitForFunction(()=>document.querySelector('[data-guidance-preview]')?.textContent.includes('grasp_offset_m=0.005'));
  release();await page.waitForTimeout(30);
  assert.doesNotMatch(await page.locator('[data-guidance-preview]').innerText(),/0.04/);
  state.runs.push({run_id:'new',task_id:'cube_lift'});state.snapshots.new=snapshot('new');await page.evaluate(()=>window.__tick());
  await switchViewingRun(page,'new');
  await page.locator('[data-run-overview=new]').waitFor();
  assert.equal(await page.locator('#ab-hint-content').inputValue(),'');
  assert.match(await page.locator('[data-guidance-preview]').innerText(),/Enter guidance/);
});

test('DECISION-07: preview outage is explicit, reconnect recovers and expiry closes the preview',async t=>{
  const {page,state,tick}=await fixture(t);const input=page.locator('#ab-hint-content');
  await input.fill('preserve my draft');state.offline=true;await tick();
  assert.match(await page.locator('[data-guidance-preview]').innerText(),/unavailable.*Disconnected/i);
  assert.equal(await input.inputValue(),'preserve my draft');
  state.offline=false;await tick();
  await page.waitForFunction(()=>document.querySelector('[data-guidance-preview]')?.textContent.includes('No parameter rule matched'));
  state.snapshots.old.requests[0].deadline_at=Date.now()/1000-5;await tick();
  assert.equal(await page.locator('[data-guidance-preview]').count(),0);
});

function openarmOptions(_,state) {
  state.launchOptions={profiles:[
    {id:'sink-franka',suite:'robocasa',task:'counter_to_sink',seed:101,execution_target:'robocasa_sim',robot:{model:'Franka',execution_arm:'single'}},
    {id:'sink-openarm-left',suite:'robocasa',task:'counter_to_sink',seed:101,execution_target:'robocasa_sim',robot:{model:'OpenArm',execution_arm:'left'}},
    {id:'sink-openarm-right',suite:'robocasa',task:'counter_to_sink',seed:101,execution_target:'robocasa_sim',robot:{model:'OpenArm',execution_arm:'right'}},
    {id:'sink365',suite:'robocasa365',task:'pick_place_counter_to_sink',seed:101,execution_target:'robocasa365_sim'}
  ],attention_policies:['autonomous','reactive_help'],assistance_modes:['live_human_first']};
}
for(const width of [1280,390])test(`OPENARM-UI-01: robot and side choose one approved startup config at ${width}px`,async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:openarmOptions});
  const draft=page.locator('#ab-hint-content');await draft.fill('Keep the selected grasp hint');await draft.evaluate(e=>e.setSelectionRange(5,13));
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page,'robocasa','sink-franka');
  const robot=page.getByLabel('Robot',{exact:true}),arm=page.getByLabel('Execution arm',{exact:true});
  assert.deepEqual(await robot.locator('option').evaluateAll(es=>es.map(e=>e.value)),['Franka','OpenArm']);
  assert.equal(await arm.inputValue(),'single');
  await robot.selectOption('OpenArm');assert.equal(await arm.inputValue(),'left');
  await arm.selectOption('right');await arm.focus();await tick();
  assert.equal(await arm.evaluate(e=>e===document.activeElement),true);
  assert.deepEqual(await draft.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd]),['Keep the selected grasp hint',5,13]);
  assert.match(await page.locator('[data-launch-preview]').innerText(),/OpenArm.*right.*locked/i);
  assert.equal(await page.locator('.ab-launch-body').evaluate(e=>e.scrollWidth<=e.clientWidth),true);
  await evidence(page,`OPENARM-startup-${width}`);
  await page.locator('#ab-launch-form button[type=submit]').click();
  assert.equal(state.posts.length,1);
  assert.equal(state.posts[0].payload.profile_id,'sink-openarm-right');
  assert.equal(state.posts[0].payload.robot_model,'OpenArm');assert.equal(state.posts[0].payload.execution_arm,'right');
});

test('OPENARM-UI-02: recorded model and arm are locked in the run header; missing history is explicit',async t=>{
  const snap=snapshot();snap.run.suite='robocasa';snap.run.task_id='counter_to_sink';snap.run.execution_target='robocasa_sim';snap.run.robot={model:'OpenArm',execution_arm:'left'};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
  const settings=page.locator('[data-run-settings]');
  assert.match(await settings.innerText(),/Robot\s+OpenArm/);assert.match(await settings.innerText(),/Execution mode\s+Left only/);
  assert.equal(await settings.locator('select').count(),0);assert.match(await settings.innerText(),/Configuration locked/);
  delete state.snapshots.old.run.robot;await tick();
  assert.match(await settings.innerText(),/Robot\s+Not recorded/);
  assert.doesNotMatch(await settings.innerText(),/Franka|OpenArm/);
});

test('OPENARM-UI-03: other suites have no OpenArm selector and no stale robot payload',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:openarmOptions});
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page,'robocasa','sink-franka');
  await page.getByLabel('Robot',{exact:true}).selectOption('OpenArm');
  await selectLaunchTask(page,'robocasa365','sink365');
  assert.equal(await page.getByLabel('Robot',{exact:true}).isVisible(),false);
  await page.locator('#ab-launch-form button[type=submit]').click();
  assert.equal(state.posts[0].payload.robot_model,undefined);assert.equal(state.posts[0].payload.execution_arm,undefined);
});

// Native-size fixture pixels verify responsive RoboCasa viewing, independently
// of metadata. The product must retain the producer image and attempt identity.
for(const width of [1280,390])test(`ROBOCASA-HD-01: native 1080p preview fits ${width}px and retains stale/end semantics`,async t=>{
  const frame=execFileSync('ffmpeg',['-loglevel','error','-f','lavfi','-i','color=c=gray:s=1920x1080','-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1']);
  const snap=livePreviewSnapshot();snap.run.suite='robocasa';snap.run.execution_target='robocasa_sim';
  const meta={state:'live',age_seconds:.3,source_width:1920,source_height:1080,sequence:3};
  const {page}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:async page=>{
    await page.route('**/preview-frame?**',route=>route.fulfill({contentType:'image/png',body:frame}));
    await page.route('**/preview-state?**',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(meta)}));
  }});
  const image=page.locator('#ab-camera-frame');
  await page.waitForFunction(()=>document.querySelector('#ab-camera-frame')?.naturalWidth===1920);
  assert.equal(await image.evaluate(el=>el.naturalHeight),1080);
  assert.ok(await image.evaluate(el=>el.getBoundingClientRect().right<=innerWidth));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  meta.state='stale';meta.age_seconds=8;await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/not updated/);
  meta.state='ended';await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/has stopped|saved/);
  await evidence(page,`ROBOCASA-HD-preview-${width}`);
});

test('OPENARM-UI-04: hint format and unsupported preview follow the recorded robot while retaining the draft',async t=>{
  const snap=snapshot();snap.run.suite='robocasa';snap.run.task_id='counter_to_sink';snap.run.robot={model:'OpenArm',execution_arm:'left'};
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap});state.previewResult={status:'unsupported',parameters:{}};
  await page.locator('[data-hint-advanced] summary').click();
  const format=await page.locator('[data-hint-advanced]').innerText();
  assert.match(format,/grasp_offset_m=0.005;/);assert.doesNotMatch(format,/base_forward_m|close_settle_steps=|0.030–0.050/);
  const input=page.locator('#ab-hint-content'),text='AB_CONTROL_V1 base_forward_m=0.11;';
  await input.fill(text);await input.evaluate(e=>{e.focus();e.setSelectionRange(3,9,'backward');});
  await page.waitForFunction(()=>document.querySelector('[data-guidance-preview]')?.textContent.includes('Unsupported controls for OpenArm'));
  await tick();assert.deepEqual(await input.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e.selectionDirection,e===document.activeElement]),[text,3,9,'backward',true]);
  assert.equal(state.posts.length,0);await evidence(page,'OPENARM-hint-capabilities');
});

function mobileReplaySnapshot() {
  const snap=timelineSnapshot();snap.requests=snapshot().requests;
  snap.run.suite='robocasa';snap.run.task_id='counter_to_sink';snap.run.robot={model:'OpenArm',execution_arm:'left'};
  const trace=snap.traces['old-t0'];
  trace.evidence=['external','chest','wrist'].map(view=>({kind:view==='external'?'advisor_replay':`advisor_replay_${view}`,mime_type:'video/mp4',media_url:`/replay.mp4?view=${view}`,evidence_id:view}));
  trace.recording.views=Object.fromEntries(['external','chest','wrist'].map(view=>[view,{name:view==='external'?'External overview':view==='chest'?'Chest first person':'Left wrist',source_width:1920,source_height:1080,video_saved:true}]));
  return snap;
}
for(const width of [1280,390])test(`MOBILE-VIEW-01: camera switch keeps replay time, playback, timeline, draft and history at ${width}px`,async t=>{
  const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},initialSnapshot:mobileReplaySnapshot()});
  page.setDefaultTimeout(5000);
  await page.locator('[data-history-select="old-a0"]').first().click();
  const video=page.locator('[data-history-replay]');
  await page.waitForFunction(()=>document.querySelector('[data-history-replay]')?.readyState>=1);
  await page.locator('summary[data-replay-event="1"] [data-replay-link]').click();
  await page.waitForFunction(()=>document.querySelector('[data-replay-status]')?.textContent.startsWith('Located'));
  const input=page.locator('#ab-hint-content');await input.fill('Keep this independent draft');
  await input.evaluate(e=>{e.focus();e.setSelectionRange(5,12);});
  await page.locator('[data-camera-view="chest"]').evaluate(e=>e.click());
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1&&document.querySelector('video').currentTime>.49);
  assert.ok(Math.abs(await video.evaluate(e=>e.currentTime)-.5)<.01);
  assert.equal(await video.evaluate(e=>e.paused),true);
  assert.equal(await input.inputValue(),'Keep this independent draft');
  assert.deepEqual(await input.evaluate(e=>[document.activeElement===e,e.selectionStart,e.selectionEnd]),[true,5,12]);
  assert.equal(await page.locator('[data-replay-link][aria-current=true]').count(),1);
  assert.equal(await page.locator('[data-history-select="old-a0"]').first().getAttribute('aria-pressed'),'true');
  await video.evaluate(e=>e.play());
  await page.locator('[data-camera-view="wrist"]').evaluate(e=>e.click());
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1&&!document.querySelector('video').paused);
  await tick();assert.equal(await page.locator('[data-camera-view="wrist"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('video').count(),1);
  assert.equal(state.posts.length,0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
});
test('MOBILE-VIEW-02: a missing camera recording is explicit and never falls back to another view',async t=>{
  const snap=mobileReplaySnapshot();snap.traces['old-t0'].evidence=snap.traces['old-t0'].evidence.filter(e=>e.evidence_id!=='wrist');
  snap.traces['old-t0'].recording.views.wrist.video_saved=false;
  const {page}=await fixture(t,{initialSnapshot:snap});
  page.setDefaultTimeout(5000);
  await page.locator('[data-history-select="old-a0"]').first().click();
  await page.locator('[data-camera-view="wrist"]').click();
  assert.match(await page.locator('[data-execution-viewer]').innerText(),/No recording for Left wrist/);
  assert.equal(await page.locator('video').count(),0);
});

for(const width of [1280,390])test(`MOBILE-LIVE-01: three native camera routes, source clock, disconnect and recovery at ${width}px`,async t=>{
  const frame=execFileSync('ffmpeg',['-loglevel','error','-f','lavfi','-i','color=c=gray:s=1920x1080','-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1']);
  const snap=livePreviewSnapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'left'};
  snap.station.camera_views=Object.fromEntries(['external','chest','wrist'].map(key=>[key,{name:key==='external'?'External overview':key==='chest'?'Chest first person':'Left wrist',width:1920,height:1080,preview_url:`/preview-frame?run=old&attempt=old-a0&view=${key}`} ]));
  const meta={state:'live',age_seconds:.1,source_width:1920,source_height:1080,sequence:1,timestamp_basis:'physical_snapshot_wall_clock'},requests=[];
  const {page,state,tick}=await fixture(t,{initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:async page=>{
    await page.route('**/preview-frame?**',route=>{requests.push(route.request().url());return route.fulfill({contentType:'image/png',body:frame,headers:{'X-Frame-Age':'.1','X-Frame-Sequence':'1'}});});
    await page.route('**/preview-state?**',route=>route.fulfill({contentType:'application/json',body:JSON.stringify(meta)}));
  }});
  for(const key of ['external','chest','wrist']) {
    await page.locator(`[data-camera-view=${key}]`).click();
    await page.waitForFunction(()=>document.querySelector('#ab-camera-frame')?.naturalWidth===1920);
    assert.match(await page.locator('#ab-camera-frame').getAttribute('data-preview-url'),new RegExp(`view=${key}`));
    assert.match(await page.locator('.ab-camera-caption a').getAttribute('href'),new RegExp(`view=${key}`));
    await tick();assert.equal(await page.locator(`[data-camera-view=${key}]`).getAttribute('aria-pressed'),'true');
  }
  assert.ok(requests.some(url=>url.includes('view=chest'))&&requests.some(url=>url.includes('view=wrist')));
  await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/captured/);
  assert.match(await page.locator('[data-preview-freshness]').getAttribute('title'),/physical state capture/);
  meta.state='offline';await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/disconnected/);
  meta.state='live';await page.evaluate(()=>window.__cameraTick());
  assert.match(await page.locator('[data-preview-freshness]').innerText(),/Live preview/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(state.posts.length,0);
});

for(const width of [1280,390])test(`MOBILE-DEFAULT-01: production catalog submits accepted mobile OpenArm and retains Franka at ${width}px`,async t=>{
  const catalog=JSON.parse(fs.readFileSync(path.join(repo,'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'),'utf8'));
  const profiles=catalog.profiles.map(p=>{
    const cfg=JSON.parse(fs.readFileSync(p.config,'utf8'));
    return {id:p.id,suite:p.suite,task:p.task,execution_target:p.execution_target,seed:cfg.seed,
      robot:cfg.robot||{model:'Franka',execution_arm:'single'},
      ...(cfg.schema_version==='attentionbench.robocasa-openarm-mobile-config.v1'?{base_capability:'Mobile · joint driven'}:{})};
  });
  const accepted=profiles.find(p=>p.base_capability);assert.ok(accepted);
  const franka=profiles.find(p=>p.suite==='robocasa'&&p.robot.model==='Franka');assert.ok(franka);
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(_,s)=>{
    s.launchOptions={profiles,attention_policies:['autonomous','reactive_help'],assistance_modes:['live_human_first']};
  }});
  await page.locator('[data-open-launch]').click();await selectLaunchTask(page,'robocasa',franka.id);
  assert.equal(await page.getByLabel('Robot',{exact:true}).inputValue(),'Franka');
  await page.getByLabel('Robot',{exact:true}).selectOption('OpenArm');
  assert.equal(await page.getByLabel('Execution arm',{exact:true}).inputValue(),'left');
  assert.match(await page.locator('[data-launch-preview]').innerText(),/OpenArm.*left.*Mobile.*joint driven/);
  assert.equal(state.posts.length,0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await evidence(page,`MOBILE-DEFAULT-${width}`);await page.locator('#ab-launch-form button[type=submit]').click();
  assert.equal(state.posts.length,1);assert.equal(state.posts[0].payload.profile_id,accepted.id);
  assert.equal(state.posts[0].payload.robot_model,'OpenArm');assert.equal(state.posts[0].payload.execution_arm,'left');
});

// Policy-switch-v1 acceptance: append to every existing interaction assertion.
function controlFixture(_,s) {
  const snap=s.snapshots.old;
  snap.run.display_name='OpenArm · Left · Mobile · 2026-10-09T03:26:00Z · abcd01';
  snap.attention_control={initial_policy:'autonomous',active_policy:'autonomous',generation:0,read_only:false,
    binding:{allowed_policies:['autonomous','reactive_help']},request:null,events:[],segments:[{policy_id:'autonomous',effective_at:1791516360,generation:0}],switch_count:0};
  s.runs=[{...snap.run,created_at:1791516360,robot:{model:'OpenArm',execution_arm:'left'},seed:101}];
}
function badgeFixture(_,s){
  controlFixture(_,s);
  Object.assign(s.runs[0],{initial_policy:'autonomous',attention_control:s.snapshots.old.attention_control});
  const cfg={suite:'robocasa365',task_id:'pick_place_counter_to_sink',robot:'OpenArm365',execution_arm:'both',seed:101};
  const dev=(id,extra)=>({session_id:id,channel:'dev_v2',created_at:1791516300,locked:{config:cfg},versions:[],jobs:[],budget:{executions:3},...extra});
  s.devSessions=[
    dev('v2-mixed',{state:'completed',policy_id:'reactive_help',policy_segments:['autonomous','reactive_help'],mixed_policy:true,used:{executions:2},jobs:[1,2].map(n=>({job_id:'j'+n,kind:'execute',receipt:{execution_status:'failed'}}))}),
    dev('v2-nojobs',{state:'completed',policy_id:'autonomous',policy_segments:['autonomous'],used:{executions:0}}),
    dev('v2-live',{state:'running',lifecycle:{phase:'sdk'},policy_id:'full_trace_aware_attention_planner',policy_segments:['full_trace_aware_attention_planner'],used:{executions:1}}),
    {session_id:'loop-old',channel:'dev_loop',state:'budget_exhausted',created_at:1791516200,locked:{config:cfg},versions:[],jobs:[],budget:{executions:3},used:{executions:3},
      attention_control:{active_policy:null,segments:[],binding:null}}];
}
for(const width of [1280,390])test(`SIDEBAR-BADGES: read-only strategy and attempt badges per record at ${width}px`,async t=>{
  const {page}=await fixture(t,{operator:true,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:badgeFixture});
  if(!await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  await page.evaluate(()=>document.querySelectorAll('[data-sidebar] details').forEach(d=>{if(d.querySelector(':scope>.ab-task-summary'))d.open=true;}));
  const badge=(id,kind)=>page.locator(`[data-run-entry="${id}"] [data-run-badge=${kind}]`);
  assert.equal(await badge('old','strategy').innerText(),'Autonomous');
  assert.equal(await badge('old','attempts').count(),0);
  assert.equal(await badge('v2-mixed','strategy').innerText(),'Auto → Reactive');
  assert.equal(await badge('v2-mixed','strategy').getAttribute('title'),'Help strategy: Autonomous → Reactive help · 1 switch');
  assert.equal(await badge('v2-mixed','strategy').getAttribute('data-policy'),'reactive_help');
  const tint=loc=>loc.evaluate(e=>getComputedStyle(e).backgroundColor);
  assert.notEqual(await tint(badge('v2-mixed','strategy')),await tint(badge('v2-live','strategy')),'each strategy gets its own tint');
  assert.equal(await tint(badge('v2-mixed','attempts')),'rgba(0, 0, 0, 0)');
  const status=id=>page.locator(`[data-run-entry="${id}"] .ab-run-status`);
  assert.equal((await status('v2-mixed').innerText()).trim(),'Failed');
  assert.equal(await status('v2-mixed').getAttribute('data-status'),'failed');
  assert.equal(await page.locator('[data-run-entry="v2-mixed"]').getAttribute('data-tone'),'failed');
  assert.equal((await status('v2-nojobs').innerText()).trim(),'Not solved');
  assert.equal(await page.locator('[data-run-entry="v2-nojobs"]').getAttribute('data-tone'),'idle');
  assert.equal(await badge('v2-mixed','attempts').innerText(),'2/3 attempts');
  assert.equal(await badge('v2-mixed','attempts').getAttribute('data-live'),'false');
  assert.equal(await badge('v2-live','strategy').innerText(),'Planner');
  assert.equal(await badge('v2-live','attempts').innerText(),'Attempt 2/3');
  assert.equal(await badge('v2-live','attempts').getAttribute('data-live'),'true');
  assert.equal(await badge('loop-old','strategy').count(),0);
  assert.equal(await badge('loop-old','attempts').innerText(),'3/3 attempts');
  assert.equal(await page.locator('[data-run-entry] .ab-run-badge :is(button,a)').count(),0);
  const fits=await page.locator('[data-run-entry] .ab-run-badge').evaluateAll(els=>els.every(e=>{const a=e.closest('article').getBoundingClientRect(),b=e.getBoundingClientRect();return b.width>0&&b.right<=a.right+0.5;}));
  assert.ok(fits,'every badge stays inside its record card');
  await evidence(page,`SIDEBAR-BADGES-${width}`);
});
for(const width of [1280,390])test(`SIDEBAR-01: navigation, grouped identity, pins, search and remembered collapse at ${width}px`,async t=>{
  const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:controlFixture});
  await page.locator('[data-sidebar-toggle]').click();
  if(!await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  assert.match(await page.locator('[data-sidebar]').innerText(),/Tasks & runs[\s\S]*Policy controller/);
  assert.equal(await page.locator('[data-sidebar] #ab-live-run').count(),1);
  assert.match(await page.locator('[data-run-entry=old]').innerText(),/OpenArm.*Left.*Mobile/);
  await page.locator('[data-pin-run=old]').click();
  assert.equal(await page.locator('[data-pin-run=old]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-run-search]').fill('no such run');
  assert.equal(await page.locator('[data-run-entry=old]').count(),0);
  await page.locator('[data-run-search]').fill('');
  await page.locator('[data-sidebar-toggle]').click();
  assert.equal(await page.locator('[data-sidebar]').isVisible(),false);
  await page.reload();await page.locator('[data-sidebar-toggle]').waitFor();
  assert.equal(await page.locator('[data-sidebar]').isVisible(),false);
  assert.equal(state.posts.length,0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});
test('POLICY-UI-01: Requested/Pending never marks card Active before backend confirmation',async t=>{
  const {page,state,tick}=await fixture(t,{beforeNavigate:controlFixture});
  if(!await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  await page.locator('[data-attention-policy=reactive_help]').click();
  await page.waitForFunction(()=>document.querySelector('[data-sidebar-message]')?.textContent==='ok');
  assert.equal(state.posts[0].pathname,'/api/runs/old/attention-policy');
  assert.equal(state.posts[0].payload.expected_generation,0);
  assert.equal(await page.locator('[data-attention-policy=autonomous]').getAttribute('data-state'),'Active');
  assert.notEqual(await page.locator('[data-attention-policy=reactive_help]').getAttribute('data-state'),'Active');
  state.snapshots.old.attention_control.active_policy='reactive_help';state.snapshots.old.attention_control.generation=1;
  state.snapshots.old.attention_control.segments.push({policy_id:'reactive_help',effective_at:1791516370,generation:1,boundary_id:'sdk:2'});
  state.snapshots.old.attention_control.switch_count=1;await tick();
  await page.waitForFunction(()=>document.querySelector('[data-attention-policy=reactive_help]')?.dataset.state==='Active');
  assert.equal(await page.locator('[data-attention-policy=reactive_help]').getAttribute('data-state'),'Active');
});
test('POLICY-UI-02: history controller is read-only and offers associated engineering run',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:(_,s)=>{controlFixture(_,s);s.snapshots.old.run.status='completed';s.snapshots.old.attention_control.read_only=true;}});
  assert.equal(await page.locator('[data-attention-policy=reactive_help]').isEnabled(),false);
  assert.equal(await page.locator('[data-linked-policy-run]').count(),1);
  assert.equal(state.posts.length,0);
});
test('SIDEBAR-02: names remain stable across reorder and actual path expansion',async t=>{
  const {page,state,tick}=await fixture(t,{beforeNavigate:controlFixture});
  const initial=await page.locator('[data-run-entry=old] [data-run-name]').innerText();
  const control=state.snapshots.old.attention_control;
  control.segments.push({policy_id:'reactive_help',effective_at:1791516370,generation:1},{policy_id:'autonomous',effective_at:1791516380,generation:2});
  control.switch_count=2;state.runs[0].attention_control=control;await tick();
  assert.equal(await page.locator('[data-run-entry=old] [data-run-name]').innerText(),initial);
  assert.match(await page.locator('[data-run-path=old]').innerText(),/Autonomous.*Autonomous.*2 switches/);
  await page.locator('[data-run-path=old] summary').click();
  assert.match(await page.locator('[data-run-path=old]').innerText(),/Reactive help/);
});

async function switchViewingRun(page,id){
  if(!await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  await page.locator('#ab-live-run').selectOption(id);
}

test('SIDEBAR-03: history view retains executing-run stop target when navigation is collapsed',async t=>{
  const {page,state,tick}=await fixture(t,{beforeNavigate:(_,s)=>{controlFixture(_,s);s.snapshots.history=snapshot('history','completed');s.snapshots.history.requests=[];s.runs.push({...s.snapshots.history.run,display_name:'Recorded history'});}});
  await switchViewingRun(page,'history');await page.locator('[data-run-overview=history]').waitFor();
  if(await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  assert.equal(await page.locator('[data-running-status]').isVisible(),true);
  assert.match(await page.locator('[data-running-status]').innerText(),/Executing.*OpenArm/s);
  assert.equal(await page.locator('[data-running-status] [data-stop-executing]').getAttribute('data-stop-executing'),'old');
  assert.equal(state.posts.length,0);
});
test('POLICY-UI-03: switch preserves Hint draft, selection and focus without submission',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:controlFixture});
  const hint=page.locator('#ab-hint-content');await hint.fill('Draft stays with this run');await hint.evaluate(e=>e.setSelectionRange(3,12));
  await page.locator('[data-attention-policy=reactive_help]').click();
  await page.waitForFunction(()=>document.querySelector('[data-sidebar-message]')?.textContent==='ok');
  await page.waitForFunction(()=>document.querySelector('#ab-hint-content')===document.activeElement);
  assert.deepEqual(await hint.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e===document.activeElement]),['Draft stays with this run',3,12,true]);
  assert.equal(state.posts.filter(p=>p.pathname.endsWith('/respond')).length,0);
});
test('SIDEBAR-04: switching away and back restores each run Hint selection and focus',async t=>{
  const {page,state,tick}=await fixture(t,{beforeNavigate:(_,s)=>{controlFixture(_,s);s.snapshots.second=snapshot('second');s.runs.push({...s.snapshots.second.run,display_name:'Second run'});}});
  const hint=page.locator('#ab-hint-content');await hint.fill('First private draft');await hint.evaluate(e=>e.setSelectionRange(2,8));
  await page.locator('[data-view-run=second]').click();await page.locator('[data-run-overview=second]').waitFor();
  await hint.fill('Second private draft');await hint.evaluate(e=>e.setSelectionRange(4,9));
  await page.locator('[data-view-run=old]').click();await page.locator('[data-run-overview=old]').waitFor();
  assert.deepEqual(await hint.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e===document.activeElement]),['First private draft',2,8,true]);
  await page.locator('[data-view-run=second]').click();await page.locator('[data-run-overview=second]').waitFor();
  assert.deepEqual(await hint.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e===document.activeElement]),['Second private draft',4,9,true]);
  assert.equal(state.posts.length,0);
});
test('POLICY-UI-04: replay switch markers and policy follow actual executor timestamp',async t=>{
  const snap=timelineSnapshot();
  const {page,state}=await fixture(t,{initialSnapshot:snap,beforeNavigate:(_,s)=>{
    controlFixture(_,s);const c=s.snapshots.old.attention_control;
    c.segments=[{policy_id:'autonomous',effective_at:1791299999,generation:0},{policy_id:'reactive_help',effective_at:1791300002,generation:1,attempt_id:'old-a0',trace_seconds:2,boundary_kind:'sdk_action_completed'}];
    c.active_policy='reactive_help';c.switch_count=1;
  }});
  await page.locator('[data-history-select=old-a0]').click();
  assert.equal(await page.locator('[data-policy-switch-marker]').count(),1);
  assert.equal(await page.locator('[data-attention-policy=autonomous]').getAttribute('data-state'),'Active');
  assert.equal(await page.locator('[data-attention-policy=reactive_help]').isEnabled(),false);
  assert.equal(state.posts.length,0);
});

test('SIDEBAR-05: copy identifies source robot despite profile capability metadata and clears context on close',async t=>{
  const {page,state}=await fixture(t,{beforeNavigate:(_,s)=>{
    controlFixture(_,s);s.snapshots.old.run.robot={model:'OpenArm',execution_arm:'left'};
    s.launchOptions={profiles:[{id:'mobile',suite:'robosuite',task:s.snapshots.old.run.task_id,execution_target:'robosuite_sim',seed:101,attention_switching:true,robot:{model:'OpenArm',execution_arm:'left',base_capability:'mobile'}}],attention_policies:['autonomous','reactive_help'],assistance_modes:['live_human_first']};
  }});
  await page.locator('[data-copy-run=old]').click();await page.locator('[data-launch-source]').waitFor();
  assert.equal(await page.locator('#ab-launch-form [name=benchmark]').inputValue(),'robosuite');
  assert.equal(await page.locator('#ab-launch-form [name=profile_id]').inputValue(),'mobile');
  await page.locator('[data-close-launch]').first().click();await page.locator('[data-open-launch]').click();
  assert.equal(await page.locator('[data-launch-source]').count(),0);assert.equal(state.posts.length,0);
});

test('SIDEBAR-06: copied robot uses deduplicated task option and original launch budgets',async t=>{
 const {page,state}=await fixture(t,{beforeNavigate:(_,s)=>{
  controlFixture(_,s);s.snapshots.old.run.robot={model:'OpenArm',execution_arm:'left'};s.snapshots.old.run.robot_configuration={base:{capability:'mobile_base_joint_drive'}};
  s.launches=[{run_id:'old',launch_id:'source',state:'running',locked:{max_attempts:3,assistance_credits:2,token_limit:5000,human_deadline_seconds:70,overall_deadline_seconds:180}}];
  s.launchOptions={profiles:[{id:'franka',suite:'robocasa',task:s.snapshots.old.run.task_id,execution_target:'robocasa_sim',seed:101,attention_switching:true,robot:{model:'Franka',execution_arm:'single'}},{id:'mobile',suite:'robocasa',task:s.snapshots.old.run.task_id,execution_target:'robocasa_sim',seed:101,attention_switching:true,robot:{model:'OpenArm',execution_arm:'left'},base_capability:'Mobile · joint driven'}],attention_policies:['autonomous','reactive_help'],assistance_modes:['live_human_first']};
  s.snapshots.old.run.suite='robocasa';s.runs[0].suite='robocasa';
 }});
 await page.locator('[data-copy-run=old]').click();await page.locator('[data-launch-source]').waitFor();
 assert.equal(await page.locator('#ab-launch-form [name=profile_id]').inputValue(),'franka');
 assert.equal(await page.locator('#ab-launch-form [name=robot_model]').inputValue(),'OpenArm');
 assert.equal(await page.locator('#ab-launch-form [name=execution_arm]').inputValue(),'left');
 assert.equal(await page.locator('#ab-launch-form [name=max_attempts]').inputValue(),'3');
 assert.equal(await page.locator('#ab-launch-form [name=assistance_credits]').inputValue(),'2');
 assert.equal(state.posts.length,0);
});

test('POLICY-UI-05: switch seek reaches first real sample after boundary, so replay reports new policy',async t=>{
 const {page,state,tick}=await fixture(t,{initialSnapshot:timelineSnapshot(),beforeNavigate:(_,s)=>{
  controlFixture(_,s);s.snapshots.old.attention_control.segments=[{policy_id:'autonomous',generation:0,effective_at:1791299999},{policy_id:'reactive_help',generation:1,effective_at:1791300002,attempt_id:'old-a0',trace_seconds:1.3}];
 }});
 await page.locator('[data-history-select=old-a0]').click();await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1);
 await page.locator('[data-policy-switch-marker]').click();
 await page.waitForFunction(()=>document.querySelector('video')?.currentTime>.4);
 assert.equal(await page.locator('video').evaluate(v=>v.currentTime),1.2);
 await page.waitForFunction(()=>document.querySelector('[data-attention-policy=reactive_help]')?.dataset.state==='Active');
 assert.equal(await page.locator('[data-attention-policy=reactive_help]').isEnabled(),false);await tick();assert.equal(await page.locator('[data-attention-policy=reactive_help]').getAttribute('data-state'),'Active');assert.equal(state.posts.length,0);
});

test('SIDEBAR-07: persistent Hint notice does not cover floating stop at 390px',async t=>{
 const waiting=snapshot('old');
 const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width:390,height:844},beforeNavigate:(_,s)=>{s.snapshots.old.requests=[];}});
 state.snapshots.old.requests=waiting.requests;await tick();await page.locator('#ab-hint-content').scrollIntoViewIfNeeded();
 await page.waitForFunction(()=>document.querySelector('[data-run-overview]')?.dataset.floating==='true');
 assert.equal(await page.locator('#ab-emergency-interrupt').evaluate(e=>{const a=e.getBoundingClientRect(),b=document.querySelector('#ab-run-notice').getBoundingClientRect();return a.bottom<=b.top||b.bottom<=a.top||a.right<=b.left||b.right<=a.left;}),true);
 await page.locator('#ab-emergency-interrupt').click();
 assert.equal(state.posts.filter(p=>p.pathname.endsWith('/interrupt')).length,1);
});

test('SIDEBAR-08: polling preserves name editor draft, focus, disclosure and run-list scroll',async t=>{
 const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width:1280,height:900},beforeNavigate:(_,s)=>{
  controlFixture(_,s);for(let i=0;i<8;i++)s.runs.push({...s.runs[0],run_id:'extra-'+i,display_name:'Additional stable run '+i});
 }});
 const entry=page.locator('[data-run-entry=old]');await entry.getByText('Edit name',{exact:true}).click();
 const name=entry.locator('input[name=display_name]');await name.fill('Private name edit');await name.evaluate(e=>e.setSelectionRange(2,7));
 await page.locator('[data-sidebar] nav').evaluate(e=>e.scrollTop=95);await tick();
 assert.deepEqual(await name.evaluate(e=>[e.value,e.selectionStart,e.selectionEnd,e===document.activeElement]),['Private name edit',2,7,true]);
 assert.equal(await page.locator('[data-sidebar] nav').evaluate(e=>e.scrollTop),95);
 assert.equal(state.posts.length,0);
});

test('POLICY-UI-06: Attention decision boundary stays on timeline without inventing a replay frame',async t=>{
 const snap=timelineSnapshot();snap.attempts=[snap.attempts[0]];
 const {page,state}=await fixture(t,{initialSnapshot:snap,beforeNavigate:(_,s)=>{
  controlFixture(_,s);s.snapshots.old.attention_control.segments=[{policy_id:'autonomous',generation:0,effective_at:1},{policy_id:'reactive_help',generation:1,effective_at:snap.attempts[0].started_at+10,boundary_kind:'attention_decision',boundary_id:'after-attempt'}];
 }});
 await page.locator('[data-history-select=old-a0]').click();
 assert.equal(await page.locator('[data-policy-switch-marker]').count(),1);
 assert.equal(await page.locator('[data-policy-switch-marker]').isEnabled(),false);
 assert.match(await page.locator('[data-policy-switch-marker]').innerText(),/Attention decision.*no recorded frame/i);
 assert.equal(await page.locator('[data-attention-policy=autonomous]').getAttribute('data-state'),'Active');
 assert.equal(state.posts.length,0);
});

test('SIDEBAR-09: mobile drawer toggle has reserved space and does not cover navigation',async t=>{
  const {page}=await fixture(t,{styled:true,fullDocument:true,viewport:{width:390,height:844},beforeNavigate:controlFixture});
  if(!await page.locator('[data-sidebar]').isVisible())await page.locator('[data-sidebar-toggle]').click();
  const toggle=await page.locator('[data-sidebar-toggle]').boundingBox(),heading=await page.getByRole('heading',{name:'Tasks & runs',exact:true}).boundingBox(),picker=await page.locator('#ab-live-run').boundingBox();
  assert.ok(toggle.y+toggle.height<=heading.y,'drawer toggle must stay in reserved header space');
  assert.ok(toggle.y+toggle.height<=picker.y,'Viewing run must stay unobstructed');
  assert.equal(await page.locator('#ab-live-run').evaluate(el=>{let r=el.getBoundingClientRect();return document.elementFromPoint(r.x+30,r.y+r.height/2)===el;}),true);
  await page.locator('[data-sidebar-toggle]').click();assert.equal(await page.locator('[data-sidebar]').isVisible(),false);
});

for(const width of [1280,390])test(`BIMANUAL-UI-01: Both arms binds a separate profile and preserves polling focus at ${width}px`,async t=>{
 const {page,state,tick}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:(p,s)=>{
  openarmOptions(p,s);s.launchOptions.profiles.push({id:'sink-both',suite:'robocasa',task:'counter_to_sink',seed:101,execution_target:'robocasa_sim',robot:{model:'OpenArm',execution_arm:'both'}});
 }});
 await page.locator('[data-open-launch]').click();await selectLaunchTask(page,'robocasa','sink-franka');
 await page.getByLabel('Robot',{exact:true}).selectOption('OpenArm');
 const mode=page.getByLabel('Execution arm',{exact:true});
 assert.deepEqual(await mode.locator('option').evaluateAll(es=>es.map(e=>e.textContent)),['Left only','Right only','Both arms']);
 await mode.selectOption('both');await mode.focus();await tick();
 assert.equal(await mode.evaluate(e=>e===document.activeElement),true);
 assert.match(await page.locator('[data-launch-preview]').innerText(),/Both arms.*locked/);
 assert.equal(await page.locator('.ab-launch-body').evaluate(e=>e.scrollWidth<=e.clientWidth),true);
 await page.locator('#ab-launch-form button[type=submit]').click();
 assert.equal(state.posts.length,1);assert.equal(state.posts[0].payload.profile_id,'sink-both');assert.equal(state.posts[0].payload.execution_arm,'both');
});

test('BIMANUAL-UI-02: Hint scope persists with draft, submission binds the requested hand and phase',async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
 await page.locator('#ab-hint-content').fill('Receiving hand guidance');
 await page.getByLabel('Hint target hand',{exact:true}).selectOption('right');await page.getByLabel('Hint task phase',{exact:true}).selectOption('handoff');
 await tick();
 assert.equal(await page.getByLabel('Hint target hand',{exact:true}).inputValue(),'right');assert.equal(await page.getByLabel('Hint task phase',{exact:true}).inputValue(),'handoff');
 assert.equal(await page.locator('#ab-hint-content').inputValue(),'Receiving hand guidance');assert.equal(state.posts.length,0);
 await page.locator('[data-respond=hint]').click();
 assert.deepEqual(state.posts[0].payload.guidance_scope,{arm:'right',phase:'handoff',attempt_id:'old-a0'});
});

test('BIMANUAL-UI-03: hands and handoff are shown from actual control samples without changing Attention policy',async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 snap.station.arm_control={step:321,handoff:{phase:'receiving'},arms:{left:{active:false,grasped:true},right:{active:true,grasped:false,action_id:'7:3'}},grippers:{left:{driver_position_m:.02},right:{driver_position_m:.044}}};
 const {page}=await fixture(t,{initialSnapshot:snap});
 assert.match(await page.locator('[data-arm=left]').innerText(),/Left hand.*Holding.*Target contact confirmed/s);
 assert.match(await page.locator('[data-arm=right]').innerText(),/Right hand.*Executing.*7:3/s);
 assert.match(await page.locator('[data-cooperation-phase]').innerText(),/receiving.*321/);
 assert.match(await page.locator('[data-run-settings]').innerText(),/Execution mode.*Both arms/s);
});

test('BIMANUAL-UI-04: historical hands follow the replay clock and never use another attempt current state',async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 const sample=(step,phase,active)=>({step,epoch:0,handoff:{id:'0:h1',phase},arms:{left:{active:false,grasped:true},right:{active,grasped:!active,action_id:active?'0:r1':null}},grippers:{left:{driver_position_m:.024},right:{driver_position_m:.025}}});
 snap.station.arm_control=sample(999,'completed',false);
 snap.traces['old-t0'].evidence=[{evidence_id:'video',kind:'advisor_replay',mime_type:'video/mp4',media_url:'/replay.mp4'}];
 snap.traces['old-t0'].recording={control_samples:[{video_seconds:0,control:sample(101,'receiving',true)},{video_seconds:2,control:sample(205,'releasing',false)}]};
 const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
 await page.locator('[data-execution-viewer] [data-history-select=old-a0]').click();
 assert.match(await page.locator('[data-cooperation-phase]').innerText(),/receiving.*101/);
 await page.locator('video[data-history-replay]').evaluate(e=>{Object.defineProperty(e,'currentTime',{value:2.5,writable:true,configurable:true});e.dispatchEvent(new Event('timeupdate'));});
 assert.match(await page.locator('[data-cooperation-phase]').innerText(),/releasing.*205/);
 await tick();assert.match(await page.locator('[data-cooperation-phase]').innerText(),/releasing.*205/);
 assert.equal(state.posts.length,0);
});

test('BIMANUAL-UI-05: a changed snapshot preserves scoped Hint select focus and draft',async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 const {page,state,tick}=await fixture(t,{initialSnapshot:snap});
 await page.locator('#ab-hint-content').fill('AB_CONTROL_V1 receiving_offset_m=0.030;');
 const arm=page.getByLabel('Hint target hand',{exact:true});await arm.selectOption('right');
 await page.getByLabel('Hint task phase',{exact:true}).selectOption('handoff');await arm.focus();
 state.snapshots.old.traces['old-t0'].hypothesis='A new persisted observation';await tick();
 assert.equal(await arm.evaluate(e=>document.activeElement===e),true);
 assert.equal(await arm.inputValue(),'right');assert.equal(await page.getByLabel('Hint task phase',{exact:true}).inputValue(),'handoff');
 assert.equal(await page.locator('#ab-hint-content').inputValue(),'AB_CONTROL_V1 receiving_offset_m=0.030;');
 assert.equal(state.posts.length,0);
});

test('BIMANUAL-UI-06: a recorded phase start never displays phase completion',async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 snap.traces['old-t0'].events=[{event_id:'start',event_type:'skill.started',operation:'physical_handoff',status:'completed',timestamp:1,timestamp_basis:'relative',arguments:{participating_arms:['left','right'],coordination_group:'0:h1'}},
 {event_id:'blocked',event_type:'skill.blocked',operation:'physical_handoff',status:'failed',timestamp:2,timestamp_basis:'relative',arguments:{participating_arms:['left','right'],coordination_group:'0:h1'}}];
 const {page}=await fixture(t,{initialSnapshot:snap});await page.locator('[data-execution-viewer] [data-history-select=old-a0]').click();
 const events=page.locator('[data-history-event]');assert.equal(await events.count(),2);
 assert.match(await events.nth(0).innerText(),/started/);assert.doesNotMatch(await events.nth(0).innerText(),/completed/);
 assert.match(await events.nth(1).innerText(),/failed/);
});


for(const width of [1280,390])test(`BIMANUAL-UI-07: observed task phase and each hand have clear cards at ${width}px`,async t=>{
 const snap=snapshot();snap.run.suite='robocasa';snap.run.robot={model:'OpenArm',execution_arm:'both'};
 snap.station.arm_control={step:912,epoch:7,task_phase:'pickup',handoff:null,arms:{left:{active:true,grasped:false,action_id:'7:2'},right:{active:false,grasped:false}},grippers:{left:{driver_position_m:.044},right:{driver_position_m:.020}}};
 const {page}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},initialSnapshot:snap});
 assert.match(await page.locator('[data-cooperation-phase]').innerText(),/Task phase: Pickup.*912/);
 const layout=await page.locator('[data-arm-status]').evaluate(el=>({display:getComputedStyle(el).display,width:el.scrollWidth,client:el.clientWidth,borders:[...el.querySelectorAll('[data-arm]')].map(x=>getComputedStyle(x).borderStyle)}));
 assert.equal(layout.display,'grid');assert.ok(layout.width<=layout.client);assert.deepEqual(layout.borders,['solid','solid']);
});

for(const width of [1280,390]) test(`RECORDING-365-CURRENT: ended attempt plays in current view at ${width}px`,async t=>{
 const snap=timelineSnapshot();snap.run.suite='robocasa365';snap.run.status='failed';snap.attempts[0].ended_at=1791300010;
 snap.traces['old-t0'].recording={...snap.traces['old-t0'].recording,status:'saved',frame_count:20,sampled_seconds:3,video_saved:true};
 snap.traces['old-t0'].evidence.push(...['camera_initial','camera_final'].map(kind=>({kind,mime_type:'image/png',media_url:`/${kind}.png`})));
 const {page,tick}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
 assert.equal(await page.locator('[data-history-replay]').count(),1);
 assert.equal(await page.locator('[data-history-image]').count(),0);
 assert.match(await page.locator('[data-execution-viewer]').innerText(),/Current attempt replay/);
 await page.locator('[data-history-select="old-a0"]').click();await tick();
 assert.match(await page.locator('[data-execution-viewer]').innerText(),/History replay/);
 assert.equal(await page.locator('[data-history-image]').count(),0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});
for(const width of [1280,390]) test(`RECORDING-365-MISSING: original reason remains explicit at ${width}px`,async t=>{
 const snap=snapshot('old','failed');snap.run.suite='robocasa365';snap.requests=[];
 snap.traces['old-t0'].recording={status:'saved_images',reason:'Only initial and terminal RGB were recorded; no continuous frames exist'};
 const {page}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
 await page.locator('[data-history-select="old-a0"]').click();
 assert.match(await page.locator('.ab-history-empty').innerText(),/Only initial and terminal RGB/);
 assert.equal(await page.locator('video').count(),0);
});

for(const width of [1280,390]) for(const hd of [true,false]) test(`RECORDING-365-ORIENTATION: HD=${hd} preserves upright pixels and legacy row correction at ${width}px`,async t=>{
 const snap=timelineSnapshot();snap.run.suite='robocasa365';snap.run.status='failed';snap.attempts[0].ended_at=1791300010;
 snap.traces['old-t0'].recording.source=hd?'official_simulator_display_rgb_after_each_control_step':'official_simulator_public_rgb_after_each_control_step';
 snap.traces['old-t0'].evidence.push({kind:'camera_saved',mime_type:'image/png',media_url:'/camera.png'});
 const {page}=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
 assert.equal(await page.locator('[data-history-image]').count(),1);
 const transform=await page.locator('[data-history-image]').evaluate(el=>getComputedStyle(el).transform);
 assert.equal(transform,hd?'none':'matrix(1, 0, 0, -1, 0, 0)');
});

for(const width of [1280,390])test(`OPENARM365-UI: approved robot selection applies full-task budgets at ${width}px`,async t=>{
 const beforeNavigate=(_,state)=>{state.launchOptions={profiles:[
 {id:'365-panda',suite:'robocasa365',task:'pick_place_counter_to_sink',seed:101,execution_target:'robocasa365_sim',robot:{model:'PandaOmron',execution_arm:'right'},run_limits:{max_attempts:10,assistance_credits:9,token_limit:8000,human_deadline_seconds:30,overall_deadline_seconds:300},attempt_wall_seconds:120},
 {controller:'BASIC',approved_config_sha256:'a'.repeat(64),id:'365-openarm',suite:'robocasa365',task:'pick_place_counter_to_sink',seed:101,execution_target:'robocasa365_sim',robot:{model:'OpenArm',execution_arm:'left'},run_limits:{max_attempts:1,assistance_credits:0,token_limit:0,human_deadline_seconds:30,overall_deadline_seconds:600},attempt_wall_seconds:480}
 ],attention_policies:['autonomous','reactive_help'],assistance_modes:['live_human_first']};};
 const {page,state}=await fixture(t,{styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate});
 await page.locator('[data-open-launch]').click();await selectLaunchTask(page,'robocasa365','365-panda');
 const robot=page.getByLabel('Robot',{exact:true});assert.equal(await robot.isVisible(),true);
 await robot.selectOption('OpenArm');
 const form=page.locator('#ab-launch-form');assert.equal(await form.locator('[name=execution_arm]').inputValue(),'left');
 for(const [key,value] of Object.entries({max_attempts:1,assistance_credits:0,token_limit:0,overall_deadline_seconds:600}))assert.equal(await form.locator(`[name=${key}]`).inputValue(),String(value));
 assert.match(await form.innerText(),/each attempt ≤480 s/);
 assert.match(await form.innerText(),/Controller BASIC · configuration aaaaaaaaaaaa/);
 await form.locator('button[type=submit]').click();assert.equal(state.posts[0].payload.profile_id,'365-openarm');assert.equal(state.posts[0].payload.robot_model,'OpenArm');assert.equal(state.posts[0].payload.execution_arm,'left');
});

// Approved operator acceptance matrix: real DOM/video, isolated API fixtures.
const fontfixSource=fs.readFileSync([path.join(repo,'../candidate-fontfix'),path.join(repo,'outputs/dev-live-monitor-20261014/candidate-fontfix')].map(p=>path.join(p,'benchmarks/attention_harness/ui/live.js')).find(p=>fs.existsSync(p)),'utf8');
const operatorHeadings=['01 TASK SETUP','02 STATUS & PROGRESS','03 REPLAY & ATTEMPTS','04 HELP & REPLIES','05 RESOURCES'];
const operatorPrivatePattern=/[a-z]+_[a-z_]+|[a-z]+\.[a-z_]+\(|\b[0-9a-f]{12,}\b|\{\s*"|RuntimeError|SHA256/;
function operatorSnapshot(kind,waiting=false) {
 const snap=snapshot('old',waiting?'running':'completed');
 snap.run={...snap.run,developer_model:'provider/GLM',evaluator_model:'native_evaluator',robot:{model:'OpenArm365',execution_arm:'both'},robot_configuration:{base:{capability:'stationary'}}};
 snap.dev_evidence_gaps=[];snap.task_plan={max_attempts:4,definition:{description:'Pick up the item on the counter and put it in the sink'}};
 snap.attempts=[0,1,2].map(index=>({attempt_id:'old-a'+index,index,started_at:1791300000+index*5,ended_at:1791300004+index*5,execution_complete:true,status:'failed',dev_version:index+1}));
 snap.traces=Object.fromEntries(snap.attempts.map(a=>['t'+a.index,{trace_id:'t'+a.index,attempt_id:a.attempt_id,execution_id:'execution-secret',failure:{message:'RuntimeError: joint tracking exceeds 4 mrad'},events:[{operation:'sensors.get_observation',status:'completed',timestamp:.5,timestamp_basis:'relative'},{operation:'sensors.get_observation',status:'completed',timestamp:.7,timestamp_basis:'relative'},{operation:'right_arm.move_delta',status:'failed',timestamp:1.2,timestamp_basis:'relative',arguments:{private_key:3}}],evidence:[{media_url:'/replay.mp4',mime_type:'video/mp4'}]}]));
 snap.station={status:'ended'};snap.eval={native_success:false,stopped_reason:'max_attempts',native_outcomes:{}};
 snap.requests=[0,1].map(i=>({request_id:'old-r'+i,trace_id:'t'+i,attempt_id:'old-a'+i,request_type:'hint',state:'answered',mode:'automatic',response_id:'reply'+i,created_at:100+i*30}));
 snap.responses=Object.fromEntries([0,1].map(i=>['reply'+i,{response_id:'reply'+i,responder:'advisor_proxy',content:'Use smaller movements for attempt '+(i+1)+'.',created_at:114+i*30}]));
 snap.attention_control={active_policy:'reactive_help',read_only:!waiting,binding:{allowed_policies:['autonomous','reactive_help']},segments:[],events:[],hint_count:2,hint_limit:3};
 if(waiting&&kind==='formal')snap.requests.push({request_id:'pending-reply',trace_id:'t2',attempt_id:'old-a2',request_type:'hint',state:'pending',mode:'live_human_first',created_at:Date.now()/1000-3,deadline_at:Date.now()/1000+600});
 const resource=(used,limit)=>({used,limit,reserved:0,remaining:limit-used});
 snap.resources={...snap.resources,executions:resource(3,3),help_count:resource(2,3),model_calls:resource(3,3),wall_seconds:resource(100,900),execution_seconds:resource(50,300),tokens:resource(21000,220000)};
 if(kind!=='formal')snap.dev_session={session_id:'dev-fixture',state:waiting?'running':'budget_exhausted',model:'provider/GLM',created_at:100,locked:{config:{suite:'robocasa365',task_id:'pick_place_counter_to_sink',robot:'OpenArm365',execution_arm:'both',base_mode:'stationary'}},jobs:[],versions:[1,2,3].map(version=>({version,reason:'Use smaller movements.',valid:true,source:{sha256:'b'.repeat(64)}})),used:{executions:3},budget:{executions:3},reason:'execution_limit_reached',attention_control:snap.attention_control};
 if(waiting&&kind==='dev1')snap.attention_control.hint={state:'pending',request_id:'pending-guidance',version:4,created_at:Date.now()/1000-3,feedback:{public_error:'joint tracking exceeds'},reason:'request_after_each_failure'};
 if(kind==='dev2')snap.dev_v2={experiment_id:'experiment1',key:'cell1',mode:'interactive',jobs:[],ledger:{goal_id:'private-goal',recompute_matches:true,goal:{limits:{},used:{},reserved:{}}}};
 return snap;
}
for(const kind of ['formal','dev1','dev2'])for(const width of [1280,390])test(`OPERATOR-VIEW ${kind} ${width}px: GOAL 8.1 items 1-8`,async t=>{
 const snap=operatorSnapshot(kind),{page,state}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
 // 1: Only the five numbered module headings, in order.
 assert.deepEqual(await page.locator('.view > .panel .op-heading').evaluateAll(es=>es.map(e=>e.innerText.replace(/\s+/g,' ').trim().match(/^(\d{2}) (.*?) (?:What|How|Do)/).slice(1,3).join(' '))),operatorHeadings);
 // 2/3: Technical HTML is absent, not CSS-hidden.
 assert.equal(await page.locator('.view pre,.view code').count(),0);assert.doesNotMatch(await page.locator('.view').innerText(),operatorPrivatePattern);
 // 4: The page has exactly one Tasks button.
 assert.equal(await page.getByRole('button',{name:'Tasks',exact:true}).count(),1);assert.equal(await page.getByRole('button',{name:'☰ Tasks',exact:true}).count(),0);
 // 5: Replies are rendered once, and the whole original body remains expandable.
 const help=await page.locator('[data-core-region=request]').innerText();for(const i of [1,2])assert.equal(help.split('Use smaller movements for attempt '+i+'.').length-1,1);
 // 7: Existing transport seeks the same video to the saved event target.
 const video=page.locator('video[data-history-replay]').first();await video.evaluate(v=>{v.preload='auto';v.load();});await page.waitForFunction(()=>document.querySelector('video[data-history-replay]')?.readyState>=2);
 await page.locator('summary[data-replay-event="2"]').click();await page.waitForFunction(()=>Math.abs(document.querySelector('video[data-history-replay]').currentTime-1.2)<.08);
 assert.equal(state.posts.length,0);assert.equal(state.previewPosts.length,0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 // 8: Debug mode still renders the original technical content, with legacy assertions unchanged.
 const debug=await fixture(t,{initialSnapshot:snap,styled:true,viewport:{width,height:900}});
 for(const text of (kind==='dev1'?['Run details']:['Run details','Evidence details']))assert.ok(await debug.page.getByText(text).count()>0,text);
 if(kind!=='formal')assert.ok(await debug.page.getByText(/Policy revisions · 3 versions/).count()>0);
 if(kind==='dev2')assert.ok(await debug.page.getByText('Experiment ledger & exports',{exact:true}).count()>0);
 // 6: Dev v2 currently exposes automatic Advisor help, without a human submission API.
 if(kind==='dev2'){assert.equal(await page.locator('[data-core-region=request] textarea').count(),0);return;}
 const pending=operatorSnapshot(kind,true),candidate=await fixture(t,{operator:true,initialSnapshot:pending,styled:true,viewport:{width,height:900}});
 assert.match(await candidate.page.locator('[data-operator-outcome]').innerText(),/WAITING FOR YOU/);
 const field=candidate.page.locator('[data-core-region=request] textarea');await candidate.page.getByRole('button',{name:'Move more slowly',exact:true}).click();assert.equal(await field.inputValue(),'Move more slowly');
 await candidate.page.waitForTimeout(350);assert.equal(candidate.state.posts.length,0);assert.equal(candidate.state.previewPosts.length,0);
 const baseline=await fixture(t,{initialSnapshot:operatorSnapshot(kind,true),sourceText:fontfixSource});
 const baselineField=baseline.page.locator('[data-core-region=request] textarea');await baselineField.fill('Move more slowly');
 if(kind==='formal')await baseline.page.locator('[data-respond=hint]').click();else await baseline.page.locator('[data-dev-guidance] button[value=answer]').click();
 await candidate.page.getByRole('button',{name:'Send reply',exact:true}).click();
 assert.equal(candidate.state.posts.length,1);assert.equal(baseline.state.posts.length,1);
 assert.equal(candidate.state.posts[0].pathname,baseline.state.posts[0].pathname);assert.deepEqual(candidate.state.posts[0].payload,baseline.state.posts[0].payload);
});

// ---- OPERATOR-POLISH: finish pass on the approved layout (Tasks left, one number per module, Current view, Loading stage) ----
const loadingPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6Y9sAAAAASUVORK5CYII=','base64');
const loadingTick=page=>page.evaluate(()=>{for(const item of window.__intervals.filter(item=>item.ms===250))item.fn();});
for(const width of [1280,390])test(`OPERATOR-POLISH ended ${width}px: Tasks first, one number per module, Current view always present, no sensor noise`,async t=>{
  const {page}=await fixture(t,{operator:true,initialSnapshot:operatorSnapshot('formal'),styled:true,fullDocument:true,viewport:{width,height:900}});
  const heads=await page.locator('.view > .panel .op-heading').evaluateAll(es=>es.map(e=>({numbers:e.querySelectorAll('.op-number').length,before:getComputedStyle(e.querySelector('h3'),'::before').content})));
  assert.equal(heads.length,5);for(const head of heads){assert.equal(head.numbers,1);assert.ok(['none','normal'].includes(head.before),'legacy numbering must be suppressed: '+head.before);}
  const top=await page.evaluate(()=>{const toggle=document.querySelector('[data-sidebar-toggle]').getBoundingClientRect(),brand=document.querySelector('.top .brand').getBoundingClientRect();return {first:document.querySelector('.top').firstElementChild===document.querySelector('[data-sidebar-toggle]'),left:toggle.right<=brand.left+1};});
  assert.ok(top.first&&top.left,'Tasks must be the first control and sit left of the title');
  assert.match((await page.locator('.op-tabs button').first().innerText()).trim(),/Current view/);
  assert.match((await page.locator('.op-tabs button[aria-pressed=true]').innerText()).trim(),/Attempt 3/);
  assert.doesNotMatch(await page.locator('.ab-history-events').innerText(),/Checked arm position/);
  assert.match(await page.title(),/Failed/);
  // Current view on an ended run is an honest idle card that leads back to the last attempt.
  await page.locator('.op-tabs [data-history-select="live"]').click();
  assert.match(await page.locator('.op-idle').innerText(),/Nothing is running right now/);
  await page.getByRole('button',{name:'Watch the last attempt'}).click();
  assert.match((await page.locator('.op-tabs button[aria-pressed=true]').innerText()).trim(),/Attempt 3/);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});
for(const width of [1280,390])test(`OPERATOR-POLISH running ${width}px: Current view opens live, LOADING fills the black stage and fades when the first frame arrives`,async t=>{
  const snap=operatorSnapshot('formal');
  snap.run.status='running';snap.attempts[2]={...snap.attempts[2],status:'running',ended_at:null,execution_complete:false,started_at:Date.now()/1000-20};
  snap.station={status:'running',camera_url:'/camera',camera_attempt_id:'old-a2'};snap.requests=snap.requests.slice(0,2);snap.eval={status:'unavailable'};
  let release;const gate=new Promise(resolve=>{release=resolve;});
  const {page}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900},beforeNavigate:async page=>{await page.route('**/camera',async route=>{await gate;await route.fulfill({contentType:'image/png',body:loadingPng});});}});
  assert.match((await page.locator('.op-tabs button[aria-pressed=true]').innerText()).trim(),/Current view/);
  assert.equal(await page.locator('.op-live-strip').getAttribute('data-state'),'live');
  assert.match(await page.locator('.op-live-strip').innerText(),/LIVE/);
  const stage=page.locator('[data-op-stage="live"]');
  assert.equal(await stage.getAttribute('data-state'),'loading');
  assert.match(await page.locator('[data-op-stage="live"] .op-loading').innerText(),/LOADING/);
  const covers=await page.evaluate(()=>{const s=document.querySelector('[data-op-stage="live"]').getBoundingClientRect(),o=document.querySelector('[data-op-stage="live"] .op-loading').getBoundingClientRect();return o.width>=s.width-1&&o.height>=s.height-1&&s.height>=200;});
  assert.ok(covers,'LOADING must cover the whole stage');
  assert.match(await page.title(),/Running/);
  release();
  await page.waitForFunction(()=>document.querySelector('#ab-camera-frame')?.complete&&document.querySelector('#ab-camera-frame').naturalWidth>0);
  await loadingTick(page);
  await page.waitForFunction(()=>document.querySelector('[data-op-stage="live"]').dataset.state==='ready');
  assert.equal(await page.locator('[data-op-stage="live"] .op-loading').isVisible(),false);
});
test('OPERATOR-POLISH narrow: Tasks opens a drawer that closes on outside click; zero-limit budgets are not shown',async t=>{
  const snap=operatorSnapshot('formal');snap.resources={...snap.resources,help_count:{used:0,limit:0,reserved:0,remaining:0},tokens:{used:0,limit:0}};
  const {page}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width:390,height:844}});
  assert.equal(await page.locator('.ab-workspace').getAttribute('data-sidebar-open'),'false');
  await page.getByRole('button',{name:'Tasks',exact:true}).click();
  assert.equal(await page.locator('.ab-workspace').getAttribute('data-sidebar-open'),'true');
  await page.mouse.click(380,700);
  assert.equal(await page.locator('.ab-workspace').getAttribute('data-sidebar-open'),'false');
  assert.doesNotMatch(await page.locator('[data-resource-details]').innerText(),/Help requests|AI usage/);
});

// ---- OPERATOR-POLISH regressions reported from the 8771 preview ----
test('OPERATOR-POLISH ended run with no saved picture: LOADING gives way to NO PICTURE instead of waiting forever',async t=>{
  const snap=operatorSnapshot('formal');
  snap.station={status:'ended',camera_url:'/camera-missing',camera_attempt_id:'old-a2',camera_monitor_terminal:true};
  const {page}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width:1280,height:900},beforeNavigate:async page=>{await page.route('**/camera-missing',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));}});
  await page.locator('.op-tabs [data-history-select="live"]').click();
  const stage=page.locator('[data-op-stage="live"]');await stage.waitFor();
  assert.equal(await stage.getAttribute('data-op-ended'),'true');
  await page.waitForFunction(()=>{const i=document.querySelector('#ab-camera-frame');return i&&(i.complete||i.hidden);});
  await loadingTick(page);
  assert.equal(await stage.getAttribute('data-state'),'error');
  const text=await page.locator('[data-op-stage="live"] .op-loading').innerText();
  assert.match(text,/NO PICTURE/);assert.match(text,/No picture was saved/);assert.doesNotMatch(text,/Camera not responding|robot may still be working|LOADING/);
});
test('OPERATOR-POLISH system problem: a run that never started is not reported as a robot failure',async t=>{
  const snap=operatorSnapshot('dev1');
  snap.dev_session.reason='DevConflict: native infrastructure/dispatch unknown; no revision/retry';snap.dev_session.state='needs_review';
  snap.resources={...snap.resources,executions:{used:0,limit:3,reserved:0,remaining:3}};snap.dev_session.used={...snap.dev_session.used,executions:0};
  snap.attempts=[snap.attempts[0]];snap.traces={};snap.requests=[];snap.responses={};
  const {page}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width:1280,height:900}});
  const banner=await page.locator('[data-operator-outcome]').innerText();
  assert.match(banner,/SYSTEM PROBLEM/);assert.match(banner,/never started/);assert.match(banner,/not the robot's fault/);
  assert.doesNotMatch(banner,/NOT COMPLETED|execution error|human check|attempts failed/);
  assert.match(await page.locator('.op-flow').innerText(),/Not started/);
  assert.match(await page.locator('.op-flow').innerText(),/System problem/);
  assert.doesNotMatch(await page.locator('.view').innerText(),/infrastructure|dispatch|DevConflict/);
});

// ---- found by a real New run on 8772: the AI is still writing the plan, the robot has not started ----
test('OPERATOR-POLISH preparing: while the AI writes the plan the strip says so and the stage is never treated as ended',async t=>{
  const snap=operatorSnapshot('dev1',true);
  snap.attempts=[];snap.traces={};snap.requests=[];snap.responses={};snap.attention_control.hint=undefined;
  snap.dev_session.state='running';snap.dev_session.jobs=[{kind:'model',state:'claimed',version:1}];snap.eval={status:'unavailable'};
  snap.station={status:'running',camera_url:'/camera-missing'};
  const {page}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width:1280,height:900},beforeNavigate:async page=>{await page.route('**/camera-missing',route=>route.fulfill({status:404,contentType:'application/json',body:'{}'}));}});
  const strip=page.locator('.op-live-strip');
  assert.equal(await strip.getAttribute('data-state'),'prep');
  assert.match(await strip.innerText(),/WORKING/);assert.match(await strip.innerText(),/AI is writing the robot's plan/);
  assert.doesNotMatch(await strip.innerText(),/not running right now/);
  const stage=page.locator('[data-op-stage="live"]');assert.equal(await stage.getAttribute('data-op-ended'),'false');
  await page.evaluate(()=>{document.querySelector('[data-op-stage="live"]').dataset.since=String(Date.now()-20000);});
  await loadingTick(page);
  assert.notEqual(await stage.getAttribute('data-state'),'error');
  assert.match(await page.locator('[data-op-stage="live"] .op-loading-text').innerText(),/AI is writing the robot's plan/);
});

// ---- reported from the real 8769 run: a policy change during a run was accepted (202) but the page gave no sign of it ----
for(const width of [1280,390])test(`OPERATOR-POLISH policy change during a run gives immediate, persistent feedback and sends exactly one request at ${width}px`,async t=>{
  const snap=operatorSnapshot('dev2',true);
  snap.run.status='running';snap.eval={status:'unavailable'};snap.station={status:'running'};snap.requests=[];snap.responses={};
  snap.attempts=snap.attempts.slice(0,1).map(a=>({...a,status:'failed'}));
  snap.dev_session.state='running';snap.dev_session.jobs=[{kind:'model',state:'claimed',version:2}];
  snap.dev_v2={...snap.dev_v2,key:'cell1',experiment_id:'experiment1',mode:'interactive',state:'running'};
  snap.attention_control={active_policy:'reactive_help',read_only:false,binding:{allowed_policies:['autonomous','demo_first','reactive_help','retry_k_then_ask']},segments:[],events:[]};
  const {page,state,tick}=await fixture(t,{operator:true,initialSnapshot:snap,styled:true,fullDocument:true,viewport:{width,height:900}});
  await page.locator('.op-strategy>summary').click();
  const list=page.locator('.op-policy-list');await list.waitFor();
  assert.equal(await page.locator('.op-policy-list>button').count(),4);
  assert.equal(await page.locator('[data-attention-policy="reactive_help"]').getAttribute('data-state'),'Active');
  assert.doesNotMatch(await page.locator('.op-policy').innerText(),/Policy controller|CONTROLLING DEV SESSION|Select\b|Live\b/);
  await page.locator('[data-attention-policy="autonomous"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-attention-policy="autonomous"]')?.dataset.state==='Requested');
  assert.equal(state.posts.filter(p=>p.pathname.endsWith('/switch')).length,1);
  const post=state.posts.find(p=>p.pathname.endsWith('/switch'));assert.equal(post.pathname,'/api/dev-v2/experiments/experiment1/switch');assert.deepEqual(post.payload,{cell:'cell1',policy:'autonomous'});
  assert.match(await page.locator('.op-policy-intro').innerText(),/Change requested: Never asks for help.*next attempt/);
  assert.match(await page.locator('.op-toast').innerText(),/Change requested/);
  assert.equal(await page.locator('.op-policy-list>button:not([disabled])').count(),0,'a pending request locks the other options, so repeated clicks cannot pile up');
  await tick();await tick();   // background refreshes must not wipe the pending state
  assert.equal(await page.locator('[data-attention-policy="autonomous"]').getAttribute('data-state'),'Requested');
  assert.equal(await page.locator('.op-policy-intro').getAttribute('data-state'),'requested');
  assert.equal(state.posts.filter(p=>p.pathname.endsWith('/switch')).length,1);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});

