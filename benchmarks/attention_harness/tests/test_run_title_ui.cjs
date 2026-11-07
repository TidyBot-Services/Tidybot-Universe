// The run page title: the status chip always, and New run (the page's call to action) only once the run has ended.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/live.js'), 'utf8');
const start = source.indexOf('  function operatorTitle(snapshot) {');
const block = source.slice(start, source.indexOf('\n  }\n', start) + 4);
const esc = v => String(v ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

function title(ended) {
  const record = {development: true, session_id: 'v2-k', created_at: 1};
  return vm.runInNewContext(block + '\noperatorTitle(snapshot)', {
    esc, snapshot: {run: {seed: 102, policy_id: 'autonomous'}, ended}, selectedDev: 'v2-k', selectedRun: null,
    navigationRecords: () => [record], taskTitle: () => 'Pick Place: Counter → Sink', taskDisplayName: () => 'Pick Place: Counter → Sink', policyTitle: () => 'Autonomous',
    relativeTime: () => '23 h ago', recordStatus: () => 'Needs review', outcomeKind: () => '', isTerminal: s => s.ended,
    launchButton: (s, hero) => `<button class="ab-button-primary${hero ? ' op-new-run' : ''}" type="button" data-open-launch>New run</button>`,
    fixtureSceneNotice: () => '',
  });
}

test('an ended run puts New run in the page title, next to its status', () => {
  const html = title(true);
  assert.match(html, /Run · 23 h ago · Autonomous · Scene seed 102/);
  assert.match(html, /Pick Place <em>Counter → Sink<\/em>/);
  assert.match(html, /class="op-title-actions"><span class="op-title-status" data-outcome="">Needs review<\/span><button class="ab-button-primary op-new-run"[^>]*data-open-launch>New run/);
});

test('a running run keeps New run out of the title', () => {
  assert.doesNotMatch(title(false), /data-open-launch/);
});

test('the title keeps to run, time, policy and seed; what a run cannot be compared with moves to Resources', () => {
  const notesStart = source.indexOf('  function operatorRunNotes(snapshot) {');
  const notes = source.slice(notesStart, source.indexOf('\n  }\n', notesStart) + 4);
  const config = {execution_settings: {profile: 'r3-20261025'}, dev_sdk_description: 'base enabled', dev_sdk_sha256: 'b'.repeat(64), scene_verification: 'fixture_contract_only'};
  const snapshot = {run: {seed: 101, policy_id: 'reactive_help'}, dev_session: {locked: {config}}};
  const html = vm.runInNewContext(block + '\noperatorTitle(snapshot)', {
    esc, snapshot, selectedDev: null, selectedRun: null, navigationRecords: () => [], taskTitle: () => 'Task', policyTitle: () => 'Reactive help', relativeTime: () => 'just now', isTerminal: () => false,
  });
  assert.match(html, /<p>Run · just now · Reactive help · Scene seed 101<\/p>/);
  assert.doesNotMatch(html, /SDK description|Scene verification|not compared|not comparable/);
  const scene = () => '<span data-scene-verification>Scene verification: fixture rules only · scene positions not verified · interactive · not compared</span>';
  const moved = vm.runInNewContext(notes + '\noperatorRunNotes(snapshot)', {esc, snapshot, fixtureSceneNotice: scene});
  for (const text of [/Execution settings r3 · not comparable with earlier runs/, /SDK description: base enabled/, /title="b{64}"/, /scene positions not verified · interactive · not compared/]) assert.match(moved, text);
  assert.equal(vm.runInNewContext(notes + '\noperatorRunNotes(snapshot)', {esc, snapshot: {run: {}}, fixtureSceneNotice: () => ''}), '');
  assert.match(source, /<\/div>\$\{operatorRunNotes\(snapshot\)\}<div class="op-budget-foot">/);
});

test('host drift and differing hosts are visible on the run page', () => {
  const html=vm.runInNewContext(block+'\noperatorTitle(snapshot)',{
    esc,snapshot:{run:{run_id:'run-1'},dev_v2:{jobs:[{receipt:{host_drift:[{path:'/lib/libc.so.6',expected:'old',actual:'new'}],host_fingerprint_sha256:'a'}},{receipt:{host_fingerprint_sha256:'b'}}]}},
    selectedDev:null,selectedRun:null,navigationRecords:()=>[],taskTitle:()=>'Task',policyTitle:()=>'',
    relativeTime:()=>'',recordStatus:()=>'',outcomeKind:()=>'',isTerminal:()=>false,
    fixtureSceneNotice:()=>'',
  });
  assert.match(html,/host libs changed since lock built: \/lib\/libc.so.6: old -> new/);
  assert.match(html,/host differs/);
});
