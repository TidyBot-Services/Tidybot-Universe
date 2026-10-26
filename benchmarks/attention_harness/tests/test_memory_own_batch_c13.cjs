const test=require('node:test');
const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');
test('interrupted begin stays visible even while master switch is Off',()=>{
  const html=automaticHtml({enabled:false,status:'Off',current:{title:'Repair pickup',stage:'begin',paused:true},errors:['Automatic checks paused: catalog <bad>']});
  for(const text of ['Repair pickup','Preparing validation batch','waiting to retry','role="alert"','catalog &lt;bad&gt;'])assert.ok(html.includes(text),text);
  assert.ok(!html.includes('pair undefined'));
  assert.ok(!html.includes('pair 0'));
});
