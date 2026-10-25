const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const monitor=require('../ui/memory.js');
const root=path.join(__dirname,'..');
const live=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
const page=fs.readFileSync(path.join(root,'ui/memory.html'),'utf8');
const css=fs.readFileSync(path.join(root,'ui/memory.css'),'utf8');

function payload(notes){return {run:{state:'running',versions:[1,2,3,4,5].map(n=>({n,note_ids:n===1?['a']:n===2?['a','b']:[]}))},notes,events:[]}}
test('adapter bounds notes and groups, folds case, maps status and test flags',()=>{
 const rows=Array.from({length:610},(_,i)=>({id:'n'+i,title:'T'.repeat(200),summary:'S'.repeat(250),group:['Grasp','grasp','Reach','Motion','Vision','Safety','Release'][i%7],status:i===0?'withdrawn':'active',effect:'helps',test_seeded:i===1}));
 const data=monitor.adapt(payload(rows));
 assert.equal(data.notes.length,600);assert.equal(data.groups.length,6);assert.equal(data.groups.at(-1).name,'Other');
 assert.equal(data.notes[0].title.length,80);assert.equal(data.notes[0].summary.length,180);
 assert.equal(data.notes[0].status,'withdrawn');assert.equal(data.notes[1].effect,'not_tested');
 assert.equal(data.notes[0].group,data.notes[1].group);
});
test('event diff flashes recent initial reads and only newly appended events',()=>{
 const old={seq:1,ts:100,type:'planner_memory_retrieved',note_ids:['a']};
 const recent={seq:2,ts:198,type:'planner_memory_retrieved',note_ids:['b']};
 const check={seq:3,ts:199,type:'planner_memory_query_finished',note_ids:[]};
 assert.deepEqual(monitor.newEvents(null,[old,recent,check],200).map(e=>e.seq),[2]);
 assert.deepEqual(monitor.newEvents([old],[old,recent,check],200).map(e=>e.seq),[2,3]);
 assert.deepEqual(monitor.newEvents([old,recent,check],[old,recent,check],200),[]);
});
test('version membership and used filter handle notes read by multiple versions',()=>{
 const data=monitor.adapt(payload([{id:'a',status:'active'},{id:'b',status:'active'},{id:'c',status:'off'}]));
 assert.deepEqual(data.byId.get('a').versions,[1,2]);
 assert.deepEqual(monitor.visibleNotes(data,1,true).map(n=>n.id),['a']);
 assert.deepEqual(monitor.visibleNotes(data,2,true).map(n=>n.id),['a','b']);
 assert.deepEqual(monitor.visibleNotes(data,0,false).map(n=>n.id),['a','b','c']);
});
test('card escapes service text and shows read count, summary and folded ID',()=>{
 const note={id:'private-1',title:'<script>',summary:'A useful note',group:'Grasp',status:'active',effect:'helps',lift_pt:2,reads:3,last_used_at:null,version:1,versions:[1],test_seeded:false};
 const html=monitor.cardHtml(note,true);
 assert.match(html,/&lt;script&gt;/);assert.match(html,/A useful note/);assert.match(html,/Read 3 times · last used never/);assert.match(html,/Helps \(\+2 pt\)/);
 assert.doesNotMatch(html.split('<details class="details">')[0],/private-1/);
});
test('main header badge counts distinct selected-run notes and pulses only on increase',()=>{
 const start=live.indexOf('  function updateMemoryLink(snapshot){'),end=live.indexOf('  if (!DEBUG_VIEW',start);
 const badge={hidden:true,textContent:''};const link={href:'',classList:{classes:new Set(),add(x){this.classes.add(x)},remove(x){this.classes.delete(x)}},querySelector:()=>badge,offsetWidth:10};
 const ctx=vm.createContext({memoryLink:link,memoryCounts:new Map(),memoryPulseTimer:null,clearTimeout:()=>{},setTimeout:()=>1,encodeURIComponent});
 vm.runInContext(live.slice(start,end),ctx);
 const id='devexp-'+'a'.repeat(24),snapshot=ids=>({dev_v2:{experiment_id:id},memory:{dev_v2:{retrievals:[{entries:ids.map(memory_id=>({memory_id}))}]}}});
 ctx.updateMemoryLink(snapshot([]));assert.equal(badge.hidden,true);
 ctx.updateMemoryLink(snapshot(['a','a']));assert.equal(badge.textContent,1);assert.equal(badge.hidden,false);assert.equal(link.classList.classes.has('pulse'),true);
 ctx.updateMemoryLink(snapshot(['a']));assert.equal(badge.textContent,1);
 assert.equal(link.href,'/ui/memory.html#exp='+id);
});
test('light default, theme tokens and graceful 3D fallback are wired',()=>{
 assert.match(page,/id="monitor" class="light"/);
 assert.match(css,/#monitor\.light\{/);assert.match(css,/--acc:#d94d27/);
 assert.match(fs.readFileSync(path.join(root,'ui/memory.js'),'utf8'),/localStorage\.getItem/);
 assert.match(fs.readFileSync(path.join(root,'ui/memory.js'),'utf8'),/querySelector\('\[data-view="3d"\]'\)\.hidden=true/);
});
test('nonempty warnings render as one muted friendly line',()=>{
 assert.match(page,/id="warning" role="status"/);
 assert.match(css,/#monitor \.warning\{color:var\(--mut\);font-size:11px/);
 assert.equal(monitor.warningText([]),'');
 assert.equal(monitor.warningText(['secret service detail']),'Some note details are unavailable right now.');
 assert.deepEqual(monitor.adapt({...payload([]),warnings:['unavailable']}).warnings,['unavailable']);
});
test('monitor fills viewport and moves sample preview to footer',()=>{
 assert.match(css,/\.wrap\{max-width:none;width:100%;min-height:calc\(100dvh - 32px\)/);
 assert.match(css,/#monitor\{flex:1;height:calc\(100dvh - 130px\);min-height:560px/);
 assert.match(css,/grid-template-columns:minmax\(0,1fr\) 380px/);
 assert.match(css,/#monitor \.li\{[^}]*overflow-y:auto/);
 assert.match(css,/@media\(max-width:780px\)[^\n]*grid-template-columns:1fr/);
 assert.match(page,/<footer class="demo-link">Preview with sample data: <a href="#demo=40">40<\/a> · <a href="#demo=150">150<\/a> · <a href="#demo=400">400<\/a> notes<\/footer>/);
 assert.match(page,/id="real-data" href="\/ui\/memory.html" hidden>Back to real data/);
 assert.match(page,/id="sample" class="sample" hidden>Sample data/);
 assert.match(fs.readFileSync(path.join(root,'ui/memory.js'),'utf8'),/preserveAspectRatio="xMidYMid meet"/);
 assert.equal(monitor.flowLayout(390),'vertical');assert.equal(monitor.flowLayout(900),'wide');
});
test('monitor and main page load the same notices bell',()=>{
 assert.match(page,/id="memory-notices" class="ab-notices-host"/);
 assert.match(page,/\.\/notices\.js/);
 assert.match(live,/AttentionNotices\.createNotices/);
 assert.match(css,/\.ab-notices-button\[data-level="error"\]/);
});
test('provisional notes show Unvalidated with their own colour, use counts and expiry',()=>{
 const data=monitor.adapt(payload([{id:'p',title:'Avoid PlanningRejected',status:'unvalidated',unvalidated:true,uses:{grants:4,outcomes:4,failure_gone:3},expires_in_days:12,scene_seed:102},
  {id:'r',title:'Other scene',status:'active',effect:'helps',reference:true,scene_seed:101},
  {id:'q',title:'Other scene draft',status:'unvalidated',unvalidated:true,reference:true,scene_seed:103,uses:{grants:1,failure_gone:0},expires_in_days:1}]));
 const [p,r,q]=data.notes;
 assert.equal(p.status,'active');assert.equal(p.effect,'unvalidated');
 const html=monitor.cardHtml(p,true);
 assert.match(html,/class="v">Unvalidated</);assert.match(html,/Not counted in results/);
 assert.match(html,/Used 4 · failure gone 3 · Expires in 12 days/);assert.doesNotMatch(html,/Test note/);
 assert.match(monitor.cardHtml(r,true),/Reference · validated on scene 101/);
 assert.match(monitor.cardHtml(r,true),/Not counted in results/);
 const qh=monitor.cardHtml(q,true);assert.match(qh,/Unvalidated · from scene 103/);assert.match(qh,/Expires in 1 day</);
 assert.match(css,/#monitor\{--cv:#c79bff/);
 const t={...data.notes[1],test_seeded:true};assert.match(monitor.cardHtml(t,true),/Reference · test note from scene 101/);assert.match(css,/#monitor\.light\{--cv:#8a4fd0/);assert.match(css,/#monitor \.chips span\.v\{/);
 assert.match(page,/id="count-unvalidated"/);assert.match(page,/id="bar-unvalidated"/);
});
test('main page tags a run that read unvalidated memory, with the exclusion tooltip',()=>{
 const start=live.indexOf('  function unvalidatedMemoryTag(snapshot) {'),end=live.indexOf('  function operatorOverview(snapshot) {',start);
 const ctx=vm.createContext({});vm.runInContext(live.slice(start,end),ctx);
 assert.equal(ctx.unvalidatedMemoryTag({memory:{dev_v2:{unvalidated_used:false}}}),'');
 assert.equal(ctx.unvalidatedMemoryTag({}),'');
 const html=ctx.unvalidatedMemoryTag({memory:{dev_v2:{unvalidated_used:true}}});
 assert.match(html,/>Used unvalidated memory</);
 assert.match(html,/title="This run read a note that has not been validated\. Excluded from results\."/);
 assert.match(live,/\$\{operatorOutcome\(snapshot\)\}\$\{unvalidatedMemoryTag\(snapshot\)\}/);
 const index=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
 assert.match(index,/\.op-memory-flag\{/);assert.match(index,/\[data-theme="dark"\] \.op-memory-flag\{/);
});

test('local run notes have version membership, counts, distinct themes and a local kill switch',()=>{
 const data=monitor.adapt({run:{versions:[{n:2,note_ids:[]},{n:3,note_ids:['r-note']},{n:4,note_ids:['r-note']}]},notes:[],run_notes:[{id:'r-note',cell:'cell-a',title:'Lift <cup>',help_version:2,from_version:3,uses:2,failure_gone:1,off:false}]});
 const note=data.byId.get('r-note');
 assert.equal(note.run_note,true);assert.equal(note.effect,'run_note');
 assert.deepEqual(monitor.visibleNotes(data,2,false),[]);
 assert.deepEqual(monitor.visibleNotes(data,3,true),[note]);
 const html=monitor.cardHtml(note,true);
 assert.match(html,/Lift &lt;cup&gt;/);assert.match(html,/This run · used 2 · failure gone 1/);
 assert.match(html,/Unvalidated · not counted in results/);assert.match(html,/Turn off for this run/);
 assert.doesNotMatch(html,/data-manage|Withdraw|Set expiry/);
 assert.doesNotMatch(monitor.cardHtml(note,false),/data-run-note-off/);
 assert.doesNotMatch(monitor.cardHtml({...note,status:'off'},true),/data-run-note-off/);
 for(const theme of Object.values(monitor.themes))assert.ok(theme.efc.r&&!Object.entries(theme.efc).some(([k,v])=>k!=='r'&&v===theme.efc.r));
 const req=monitor.runNoteOffRequest('devexp-a',note);
 assert.equal(req.url,'/api/dev-v2/experiments/devexp-a/run-note-off');
 assert.equal(req.options.method,'POST');assert.ok('X-Attention-Token' in req.options.headers);
 assert.deepEqual(JSON.parse(req.options.body),{cell:'cell-a',note_id:'r-note'});
 assert.equal(monitor.feedLabel({type:'planner_memory_run_note_created',text:'v2 learned: Lift cup'}),'v2 learned: Lift cup');
 assert.equal(monitor.feedLabel({type:'planner_memory_run_note_off',text:'turned off for this run: Lift cup'}),'turned off for this run: Lift cup');
});

test('nested layers filter jointly with version and used-only, with public path cards',()=>{
 const data=monitor.adapt({run:{family_reference:false,layer_counts:{run:1,scene:1,task:1,family:1},versions:[{n:1,note_ids:['s','t']},{n:2,note_ids:['r','f']}]},
  notes:[{id:'s',layer:'scene',status:'active',path:'Test note → scene 102'},
         {id:'t',layer:'task',status:'active',path:'Validated on scene 101 → read here as task reference'},
         {id:'f',layer:'family',status:'active',path:'Validated on scene 103 → read here as family reference'},
         {id:'other',layer:null,status:'active'}],
  run_notes:[{id:'r',layer:'run',title:'Lift cup',help_version:2,path:'Learned in devexp-abc… v2 → this run only'}]});
 assert.deepEqual(monitor.visibleNotes(data,1,true,'task').map(n=>n.id),['t']);
 assert.deepEqual(monitor.visibleNotes(data,2,true,'task'),[]);
 assert.equal(monitor.versionCount(data,data.versions[0],0,false,'scene'),1);
 assert.equal(monitor.visibleNotes(data,0,false,'all').length,5);
 for(const layer of ['run','scene','task','family']){
  const note=data.notes.find(n=>n.layer===layer),html=monitor.cardHtml(note);
  assert.match(html,new RegExp(layer[0].toUpperCase()+layer.slice(1)+' layer'));
  assert.ok(html.includes(note.path));
 }
 assert.match(monitor.layerLegend(data),/Family<b>off<\/b>/);
 assert.match(monitor.layerLegend(data),/class="layer-badge">R<\/i>Run/);
 assert.match(page,/id="layer-legend"/);assert.match(page,/id="layer-filters"/);
 assert.match(css,/\.layer-badge\{[^}]*border:1px solid currentColor/);
 assert.match(css,/#monitor\s+\.lg\s+i\.layer-badge\s*\{[^}]*\bborder-radius:\s*4px\s*[;}]/);
});

test('r3 run banner and learned applicability are visible', () => {
  const ui=require('../ui/memory.js');
  assert.match(ui.memoryScopeText({execution_profile:'r3-20261025'}),/Execution settings r3 · not comparable with earlier runs/);
  const data=ui.adapt({notes:[{id:'r3',title:'Note',execution_profile:'r3-20261025',status:'active'}]});
  assert.match(ui.cardHtml(data.notes[0],false,true),/Learned under execution settings r3/);
});
