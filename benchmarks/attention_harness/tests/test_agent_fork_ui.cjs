const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const fn=name=>{const i=source.indexOf(`  function ${name}(`);return source.slice(i,source.indexOf('\n  function ',i+10));};
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const MANIFEST={tasks:[{task_id:'categorize_condiments',title:'Sort the condiments',seeds:[{seed:102,pictures:true,start:'cc-102-start.jpg',done:'cc-102-done.jpg'}]},
  {task_id:'pick_place_counter_to_sink',title:'Counter to sink',seeds:[]}]};
const snapshot=()=>({run:{status:'completed',seed:102},dev_v2_selection:{dev_mode:'agent'},
  dev_v2:{experiment_id:'devexp-'+'a'.repeat(24),key:'b'.repeat(64),seed:102,versions:[{version:1,valid:true},{version:2,valid:false},{version:3,valid:true}]}});
function api(extra={}) {
  // fn('continueButton') also carries the FORK_CHECK_LABELS constant that follows it in live.js.
  return vm.runInNewContext(['canContinue','forkIcon','continueButton','runAgainButton','operatorTitle','agentAttemptCard','forkCheckChip','forkAttemptVerdict','forkLineHtml','forkPanel',
    'fixtureTaskKey','taskPickerCards','runConfigDefaultsFromSelection','runConfigSource','launchSourceProfile','launchSourceNotice'].map(fn).join('\n')+
    '\n({canContinue,forkLineHtml,continueButton,runAgainButton,operatorTitle,agentAttemptCard,forkCheckChip,forkAttemptVerdict,forkPanel,runConfigDefaultsFromSelection,runConfigSource,launchSourceProfile,launchSourceNotice})',{
      esc,root:{dataset:{}},launchOptions:{dev_v2:{continue_from_attempt:true}},isTerminal:s=>s.run.status==='completed',
      navigationRecords:()=>[],taskTitle:()=> 'Task',taskName:String,displayRun:s=>s.task_id,relativeTime:()=> 'now',policyTitle:String,selectedDev:null,selectedRun:null,
      launchButton:()=>'',agentStoryReason:()=>'',loadTaskPickerManifest:()=>Promise.resolve(MANIFEST),...extra});
}
test('continuation is limited to ended, owned local Agent runs with valid attempts',()=>{
  const a=api(),s=snapshot();assert.equal(a.canContinue(s),true);
  for(const change of [{run:{status:'running'}},{lobby:{mine:false}},{dev_v2_selection:{dev_mode:'one_call'}},{dev_v2:{versions:[{version:1,valid:false}]}}])
    assert.equal(a.canContinue({...s,...change}),false);
  assert.equal(api({root:{dataset:{remote:'1'}}}).canContinue(s),false);
  assert.equal(api({launchOptions:{dev_v2:{}}}).canContinue(s),false);
  assert.match(a.continueButton(s),/data-continue-attempt data-open-fork.*Continue from an attempt…/);
  assert.match(a.continueButton(s,3),/class="op-continue op-continue-ghost" data-continue-attempt data-open-fork data-fork-version="3"[\s\S]*<svg[\s\S]*Continue from here<\/button>/);
  assert.equal(a.continueButton(s,2),'');
  assert.match(css,/\[data-lobby-view="other"\][^\n]*\[data-continue-attempt\]/);
});
test('valid attempt cards carry a ghost button in the header row and invalid attempts have no continuation',()=>{
  const a=api(),s=snapshot();
  const card=v=>a.agentAttemptCard({version:v,native_success:false,evaluator_review:{passed:false}},false,s);
  // The button is the last child of the header row: no separate row under the card title.
  assert.match(card(3),/data-agent-attempt-card="3"><div class="op-agent-card-head"><b>Attempt 3<\/b>[\s\S]*<button[^>]*data-continue-attempt[\s\S]*<svg[\s\S]*Continue from here<\/button><\/div><\/div>$/);
  assert.doesNotMatch(card(3),/op-continue-row/);
  assert.match(css,/\.op-agent-card-head \.op-continue-ghost[^{]*\{[^}]*margin-left:auto/);
  assert.doesNotMatch(card(2),/data-continue-attempt/);
  assert.doesNotMatch(a.agentAttemptCard({version:3},false,{...s,lobby:{mine:false}}),/data-continue-attempt/);
});
test('agent title offers both actions and preserves the source lineage',()=>{
  const s=snapshot();s.dev_v2_selection.fork={experiment_id:'devexp-'+'c'.repeat(24),cell:'d'.repeat(64),version:3};
  const html=api().operatorTitle(s);
  assert.match(html,/Run again with the same settings<\/button><button[^>]*data-continue-attempt/);
  assert.match(html,/class="op-lineage"[^>]*>(<svg[^>]*>[\s\S]*?<\/svg>)Continued from Attempt 3<\/a><span class="op-excluded">Excluded from comparison/);
  assert.doesNotMatch(html.replace(/<[^>]*>/g,''),/devexp-/);  // the id stays in the link target only
  assert.match(source,/data-run-badge="continued">Continued/);
  // The source run's age follows when its record is known.
  s.dev_v2_selection.fork.cell='d'.repeat(64);
  const known=api({navigationRecords:()=>[{session_id:'v2-'+'d'.repeat(64),created_at:1},{session_id:'v2-other',created_at:2}],relativeTime:v=>v===1?'2 days ago':'wrong'}).operatorTitle(s);
  assert.match(known,/Continued from Attempt 3 · 2 days ago<\/a>/);
  // The link uses the page's orange, not the old blue pill.
  assert.match(css,/\.op-page-title \.op-lineage\{[^}]*color:var\(--orange\)[^}]*text-decoration:underline/);
  assert.doesNotMatch(css,/\.op-lineage\{[^}]*#24507e/);
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
  const picker=element(),header={kicker:element(),title:element()};
  // The panel is the wizard's own section; its listeners are bound once and read the event target.
  const panel={dataset:{},innerHTML:'',listeners:{},addEventListener(name,fn){this.listeners[name]=fn;},querySelector:()=>null,querySelectorAll:()=>[]};
  const form={...fields,querySelector:selector=>selector==='[data-task-picker]'?picker:selector==='[data-fork-panel]'?panel:null};
  const urls=[];let response={attempts:s.dev_v2.versions.map(v=>({...v,outcome:'unsuccessful'})),blocked,
    created_at:1794379610,checks:[{id:'scene',level:blocked?'block':'ok',title:blocked?'Scene changed':'Scene and task match the source run (seed 102).'},
      {id:'sdk',level:'ok',title:'The SDK text the AI sees is the same as in the source attempt.'},
      {id:'execution_settings',level:'warn',title:'Execution settings differ from the source run: planner_deployment changed.',body:'The code will run with the new settings and may behave differently.'},
      {id:'excluded',level:'warn',title:'Excluded from comparison.',body:'A run that continues earlier work reuses its attention and time.'}],
    prompt_includes:['policy.py from Attempt 3 · 131 lines, as version 1','Eval’s verdict']};
  let fork,disabled=true;
  const a=api({currentSnapshot:s,launchDialog:{querySelector:()=>({querySelector:selector=>selector==='.kicker'?header.kicker:header.title})},
    fetch:async url=>{urls.push(url);if(fail)throw new Error('Offline');return {ok:true,json:async()=>response};}});
  const origin=a.runConfigSource(s.run,a.runConfigDefaultsFromSelection(s.dev_v2_selection));
  const profile={...fixtureProfile(),task_id:s.run.task_id,...(!fixture?{rules_profile:undefined}:{})};
  const match=a.launchSourceProfile(origin,offered?[profile]:[],{});
  fork=a.forkPanel(form,{version:start,source:origin,...match},()=>{disabled=!fork?.ready();});
  await new Promise(resolve=>setImmediate(resolve));
  const pick=version=>panel.listeners.click({target:{closest:()=>({disabled:false,dataset:{forkAttemptCard:String(version)}})}});
  const carry=value=>panel.listeners.change({target:{name:'fork_carry',value}});
  return {fork,form,panel,urls,picker,header,pick,carry,disabled:()=>disabled,setResponse:value=>{response=value;}};
}
test('fork dialog defaults to the last valid attempt, locks task and seed, and previews feedback',async()=>{
  const d=await dialog();
  assert.equal(d.fork.request().version,3);assert.equal(d.fork.request().carry,'feedback');assert.equal(d.fork.ready(),true);
  assert.match(d.urls[0],/fork-preview\?cell=b+&version=3&carry=feedback$/);
  assert.equal(d.form.v2_mode.value,'interactive');assert.equal(d.form.seed.value,'102');
  for(const field of ['benchmark','task_id','seed','v2_mode'])assert.equal(d.form[field].disabled,true);
  assert.equal(d.picker.hidden,true);
  const html=d.panel.innerHTML;
  // Source strip: task title, source date, attempt count and outcome; the raw run id is not shown.
  assert.match(html,/data-fork-source><span class="fk-src-icon"><svg[\s\S]*CONTINUING[\s\S]*Counter to sink · [A-Z][a-z]{2} \d+, \d\d:\d\d · 3 attempts · Not solved<\/span><span class="fk-chip fk-excluded"[^>]*>Excluded from comparison<\/span>/);
  assert.doesNotMatch(html,/devexp-/);
  assert.match(html,/<h4>Pick the starting attempt<\/h4><small>Its code becomes version 1 of the new run\. Task and scene stay the same\.<\/small>/);
  // Attempts are cards (radio buttons), the chosen one selected, the invalid one disabled.
  assert.match(html,/class="tp-item fk-att is-selected" role="radio" aria-checked="true" data-fork-attempt-card="3"/);
  assert.match(html,/class="tp-item fk-att" role="radio" aria-checked="false" data-fork-attempt-card="1" >/);
  assert.match(html,/data-fork-attempt-card="2" disabled/);
  assert.doesNotMatch(html,/<select/);
  // Task panel: locked task, seed, robot and mode.
  assert.match(html,/data-fork-conditions[\s\S]*<b class="tp-title">Counter to sink<\/b>[\s\S]*Fixed to the source run[\s\S]*class="tp-seed" aria-pressed="true" disabled>[\s\S]*102<\/button><span>· OpenArm365 · both arms · interactive<\/span>/);
  // Carry: two option cards over real radio inputs.
  assert.match(html,/class="fk-opt is-on"><input type="radio" name="fork_carry" value="feedback" checked>[\s\S]*Code \+ earlier feedback[\s\S]*what happened in Attempts 1–3/);
  assert.match(html,/class="fk-opt"><input type="radio" name="fork_carry" value="code" >[\s\S]*Code only[\s\S]*Just policy\.py from Attempt 3/);
  // What Dev will see: numbered list from prompt_includes.
  assert.match(html,/What Dev will see[\s\S]*<ol class="fk-see" data-fork-prompt><li><span class="fk-n" aria-hidden="true">1<\/span><span>policy\.py from Attempt 3<em class="fk-muted"> · 131 lines, as version 1<\/em><\/span><\/li><li><span class="fk-n" aria-hidden="true">2<\/span><span>Eval’s verdict<\/span>/);
  assert.match(html,/Not carried: anything after Attempt 3, the old Dev conversation, Memory notes from this run\./);
  assert.equal(api().forkLineHtml('Plain line'),'Plain line');
  assert.equal(api().forkLineHtml('Help <hints> · text only'),'Help &lt;hints><em class="fk-muted"> · text only</em>');
  // Checks are chips: ok green check, warn mark, short labels, full text in the tooltip; "excluded" lives in the strip.
  assert.match(html,/data-level="ok" title="Scene and task match the source run \(seed 102\)\.">✓ Same task and scene</);
  assert.match(html,/data-level="ok"[^>]*>✓ Same SDK</);
  assert.match(html,/data-level="warn" title="Execution settings differ from the source run: planner_deployment changed\. The code will run with the new settings and may behave differently\.">! Execution settings changed</);
  assert.doesNotMatch(html,/fk-check[^>]*>! Excluded/);
  assert.match(html,/class="fk-chip fk-excluded" title="Excluded from comparison\. A run that continues/);
  assert.doesNotMatch(html,/class="fk-block"/);
  assert.equal(d.disabled(),false);
});
test('check chips shorten known titles only and a block shows the full text',()=>{
  const a=api();
  assert.match(a.forkCheckChip({id:'sdk',level:'warn',title:'The SDK text the AI sees changed after the source run.',body:'x'}),/data-level="warn" title="The SDK text the AI sees changed after the source run\. x">! SDK text changed</);
  assert.match(a.forkCheckChip({id:'scene_contract',level:'block',title:'The scene contract changed.'}),/data-level="block"[^>]*>✕ The scene contract changed\.</);
  assert.match(a.forkCheckChip({id:'mystery',level:'ok',title:'Something new is fine.'}),/>✓ Something new is fine\.</);
  assert.deepEqual([...a.forkAttemptVerdict({native_success:true},{})],['Solved','green']);
  assert.deepEqual([...a.forkAttemptVerdict({evaluator_review:{passed:false}},{})],['Eval fail','red']);
  assert.deepEqual([...a.forkAttemptVerdict({valid:false},{valid:false})],['Invalid','grey']);
  assert.deepEqual([...a.forkAttemptVerdict({},{outcome:'unsuccessful'})],['Not solved','grey']);
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
  // The raw profile name stays available as the pill's tooltip; the picture comes from the task manifest.
  assert.match(d.panel.innerHTML,/data-fork-conditions[\s\S]*\.\/tasks\/cc-102-start\.jpg[\s\S]*\.\/tasks\/cc-102-done\.jpg[\s\S]*Sort the condiments[\s\S]*class="tp-badge" title="r5-fixture-20261031">R5 fixture rules[\s\S]*102[\s\S]*OpenArm365 · both arms · interactive/);
  d.form.task_id.value='pick_place_counter_to_sink';d.form.seed.value='101';d.fork.lock();
  assert.equal(d.form.task_id.value,'categorize_condiments@'+PROFILE);assert.equal(d.form.seed.value,'102');
  assert.match(source,/runConfigSource\(forkStart\?currentSnapshot\?\.run:[^\n]*,repeat\)/);
  assert.match(source,/forkStart\?'':launchSourceNotice/);
});
test('an unoffered source remains the locked source and blocks creation even when preview checks pass',async()=>{
  const d=await dialog({fixture:true,offered:false});
  assert.equal(d.fork.ready(),false);assert.equal(d.disabled(),true);
  assert.equal(d.form.task_id.value,'categorize_condiments@'+PROFILE);
  assert.match(d.panel.innerHTML,/class="fk-block" data-level="block" role="alert">Cannot continue this run: The task, robot and arm combination is no longer supported/);
  assert.match(d.panel.innerHTML,/data-level="block">✕ Source not offered/);
  assert.doesNotMatch(d.panel.innerHTML,/Choose a supported configuration/);
  const a=api(),origin=a.runConfigSource(sourceRun(),a.runConfigDefaultsFromSelection(sourceSelection()));
  const unavailableSeed=a.launchSourceProfile(origin,[{...fixtureProfile(),seeds:[101]}],{});
  assert.equal(unavailableSeed.profile,null);assert.match(unavailableSeed.reason,/102 is unavailable/);
});
test('attempt and carry changes recheck; a blocking check disables creation',async()=>{
  const d=await dialog({start:1});assert.equal(d.fork.request().version,1);
  d.setResponse({attempts:[],blocked:true,checks:[{level:'block',title:'Scene changed'}],prompt_includes:[]});
  d.carry('code');assert.equal(d.disabled(),true);assert.equal(d.fork.ready(),false);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.fork.request().carry,'code');assert.equal(d.fork.ready(),false);assert.equal(d.disabled(),true);
  assert.match(d.panel.innerHTML,/class="fk-chip fk-check" data-level="block"[^>]*>✕ Scene changed/);
  d.setResponse({attempts:[],blocked:false,checks:[],prompt_includes:[]});
  d.pick(3);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.fork.request().version,3);assert.equal(d.fork.ready(),true);
  assert.match(d.panel.innerHTML,/Not carried: earlier feedback, anything after Attempt 3, the old Dev conversation, Memory notes from this run\./);
  // A disabled (invalid) attempt card and the already chosen one do not reload the preview.
  const calls=d.urls.length;
  d.panel.listeners.click({target:{closest:()=>({disabled:true,dataset:{forkAttemptCard:'2'}})}});d.pick(3);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(d.urls.length,calls);assert.equal(d.fork.request().version,3);
  d.panel.listeners.change({target:{name:'other',value:'x'}});assert.equal(d.urls.length,calls);
});
test('preview errors keep creation disabled and launcher serialization gates readiness',async()=>{
  const d=await dialog({fail:true});assert.equal(d.disabled(),true);assert.equal(d.fork.ready(),false);assert.match(d.panel.innerHTML,/Offline/);
  assert.match(source,/if\(fork\)\{\s+if\(!fork.ready\(\)\)throw new Error\([\s\S]*?selection.fork=fork.request\(\)/);
  assert.match(source,/button.disabled=[^\n]*fork&&!fork.ready\(\)/);
});

test('the fork dialog is the normal four-step wizard with a source step 01',()=>{
  // Steps 02-04 are the normal ones; only step 01 is renamed.
  assert.match(source,/const FORK_WIZARD_STEPS=\[\{\.\.\.LAUNCH_WIZARD_STEPS\[0\],key:'source',label:'Source',note:'Attempt and carry'\},\.\.\.LAUNCH_WIZARD_STEPS\.slice\(1\)\];/);
  assert.match(source,/function bindLaunchWizard\(dialog,form,steps=LAUNCH_WIZARD_STEPS\)/);
  assert.match(source,/bindLaunchWizard\(launchDialog,form,forkStart\?FORK_WIZARD_STEPS:LAUNCH_WIZARD_STEPS\)/);
  assert.doesNotMatch(source.slice(source.indexOf('  function bindLaunchWizard('),source.indexOf('  // 11-11 (user): the read-only limits')),/LAUNCH_WIZARD_STEPS\[/);
  // The panel exists before the wizard collects its sections, so it belongs to step 01.
  assert.match(source,/forkStart\?'<section class="ab-launch-section ab-fork" data-fork-panel><\/section>':''\}<section class="ab-launch-section" data-run-config-section="what">/);
  // A blocked continuation cannot move on from the source step.
  assert.match(source,/nextStep=form\.querySelector\('\[data-wizard-next\]'\);if\(nextStep&&fork\)nextStep\.disabled=!fork\.ready\(\)/);
  // Create/Start stay gated by the readiness check, and the request payload is unchanged.
  assert.match(source,/selection\.fork=fork\.request\(\)/);
  assert.match(source,/request:\(\)=>\(\{experiment_id:eid,cell:key,version:state\.version,carry:state\.carry\}\)/);
});
test('the fork dialog reuses the task picker and wizard classes and defines its own few',()=>{
  for(const reused of ['tp-item','tp-preview-card','tp-pics','tp-seed','tp-badge','tp-title'])assert.match(source.slice(source.indexOf('  function forkPanel(')),new RegExp(reused));
  for(const own of ['.fk-src','.fk-cols','.fk-att-top','.fk-reason','.fk-opt','.fk-see','.fk-n','.fk-checks','.fk-chip'])assert.ok(css.includes(own),own);
  assert.match(css,/\.fk-reason\{[^}]*-webkit-line-clamp:2/);
  // Real radio inputs stay in the document for assistive technology; focus is shown on the card.
  assert.match(css,/\.fk-opt input\{[^}]*opacity:0/);
  assert.match(css,/\.fk-opt:has\(input:focus-visible\)\{outline/);
  // The old fieldset/select look is gone.
  assert.doesNotMatch(css,/\.ab-fork-(carry|checks|strip)/);
  assert.doesNotMatch(source,/ab-fork-(carry|checks|strip)|data-fork-attempt[^-]/);
});
test('the fork dialog styles use theme tokens so light and dark both work',()=>{
  const block=css.slice(css.indexOf('/* Continue from an attempt (11-14)'),css.indexOf('</style>',css.indexOf('/* Continue from an attempt (11-14)')));
  assert.ok(block.length>500);
  // Only the amber token and the selected-card chip tints are literal colours, each with a dark counterpart.
  const literal=[...block.matchAll(/#[0-9a-fA-F]{6}\b/g)].map(m=>m[0].toLowerCase());
  assert.deepEqual([...new Set(literal)].sort(),['#3f6a35','#8a5a12','#a23f32','#a8691a','#b6d9a8','#e0a24a','#f0b9a9','#f0d19b']);
  assert.match(block,/\[data-theme="dark"\] \.ab-launch-dialog \.ab-fork\{--fk-amber/);
  // The generated dark theme carries the lineage link colour.
  assert.match(css,/\[data-theme="dark"\] \.op-page-title \.op-lineage\{[^}]*color:var\(--orange\)|\[data-theme="dark"\] \.op-page-title \.op-lineage\{[^}]*#ec6a43/);
});
