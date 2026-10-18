const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const ui=path.join(__dirname,'../dev_ui');
const view=require(path.join(ui,'view_model.js'));
const app=fs.readFileSync(path.join(ui,'app.js'),'utf8');
const html=fs.readFileSync(path.join(ui,'index.html'),'utf8');

test('05 resources preserve goal used/reserved/remaining and isolate wait, Eval and model time',()=>{
  const goal={used:{help_wait_seconds:81,eval_calls:2,model_seconds:140,execution_seconds:360},
    reserved:{help_wait_seconds:120,eval_calls:1,model_seconds:120,execution_seconds:480},
    remaining:{help_wait_seconds:99,eval_calls:0,model_seconds:240,execution_seconds:900}};
  const rows=Object.fromEntries(view.resourceRows(goal).map(row=>[row.key,row]));
  assert.deepEqual([rows.help_wait_seconds.used,rows.help_wait_seconds.reserved,rows.help_wait_seconds.remaining],[81,120,99]);
  assert.match(rows.help_wait_seconds.label,/等待人工\/AdvisorProxy 回答的时间/);
  assert.deepEqual([rows.eval_calls.used,rows.model_seconds.used,rows.execution_seconds.used],[2,140,360]);
  assert.match(html,/id="resource-rows"/);
  assert.match(html,/id="resources-raw"/);
  assert.match(app,/row\.dataset\.resource=resource\.key/);
  assert.match(app,/resource-wait/);
});

test('Memory query events become one card with provenance, digest, elapsed time and verification',()=>{
  const events=[
    {at:1760778000,kind:'planner_memory_query_started',detail:{cell:'cell-a',version:2,started_at:1760778000}},
    {at:1760778002,kind:'planner_memory_retrieved',detail:{cell:'cell-a',version:2,authority:'authority-store',namespace:'dev-a',packet:{sha256:'123456789abc'+ 'f'.repeat(52)},versions:[{memory_id:'memory-1',version:3,grant_id:'grant-1'}],verification:{passed:true}}},
    {at:1760778003,kind:'planner_memory_query_finished',detail:{cell:'cell-a',version:2,started_at:1760778000,actual_seconds:3,status:'completed'}},
    {at:1760778100,kind:'planner_memory_query_started',detail:{cell:'cell-a',version:3,started_at:1760778100}},
    {at:1760778102,kind:'planner_memory_query_finished',detail:{cell:'cell-a',version:3,started_at:1760778100,actual_seconds:2,status:'failed',error:'PermissionError',verified:false}}
  ];
  const records=view.memoryRecords(events);
  assert.equal(records.length,2);
  assert.deepEqual({status:records[0].status,sources:records[0].sources,authority:records[0].authority,
    namespace:records[0].namespace,packetSha:records[0].packetSha,seconds:records[0].seconds,verification:records[0].verification},
    {status:'finished',sources:[{memory_id:'memory-1',version:3,grant_id:'grant-1'}],authority:'authority-store',namespace:'dev-a',packetSha:'123456789abc',seconds:3,verification:true});
  assert.equal(records[1].status,'failed');assert.equal(records[1].verification,false);
  assert.match(html,/id="planner-memory-cards"/);assert.match(html,/id="planner-memory-raw"/);
  assert.match(app,/article\.dataset\.memoryQuery/);
});

test('hash restoration selects only an existing experiment and silently falls back',()=>{
  const id='devexp-'+'a'.repeat(24),missing='devexp-'+'b'.repeat(24);
  const items=[{experiment_id:id}];
  assert.equal(view.restoredExperiment(`#exp=${id}`,items),id);
  assert.equal(view.restoredExperiment(`#exp=${missing}`,items),null);
  assert.equal(view.restoredExperiment('#exp=bad',items),null);
  assert.match(app,/location\.hash=`exp=\$\{id\}`/);
  assert.match(app,/if\(restored\)await selectExperiment\(restored\)/);
  assert.match(app,/dataset\.selectedExperiment=id/);
});

