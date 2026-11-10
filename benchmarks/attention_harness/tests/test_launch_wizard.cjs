const test=require('node:test');const assert=require('node:assert');const fs=require('node:fs');const path=require('node:path');
const ui=path.join(__dirname,'..','ui');const live=fs.readFileSync(path.join(ui,'live.js'),'utf8');const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');
test('Run configuration is a four-step wizard: Task, Help, Models, Start',()=>{
  assert.match(live,/bindLaunchWizard\(launchDialog,form\);/);
  const steps=[...live.matchAll(/\{key:'(\w+)',label:'(\w+)',note:'[^']*',sections:\[([^\]]*)\]\}/g)].map(m=>[m[1],m[3]]);
  assert.deepStrictEqual(steps,[['task',"'what'"],['help',"'help'"],['models',"'models','reviewer'"],['start',"'name','limits','advanced'"]]);
});
test('Start run shows only on the last step; Enter before it moves to the next step',()=>{
  assert.match(live,/finals\.forEach\(b=>b\.classList\.toggle\('wz-off',step!==last\)\)/);
  assert.match(live,/form\.addEventListener\('submit',event=>\{if\(step<last\)\{event\.preventDefault\(\);event\.stopImmediatePropagation\(\)/);
  assert.match(live,/form\.addEventListener\('invalid'/);
  assert.match(html,/\.ab-launch-dialog \.wz-off,#attentionbench-clear \.ab-launch-dialog \.wz-hide\{display:none!important\}/);
});
test('Step 1 is the picker alone: Session type joins Help, the name suffix joins Start, Robot hides',()=>{
  assert.match(live,/if\(mode&&helpCount\)helpCount\.before\(mode\)/);
  assert.match(live,/field\('robot_model'\)\?\.classList\.add\('wz-hide'\)/);
  assert.match(live,/sec\.dataset\.runConfigSection='name'/);
});
