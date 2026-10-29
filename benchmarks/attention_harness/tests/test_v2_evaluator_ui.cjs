// Rendering contracts only. No browser, simulator or network; not visual acceptance.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const block=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function api(){return vm.runInNewContext(block+'\n({operatorSettings,operatorAIReview,operatorResources,codexRunQuotaText})',{
 DEBUG_VIEW:false,esc,number:String,pct:()=>0,launches:[],stored:()=>null,isTerminal:()=>true,devEndSummary:()=>({key:'executions'}),taskTitle:()=> 'Open cabinet',taskDisplayName:()=> 'Open cabinet',policyTitle:()=> 'Reactive help'
});}
test('Codex run quota line uses recorded first-to-last points only',()=>{
 const snapshot={run:{run_id:'q'},dev_session:{locked:{config:{model_source:'codex'}},versions:[]},
   dev_v2:{experiment_id:'e',quota_usage:{before:12.1,after:15.6}},resources:{tokens:{used:100,limit:1000}},attempts:[]};
 assert.match(api().operatorResources(snapshot),/AI usage:[\s\S]*data-codex-run-quota>Codex quota used by this run: about \+4 points/);
 snapshot.dev_v2.quota_usage.after=12.1;
 assert.match(api().operatorResources(snapshot),/Codex quota used by this run: less than 1 point/);
 snapshot.dev_v2.quota_usage.after=11;
 assert.doesNotMatch(api().operatorResources(snapshot),/Codex quota used by this run/);
 snapshot.dev_v2.quota_usage.after=15;
 snapshot.dev_session.locked.config.model_source='parcc';
 assert.doesNotMatch(api().operatorResources(snapshot),/Codex quota used by this run/);
});
test('EVAL UI 01 enabled Qwen and old disabled task-check text',()=>{
 const snapshot={run:{run_id:'a',suite:'robocasa365',execution_target:'robocasa365_sim',robot:{model:'OpenArm365',execution_arm:'both'},seed:102},task_plan:{},attention_control:{active_policy:'reactive_help',read_only:true},dev_session:{model:'parcc/GLM'},dev_v2:{},dev_eval_enabled:true};
 const on=api().operatorSettings(snapshot);assert.match(on,/Eval agent<\/span><b>Qwen/);assert.match(on,/Reviews the recording and camera views/);assert.match(on,/the simulator still decides success/);
 delete snapshot.dev_eval_enabled;const off=api().operatorSettings(snapshot);assert.match(off,/Simulator's own task check/);assert.doesNotMatch(off,/Eval agent<\/span><b>Qwen/);
});
for(const [status,passed,label] of [['completed',true,'Looks right'],['completed',false,'Problems found'],['failed',false,'Review unavailable'],['timeout',false,'Review unavailable'],['skipped_budget',false,'Review unavailable']]){
 test(`EVAL UI 03 ${status}/${passed} opinion label and escaped expandable review`,()=>{
  const review={status,passed,feedback:'frame #1 <script>bad</script>',full_text:'### What happened\ntrace event #1'};
  const html=api().operatorAIReview(review,1);assert.match(html,new RegExp(label));assert.match(html,/AI opinion — the simulator decides success/);assert.match(html,/Read full review/);assert.match(html,/op-review-preview/);assert.doesNotMatch(html,/<script>/);
  const debug=api().operatorAIReview(review,1,true);assert.match(debug,/Raw AI review JSON/);assert.match(debug,/&quot;status&quot;/);
 });
}
test('EVAL UI 05 counts enabled reviews and marks disabled reviews untracked',()=>{
 const snap={run:{run_id:'x'},dev_session:{},dev_v2:{experiment_id:'e'},dev_eval_enabled:true,attempts:[],resources:{executions:{used:2,limit:3},eval_calls:{used:2,limit:3}}};
 const html=api().operatorResources(snap);assert.match(html,/Reviews requested/);assert.match(html,/data-dev-budget-key="eval_calls"/);assert.match(html,/data-count-to="2"/);
 snap.resources.eval_calls={used:0,limit:0};assert.match(api().operatorResources(snap),/Reviews requested/);
 delete snap.dev_eval_enabled;assert.match(api().operatorResources(snap),/data-dev-budget-key="eval_calls"><div class="op-meter op-meter--na" title="Not tracked for this run"><span>Reviews requested<\/span><b>—<\/b>/);
});
test('EVAL UI New run defaults on, locked selection and snapshot carry review',()=>{
 assert.match(source,/name="eval_agent_enabled" type="checkbox" checked/);
 assert.match(source,/eval_agent:\{enabled:form.eval_agent_enabled.checked,model:form.model_source.value==='codex'\?'gpt-6-astra':'parcc\/Qwen'\}/);
 assert.match(source,/reviewer:form.eval_agent_enabled.checked/);
 assert.match(source,/snapshot.dev_eval_enabled=s.locked\?\.config\?\.eval_agent\?\.enabled===true/);
 assert.match(source,/snapshot.dev_session.versions=cell.versions/);
 assert.match(source,/operatorAIReview\(version\?\.evaluator_review/);
});

// ---- Qwen -> GLM fallback must be explicit in the UI ----
const textOnly=(extra={})=>({status:'completed',passed:null,mode:'text_only',model:'parcc/GLM',model_requested:'parcc/Qwen',fallback_reason:'qwen_unreachable',feedback:'Text-only review: no recording was viewed. Trace event #0 failed.',full_text:'### Issues',...extra});
test('EVAL UI 03 text-only review says nobody viewed the recording and gives no verdict',()=>{
 const html=api().operatorAIReview(textOnly(),2);
 assert.match(html,/No verdict/);assert.match(html,/data-result="textonly"/);assert.match(html,/data-review-mode="text_only"/);
 assert.match(html,/Text-only review \(GLM\)/);assert.match(html,/no recording was viewed, so this is not a visual check/);assert.match(html,/Qwen did not respond/);
 assert.doesNotMatch(html,/Looks right|Problems found|AI opinion — the simulator decides success/);
 for(const [reason,text] of [['qwen_vision_timeout',/took too long/],['qwen_skipped_after_vision_timeout',/too slow earlier in this run/],['no_frames',/no recording was available/]])
  assert.match(api().operatorAIReview(textOnly({fallback_reason:reason}),2),text);
 assert.match(api().operatorAIReview(textOnly({status:'failed',feedback:'AI review unavailable.'}),2),/Review unavailable/);   // GLM failed too
 assert.match(api().operatorAIReview(textOnly(),2,true),/&quot;fallback_reason&quot;: &quot;qwen_unreachable&quot;/);               // raw JSON only in debug
});
test('EVAL UI 03 vision reviews are unchanged',()=>{
 const html=api().operatorAIReview({status:'completed',passed:true,mode:'vision',model:'parcc/Qwen',feedback:'ok'},1);
 assert.match(html,/Looks right/);assert.match(html,/AI opinion — the simulator decides success/);assert.match(html,/data-review-mode="vision"/);assert.doesNotMatch(html,/text-only/i);
});
test('EVAL UI 01 and 05 flag the fallback only once a review was text-only',()=>{
 const base={run:{run_id:'a',suite:'robocasa365',execution_target:'robocasa365_sim',robot:{model:'OpenArm365',execution_arm:'both'},seed:102},task_plan:{},attention_control:{active_policy:'reactive_help',read_only:true},dev_v2:{experiment_id:'e'},dev_eval_enabled:true,attempts:[],resources:{executions:{used:2,limit:3},eval_calls:{used:2,limit:3}}};
 const fine={...base,dev_session:{model:'parcc/GLM',versions:[{version:1,evaluator_review:{status:'completed',passed:false,mode:'vision'}}]}};
 assert.match(api().operatorSettings(fine),/Eval agent<\/span><b>Qwen/);assert.doesNotMatch(api().operatorSettings(fine),/op-warn|text only/);
 assert.doesNotMatch(api().operatorResources(fine),/text-only/);
 const fell={...base,dev_session:{model:'parcc/GLM',versions:[{version:1,evaluator_review:textOnly()},{version:2,evaluator_review:textOnly({fallback_reason:'qwen_skipped_after_vision_timeout'})}]}};
 const s=api().operatorSettings(fell);
 assert.match(s,/Eval agent<\/span><b>GLM · text only/);assert.match(s,/op-field op-warn/);assert.match(s,/Qwen was unavailable, so recordings were not reviewed visually/);assert.match(s,/the simulator still decides success/);
 assert.match(api().operatorResources(fell),/data-text-only-note/);assert.match(api().operatorResources(fell),/2 Eval agent reviews were text-only \(Qwen unavailable\)/);
 fell.dev_session.versions.pop();assert.match(api().operatorResources(fell),/1 Eval agent review was text-only/);
 const bare={...base,dev_session:{model:'parcc/GLM'}};                                   // no versions yet: must not throw
 assert.match(api().operatorSettings(bare),/<b>Qwen/);assert.doesNotMatch(api().operatorResources(bare),/text-only/);
});

// ---- extra camera angles (side, top, both wrists): wiring contracts ----
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to));
function cameraViewsReader(fetchImpl){
 const text=slice('  const CAMERA_VIEW_VIDEO','  async function devWorkspaceSnapshot');
 return vm.runInNewContext(text+'\n readCameraViews',{devFile:(s,uri)=>uri.startsWith(s.artifact_dir+'/')?'/files/'+uri.slice(s.artifact_dir.length+1):null,fetch:fetchImpl});
}
const manifest=views=>({ok:true,json:async()=>({views})});
const row=(label,video,status='saved')=>({label,video,status,width:960,height:540});
const session={artifact_dir:'/cell'};
test('CAMERA UI manifest: only saved, well-named videos become views, and one view alone is not a picker',async()=>{
 const read=cameraViewsReader(async()=>manifest({external:row('Overview','advisor_replay.mp4'),side:row('Side','advisor_replay_side.mp4'),top:row('Top-down','advisor_replay_top.mp4','failed'),
  left_wrist:row('Left wrist','../../etc/passwd'),right_wrist:row('Right wrist','evil.mp4'),'Bad Key':row('x','advisor_replay_x.mp4')}));
 const views=await read(session,'/cell/v1/executions/ep');
 assert.deepEqual(Object.keys(views),['external','side']);                              // failed / unsafe / odd entries are dropped
 const shuffled=await cameraViewsReader(async()=>manifest({right_wrist:row('R','advisor_replay_right_wrist.mp4'),top:row('T','advisor_replay_top.mp4'),external:row('O','advisor_replay.mp4'),left_wrist:row('L','advisor_replay_left_wrist.mp4'),side:row('S','advisor_replay_side.mp4')}))(session,'/cell/v1/ep');
 assert.deepEqual(Object.keys(shuffled),['external','side','top','left_wrist','right_wrist']);                     // fixed picker order
 assert.equal(await cameraViewsReader(async()=>manifest({external:row('Overview','advisor_replay.mp4')}))(session,'/cell/v1/ep'),null);   // single view: no picker
 assert.equal(await cameraViewsReader(async()=>({ok:false}))(session,'/cell/v1/ep'),null);                                                  // old runs: no manifest
 assert.equal(await cameraViewsReader(async()=>{throw new Error('network')})(session,'/cell/v1/ep'),null);
 assert.equal(await cameraViewsReader(async()=>manifest({}))(session,'/elsewhere/v1/ep'),null);                                           // outside the cell
});
test('CAMERA UI live: extra angles select their own state and frame URLs; the overview keeps the original ones',()=>{
 const text=slice('  function cameraStation(snapshot) {','  let previewPolling');
 const station=vm.runInNewContext(text+'\n cameraStation',{cameraChoice:s=>s.choice});
 const base='/api/dev-v2/experiments/e/cells/k/live';
 const station0={camera_url:base+'/frame',camera_preview_url:base+'/frame',camera_state_url:base,camera_views:{
  external:{preview_url:base+'/frame',state_url:base},side:{preview_url:base+'/frame?view=side',state_url:base+'?view=side'}}};
 const side=station({station:station0,choice:'side'});
 assert.equal(side.camera_preview_url,base+'/frame?view=side');assert.equal(side.camera_state_url,base+'?view=side');
 const main=station({station:station0,choice:'external'});assert.equal(main.camera_state_url,base);
 // the old pipeline's URL rewrite still works
 const old=station({station:{camera_views:{chest:{preview_url:'/api/station/preview-frame?run=r&view=chest'}}},choice:'chest'});
 assert.match(old.camera_state_url,/preview-state\?run=r/);
});
test('CAMERA UI wiring: live angles only while running, replays from the manifest, unknown choice falls back to the overview',()=>{
 assert.match(source,/const liveViews=terminal\?null:/);
 assert.match(source,/camera_views:liveViews/);
 assert.match(source,/for\(const \[key,name,width,height\] of|\['side','Side',960,540\]/);
 assert.match(source,/packet = \{bundle,trace,views:v2\?await readCameraViews\(s, bundle\.artifact_dir\):null\}/);
 assert.match(source,/kind:'advisor_replay_'\+key,mime_type:'video\/mp4'/);
 assert.match(source,/const chosenView=\(currentReplay && !viewOptions \|\| viewOptions && \(!viewOptions\[wantedView\] \|\| currentReplay && !videoForView\(wantedView\) && !imageForView\(wantedView\)\)\) \? 'external' : wantedView;/);
 // the attempt tabs and the per-view video filter already exist and are reused unchanged
 assert.match(source,/allVideos\.filter\(e=>e\.kind===\(chosenView==='external'\?'advisor_replay':`advisor_replay_\$\{chosenView\}`\)\)/);
});
