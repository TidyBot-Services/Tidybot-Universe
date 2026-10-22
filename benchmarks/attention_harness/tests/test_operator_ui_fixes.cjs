const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const fixtureDir=path.join(__dirname,'fixtures/ui_real_20261019');
const real=(id,kind)=>JSON.parse(fs.readFileSync(path.join(fixtureDir,`${id}.${kind}.json`),'utf8'));
const reviewId='devexp-d4e3c05c85ce05d0f50cc2ac';
const plannerId='devexp-7cf85c4709a356146dfe3b3c';

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const operator=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const replay=source.slice(source.indexOf('  function replayTarget('),source.indexOf('  function rememberReplay('));
const terminal=vm.runInNewContext(source.slice(source.indexOf('  function isTerminal('),source.indexOf('  function executionFailure('))+'\n isTerminal');
const api=vm.runInNewContext(replay+operator+'\n({operatorState,operatorPageTitle,operatorOutcome,operatorReplayTarget,operatorEvents,operatorExecution})',{
  DEBUG_VIEW:false,esc,isTerminal:terminal,
  operatorLast:s=>s.attempts.at(-1),operatorWaiting:()=>false,devEndSummary:()=>({key:null}),
  plainError:()=>'',executionFailure:()=>null,launches:[],dateTime:()=>'',humanDuration:()=>'',
  actionableRequests:()=>[],cameraStation:s=>s.station,
  number:String,pct:()=>0,operatorTime:n=>`${Math.floor(n/60)}:${String(Math.round(n%60)).padStart(2,'0')}`,
});
const navigation=source.slice(source.indexOf('  function recordIsActive('),source.indexOf('  // End task navigation projection.'));
const sidebar=vm.runInNewContext(navigation+'\n ({recordStatus,recordIsActive})',{DEBUG_VIEW:false,label:value=>value});

const snapshot=(status,reason)=>({run:{run_id:'run-1',status},dev_session:{state:status,reason},dev_v2:{lifecycle:{phase:'terminal'}},
  attempts:[],traces:{},eval:{native_outcomes:{}},requests:[]});

test('terminal condition and review states keep their title, reason and sidebar color class',()=>{
  const cases=[
    ['condition_invalid','scene_contract_mismatch','Condition invalid','the scene did not match the registered scene'],
    ['condition_invalid','eval_vision_unavailable: unparseable_answer','Condition invalid','the automatic reviewer could not read the recording'],
    ['needs_review','worker_lost_no_automatic_redispatch','Needs review','the worker stopped unexpectedly'],
    ['cancelled','user cancelled','Cancelled','this run was cancelled'],
    ['not_started','upstream_condition_invalid','Not started','before any execution began']];
  for(const [status,reason,title,copy] of cases){
    const run=snapshot(status,reason);
    assert.match(api.operatorPageTitle(run,'Pick Place'),new RegExp(title));
    assert.doesNotMatch(api.operatorPageTitle(run,'Pick Place'),/Running/);
    const html=api.operatorOutcome(run);
    assert.match(html,new RegExp(`data-operator-outcome="${status}"`));
    assert.match(html,new RegExp(copy));
    assert.doesNotMatch(html,/RUNNING/);
  }
  assert.equal(api.operatorState(snapshot('condition_invalid','')),'condition_invalid');
  assert.equal(api.operatorState(snapshot('needs_review','')),'review');
  for(const [status,expected] of [['condition_invalid','Condition Invalid'],['needs_review','Needs review'],['failed','Failed']]){
    assert.equal(sidebar.recordStatus({development:true,status,lifecycle:{phase:'terminal'}}),expected);
    assert.equal(sidebar.recordIsActive({development:true,status,lifecycle:{phase:'sdk'}}),false);
  }
  const running={...snapshot('running',''),dev_v2:null};
  assert.equal(api.operatorState(running),'running');
  assert.match(api.operatorOutcome(running),/● RUNNING/);
  const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  assert.match(css,/\.ab-run-status\[data-status="condition_invalid"\]/);
  assert.match(css,/\.op-outcome\.condition_invalid/);
  assert.match(css,/\.op-outcome\.needs_review/);
});

