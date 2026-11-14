const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fragment=source.slice(source.indexOf('  function agentMemoryUse('),source.indexOf('  function workspaceHTML('));
const api=vm.runInNewContext(fragment+'\n({agentMemoryUse,operatorThreeParts})',{
  esc,DEBUG_VIEW:false,operatorHeading:(n,title)=>`<div class="panel-title op-heading"><h3>${n} ${title}</h3><span></span></div>`
});
const snapshot={agent:{memory_use:[{version:1,notes:[
  {memory_id:'mem-1',title:'Grasp <carefully>',level:'L1',referenced:'explicit',evidence:[{file:'v1/policy.py',line:4,excerpt:'# Follow mem-1 <script>'}]},
  {memory_id:'mem-2',title:'Cabinet',level:'L2',referenced:'none',evidence:[],behavioural_hint:[{file:'trace.json',event_index:7,excerpt:'Matched retreat'}]}
]}]}};

test('received/used count, note titles, excerpts, and not used are plain and escaped',()=>{
  const text=api.agentMemoryUse(snapshot);
  assert.match(text,/Memory: received 2 notes · used 1/);
  assert.match(text,/Grasp &lt;carefully&gt;/);
  assert.match(text,/v1\/policy.py:4/);
  assert.match(text,/# Follow mem-1 &lt;script&gt;/);
  assert.match(text,/not used/);
  assert.match(text,/Behavioural hint \(weaker\)/);
  assert.doesNotMatch(text,/<script>/);
});

test('per-version evidence and event index remain visible, repeated deliveries are labelled',()=>{
  const copy=JSON.parse(JSON.stringify(snapshot));
  copy.agent.memory_use.push({version:2,notes:[{...copy.agent.memory_use[0].notes[0],evidence:[{file:'agent/private/turns/t000002/events.jsonl',event_index:11,excerpt:'Following mem-1'}]}]});
  const text=api.agentMemoryUse(copy);
  assert.match(text,/received 3 notes · used 2/);
  assert.match(text,/v2 · L1/);
  assert.match(text,/event 11/);
  assert.match(text,/Counts include each version’s delivery/);
});

test('all analysis errors show, including write failures and no completed versions',()=>{
  const copy=JSON.parse(JSON.stringify(snapshot));
  copy.agent.memory_use[0].errors=[{error:'Malformed events <oops>'},{error:'Missing code'}];
  copy.agent.errors=[{source:'Memory analysis',error:{version:1,error:'Disk failure'}}];
  const text=api.agentMemoryUse(copy);
  assert.equal((text.match(/role="alert"/g)||[]).length,3);
  for(const error of ['Malformed events &lt;oops&gt;','Missing code','Disk failure'])assert.ok(text.includes(error));
  const empty=api.agentMemoryUse({agent:{errors:copy.agent.errors}});
  assert.match(empty,/received 0 notes · used 0/);
  assert.match(empty,/Disk failure/);
});

test('the Memory element is inside Progress and help, absent from one-call pages',()=>{
  const regions=['<section>Setup</section>','<section>Overview</section>','<section>Replay</section>','<section>Help</section>','<section>Resources</section>'];
  const result=api.operatorThreeParts(regions,snapshot);
  const progress=result.slice(result.indexOf('data-part="progress"'),result.indexOf('data-part="setup"'));
  assert.match(progress,/data-memory-use/);
  assert.match(progress,/Memory: received 2 notes · used 1/);
  assert.equal(api.agentMemoryUse({}), '');
});

test('memory styles and errors have generated dark-theme rules',()=>{
  const dark=html.match(/<style id="ab-dark-theme">([\s\S]*?)<\/style>/)[1];
  assert.match(html,/#attentionbench-clear \.op-memory-error\{[^}]*#a23f32/);
  assert.match(dark,/\[data-theme="dark"\][^{]*\.op-memory-error\{[^}]*#e08670/);
  assert.match(dark,/\.op-memory-error\{[^}]*#3a2824/);
});

test('declined citations are counted separately and their evidence is visible',()=>{
  const copy=JSON.parse(JSON.stringify(snapshot));
  copy.agent.memory_use[0].notes[0]={...copy.agent.memory_use[0].notes[0],referenced:'none',evidence:[],
    considered_not_used:true,considered_not_used_evidence:[{file:'v1/policy.py',line:9,excerpt:'# Not using note mem-1: no carried <object>.'}]};
  const text=api.agentMemoryUse(copy);
  assert.match(text,/received 2 notes · used 0 · considered, not used 1/);
  assert.match(text,/considered · not used/);
  assert.match(text,/Not using note mem-1: no carried &lt;object&gt;/);
  assert.match(text,/v1\/policy.py:9/);
});
