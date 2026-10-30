const assert=require('node:assert/strict');
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
