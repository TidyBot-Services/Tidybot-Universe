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
    '({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar,workspaceHTML,agentWorkspaceSnapshot,drawAgentInstrument,devFile,readDevEvidence,attentionLedger,attentionStripModel,attentionEfficiency,agentTimelineDetail,agentFullStory,bindAgentStory,bindAttentionEfficiency,bindHistorySelection,selectAgentAttempt,agentActivityCaption,devNavigationRecord,sidebarRunBadges,enterAgentWorkspace})');
const {rendererAPI}=vm.runInNewContext(helper+'\n({rendererAPI})',{source:source.replace('  function render(snapshot) {','  function renderFullWorkspace(snapshot) {'),vm,URL,URLSearchParams});
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
  // Parity with one-call: views that have a video carry no extra still frames.
  assert.ok(!trace.evidence.some(e=>e.kind==='recording_frame'));
  assert.ok(trace.evidence.some(e=>e.kind==='advisor_replay_side'));
  assert.equal(trace.recording.replay_timing.frame_video_seconds[0],0);
});
test('agent keeps five regions and selected-attempt evidence without duplicate activity rows',async()=>{
  const {snapshot}=await agentSnapshot();
  const {api}=apiFor(snapshot,{sessionStorage:{getItem:key=>key.startsWith('attention.history:')?snapshot.attempts[0].attempt_id:null}});
  const html=api.workspaceHTML(snapshot,null);
  for(let n=1;n<=5;n++)assert.equal((html.match(new RegExp(`class="op-number">0${n}<`,'g'))||[]).length,1);
  for(const selector of ['data-agent-settings','data-agent-story-open','data-agent-story','data-agent-attempt-details','data-agent-code','data-agent-citations','data-agent-help-refusal','data-instrument-canvas','data-history-replay'])assert.ok(html.includes(selector),selector);
  for(const text of ['versions 3/10','executions 4/40','probes 2/300','quota stop 90%','fatal_cause: broker_invariant','Approach was rejected','Try a smaller offset.'])assert.ok(html.includes(text),text);
  assert.doesNotMatch(html,/data-agent-version=|data-agent-secondary|data-agent-errors|v3 · Execution instruments/);
  assert.ok(!html.includes('Saved static frame'));
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
test('one intended one-call visual change: P3 PLAN row replaces revision; every other golden byte unchanged',()=>{
  const {api}=apiFor(golden.snapshot);
  const html=api.workspaceHTML(golden.snapshot,null);
  const expectedPlan='<details class="op-plan" data-plan-row data-persist-detail="operator-plan:1"><summary><b>PLAN</b><span>Use smaller movements.</span></summary><div data-plan-note>Use smaller movements.</div></details>';
  assert.ok(html.includes(expectedPlan));
  assert.doesNotMatch(html,/What changed in this attempt|class="op-revision"/);
  const oldRevision='<details class="op-revision" data-persist-detail="operator-revision:1"><summary>What changed in this attempt (version 1)</summary><div class="op-clamp six" data-operator-copy>Use smaller movements.</div><button type="button" class="op-more" data-operator-expand>Show more</button></details>';
  assert.ok(golden.html.includes(oldRevision));
  assert.equal(html.replace(expectedPlan,''),golden.html.replace(oldRevision,''));
  assert.match(html,/<div class="ab-history-events"[^>]*><details class="op-plan"/);
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
  for(const text of ['Completed on attempt 3 of 10','on attempt 3 of 10','274.8 s','corrected; recorded 1231.96 s','1 delivered and charged, 1 refused and not charged','1 of 1 delivered','not delivered'])assert.ok(html.includes(text),text);
  assert.match(html,/After attempt 1<\/b><small> · refused \(evaluation-derived text rejected at Dev boundary\)/);
  assert.match(html,/After attempt 2<\/b><small>Answered( in [^<]+)?<\/small>/);
  assert.ok(!html.includes('all undefined cells') && html.includes('This run has ended. The help the robot received is listed below.'));
  assert.doesNotMatch(html,/Closed without a reply|closed before anyone|After attempt 3<\/b>|of 40|Reviews requested/);
  const resources=api.resourceDetails(snapshot);
  for(const [name,used,limit] of [['Versions',3,10],['Executions',4,40],['Probes',2,300],['Help',1,1],['Eval',3,10]]){
    assert.match(resources,new RegExp(`<span>${name}</span><b><em[^>]*>${used}</em> <small>/ ${limit}</small>`));
  }
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

const r6=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/agent-timeline-r6.json')));
async function timelineSnapshot() {
  const {snapshot}=await agentSnapshot();
  snapshot.dev_session.state='completed';snapshot.dev_session.reason='native_success';
  snapshot.dev_session.versions.forEach((v,i)=>Object.assign(v,r6.versions[i]));
  snapshot.requests.forEach((r,i)=>{r.agent_help=r6.versions[i+1].help_content;});
  return snapshot;
}
function memoryStorage() {
  const values=new Map();
  return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
}
function visibleText(html) {return html.replace(/<[^>]*>/g,' ');}
test('r6 timeline shows request-sequence dots, short verdicts and clean selected detail cards',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  let html=api.attentionEfficiency(snapshot);
  assert.match(html,/Click a help request/);
  assert.match(html,/>Start<\/text>/);
  assert.equal((html.match(/<circle class="op-agent-trial grey"/g)||[]).length,3);
  assert.equal((html.match(/<circle class="op-agent-trial red"/g)||[]).length,2);
  for(const text of ['18 checks · 2 previews','23 checks · 1 preview','20 checks · 1 preview','grasp not confirmed','out of reach','solved','Trial run, kept going','Trial run stopped by a safety limit'])assert.ok(html.includes(text),text);
  assert.match(html,/op-attn-help automatic dashed/);
  api.selectAgentAttempt(snapshot,snapshot.attempts[1].attempt_id);
  const card=api.agentTimelineDetail(snapshot,null);
  for(const text of ['Attempt 2','Not solved · Eval fail','Out of reach','Before this attempt','help not delivered','23 checks · 1 preview','gripper speed limit'])assert.ok(card.includes(text),text);
  assert.doesNotMatch(card,/<button|e000|v000|stdout|\.json|RuntimeError|Details \(/);
  assert.match(card,/op-agent-chip refused/);
  assert.ok(!card.includes(r6.versions[1].story.sentence));
  assert.doesNotMatch(card,/<p>/);
  sessionStorage.setItem(`attention.efficiency.help:${snapshot.run.run_id}`,snapshot.requests[1].request_id);
  const help=api.agentTimelineDetail(snapshot,api.attentionLedger(snapshot).rows[1]);
  assert.match(help,/Automatic advisor · delivered/);assert.match(help,/274.8 s/);assert.match(help,/Full reply/);
  assert.ok(help.includes('banana'));
  const refused=api.agentTimelineDetail(snapshot,api.attentionLedger(snapshot).rows[0]);
  assert.match(refused,/refused before reaching the advisor/);assert.match(refused,/0 s · not charged/);
});
test('02 attempt click and 03 replay click share selection and clear a selected help',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage();
  const clicks=new Map(),circle={dataset:{attnWatch:snapshot.attempts[1].attempt_id},addEventListener:(name,fn)=>clicks.set('circle:'+name,fn)};
  const tab={dataset:{historySelect:snapshot.attempts[0].attempt_id},addEventListener:(name,fn)=>clicks.set('tab:'+name,fn)};
  const panel={querySelector:()=>null,querySelectorAll:selector=>selector==='[data-attn-watch]'?[circle]:[]};
  const view={querySelector:selector=>selector==='[data-attention-efficiency]'?panel:null,querySelectorAll:selector=>selector.startsWith('[data-history-select]')?[tab]:[]};
  let rendered=0;
  const {api}=apiFor(snapshot,{sessionStorage,view,render:()=>rendered++});
  api.bindAttentionEfficiency(snapshot);api.bindHistorySelection(snapshot);
  clicks.get('circle:click')();
  assert.equal(sessionStorage.getItem(`attention.history:${snapshot.run.run_id}`),snapshot.attempts[1].attempt_id);
  assert.match(api.executionViewer(snapshot),new RegExp(`data-history-select="${snapshot.attempts[1].attempt_id}" aria-pressed="true"`));
  sessionStorage.setItem(`attention.efficiency.help:${snapshot.run.run_id}`,snapshot.requests[1].request_id);
  clicks.get('tab:click')();
  assert.equal(sessionStorage.getItem(`attention.history:${snapshot.run.run_id}`),snapshot.attempts[0].attempt_id);
  assert.equal(sessionStorage.getItem(`attention.efficiency.help:${snapshot.run.run_id}`),null);
  assert.match(api.attentionEfficiency(snapshot),/op-attn-attempt failed selected" data-attn-attempt="1"/);
  assert.match(api.agentTimelineDetail(snapshot,null),/data-agent-attempt-card="1"/);
  assert.equal(rendered,2);
});
test('full story follows Start, explore, attempt, help, completion and closes without rendering the page',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),events=new Map();
  let focus=0,rendered=0;
  const close={addEventListener:(name,fn)=>events.set('close:'+name,fn)};
  const dialog={open:false,showModal(){this.open=true;},close(){this.open=false;},querySelector:()=>close,addEventListener:(name,fn)=>events.set(name,fn)};
  const entry={addEventListener:(name,fn)=>events.set('open:'+name,fn),focus(){focus++;}};
  const view={querySelector:selector=>selector==='[data-agent-story]'?dialog:entry};
  const {api}=apiFor(snapshot,{sessionStorage,view,render:()=>rendered++});
  api.selectAgentAttempt(snapshot,snapshot.attempts[1].attempt_id);
  const html=api.agentFullStory(snapshot),text=visibleText(html);
  assert.match(text,/Start[^]*Explore &amp; try[^]*Attempt 1[^]*Help #1[^]*Explore &amp; try[^]*Attempt 2[^]*Help #2[^]*Explore &amp; try[^]*Attempt 3[^]*Completed/);
  assert.doesNotMatch(text,/e000|v000|stdout|state_log|RuntimeError/);
  assert.match(html,/<dialog[^>]*aria-labelledby="agent-story-title"/);
  api.bindAgentStory(snapshot);events.get('open:click')();assert.equal(dialog.open,true);
  events.get('close:click')();assert.equal(dialog.open,false);
  events.get('open:click')();events.get('cancel')({preventDefault(){}});assert.equal(dialog.open,false);
  assert.equal(focus,2);assert.equal(rendered,0);
  assert.equal(sessionStorage.getItem(`attention.history:${snapshot.run.run_id}`),snapshot.attempts[1].attempt_id);
});
test('ten agent versions grow horizontally with the same timeline height and one selected replay detail',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  const original=api.attentionEfficiency(snapshot);
  const base=snapshot.attempts[0],version=snapshot.dev_session.versions[0];
  snapshot.attempts=Array.from({length:10},(_,i)=>({...base,attempt_id:'long:'+i,index:i,dev_version:i+1,status:i===9?'succeeded':'failed'}));
  snapshot.dev_session.versions=snapshot.attempts.map((a,i)=>({...version,version:i+1,version_id:`v${String(i+1).padStart(4,'0')}`,native_success:i===9}));
  snapshot.eval.native_outcomes={};
  const long=api.attentionEfficiency(snapshot),size=html=>html.match(/<svg viewBox="0 0 (\d+) (\d+)" style="width:(\d+)px;height:(\d+)px;min-width:/).slice(1).map(Number);
  assert.equal(size(original)[1],size(long)[1]);assert.equal(size(original)[3],size(long)[3]);assert.ok(size(long)[0]>size(original)[0]);
  assert.equal((long.match(/data-attn-attempt=/g)||[]).length,10);
  assert.match(long,/op-attn-scroll/);
  assert.equal(api.agentActivityCaption({checks:0,previews:1}),'1 preview');
  assert.equal(api.agentActivityCaption({checks:2,previews:0}),'2 checks');
  assert.equal(api.agentActivityCaption({checks:0,previews:0}),'');
});

test('r6 S3 strip follows each selected attempt under its video, with P3 plan first in steps',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  for(let i=0;i<3;i++){
    api.selectAgentAttempt(snapshot,snapshot.attempts[i].attempt_id);
    const html=api.executionViewer(snapshot),version=r6.versions[i];
    assert.equal((html.match(/data-eval-strip/g)||[]).length,1);
    assert.match(html,/<div class="ab-viewer-media">[^]*data-eval-strip[^]*<aside class="ab-viewer-trace">/);
    assert.match(html,/<div class="ab-history-events"[^>]*><details class="op-plan"/);
    assert.ok(html.includes(version.plan.summary.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')));
    assert.match(html,/<details class="op-plan"[^>]*><summary><b>PLAN<\/b>[^]*<div data-plan-note>/);
    assert.ok(version.plan.note.length>=version.plan.summary.length);
    assert.doesNotMatch(html,/What changed in this attempt|What went wrong \(objective\)|Reference suggestion \(for the advisor\)|Read objective reason|Read reference suggestion/);
    const strip=html.match(/<section class="op-eval-strip"[^]*?<\/section>/)[0];
    assert.doesNotMatch(strip,/stdout\.txt|trace\.json|state_log\.json/);
    if(i===2){assert.doesNotMatch(strip,/op-eval-stage failed|op-eval-stage unreached|Try next:/);assert.match(strip,/>Completed<\/span>/);}
    else{assert.equal((strip.match(/op-eval-stage failed/g)||[]).length,1);assert.match(strip,i===0?/✕ Close: grasp not confirmed/:/✕ Descent: no reachable path/);assert.match(strip,/Try next:/);}
    assert.match(html,/<details data-agent-attempt-details[^]*data-agent-instrument/);
  }
});
