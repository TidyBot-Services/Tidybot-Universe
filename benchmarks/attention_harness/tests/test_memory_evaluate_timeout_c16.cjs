const test=require('node:test');
const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');

test('evaluation progress and paused error stay visible without a sixth pair',()=>{
  for(const paused of [false,true]){
    const html=automaticHtml({enabled:true,current:{title:'Pickup',stage:'evaluate',paused},
      errors:paused?['Automatic checks paused: timed out']:[]});
    assert.ok(html.includes('Evaluating 5 pairs (this can take several minutes)'));
    assert.ok(!html.includes('pair undefined')&&!html.includes('pair 6'));
    if(paused)assert.ok(html.includes('role="alert"')&&html.includes('timed out'));
  }
});