test('steps without exact time use nearby evidence and mark approximate seek mode',()=>{
  const recording={duration_seconds:5.2,replay_timing:{basis:'relative',frame_trace_seconds:[1,3,5],frame_video_seconds:[0,2,5]}};
  const events=[
    {operation:'right_arm.move_delta',status:'completed',timestamp_basis:'relative',timestamp:1.1},
    {operation:'right_arm.move_to_position',status:'completed'},
    {operation:'right_arm.move_delta',status:'failed',error:{message:'halted'}}];
  assert.equal(api.operatorReplayTarget(events,0,recording,true).mode,'sample');
  assert.equal(api.operatorReplayTarget(events,1,recording,true).mode,'nearest');
  assert.equal(api.operatorReplayTarget(events,1,recording,true).seconds,0);
  assert.equal(api.operatorReplayTarget([events[0],events[1],{...events[0],timestamp:5}],1,recording,true).seconds,0);
  assert.equal(api.operatorReplayTarget(events,2,recording,true).mode,'end');
  assert.equal(api.operatorReplayTarget(events,2,recording,true).seconds,5);
  const html=api.operatorEvents({events,recording,failure:{message:'halted'}},true);
  assert.match(html,/data-replay-event="1" data-replay-seconds="0" data-replay-mode="nearest"/);
  assert.match(html,/data-replay-event="2" data-replay-seconds="5" data-replay-mode="end"/);
  assert.match(html,/Near recording end · approximate seek/);
  assert.match(html,/~0:05/);
  assert.equal(api.operatorReplayTarget(events,2,recording,false).seconds,null);
  assert.equal(api.operatorReplayTarget([{status:'failed',timestamp_basis:'relative',timestamp:99}],0,recording,true).mode,'end');
  assert.equal(api.operatorReplayTarget([{status:'completed',timestamp_basis:'relative',timestamp:99}],0,recording,true).seconds,null);
  assert.equal(api.operatorReplayTarget([{status:'failed'}],0,{...recording,replay_timing:{...recording.replay_timing,frame_video_seconds:[0,-1,5]}},true).seconds,null);
  assert.equal(api.operatorReplayTarget([{status:'completed'}],0,{duration_seconds:5},true).seconds,0);
  assert.equal(api.operatorReplayTarget([{status:'failed'}],0,{duration_seconds:5},true).seconds,4.9);
});

test('Dev v2 source matching accepts supported seeds and explains unsupported copies',()=>{
  const helper=source.slice(source.indexOf('  function launchSourceProfile('),source.indexOf('  function bindLaunch('));
  const {launchSourceProfile:match,launchSourceNotice:notice}=vm.runInNewContext(helper+'\n ({launchSourceProfile,launchSourceNotice})',
    {esc,displayRun:()=>'<Current run>'});
  const profiles=[{suite:'robocasa365',task_id:'pick_place_counter_to_sink',seed:101,robot:'OpenArm365',execution_arm:'both',channel:'dev_v2',enabled:true}];
  const options={dev_v2:{tasks:[{task_id:'pick_place_counter_to_sink',seeds:[101,102]}]}};
  const sourceRun={suite:'robocasa365',task_id:'pick_place_counter_to_sink',seed:102,robot:{model:'OpenArm365',execution_arm:'both'}};
  assert.equal(match(sourceRun,profiles,options,true).profile,profiles[0]);
  assert.equal(match(sourceRun,profiles,options,true).reason,'');
  const supported=notice(sourceRun,match(sourceRun,profiles,options,true).profile,'');
  assert.match(supported,/data-launch-source/);
  assert.doesNotMatch(supported,/unavailable|Choose a supported configuration/);
  assert.match(supported,/&lt;Current run&gt;/);
  assert.match(match({...sourceRun,seed:999},profiles,options,true).reason,/Scene seed 999/);
  assert.match(notice(sourceRun,null,'Scene seed 999 is unavailable.'),/unavailable for a new run: Scene seed 999 is unavailable/);
  assert.match(match({...sourceRun,robot:{model:'Other',execution_arm:'both'}},profiles,options,true).reason,/robot and arm/);
});

