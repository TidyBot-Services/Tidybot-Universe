const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {automaticHtml}=require('../ui/memory.js');
test('automatic checks show switch, queue, pair, counts, demotion and escaped errors',()=>{
 const html=automaticHtml({enabled:false,status:'Off',runs_today:7,weekly_quota:43.5,current:{title:'Cup',pair:2,arm:'treatment',paused:true},queue:[{title:'Cup',uses:3,failure_gone:1}],results:[{title:'Cup',status:'trusted',control:5,treatment:0}],events:[{title:'Cup',event:'demoted'}],errors:['<script>bad</script>']});
 for(const text of ['Off','Runs today: 7 of 20','Codex weekly use: 43.5%','Trusted on this scene','pair 2 of 5','treatment','paused','used 3 times','failure gone 1 times','without note 5 of 5','with note 0 of 5','Unvalidated','role="alert"','&lt;script&gt;'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('<script>'));
});
test('section follows both page themes through the shared shell tokens',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../ui/memory.html'),'utf8');
 const css=fs.readFileSync(path.join(__dirname,'../ui/memory.css'),'utf8');
 assert.ok(html.includes('id="automatic-checks"'));
 const rule=css.match(/#automatic-checks\{[^}]*\}/)[0];
 assert.match(rule,/background:var\(--sh-panel\)/);assert.match(rule,/color:var\(--sh-ink\)/);assert.doesNotMatch(rule,/#fff|white/);
});
test('switched off with no work hides the three columns; queued work opens them',()=>{
 const off=automaticHtml({enabled:false,status:'Off',runs_today:0});
 assert.ok(off.includes('Off'));assert.ok(!off.includes('Off · Off'));assert.ok(!off.includes('ac-grid'));
 assert.ok(automaticHtml({enabled:false,status:'Off',errors:['x']}).includes('role="alert"'));
 for(const value of [{enabled:true,status:'Waiting'},{enabled:false,queue:[{title:'Cup'}]},{enabled:false,results:[{title:'Cup',status:'trusted',control:5,treatment:0}]}])
  assert.ok(automaticHtml(value).includes('ac-grid'));
});
test('automatic check errors have readable contrast on both shared shell backgrounds',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../ui/memory.css'),'utf8');
 const shell=fs.readFileSync(path.join(__dirname,'../ui/shell.css'),'utf8');
 const alert=css.match(/#automatic-checks \[role=alert\]\{color:([^;}]+)/)[1];
 const blocks=[':root','html[data-theme="dark"]'].map(selector=>shell.slice(shell.indexOf(selector)).match(/\{([^}]+)\}/)[1]);
 const luminance=hex=>hex.slice(1).match(/../g).map(v=>parseInt(v,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
 for(const block of blocks){
  const vars=Object.fromEntries([...block.matchAll(/(--[\w-]+):(#[\da-f]+)/g)].map(m=>[m[1],m[2]]));
  const foreground=alert.startsWith('var(')?vars[alert.slice(4,-1)]:alert;
  // The alert is a strip on --sh-badbg (2026-10-24); check it and the page behind it.
  for(const ground of ['--sh-bg','--sh-badbg']){
   const a=luminance(foreground),b=luminance(vars[ground]);
   assert.ok((Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5,`${foreground} on ${vars[ground]}`);
  }
 }
});
test('inconclusive and early safety rejection use plain reasons without invented five-pair counts',()=>{
 const html=automaticHtml({enabled:true,status:'Waiting',results:[{title:'Cup',status:'inconclusive',reason:'Could not finish the check: the scene did not reproduce',pairs:0},{title:'Glass',status:'rejected',reason:'Rejected: a run with the note failed the independent safety check',pairs:0}]});
 for(const text of ['Could not finish the check: the scene did not reproduce','note stays Unvalidated','failed the independent safety check'])assert.ok(html.includes(text));
 assert.ok(!html.includes('of 5 times'));assert.ok(!html.includes('inconclusive'));
 const escaped=automaticHtml({results:[{title:'x',status:'inconclusive',reason:'<script>unsafe</script>'}]});
 assert.ok(escaped.includes('&lt;script&gt;'));assert.ok(!escaped.includes('<script>'));
});

test('budget defaults and capitalised result labels remain readable without quota',()=>{
 const empty=automaticHtml({enabled:false,status:'Off'});
 assert.ok(empty.includes('Runs today: 0 of 20'));assert.ok(!empty.includes('Codex weekly use:'));
 for(const [status,label] of [['trusted','Trusted on this scene'],['rejected','Rejected'],['inconclusive','Could not finish']]){
  const html=automaticHtml({enabled:true,status,results:[{title:'Cup',status,control:5,treatment:0}]});
  assert.ok(html.includes(label));assert.ok(!html.includes(` · ${status} · `));
 }
 for(const weekly_quota of [null,'45',NaN,Infinity,-1,101])assert.ok(!automaticHtml({weekly_quota}).includes('Codex weekly use:'));
 assert.ok(automaticHtml({weekly_quota:0,runs_today:20}).includes('Runs today: 20 of 20 · Codex weekly use: 0%'));
});

test('cross-seed scenes, requirements, promotion and one-level demotion',()=>{
 const value={cross_seed_enabled:true,cross_seed_notes:[{title:'Exact cup',task_trust:{trust_scope:'task_scoped',missing:[]},seeds:[{target_seed:114,role:'transfer',pairs_done:5,pairs_needed:5,verdict:'transfer_passed'},{target_seed:104,role:'neutral',pairs_done:3,pairs_needed:3,verdict:'neutral_no_harm'},{target_seed:107,role:'could_not_finish',pairs_done:0,pairs_needed:0,verdict:'could_not_finish'}]},{title:'Waiting',task_trust:{missing:['Worked on at least one other transfer seed']},seeds:[]}],skipped_seeds:[{seed:120,reason:'Seed 120: no scene contract registered <script>'}],events:[{title:'Exact cup',event:'promoted',trust_scope:'task_scoped'},{title:'Old note',event:'demoted',trust_scope:'scene_scoped'}]};
 const html=automaticHtml(value);
 for(const text of ['Trusted for this task','114','104','107','Transfer','Neutral','5 of 5','3 of 3','No harm','Could not finish','Still needed for task trust','Seed 120: no scene contract registered','Back to trusted on origin scene'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('<script>'));assert.ok(!html.includes('Back to Unvalidated'));
 const probe=automaticHtml({enabled:true,current:{title:'Cup',stage:'probe',seed:114,arm:'probe'}});assert.ok(probe.includes('probe without notes'));assert.ok(!probe.includes('pair undefined'));
 const pair=automaticHtml({enabled:true,current:{title:'Cup',seed:104,pair:2,pairs_needed:3,arm:'control'}});assert.ok(pair.includes('pair 2 of 3'));
});

test('cross-seed empty state is visible with checks on or off',()=>{
 for(const enabled of [true,false])for(const cross_seed_enabled of [true,false]){
  const html=automaticHtml({enabled,cross_seed_enabled,cross_seed_notes:[],skipped_seeds:[]});
  assert.ok(html.includes(`Other seeds · ${cross_seed_enabled?'On':'Off'}`));
  assert.ok(html.includes('No cross-seed checks'));
 }
 const populated=automaticHtml({cross_seed_notes:[{title:'Cup',seeds:[]}]});
 assert.ok(!populated.includes('No cross-seed checks'));
 const skipped=automaticHtml({skipped_seeds:[{reason:'No scene contract <seed>'}]});
 assert.ok(skipped.includes('No cross-seed checks'));
 assert.ok(skipped.includes('No scene contract &lt;seed&gt;'));
});

test('missing Family and cross-seed methods render visible alerts in both layouts',()=>{
 const errors=['Family feed unavailable: memory gateway has no family_list',
  'Cross-seed feed unavailable: memory gateway has no cross_seed_queue',
  'Cross-seed feed unavailable: memory gateway has no cross_seed_status'];
 for(const enabled of [true,false]){
  const html=automaticHtml({enabled,errors});
  for(const error of errors)assert.ok(html.includes(`<p class="ac-err" role="alert">${error}</p>`));
 }
});

test('origin preparation shows a phase without fictional pairs or Memory exposure',()=>{
 const html=automaticHtml({enabled:true,runs_today:1,current:{title:'Cup <source>',stage:'origin',paused:true}});
 assert.ok(html.includes('Preparing origin evidence · paused'));
 assert.ok(html.includes('Cup &lt;source&gt;'));
 assert.ok(!html.includes('pair undefined')&&!html.includes('undefined run'));
 assert.ok(!html.includes('ac-dots'));
});

test('failed cross-seed origin preparation preserves the displayed existing trust',()=>{
 const html=automaticHtml({results:[{title:'Cup',status:'inconclusive',note_remains:'trusted',reason:'Origin scene did not reproduce'}]});
 assert.ok(html.includes('note keeps its existing trust'));
 assert.ok(!html.includes('note stays Unvalidated'));
});

test('weekly quota shows fresh, stale and unknown receipt ages beside the percentage',()=>{
 for(const [age,label] of [[0,'reading less than a minute ago'],[125,'reading 2 min ago'],[600,'reading 10 min ago'],[601,'reading 10 min ago · stale'],[3600,'reading 60 min ago · stale'],[null,'reading age unknown'],[NaN,'reading age unknown']]){
  const html=automaticHtml({weekly_quota:8,weekly_quota_age_seconds:age});
  assert.ok(html.includes(`Codex weekly use: 8% (${label})`),html);
 }
 assert.ok(!automaticHtml({weekly_quota_age_seconds:30}).includes('reading less'));
});

test('quota refresh history shows time, before and after, and plain failure',()=>{
 const html=automaticHtml({quota_refreshes:[{at:1792713600,before_percent:7,after_percent:8,status:'refreshed'}]});
 assert.ok(html.includes('Last Codex refresh at '));assert.ok(html.includes('7% → 8% · reading refreshed'));
 const failed=automaticHtml({quota_refreshes:[{before_percent:8,after_percent:null,status:'failed'}]});
 assert.ok(failed.includes('8% → unavailable · could not read weekly use'));
 const unsafe=automaticHtml({quota_refreshes:[{at:'<script>',before_percent:'<script>',after_percent:NaN,status:'<script>'}]});
 assert.ok(!unsafe.includes('<script>'));assert.ok(unsafe.includes('status unavailable'));
});
