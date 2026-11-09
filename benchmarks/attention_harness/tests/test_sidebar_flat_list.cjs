// 11-10 (user): the run list is one flat list by default (no person-then-task nesting), with Task and Who chips;
// grouping is still available under "More".
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
test('flat list is the default and grouping stays reachable',()=>{
  assert.match(live,/let runListMode=preference\('attention\.sidebar-layout'\)==='grouped'\?'grouped':'flat'/);
  assert.match(live,/const flatView=!DEBUG_VIEW&&runListMode==='flat'/,'the engineering debug view keeps its own list');
  assert.match(live,/data-sidebar-layout="\$\{m\}"/,'One list / Grouped switch under More');
  assert.match(live,/Back to one list/);
});
test('sections, chips and card subline',()=>{
  for(const s of ["section('running','Running now'","section('mine','My runs'","section('earlier','Earlier · newest first'"])assert.ok(live.includes(s),s);
  assert.match(live,/chip\('data-sidebar-task','','All',records\.length,!sidebarTask\)/);
  assert.match(live,/chip\('data-lb-mode','mine','Mine'/,'Who reuses the lobby mode buttons');
  assert.match(live,/<small class="ab-run-sub" data-run-sub>/);
  assert.match(live,/!sidebarTask\?taskTitle\(r\):''/,'a picked task is not repeated on every card');
});
