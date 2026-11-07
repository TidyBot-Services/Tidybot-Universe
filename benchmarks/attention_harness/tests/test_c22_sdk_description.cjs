const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
function load(name,globals={}) {
  const start=source.indexOf(`  function ${name}(`);
  assert.ok(start>=0);
  const end=source.indexOf('\n  function ',start+1);
  return vm.runInNewContext(source.slice(start,end)+`\n${name}`,globals);
}
const common={fixtureSceneNotice:load('fixtureSceneNotice'),esc:String,devV2State:()=> 'completed',devEndSummary:()=>'',policyTitle:String,policyShort:String,recordIsActive:()=>false};
test('history records keep the frozen description; the sidebar cards no longer show it (10-30)',()=>{
  const navigation=load('devNavigationRecord',common);
  const badges=load('sidebarRunBadges',common);
  for(const description of ['base enabled','base stationary',undefined]) {
    const r=navigation({session_id:'v2-test',locked:{config:{dev_sdk_description:description,dev_sdk_sha256:'a'.repeat(64)}}});
    assert.equal(r.dev_sdk_description,description||'base stationary');
    assert.doesNotMatch(badges(r),/SDK description|sdk-description/);
  }
  assert.doesNotMatch(badges({development:false}),/SDK description/);
});
test('run detail labels use the same frozen description and digest',()=>{
  const title=load('operatorRunNotes',common);
  for(const description of ['base enabled','base stationary',undefined]) {
    const result=title({run:{},dev_session:{locked:{config:{dev_sdk_description:description,dev_sdk_sha256:'b'.repeat(64)}}}});
    assert.match(result,new RegExp(`SDK description: ${description||'base stationary'}`));
    assert.match(result,/title="b{64}"/);
  }
  assert.doesNotMatch(title({run:{}}),/SDK description/);
});