test('terminal Dev v2 projection keeps state and removes live preview endpoints',async()=>{
  const projection=source.slice(source.indexOf('  function devV2State('),source.indexOf('  async function updateV2Snapshot'));
  const project=vm.runInNewContext(projection+'\n devV2WorkspaceSnapshot',{
    devWorkspaceSnapshot:async()=>({run:{},station:{camera_url:'/old/frame',camera_preview_url:'/old/frame',camera_state_url:'/old/state'},
      dev_session:{},attempts:[{attempt_id:'a'}],resources:{},requests:[],responses:{},eval:{native_outcomes:{}}}),
    launchOptions:{dev_v2:{policies:[]}},v2MonitorIdentities:new Map(),encodeURIComponent
  });
  const s={session_id:'v2-1',locked:{config:{eval_agent:{enabled:false}}}};
  const base={experiment_id:'devexp-1',key:'cell-1',state:'condition_invalid',reason:'scene_contract_mismatch',
    lifecycle:{phase:'terminal'},mode:'benchmark',policy_id:'reactive_help',budget:{dev_calls:1,provider_tokens:1},used:{},jobs:[],versions:[],
    ledger:{goal:{wall_limit:100,wall_used:1,wall_remaining:99}}};
  for(const state of ['condition_invalid','needs_review','cancelled','failed','completed','not_started','budget_exhausted']){
    const result=await project(s,{...base,state});
    assert.equal(result.run.status,state);
    assert.equal(result.station.camera_url,null);
    assert.equal(result.station.camera_preview_url,null);
    assert.equal(result.station.camera_state_url,null);
    assert.equal(result.station.camera_monitor_v2,false);
    const html=api.operatorExecution({...result,attempts:[],traces:{},eval:{native_outcomes:{}},dev_session:{...result.dev_session,versions:[]}},
      {attempt:null,selected:'live',attempts:[],trace:null,recording:null,videos:[],images:[],recordingViews:null,viewOptions:null,
        chosenView:'external',streaming:false,active:false,flipStaticRows:false,cameraSuite:'RoboCasa365',cameraLabel:''});
    assert.doesNotMatch(html,/data-live-stream-key|data-preview-freshness|Preview error|retrying/);
  }
  const running=await project(s,{...base,state:'running',lifecycle:{phase:'sdk'}});
  assert.equal(running.run.status,'running');
  assert.match(running.station.camera_preview_url,/\/live\/frame$/);
});

test('real review experiment overrides a planned cell throughout status and sidebar',async()=>{
  const experiment=real(reviewId,'experiment'),cell=real(reviewId,'view');
  assert.equal(experiment.state,'needs_review');
  assert.equal(cell.state,'planned');
  assert.equal(cell.versions.length,0);
  const projection=source.slice(source.indexOf('  function devV2State('),source.indexOf('  async function updateV2Snapshot'));
  const {devV2State,devV2WorkspaceSnapshot}=vm.runInNewContext(projection+'\n ({devV2State,devV2WorkspaceSnapshot})',{
    devWorkspaceSnapshot:async session=>({run:{},station:{},dev_session:session,attempts:[],traces:{},resources:{},
      requests:[],responses:{},eval:{native_outcomes:{}}}),
    launchOptions:{dev_v2:{policies:[]}},v2MonitorIdentities:new Map(),encodeURIComponent
  });
  const session={session_id:'v2-'+cell.key,experiment_id:experiment.experiment_id,
    experiment_state:experiment.state,state:cell.state,locked:{config:{eval_agent:{enabled:false}}}};
  const snapshot=await devV2WorkspaceSnapshot(session,cell,experiment);
  assert.equal(snapshot.run.status,'needs_review');
  assert.equal(snapshot.dev_session.reason,experiment.reason);
  assert.equal(terminal(snapshot),true);
  assert.match(api.operatorPageTitle(snapshot,'Open cabinet'),/Needs review/);
  assert.doesNotMatch(api.operatorPageTitle(snapshot,'Open cabinet'),/Running/);
  const outcome=api.operatorOutcome(snapshot);
  assert.match(outcome,/data-operator-outcome="needs_review"/);
  assert.match(outcome,/Needs review: the worker stopped unexpectedly and the run cannot continue automatically\./);
  assert.doesNotMatch(outcome,/RUNNING|Attempt 1 of 5 is starting/);
  const nav=vm.runInNewContext(source.slice(source.indexOf('  function devNavigationRecord('),source.indexOf('  function allNavigationRecords('))+'\n devNavigationRecord',{
    devV2State,devEndSummary:()=>null
  });
  const sidebarRow=nav({...session,budget:cell.budget,used:cell.used,versions:cell.versions,jobs:cell.jobs});
  assert.equal(sidebarRow.status,'needs_review');
  assert.equal(sidebar.recordStatus(sidebarRow),'Needs review');
  assert.equal(sidebar.recordIsActive(sidebarRow),false);
  assert.equal(devV2State('planned','running'),'running');
  const active=await devV2WorkspaceSnapshot({...session,experiment_state:'running'},cell,{...experiment,state:'running',reason:null});
  assert.equal(active.run.status,'running');
  assert.equal(terminal(active),false);
  assert.match(api.operatorPageTitle(active,'Open cabinet'),/Running/);
  const activeRow=nav({...session,experiment_state:'running',budget:cell.budget,used:cell.used,versions:cell.versions,jobs:cell.jobs});
  assert.equal(sidebar.recordStatus(activeRow),'Running');
  for(const state of ['cancelled','condition_invalid'])assert.equal(devV2State('running',state),state);
});

