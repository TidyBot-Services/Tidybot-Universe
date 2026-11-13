const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const fn=name=>{const i=source.indexOf(`  function ${name}(`);return source.slice(i,source.indexOf('\n  function ',i+10));};
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const snapshot=()=>({run:{status:'completed',seed:102},dev_v2_selection:{dev_mode:'agent'},
  dev_v2:{experiment_id:'devexp-'+'a'.repeat(24),key:'b'.repeat(64),seed:102,versions:[{version:1,valid:true},{version:2,valid:false},{version:3,valid:true}]}});
function api(extra={}) {
  return vm.runInNewContext(['canContinue','continueButton','runAgainButton','operatorTitle','agentAttemptCard','forkPanel',
    'fixtureTaskKey','runConfigDefaultsFromSelection','runConfigSource','launchSourceProfile','launchSourceNotice'].map(fn).join('\n')+
    '\n({canContinue,continueButton,runAgainButton,operatorTitle,agentAttemptCard,forkPanel,runConfigDefaultsFromSelection,runConfigSource,launchSourceProfile,launchSourceNotice})',{
      esc,root:{dataset:{}},launchOptions:{dev_v2:{continue_from_attempt:true}},isTerminal:s=>s.run.status==='completed',
      navigationRecords:()=>[],taskTitle:()=> 'Task',taskName:String,displayRun:s=>s.task_id,relativeTime:()=> 'now',policyTitle:String,selectedDev:null,selectedRun:null,
      launchButton:()=>'',agentStoryReason:()=>'',...extra});
}
test('continuation is limited to ended, owned local Agent runs with valid attempts',()=>{
  const a=api(),s=snapshot();assert.equal(a.canContinue(s),true);
  for(const change of [{run:{status:'running'}},{lobby:{mine:false}},{dev_v2_selection:{dev_mode:'one_call'}},{dev_v2:{versions:[{version:1,valid:false}]}}])
    assert.equal(a.canContinue({...s,...change}),false);
  assert.equal(api({root:{dataset:{remote:'1'}}}).canContinue(s),false);
  assert.equal(api({launchOptions:{dev_v2:{}}}).canContinue(s),false);
  assert.match(a.continueButton(s),/data-continue-attempt data-open-fork.*Continue from an attempt…/);
  assert.match(a.continueButton(s,3),/data-fork-version="3".*Continue from this attempt/);
  assert.equal(a.continueButton(s,2),'');
  assert.match(css,/\[data-lobby-view="other"\][^\n]*\[data-continue-attempt\]/);
});
test('valid attempt cards get a fork icon and invalid attempts have no continuation',()=>{
  const a=api(),s=snapshot();
  const card=v=>a.agentAttemptCard({version:v,native_success:false,evaluator_review:{passed:false}},false,s);
  assert.match(card(3),/data-agent-attempt-card="3"[\s\S]*data-continue-attempt[\s\S]*<svg[\s\S]*Continue from this attempt/);
  assert.doesNotMatch(card(2),/data-continue-attempt/);
  assert.doesNotMatch(a.agentAttemptCard({version:3},false,{...s,lobby:{mine:false}}),/data-continue-attempt/);
});
test('agent title offers both actions and preserves the source lineage',()=>{
  const s=snapshot();s.dev_v2_selection.fork={experiment_id:'devexp-'+'c'.repeat(24),cell:'d'.repeat(64),version:3};
  const html=api().operatorTitle(s);
  assert.match(html,/Run again with the same settings<\/button><button[^>]*data-continue-attempt/);
  assert.match(html,/class="op-lineage"[\s\S]*Attempt 3<\/a><span class="op-excluded">Excluded from comparison/);
  assert.match(source,/data-run-badge="continued">Continued/);
});
function element(value='') {return {value,disabled:false,hidden:false,textContent:'',listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};}
const PROFILE='r5-fixture-20261031';
const sourceRun=()=>({status:'completed',suite:'robocasa365',task_id:'categorize_condiments',seed:102,robot:{model:'OpenArm365',execution_arm:'both'}});
const fixtureProfile=()=>({enabled:true,suite:'robocasa365',task_id:'categorize_condiments',robot:'OpenArm365',execution_arm:'both',rules_profile:PROFILE,channel:'dev_v2',seeds:[101,102]});
const sourceSelection=()=>({dev_mode:'agent',mode:'interactive',tasks:['categorize_condiments'],seeds:[102],policies:['agent_initiated_help'],help_budget:5,rules_profile:PROFILE,
  dev_model:'gpt-6.1-sol',eval_model:'gpt-6.1-sol',advisor_model:'gpt-6.1-sol'});
