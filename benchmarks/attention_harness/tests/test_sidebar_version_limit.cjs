// 11-09 (user): a Remote run's sidebar card showed 0/10 while its page said 5 versions. The card must use the
// run's own limit (agent_resources.limits, as the used count already does) before the 10-version default.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
test('sidebar version limit comes from the run, not the formal default',()=>{
  assert.match(live,/attempts_limit:agent\?\(session\.agent\?\.limits\?\.formal_versions\?\?session\.agent_resources\?\.limits\?\.formal_versions\?\?10\)/);
  assert.match(live,/const counts=session\.agent\?\.counts\|\|session\.agent_resources\?\.used;/,'used and limit read the same record');
});
