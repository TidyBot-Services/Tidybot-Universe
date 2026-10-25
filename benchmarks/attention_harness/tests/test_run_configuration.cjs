const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const pure=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const {runConfigBudget,runConfigDefaultModelMinutes,runConfigDefaultsFromSelection,runConfigSeedOptions,runConfigSummary}=vm.runInNewContext(pure+'\n({runConfigBudget,runConfigDefaultModelMinutes,runConfigDefaultsFromSelection,runConfigSeedOptions,runConfigSummary})');
const profile={sdk_call_limit:320,control_step_limit:9600,attempt_wall_seconds:1920,startup_cleanup_seconds:240};
const defaults={hint_human_seconds:0,provider_tokens:0,input_tokens:0,output_tokens:0};
const budget=(versions,help=5,reviewer=true)=>runConfigBudget({versions,help,waitSeconds:300,modelMinutes:30,profile,defaults,reviewer,policyCount:2});

test('version count scales every robot and reviewer quota',()=>{
  for(const n of [1,5,10]){
    const {cell,experiment}=budget(n);
    assert.equal(cell.dev_calls,n);
    assert.equal(cell.executions,n);
    assert.equal(cell.eval_calls,n);
    assert.equal(cell.sdk_calls,n*profile.sdk_call_limit);
    assert.equal(cell.control_steps,n*profile.control_step_limit);
    assert.equal(cell.execution_seconds,n*(1920+240));
    assert.equal(cell.model_seconds,1800);
    assert.equal(cell.provider_tokens,200000*n);
    assert.equal(cell.input_tokens,170000*n);
    assert.equal(cell.output_tokens,30000*n);
    assert.equal(experiment.sdk_calls,2*cell.sdk_calls);
    assert.equal(experiment.help_count,2*cell.help_count);
  }
  assert.equal(budget(5,5,false).cell.eval_calls,0);
});

test('help count boundaries scale credits, advisor and waiting budget',()=>{
  for(const help of [1,10]){
    const {cell}=budget(5,help);
    assert.equal(cell.help_count,help);
    assert.equal(cell.help_credits,help);
    assert.equal(cell.advisor_calls,help);
    assert.equal(cell.help_wait_seconds,help*120+5*300);
  }
});

