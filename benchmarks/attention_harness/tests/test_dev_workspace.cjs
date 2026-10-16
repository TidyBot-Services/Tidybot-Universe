// Pure read-only UI projection tests. No browser, provider or simulator is started.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto,createHash}=require('node:crypto');
const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
const block=source.slice(source.indexOf('  // Dev workspace projection:'),source.indexOf('  // End Dev workspace projection.'));
const sid='dev-'+'a'.repeat(24), root='/evidence/'+sid;
function setup(change=()=>{}) {
 const s={session_id:sid,artifact_dir:root,state:'budget_exhausted',created_at:100,model:'parcc/GLM',locked:{config_sha256:'c'.repeat(64),config:{suite:'robocasa365',task_id:'pick_place_counter_to_sink',seed:101,robot:'OpenArm365',execution_arm:'both',base_mode:'stationary'}},versions:[{version:1,source:{sha256:'d'.repeat(64)},valid:true}],jobs:[],budget:{executions:3,model_calls:3,tokens:10000,sdk_calls:240,execution_seconds:1440,wall_seconds:1800},used:{},reserved:{},remaining:{}};
 for(const k of Object.keys(s.budget)){s.used[k]=0;s.reserved[k]=0;s.remaining[k]=s.budget[k];}
 const ids={run_id:'run:attention-'+sid,attempt_id:'attempt:attention-'+sid+':0',config_sha256:s.locked.config_sha256,policy_sha256:s.versions[0].source.sha256};
 const trace={...ids,status:'failed',error:'Public planning rejection',sdk_events:[{source:'right_arm',operation:'move_delta',timestamp:4.2,timestamp_basis:'relative',status:'failed',arguments:{dx:.01}}]};
 const bundle={...ids,dev_version:1,formal_eligible:false,artifact_dir:root+'/v1/episode',recording:{...ids,video_saved:true,source:'official_simulator_display_rgb_after_each_control_step',replay_timing:{frame_trace_seconds:[4],frame_video_seconds:[0]}},native_evaluator:{...ids,evaluated:true},native_success:false};
 change({s,trace,bundle});
 const files=new Map(),calls=[];
 const ref=(name,value)=>{const bytes=Buffer.from(JSON.stringify(value));const uri=root+'/v1/'+name;files.set(uri,bytes);return {uri,sha256:createHash('sha256').update(bytes).digest('hex')};};
 const receipt={bundle:ref('bundle.json',bundle),trace:ref('trace.json',trace),execution_status:'failed'};
 s.jobs.push({kind:'execute',version:1,state:'settled',receipt});
 const fetch=async (url,options)=>{calls.push({url,options});assert.ok(!options.method||options.method==='GET');const uri=root+'/'+decodeURIComponent(url.split('/files/')[1]);const bytes=files.get(uri);return {ok:!!bytes,status:bytes?200:404,arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};};
 const api=vm.runInNewContext(block+'\n({devFile,readDevEvidence,devWorkspaceSnapshot,devEvidenceCache})',{fetch,crypto:webcrypto,TextDecoder,Uint8Array,encodeURIComponent});
 return {s,trace,bundle,receipt,files,calls,api};
}
test('empty Dev uses its real budget dimensions with no human request or fabricated GPU budget',async()=>{
 const {s,api}=setup();s.jobs=[];s.versions=[];const snap=await api.devWorkspaceSnapshot(s);
 assert.equal(snap.attempts.length,0);assert.equal(snap.requests.length,0);assert.equal(snap.resources.gpu_seconds,undefined);assert.equal(snap.resources.assistance,undefined);assert.equal(snap.resources.sdk_calls.limit,240);
});
test('settled execution binds actual SDK events, recording clock and native false without mutating the session',async()=>{
 const {s,api,calls}=setup();const before=JSON.stringify(s);const snap=await api.devWorkspaceSnapshot(s);const t=Object.values(snap.traces)[0];
 assert.equal(t.events[0].operation,'right_arm.move_delta');assert.equal(t.events[0].timestamp,4.2);assert.equal(t.recording.replay_timing.frame_trace_seconds[0],4);
 assert.equal(snap.eval.native_success,false);assert.equal(t.evidence[0].media_url,`/api/dev/sessions/${sid}/files/v1/episode/advisor_replay.mp4`);
 assert.equal(snap.attempts[0].ended_at,null);assert.equal(snap.attempts[0].execution_complete,true);assert.equal(JSON.stringify(s),before);assert.equal(calls.length,2);
});
for(const [field,value] of [['run_id','foreign'],['attempt_id','foreign'],['config_sha256','foreign'],['policy_sha256','foreign'],['dev_version',2],['formal_eligible',true]]) test(`foreign ${field} never becomes displayed native or recording evidence`,async()=>{
 const {s,api}=setup(({bundle})=>{bundle[field]=value;});const snap=await api.devWorkspaceSnapshot(s);
 assert.equal(Object.keys(snap.traces).length,0);assert.equal(snap.eval.native_success,null);assert.match(snap.dev_evidence_gaps[0],/identity mismatch/);
});
test('foreign recording binding is rejected independently of execution identity',async()=>{
 const {s,api}=setup(({bundle})=>{bundle.recording.attempt_id='foreign';});const snap=await api.devWorkspaceSnapshot(s);assert.equal(Object.keys(snap.traces).length,0);assert.match(snap.dev_evidence_gaps[0],/Recording identity mismatch/);
});
test('modified evidence bytes never pass the saved digest',async()=>{
 const {s,api,files,receipt}=setup();files.set(receipt.trace.uri,Buffer.from('{}'));const snap=await api.devWorkspaceSnapshot(s);assert.equal(snap.eval.native_success,null);assert.match(snap.dev_evidence_gaps[0],/digest mismatch/);
});
test('media references cannot escape the session artifact directory',async()=>{
 const {s,api}=setup(({bundle})=>{bundle.recording.video='/private/foreign.mp4';});const snap=await api.devWorkspaceSnapshot(s);assert.equal(Object.values(snap.traces)[0].evidence.length,0);
 assert.equal(api.devFile(s,root+'/../foreign.mp4'),null);assert.equal(api.devFile(s,root+'/.owner-tokens.json'),null);
});
test('claimed next version cannot borrow a previous native outcome or recording',async()=>{
 const {s,api}=setup();s.state='running';s.versions.push({version:2,source:{sha256:'e'.repeat(64)},valid:true});s.jobs.push({kind:'execute',version:2,state:'claimed'});
 const snap=await api.devWorkspaceSnapshot(s);assert.equal(snap.attempts[1].status,'running');assert.equal(snap.eval.native_success,null);assert.equal(Object.keys(snap.eval.native_outcomes).length,1);assert.equal(snap.station.camera_attempt_id,'attempt:attention-'+sid+':1');
});
test('review reservations remain visible and the projection invents no confirmed outcome',async()=>{
 const {s,api}=setup(({bundle})=>{bundle.native_evaluator.evaluated=false;});s.state='needs_review';s.jobs[0].state='needs_review';s.jobs[0].review_evidence=s.jobs[0].receipt;delete s.jobs[0].receipt;s.reserved.executions=1;s.remaining.executions=2;
 const snap=await api.devWorkspaceSnapshot(s);assert.equal(snap.resources.executions.reserved,1);assert.equal(snap.attempts[0].status,'unknown');assert.equal(snap.eval.native_success,null);assert.equal(snap.work_items[0].state,'blocked');
});
test('evidence cache is immutable-keyed and changed config bindings cannot reuse it',async()=>{
 const {s,api,calls}=setup();await api.devWorkspaceSnapshot(s);await api.devWorkspaceSnapshot(s);assert.equal(calls.length,2);
 s.locked.config_sha256='f'.repeat(64);const snap=await api.devWorkspaceSnapshot(s);assert.equal(Object.keys(snap.traces).length,0);assert.equal(calls.length,4);
});
test('running Dev exposes its version-bound live frame and metadata endpoints',async()=>{
 const {s,api}=setup();s.state='running';s.jobs[0].state='claimed';
 s.preview_station={camera_url:`/api/dev/sessions/${sid}/preview-frame?version=1`,camera_preview_url:`/api/dev/sessions/${sid}/preview-frame?version=1`,camera_state_url:`/api/dev/sessions/${sid}/preview-state?version=1`,preview_state:{state:'live',sequence:2}};
 const snap=await api.devWorkspaceSnapshot(s);
 assert.equal(snap.station.camera_url,s.preview_station.camera_url);
 assert.equal(snap.station.camera_state_url,s.preview_station.camera_state_url);
 assert.equal(snap.station.preview_state.sequence,2);assert.equal(snap.attempts[0].status,'running');
});
test('failed startup needing review is not projected as an executing camera',async()=>{
 const {s,api}=setup();s.state='needs_review';s.jobs[0].state='needs_review';
 const snap=await api.devWorkspaceSnapshot(s);assert.equal(snap.run.status,'completed');assert.equal(snap.station.status,'ended');
});

