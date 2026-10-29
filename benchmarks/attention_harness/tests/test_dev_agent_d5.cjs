const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/d5-agent-run.json'),'utf8'));
const functions=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const render=source.slice(source.indexOf('  function agentSettingsLine('),source.indexOf('  async function agentWorkspaceSnapshot('));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=vm.runInNewContext(functions+render+'\n({launcherDevMode,agentLaunchSelection,agentOptionGate,runConfigDefaultsFromSelection,agentSettingsLine,agentActivityCaption,agentTrialChips})',{esc});
const old={eval_agent:{enabled:true,model:'parcc/Qwen'},request_key:'unchanged-123',tasks:['open_cabinet'],seeds:[102],policies:['reactive_help'],mode:'experiment',help_budget:5,human_wait_seconds:300,configuration:{goal_id:'old-goal-123',experiment_budget:{executions:5},cell_budget:{executions:5},max_iterations:5,wall_seconds:21600}};

test('absent mode and explicit one-call keep original body bytes and identity',()=>{
  for(const values of [{},{devMode:'one_call',devModel:'gpt-6-sol'}]){
    const result=api.agentLaunchSelection(old,values);
    assert.equal(result,old);
    assert.equal(JSON.stringify({selection:result,start:true}),JSON.stringify({selection:old,start:true}));
    assert.ok(!('dev_mode' in result));
  }
});
test('agent request is modern single cell and independently freezes model choices',()=>{
  const values={devMode:'agent',channel:'dev_v2',tier:'dev',devModel:'gpt-6-sol'};
  const result=api.agentLaunchSelection(old,values);
  assert.equal(result.dev_mode,'agent');assert.equal(result.dev_model,'gpt-6-sol');assert.equal(result.eval_model,'gpt-6.1-sol');
  assert.equal(result.model_source,'codex');assert.equal(result.quota_stop_percent,90);assert.equal(result.goal_key,'old-goal-123');
  assert.ok(!('configuration' in result));assert.ok(!('eval_agent' in result));
  assert.equal(api.agentLaunchSelection(old,{...values,evalModel:'gpt-6-sol',devModel:'gpt-6-astra'}).eval_model,'gpt-6-sol');
  assert.ok(old.configuration);
});
test('formal, legacy, multiple tasks/seeds/policies and fork are gated with reasons',()=>{
  for(const changed of [{channel:'formal'},{tier:'formal'},{tier:'legacy'},{policyCount:7},{taskCount:2},{seedCount:2},{fork:true}]){
    assert.ok(api.agentOptionGate({channel:'dev_v2',tier:'dev',...changed}));
  }
  assert.equal(api.agentOptionGate({channel:'dev_v2',tier:'dev'}),'');
  for(const field of ['tasks','seeds','policies'])assert.throws(()=>api.agentLaunchSelection({...old,[field]:['a','b']},{devMode:'agent',channel:'dev_v2',tier:'dev'}),/单 cell/);
});
test('Run again restores agent models, old selection gains no agent defaults',()=>{
  const values=api.runConfigDefaultsFromSelection({...old,dev_mode:'agent',dev_model:'gpt-6-sol',eval_model:'gpt-6-astra'});
  assert.equal(values.devMode,'agent');assert.equal(values.devModel,'gpt-6-sol');assert.equal(values.evalModel,'gpt-6-astra');
  assert.equal(api.runConfigDefaultsFromSelection(old).devMode,'one_call');
});
test('agent facts fit the original workspace without raw sections',()=>{
  const activity=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-timeline-r6.json'))).versions[1].before_attempt;
  const html=api.agentSettingsLine(fixture.agent)+api.agentActivityCaption(activity)+api.agentTrialChips(activity);
  for(const text of ['versions 3/10','executions 4/40','probes 2/300','wall 0:00/6:00','gpt-6-sol','gpt-6-astra','23 checks · 1 preview','1 trial run · gripper speed limit'])assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/<pre|\{&quot;/);
  const fatal={...fixture.agent,fatal_cause:'broker_invariant',details:[{source:'Eval',error:'Exact timeout: <script>alert(1)</script>'}]};
  assert.match(api.agentSettingsLine(fatal),/fatal_cause: broker_invariant/);
  assert.doesNotMatch(html,/data-agent-errors|data-agent-secondary|stdout/);
});
test('new controls are connected to actual launcher serialization and separate agent rendering',()=>{
  for(const name of ['dev_mode','dev_model','eval_model','quota_stop_percent'])assert.ok(source.includes(`name="${name}"`));
  assert.match(source,/selection=agentLaunchSelection\(selection,/);
  assert.match(source,/function updatePreview\(\) \{\s+syncAgentOptions\(\)/);
  assert.match(source,/if\(cell.dev_mode==='agent'\)/);
  assert.match(source,/await devV2WorkspaceSnapshot\(session,compat,experiment\)/);
  assert.doesNotMatch(source,/view.innerHTML=agentRunPage/);
});

test('actual dialog gates disable unsupported mode and retain independent model choices',()=>{
  const fragment=source.slice(source.indexOf('      function syncAgentOptions()'),source.indexOf('      function updatePreview()'));
  const fields=Object.fromEntries(['dev_mode','dev_model','eval_model','model_source','eval_agent_enabled','max_attempts','wall_seconds','model_seconds_minutes','v2_policy','v2_mode'].map(k=>[k,{value:''}]));
  const nodes={};fields.querySelector=key=>nodes[key]??=( {textContent:'',hidden:false} );
  const agentOption={disabled:false};fields.dev_mode.querySelector=()=>agentOption;
  fields.dev_mode.value='agent';fields.dev_model.value='gpt-6-sol';fields.eval_model.value='gpt-6-astra';
  fields.v2_policy.value='reactive_help';fields.v2_mode.value='interactive';
  const context={form:fields,options:{dev_v2:{budget_tier:'dev',policies:['a','b']}},selectedProfile:()=>({channel:'dev_v2'}),forkStart:null,requestedDevMode:'agent',oneCallFields:null,launcherDevMode:api.launcherDevMode,agentOptionGate:api.agentOptionGate,showQuota:()=>{}};
  vm.runInNewContext(fragment+'\nsyncAgentOptions();',context);
  assert.equal(fields.dev_mode.disabled,false);assert.equal(fields.model_source.value,'codex');assert.equal(fields.model_source.disabled,true);
  assert.equal(fields.dev_model.value,'gpt-6-sol');assert.equal(fields.eval_model.value,'gpt-6-astra');
  assert.equal(fields.max_attempts.value,'10');assert.equal(fields.wall_seconds.value,'21600');
  assert.ok(nodes['[data-agent-reason]'].textContent.includes('300'));
  fields.v2_policy.value='all';vm.runInNewContext('syncAgentOptions();',context);
  assert.equal(fields.dev_mode.value,'one_call');assert.equal(fields.dev_mode.disabled,true);assert.equal(agentOption.disabled,true);
  assert.ok(nodes['[data-agent-reason]'].textContent.includes('矩阵'));
});

test('fresh dev single cell defaults to Agent; Advanced one-call preserves form and request bytes',()=>{
  assert.equal(api.launcherDevMode({channel:'dev_v2',tier:'dev'}),'agent');
  const advanced=source.slice(source.indexOf('<details class="ab-launch-section ab-run-advanced"'),source.indexOf('const form = launchDialog'));
  assert.match(advanced,/name="dev_mode"[^]*value="one_call"/);
  assert.doesNotMatch(advanced.split('<summary>')[0],/ open/);
  const fragment=source.slice(source.indexOf('      function syncAgentOptions()'),source.indexOf('      function updatePreview()'));
  const form=Object.fromEntries(['dev_mode','dev_model','eval_model','model_source','eval_agent_enabled','max_attempts','wall_seconds','model_seconds_minutes','v2_policy','v2_mode'].map(k=>[k,{value:''}]));
  form.querySelector=()=>({});form.dev_mode.querySelector=()=>({});
  Object.assign(form.model_source,{value:'parcc'});form.eval_agent_enabled.checked=false;form.max_attempts.value='5';form.wall_seconds.value='7200';
  form.v2_policy.value='reactive_help';form.v2_mode.value='interactive';
  const before=JSON.stringify([form.model_source.value,form.eval_agent_enabled.checked,form.max_attempts.value,form.wall_seconds.value]);
  const context={form,options:{dev_v2:{budget_tier:'dev'}},selectedProfile:()=>({channel:'dev_v2'}),forkStart:null,requestedDevMode:'agent',oneCallFields:null,launcherDevMode:api.launcherDevMode,agentOptionGate:api.agentOptionGate,showQuota:()=>{}};
  vm.runInNewContext(fragment+'syncAgentOptions();',context);
  assert.equal(form.dev_mode.value,'agent');
  context.requestedDevMode='one_call';vm.runInNewContext('syncAgentOptions();',context);
  assert.equal(JSON.stringify([form.model_source.value,form.eval_agent_enabled.checked,form.max_attempts.value,form.wall_seconds.value]),before);
  assert.equal(JSON.stringify({selection:api.agentLaunchSelection(old,{devMode:form.dev_mode.value}),start:true}),JSON.stringify({selection:old,start:true}));
});
test('launcher falls back for gated entries and repeat preserves either mode and request',()=>{
  for(const change of [{channel:'formal'},{tier:'legacy'},{tier:'formal'},{taskCount:2},{seedCount:2},{policyCount:7},{fork:true}]){
    const gate={channel:'dev_v2',tier:'dev',...change};
    assert.equal(api.launcherDevMode(gate),'one_call');assert.ok(api.agentOptionGate(gate));
  }
  for(const devMode of ['agent','one_call']){
    const original=api.agentLaunchSelection(old,{devMode,channel:'dev_v2',tier:'dev'});
    const repeat=api.runConfigDefaultsFromSelection(original);
    assert.equal(repeat.devMode,devMode);
    const again=api.agentLaunchSelection(old,{...repeat,channel:'dev_v2',tier:'dev'});
    assert.equal(JSON.stringify({selection:again,start:true}),JSON.stringify({selection:original,start:true}));
  }
});

test('Advanced one-call serializes the actual submit form to the original request bytes',()=>{
  const begin=source.indexOf("          if(profile.channel==='dev_v2'){\n            syncBudget();");
  const end=source.indexOf("          if(form.name_suffix.value.trim()",begin);
  const serialize=source.slice(begin,end);
  const field=value=>({value:String(value)});
  const cell={executions:5},experiment={executions:5};
  const form={max_attempts:field(5),help_count:field(5),human_wait_seconds:field(300),wall_seconds:field(21600),
    goal_id:field('old-goal-123'),model_source:field('parcc'),eval_agent_enabled:{checked:true},
    seed:field(102),v2_policy:field('reactive_help'),v2_mode:field('experiment'),dev_mode:field('one_call'),dev_model:field('gpt-6-astra'),eval_model:field('gpt-6-astra')};
  form.querySelectorAll=selector=>Object.entries(selector==='[data-v2-cell]'?cell:experiment).map(([key,value])=>({value,dataset:selector==='[data-v2-cell]'?{v2Cell:key}:{v2Experiment:key}}));
  const context={form,profile:{channel:'dev_v2',task_id:'open_cabinet'},options:{dev_v2:{budget_tier:'dev'}},fork:null,
    requestKey:'unchanged-123',syncBudget:()=>{},selection:null,agentLaunchSelection:api.agentLaunchSelection};
  vm.runInNewContext(serialize,context);
  assert.equal(JSON.stringify({selection:context.selection,start:true}),JSON.stringify({selection:old,start:true}));
  for(const devMode of ['one_call','agent']){
    form.dev_mode.value=devMode;vm.runInNewContext(serialize,context);
    const initial=JSON.stringify({selection:context.selection,start:true});
    const repeat=api.runConfigDefaultsFromSelection(context.selection);
    form.dev_mode.value=repeat.devMode;form.dev_model.value=repeat.devModel;form.eval_model.value=repeat.evalModel;
    vm.runInNewContext(serialize,context);
    assert.equal(JSON.stringify({selection:context.selection,start:true}),initial);
  }
});
test('agent launch defaults the Advisor to gpt-6.1-sol; Run again keeps an old run on the fixed Advisor',()=>{
  const values={devMode:'agent',channel:'dev_v2',tier:'dev'};
  assert.equal(api.agentLaunchSelection(old,values).advisor_model,'gpt-6.1-sol');
  assert.equal(api.agentLaunchSelection(old,{...values,advisorModel:'gpt-6-astra'}).advisor_model,'gpt-6-astra');
  assert.equal(api.runConfigDefaultsFromSelection({...old,dev_mode:'agent',dev_model:'gpt-6-astra',eval_model:'gpt-6-astra'}).advisorModel,'gpt-6-astra');
  assert.equal(api.runConfigDefaultsFromSelection({...old,dev_mode:'agent',advisor_model:'gpt-6.1-sol'}).advisorModel,'gpt-6.1-sol');
  assert.ok(!('advisor_model' in api.agentLaunchSelection(old,{})));
});
