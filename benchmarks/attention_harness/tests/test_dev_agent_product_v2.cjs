const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-workspace.json')));
const v2=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-policy-v2-help.json')));
const harness=fs.readFileSync(path.join(__dirname,'test_dev_workspace.cjs'),'utf8');
const helper=harness.slice(harness.indexOf('function rendererAPI('),harness.indexOf("test('Current view keeps"))
  .replace('({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar})',
    '({agentWorkspaceSnapshot,workspaceHTML,agentHelpCard,agentPolicyHelp,attentionLedger,attentionStripModel,agentFullStory,devPolicyChoices,agentLaunchSelection,policyShort})');
const {rendererAPI}=vm.runInNewContext(helper+'\n({rendererAPI})',{source:source.replace('  function render(snapshot) {','  function renderFullWorkspace(snapshot) {'),vm,URL,URLSearchParams});
function apiFor(snapshot=null){
  return rendererAPI(snapshot,false,{crypto:webcrypto,TextDecoder,Uint8Array,
    CAMERA_VIEW_VIDEO:/^advisor_replay(_[a-z_]+)?\.mp4$/,devEvidenceCache:new Map(),v2MonitorIdentities:new Map(),
    fetch:async url=>{const entry=fixture.files['agent-view/'+decodeURIComponent(url.split('/agent/files/')[1])];
      const bytes=entry&&Buffer.from(entry.base64,'base64');return {ok:!!entry,status:entry?200:404,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};}});
}
async function snapshot(){
 const cell=structuredClone(fixture.view);
 Object.assign(cell.agent,{...v2});delete cell.agent.help_intervals;
 cell.dev_v2_compat.help_intervals=structuredClone(v2.help_intervals);
 const s={session_id:'v2-multiple-fixture',experiment_id:cell.experiment_id,key:cell.key,channel:'dev_v2',model:cell.agent.dev_model,
   locked:{config:{suite:'robocasa365',task_id:cell.task_id,seed:cell.seed,robot:'OpenArm365',execution_arm:'both',model_source:'codex'}}};
 return apiFor().agentWorkspaceSnapshot(s,cell,{state:cell.state,selection:{dev_mode:'agent'}});
}
test('v2 keeps multiple practice help requests, stable eid identities and charged failures',async()=>{
 const s=await snapshot(),api=apiFor(s),ledger=api.attentionLedger(s);
 assert.equal(s.requests.length,3);assert.equal(new Set(s.requests.map(r=>r.request_id)).size,3);
 assert.ok(s.requests[0].request_id.endsWith('help:e000001'));
 assert.equal(s.requests[0].attempt_id,undefined);assert.equal(s.requests[1].response_id,null);
 assert.equal(ledger.humanSeconds,69);assert.equal(ledger.rows.filter(r=>r.nextIndex===0).length,2);
 const strip=api.attentionStripModel(ledger);assert.equal(strip.requests.length,3);
 assert.equal(strip.requests.filter(r=>r.after===-1).length,2);
});
test('v2 help panel shows all required states and errors, including non-delivered actual fees',async()=>{
 const s=await snapshot(),api=apiFor(s),html=api.workspaceHTML(s,null);
 const visible=html.replace(/<[^>]*>/g,'');
 for(const text of ['e000001','e000002','e000003','Forced question','Agent initiated question','The agent asked for help','The attempt failed its review','Delivered','Not delivered','23.0 s','Must ask before next attempt','Submit held: ask first (no charge)','Asked automatically · before the next attempt','Automatic ask skipped: time limit reached (no charge)','DriveError: synthetic visible failure'])assert.ok(visible.includes(text),text);
 for(const code of ['agent_requested','eval_failed','pending_ask','ask_required','forced_fallback · next_submit','forced_fallback_blocked: wall_stop']){
   assert.ok(!visible.includes(code),code);
   assert.ok(html.includes(`title="${code}"`),code);
 }
 assert.match(html,/<small>e000004<\/small>/);
 assert.match(html,/data-agent-policy-errors/);
 const story=api.agentFullStory(s);assert.equal((story.match(/data-agent-help-id=/g)||[]).length,3);
});
test('policy dropdown keeps legacy seven and matrix; agent has eight and no matrix',()=>{
 const api=apiFor(),old=['autonomous','demo_first','reactive_help','retry_k_then_ask','budget_matched_random_escalation','trace_aware_hint_only','full_trace_aware_attention_planner'];
 const options={policies:old,agent_policies:{agent_policy_version:'agent-policy-v2',policies:[...old,'agent_initiated_help'].map(policy_id=>({policy_id,parameters:{}}))}};
 assert.deepEqual(Array.from(api.devPolicyChoices(options,'one_call')),['all',...old]);
 assert.deepEqual(Array.from(api.devPolicyChoices(options,'agent')),[...old,'agent_initiated_help']);
 const selection={tasks:['t'],seeds:[1],policies:['autonomous']};
 assert.equal(api.agentLaunchSelection(selection,{devMode:'one_call'}),selection);
 const created=api.agentLaunchSelection(selection,{devMode:'agent',channel:'dev_v2',tier:'dev'});
 assert.equal(created.dev_mode,'agent');assert.ok(!('agent_policy_version' in created)); // server supplies current default; no v1 UI entry.
});

