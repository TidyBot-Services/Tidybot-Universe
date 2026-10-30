const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const {familyHtml,automaticHtml}=require('../ui/memory.js');
const family={fixtures:[{title:'Fixture <x>',label:'test · not counted in results',guidance:'heading',statement:'Operator preset; same-scene and cross-seed validation were skipped',statement_zh:'操作员预置，跳过了同场景和跨 seed 验证',user_quotes:['approved <quote>'],revoked_at:1}],candidates:[{title:'Rewrite',guidance:'Lift',original_guidance:'Origin',from_source:{title:'Origin',kind:'fixture',id:'fixture-one',version:1},status:'revoked',scenes:[{target_seed:114,role:'neutral',pairs_done:3,pairs_needed:3,verdict:'neutral_no_harm',reason:'No harm'}],conditions:{evidence_strength:'no harm only',met:['Two scenes'],missing:['Source inactive']}}]};
for(const enabled of [true,false]) {
 const html=automaticHtml({enabled,family});
 for(const text of ['Family','Model rewrite','test · not counted in results','Operator preset; same-scene and cross-seed validation were skipped','操作员预置，跳过了同场景和跨 seed 验证','original, unchanged','Revoked','no harm only','3/3 pairs','Missing: Source inactive','approved &lt;quote&gt;'])assert.ok(html.includes(text),text);
 assert.ok(!html.includes('<button'));
}
console.log('PASS: family cards, exact labels/statements, escaping, neutral-only, revocation; no switch controls');
for(const enabled of [true,false]) {
 const html=automaticHtml({enabled,family:{fixtures:[],candidates:[],events:[]}});
 assert.ok(html.includes('<h3>Family</h3>'));
 assert.ok(html.includes('No family checks yet'));
 assert.ok(!automaticHtml({enabled,family}).includes('No family checks yet'));
}
const notices=familyHtml({fixtures:[],candidates:[],events:[{kind:'fixture_revoked',reason:'End <test>'}]});
assert.ok(notices.includes('fixture_revoked · End &lt;test&gt;'));
assert.ok(!notices.includes('No family checks yet'));
console.log('PASS: explicit empty Family state, populated cards and notices preserved');

const unrun={target_seed:115,role:'pending',pairs_done:0,pairs_needed:null,verdict:null,reason:'Waiting for clean baseline probe'};
const decided={target_seed:114,role:'neutral',pairs_done:3,pairs_needed:3,verdict:'neutral_harm',reason:'Harm detected'};
const decidedRow='<li>Seed 114 · neutral · 3/3 pairs · neutral_harm<small>Harm detected</small></li>';
test('finished candidates explain unrun seeds and preserve decided seeds',()=>{
 for(const [state,reason] of [
  [{status:'revoked'},'revoked'],
  [{status:'disabled'},'disabled'],
  [{status:'finished'},'finished'],
  [{status:'completed'},'finished'],
  [{status:'decided'},'finished'],
  [{status:'family_trusted'},'finished'],
  [{status:'candidate',decision:{decision:'would not'}},'finished'],
  [{status:'candidate',decision:{decision:'would be trusted for the family'}},'finished'],
  [{status:'revoked',decision:{decision:'Revoked'}},'revoked'],
 ]){
  const html=familyHtml({candidates:[{...family.candidates[0],...state,scenes:[decided,unrun]}]});
  assert.ok(html.includes(`<li>Seed 115 · Could not finish<small>Did not run: candidate ${reason}.</small></li>`));
  assert.ok(!html.includes('Waiting'));
  assert.ok(!html.includes('pending · 0/— pairs'));
  assert.ok(html.includes(decidedRow));
 }
});
test('active candidates keep Waiting and existing seed rows unchanged',()=>{
 for(const decision of [undefined,null,{}]){
  const html=familyHtml({candidates:[{...family.candidates[0],status:'candidate',decision,scenes:[decided,unrun]}]});
  assert.ok(html.includes('<li>Seed 115 · pending · 0/— pairs · Waiting<small>Waiting for clean baseline probe</small></li>'));
  assert.ok(html.includes(decidedRow));
  assert.ok(!html.includes('Did not run'));
 }
});
test('a partially run seed is not described as unrun',()=>{
 const html=familyHtml({candidates:[{status:'revoked',scenes:[{...unrun,pairs_done:1,pairs_needed:3}]}]});
 assert.ok(!html.includes('Did not run'));
 assert.ok(html.includes('1/3 pairs'));
});
test('Family tags size to their text in both themes',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../ui/memory.css'),'utf8');
 assert.ok(familyHtml(family).includes('<span class="ac-tag">Model rewrite</span>'));
 // The shared rule overrides the grid item's default stretch in either theme.
 assert.match(css,/#automatic-checks \.ac-family \.ac-tag\s*\{\s*justify-self:\s*start\s*;?\s*\}/);
});
test('same-title fixtures show distinct IDs, creation times and revocation times',()=>{
 const originalNow=Date.now;
 Date.now=()=>1800000000000;
 try{
  const fixtures=[
   {...family.fixtures[0],fixture_id:'run8-heading-lift-m11g-fixture',created_at:1799999880,revoked_at:1799999940},
   {...family.fixtures[0],fixture_id:'run9-heading-lift-m11g-fixture',created_at:1799999700,revoked_at:1799999820},
  ];
  const html=familyHtml({fixtures});
  for(const [index,times] of ['Created 2 min ago · Revoked 1 min ago','Created 5 min ago · Revoked 3 min ago'].entries()){
   assert.ok(html.includes(`<b>Fixture &lt;x&gt;</b><small>Fixture: ${fixtures[index].fixture_id}</small><small>${times}</small>`));
  }
  const active=familyHtml({fixtures:[{...fixtures[0],revoked_at:null}]});
  assert.ok(active.includes('Created 2 min ago'));
  assert.ok(!active.includes('Revoked'));
  const legacy=familyHtml({fixtures:[{fixture_id:'legacy-<fixture>',title:'Legacy',revoked_at:null}]});
  assert.ok(legacy.includes('Fixture: legacy-&lt;fixture&gt;'));
  assert.ok(legacy.includes('Created time unavailable'));
  assert.ok(!legacy.includes('never'));
 }finally{Date.now=originalNow;}
});