test('workspace projection keeps the source execution profile for configuration matching',async()=>{
  const code=source.slice(source.indexOf('  async function devWorkspaceSnapshot('),source.indexOf('  // End Dev workspace projection.'));
  const project=vm.runInNewContext(code+'\n devWorkspaceSnapshot');
  for(const profile of [PROFILE,'r4-20261027',undefined]){
    const snapshot=await project({session_id:'source',state:'completed',jobs:[],versions:[],budget:{},locked:{config:{task_id:'categorize_condiments',seed:102,
      robot:'OpenArm365',execution_arm:'both',execution_settings:profile?{profile}:undefined}}});
    assert.equal(snapshot.run.execution_profile,profile||'legacy');
  }
});
async function dialog({blocked=false,start=null,fail=false,offered=true,fixture=false}={}) {
  const s=snapshot(),fields={};
  s.run=sourceRun();s.dev_v2_selection=sourceSelection();
  if(!fixture){s.run.task_id='pick_place_counter_to_sink';delete s.dev_v2_selection.rules_profile;s.dev_v2_selection.tasks=[s.run.task_id];}
  for(const name of ['benchmark','task_id','v2_mode','seed','robot_model','execution_arm','goal_id']){
    fields[name]=element();const label=element();fields[name].closest=()=>label;
  }
  const picker=element(),attempt=element(),radio=[element('feedback'),element('code')],header={kicker:element(),title:element()};
  const panel={dataset:{},innerHTML:'',querySelector:()=>attempt,querySelectorAll:()=>radio};
  const form={...fields,querySelector:selector=>selector==='[data-task-picker]'?picker:{prepend(){}}};
  const urls=[];let response={attempts:s.dev_v2.versions.map(v=>({...v,outcome:'unsuccessful'})),blocked,
    checks:[{level:blocked?'block':'ok',title:blocked?'Scene changed':'Scene matches'}],prompt_includes:['Earlier feedback through Attempt 3']};
  let fork,disabled=true;
  const a=api({currentSnapshot:s,document:{createElement:()=>panel},launchDialog:{querySelector:()=>({querySelector:selector=>selector==='.kicker'?header.kicker:header.title})},
    fetch:async url=>{urls.push(url);if(fail)throw new Error('Offline');return {ok:true,json:async()=>response};}});
  const origin=a.runConfigSource(s.run,a.runConfigDefaultsFromSelection(s.dev_v2_selection));
  const profile={...fixtureProfile(),task_id:s.run.task_id,...(!fixture?{rules_profile:undefined}:{})};
  const match=a.launchSourceProfile(origin,offered?[profile]:[],{});
  fork=a.forkPanel(form,{version:start,source:origin,...match},()=>{disabled=!fork?.ready();});
  await new Promise(resolve=>setImmediate(resolve));
  return {fork,form,panel,urls,picker,header,attempt,radio,disabled:()=>disabled,setResponse:value=>{response=value;}};
}
test('fork dialog defaults to the last valid attempt, locks task and seed, and previews feedback',async()=>{
  const d=await dialog();
  assert.equal(d.fork.request().version,3);assert.equal(d.fork.request().carry,'feedback');assert.equal(d.fork.ready(),true);
  assert.match(d.urls[0],/fork-preview\?cell=b+&version=3&carry=feedback$/);
  assert.equal(d.form.v2_mode.value,'interactive');assert.equal(d.form.seed.value,'102');
  for(const field of ['benchmark','task_id','seed','v2_mode'])assert.equal(d.form[field].disabled,true);
  assert.equal(d.picker.hidden,true);assert.match(d.panel.innerHTML,/Interactive · excluded from comparison/);
  assert.match(d.panel.innerHTML,/What Dev will see[\s\S]*Earlier feedback through Attempt 3/);
  assert.match(d.panel.innerHTML,/value="2"\s+disabled/);assert.equal(d.disabled(),false);
});
test('fixture selection resolves the source for both repeat and fork without an execution_profile on the run',async()=>{
  const a=api(),saved=sourceSelection(),defaults=a.runConfigDefaultsFromSelection(saved);
  assert.equal(defaults.taskId,'categorize_condiments');assert.equal(defaults.rulesProfile,PROFILE);
  assert.equal(defaults.policy,'agent_initiated_help');assert.equal(defaults.modelSource,'codex');assert.equal(defaults.armMotion,'short');
  const origin=a.runConfigSource(sourceRun(),defaults),profile=fixtureProfile();
  const resolved=a.launchSourceProfile(origin,[{...profile,rules_profile:undefined},profile],{dev_v2:{fixture_tasks:[profile]}});
  assert.equal(resolved.profile,profile);assert.equal(resolved.reason,'');
  assert.doesNotMatch(a.launchSourceNotice(origin,resolved.profile,resolved.reason),/unavailable/);
  const d=await dialog({fixture:true});
  assert.equal(d.form.task_id.value,'categorize_condiments@'+PROFILE);
  assert.equal(d.form.robot_model.value,'OpenArm365');assert.equal(d.form.execution_arm.value,'both');
  assert.equal(d.form.seed.value,'102');assert.equal(d.fork.ready(),true);
  for(const name of ['benchmark','task_id','v2_mode','seed','robot_model','execution_arm']){
    assert.equal(d.form[name].disabled,true);assert.equal(d.form[name].closest('label').hidden,true);
  }
  assert.match(d.panel.innerHTML,/data-fork-conditions[\s\S]*categorize_condiments[\s\S]*102[\s\S]*OpenArm365[\s\S]*both[\s\S]*r5-fixture-20261031/);
  d.form.task_id.value='pick_place_counter_to_sink';d.form.seed.value='101';d.fork.lock();
  assert.equal(d.form.task_id.value,'categorize_condiments@'+PROFILE);assert.equal(d.form.seed.value,'102');
  assert.match(source,/runConfigSource\(forkStart\?currentSnapshot\?\.run:[^\n]*,repeat\)/);
  assert.match(source,/forkStart\?'':launchSourceNotice/);
});
test('an unoffered source remains the locked source and blocks creation even when preview checks pass',async()=>{
  const d=await dialog({fixture:true,offered:false});
  assert.equal(d.fork.ready(),false);assert.equal(d.disabled(),true);
  assert.equal(d.form.task_id.value,'categorize_condiments@'+PROFILE);
  assert.match(d.panel.innerHTML,/data-level="block">Cannot continue this run: The task, robot and arm combination is no longer supported/);
  assert.doesNotMatch(d.panel.innerHTML,/Choose a supported configuration/);
  const a=api(),origin=a.runConfigSource(sourceRun(),a.runConfigDefaultsFromSelection(sourceSelection()));
  const unavailableSeed=a.launchSourceProfile(origin,[{...fixtureProfile(),seeds:[101]}],{});
  assert.equal(unavailableSeed.profile,null);assert.match(unavailableSeed.reason,/102 is unavailable/);
});
test('attempt and carry changes recheck; a blocking check disables creation',async()=>{
  const d=await dialog({start:1});assert.equal(d.fork.request().version,1);
  d.setResponse({attempts:[],blocked:true,checks:[{level:'block',title:'Scene changed'}],prompt_includes:[]});
  d.radio[1].listeners.change();assert.equal(d.disabled(),true);assert.equal(d.fork.ready(),false);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.fork.request().carry,'code');assert.equal(d.fork.ready(),false);assert.equal(d.disabled(),true);
  assert.match(d.panel.innerHTML,/data-level="block"/);
  d.setResponse({attempts:[],blocked:false,checks:[],prompt_includes:[]});
  d.attempt.listeners.change({target:{value:'3'}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.fork.request().version,3);assert.equal(d.fork.ready(),true);
});
test('preview errors keep creation disabled and launcher serialization gates readiness',async()=>{
  const d=await dialog({fail:true});assert.equal(d.disabled(),true);assert.equal(d.fork.ready(),false);assert.match(d.panel.innerHTML,/Offline/);
  assert.match(source,/if\(fork\)\{\s+if\(!fork.ready\(\)\)throw new Error\([\s\S]*?selection.fork=fork.request\(\)/);
  assert.match(source,/button.disabled=[^\n]*fork&&!fork.ready\(\)/);
});
