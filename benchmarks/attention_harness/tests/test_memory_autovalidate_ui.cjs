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
test('section inherits both page themes without setting a light background',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../ui/memory.html'),'utf8');
 const css=fs.readFileSync(path.join(__dirname,'../ui/memory.css'),'utf8');
 assert.ok(html.includes('id="automatic-checks"'));assert.ok(css.includes('color:inherit;background:transparent'));
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
