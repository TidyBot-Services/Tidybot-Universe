const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/d5-agent-run.json'),'utf8'));
const functions=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const render=source.slice(source.indexOf('  function enterAgentWorkspace('),source.indexOf('  async function agentWorkspaceSnapshot('));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=vm.runInNewContext(functions+render+'\n({agentLaunchSelection,agentOptionGate,devPolicyChoices,runConfigDefaultsFromSelection,agentActivityCaption,agentTrialChips})',{esc});
const base={request_key:'unchanged-123',tasks:['open_cabinet'],seeds:[102],policies:['reactive_help'],mode:'experiment',help_budget:5,goal_key:'old-goal-123'};
const agent={channel:'dev_v2',tier:'dev'};

test('agent request is modern single cell and independently freezes model choices',()=>{
  const result=api.agentLaunchSelection(base,{...agent,devModel:'gpt-6-sol'});
  assert.equal(result.dev_mode,'agent');assert.equal(result.dev_model,'gpt-6-sol');assert.equal(result.eval_model,'gpt-6.1-sol');
  assert.equal(result.model_source,'codex');assert.equal(result.quota_stop_percent,90);assert.equal(result.goal_key,'old-goal-123');
  assert.ok(!('configuration' in result));assert.ok(!('eval_agent' in result));
  assert.equal(api.agentLaunchSelection(base,{...agent,evalModel:'gpt-6-sol',devModel:'gpt-6-astra'}).eval_model,'gpt-6-sol');
});
test('a server that cannot run Agent says so and refuses the launch',()=>{
  for(const changed of [{channel:'formal'},{tier:'formal'},{tier:'legacy'}]){
    assert.ok(api.agentOptionGate({...agent,...changed}));
    assert.throws(()=>api.agentLaunchSelection(base,{...agent,...changed}),/modern dev tier/);
  }
  assert.equal(api.agentOptionGate(agent),'');
});
test('New run lists the Agent policies only',()=>{
  assert.equal(JSON.stringify(api.devPolicyChoices({agent_policies:{policies:[{policy_id:'autonomous'},{policy_id:'reactive_help'}]},policies:['seven']})),JSON.stringify(['autonomous','reactive_help']));
  assert.equal(JSON.stringify(api.devPolicyChoices(undefined)),'[]');
  assert.doesNotMatch(source,/All seven/);
});
test('Run again restores agent models',()=>{
  const values=api.runConfigDefaultsFromSelection({...base,dev_mode:'agent',dev_model:'gpt-6-sol',eval_model:'gpt-6-astra'});
  assert.equal(values.devModel,'gpt-6-sol');assert.equal(values.evalModel,'gpt-6-astra');
});
test('agent facts fit the original workspace without raw sections',()=>{
  const activity=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-timeline-r6.json'))).versions[1].before_attempt;
  // 10-30: the settings/counter line was removed; activity captions remain.
  const html=api.agentActivityCaption(activity)+api.agentTrialChips(activity);
  for(const text of ['23 checks · 1 preview','1 trial run · gripper speed limit'])assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/<pre|\{&quot;/);
  assert.doesNotMatch(html,/data-agent-errors|data-agent-secondary|stdout/);
});
test('new controls are connected to actual launcher serialization and separate agent rendering',()=>{
  for(const name of ['${role}_model','quota_stop_percent'])assert.ok(source.includes(`name="${name}"`));
  assert.match(source,/selection=agentLaunchSelection\(selection,/);
  assert.match(source,/function updatePreview\(\) \{\s+fixtureRunMode\([^\n]+\);\s+const blocked=syncAgentOptions\(\)/);
  assert.match(source,/if\(cell.dev_mode==='agent'\)/);
  assert.match(source,/await devV2WorkspaceSnapshot\(session,compat,experiment\)/);
  assert.doesNotMatch(source,/view.innerHTML=agentRunPage/);
});

test('agent launch defaults the Advisor to gpt-6.1-sol; Run again keeps an old run on the fixed Advisor',()=>{
  assert.equal(api.agentLaunchSelection(base,agent).advisor_model,'gpt-6.1-sol');
  assert.equal(api.agentLaunchSelection(base,{...agent,advisorModel:'gpt-6-astra'}).advisor_model,'gpt-6-astra');
  assert.equal(api.runConfigDefaultsFromSelection({...base,dev_mode:'agent',dev_model:'gpt-6-astra',eval_model:'gpt-6-astra'}).advisorModel,'gpt-6-astra');
  assert.equal(api.runConfigDefaultsFromSelection({...base,dev_mode:'agent',advisor_model:'gpt-6.1-sol'}).advisorModel,'gpt-6.1-sol');
});
