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
test('operator origin recovery events remain visible while Off and escape reasons', () => {
  for (const event of ['origin_reopened','origin_interrupted_abandoned','origin_registration_retried']) {
    const html=automaticHtml({enabled:false,events:[{event,status:'abandoned',memory_id:'p-test',reason:'Reviewed <interruption>'}]});
    for (const text of [event==='origin_reopened'?'Origin reopened':event==='origin_registration_retried'?'Origin registration retried':'Interrupted origin abandoned','p-test','Reviewed &lt;interruption&gt;','Previous run reservation remains counted']) assert.ok(html.includes(text),text);
    assert.ok(!html.includes('<interruption>'));
  }
  assert.ok(automaticHtml({events:[{event:'origin_interrupted_abandoned',status:'archiving',memory_id:'p-test',reason:'Reviewed'}]}).includes('Origin recovery incomplete'));
  assert.ok(automaticHtml({events:[{event:'origin_registration_retried',status:'retrying',memory_id:'p-test',reason:'Reviewed'}]}).includes('Origin recovery incomplete'));
});