// Presentation checks run without a browser. They are not visual acceptance.
const operatorBlock=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
function operatorAPI(overrides={}) {
 return vm.runInNewContext(operatorBlock+'\n({plainAction,plainError,operatorTime,operatorPolicy,operatorSettings,operatorOutcome,operatorFlow,operatorEvents,operatorQuickReplies,operatorHelpHistory,operatorBudgetCard,operatorResources})',{
  DEBUG_VIEW:false,number:value=>String(value),pct:(used,limit)=>limit>0?Math.max(0,Math.min(100,used/limit*100)):0,launches:[],actionableRequests:s=>s.requests.filter(r=>r.state==='pending'&&r.mode==='live_human_first'),dateTime:value=>value?'Recorded time':'Not recorded',executionFailure:t=>t?.failure,replayTarget:()=>({seconds:45}),devEndSummary:()=>({key:'executions'}),requestState:r=>r.state,readableReply:r=>'<p>'+r.content+'</p>',esc:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  taskDisplayName:run=>run.display_name||'Lift the cube',policyTitle:id=>({reactive_help:'Reactive help',autonomous:'Autonomous'})[id]||'Recorded strategy',
  stored:()=>null,isTerminal:s=>['completed','failed','cancelled'].includes(s.run.status),...overrides
 });
}
for(const kind of ['formal','dev1','dev2'])test(`OPERATOR-01 presentation: six fields for ${kind}`,()=>{
 const snap={run:{run_id:'private_id',display_name:'Lift the cube',suite:'robosuite',seed:101,execution_target:'robosuite_sim',assistance_mode:'live_human_first',policy_id:'reactive_help',developer_model:'provider/GLM',robot:{model:'OpenArm365',execution_arm:'both'},base:{capability:'stationary'},status:'completed'},task_plan:{definition:{description:'Lift the cube safely'}},attention_control:{active_policy:'reactive_help',read_only:true}};
 if(kind!=='formal')snap.dev_session={model:'provider/GLM',locked:{config:{}}};if(kind==='dev2')snap.dev_v2={};
 const html=operatorAPI().operatorSettings(snap);
 assert.match(html,/01.*TASK SETUP/);assert.equal((html.match(/class="op-field(?: |")/g)||[]).length,6);
 assert.match(html,/Lift the cube safely/);assert.match(html,/OpenArm · both arms/);
 // big text = the summary, small text = the explanation
 assert.match(html,/<b>Lift the cube<\/b><small>Lift the cube safely<\/small>/);
 assert.match(html,/Where it runs<\/span><b>Robosuite simulator<\/b><small>Scene seed 101/);
 assert.match(html,/Help strategy<\/span><b>Reactive help<\/b><small>Ask for help after each failed attempt · /);
 if(kind==='formal')assert.match(html,/Dev agent<\/span><b>Fixed plan<\/b><small>Uses an approved fixed plan/);
 else assert.match(html,/Dev agent<\/span><b>GLM<\/b><small>AI rewrites its plan after each attempt/);
 assert.match(html,/Eval agent<\/span><b>Simulator's own task check<\/b><small>Success is decided by the simulator, not by an AI model/);assert.doesNotMatch(html,/How the robot improves|Who judges success/);
 assert.doesNotMatch(html,/<pre|<code|Run details|Configuration locked|false|data-policy-host/);
 assert.match(html,kind==='formal'?/Uses an approved fixed plan/:/AI rewrites its plan after each attempt/);
});
test('OPERATOR privacy mappings follow GOAL and refuse unknown identifiers',()=>{
 const api=operatorAPI();assert.equal(api.plainAction('sensors.find_objects'),'Looked for the objects');assert.equal(api.plainAction('right_arm.move_delta'),'Moved right arm a small step');assert.equal(api.plainAction('secret_sdk.fn'),'Robot action');assert.equal(api.plainAction('secret_sdk.fn',true),'secret_sdk.fn');
 for(const [raw,expected] of [['RuntimeError: ValueError: Exception: joint tracking exceeds 20 mrad','the arm moved too abruptly, so the safety controller halted it'],['PlanningRejected','the motion planner refused the move (out of reach or would collide)'],['It slipped','the item slipped out of the gripper'],['item is required',"the robot couldn't clearly see the item"],['timed out','the action took too long'],['budget exhausted','it ran out of its allowed budget'],['An ordinary failure occurred','An ordinary failure occurred'],['error_id at SDK.run(x)','the robot hit an execution error'],['{"private":"value"}','the robot hit an execution error'],['','the robot hit an execution error']])assert.equal(api.plainError(raw),expected);
 assert.equal(api.operatorTime(65.2),'1:05');assert.equal(api.operatorTime(3.8),'0:04');
});

