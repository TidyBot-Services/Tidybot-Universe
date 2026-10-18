const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const pure=source.slice(source.indexOf('  // Values shared by the visible limits'),source.indexOf('  function bindLaunch()'));
const {runConfigBudget,runConfigSeedOptions,runConfigSummary}=vm.runInNewContext(pure+'\n({runConfigBudget,runConfigSeedOptions,runConfigSummary})');
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
