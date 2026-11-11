// 11-09 (user): a Remote run's sidebar card showed 0/10 while its page said 5 versions. The card must use the
// run's own limit (agent_resources.limits, as the used count already does) before the 10-version default.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
test('sidebar version limit comes from the run, not the formal default',()=>{
  assert.match(live,/attempts_limit:agent\?\(session\.agent\?\.limits\?\.formal_versions\?\?session\.agent_resources\?\.limits\?\.formal_versions\?\?session\.agent_versions_limit\?\?10\)/);
  assert.match(live,/const counts=session\.agent\?\.counts\|\|session\.agent_resources\?\.used;/,'used and limit read the same record');
});
// 11-11 (user): a run that had just started still showed 0/10, because agent_resources appears only after
// the ledger's first progress. The list row now carries the manifest's limit from the start.
test('the run list carries the version limit before the ledger starts',()=>{
  const server=fs.readFileSync(path.join(__dirname,'../daily_dev_v2.py'),'utf8');
  assert.match(server,/versions_limit=\(config\.get\('agent_budget'\) or \{\}\)\.get\('formal_versions'\)/);
  assert.match(server,/row\['agent_versions_limit'\]=versions_limit/);
});
// 11-11 (user): pictures stayed black after a gateway restart, until a reload. A failed load is not remembered.
test('a task picture that failed to load is asked for again',()=>{
  assert.match(live,/\.catch\(\(\)=>\{taskPictureReady\.delete\(src\);return null;\}\)/);
});
test('Loading screen: one short status line, the explanation is a centred hint under the bar',()=>{
  const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert');
  const ui=path.join(__dirname,'..','ui');const live=fs.readFileSync(path.join(ui,'live.js'),'utf8');const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
  assert.match(live,/class="op-loading-hint" data-op-loading-hint hidden/);
  assert.match(live,/'Waiting for the first picture'/);
  assert.doesNotMatch(live,/Waiting for the first picture… the simulator/);
  assert.match(html,/\.op-loading-hint\{[^}]*text-align:center/);
});
