// Offline tests of the production routing helpers; no browser, sockets or dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/live.js'), 'utf8');
const section = source.slice(source.indexOf('  const BYOK_ALL_ROLES'), source.indexOf('  function bindLaunch()'));
function load(allRoles = false) {
  const context = vm.createContext({});
  vm.runInContext(section.replace('BYOK_ALL_ROLES = false', `BYOK_ALL_ROLES = ${allRoles}`) +
    '\nthis.api={keysSelection,keyRoleAssignments,keysSectionHtml,BYOK_ALL_ROLES};', context);
  return context.api;
}
const api = load();
const plain = value => JSON.parse(JSON.stringify(value));
function state(mode = 'lab') {
  return {mode, shared: {credential:'mine', model:' shared-model '},
    choice: {dev:'codex', eval:'codex', advisor:'codex'}, model: {dev:'dev-model', eval:'eval-model', advisor:'advisor-model'},
    creds: [{credential_id:'mine', provider:'deepseek', expires_at:1000},
      {credential_id:'second', provider:'openai-compatible', expires_at:1000}]};
}
const route = (credential, provider, model) => ({credential, provider, model});
const select = (value, allRoles = false) => plain(load(allRoles).keysSelection(value, undefined, 100));

test('phase 1 is the production default', () => {
  assert.match(section, /const BYOK_ALL_ROLES = false;/);
  assert.equal(api.BYOK_ALL_ROLES, false);
});
for (const allRoles of [false, true]) {
  test(`Lab mode ignores saved and per-role keys (phase 2: ${allRoles})`, () => {
    const s = state();
    s.choice = {dev:'key:mine', eval:'key:second', advisor:'key:mine'};
    assert.equal(select(s, allRoles), null);
  });
  test(`all-lab custom routing returns null (phase 2: ${allRoles})`, () => {
    assert.equal(select(state('different'), allRoles), null);
  });
  test(`shared key reaches exactly the supported roles (phase 2: ${allRoles})`, () => {
    const roles = {dev:route('mine', 'deepseek', 'shared-model')};
    if (allRoles) Object.assign(roles, {eval:roles.dev, advisor:roles.dev});
    assert.deepEqual(select(state('all'), allRoles), {roles});
  });
  test(`different roles keep their own credential and model (phase 2: ${allRoles})`, () => {
    const s = state('different');
    s.choice = {dev:'key:mine', eval:'key:second', advisor:'key:mine'};
    const roles = {dev:route('mine', 'deepseek', 'dev-model')};
    if (allRoles) Object.assign(roles, {eval:route('second', 'openai-compatible', 'eval-model'), advisor:route('mine', 'deepseek', 'advisor-model')});
    assert.deepEqual(select(s, allRoles), {roles});
  });
}
test('phase 1 ignores unsupported role choices even if stale state contains them', () => {
  const s = state('different');s.choice.eval = 'key:missing';s.choice.advisor = 'key:mine';
  assert.equal(select(s), null);
});
test('phase 2 supports Eval-only and Advisor-only overrides with Dev on lab', () => {
  for (const role of ['eval', 'advisor']) {
    const s = state('different');s.choice[role] = 'key:second';
    assert.deepEqual(select(s, true), {roles:{[role]:route('second', 'openai-compatible', `${role}-model`)}});
  }
});
test('mode switches restore independent custom choices without mutating state', () => {
  const s = state('different');s.choice.dev = 'key:second';
  const before = JSON.stringify(s), custom = select(s);
  s.mode = 'all';assert.equal(select(s).roles.dev.credential, 'mine');
  s.mode = 'lab';assert.equal(select(s), null);
  s.mode = 'different';assert.deepEqual(select(s), custom);assert.equal(JSON.stringify(s), before);
});
for (const mode of ['all', 'different']) {
  test(`${mode}: missing, deleted and expired keys cannot silently fall back to lab`, () => {
    const s = state(mode);s.choice.dev = 'key:mine';s.creds = [];
    assert.throws(() => select(s), /missing or has expired/);
    s.creds = [{credential_id:'mine', provider:'deepseek', expires_at:100}];
    assert.throws(() => select(s), /expired/);
  });
  test(`${mode}: empty model is rejected; model whitespace is trimmed`, () => {
    const s = state(mode);s.choice.dev = 'key:mine';s.model.dev = s.shared.model = '  ';
    assert.throws(() => select(s), /model name/);
    s.model.dev = s.shared.model = ' custom-model ';
    assert.equal(select(s).roles.dev.model, 'custom-model');
  });
}
test('unsaved shared mode cannot start; pending saves cannot launch with old routing', () => {
  const s = state('all');s.shared.credential = '';
  assert.throws(() => select(s), /Save a key/);
  s.shared.credential = 'mine';s.saving = true;
  assert.throws(() => select(s), /finish saving/);
  s.mode = 'lab';assert.equal(select(s), null);
});
test('selection exposes only the public credential, provider and model', () => {
  const s = state('all');s.creds[0].base_url_host = 'example.invalid';s.creds[0].key_set = true;
  assert.deepEqual(Object.keys(select(s).roles.dev).sort(), ['credential', 'model', 'provider']);
});
test('template defaults to Lab and collapsed roles, retaining named lab models and password input', () => {
  const html = api.keysSectionHtml();
  assert.match(html, /value="lab" data-keys-mode checked/);
  assert.match(html, /<details[^>]*data-keys-details>/);
  assert.equal((html.match(/type="radio"/g) || []).length, 3);
  for (const role of ['dev', 'eval', 'advisor']) {
    assert.equal((html.match(new RegExp(`name="${role}_model"`, 'g')) || []).length, 1);
    const options = html.match(new RegExp(`<select name="${role}_model"[^>]*>(.*?)</select>`))[1];
    assert.match(options, /<option selected>gpt-6\.1-sol<\/option><option>gpt-6-astra<\/option>/);
    assert.equal(options.includes('gpt-6-sol'), role !== 'advisor');
  }
  assert.match(html, /data-key-value type="password" autocomplete="off"/);
  assert.equal((html.match(/data-key-value/g) || []).length, 1);
});
test('shared phase 2 explanation follows the real feature flag', () => {
  assert.match(api.keysSectionHtml(), /data-keys-phase2 >Eval and Advisor use the lab's models until phase 2/);
  assert.match(load(true).keysSectionHtml(), /data-keys-phase2 hidden>/);
});
