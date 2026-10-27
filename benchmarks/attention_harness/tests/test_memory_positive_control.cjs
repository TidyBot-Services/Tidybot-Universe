const assert=require('node:assert/strict');
const {automaticHtml}=require('../ui/memory.js');
for(const enabled of [true,false]) {
  const html=automaticHtml({enabled,positive_controls:[{title:'Run8 <test>',status:'Checking pair 2',decision:'would_pass',
    failed_gates:['safety_regression'],pairs:[{seed:102,pair_id:'0',control:true,treatment:false}],
    cross_seed:{task_trust:{verdict:'would be trusted for this task',missing:[]},seeds:[{target_seed:114,role:'transfer',verdict:'transfer_passed'}]},errors:['test <error>']}]});
  for(const text of ['Test · not counted in results','Run8 &lt;test&gt;','would pass','safety_regression','102 / 0','Present','Absent','would be trusted for this task','Seed 114','test &lt;error&gt;'])assert.ok(html.includes(text),text);
  assert.ok(!html.includes('Note checked and trusted'));
}
console.log('PASS: positive-control section, pair results, gates, verdicts, escaped errors, including Off');
