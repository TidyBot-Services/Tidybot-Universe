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
test('switched off with nothing to show is one quiet line; anything to show opens the three columns',()=>{
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
