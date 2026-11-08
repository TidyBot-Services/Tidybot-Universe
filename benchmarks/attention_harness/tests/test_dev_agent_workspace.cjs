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
    '({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar,workspaceHTML,agentWorkspaceSnapshot,devFile,readDevEvidence,attentionLedger,attentionStripModel,attentionEfficiency,agentTimelineDetail,agentFullStory,bindAgentStory,bindAttentionEfficiency,bindHistorySelection,selectAgentAttempt,agentActivityCaption,devNavigationRecord,sidebarRunBadges,enterAgentWorkspace,agentReplayEvents,agentStripStages,operatorTitle,taskTitle,taskDisplayName,agentTrialLegend,sidebarRunCard,sidebarTaskSummary})');
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
  for(const selector of ['data-agent-story-open','data-agent-story','data-agent-help-refusal','data-history-replay'])assert.ok(html.includes(selector),selector);
  for(const text of ['hit an internal error and cannot continue on its own','(error code: broker_invariant)','Try a smaller offset.'])assert.ok(html.includes(text),text);
  // 10-30: no settings/counter line in 01 and no model subtitle in 02; 05 keeps the counters.
  assert.doesNotMatch(html,/data-agent-settings|quota stop|Codex subscription/);
  assert.doesNotMatch(html,/data-agent-version=|data-agent-secondary|data-agent-errors|v3 · Execution instruments/);
  assert.ok(!html.includes('Saved static frame'));
  assert.doesNotMatch(html,/&quot;outcome&quot;|&quot;diagnostics&quot;|data-agent-timeline|\/home\/|\/private\//);
  // 10-30: region 03 has no Attempt details (code, citations, instruments) any more.
  assert.doesNotMatch(html,/data-agent-attempt-details|data-agent-attempt-errors|data-agent-code|data-agent-citations|data-instrument-canvas|>Attempt details<|>Errors \(/);
  assert.match(html,/\/report"[^>]*>Download agent report \(JSON\)/);
  assert.doesNotMatch(html,/\/export.csv/);
});
test('one intended one-call visual change: P3 PLAN row replaces revision; every other golden byte unchanged',()=>{
  const {api}=apiFor(golden.snapshot);
  const html=api.workspaceHTML(golden.snapshot,null);
  const expectedPlan='<details class="op-plan" data-plan-row data-persist-detail="operator-plan:1"><summary><b>PLAN</b><span>Use smaller movements.</span></summary><div data-plan-note>Use smaller movements.</div></details>';
  assert.ok(html.includes(expectedPlan));
  assert.doesNotMatch(html,/What changed in this attempt|class="op-revision"/);
  const oldRevision='<details class="op-revision" data-persist-detail="operator-revision:1"><summary>What changed in this attempt (version 1)</summary><div class="op-clamp six" data-operator-copy>Use smaller movements.</div><button type="button" class="op-more" data-operator-expand>Show more</button></details>';
  assert.ok(golden.html.includes(oldRevision));
  // Second intended change (10-30): region headings drop their question line; 02 keeps only the model source.
  const heading=h=>h.replace(/(<\/h3><span>)[^<]*(<\/span>)/g,'$1$2');
  assert.doesNotMatch(html,/What is the robot trying to do\?|How is it going\?|where did it go wrong\?|Do I need to do anything\?|How much budget is left\?/);
  // Third intended change (10-30): the attention legend lists only the marks the run shows.
  const legendOf=h=>(h.match(/<div class="op-attn-legend">([^]*?)<\/div>/)||[])[1]||'';
  assert.doesNotMatch(legendOf(html),/Failed attempt|<i class="solved">|Bar under each help/);
  const noLegend=h=>h.replace(/<div class="op-attn-legend">[^]*?<\/div>/g,'');
  assert.equal(noLegend(heading(html.replace(expectedPlan,''))),noLegend(heading(golden.html.replace(oldRevision,''))));
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
  for(const text of ['Completed on attempt 3 of 10','on attempt 3 of 10','274.8 s','corrected; recorded 1231.96 s','1 of 1 delivered','not delivered'])assert.ok(html.includes(text),text);
  // 10-30: the "N delivered and charged, M refused" line is gone; corrections stay in the Human attention tooltip.
  assert.doesNotMatch(html,/delivered and charged|refused and not charged|data-agent-attention-detail/);
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
  // 10-30: the legend lists only marks this run shows.
  const legend=html.match(/<div class="op-attn-legend">([^]*?)<\/div>/)[1];
  assert.match(legend,/<i class="automatic"><\/i>Automatic advisor/);
  assert.doesNotMatch(legend,/Failed attempt|<i class="solved">|Person replied|Bar under each help/);
  assert.equal(api.agentTrialLegend([]),'');
  assert.doesNotMatch(api.agentTrialLegend([{color:'red'}]),/kept going/);
  api.selectAgentAttempt(snapshot,snapshot.attempts[1].attempt_id);
  const card=api.agentTimelineDetail(snapshot,null);
  for(const text of ['Attempt 2','Not solved · Eval fail','out of reach','Before this attempt','help not delivered','23 checks · 1 preview','gripper speed limit'])assert.ok(card.includes(text),text);
  assert.doesNotMatch(card,/<button|e000|v000|stdout|\.json|RuntimeError|Details \(/);
  assert.match(card,/op-agent-chip refused/);
  assert.ok(!card.includes(r6.versions[1].story.sentence));
  assert.doesNotMatch(card,/<p>/);
  sessionStorage.setItem(`attention.efficiency.help:${snapshot.run.run_id}`,snapshot.requests[1].request_id);
  const help=api.agentTimelineDetail(snapshot,api.attentionLedger(snapshot).rows[1]);
  assert.match(help,/Automatic advisor · delivered/);assert.match(help,/274.8 s/);assert.match(help,/Full reply/);
  // Delivered despite an invalid_usage bookkeeping error: never 'could not deliver'.
  assert.doesNotMatch(help,/could not deliver/);assert.match(help,/delivered a hint/);
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
    else{assert.equal((strip.match(/op-eval-stage failed/g)||[]).length,1);assert.match(strip,i===0?/Stopped at Close<\/b> · grasp not confirmed/:/Stopped at Descent<\/b> · out of reach/);assert.match(strip,/Try next:/);}
    assert.doesNotMatch(html,/data-agent-attempt-details|data-agent-instrument/);
  }
});

test('r6 terminal descent rejection becomes the last red step; continued rejections retain their status',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  const version=r6.versions[1],raw=r6.evidence[`executions/${version.execution_id}/trace.json`];
  const events=raw.map((e,i)=>({...e,event_id:`r6:${i}`,sequence:i,event_type:'sdk.call',operation:`${e.source}.${e.operation}`}));
  const mapped=api.agentReplayEvents(events,version);
  assert.equal(mapped.at(-1).status,'failed');
  assert.equal(mapped.at(-1).recorded_status,'rejected');
  assert.equal(mapped.at(-1).error.message,'official_ik_non_success');
  assert.equal(events.at(-1).status,'rejected','recorded evidence remains intact');
  const trace=Object.values(snapshot.traces).find(t=>t.attempt_id===snapshot.attempts[1].attempt_id);
  trace.events=mapped;
  api.selectAgentAttempt(snapshot,snapshot.attempts[1].attempt_id);
  const html=api.executionViewer(snapshot),steps=[...html.matchAll(/<details data-history-event[^]*?<\/details>/g)].map(m=>m[0]);
  assert.match(steps.at(-1),/class="op-step failed"/);
  // Same plain wording as one-call for a planning rejection.
  assert.match(steps.at(-1),/the motion planner refused the move \(out of reach or would collide\)/);
  assert.match(steps.at(-1),/✕[^]*Moved left arm a small step[^]*Stopped:/);
  assert.equal(steps.filter(s=>s.includes('op-step failed')).length,1);
  const continued=[...events,{operation:'left_arm.move_delta',status:'completed'}];
  assert.equal(api.agentReplayEvents(continued,version)[events.length-1].status,'rejected');
  const observed=[...events,{operation:'sensors.get_observation',status:'completed'}];
  assert.equal(api.agentReplayEvents(observed,version)[events.length-1].status,'failed');
  const nested=events.map((e,i)=>i===events.length-1?{...e,status:'completed'}:e);
  assert.equal(api.agentReplayEvents(nested,version).at(-1).status,'failed');
  assert.equal(api.agentReplayEvents(events,{native_success:true,evaluator_review:{passed:true}}).at(-1).status,'rejected');
});
test('every r6 strip shows all seven named stages, one chip each (10-30)',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  const names=['Find objects','Pre-grasp','Descent','Close','Lift','Carry','Release'];
  for(let i=0;i<3;i++){
    api.selectAgentAttempt(snapshot,snapshot.attempts[i].attempt_id);
    const version=r6.versions[i],stages=api.agentStripStages(version.eval_stages);
    assert.deepEqual(Array.from(stages.map(s=>s.label)),names);
    assert.deepEqual(Array.from(stages.flatMap(s=>s.stages)),[0,1,2,3,4,5,6]);
    const strip=api.executionViewer(snapshot).match(/<section class="op-eval-strip"[^]*?<\/section>/)[0];
    assert.equal((strip.match(/class="op-eval-stage /g)||[]).length,7);
    assert.doesNotMatch(strip,/\d+ steps/);
    if(i===2)assert.equal((strip.match(/op-eval-stage completed/g)||[]).length,7);
  }
  const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  assert.match(css,/\.op-eval-stages\{[^}]*grid-template-columns:repeat\(7,minmax\(0,1fr\)\)/);
  assert.doesNotMatch(css,/op-eval-arrow/);
});
test('r6 A1 and A2 use the same Eval short reason in 02, 03 and full story',async()=>{
  const snapshot=await timelineSnapshot(),sessionStorage=memoryStorage(),{api}=apiFor(snapshot,{sessionStorage});
  for(let i=0;i<2;i++){
    api.selectAgentAttempt(snapshot,snapshot.attempts[i].attempt_id);
    const reason=r6.versions[i].story.short;
    const node=api.attentionEfficiency(snapshot).match(new RegExp(`data-attn-attempt="${i+1}"[^]*?<\\/g>`))[0];
    assert.ok(node.includes(`>${reason}</text>`));
    const strip=api.executionViewer(snapshot).match(/data-eval-stopped><b>Stopped at [^<]*<\/b> · ([^<]+)<\/p>/)[1];
    assert.equal(strip,reason);
    const story=api.agentFullStory(snapshot).match(new RegExp(`data-agent-attempt-card="${i+1}"[^]*?data-agent-failure-reason>([^<]+)`));
    assert.equal(story[1],reason);
    assert.doesNotMatch(api.agentTimelineDetail(snapshot,null),/Planner refused|Out of reach/);
  }
});
test('agent cards show each run\'s recorded Dev, Eval and Advisor models (10-30: r6 astra, sol3 sol)',async()=>{
  const {api}=apiFor();
  const {snapshot}=await agentSnapshot();
  const card=(html,title)=>html.match(new RegExp(`<span>${title}</span><b>([^<]*)</b>(?:<small>([^<]*)</small>)?`));
  for(const model of ['gpt-6-astra','gpt-6.1-sol']){
    const view={...snapshot,dev_eval_enabled:true,agent:{...snapshot.agent,dev_model:model,eval_model:model,advisor_model:model}};
    const html=api.workspaceHTML(view,null);
    assert.equal(card(html,'Eval agent')[1],model);
    assert.ok(card(html,'Help strategy')[2].endsWith(`an automatic advisor answers · ${model}`),card(html,'Help strategy')[2]);
    assert.doesNotMatch(html,/data-agent-settings|Codex subscription/);
    if(model!=='gpt-6-astra')assert.doesNotMatch(html.slice(0,html.indexOf('data-core-region="status"')),/gpt-6-astra/);
  }
});
test('the launch name suffix stays in the run list but not in the headline, 01 Task or tab title (10-30)',async()=>{
  const {snapshot}=await agentSnapshot();
  const view={...snapshot,run:{...snapshot.run,name_suffix:'line B agent sol'}},{api}=apiFor(view);
  assert.ok(api.taskDisplayName(view.run).endsWith(' -- line B agent sol'));
  assert.doesNotMatch(api.taskTitle(view.run),/line B agent sol/);
  assert.doesNotMatch(api.operatorTitle(view),/line B agent sol/i);
  const html=api.workspaceHTML(view,null),setup=html.slice(0,html.indexOf('data-core-region="status"'));
  assert.match(setup,/<span>Task<\/span>/);
  assert.doesNotMatch(setup,/line B agent sol/);
});
test('agent cost bars sit clear of the segment caption above them (10-30)',async()=>{
  const snapshot=await timelineSnapshot(),{api}=apiFor(snapshot,{sessionStorage:memoryStorage()});
  const html=api.attentionEfficiency(snapshot);
  const captions=[...html.matchAll(/<text class="op-agent-activity" x="[^"]+" y="([\d.]+)"/g)].map(m=>Number(m[1]));
  const bars=[...html.matchAll(/<rect class="op-attn-cost" x="[^"]+" y="([\d.]+)" width="26" height="([\d.]+)"/g)].map(m=>({top:Number(m[1]),bottom:Number(m[1])+Number(m[2])}));
  const labels=[...html.matchAll(/<text class="op-attn-cost-label" x="[^"]+" y="([\d.]+)"/g)].map(m=>Number(m[1]));
  const height=Number(html.match(/<svg viewBox="0 0 [\d.]+ ([\d.]+)"/)[1]);
  assert.ok(captions.length&&bars.length&&labels.length);
  for(const bar of bars)assert.ok(bar.top-Math.max(...captions)>=8,`bar top ${bar.top} vs caption ${Math.max(...captions)}`);
  for(const label of labels)assert.ok(label+6<=height,`cost label ${label} inside svg height ${height}`);
});
test('task-group run cards lead with the robot and a compact time, show only explicit renames; other lists keep the task name (10-30)',()=>{
  const {api}=apiFor(null,{selectedDev:null,selectedRun:null,pins:new Set()});
  const r={run_id:'v2-card',session_id:'v2-card',development:true,suite:'robocasa365',task_id:'pick_place_counter_to_sink',name_suffix:'line B agent sol',
    robot:{model:'OpenArm365',execution_arm:'both'},created_at:1793260564,status:'completed',policy_ids:['reactive_help'],attempts_used:3,attempts_limit:10,agent:true,versions:[],jobs:[]};
  const grouped=api.sidebarRunCard(r,true),plain=api.sidebarRunCard(r);
  // Outcome-rail card (10-30): robot + compact time, then version bar, count, coloured strategy chip and status text.
  assert.match(grouped,/<article [^>]*data-card="rail"/);
  assert.match(grouped,/<strong data-run-name[^>]*>OpenArm365 · Both arms<\/strong><small class="ab-record-time">[A-Z][a-z]{2} \d{1,2} · \d{2}:\d{2}<\/small><\/button>/);
  assert.match(grouped,/<div class="ab-run-rail-meta"><span class="ab-run-bar" title="3 of 10 formal versions used"><i style="width:30%"><\/i><\/span><span class="ab-run-rail-text">3\/10<\/span><span class="ab-run-badge" data-run-badge="strategy" data-policy="reactive_help" title="Help strategy: [^"]+"[^>]*><svg[^]*?<\/svg>[^<]+<\/span><span class="ab-run-status"[^>]*>[^<]+<\/span><\/div>/);
  assert.doesNotMatch(grouped,/ab-run-badges|ab-run-metadata|SDK description|ab-run-label|line B agent sol<\/small>/);
  const renamed=api.sidebarRunCard({...r,development:false,display_name:'Demo for Monday'},true);
  assert.match(renamed,/<small class="ab-run-label" title="Demo for Monday">Demo for Monday<\/small>/);
  assert.match(plain,/<strong data-run-name[^>]*>[^<]* -- line B agent sol<\/strong>/);
  assert.match(plain,/<small class="ab-run-metadata">OpenArm365 · Both arms<\/small>/);
  assert.doesNotMatch(plain,/ab-run-label|data-card=|ab-run-rail-meta/);
  assert.match(plain,/<div class="ab-run-badges">/);
});
test('task cards summarise every run as an outcome bar with a solved count (10-30 sidebar 4B)',()=>{
  const {api}=apiFor(null,{selectedDev:null,selectedRun:null,pins:new Set()});
  const base={suite:'robocasa365',task_id:'pick_place_counter_to_sink'};
  const rows=[{...base,run_id:'a',status:'completed',agent:true,agent_solved:true},{...base,run_id:'b',status:'failed'},{...base,run_id:'c',status:'failed'},{...base,run_id:'d',status:'needs_review',reason:'eval disagreement'}];
  const html=api.sidebarTaskSummary(rows,null);
  assert.match(html,/<summary class="ab-task-summary" data-outcome-summary><strong class="ab-task-title"[^>]*>Pick Place: Counter → Sink<\/strong><span class="ab-task-count"[^>]*>4<\/span>/);
  assert.match(html,/<span class="ab-outcome-bar"><i class="success" style="width:25.0%"><\/i><i class="failed" style="width:50.0%"><\/i><i class="waiting" style="width:25.0%"><\/i><\/span>/);
  assert.match(html,/title="1 solved · 2 failed · 1 need you"/);
  assert.match(html,/<span class="ab-outcome-text"><b>1<\/b> \/ 4 solved<\/span>/);
  assert.doesNotMatch(html,/op-dots/);
});
test('the viewed task stays a white card and the TASKS header has no icon tag (10-30)',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  const cleared=css.indexOf('#attentionbench-clear.ab-industrial .ab-sidebar nav>[data-task-group][data-viewing=true]{background:none');
  const white=css.indexOf('#attentionbench-clear[data-operator-view="true"] .ab-sidebar nav>[data-task-group][data-viewing="true"]{background:#fff}');
  assert.ok(cleared>=0&&white>cleared,'the white card rule must come after the equally specific industrial background:none');
  assert.doesNotMatch(source+css,/ab-sidebar-tag/);
});