test('v2 help reasons use readable text with original codes on hover',()=>{
 const api=apiFor(),reasons={
   stuck_within_version:'The same failure repeated in this version',
   same_failure_after_code_change:'The same failure continued after a code change',
   unchanged_repeated_failure:'Unchanged code repeated the previous failure',
   cause_unresolved:'The cause remains unresolved',
   request_after_each_failure:'Help is requested after each failed attempt',
   retry_threshold_reached:'The retry limit was reached',
   budget_matched_random_slot:'This attempt was selected for random help',
   trusted_memory_matches:'Saved guidance matches this situation',
   agent_requested:'The agent asked for help',
   eval_failed:'The attempt failed its review'
 };
 for(const [code,text] of Object.entries(reasons)){
   for(const fields of [{reason:code},{decision:{reason:code}}]){
     const html=api.agentHelpCard({agent_policy_version:'agent-policy-v2',...fields},1);
     assert.ok(html.includes(`title="${code}">${text}</span>`),code);
     assert.ok(!html.replace(/<[^>]*>/g,'').includes(code),code);
   }
 }
 const html=api.agentHelpCard({agent_policy_version:'agent-policy-v2',reason:'future_reason"<&'},1);
 assert.ok(html.includes('title="future_reason&quot;&lt;&amp;">future_reason&quot;&lt;&amp;</span>'));
});
test('v2 automatic ask notices cover success, blocked limits and unknown codes',async()=>{
 const s=await snapshot(),api=apiFor(s);
 s.agent.pending_ask={execution_id:'e000005',reason:'stuck_within_version'};
 s.agent.forced_fallback={execution_id:'e000005',fallback_trigger:'next_submit'};
 let html=api.agentPolicyHelp(s);
 assert.ok(html.includes('title="stuck_within_version">The same failure repeated in this version'));
 assert.ok(html.includes('title="forced_fallback · next_submit">Asked automatically · before the next attempt'));
 const blocked={
   wall_stop:'time limit reached',quota_stop:'quota limit reached',quota_unavailable:'quota unavailable',
   assistance_budget_exhausted:'help budget exhausted',public_view_unavailable:'the help context is unavailable',
   fatal:'a fatal error stopped the run',cancel:'the run was cancelled',pause:'the run was paused',
   future_limit:'future_limit'
 };
 for(const [code,text] of Object.entries(blocked)){
   s.agent.forced_fallback={execution_id:'e000005',forced_fallback_blocked:code};
   html=api.agentPolicyHelp(s);
   assert.ok(html.includes(`title="forced_fallback_blocked: ${code}">Automatic ask skipped: ${text} (no charge)`),code);
   const card=api.agentHelpCard({agent_policy_version:'agent-policy-v2',forced_fallback:true,forced_fallback_blocked:code},1);
   assert.ok(card.includes(`Automatic ask skipped: ${text} (no charge)`),code);
   assert.ok(!card.replace(/<[^>]*>/g,'').includes('Asked automatically'));
 }
 s.agent.pending_ask={execution_id:'e000005',reason:'future_reason'};
 assert.ok(api.agentPolicyHelp(s).includes('title="future_reason">future_reason</span>'));
});
test('policy cards have distinct short names for self-ask, hints and planner',()=>{
 const api=apiFor();
 for(const inPath of [false,true]){
   assert.equal(api.policyShort('agent_initiated_help',inPath),'Self-ask');
   assert.equal(api.policyShort('trace_aware_hint_only',inPath),'Hints');
   assert.equal(api.policyShort('full_trace_aware_attention_planner',inPath),'Planner');
 }
});
