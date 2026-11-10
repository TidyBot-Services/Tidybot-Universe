const test=require('node:test');const assert=require('node:assert');const fs=require('node:fs');const path=require('node:path');
const ui=path.join(__dirname,'..','ui');const live=fs.readFileSync(path.join(ui,'live.js'),'utf8');const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
test('Limits: read-only boxes become one line under the versions field (11-11, user)',()=>{
  assert.match(live,/<p class="ab-limits-note" data-limits-note><\/p>/);
  assert.equal((live.match(/ab-limit-info">/g)||[]).length,3,'robot time, robot steps and AI tokens are info-only');
  assert.match(html,/\.ab-launch-dialog \.ab-limit-info,#attentionbench-clear \.ab-launch-dialog \.ab-launch-field\[data-no-time-limit\]\{display:none!important\}/);
  assert.match(live,/updateLimitsNote\(form\);\n\s*const stopLabel/);
  assert.match(live,/Billed to your own key':labTokens;if\(typeof updateLimitsNote==='function'\)updateLimitsNote\(form\);/);
  assert.match(live,/steps=Math\.round\(\(Number\(form\.sdk_calls\?\.value\)\|\|0\)\/versions\)/,'steps are per attempt, not the run total');
  assert.match(live,/<input name="attempt_wall_seconds" type="number" readonly hidden>/,'hidden fields still submit');
});
