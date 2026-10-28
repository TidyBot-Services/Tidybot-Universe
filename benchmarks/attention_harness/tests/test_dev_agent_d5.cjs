const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/d5-agent-run.json'),'utf8'));
const functions=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const render=source.slice(source.indexOf('  function agentRunPage('),source.indexOf('  async function agentWorkspaceSnapshot('));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=vm.runInNewContext(functions+render+'\n({agentLaunchSelection,agentOptionGate,runConfigDefaultsFromSelection,agentRunPage})',{esc});
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
  assert.equal(result.dev_mode,'agent');assert.equal(result.dev_model,'gpt-6-sol');assert.equal(result.eval_model,'gpt-6-astra');
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
  assert.equal(api.runConfigDefaultsFromSelection(old).devMode,undefined);
});
test('fixture renders counts, every timeline row, public evidence and agreement',()=>{
  const html=api.agentRunPage(fixture);
  for(const text of ['正式版本 3/10','执行 4/40','只读探查 2/300','墙钟 00:00/6:00','额度 72.5%',
    'native_success','gpt-6-sol','gpt-6-astra','no_eval_execution','formal_version','probe','Eval verdict + advice',
    'stdout','stderr','diagnostics','code','help','success','failure','agree','disagree','unavailable','Scan a smaller base offset.'])assert.ok(html.includes(text),text);
  assert.equal((html.match(/<details data-persist-detail=/g)||[]).length,fixture.agent.timeline.length);
  assert.ok(!html.includes('/private/'));assert.ok(!html.includes('/home/'));
});
test('all states, raw errors, held resources and stop reasons remain visible and escaped',()=>{
  for(const phase of ['turn running','waiting on execution','Eval','help','stopped']){
    const value=structuredClone(fixture);value.agent.phase=phase;value.agent.wall_seconds=3661;
    value.agent.held.moving_executions=1;
    for(const reason of ['native_success','quota_stop','agent_idle','budget_exhausted','wall_stop','fatal_error']){
      value.agent.reason=reason;value.agent.errors=[{source:'Eval',error:'Exact timeout: <script>alert(1)</script>'}];
      const html=api.agentRunPage(value);
      assert.ok(html.includes(phase)&&html.includes(reason));assert.ok(html.includes('01:01/6:00'));
      assert.ok(html.includes('executions 1'));assert.ok(html.includes('Exact timeout: &lt;script&gt;'));
      assert.ok(!html.includes('<script>'));
    }
  }
  const fatal=structuredClone(fixture);fatal.agent.phase='stopped';fatal.agent.reason='fatal_error';fatal.agent.fatal_cause='broker_invariant';
  assert.ok(api.agentRunPage(fatal).includes('fatal_error · cause broker_invariant'));
});
test('new controls are connected to actual launcher serialization and separate agent rendering',()=>{
  for(const name of ['dev_mode','dev_model','eval_model','quota_stop_percent'])assert.ok(source.includes(`name="${name}"`));
  assert.match(source,/selection=agentLaunchSelection\(selection,/);
  assert.match(source,/function updatePreview\(\) \{\s+syncAgentOptions\(\)/);
  assert.match(source,/if\(cell.dev_mode==='agent'\)/);
  assert.match(source,/view.innerHTML=agentRunPage\(snapshot.dev_v2\)/);
});

test('actual dialog gates disable unsupported mode and retain independent model choices',()=>{
  const fragment=source.slice(source.indexOf('      function syncAgentOptions()'),source.indexOf('      function updatePreview()'));
  const fields=Object.fromEntries(['dev_mode','dev_model','eval_model','model_source','eval_agent_enabled','max_attempts','wall_seconds','model_seconds_minutes','v2_policy','v2_mode'].map(k=>[k,{value:''}]));
  const nodes={};fields.querySelector=key=>nodes[key]??=( {textContent:'',hidden:false} );
  const agentOption={disabled:false};fields.dev_mode.querySelector=()=>agentOption;
  fields.dev_mode.value='agent';fields.dev_model.value='gpt-6-sol';fields.eval_model.value='gpt-6-astra';
  fields.v2_policy.value='reactive_help';fields.v2_mode.value='interactive';
  const context={form:fields,options:{dev_v2:{budget_tier:'dev',policies:['a','b']}},selectedProfile:()=>({channel:'dev_v2'}),forkStart:null,agentOptionGate:api.agentOptionGate,showQuota:()=>{}};
  vm.runInNewContext(fragment+'\nsyncAgentOptions();',context);
  assert.equal(fields.dev_mode.disabled,false);assert.equal(fields.model_source.value,'codex');assert.equal(fields.model_source.disabled,true);
  assert.equal(fields.dev_model.value,'gpt-6-sol');assert.equal(fields.eval_model.value,'gpt-6-astra');
  assert.equal(fields.max_attempts.value,'10');assert.equal(fields.wall_seconds.value,'21600');
  assert.ok(nodes['[data-agent-reason]'].textContent.includes('300'));
  fields.v2_policy.value='all';vm.runInNewContext('syncAgentOptions();',context);
  assert.equal(fields.dev_mode.value,'one_call');assert.equal(fields.dev_mode.disabled,true);assert.equal(agentOption.disabled,true);
  assert.ok(nodes['[data-agent-reason]'].textContent.includes('矩阵'));
});
