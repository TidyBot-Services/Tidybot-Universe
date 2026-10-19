const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
const real=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/devexp-7cf85c4709a356146dfe3b3c.view.json'),'utf8'));
const snippet=source.slice(source.indexOf('  function memoryPanel(snapshot) {'),source.indexOf('  function attentionRunContext(snapshot) {'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const panel=vm.runInNewContext(snippet+'\nmemoryPanel',{memoryCatalogue:null,memorySelected:null,memoryMessage:'',esc});
const baseline=()=>({memory:{control:null,ingestion:[],uses:[],records:{},dev_v2:{queues:[],validations:[],retrievals:[],generations:[],receipts:[],empty:true}},dev_v2:real});
const helpSource=source.slice(source.indexOf('  function operatorHelp(snapshot,chosen) {'),source.indexOf('  function operatorBudgetCard('));
const help=vm.runInNewContext(helpSource+'\noperatorHelp',{
  memoryPanel:panel,devEndSummary:session=>session.state==='approval_closed'?{key:'action_approval',explanation:'Closed',review:false}:null,
  operatorHeading:()=>'<h3>04 Help & replies</h3>',operatorHelpHistory:()=>'<div data-help-history></div>',
  operatorWaiting:()=>false,operatorLast:()=>null,plainError:()=>'',executionFailure:()=>null,
  operatorLifecycleCopy:()=>({text:'Working'}),isTerminal:()=>false,esc,
});

test('real daily cell payload renders an explicit empty Memory state',()=>{
  assert.equal(real.seed,102);
  assert.equal(real.policy_id,'full_trace_aware_attention_planner');
  const html=panel(baseline());
  assert.match(html,/data-memory-panel/);
  assert.match(html,/data-dev-memory-empty/);
  assert.match(html,/No Dev v2 Memory candidates/);
  assert.match(source,/\$\{runSettings\(snapshot\)\}\$\{runOverview\(snapshot\)\}\$\{executionViewer\(snapshot\)\}\$\{requestPanel\(snapshot,chosen\)\}\$\{resourceDetails\(snapshot\)\}/);
  assert.doesNotMatch(source,/class="panel ab-memory-panel"/);
});

test('Dev v2 Memory stays folded inside 04 after the request content in every operator state',()=>{
  for(const [state,phase] of [['running','sdk'],['running','approval'],['approval_closed','terminal']]){
    const snap={...baseline(),dev_session:{state},dev_v2:{...real,lifecycle:{phase}},traces:{}};
    const html=help(snap,null);
    assert.match(html,/data-core-region="request"/);
    assert.match(html,/data-help-history><\/div><details data-memory-panel/);
    assert.match(html,/<\/details><\/section>$/);
    assert.doesNotMatch(html,/<details data-memory-panel[^>]* open(?:\s|>)/);
    assert.match(html,/data-dev-memory-empty/);
  }
});

test('debug request view also keeps Dev v2 Memory inside 04',()=>{
  const requestSource=source.slice(source.indexOf('  function requestPanel(snapshot, chosen) {'),source.indexOf('  function mdLite('));
  const request=vm.runInNewContext(requestSource+'\nrequestPanel',{
    DEBUG_VIEW:true,selectedRequest:null,actionableRequests:()=>[],feedbackRequest:()=>null,
    submittedHint:()=>'',feedbackShortcut:()=>'',idleGuidance:()=>'',esc,memoryPanel:panel,
  });
  const html=request({...baseline(),dev_session:{state:'running'},requests:[]},null);
  assert.match(html,/data-core-region="request"/);
  assert.match(html,/data-memory-panel/);
  assert.match(html,/<\/details><\/section>$/);
});

test('daily panel renders proposal, validation, grant, generation and result source labels',()=>{
  const snap=baseline();
  snap.memory.dev_v2={empty:false,
    queues:[{source:'待离线导入提案',version:2,status:'pending_offline_import',kind:'source'},
            {source:'离线 result 队列',version:2,status:'offline_receipt_recorded',kind:'result'}],
    validations:[{version:2,exposure:'candidate_validation',grant_id:'validation-1',status:'dispatched',memory_ids:['memory-1']}],
    retrievals:[{version:2,authority:'fixture-authority',namespace:'daily-test',entries:[{source:'真实候选',memory_id:'memory-1',version:3,trusted:true,grant_id:'dev-grant-3',applicability:{scene_id:'scene-102'}},
      {source:'手工为测试种入',seeded_for_tests:true,excluded_from_results:true,memory_id:'memory-test',version:1,status:'trusted',trusted:true,grant_id:'dev-grant-test',applicability:{seed:102,scene_id:'scene-102',camera_names:['front','wrist'],scene_contract_ref:{uri:'/test/contract.json',sha256:'a'.repeat(64)}}}]}],
    generations:[{version:2,memory_packet_sha256:'a'.repeat(64),model_input_sha256:'b'.repeat(64),result_source_sha256:'c'.repeat(64)}],
    receipts:[{source:'手工为测试种入',operation:'result',version:2,seeded_for_tests:true,status:'receipt_recorded'}]};
  const html=panel(snap);
  for(const selector of ['data-memory-panel','data-dev-memory-panel','data-memory-queue','data-memory-validation','data-memory-retrieval','data-request-memory','data-memory-generation','data-memory-receipt'])
    assert.match(html,new RegExp(selector));
  for(const text of ['待离线导入提案','真实候选','手工为测试种入','seeded for tests, excluded from results','scene-102','dev-grant-3'])
    assert.ok(html.includes(text),text);
  assert.match(html,/ab-memory-test-label/);
  assert.match(html,/memory-test @ 1 · trusted at retrieval/);
  assert.match(html,/&quot;seed&quot;:102/);
  assert.match(html,/scene_contract_ref/);
  assert.doesNotMatch(html,/data-memory-operation/);
  assert.match(source,/data-persist-detail="planner-memory"/);
});
