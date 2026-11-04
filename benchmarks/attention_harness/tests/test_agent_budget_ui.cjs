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
const base={request_key:'k-1234567',tasks:['open_cabinet'],seeds:[102],policies:['reactive_help'],mode:'interactive',help_budget:5,configuration:{goal_id:'g',max_iterations:5,wall_seconds:300}};
const gate={devMode:'agent',channel:'dev_v2',tier:'dev'};

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
  const oneCall=api.agentLaunchSelection(base,{devMode:'one_call',limits:{versions:2,wall_seconds:7200}});
  assert.ok(!('agent_limits' in oneCall));
});

test('Run again restores a reduced budget',()=>{
  const repeat=api.runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[102],policies:['reactive_help'],agent_limits:{versions:3,wall_seconds:10800}});
  assert.deepEqual({...repeat.agentLimits},{versions:3,wall_seconds:10800});
  assert.ok(!('agentLimits' in api.runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[102],policies:['reactive_help']})));
});

test('05 is editable in Agent mode, only downwards, and the default is set once on entering Agent mode',()=>{
  assert.doesNotMatch(source,/max_attempts\.readOnly=agent/);
  assert.match(source,/form\.max_attempts\.min='1';form\.max_attempts\.max='10';/);
  assert.match(source,/form\.wall_seconds\.min=agent\?'3600':'30';form\.wall_seconds\.max=agent\?'21600':'86400'/);
  const fragment=source.slice(source.indexOf('      function syncAgentOptions()'),source.indexOf('      function updatePreview()'));
  const field=value=>({value:String(value)});
  const form=Object.fromEntries(['dev_mode','dev_model','eval_model','model_source','eval_agent_enabled','max_attempts','wall_seconds','model_seconds_minutes','v2_policy','v2_mode','quota_stop_percent'].map(k=>[k,field('')]));
  const nodes={};form.querySelector=key=>nodes[key]??={textContent:'',hidden:false};form.dev_mode.querySelector=()=>({disabled:false});
  form.dev_mode.value='agent';form.v2_policy.options=[];form.v2_policy.value='reactive_help';form.v2_mode.value='interactive';form.quota_stop_percent.value='90';
  form.max_attempts.value='5';form.wall_seconds.value='7200';form.eval_agent_enabled.checked=false;
  const context={form,options:{dev_v2:{budget_tier:'dev',policies:['a']}},selectedProfile:()=>({channel:'dev_v2'}),forkStart:null,requestedDevMode:'agent',oneCallFields:null,
    launcherDevMode:(g,r)=>r,agentReasonText:api.agentReasonText,agentOptionGate:()=>'',devPolicyChoices:()=>[],fillOptions:()=>{},showQuota:()=>{}};
  vm.runInNewContext(fragment+'syncAgentOptions();',context);
  assert.equal(form.max_attempts.value,'10');assert.equal(form.wall_seconds.value,'21600'); // entering Agent mode: official budget
  assert.equal(form.max_attempts.readOnly,false);assert.equal(form.wall_seconds.readOnly,false);
  assert.equal(form.wall_seconds.min,'3600');assert.equal(form.wall_seconds.max,'21600');
  form.max_attempts.value='2';form.wall_seconds.value='7200';
  vm.runInNewContext('syncAgentOptions();',context); // any later input event: the edit must survive
  assert.equal(form.max_attempts.value,'2');assert.equal(form.wall_seconds.value,'7200');
  assert.match(nodes['[data-agent-reason]'].textContent,/^2 formal versions .* 2 hours /);
  context.requestedDevMode='one_call';vm.runInNewContext('syncAgentOptions();',context);
  assert.equal(form.max_attempts.value,'5');assert.equal(form.wall_seconds.value,'7200'); // restored from before Agent mode
  assert.equal(form.wall_seconds.max,'86400');assert.equal(form.wall_seconds.min,'30');
});

test('submit is blocked and the server is never asked for a budget above the envelope',()=>{
  assert.match(source,/if\(form\.dev_mode\.value==='agent'&&!budget\.valid\)throw new Error\(Object\.values\(budget\.errors\)\[0\]\)/);
  assert.match(source,/button\.disabled=submitting\|\|!profile\?\.enabled\|\|!budget\.valid/);
  assert.match(source,/limits:form\.dev_mode\.value==='agent'\?budget\.limits:null/);
});

test('04 has no Model source; 03 has one dropdown and a Billed to card; BYOK radios are still there',()=>{
  const reviewer=source.slice(source.indexOf('data-run-config-section="reviewer"'),source.indexOf('data-run-config-section="limits"'));
  assert.doesNotMatch(reviewer,/Model source|model_source|data-codex-quota/);
  const keys=source.slice(source.indexOf('  function keysSectionHtml()'),source.indexOf('  function keyTimeLeft('));
  assert.match(keys,/data-keys-source/);assert.match(keys,/data-bill/);assert.match(keys,/data-codex-quota/);
  assert.match(keys,/name="model_source" aria-label="Model source" hidden/);
  assert.match(keys,/<fieldset class="ab-keys-modes" hidden>/);
  for(const value of ['lab','all','different'])assert.match(keys,new RegExp(`name="keys_mode" value="${value}" data-keys-mode`));
  assert.match(keys,/Dev mode: <output data-dev-mode-label>Agent<\/output><input name="quota_stop_percent"/); // the 90% moved out of the header
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
  assert.match(api.agentReasonText(true,'lab',lab),/^2 formal versions .* 2 hours · stops at 90% quota\. Eval picks its own model/);
  assert.doesNotMatch(api.agentReasonText(true,'lab',{...lab,source:'parcc'}),/quota/);
  for(const mode of ['all','different']){
    const text=api.agentReasonText(true,mode,lab);
    assert.match(text,/Limits are set in 05/);
    assert.doesNotMatch(text,/90%|stops at|formal versions|hours|Eval picks/);
  }
  assert.match(api.agentReasonText(true,'all',lab),/own key; the lab quota does not apply/);
  assert.match(api.agentReasonText(true,'different',lab),/Agent models below/);
  assert.match(api.agentReasonText(false,'all',lab),/^One call keeps/);
});