test('thinking time default follows the frozen cell budget and rounds up',()=>{
  assert.equal(runConfigDefaultModelMinutes({default_cell_budget:{model_seconds:3000}}),50);
  assert.equal(runConfigDefaultModelMinutes({default_cell_budget:{model_seconds:2761}}),47);
  assert.equal(runConfigDefaultModelMinutes({default_cell_budget:{model_seconds:2400}}),40);
  assert.equal(runConfigDefaultModelMinutes({default_cell_budget:{model_seconds:1800}}),30);
  assert.equal(runConfigBudget({versions:5,help:5,waitSeconds:300,modelMinutes:50,profile,defaults,reviewer:true}).cell.model_seconds,3000);
});
test('Run again reads saved Dev v2 selection and falls back for absent values',()=>{
  const selection={seeds:[114],policies:['reactive_help'],mode:'interactive',model_source:'codex',help_budget:3,
    human_wait_seconds:240,eval_agent:{enabled:false},configuration:{goal_id:'goal-1',max_iterations:4,
      wall_seconds:7200,cell_budget:{model_seconds:2761,dev_calls:4},experiment_budget:{dev_calls:4}}};
  const values=runConfigDefaultsFromSelection(selection);
  assert.equal(values.seed,114);assert.equal(values.policy,'reactive_help');assert.equal(values.mode,'interactive');
  assert.equal(values.modelSource,'codex');assert.equal(values.versions,4);assert.equal(values.help,3);
  assert.equal(values.waitSeconds,240);assert.equal(values.wallSeconds,7200);assert.equal(values.modelMinutes,47);
  assert.equal(values.reviewer,false);assert.equal(values.cellBudget.dev_calls,4);
  assert.equal(values.experimentBudget.dev_calls,4);assert.equal(values.goalId,'goal-1');
  const missing=runConfigDefaultsFromSelection({});
  assert.equal(missing.seed,undefined);assert.equal(missing.versions,undefined);assert.equal(missing.modelMinutes,null);
  assert.match(source,/data-open-run-again>Run again with the same settings/);
  assert.match(source,/button\.hasAttribute\('data-open-run-again'\)/);
  assert.match(source,/set\('max_attempts',repeat\.versions\)/);
  assert.match(source,/manualBudget=!!\(repeat\.cellBudget\|\|repeat\.experimentBudget\)/);
});
test('Run again applies saved fields to the existing dialog before Start',()=>{
  const fragment=source.match(/      if\(repeat && sourceProfile\?\.channel==='dev_v2'\) \{[\s\S]*?\n      \}(?=\n      const fork=)/)[0];
  const field=()=>({value:'default'}),form={seed:{value:'102',options:[{value:'114'}]},v2_policy:{value:'autonomous',options:[{value:'reactive_help'}]},
    model_source:{value:'parcc',querySelector:()=>({disabled:false})},max_attempts:field(),help_count:field(),human_wait_seconds:field(),
    wall_seconds:field(),model_seconds_minutes:field(),goal_id:field(),name_suffix:field(),v2_mode:field(),eval_agent_enabled:{checked:true}};
  const cell={dataset:{v2Cell:'dev_calls'},value:'default'},experiment={dataset:{v2Experiment:'dev_calls'},value:'default'};
  form.querySelectorAll=selector=>selector==='[data-v2-cell]'?[cell]:[experiment];
  const repeat=runConfigDefaultsFromSelection({seeds:[114],policies:['reactive_help'],mode:'interactive',model_source:'codex',
    help_budget:3,human_wait_seconds:240,eval_agent:{enabled:false},name_suffix:'again',configuration:{goal_id:'goal-1',
      max_iterations:4,wall_seconds:7200,cell_budget:{model_seconds:2761,dev_calls:4},experiment_budget:{dev_calls:4}}});
  const context={repeat,sourceProfile:{channel:'dev_v2'},form,manualBudget:false,syncSeedOptions:()=>{},updatePreview:()=>{},showQuota:()=>{}};
  vm.runInNewContext(fragment,context);
  assert.equal(form.seed.value,'114');assert.equal(form.v2_policy.value,'reactive_help');assert.equal(form.model_source.value,'codex');
  assert.equal(form.max_attempts.value,'4');assert.equal(form.help_count.value,'3');assert.equal(form.human_wait_seconds.value,'240');
  assert.equal(form.wall_seconds.value,'7200');assert.equal(form.model_seconds_minutes.value,'47');assert.equal(form.goal_id.value,'goal-1');
  assert.equal(form.name_suffix.value,'again');assert.equal(form.eval_agent_enabled.checked,false);
  assert.equal(cell.value,4);assert.equal(experiment.value,4);assert.equal(context.manualBudget,true);
});

test('unregistered experiment seed is labelled; interactive seed is selectable',()=>{
  const seeds=[101,102,103,114],registered=[101,102,103];
  const frozen=runConfigSeedOptions(seeds,registered,'experiment');
  assert.equal(frozen.find(row=>row.seed===114).unregistered,true);
  assert.match(frozen.find(row=>row.seed===114).title,/no scene contract · interactive only/);
  assert.equal(runConfigSeedOptions(seeds,registered,'interactive').find(row=>row.seed===114).unregistered,false);
});

test('footer summary reflects every visible choice',()=>{
  assert.equal(runConfigSummary({robot:'OpenArm365',arm:'both arms',seed:102,layout:11,style:34,versions:5,help:5,reviewer:true}),
    'OpenArm365 · both arms · seed 102 · layout 11 / style 34 · up to 5 versions · 5 help requests · reviewer on');
});

test('Codex source is labelled, sent per run, and refused outside the Dev tier',()=>{
  assert.match(source,/name="model_source" aria-label="Model source"/);
  assert.match(source,/Codex subscription \(gpt-6-astra, high effort\)/);
  assert.match(source,/Codex results are not comparable with PARCC results and use the Codex subscription quota/);
  assert.match(source,/options\?\.dev_v2\?\.budget_tier!=='dev'/);
  assert.match(source,/selection\.model_source='codex'/);
  assert.match(source,/selection\.model_source='codex'/);
  assert.match(runConfigSummary({robot:'OpenArm365',arm:'both arms',seed:102,layout:11,style:34,versions:5,help:5,reviewer:true,modelSource:'codex'}),
    /Codex subscription \(gpt-6-astra, high effort\)$/);
});
