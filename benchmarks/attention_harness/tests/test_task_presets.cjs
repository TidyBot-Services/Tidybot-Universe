const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const code=source.slice(source.indexOf('  function fixtureTaskKey('),source.indexOf('  function runConfigSeedOptions('));
const escape=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const {fixtureTaskKey,fixtureTaskOptions,presetSeed}=vm.runInNewContext(code+'\n({fixtureTaskKey,fixtureTaskOptions,presetSeed})',{esc:escape,taskName:v=>v});
const PROFILE='r5-fixture-20261031';
const rows=seeds=>seeds.map(([seed,enabled=true])=>({seed,enabled}));
const fixture=(task,family,label,seeds=[[101],[102],[103]])=>({task_id:task,rules_profile:PROFILE,family,family_label:label,held_out:false,seeds:seeds.filter(s=>s[1]!==false).map(s=>s[0]),seed_options:rows(seeds)});
const microwave=fixture('turn_on_microwave','B','Buttons and knobs'),stove=fixture('turn_off_stove','B','Buttons and knobs');
const mixer=fixture('open_stand_mixer_head','B','Buttons and knobs'),cabinet=fixture('open_cabinet','A','Open and close fixtures',[[101,false],[102],[103,false]]);
const pickPlace={task_id:'pick_place_counter_to_sink',seed:101};

test('the two presets are listed first in their own group, once, with the seed in the label',()=>{
  const html=fixtureTaskOptions([pickPlace,cabinet,microwave,stove,mixer]);
  assert.match(html,/<optgroup label="Presets · seed 101 · fixture rules \(R5\)"><option value="turn_on_microwave@r5-fixture-20261031">turn_on_microwave<\/option><option value="turn_off_stove@r5-fixture-20261031">turn_off_stove<\/option><\/optgroup>/);
  assert.equal((html.match(/value="turn_on_microwave@/g)||[]).length,1);
  assert.equal((html.match(/value="turn_off_stove@/g)||[]).length,1);
  assert.ok(html.indexOf('Presets ·')<html.indexOf('A · Open and close fixtures'));
  assert.ok(html.indexOf('pick_place_counter_to_sink')<html.indexOf('Presets ·'));
});
test('other fixture tasks keep their family groups and seed lists',()=>{
  const html=fixtureTaskOptions([cabinet,microwave,stove,mixer]);
  assert.match(html,/<optgroup label="B · Buttons and knobs · fixture rules \(R5\)"><option value="open_stand_mixer_head@r5-fixture-20261031">open_stand_mixer_head<\/option><\/optgroup>/);
  assert.equal(presetSeed(mixer),null);assert.equal(presetSeed(cabinet),null);
  assert.equal(presetSeed(pickPlace),null);assert.equal(presetSeed({...pickPlace,rules_profile:PROFILE}),null);
  assert.equal(fixtureTaskKey(pickPlace),'pick_place_counter_to_sink');
});
test('presets lock seed 101 only while 101 is enabled for the task',()=>{
  assert.equal(presetSeed(microwave),101);assert.equal(presetSeed(stove),101);
  const excluded=fixture('turn_off_stove','B','Buttons and knobs',[[101,false],[102],[103]]);
  assert.equal(presetSeed(excluded),null);
  const html=fixtureTaskOptions([microwave,excluded]);
  assert.match(html,/Presets · seed 101[^>]*><option value="turn_on_microwave@[^"]+">turn_on_microwave<\/option><\/optgroup>/);
  assert.match(html,/B · Buttons and knobs[^>]*><option value="turn_off_stove@/);
  assert.equal(fixtureTaskOptions([excluded]).includes('Presets ·'),false);
  assert.equal(presetSeed({task_id:'turn_on_microwave',seed_options:rows([[101]])}),null,'a legacy profile without rules is never a preset');
});
test('the form locks the preset seed, shows Seed N, and every other path unlocks it',()=>{
  assert.match(source,/const fixedSeed=presetSeed\(profile\);\s*form\.seed\.disabled=fixedSeed!==null;/);
  assert.match(source,/<option value="\$\{fixedSeed\}">Seed \$\{fixedSeed\}<\/option>/);
  assert.match(source,/form\.seed\.value=String\(fixedSeed\);/);
  // The submit body still reads the select's value, which a disabled select keeps.
  assert.match(source,/seeds:\[Number\(form\.seed\.value\)\]/);
});
