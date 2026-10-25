const test=require('node:test');
const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');

test('daily count shows default, configured cap, or unlimited without a meter',()=>{
 for(const enabled of [false,true]){
  const base={enabled,runs_today:43,weekly_quota:12};
  assert.ok(automaticHtml(base).includes('Runs today: 43 of 20'));
  const limited=automaticHtml({...base,daily_run_limit:86});
  assert.ok(limited.includes('Runs today: 43 of 86'));
  assert.ok(limited.includes('width:50%'));
  const unlimited=automaticHtml({...base,daily_run_limit:null});
  assert.ok(unlimited.includes('Runs today: 43 · Codex weekly use: 12%'));
  assert.ok(!unlimited.includes('Runs today: 43 of'));
  assert.ok(!unlimited.includes('ac-meter'));
  assert.ok(!unlimited.includes('NaN')&&!unlimited.includes('Infinity'));
 }
 assert.ok(automaticHtml({daily_run_limit:null}).includes('Runs today: 0</span>'));
});
