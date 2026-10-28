// Real snapshot + five-region renderer; offline byte-backed HTTP fixtures only.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-workspace.json')));
const golden=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/one-call-workspace-golden.json')));
// Reuse the established renderer harness and its exact one-call fixture.
const harness=fs.readFileSync(path.join(__dirname,'test_dev_workspace.cjs'),'utf8');
const helper=harness.slice(harness.indexOf('function rendererAPI('),harness.indexOf("test('Current view keeps"))
  .replace('({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar})',
    '({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar,workspaceHTML,agentWorkspaceSnapshot,drawAgentInstrument,devFile,readDevEvidence,attentionLedger,attentionStripModel,attentionEfficiency,agentErrorDetails,devNavigationRecord,sidebarRunBadges,enterAgentWorkspace})');
const {rendererAPI}=vm.runInNewContext(helper+'\n({rendererAPI})',{source,vm,URL,URLSearchParams});
function apiFor(snapshot=null,overrides={}) {
  const calls=[];
  const fetch=async url=>{
    calls.push(url);
    const key='agent-view/'+decodeURIComponent(url.split('/agent/files/')[1]);
    const entry=fixture.files[key],bytes=entry&&Buffer.from(entry.base64,'base64');
    return {ok:!!entry,status:entry?200:404,json:async()=>JSON.parse(bytes),arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
  };
  return {calls,api:rendererAPI(snapshot,false,{fetch,crypto:webcrypto,TextDecoder,Uint8Array,
    CAMERA_VIEW_VIDEO:/^advisor_replay(_[a-z_]+)?\.mp4$/,devEvidenceCache:new Map(),v2MonitorIdentities:new Map(),...overrides})};
}
async function agentSnapshot() {
  const {api,calls}=apiFor();
  const cell=fixture.view;
  const s={session_id:'agent-fixture',experiment_id:cell.experiment_id,key:cell.key,channel:'dev_v2',
    model:cell.agent.dev_model,locked:{config:{suite:'robocasa365',task_id:cell.task_id,seed:cell.seed,robot:'OpenArm365',execution_arm:'both',model_source:'codex'}}};
  const snapshot=await api.agentWorkspaceSnapshot(s,cell,{state:cell.state,selection:{dev_mode:'agent'}});
  return {snapshot,calls};
}
test('agent snapshot uses all formal versions and verified original execution evidence',async()=>{
  const {snapshot,calls}=await agentSnapshot();
  assert.equal(snapshot.attempts.length,3);
  assert.equal(Object.keys(snapshot.traces).length,3);
  assert.equal(snapshot.dev_evidence_gaps.length,0);
  assert.equal(snapshot.eval.native_success,true);
  assert.equal(snapshot.station.camera_url,null);
  assert.equal(snapshot.station.camera_monitor_terminal,true);
  assert.equal(snapshot.requests.length,2);
  assert.ok(Object.values(snapshot.responses).some(r=>r.content==='Try a smaller offset.'));
  assert.ok(calls.every(url=>url.includes('/agent/files/')));
  const trace=Object.values(snapshot.traces)[0];
  assert.ok(trace.evidence.some(e=>e.kind==='recording_frame'&&e.media_url.includes('/frames/')));
  assert.ok(trace.evidence.some(e=>e.kind==='advisor_replay_side'));
  assert.equal(trace.recording.replay_timing.frame_video_seconds[0],0);
});
test('agent renders original five regions with compact facts, secondary activity, errors and version evidence',async()=>{
  const {snapshot}=await agentSnapshot();
  const {api}=apiFor(snapshot,{sessionStorage:{getItem:()=>snapshot.attempts[0].attempt_id}});
  const html=api.workspaceHTML(snapshot,null);
  for(let n=1;n<=5;n++)assert.equal((html.match(new RegExp(`class="op-number">0${n}<`,'g'))||[]).length,1);
  for(const selector of ['data-agent-settings','data-agent-secondary','data-agent-secondary-row','data-agent-errors','data-agent-versions','data-agent-code','data-agent-citations','data-agent-help-refusal','data-instrument-canvas','data-history-replay'])assert.ok(html.includes(selector),selector);
  for(const text of ['versions 3/10','executions 4/40','probes 2/300','quota stop 90%','fatal_cause: broker_invariant','1 no-eval executions','1 ik_check','Blocked approach','Approach was rejected','Try a smaller offset.'])assert.ok(html.includes(text),text);
  assert.equal((html.match(/data-agent-version="/g)||[]).length,3);
  assert.match(html,/<details[^>]*><summary>controller · broker_invariant/);
  assert.doesNotMatch(html,/&quot;outcome&quot;|&quot;diagnostics&quot;|data-agent-timeline|\/home\/|\/private\//);
  assert.match(html,/<details data-agent-code[^>]*><summary>Code[^]*?<pre>/);
  assert.match(html,/\/report"[^>]*>Download agent report \(JSON\)/);
  assert.doesNotMatch(html,/\/export.csv/);
});
test('instrument canvas consumes persisted samples and distinguishes absent data',()=>{
  const {api}=apiFor(),ops=[];
  const context=new Proxy({}, {get:(o,k)=>o[k]??((...args)=>ops.push([k,...args])),set:(o,k,v)=>(o[k]=v,true)});
  const canvas={width:800,height:220,getContext:()=>context};
  assert.equal(api.drawAgentInstrument(canvas,[]),0);
  assert.equal(api.drawAgentInstrument(canvas,[{physics_step:1,qpos:[0,.1]},{physics_step:2,qpos:[.1,.2]}]),2);
  assert.ok(ops.some(([name])=>name==='lineTo'));
});
test('one-call five-region HTML remains byte-identical to fd972d6 existing fixture',()=>{
  const {api}=apiFor(golden.snapshot);
  assert.equal(api.workspaceHTML(golden.snapshot,null),golden.html);
});
test('agent virtual references reject traversal; shared digest check rejects modified evidence',async()=>{
  const {snapshot}=await agentSnapshot();
  const {api}=apiFor(snapshot,{fetch:async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode('{}').buffer})});
  assert.equal(api.devFile(snapshot.dev_session,'agent-view/../private/native_result.json'),null);
  await assert.rejects(()=>api.readDevEvidence(snapshot.dev_session,snapshot.dev_session.versions[0].bundle),/digest mismatch/);
});
test('agent help uses two post-attempt boundaries, refused vs delivered, and persisted corrected attention',async()=>{
  const {snapshot}=await agentSnapshot();
  snapshot.run.status='completed';snapshot.dev_session.state='completed';snapshot.dev_session.reason='native_success';
  const {api}=apiFor(snapshot);
  const [refused,answered]=snapshot.requests;
  assert.equal(refused.attempt_id,snapshot.attempts[0].attempt_id);
  assert.equal(refused.state,'rejected');assert.equal(refused.response_id,null);
  assert.equal(answered.attempt_id,snapshot.attempts[1].attempt_id);
  assert.equal(answered.state,'answered');
  const ledger=api.attentionLedger(snapshot),strip=api.attentionStripModel(ledger);
  assert.equal(ledger.requests,2);assert.equal(ledger.hits,1);assert.equal(ledger.judged,1);
  assert.equal(ledger.maximum,10);assert.equal(ledger.humanSeconds,274.818);
  assert.deepEqual(Array.from(strip.requests,r=>r.after),[0,1]);
  assert.equal(ledger.rows[0].seconds,0);assert.equal(ledger.rows[0].effect,'no_reply');
  const html=api.workspaceHTML(snapshot,null);
  for(const text of ['Completed on attempt 3 of 10','on attempt 3 of 10','274.8 s','corrected; recorded 1231.96 s','1 delivered and charged, 1 refused and not charged','1 of 1 delivered','not delivered','Attempt 1 · Not solved · Eval: fail'])assert.ok(html.includes(text),text);
  assert.match(html,/After attempt 1<\/b><small> · refused \(evaluation-derived text rejected at Dev boundary\)/);
  assert.match(html,/After attempt 2<\/b><small> · answered/);
  assert.doesNotMatch(html,/Closed without a reply|closed before anyone|After attempt 3<\/b>|of 40|Reviews requested/);
  const resources=api.resourceDetails(snapshot);
  for(const [name,used,limit] of [['Versions',3,10],['Executions',4,40],['Probes',2,300],['Help',1,1],['Eval',3,10]]){
    assert.match(resources,new RegExp(`<span>${name}</span><b><em[^>]*>${used}</em> <small>/ ${limit}</small>`));
  }
});
test('agent diagnostics show outcome failures once with routine entries inside one collapsed Details control',()=>{
  const {api}=apiFor();
  const html=api.agentErrorDetails({failure_errors:[{source:'e000003 action',error:{error:'Grasp failed'},reports:[{source:'execution controller',error:'fatal_sdk_error'}]}],details:[{source:'e000002 action',error:{status:'rejected'}},{source:'eval/v0001/stderr',error:'Reading additional input from stdin…'},{source:'help',error:'public_view_unavailable'}]});
  assert.equal((html.match(/<summary>e000003 action/g)||[]).length,1);
  assert.match(html,/<details data-agent-error-details data-persist-detail="agent-error-details"><summary>Details \(3\)/);
  assert.ok(html.indexOf('status: rejected')>html.indexOf('Details (3)'));
  assert.doesNotMatch(html,/<details[^>]* open|<pre>|&quot;/);
});
test('agent desktop entry restores TASKS layout and formal-version card; subsequent polls preserve collapse',async()=>{
  const {snapshot}=await agentSnapshot();
  snapshot.dev_session.state='completed';snapshot.dev_session.reason='native_success';
  const session={...snapshot.dev_session,dev_mode:'agent',agent:undefined,agent_resources:{used:{formal_versions:3}},used:{executions:0},budget:{executions:10}};
  const control={remove(){},querySelectorAll(){return []}},nav={scrollTop:0,before(){}},search={value:'',addEventListener(){}};
  const sidebar={hidden:true,innerHTML:'',querySelectorAll(){return []},querySelector(selector){return selector==='nav'?nav:selector==='[data-run-search]'?search:selector==='[data-policy-controller]'?control:selector==='.ab-executing-runs'?{}:null;}};
  const workspace={dataset:{},querySelector:()=>null},toggle={querySelector:()=>null,setAttribute(){}};
  const overrides={sidebar,sidebarOpen:false,workspace,sidebarToggle:toggle,matchMedia:()=>({matches:false}),view:{querySelector:()=>null},document:{activeElement:null},runningStatus:{querySelectorAll:()=>[]},runs:[],devSessions:[session],selectedDev:session.session_id,selectedRun:session.session_id,sidebarQuery:'',sidebarFilter:'all',sidebarMessage:'',historyNotice:null,switchRequests:new Map(),pins:new Set(),taskGroupChoices:new Map(),policyControllerElement:null};
  const {api}=apiFor(null,overrides);
  api.enterAgentWorkspace(snapshot);assert.equal(sidebar.hidden,false);assert.equal(workspace.dataset.sidebarOpen,'true');
  api.renderSidebar();assert.match(sidebar.innerHTML,/<h2>Tasks<\/h2>/);assert.match(sidebar.innerHTML,/3\/10 versions/);assert.match(sidebar.innerHTML,/>Solved<\/span>/);
  const record=api.devNavigationRecord(session);assert.equal(record.attempts_used,3);assert.equal(record.attempts_limit,10);
  const oneCall=api.devNavigationRecord({...session,dev_mode:'one_call',used:{executions:2},budget:{executions:7}});
  assert.match(api.sidebarRunBadges(oneCall),/>2\/7 attempts<\/span>/);
  sidebar.hidden=true;workspace.dataset.sidebarOpen='false';
  apiFor(snapshot,overrides).api.enterAgentWorkspace(snapshot);
  assert.equal(sidebar.hidden,true);assert.equal(workspace.dataset.sidebarOpen,'false');
});