for(const state of ['running','waiting','failed','success'])test(`OPERATOR-02 banner and merged flow: ${state}`,()=>{
 const snap={run:{run_id:'private_id',status:['failed','success'].includes(state)?'completed':'running'},attempts:[{attempt_id:'attempt:private',index:0,status:'failed'}],task_plan:{max_attempts:3},traces:{t:{attempt_id:'attempt:private',failure:{message:'joint tracking exceeds'},events:[{status:'failed',timestamp:45,timestamp_basis:'relative'}]}},eval:{native_success:state==='success'},requests:state==='waiting'?[{attempt_id:'attempt:private',state:'pending',mode:'live_human_first'}]:[],responses:{}};
 const api=operatorAPI(),html=api.operatorOutcome(snap);
 assert.match(html,new RegExp({running:'RUNNING',waiting:'WAITING FOR YOU',failed:'NOT COMPLETED',success:'COMPLETED'}[state]));
 assert.doesNotMatch(html,/private_id|joint tracking exceeds|RuntimeError/);
 if(state==='waiting')assert.equal((api.operatorFlow(snap).match(/Asked for help/g)||[]).length,1);
});

test('OPERATOR-03 actions group consecutive checks and retain actual seek data',()=>{
 const api=operatorAPI({replayTarget:e=>({seconds:e.timestamp,mode:'approximate'})});
 const trace={events:[{operation:'sensors.get_observation',timestamp:3,timestamp_basis:'relative',status:'completed'},{operation:'sensors.get_observation',timestamp:4,timestamp_basis:'relative',status:'completed'},{operation:'right_arm.move_delta',timestamp:10,timestamp_basis:'relative',status:'completed'},{operation:'right_arm.move_delta',timestamp:11,timestamp_basis:'relative',status:'completed'},{operation:'right_arm.move_delta',timestamp:45,timestamp_basis:'relative',status:'failed',arguments:{secret_id:1}}],failure:{message:'RuntimeError: joint tracking exceeds'}};
 const html=api.operatorEvents(trace,true);assert.equal((html.match(/data-history-event/g)||[]).length,2);assert.match(html,/2 times/);assert.doesNotMatch(html,/Checked arm position|checks/);assert.match(html,/data-replay-event="4" data-replay-seconds="45"/);assert.match(html,/0:45/);assert.match(html,/safety controller halted/);assert.doesNotMatch(html,/<pre|<code|sensors\.|right_arm|secret_id|RuntimeError/);
});

