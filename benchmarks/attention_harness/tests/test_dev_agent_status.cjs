// 10-30 sol3: attempt outcome follows the native task check; an Eval that did not run is labelled with its reason.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to));
const api=vm.runInNewContext(slice('  function agentStripStages(','  function operatorEvalReview(')+slice('  function enterAgentWorkspace(','  async function agentWorkspaceSnapshot(')+
  '\n({agentEvalStrip,agentAttemptCard})',{esc});
const sol3v3={version:3,native_success:true,eval_unavailable:'disk full (ENOSPC)',evaluator_review:{status:'unavailable',passed:null},story:{short:'solved'},eval_stages:[]};
test('sol3 attempt 3: solved by the task check, Eval did not run with its reason',()=>{
  const card=api.agentAttemptCard(sol3v3,false),strip=api.agentEvalStrip(sol3v3,'a3');
  assert.match(card,/class="op-agent-chip solved">Solved · Eval did not run</);
  assert.match(card,/data-eval-did-not-run>Eval did not run · disk full \(ENOSPC\)</);
  assert.match(strip,/data-result="pass">Completed<\/span><span class="op-review-tag" data-eval-did-not-run data-result="unavailable">Eval did not run · disk full \(ENOSPC\)/);
  assert.doesNotMatch(card+strip,/awaiting/i);
});
test('r6-style attempts are unchanged, and an Eval pass never overrides a failed task check',()=>{
  const solved={version:3,native_success:true,evaluator_review:{passed:true},story:{short:'solved'},eval_stages:[]};
  assert.match(api.agentAttemptCard(solved,false),/>Solved · Eval pass</);
  assert.doesNotMatch(api.agentAttemptCard(solved,false)+api.agentEvalStrip(solved,'a'),/did not run/);
  const failed={version:1,native_success:false,evaluator_review:{passed:false},story:{short:'grasp not confirmed'},eval_stages:[]};
  assert.match(api.agentAttemptCard(failed,false),/>Not solved · Eval fail<\/span><span class="op-agent-chip failed" data-agent-failure-reason>grasp not confirmed</);
  const disagree={version:2,native_success:false,evaluator_review:{passed:true},story:{short:'Eval passed, task check failed'},eval_stages:[]};
  assert.match(api.agentEvalStrip(disagree,'a'),/data-result="fail">Not completed</);
});
test('a finished run shows no waiting/awaiting help effect',()=>{
  assert.match(source,/no_next:\['— Run ended before another attempt','mid'\]/);
  assert.match(source,/!next\?\(ended\?'no_next':'waiting'\)/);
  assert.match(source,/isTerminal\(snapshot\) \? 'No native outcome recorded' : 'Native outcome awaiting verification'/);
});
test('a generic "not solved" reason is not repeated next to the outcome; a named cause still shows (10-30)',()=>{
  const stages=[{stages:[0],label:'Find objects',state:'completed',cause:''},{stages:[1],label:'Pre-grasp',state:'failed',cause:'not solved'}];
  const generic={version:2,native_success:false,evaluator_review:{passed:false},story:{short:'not solved'},eval_stages:stages};
  const card=api.agentAttemptCard(generic,false),strip=api.agentEvalStrip(generic,'a2');
  assert.match(card,/Not solved · Eval fail/);
  assert.doesNotMatch(card,/data-agent-failure-reason/);
  assert.match(strip,/<b>Stopped at Pre-grasp<\/b><\/p>/);
  assert.doesNotMatch(strip,/· not solved/);
  const named={...generic,story:{short:'grasp not confirmed'}};
  assert.match(api.agentAttemptCard(named,false),/data-agent-failure-reason>grasp not confirmed</);
  assert.match(api.agentEvalStrip(named,'a2'),/Stopped at Pre-grasp<\/b> · grasp not confirmed/);
});

