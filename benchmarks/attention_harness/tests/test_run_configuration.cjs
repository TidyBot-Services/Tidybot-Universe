const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const pure=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const {runConfigDefaultsFromSelection,runConfigSeedOptions,runConfigSummary}=vm.runInNewContext(pure+'\n({runConfigDefaultsFromSelection,runConfigSeedOptions,runConfigSummary})');

const repeatBlock=()=>source.match(/      if\(repeat && sourceProfile\) \{[\s\S]*?\n      \}(?=\n      form\.benchmark\.addEventListener)/)[0];
const options=(...values)=>values.map(value=>({value}));
function repeatForm(values={}) {
  const field=value=>({value}),select=(value,...rest)=>({value,options:options(value,...rest)});
  return {seed:{value:'102',options:options('114')},v2_policy:select('autonomous','reactive_help'),model_source:field('codex'),dev_model:select('gpt-6.1-sol','gpt-6-astra'),
    eval_model:select('gpt-6.1-sol','gpt-6-astra'),advisor_model:select('gpt-6.1-sol','gpt-6-astra'),max_attempts:field('10'),help_count:field('5'),
    wall_seconds:field('21600'),goal_id:field(''),name_suffix:field(''),v2_mode:field('experiment'),...values};
}
const replay=(repeat,form,keys={labSource(){}})=>{vm.runInNewContext(repeatBlock(),{repeat,sourceProfile:{channel:'dev_v2'},form,keys,syncSeedOptions:()=>{},updatePreview:()=>{},showQuota:()=>{}});};

test('Run again reads a saved Agent selection and falls back for absent values',()=>{
  const values=runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[114],policies:['reactive_help'],mode:'interactive',model_source:'codex',help_budget:3,
    dev_model:'gpt-6-astra',eval_model:'gpt-6.1-sol',advisor_model:'gpt-6-astra',goal_key:'goal-1',name_suffix:'again',agent_limits:{versions:3,wall_seconds:7200}});
  assert.equal(values.seed,114);assert.equal(values.policy,'reactive_help');assert.equal(values.mode,'interactive');
  assert.equal(values.modelSource,'codex');assert.equal(values.help,3);assert.equal(values.goalId,'goal-1');
  assert.equal(values.devModel,'gpt-6-astra');assert.equal(values.advisorModel,'gpt-6-astra');assert.equal(values.agentLimits.versions,3);
  const missing=runConfigDefaultsFromSelection({});
  assert.equal(missing.seed,undefined);assert.equal(missing.modelSource,'codex');assert.equal('agentLimits' in missing,false);
  assert.match(source,/data-open-run-again>Run again with the same settings/);
  assert.match(source,/button\.hasAttribute\('data-open-run-again'\)/);
});

test('only an ended Agent run offers Run again; nothing offers to continue from an attempt',()=>{
  const fragment=source.slice(source.indexOf('  function runAgainButton('),source.indexOf('\n  function ',source.indexOf('  function runAgainButton(')+10));
  const {runAgainButton}=vm.runInNewContext(fragment+'\n({runAgainButton})',{isTerminal:snapshot=>snapshot.ended});
  const run=mode=>({ended:true,dev_v2:{versions:[{version:1}]},dev_v2_selection:mode?{dev_mode:mode}:{}});
  assert.match(runAgainButton(run('agent')),/data-open-run-again/);
  assert.equal(runAgainButton(run(null)),'');                  // an older One call run
  assert.equal(runAgainButton({...run('agent'),ended:false}),'');
  assert.equal(runAgainButton({ended:true}),'');              // a Fixed run has no Dev v2 cell
  assert.doesNotMatch(source,/data-open-fork|continueButton|forkPanel/);
});

test('Run again applies saved fields to the existing dialog before Start',()=>{
  const form=repeatForm(),repeat=runConfigDefaultsFromSelection({dev_mode:'agent',seeds:[114],policies:['reactive_help'],mode:'interactive',help_budget:3,
    dev_model:'gpt-6-astra',eval_model:'gpt-6-astra',advisor_model:'gpt-6-astra',goal_key:'goal-1',name_suffix:'again'});
  replay(repeat,form);
  assert.equal(form.seed.value,'114');assert.equal(form.v2_policy.value,'reactive_help');assert.equal(form.model_source.value,'codex');
  assert.equal(form.dev_model.value,'gpt-6-astra');assert.equal(form.help_count.value,'3');assert.equal(form.goal_id.value,'goal-1');
  assert.equal(form.name_suffix.value,'again');assert.equal(form.max_attempts.value,'10');assert.equal(form.wall_seconds.value,'21600');
});

test('Run again keeps a reduced budget and the PARCC source, and an own-key model never blanks a select',()=>{
  const lab=model=>({credential:'lab',provider:'parcc',model}),seen=[];
  const form=repeatForm(),repeat=runConfigDefaultsFromSelection({dev_mode:'agent',dev_model:'parcc/GLM',eval_model:'parcc/Qwen',advisor_model:'parcc/GLM',seeds:[102],policies:['autonomous'],
    agent_limits:{versions:3,wall_seconds:7200},agent_provider:{roles:{dev:lab('parcc/GLM'),eval:lab('parcc/Qwen'),advisor:lab('parcc/GLM')}}});
  assert.equal(repeat.modelSource,'parcc');
  replay(repeat,form,{labSource:(...args)=>seen.push(args)});
  assert.equal(form.model_source.value,'parcc');assert.equal(form.max_attempts.value,'3');assert.equal(form.wall_seconds.value,'7200');
  assert.equal(JSON.stringify(seen),JSON.stringify([['parcc',{dev:'parcc/GLM',eval:'parcc/Qwen',advisor:'parcc/GLM'}]]));
  const mixed=runConfigDefaultsFromSelection({dev_mode:'agent',agent_provider:{roles:{dev:lab('parcc/GLM')}}});
  assert.equal(mixed.modelSource,'codex');
  const own=repeatForm();
  replay(runConfigDefaultsFromSelection({dev_mode:'agent',dev_model:'custom-model',eval_model:'custom-model',advisor_model:'custom-model'}),own);
  assert.equal(own.dev_model.value,'gpt-6.1-sol');assert.equal(own.eval_model.value,'gpt-6.1-sol');
});

test('unregistered experiment seed is labelled; interactive seed is selectable',()=>{
  const seeds=[101,102,103,114],registered=[101,102,103];
  const frozen=runConfigSeedOptions(seeds,registered,'experiment');
  assert.equal(frozen.find(row=>row.seed===114).unregistered,true);
  assert.match(frozen.find(row=>row.seed===114).title,/no scene contract · interactive only/);
  assert.equal(runConfigSeedOptions(seeds,registered,'interactive').find(row=>row.seed===114).unregistered,false);
});

test('footer summary reflects every visible choice',()=>{
  assert.equal(runConfigSummary({robot:'OpenArm365',arm:'both arms',seed:102,layout:11,style:34,versions:5,help:5}),
    'OpenArm365 · both arms · seed 102 · layout 11 / style 34 · up to 5 versions · 5 help requests');
});

test('the Codex source says what it costs',()=>{
  assert.match(source,/name="model_source" aria-label="Model source"/);
  assert.match(source,/Codex results are not comparable with PARCC results and use the Codex subscription quota/);
});
