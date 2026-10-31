const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../ui/live.js'),'utf8');
const code=source.slice(source.indexOf('  function fixtureTaskKey('),source.indexOf('  function runConfigSeedOptions('));
const escape=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
const {fixtureTaskKey,fixtureTaskOptions,fixtureSeedOptions,launchCreationError,fixtureRunMode,fixtureSceneNotice}=vm.runInNewContext(code+'\n({fixtureTaskKey,fixtureTaskOptions,fixtureSeedOptions,launchCreationError,fixtureRunMode,fixtureSceneNotice})',{esc:escape,taskName:v=>v});
const profile={task_id:'open_cabinet',rules_profile:'r5-fixture-20261031',family:'A',family_label:'Open and close fixtures',held_out:false};
test('explicit fixtures are grouped A–D and held-out marked',()=>{
  const profiles='ABCD'.split('').map((family,i)=>({...profile,task_id:'task'+i,family,held_out:i===3}));
  const html=fixtureTaskOptions([{task_id:'open_cabinet'},...profiles]);
  assert.equal((html.match(/<optgroup /g)||[]).length,4);
  assert.equal((html.match(/held-out/g)||[]).length,1);
  assert.match(html,/fixture rules \(R5\)/);
  assert.match(html,/<option value="open_cabinet">open_cabinet<\/option>/);
});
test('shared task ids select independent R5 profile choices',()=>{
  assert.equal(fixtureTaskKey({task_id:'open_cabinet'}),'open_cabinet');
  assert.equal(fixtureTaskKey(profile),'open_cabinet@r5-fixture-20261031');
  assert.match(source,/selection.rules_profile=profile.rules_profile/);
});
test('excluded seeds are disabled with the visible refusal reason',()=>{
  const html=fixtureSeedOptions({...profile,seed_options:[{seed:101,enabled:false,reason:'start_state_collision'},{seed:102,enabled:true}]});
  assert.match(html,/<option value="101" disabled>101 · start_state_collision<\/option>/);
  assert.match(html,/<option value="102" >102<\/option>/);
  assert.match(source,/profile.seeds.includes\(previous\)\?previous:profile.seeds\[0\]/);
});
test('creation errors are rendered as text in the visible form message',()=>{
  assert.equal(launchCreationError(new Error('start_state_collision')),'Could not create run: start_state_collision');
  assert.equal(launchCreationError(new Error('server failed')),'Could not create run: server failed');
  assert.match(source,/querySelector\('#ab-launch-message'\).textContent = launchCreationError\(error\)/);
  assert.match(source,/submitting = false;\s*form.querySelector\('#ab-launch-message'\)/);
});

test('R5 forces interactive and one policy through profile changes and repeat',()=>{
  const mode={value:'experiment',disabled:false};
  const policy={value:'all',options:[{value:'all'},{value:'autonomous'},{value:'reactive_help'}]};
  fixtureRunMode(profile,mode,policy);
  assert.equal(mode.value,'interactive');assert.equal(mode.disabled,true);
  assert.equal(policy.value,'autonomous');assert.equal(policy.options[0].disabled,true);
  mode.value='experiment';fixtureRunMode(profile,mode,policy);
  assert.equal(mode.value,'interactive');
  fixtureRunMode({task_id:'open_cabinet'},mode,policy);
  assert.equal(mode.disabled,false);assert.equal(policy.options[0].disabled,false);
  mode.value='experiment';fixtureRunMode({task_id:'open_cabinet'},mode,policy);
  assert.equal(mode.value,'experiment');
  assert.match(source,/fixtureRunMode\(selectedProfile\(\),form.v2_mode,form.v2_policy,!!forkStart\)/);
});
test('R5 interactive exclusion label and exact backend refusal are visible',()=>{
  assert.match(fixtureTaskOptions([profile]),/fixture rules \(R5\) · interactive · not compared \(scene positions not verified\)/);
  const reason='R5 fixture runs are interactive only until a measured fixture scene contract exists (scene positions are not verified)';
  assert.equal(launchCreationError(new Error(reason)),'Could not create run: '+reason);
  assert.match(source,/if \(!response.ok\) throw new Error\(result.error/);
});

test('run record displays fixture-only verification and comparison exclusion',()=>{
  assert.equal(fixtureSceneNotice({}), '');
  assert.match(fixtureSceneNotice({scene_verification:'fixture_contract_only'}),/data-scene-verification="fixture_contract_only"/);
  assert.match(fixtureSceneNotice({scene_verification:'fixture_contract_only'}),/scene positions not verified · interactive · not compared/);
  assert.match(source,/fixtureSceneNotice\(snapshot.dev_session\?\.locked\?\.config\)/);
});
