const test = require('node:test');
const assert = require('node:assert/strict');
const {adapt, cardHtml} = require('../ui/memory.js');
test('Memory card shows the actual simulated Dev permission and actor', () => {
  const row = {id:'motion', title:'Motion note', status:'active', motion_permission:{action:'dev_tier_sim_motion',actor:'dev_tier_sim_motion'}};
  const note = adapt({notes:[row]}).notes[0];
  assert.ok(cardHtml(note).includes('Motion: simulated dev tier, no human approval (dev_tier_sim_motion)'));
  assert.ok(!cardHtml(adapt({notes:[{...row,motion_permission:{action:'dev_tier_sim_motion',actor:'authenticated_human'}}]}).notes[0]).includes('no human approval'));
  assert.ok(!cardHtml(adapt({notes:[{...row,motion_permission:undefined}]}).notes[0]).includes('no human approval'));
});
