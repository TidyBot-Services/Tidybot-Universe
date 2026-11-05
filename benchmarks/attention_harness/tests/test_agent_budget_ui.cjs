const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const functions=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=vm.runInNewContext(functions+'\n({agentBudgetCheck,agentLaunchSelection,runConfigDefaultsFromSelection,agentReasonText})',{esc});
const base={request_key:'k-1234567',tasks:['open_cabinet'],seeds:[102],policies:['reactive_help'],mode:'interactive',help_budget:5,goal_key:'goal-12345'};
const gate={channel:'dev_v2',tier:'dev'};

test('the Agent envelope: lower is allowed, higher is refused, the official budget is not a reduced budget',()=>{
  const official=api.agentBudgetCheck(10,21600);
  assert.deepEqual({...official,errors:{...official.errors}},{errors:{},valid:true,reduced:false,limits:null});
  assert.deepEqual({...api.agentBudgetCheck(2,7200).limits},{versions:2,wall_seconds:7200});
  assert.equal(api.agentBudgetCheck('2','7200').reduced,true);
  assert.equal(api.agentBudgetCheck(10,3600).reduced,true);
  assert.equal(api.agentBudgetCheck(1,21600).reduced,true);
  for(const [v,w,key] of [[11,21600,'versions'],[0,21600,'versions'],[2.5,21600,'versions'],['',21600,'versions'],[10,21601,'wall'],[10,3599,'wall'],[10,'x','wall']]){
    const result=api.agentBudgetCheck(v,w);
    assert.equal(result.valid,false,`${v}/${w}`);assert.ok(result.errors[key],`${v}/${w} ${key}`);assert.equal(result.limits,null);
  }
  assert.match(api.agentBudgetCheck(12,21600).errors.versions,/separate approval/);
  assert.match(api.agentBudgetCheck(10,28800).errors.wall,/6 hours/);
});

test('an official Agent run sends exactly what it sent before; only a reduced one adds agent_limits',()=>{
  const official=api.agentLaunchSelection(base,{...gate,limits:null});
  assert.ok(!('agent_limits' in official));
  assert.equal(JSON.stringify(official),JSON.stringify(api.agentLaunchSelection(base,gate)));
  const reduced=api.agentLaunchSelection(base,{...gate,limits:{versions:2,wall_seconds:7200}});
  assert.deepEqual({...reduced.agent_limits},{versions:2,wall_seconds:7200});
  assert.equal(reduced.wall_limit,21600); // unchanged legacy field
});

test('Run again restores a reduced budget',()=>{
  const repeat=api.runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[102],policies:['reactive_help'],agent_limits:{versions:3,wall_seconds:10800}});
  assert.deepEqual({...repeat.agentLimits},{versions:3,wall_seconds:10800});
  assert.ok(!('agentLimits' in api.runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[102],policies:['reactive_help']})));
});

test('05 starts at the official budget, only lowers, and a repaint leaves the edit alone',()=>{
  assert.match(source,/name="max_attempts" type="number" min="1" max="10" value="10" required/);
  assert.match(source,/name="wall_seconds" type="number" min="3600" max="21600" value="21600" required/);
  assert.match(source,/form\.max_attempts\.value='10';form\.wall_seconds\.value='21600';/);
  const fragment=source.slice(source.indexOf('      function syncAgentOptions()'),source.indexOf('      function updatePreview()'));
  const form={max_attempts:{value:'2'},wall_seconds:{value:'7200'},quota_stop_percent:{value:'90'},model_source:{value:'codex'}};
  const nodes={};form.querySelector=key=>nodes[key]??={textContent:'',hidden:false};
  const context={form,options:{dev_v2:{budget_tier:'dev'}},selectedProfile:()=>({channel:'dev_v2'}),keyMode:()=>'lab',agentReasonText:api.agentReasonText,agentOptionGate:()=>'',showQuota:()=>{}};
  vm.runInNewContext(fragment+'syncAgentOptions();',context);
  assert.equal(form.max_attempts.value,'2');assert.equal(form.wall_seconds.value,'7200');
  assert.match(nodes['[data-agent-reason]'].textContent,/^2 formal versions .* 2 hours /);
});

test('submit is blocked and the server is never asked for a budget above the envelope',()=>{
  assert.match(source,/if\(!budget\.valid\)throw new Error\(Object\.values\(budget\.errors\)\[0\]\)/);
  assert.match(source,/button\.disabled=submitting\|\|!profile\?\.enabled\|\|!budget\.valid/);
  assert.match(source,/limits:budget\.limits/);
});

test('04 has no Model source; 03 has one dropdown and a Billed to card; BYOK radios are still there',()=>{
  const reviewer=source.slice(source.indexOf('data-run-config-section="reviewer"'),source.indexOf('data-run-config-section="limits"'));
  assert.doesNotMatch(reviewer,/Model source|model_source|data-codex-quota/);
  const keys=source.slice(source.indexOf('  function keysSectionHtml()'),source.indexOf('  function keyTimeLeft('));
  assert.match(keys,/data-keys-source/);assert.match(keys,/data-bill/);assert.match(keys,/data-codex-quota/);
  assert.match(keys,/name="model_source" aria-label="Model source" hidden/);
  assert.match(keys,/<fieldset class="ab-keys-modes" hidden>/);
  for(const value of ['lab','all','different'])assert.match(keys,new RegExp(`name="keys_mode" value="${value}" data-keys-mode`));
  assert.match(keys,/<input name="quota_stop_percent"/); // the 90% moved out of the header
  assert.doesNotMatch(keys,/· Quota stop/);
  assert.match(source,/BYOK_ALL_ROLES = true/);
});

test('the dialog styles live in the run-dialog block and use theme tokens only',()=>{
  const block=/<style id="ab-run-dialog">([\s\S]*?)<\/style>/.exec(html)[1];
  for(const name of ['ab-source-row','ab-bill','ab-meter','ab-chip','ab-reduced','ab-limit-error'])assert.ok(block.includes('.'+name),name);
  const mine=block.slice(block.indexOf('Model source row (03)'));
  assert.doesNotMatch(mine,/#[0-9a-fA-F]{3,8}\b/);
});

test('the 03 summary describes whoever pays: no quota stop next to an own key, no hard-coded limits',()=>{
  const lab={versions:'2',wall:'7200',stop:'90',source:'codex'};
  assert.match(api.agentReasonText('lab',lab),/^2 formal versions .* 2 hours · stops at 90% quota\. Eval picks its own model/);
  assert.doesNotMatch(api.agentReasonText('lab',{...lab,source:'parcc'}),/quota/);
  for(const mode of ['all','different']){
    const text=api.agentReasonText(mode,lab);
    assert.match(text,/Limits are set in 05/);
    assert.doesNotMatch(text,/90%|stops at|formal versions|hours|Eval picks/);
  }
  assert.match(api.agentReasonText('all',lab),/own key; the lab quota does not apply/);
  assert.match(api.agentReasonText('different',lab),/Agent models below/);
});
