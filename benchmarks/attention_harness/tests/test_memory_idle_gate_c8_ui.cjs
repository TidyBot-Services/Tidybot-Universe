const test=require('node:test');
const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');

test('blocking experiment and skipped stale runs are visible and escaped',()=>{
  for(const enabled of [true,false]){
    const html=automaticHtml({enabled,status:'Another run has unresolved work: devexp-live',
      ignored_stale_runs:[{id:'devexp-old<script>',reason:'scheduler not running'}]});
    for(const text of ['devexp-live','Skipped stale run: devexp-old&lt;script&gt;','scheduler not running'])
      assert.ok(html.includes(text),text);
    assert.ok(!html.includes('<script>'));
  }
});
