// The test gateway may offer one lab Codex model on Remote; the form then names every role's source, Codex included.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/live.js'), 'utf8');
const section = source.slice(source.indexOf('  const BYOK_ALL_ROLES'), source.indexOf('  function bindLaunch()'));
const context = vm.createContext({});
vm.runInContext(section + '\nthis.api={keysSelection,LAB_MODELS};', context);
const {keysSelection, LAB_MODELS} = context.api;
const plain = value => JSON.parse(JSON.stringify(value));
const sol = {...LAB_MODELS, codex: {dev: ['gpt-6.1-sol'], eval: ['gpt-6.1-sol'], advisor: ['gpt-6.1-sol']}};
const hosted = (extra) => ({mode: 'lab', labSource: 'parcc', shared: {credential: '', model: ''}, creds: [], labModels: sol, explicitCodex: true,
  choice: {dev: 'parcc', eval: 'parcc', advisor: 'parcc'}, model: {dev: '', eval: '', advisor: ''}, ...extra});
const codex = {credential: 'lab', provider: 'openai', model: 'gpt-6.1-sol'};
const parcc = {credential: 'lab', provider: 'parcc', model: 'parcc/GLM'};

test('PARCC on Remote sends PARCC for all three roles', () => {
  assert.deepEqual(plain(keysSelection(hosted(), true, 100)), {roles: {dev: parcc, eval: parcc, advisor: parcc}});
});
test('Lab Codex on Remote sends gpt-6.1-sol for all three roles, named', () => {
  const picked = {dev: 'gpt-6.1-sol', eval: 'gpt-6.1-sol', advisor: 'gpt-6.1-sol'};
  assert.deepEqual(plain(keysSelection(hosted({labSource: 'codex', labModel: picked}), true, 100)), {roles: {dev: codex, eval: codex, advisor: codex}});
});
test('Eval and Advisor can each be put on Lab Codex alone', () => {
  const s = hosted({mode: 'different', choice: {dev: 'parcc', eval: 'codex', advisor: 'parcc'}, labModel: {dev: 'parcc/GLM', eval: 'gpt-6.1-sol', advisor: 'parcc/GLM'}});
  assert.deepEqual(plain(keysSelection(s, true, 100)), {roles: {dev: parcc, eval: codex, advisor: parcc}});
  s.choice = {dev: 'parcc', eval: 'parcc', advisor: 'codex'};s.labModel = {dev: 'parcc/GLM', eval: 'parcc/GLM', advisor: 'gpt-6.1-sol'};
  assert.deepEqual(plain(keysSelection(s, true, 100)), {roles: {dev: parcc, eval: parcc, advisor: codex}});
});
test('without the host offer, local Lab Codex still leaves the roles to the server as before', () => {
  const s = hosted({labSource: 'codex', explicitCodex: false, labModels: undefined});
  assert.equal(keysSelection(s, true, 100), null);
});
test('the launch form lists Lab Codex on Remote only when the host offers it, and shows the Advisor in the summary', () => {
  assert.match(source, /typeof options\?\.hosted\?\.lab_codex\?\.model==='string'/);
  assert.match(source, /if\(labCodex\)codex\.textContent=labCodex\.label;else codex\.remove\(\);/);
  assert.match(source, /mine\.advisor\|\|labCodex\?` · Advisor \$\{label\('advisor',form\.advisor_model\?\.value\|\|''\)\}`/);
  assert.match(source, /if\(labCodex\)form\.model_source\.value='parcc';/);
  assert.match(source, /keyMode\(\)!=='lab'\|\|hosted;/);
});
