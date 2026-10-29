const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const body=source.slice(source.indexOf('  function devEndSummary('),source.indexOf('  function mountPolicyController('));
const devEndSummary=vm.runInNewContext(body+'\n devEndSummary');

test('the Eval/native disagreement stop reads as its own reviewed end',()=>{
  const end=devEndSummary({state:'failed',reason:'eval_native_disagreement'});
  assert.equal(end.key,'eval_native_disagreement');
  assert.equal(end.short,'Eval disagreed');
  assert.equal(end.review,true);
  assert.match(end.title,/Review required/);
  // No native field names reach the page.
  assert.doesNotMatch(JSON.stringify(end),/native_success|object_in_sink/);
});

test('other failed ends are unchanged',()=>{
  assert.equal(devEndSummary({state:'failed',reason:'agent_idle'}).title,'Ended · Execution or service failure');
});
