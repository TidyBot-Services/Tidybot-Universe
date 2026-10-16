// Node-only rendering and seek wiring; real browser verification is separate.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const snapshot=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/selected_moments_snapshot.json'),'utf8'));
const block=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const choices=new Map(),saved=new Map(),seeks=[];
let shown='attempt-1',renders=0;
const handlers={};
const scrubber={querySelectorAll:()=>[],insertAdjacentHTML:(_,html)=>{handlers.html=html},addEventListener:(type,fn)=>{handlers.scrubber=fn}};
const video={duration:10,requestReplaySeek:(seconds,mode)=>seeks.push({seconds,mode}),nextElementSibling:{querySelector:()=>scrubber},addEventListener:()=>{}};
const panel={dataset:{get replayAttempt(){return shown;}},querySelector:selector=>selector==='video[data-history-replay]'?video:null,scrollIntoView:()=>{}};
const listButton={dataset:{momentRequest:'targeted-1',momentIndex:'0'},addEventListener:(type,fn)=>{handlers.list=fn}};
const view={querySelector:selector=>selector==='[data-execution-viewer]'?panel:selector==='[data-execution-viewer] video[data-history-replay]'?video:null,
  querySelectorAll:selector=>selector==='[data-request-moments] [data-selected-moment]'?[listButton]:[]};
const api=vm.runInNewContext(block+'\n({selectedMoments,selectedMomentDetails,selectedMomentMarkerHTML,jumpToSelectedMoment,bindSelectedMomentControls})',{
  esc,replayTime:seconds=>`${String(Math.floor(seconds/60)).padStart(2,'0')}:${(seconds%60).toFixed(2).padStart(5,'0')}`,
  view,cameraChoices:choices,cameraContext:s=>s.run.run_id,cameraChoice:s=>choices.get(s.run.run_id)||'external',
  stored:(key,value)=>{if(value!==undefined)saved.set(key,value);return saved.get(key)},render:()=>{renders++;shown='attempt-1';}
});

test('selected moments render view, role, note and frame; generic and legacy requests degrade',()=>{
  const targeted=api.selectedMomentDetails(snapshot,snapshot.requests[0]);
  assert.match(targeted,/Selected moments/);
  assert.match(targeted,/m1 · Overview · Before · 00:08.40 · frame 12/);
  assert.match(targeted,/m2 · Left wrist · After · 00:08.80 · frame 13/);
  assert.match(targeted,/close\(\) completed/);
  assert.match(api.selectedMomentDetails(snapshot,snapshot.requests[1]),/Generic request · relative timestamp unavailable/);
  assert.match(api.selectedMomentDetails(snapshot,snapshot.requests[2]),/Generic request/);
  assert.equal(api.selectedMoments({selected_moments:null}).length,0);
});

test('scrubber markers are labelled and appear only on the matching view at their video time',()=>{
  const overview=api.selectedMomentMarkerHTML(snapshot,'attempt-1','external',10);
  assert.match(overview,/data-selected-moment/);
  assert.match(overview,/data-moment-request="targeted-1"/);
  assert.match(overview,/left:84\.00%/);
  assert.match(overview,/m1 Before/);
  assert.doesNotMatch(overview,/m2 After/);
  const wrist=api.selectedMomentMarkerHTML(snapshot,'attempt-1','left_wrist',10);
  assert.match(wrist,/left:88\.00%/);
  assert.match(wrist,/m2 After/);
  assert.doesNotMatch(wrist,/m1 Before/);
  assert.equal(api.selectedMomentMarkerHTML(snapshot,'attempt-1','top',10),'');
  assert.equal(api.selectedMomentMarkerHTML(snapshot,'attempt-1','external',0),'');
});

test('click path uses the shared replay seek and switches view before seeking',()=>{
  const request=snapshot.requests[0];
  assert.equal(api.jumpToSelectedMoment(snapshot,request,request.selected_moments[0]),true);
  assert.equal(renders,0);
  assert.deepEqual(seeks.at(-1),{seconds:8.4,mode:'moment'});
  assert.equal(api.jumpToSelectedMoment(snapshot,request,request.selected_moments[1]),true);
  assert.equal(choices.get('moments-run'),'left_wrist');
  assert.equal(saved.get('attention.history:moments-run'),'attempt-1');
  assert.equal(renders,1);
  assert.deepEqual(seeks.at(-1),{seconds:8.8,mode:'moment'});
  assert.equal(api.jumpToSelectedMoment(snapshot,snapshot.requests[2],undefined),false);
  assert.equal(renders,1);
});

test('list and scrubber click handlers both use the moment jump',()=>{
  api.bindSelectedMomentControls(snapshot);
  assert.match(handlers.html,/data-selected-moment/);
  handlers.list();
  assert.deepEqual(seeks.at(-1),{seconds:8.4,mode:'moment'});
  const marker={dataset:{momentRequest:'targeted-1',momentIndex:'1'}};
  handlers.scrubber({target:{closest:()=>marker}});
  assert.deepEqual(seeks.at(-1),{seconds:8.8,mode:'moment'});
});

test('Dev v2 read-only projection copies help localization into the UI request',async()=>{
  const projection=source.slice(source.indexOf('  async function devV2WorkspaceSnapshot'),source.indexOf('  async function updateV2Snapshot'));
  const base={run:{},dev_session:{},resources:{},station:{},attempts:[{attempt_id:'attempt-1',dev_version:1}],traces:{t:{attempt_id:'attempt-1',trace_id:'trace-1'}},
    requests:[],responses:{},response_evidence:{},dev_evidence_gaps:[],eval:{native_outcomes:{}}};
  const project=vm.runInNewContext(projection+'\n devV2WorkspaceSnapshot',{
    devWorkspaceSnapshot:async()=>base,launchOptions:{dev_v2:{policies:[]}},v2MonitorIdentities:new Map(),encodeURIComponent,
    devEvidenceCache:new Map(),readDevEvidence:async()=>{throw Error('unexpected read')}
  });
  const cell={key:'cell-1',experiment_id:'exp-1',artifact_dir:'/tmp/unused',state:'completed',mode:'benchmark',policy_id:'reactive_help',
    lifecycle:null,budget:{dev_calls:2,provider_tokens:10},used:{},jobs:[],ledger:{goal:{wall_limit:100,wall_used:1,wall_remaining:99}},
    versions:[{version:2,help_content:{provider:'advisor',content:'move slowly',selected_moments:snapshot.requests[0].selected_moments,localization:{status:'targeted',reason:null}}}]};
  const result=await project({session_id:'session-1',locked:{config:{}}},cell);
  assert.equal(result.requests[0].selected_moments[1].video_seconds,8.8);
  assert.equal(result.requests[0].localization.status,'targeted');
  cell.versions[0].help_content={provider:'advisor',content:'generic'};
  const old=await project({session_id:'session-1',locked:{config:{}}},cell);
  assert.equal(old.requests[0].selected_moments,undefined);
});