test('real Attempt 4 final failed action seeks near saved recording end',()=>{
  const experiment=real(plannerId,'experiment'),cell=real(plannerId,'view');
  assert.equal(experiment.state,'completed');
  assert.equal(cell.state,'completed');
  const version=cell.versions.find(row=>row.version===4);
  const events=version.public_feedback.action_trace.map((row,index)=>({...row,
    event_id:`${cell.key}:v4:${index}`,sequence:index,event_type:'sdk.call',
    operation:[row.source,row.operation].filter(Boolean).join('.')}));
  const recording=version.recording;
  assert.equal(events.at(-1).status,'failed');
  assert.ok(events.at(-1).timestamp>recording.replay_timing.frame_trace_seconds.at(-1));
  const html=api.operatorEvents({events,recording,failure:{message:version.public_feedback.public_error}},true);
  const failedRow=html.match(/<details data-history-event class="op-step failed">.*?<\/details>/)?.[0];
  assert.ok(failedRow,'the final failed action is rendered');
  assert.match(failedRow,/Moved left arm a small step/);
  const seconds=Number(failedRow.match(/data-replay-seconds="([\d.]+)"/)?.[1]);
  assert.ok(seconds>0 && seconds<=recording.duration_seconds,{seconds,duration:recording.duration_seconds});
  assert.match(failedRow,/data-replay-mode="end"/);
  assert.match(failedRow,/approximate seek/);
  assert.equal(seconds,recording.replay_timing.frame_video_seconds.at(-1));
});

test('Dev v2 refresh reads the real experiment state and reason beside its cell view',async()=>{
  const experiment=real(reviewId,'experiment'),cell=real(reviewId,'view');
  const sourceBlock=source.slice(source.indexOf('  async function updateV2Snapshot('),source.indexOf('  async function updateDevSnapshot('));
  const paths=[],projected=[];
  const session={session_id:'v2-'+cell.key,experiment_id:experiment.experiment_id,key:cell.key};
  const update=vm.runInNewContext(sourceBlock+'\n updateV2Snapshot',{
    selectedDev:session.session_id,encodeURIComponent,fetch:async url=>{
      paths.push(url);
      return {ok:true,json:async()=>url.endsWith('/view')?cell:experiment};
    },lastRendered:'',currentSnapshot:null,
    devV2WorkspaceSnapshot:async (s,c,e)=>{projected.push({s,c,e});return {run:{status:e.state},dev_v2:c};},
    render:()=>{},location:{hash:'#exp='+experiment.experiment_id,href:'http://fixture/ui/#exp='+experiment.experiment_id},
    URL,history:{replaceState:()=>{}},experimentFromHash:hash=>hash.slice(5)
  });
  await update(session);
  assert.equal(paths.length,2);
  assert.equal(paths[0],`/api/dev-v2/experiments/${reviewId}/cells/${cell.key}/view`);
  assert.equal(paths[1],`/api/dev-v2/experiments/${reviewId}`);
  assert.equal(projected[0].e.state,'needs_review');
  assert.equal(projected[0].e.reason,experiment.reason);
  assert.match(projected[0].e.reason,/model_reserve_seconds/);
});

