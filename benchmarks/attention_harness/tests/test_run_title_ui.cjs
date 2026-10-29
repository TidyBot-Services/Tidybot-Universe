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

test('r3 execution label is shown beside the run title', () => {
  const html=vm.runInNewContext(block+'\noperatorTitle(snapshot)',{
    esc,snapshot:{run:{seed:102},dev_session:{locked:{config:{execution_settings:{profile:'r3-20261025'}}}}},
    selectedDev:null,selectedRun:null,navigationRecords:()=>[],taskTitle:()=>'Task',taskDisplayName:()=>'Task',policyTitle:()=>'',
    relativeTime:()=>'',recordStatus:()=>'',outcomeKind:()=>'',isTerminal:()=>false,
  });
  assert.match(html,/Execution settings r3 · not comparable with earlier runs/);
  assert.doesNotMatch(title(false),/Execution settings r3/);
});