test('OPERATOR-04 renders each recorded response once and quick options are draft-only',()=>{
 const snap={requests:[{request_id:'r1',response_id:'reply1',attempt_id:'a1',state:'answered',created_at:100},{request_id:'r1',response_id:'reply1',attempt_id:'a1',state:'answered',created_at:100}],attempts:[{attempt_id:'a1',index:0}],responses:{reply1:{content:'Use smaller movements.',responder:'advisor_proxy',created_at:114}}};
 const api=operatorAPI(),html=api.operatorHelpHistory(snap);assert.equal((html.match(/Use smaller movements\./g)||[]).length,1);assert.match(html,/Answered in 14 s/);assert.match(html,/Advisor/);assert.match(html,/Read full reply/);assert.doesNotMatch(html,/Evidence details|Request history|<pre|<code/);
 const chips=api.operatorQuickReplies();assert.equal((chips.match(/type="button"/g)||[]).length,4);assert.match(chips,/data-preset-text="Move more slowly"/);assert.doesNotMatch(chips,/data-respond|type="submit"/);
});

test('OPERATOR-05 uses human-readable cards (wall time hidden once a run has ended), exhaustion and unchanged CSV export',()=>{
 const snap={run:{run_id:'dev-private',status:'completed'},dev_session:{},dev_v2:{experiment_id:'experiment1',ledger:{recompute_matches:false}},resources:{executions:{used:3,limit:3},help_count:{used:2,limit:2},model_calls:{used:3,limit:3},execution_seconds:{used:501,limit:1600},wall_seconds:{used:1100,limit:3600},tokens:{used:21000,limit:220000}},attempts:[]};
 const html=operatorAPI().operatorResources(snap);assert.equal((html.match(/class="op-meter(?: |")/g)||[]).length,4);assert.doesNotMatch(html,/Total time/);assert.match(html,/8:21.*26:40/);assert.match(html,/AI usage: 21k of 220k tokens/);assert.match(html,/Used up · this ended the run/);assert.match(html,/Data check failed · tell the research team/);assert.match(html,/\/api\/dev-v2\/experiments\/experiment1\/export.csv/);assert.doesNotMatch(html,/Reserved|<pre|<code|Experiment ledger/);
});