test('app startup keeps browser history and clears a missing experiment hash without error',async()=>{
  const elements=new Map();
  const element=id=>{
    if(!elements.has(id))elements.set(id,{
      value:'',checked:false,options:[],selectedOptions:[],dataset:{},textContent:'',
      append(){},replaceChildren(){},addEventListener(){},querySelectorAll(){return [];},
      scrollIntoView(){this.scrolled=true}
    });
    return elements.get(id);
  };
  const location={hash:'#exp=missing',pathname:'/dev',search:'?tab=history'};
  const replacements=[];
  const browserHistory={replaceState(...args){replacements.push(args);location.hash='';}};
  const sandbox={
    document:{getElementById:element,querySelector:()=>({content:'token'}),createElement:()=>({selected:false})},
    location,history:browserHistory,DevUiView:view,crypto:{randomUUID:()=> 'test-id'},
    setInterval(){},
    fetch:async path=>({ok:true,json:async()=>{
      if(path==='/api/dev/options')return {tasks:[{task_id:'task',task_prompt:'Task',sdk:{sdk_calls:1,control_steps:1,attempt_seconds:1}}],policies:[],robot:'robot',execution_arm:'arm'};
      if(path==='/api/dev/experiments')return [];
      if(path==='/api/dev/preview')return {};
      throw Error(`Unexpected request: ${path}`);
    }})
  };
  sandbox.window=sandbox;
  vm.runInNewContext(app,sandbox);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(typeof sandbox.window.history.replaceState,'function');
  assert.deepEqual(replacements,[[null,'','/dev?tab=history']]);
  assert.equal(location.hash,'');
  assert.equal(element('error').textContent,'');
});

test('Eval checkbox default and actual create selection submit enabled state without model override',()=>{
  const idValues={mode:'experiment',seeds:'101',help:'2','target-depth':'3','target-executions':'3',
    'max-versions':'5',coverage:'strict_full_depth','budget-config':'{"eval_agent":{"enabled":false,"model":"other"}}',
    'human-wait':'60','memory-url':'','memory-authority':'','memory-namespace':''};
  const elements=Object.fromEntries(Object.entries(idValues).map(([id,value])=>[id,{value}]));
  elements.tasks={selectedOptions:[{value:'task'}]};elements.policies={selectedOptions:[{value:'policy'}]};
  elements['planner-enabled']={checked:false};elements['eval-enabled']={checked:true};
  const start=app.indexOf('function createSelection()');
  const end=app.indexOf("$('create').onsubmit",start);
  const createSelection=vm.runInNewContext(app.slice(start,end)+'\ncreateSelection',{
    $:id=>elements[id],crypto:{randomUUID:()=> 'test-id'},JSON,Number,DevUiView:view
  });
  assert.equal(createSelection().eval_agent.enabled,true);
  elements.mode.value='interactive';elements['eval-enabled'].checked=false;
  assert.equal(createSelection().eval_agent.enabled,false);
  assert.match(html,/id="eval-enabled" type="checkbox" checked/);
  assert.match(app,/request\('\/api\/dev\/experiments',createSelection\(\)\)/);
});

test('server gate and budget errors remain readable in the page alert',async()=>{
  const alert={textContent:'',scrollIntoView(){this.scrolled=true}};
  const start=app.indexOf('async function request('),end=app.indexOf('function option(',start);
  const functions=vm.runInNewContext(app.slice(start,end)+'\n({request,error})',{
    fetch:async()=>({ok:false,status:400,json:async()=>({error:'strict cell budget below worst-case requirement'})}),
    headers:{},$:id=>id==='error'?alert:null,JSON,String
  });
  let failure;
  try{await functions.request('/api/dev/experiments',{});}catch(e){failure=e;}
  assert.match(failure.message,/strict cell budget/);
  functions.error(failure);
  assert.match(alert.textContent,/strict cell budget/);
  assert.equal(alert.scrolled,true);
  assert.match(html,/id="error" role="alert"/);
});
