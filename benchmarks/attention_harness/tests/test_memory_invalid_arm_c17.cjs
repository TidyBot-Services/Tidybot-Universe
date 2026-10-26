const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');
const discarded_pairs=[{pair:3,version:1,arm:'treatment',reason:'source_size_limit_exceeded'},
  {pair:3,version:2,arm:'control',reason:'<source_validation_failed>'}];
for(const enabled of [true,false]) {
  const html=automaticHtml({enabled,current:{title:'Note',pair:3,arm:'control',discarded_pairs,
    replacement_notice:'Pair 3 discarded: treatment produced invalid source (source_size_limit_exceeded); running replacement 1 of 2'},results:[]});
  assert.ok(html.includes('running replacement 1 of 2'));
  assert.ok(html.includes('Pair 3 discarded (attempt 1)'));
  assert.ok(html.includes('&lt;source_validation_failed&gt;'));
}
const html=automaticHtml({enabled:false,results:[{title:'Note',status:'inconclusive',discarded_pairs}]});
assert.ok(html.includes('Could not finish'));
assert.ok(html.includes('Pair 3 discarded (attempt 2)'));
assert.ok(html.includes('note stays Unvalidated'));
console.log('PASS: C17 discarded pairs remain visible while running, Off and finished');