test('OPERATOR request path/fields and original submit handlers are retained',()=>{
 const frozenCandidates=['../../../../candidate-fontfix','../../../outputs/dev-live-monitor-20261014/candidate-fontfix'].map(p=>require('node:path').resolve(__dirname,p,'benchmarks/attention_harness/ui/live.js'));
 const baseline=fs.readFileSync(frozenCandidates.find(p=>fs.existsSync(p)),'utf8'); // frozen reference: sibling in the candidate layout, or inside outputs/ once merged into the main repo
 const normalize=text=>text.replaceAll('${DEBUG_VIEW ? error.message : plainError(error.message)}','${error.message}').replaceAll('textContent=DEBUG_VIEW ? error.message : plainError(error.message);','textContent=error.message;');
 const block=(text,start,end)=>normalize(text.slice(text.indexOf(start),text.indexOf(end,text.indexOf(start))));
 assert.equal(block(source,"    view.querySelectorAll('[data-respond]')",'    async function requestAction'),block(baseline,"    view.querySelectorAll('[data-respond]')",'    async function requestAction'));
 assert.equal(block(source,'  function bindDevGuidance(', '  function requestPanel('),block(baseline,'  function bindDevGuidance(', '  function requestPanel('));
 assert.equal((source.match(/const DEBUG_VIEW\s*=/g)||[]).length,1);assert.equal((source.match(/new URLSearchParams\(location.search\)/g)||[]).length,1);
});

