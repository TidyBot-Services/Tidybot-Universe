// 11-11 (user): on Remote, runs of newer RoboCasa365 tasks sat under "Archived · unlinked records".
// The gateway's /api/run-options carries only dev_v2 (no top-level profiles, no dev_loop), and resolvedTask did not read it.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');

function loadResolvedTask(launchOptions){
  const titles=live.match(/const taskTitles = \{[^\n]*\};/)[0];
  const body=live.match(/function resolvedTask\(item\)\{[\s\S]*?\n  \}/)[0];
  const label=v=>String(v).replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
  return new Function('launchOptions','label',`${titles}\n${body}\nreturn resolvedTask;`)(launchOptions,label);
}
// The Remote gateway's shape (remote_gateway/policy.filter_options): dev_v2 + hosted only.
const remoteOptions={dev_v2:{profiles:['categorize_condiments','close_blender_lid','coffee_setup_mug','open_cabinet']
  .map(t=>({suite:'robocasa365',task_id:t,enabled:true}))},hosted:{}};
const run=t=>({suite:'robocasa365',task_id:t,task_name:null});

test('a Remote run of a task listed only under dev_v2 is linked, not archived',()=>{
  const resolvedTask=loadResolvedTask(remoteOptions);
  assert.equal(resolvedTask(run('categorize_condiments')),'Categorize condiments');
  assert.equal(resolvedTask(run('close_blender_lid')),'Close blender lid');
  assert.equal(resolvedTask(run('coffee_setup_mug')),'Coffee setup mug');
  assert.equal(resolvedTask(run('open_cabinet')),'Open the cabinet','the fixed title still wins');
});
test('a task the host does not list, or a run without a suite, stays unlinked',()=>{
  const resolvedTask=loadResolvedTask(remoteOptions);
  assert.equal(resolvedTask(run('weigh_ingredients')),null);
  assert.equal(resolvedTask({task_id:'categorize_condiments'}),null);
  assert.equal(resolvedTask({suite:'robocasa',task_id:'categorize_condiments'}),null,'suite must match too');
});
test('8769 shape (dev_loop profiles) still resolves',()=>{
  const resolvedTask=loadResolvedTask({profiles:[],dev_loop:{profiles:[{suite:'robocasa365',task_id:'arrange_tea'}]}});
  assert.equal(resolvedTask(run('arrange_tea')),'Arrange tea');
});

// 11-11 (user): "以后就不需要archive了" — there is no Archived fold any more; every run stays in the run list.
test('no Archived fold: a run whose task has no known title stays in the list under its task id',()=>{
  assert.doesNotMatch(live,/data-unlinked-archive|ab-unlinked-archive|Archived · \$\{|unlinked records<\/summary>/);
  assert.match(live,/const linked=records;/,'the list takes every record, linked or not');
  assert.doesNotMatch(live,/records\.filter\(r=>!resolvedTask\(r\)\)/);
  const css=fs.readFileSync(path.join(__dirname,'../ui/index.html'),'utf8');
  assert.doesNotMatch(css,/ab-unlinked-archive/);
  const titles=live.match(/const taskTitles = \{[^\n]*\};/)[0];
  const body=live.match(/function resolvedTask\(item\)\{[\s\S]*?\n  \}/)[0];
  const title=live.match(/function taskTitle\(item\)\{[^\n]*\}/)[0];
  const label=v=>String(v??'—').replaceAll('_',' ');
  const taskTitle=new Function('launchOptions','label',`${titles}\n${body}\nfunction taskName(value){return taskTitles[value]||label(value);}\n${title}\nreturn taskTitle;`)(remoteOptions,label);
  assert.equal(taskTitle({suite:'robocasa365',task_id:'weigh_ingredients'}),'weigh ingredients','unknown to the host: its task id, readable');
  assert.equal(taskTitle({}),'Unknown task','no task id at all');
  assert.equal(taskTitle(run('open_cabinet')),'Open the cabinet');
});
