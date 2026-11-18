const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fragment=source.slice(source.indexOf('  function agentMemoryUse('),source.indexOf('  function workspaceHTML('));
// 11-18: the fragment now also wires the right-side Conversation panel and the replay split on load,
// so it needs inert DOM stubs; without window.Chat02 the panel is off and the 02 Memory line is shown.
const node={addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null,style:{setProperty(){},removeProperty(){}}};
const api=vm.runInNewContext(fragment+'\n({agentMemoryUse,operatorThreeParts})',{
  esc,DEBUG_VIEW:false,operatorHeading:(n,title)=>`<div class="panel-title op-heading"><h3>${n} ${title}</h3><span></span></div>`,
  view:node,root:node,sidebar:node,sidebarToggle:node,window:{innerWidth:1600,addEventListener(){}},ResizeObserver:class{observe(){}},localStorage:{getItem:()=>null,setItem(){},removeItem(){}},
  location:{search:''},MutationObserver:class{observe(){}},setInterval:()=>0,getComputedStyle:()=>({getPropertyValue:()=>''}),
  requestAnimationFrame:()=>0,isTerminal:()=>false,reuseConfirm:()=>'',currentSnapshot:null
});
const snapshot={run:{run_id:'run-1'},agent:{memory_use:[{version:1,notes:[
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
  // 11-18 (user, ui改动 02): the Memory line is one summary; the explanatory "Counts include…" sentence was dropped.
  assert.equal((text.match(/data-memory-note /g)||[]).length,3);
});

test('all analysis errors show, including write failures and no completed versions',()=>{
  const copy=JSON.parse(JSON.stringify(snapshot));
  copy.agent.memory_use[0].errors=[{error:'Malformed events <oops>'},{error:'Missing code'}];
  copy.agent.errors=[{source:'Memory analysis',error:{version:1,error:'Disk failure'}}];
  const text=api.agentMemoryUse(copy);
  assert.equal((text.match(/role="alert"/g)||[]).length,3);
  for(const error of ['Malformed events &lt;oops&gt;','Missing code','Disk failure'])assert.ok(text.includes(error));
  const empty=api.agentMemoryUse({agent:{errors:copy.agent.errors}});
  // 11-18 (user, ui改动 02): no "received 0" summary without notes, but the error stays visible.
  assert.doesNotMatch(empty,/received 0 notes/);
  assert.match(empty,/role="alert">Memory analysis error: [^<]*Disk failure/);
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

test('Memory monitor cards show generic source metadata and the selected version citation',()=>{
 const monitor=require('../ui/memory.js');
 const data=monitor.adapt({notes:[{id:'p-1',status:'unvalidated',level:'same_task',source_verified:true,
  provider:'future-provider',skill_tags:['cabinet'],applies_when:'Inside <cabinet>',source_run:'devexp-'+'1'.repeat(24),source_version:3,
  dev_use:[{version:1,memory_id:'p-1',use:'not_mentioned'},
    {version:2,memory_id:'p-1',use:'used',reason:'Using: retreat <slowly>',errors:['broken <audit>']},
    {version:3,memory_id:'p-1',use:'considered_not_used',reason:'Not using: no object'}]}]});
 const card=monitor.cardHtml(data.notes[0],false,false,2);
 for(const text of ['Skills: cabinet','Reuse level: same_task','Inside &lt;cabinet&gt;','Provider: future-provider','Dev v2: Using note p-1','retreat &lt;slowly&gt;','broken &lt;audit&gt;'])assert.ok(card.includes(text),text);
 assert.doesNotMatch(card,/Dev v1:|Dev v3:|<slowly>|<audit>/);
 assert.match(monitor.cardHtml(data.notes[0],false,false,3),/Dev v3: Not using note p-1/);
 assert.match(monitor.cardHtml(data.notes[0]),/Dev v1: Not mentioned/);
 const local=monitor.adapt({run_notes:[{id:'r-1',help_version:2,dev_use:[{version:3,memory_id:'r-1',use:'pending',errors:['memory_use.json is missing']}]}]}).notes[0];
 assert.match(monitor.cardHtml(local),/Dev v3: Audit pending/);
 assert.match(monitor.historyErrorsHtml([{experiment_id:'run',version:2,error:'bad <json>'}]),/run · v2 · bad &lt;json&gt;/);
});

test('current-run prefix follows explicit not-used over a simultaneous reference',()=>{
 const monitor=require('../ui/memory.js');
 const row={version:4,memory_id:'r-1',use:'used',considered_not_used:true,reason:'Not using: alternate trials failed.'};
 const card=monitor.cardHtml(monitor.adapt({run_notes:[{id:'r-1',dev_use:[row]}]}).notes[0],false,true,4);
 assert.match(card,/Dev v4: Not using note r-1 — alternate trials failed\./);
 assert.doesNotMatch(card,/Dev v4: Using|— Not using:|Also considered/);
 const fallback=monitor.devUseHtml([{...row,decision_source:'turn_log'}]);
 assert.match(fallback,/class="turn-log-tag"/);
 assert.match(fallback,/from Dev's turn log, not yet audited/);
 assert.doesNotMatch(fallback,/role="alert"/);
});