test('OPERATOR error inventory additions reuse GOAL English copies',()=>{
 const api=operatorAPI();assert.equal(api.plainError('read_only_arm_plan_rejected'),'the motion planner refused the move (out of reach or would collide)');assert.equal(api.plainError('continuous_command_validation_rejected'),'the motion planner refused the move (out of reach or would collide)');assert.equal(api.plainError('Grasp lost during lift step 1'),'the item slipped out of the gripper');
});
test('OPERATOR budgets with no data are hidden and never shown as zero',()=>{
 const api=operatorAPI();assert.equal(api.operatorBudgetCard('Total time',{used:null,limit:null},true),'');assert.equal(api.operatorBudgetCard('Help requests',{used:0,limit:0}),'');
 const html=api.operatorBudgetCard('Total time',{used:null,limit:900},true);assert.match(html,/—<\/em> <small>\/ 15:00/);assert.doesNotMatch(html,/Used up|0:00/);
});

// Exercise the real renderer entry points, including their non-final branches.
// This is a string/contract check, without DOM, layout, video or HTTP claims.
function rendererAPI(snapshot,debug=false,overrides={}) {
 const functions=[...source.matchAll(/^  (?:async )?function \w+\(/gm)].map(match=>{
  const start=match.index,lineEnd=source.indexOf('\n',start),line=source.slice(start,lineEnd);
  const end=line.trimEnd().endsWith('}')?lineEnd:source.indexOf('\n',source.indexOf('\n  }',lineEnd)+1);
  return source.slice(start,end);
 }).join('\n');
 const values=new Map();
 return vm.runInNewContext(functions+'\n({runSettings,runOverview,executionViewer,requestPanel,resourceDetails,operatorHelpHistory,renderSidebar})',{
  DEBUG_VIEW:debug,Map,Set,Date,URL,URLSearchParams,console,
  taskTitles:{cube_lift:'Lift the cube',pick_place_counter_to_sink:'Pick Place: Counter → Sink'},
  root:{dataset:{}},launches:[],launchOptions:null,currentSnapshot:snapshot,selectedRequest:null,
  observedRunPhase:null,newlyLinkedRun:null,runNotice:{hidden:true},submissionReceipts:new Map(),
  cameraChoices:new Map(),devGuidancePending:new Set(),
  localStorage:{getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)},...overrides,
 });
}
function rendererSnapshot(kind) {
 const resource=(used,limit)=>({used,limit,reserved:0,remaining:limit-used});
 const snapshot={run:{run_id:'private_record_id',suite:'robosuite',task_id:'cube_lift',status:'completed',seed:101,policy_id:'reactive_help',assistance_mode:'live_human_first',execution_target:'robosuite_sim',developer_model:'provider/GLM',locked:true},task_plan:{max_attempts:3},attempts:[{attempt_id:'a1',index:0,status:'failed',execution_complete:true,started_at:100,ended_at:110,dev_version:1}],traces:{t1:{trace_id:'t1',attempt_id:'a1',events:[{timestamp:1,timestamp_basis:'relative',status:'failed',operation:'right_arm.move_delta'}],failure:{message:'RuntimeError: joint tracking exceeds 4 mrad'},evidence:[{media_url:'/replay.mp4',mime_type:'video/mp4'}]}},requests:[{request_id:'r1',response_id:'reply1',trace_id:'t1',attempt_id:'a1',request_type:'hint',state:'answered',mode:'automatic'}],responses:{reply1:{responder:'advisor_proxy',content:'Use smaller movements.'}},response_evidence:{},response_uses:{},station:{},eval:{native_success:false,stopped_reason:'max_attempts'},dev_evidence_gaps:[],work_items:[],attention_control:{binding:{allowed_policies:[]},read_only:true,active_policy:'reactive_help',segments:[]},resources:{assistance:resource(1,2),executions:resource(1,3),help_count:resource(1,2),model_calls:resource(1,3),tokens:resource(500,1000),execution_seconds:resource(10,60),wall_seconds:resource(50,100),gpu_seconds:resource(0,60)}};
 if(kind!=='formal')snapshot.dev_session={session_id:'dev_fixture',state:'budget_exhausted',locked:{config:{}},model:'provider/GLM',versions:[{version:1,valid:true,reason:'Use smaller movements.',source:{sha256:'a'.repeat(64)}}],jobs:[],used:{executions:3},budget:{executions:3},remaining:{},reason:'executions exhausted',attention_control:snapshot.attention_control};
 if(kind==='dev2')snapshot.dev_v2={key:'cell1',mode:'interactive',experiment_id:'experiment1',jobs:[],ledger:{goal_id:'private_goal',goal:{limits:{},used:{},reserved:{}},recompute_matches:true}};
 return snapshot;
}
for(const kind of ['formal','dev1','dev2'])test(`OPERATOR real renderer entries: ${kind} has five clean modules`,()=>{
 const snap=rendererSnapshot(kind),api=rendererAPI(snap);
 const modules=[api.runSettings(snap),api.runOverview(snap),api.executionViewer(snap),api.requestPanel(snap,null),api.resourceDetails(snap)];
 modules.forEach((html,i)=>{assert.match(html,new RegExp('<span class="op-number">0'+(i+1)+'</span>'));assert.doesNotMatch(html,/<pre\b|<code\b|RuntimeError|data-dev-revisions|Experiment ledger/);});
 assert.match(modules[2],/data-replay-event="0" data-replay-seconds="1"/);assert.equal((modules[3].match(/Use smaller movements\./g)||[]).length,1);
 const debug=rendererAPI(snap,true);assert.match(debug.runSettings(snap),/Run details/);if(kind==='dev2')assert.match(debug.resourceDetails(snap),/Experiment ledger &amp; exports|Experiment ledger & exports/);
});

