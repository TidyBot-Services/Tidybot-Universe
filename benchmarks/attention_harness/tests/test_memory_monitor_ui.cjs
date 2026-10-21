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
