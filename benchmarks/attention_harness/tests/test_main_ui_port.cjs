// Pure operator view-model checks; no DOM, browser, network or simulator.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const operatorBlock=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const api=vm.runInNewContext(operatorBlock+'\n({waitCountdown,waitingRequest,resourceRows,operatorResources})',{
  DEBUG_VIEW:false,esc,number:String,pct:()=>0,operatorTime:n=>`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`,
  operatorHeading:()=>'',launches:[],isTerminal:()=>false,devEndSummary:()=>({key:''})
});
const hashBlock=source.slice(source.indexOf('  function experimentFromHash('),source.indexOf('  const initialExperiment='));
const hash=vm.runInNewContext(hashBlock+'\n({experimentFromHash,restoredExperiment})');

test('absolute deadline gives remaining seconds, late state, ended window and ring offset',()=>{
  const start=Date.parse('2026-10-19T10:00:00Z'),end='2026-10-19T10:01:00Z';
  const first=api.waitCountdown(end,start,60),late=api.waitCountdown(end,start+30_000,60);
  assert.equal(first.remaining,60);assert.equal(first.late,false);assert.equal(first.dashoffset,0);
  assert.equal(late.remaining,30);assert.equal(late.late,true);assert.ok(Math.abs(late.dashoffset-Math.PI*9)<1e-9);
  assert.equal(api.waitCountdown(end,start+60_000,60).ended,true);
  assert.equal(api.waitCountdown(end,start+64_999,60).gone,false);
  assert.equal(api.waitCountdown(end,start+65_000,60).gone,true);
  assert.equal(api.waitCountdown(end,start+31_000,60).remaining,29);
});

test('only human pending deadlines, including planner approval, drive the clock',()=>{
  const deadline=1_700_000_060;
  assert.equal(api.waitingRequest({dev_v2:{planner:{requests:[{request_type:'approval',state:'waiting',deadline_epoch:deadline,created_at:deadline-60}]}}}).deadline,deadline);
  assert.equal(api.waitingRequest({dev_v2:{planner:{requests:[{request_type:'hint',state:'waiting',deadline_epoch:deadline}]}}}),null);
  assert.equal(api.waitingRequest({requests:[{state:'pending',mode:'live_human_first',deadline_at:deadline}]}).deadline,deadline);
  assert.equal(api.waitingRequest({requests:[{state:'pending',mode:'benchmark_proxy',deadline_at:deadline}]}),null);
});

test('resource rows include waiting time after help and free models are recorded',()=>{
  const snapshot={run:{run_id:'v2'},dev_session:{},dev_v2:{experiment_id:'devexp-123',ledger:{}},dev_eval_enabled:true,attempts:[],
    resources:{executions:{used:1,limit:3},eval_calls:{used:2,limit:4},help_count:{used:1,limit:3},help_wait_seconds:{used:61,limit:120},
      model_calls:{used:2,limit:5},execution_seconds:{used:70,limit:180},wall_seconds:{used:90,limit:300},tokens:{used:30300,limit:220000}},
    model_token_accounting:{Dev:'不计限额，仅记录',Advisor:'不计限额，仅记录',Eval:'不计限额，仅记录'}};
  const rows=api.resourceRows(snapshot);
  assert.equal(rows.findIndex(r=>r[0]==='help_wait_seconds'),rows.findIndex(r=>r[0]==='help_count')+1);
  const free=api.operatorResources(snapshot);
  assert.match(free,/data-dev-budget-key="help_wait_seconds"[\s\S]*1:01[\s\S]*2:00/);
  assert.match(free,/AI usage: 30.3k tokens/);
  assert.match(free,/data-free-models-tag/);
  assert.match(free,/Reviews requested[\s\S]*recorded/);
  assert.doesNotMatch(free,/Reviews requested[\s\S]{0,140}\/ 4/);
  snapshot.model_token_accounting.Eval='计入限额';
  const metered=api.operatorResources(snapshot);
  assert.doesNotMatch(metered,/data-free-models-tag/);
  assert.match(metered,/AI usage: 30.3k of 220k tokens/);
});

test('experiment hash restores only a listed exact id and otherwise falls back',()=>{
  const id='devexp-'+ 'a'.repeat(24),items=[{experiment_id:id}];
  assert.equal(hash.experimentFromHash('#exp='+id),id);
  assert.equal(hash.restoredExperiment('#exp='+id,items),id);
  assert.equal(hash.restoredExperiment('#exp=devexp-'+ 'b'.repeat(24),items),null);
  assert.equal(hash.experimentFromHash('#exp=bad'),null);
  assert.equal(hash.experimentFromHash('#request'),null);
});