test('OPERATOR sidebar binding tolerates omitted Jump to a record control',()=>{
 const snap=rendererSnapshot('formal');
 const control={remove(){},querySelectorAll(){return []}},nav={scrollTop:0,before(){}},search={value:'',addEventListener(){}};
 const sidebar={innerHTML:'',querySelectorAll(){return []},querySelector(selector){return selector==='nav'?nav:selector==='[data-run-search]'?search:selector==='[data-policy-controller]'?control:selector==='.ab-executing-runs'?{}:null;}};
 const api=rendererAPI(snap,false,{sidebar,view:{querySelector:()=>null},workspace:{querySelector:()=>null},document:{activeElement:null},runningStatus:{querySelectorAll:()=>[]},runs:[],devSessions:[],selectedDev:null,selectedRun:snap.run.run_id,sidebarQuery:'',sidebarFilter:'all',sidebarToggle:{querySelector:()=>null},sidebarMessage:'',historyNotice:null,switchRequests:new Map(),pins:new Set(),taskGroupChoices:new Map(),policyControllerElement:null});
 assert.doesNotThrow(()=>api.renderSidebar());assert.doesNotMatch(sidebar.innerHTML,/Jump to a record|id="ab-live-run"/);assert.match(sidebar.innerHTML,/Task history/);
});

test('OPERATOR system failures are phrased as a system problem, never as an execution error',()=>{
 const api=operatorAPI();
 assert.equal(api.plainError('DevConflict: native infrastructure/dispatch unknown; no revision/retry'),"a system problem stopped it (not the robot's fault)");
 assert.equal(api.plainError('RuntimeError: joint tracking exceeds 4mrad'),'the arm moved too abruptly, so the safety controller halted it');
});

test('OPERATOR a still picture while the planner thinks is explained, not reported as a broken feed',()=>{
 const body=source.match(/  function stalePictureCopy\([\s\S]*?\n  }\n/)[0];
 const stale=vm.runInNewContext(body+'\nstalePictureCopy',{Math});
 assert.match(stale('planner_prepare',39.6),/planning its next move.*picture stays still.*40 s/);
 assert.match(stale('env_create',3),/Setting up the simulator/);
 assert.match(stale('execute',7),/Waiting for the next picture from the robot/);
 assert.doesNotMatch(stale('planner_prepare',39.6)+stale('x',1),/Frame not updated|phase:|unavailable|error/i);
 assert.match(source,/DEBUG_VIEW \? `Frame not updated · \$\{seconds\}; phase: \$\{phase\}`/,'technical wording stays in the debug view');
});
