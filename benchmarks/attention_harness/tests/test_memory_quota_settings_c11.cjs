const test=require('node:test');
const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');

test('weekly use shows configured stop and the legacy default',()=>{
 for(const enabled of [false,true]){
  const base={enabled,weekly_quota:61,weekly_quota_age_seconds:10};
  assert.ok(automaticHtml(base).includes('Codex weekly use: 61% (reading less than a minute ago) · stop at 70%'));
  for(const weekly_quota_stop_percent of [60,60.5,100]){
   assert.ok(automaticHtml({...base,weekly_quota_stop_percent}).includes(`Codex weekly use: 61% (reading less than a minute ago) · stop at ${weekly_quota_stop_percent}%`));
  }
 }
});
