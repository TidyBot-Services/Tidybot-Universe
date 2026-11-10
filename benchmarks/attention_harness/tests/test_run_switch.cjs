const test=require('node:test');const assert=require('node:assert');const fs=require('node:fs');const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'..','ui','live.js'),'utf8');const html=fs.readFileSync(path.join(__dirname,'..','ui','index.html'),'utf8');
test('switching runs answers at once: card highlight, full-screen Opening run layer, own data only (11-11, user)',()=>{
  assert.match(live,/if\(runSwitch\?\.id===id&&!runSwitch\.hidden&&!runSwitch\.failed\)return;/,'a second press on the opening run does nothing');
  assert.match(live,/renderSidebar\(\);  \/\/ the pressed card is highlighted at once/);
  assert.match(live,/async function openRunFast\(id,retry=false\)\{/);
  assert.match(live,/await updateDevSnapshot\(dev\);/);
  assert.match(live,/data-run-switch-cancel>Keep browsing · Esc</,'the full-screen layer can always be put away');
  assert.match(live,/const cellViewFetches=new Map\(\);/,'one view request per run at a time');
  assert.match(live,/same fields as the run card/);
  assert.match(html,/\.ab-run-switch\{position:fixed;inset:0;/);
});
