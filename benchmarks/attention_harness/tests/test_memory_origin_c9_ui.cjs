const test = require('node:test');
const assert = require('node:assert/strict');
const {automaticHtml} = require('../ui/memory.js');
test('origin errors remain visible while Off and leave the note waiting without a verdict', () => {
  const html = automaticHtml({enabled:false, status:'Off', runs_today:2,
    errors:['Origin preparation error: <bug>. The note stays queued; the reserved run remains counted.'],
    queue:[{title:'Waiting note',uses:1}], results:[]});
  for (const text of ['role="alert"', '&lt;bug&gt;', 'reserved run remains counted', 'Waiting note', 'Runs today: 2 of 20', 'No results yet']) assert.ok(html.includes(text),text);
  assert.ok(!html.includes('Could not finish'));
});
