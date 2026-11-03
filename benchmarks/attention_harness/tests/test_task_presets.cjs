const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const code=source.slice(source.indexOf('  function fixtureTaskKey('),source.indexOf('  function runConfigSeedOptions('));
const escape=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const {fixtureTaskKey,fixtureTaskOptions,presetSeed,newRunProfiles}=vm.runInNewContext(code+'\n({fixtureTaskKey,fixtureTaskOptions,presetSeed,newRunProfiles})',{esc:escape,taskName:v=>v});
const PROFILE='r5-fixture-20261031';
const rows=seeds=>seeds.map(([seed,enabled=true])=>({seed,enabled}));
const fixture=(task,family,label,seeds=[[101],[102],[103]])=>({task_id:task,channel:'dev_v2',rules_profile:PROFILE,family,family_label:label,held_out:false,seeds:seeds.filter(s=>s[1]!==false).map(s=>s[0]),seed_options:rows(seeds)});
const microwave=fixture('turn_on_microwave','B','Buttons and knobs'),stove=fixture('turn_off_stove','B','Buttons and knobs');
const cabinetPick=fixture('pick_place_counter_to_cabinet','D','Cabinet pick and place',[[101],[102],[107]]);
const mixer=fixture('open_stand_mixer_head','B','Buttons and knobs'),cabinet=fixture('open_cabinet','A','Open and close fixtures',[[101,false],[102],[103,false]]);
const pickPlace={task_id:'pick_place_counter_to_sink',channel:'dev_v2',seed:101};
const legacyCabinet={task_id:'open_cabinet',channel:'dev_v2',seed:101};
const certified={task_id:'open_cabinet',channel:'certified_v2'};
const all=[pickPlace,legacyCabinet,cabinet,microwave,stove,mixer,cabinetPick];
const values=html=>[...html.matchAll(/<option value="([^"]+)"/g)].map(m=>m[1]);

test('New run offers exactly Counter to Sink, TurnOnMicrowave and Counter to Cabinet',()=>{
  const html=fixtureTaskOptions(newRunProfiles(all,null));
  assert.deepEqual(values(html),['pick_place_counter_to_sink','turn_on_microwave@'+PROFILE,'pick_place_counter_to_cabinet@'+PROFILE]);
  assert.match(html,/^<option value="pick_place_counter_to_sink">pick_place_counter_to_sink<\/option><optgroup label="Presets · seed 101 · fixture rules \(R5\)"><option value="turn_on_microwave@r5-fixture-20261031">turn_on_microwave<\/option><option value="pick_place_counter_to_cabinet@r5-fixture-20261031">pick_place_counter_to_cabinet<\/option><\/optgroup>$/);
  for(const gone of ['turn_off_stove','open_cabinet','open_drawer','open_stand_mixer_head'])assert.equal(html.includes(gone),false,gone);
});
test('only Dev v2 profiles are narrowed; fixed run types keep their tasks',()=>{
  assert.deepEqual(newRunProfiles([certified,legacyCabinet,pickPlace],null),[certified,pickPlace]);
});
test('a copied run keeps its own older task selectable, with or without the fixture rules',()=>{
  assert.deepEqual(values(fixtureTaskOptions(newRunProfiles(all,legacyCabinet))),['pick_place_counter_to_sink','open_cabinet','turn_on_microwave@'+PROFILE,'pick_place_counter_to_cabinet@'+PROFILE]);
  assert.deepEqual(values(fixtureTaskOptions(newRunProfiles(all,stove))),['pick_place_counter_to_sink','turn_on_microwave@'+PROFILE,'pick_place_counter_to_cabinet@'+PROFILE,'turn_off_stove@'+PROFILE]);
  // The older task is only added to the list, never locked to a preset seed.
  assert.equal(presetSeed(stove),null);assert.equal(presetSeed(legacyCabinet),null);
});
test('the fixture seed lists are untouched; presets pin 101 only while it is enabled',()=>{
  assert.equal(presetSeed(microwave),101);assert.equal(presetSeed(cabinetPick),101);
  assert.deepEqual(cabinetPick.seeds,[101,102,107]);
  assert.equal(presetSeed(mixer),null);assert.equal(presetSeed(cabinet),null);assert.equal(presetSeed(stove),null);
  assert.equal(presetSeed(pickPlace),null);assert.equal(presetSeed({...pickPlace,rules_profile:PROFILE}),null);
  assert.equal(fixtureTaskKey(pickPlace),'pick_place_counter_to_sink');
  const excluded=fixture('turn_on_microwave','B','Buttons and knobs',[[101,false],[102],[103]]);
  assert.equal(presetSeed(excluded),null);
  assert.deepEqual(values(fixtureTaskOptions(newRunProfiles([pickPlace,excluded],null))),['pick_place_counter_to_sink','turn_on_microwave@'+PROFILE]);
  assert.match(fixtureTaskOptions([excluded]),/B · Buttons and knobs/);
  assert.equal(fixtureTaskOptions([excluded]).includes('Presets ·'),false);
  assert.equal(presetSeed({task_id:'turn_on_microwave',seed_options:rows([[101]])}),null,'a legacy profile without rules is never a preset');
});
test('the form locks the preset seed, shows Seed N, and every other path unlocks it',()=>{
  assert.match(source,/const fixedSeed=presetSeed\(profile\);\s*form\.seed\.disabled=fixedSeed!==null;/);
  assert.match(source,/<option value="\$\{fixedSeed\}">Seed \$\{fixedSeed\}<\/option>/);
  assert.match(source,/form\.seed\.value=String\(fixedSeed\);/);
  // The submit body still reads the select's value, which a disabled select keeps.
  assert.match(source,/seeds:\[Number\(form\.seed\.value\)\]/);
  // The narrowed list is what the Task select is filled from, with the copied run as the exception.
  assert.match(source,/form\.task_id\.innerHTML=fixtureTaskOptions\(newRunProfiles\(devProfiles\.filter\([^\n]*\),sourceProfile\)\);/);
});