test('Pause, Keep running and Resume explain every run state in plain words',()=>{
  const ui=vm.runInNewContext(replay+operator+'\n({devStartButton,operatorOutcome,operatorPageTitle})',{
    DEBUG_VIEW:false,esc,isTerminal:terminal,operatorLast:s=>s.attempts.at(-1),operatorWaiting:()=>false,devEndSummary:()=>({key:null}),
    plainError:()=>'',executionFailure:()=>null,launches:[],dateTime:()=>'10/22/2026, 10:30:00',humanDuration:()=>'',
    actionableRequests:()=>[],cameraStation:s=>s.station,number:String,pct:()=>0,operatorTime:()=>'0:00'});
  const run=(rc,status='running',jobs=[])=>({...snapshot(status,''),dev_v2:{experiment_id:'devexp-x',run_controls:rc,jobs}});
  let html=ui.devStartButton(run({state:'running',can_pause:true,can_resume:false,can_keep_running:false,resume_blocked_reason:null}));
  assert.match(html,/data-dev-pause>⏸ Pause/);assert.match(html,/data-dev-resume disabled>▶ Resume/);assert.doesNotMatch(html,/data-run-control-note/);
  html=ui.devStartButton(run({state:'pausing',can_pause:false,can_keep_running:true,current_step:'writing v6'}));
  assert.doesNotMatch(html,/data-dev-pause/);assert.match(html,/data-dev-resume>Keep running/);assert.match(html,/the current step \(writing v6\) will finish first/);
  assert.match(ui.operatorOutcome(run({state:'pausing',current_step:'writing v6'})),/data-operator-outcome="pausing"[\s\S]*writing v6\) will finish normally[\s\S]*Keep running/);
  html=ui.devStartButton(run({state:'paused',can_resume:true}));
  assert.match(html,/data-dev-resume\s*>▶ Resume/);assert.match(html,/time budget is not counting/);
  html=ui.operatorOutcome(run({state:'paused',can_resume:true,paused_since:1792634400}));
  assert.match(html,/⏸ PAUSED/);assert.match(html,/since 10\/22\/2026, 10:30:00/);assert.match(html,/Press Resume to continue/);assert.doesNotMatch(html,/RUNNING/);
  const ended='This run was ended with Stop run, so it cannot be resumed. Start a new run instead.';
  html=ui.devStartButton(run({state:'ended',can_resume:false,resume_blocked_reason:ended},'needs_review'));
  assert.match(html,/data-dev-resume disabled>▶ Resume/);assert.match(html,/ended with Stop run, so it cannot be resumed/);
  // A Stop that cut off an AI call is reported as the operator's stop, never as a timeout.
  html=ui.operatorOutcome(run({state:'ended',interrupted_by_stop:{kind:'model',version:6,step:'writing v6'},resume_blocked_reason:ended},'needs_review',[{state:'needs_review',kind:'model'}]));
  assert.match(html,/You ended this run with Stop while the AI was writing v6/);assert.match(html,/not a timeout/);assert.doesNotMatch(html,/timed out/);
  // Tab title and sidebar say Paused / Pausing instead of Running or Planned.
  assert.match(ui.operatorPageTitle(run({state:'paused'}),'Pick Place'),/^⏸ Paused · Pick Place/);
  assert.match(ui.operatorPageTitle(run({state:'pausing'}),'Pick Place'),/^● Pausing · Pick Place/);
  assert.equal(sidebar.recordStatus({development:true,status:'paused'}),'Paused');
});
