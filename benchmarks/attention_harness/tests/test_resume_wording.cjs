// 11-12 user: after Pause → Resume a Remote run with 19 tries showed "Starting the first attempt".
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const js = fs.readFileSync(path.join(__dirname, '..', 'ui', 'live.js'), 'utf8');
const start = js.indexOf('  function operatorLifecycleCopy(snapshot) {');
const end = js.indexOf('\n  }\n', start) + 4;
// eslint-disable-next-line no-new-func
const copy = new Function(`${js.slice(start, end)}; return operatorLifecycleCopy;`)();
const base = l => ({dev_v2: {lifecycle: l}, lobby: {state: 'running'}, run: {}, attempts: [], try_attempts: [], dev_session: {versions: []}});

test('a resumed run that already has tries says Resuming', () => {
  const s = base({phase: 'prepared'});s.try_attempts = [{}, {}];
  assert.equal(copy(s).short, 'Resuming');
});
test('a brand-new run still says it is starting its first attempt', () => {
  assert.equal(copy(base({phase: 'prepared'})).short, 'Starting the first attempt');
});
test('a queued run still says it is waiting in line', () => {
  const s = base({phase: 'prepared'});s.lobby.state = 'queued';s.try_attempts = [{}];
  assert.equal(copy(s).short, 'Waiting in line');
});
