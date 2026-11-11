// 11-12: on Remote, the owner of a running run saw Stop run but no Pause — a CSS rule for
// data-lobby-view="mine" hid [data-dev-pause] and [data-dev-resume]. The owner must see them;
// everyone else must not.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'), 'utf8');
const hidden = view => [...html.matchAll(new RegExp(`#attentionbench-clear\\[data-lobby-view="${view}"\\] :is\\(([^)]*)\\)\\{display:none!important\\}`, 'g'))].map(m => m[1]).join(',');

test('the owner of a Remote run is not hidden Pause or Resume', () => {
  const mine = hidden('mine');
  assert.ok(mine.length > 0, 'the rule for your own run still exists');
  assert.doesNotMatch(mine, /data-dev-pause|data-dev-resume/);
});

test("someone else's Remote run still hides Pause, Resume and Stop", () => {
  const other = hidden('other');
  for (const sel of ['[data-dev-pause]', '[data-dev-resume]', '#ab-emergency-interrupt', '#ab-cancel-run']) assert.ok(other.includes(sel), sel);
});
