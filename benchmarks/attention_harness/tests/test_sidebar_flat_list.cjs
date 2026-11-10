// 11-10 (user): the run list is one flat list by default (no person-then-task nesting), with Task and Who chips;
// 11-11 (user): the "More: status filter and grouping" fold under the flat list is gone.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
test('flat list is the default; no More fold, and a saved status filter cannot hide runs there (user 11-11)',()=>{
  assert.match(live,/let runListMode=preference\('attention\.sidebar-layout'\)==='grouped'\?'grouped':'flat'/);
  assert.match(live,/const flatView=!DEBUG_VIEW&&runListMode==='flat'/,'the engineering debug view keeps its own list');
  assert.doesNotMatch(live,/More: status filter and grouping|data-list-more/,'the More fold is removed');
  assert.match(live,/function sidebarMatches\(r\) \{[\s\S]{0,260}if\(!DEBUG_VIEW&&runListMode==='flat'\)return true;/,'flat list ignores a saved status filter');
  assert.match(live,/Back to one list/);
});
test('sections, chips and card subline',()=>{
  for(const s of ["section('running','Running now'","section('mine','My runs'","section('earlier','Earlier · newest first'"])assert.ok(live.includes(s),s);
  assert.match(live,/chip\('data-sidebar-task','','All',records\.length,!sidebarTask\)/);
  assert.match(live,/chip\('data-lb-mode','mine','Mine'/,'Who reuses the lobby mode buttons');
  assert.match(live,/<span class="ab-fcard-sub" data-run-sub>/,'mockup card: task · seed · time under the name');
  assert.match(live,/\(c\|\|customRunName\(r\)\)&&!sidebarTask\?taskTitle\(r\)/,'a picked task is not repeated on every card');
  assert.match(live,/list\.map\(flatCard\)/,'the flat list uses its own card');
  assert.match(live,/One robot · \$\{/,'the robot box becomes one line');
});
