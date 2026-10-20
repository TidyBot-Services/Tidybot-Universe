const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
const real=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/devexp-7cf85c4709a356146dfe3b3c.view.json'),'utf8'));
const directory=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/dev_v2_memory_directory.json'),'utf8'));
const cached=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/cache_help_projection.json'),'utf8'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const panelSource=source.slice(source.indexOf('  function memoryPanel(snapshot) {'),source.indexOf('  function attentionRunContext(snapshot) {'));
const bindSource=source.slice(source.indexOf('  function bindAttentionPanels(snapshot,chosen) {'),source.indexOf('  // End Attention mechanism presentation.'));
const snapshot={run:{run_id:'dev-fixture'},memory:{control:null,records:{},ingestion:[],dev_v2:{empty:true,queues:[],validations:[],retrievals:[],generations:[],receipts:[]}},dev_v2:real,dev_session:{state:'running'}};

function harness({canOperate=true,error=null}={}){
  const callbacks={};const sent=[];const reads=[];const confirmations=[];
  const form={dataset:{memoryId:directory.memories[0].memory_id},elements:{actor:{value:'alice'},reason:{value:'review'},expires_at:{value:''}},reportValidity:()=>true};
  const buttons=Object.fromEntries(['disable','enable','expiry','rollback'].map(action=>[action,{
    dataset:{devMemoryAction:action},closest:()=>form,addEventListener:(_,fn)=>{callbacks[action]=fn;},disabled:false,
  }]));
  const view={querySelector:()=>null,querySelectorAll:selector=>selector==='[data-dev-memory-action]'?Object.values(buttons):[]};
  let status='trusted';
  const ctx=vm.createContext({
    memoryCatalogue:null,memorySelected:null,memoryMessage:'',devMemoryCatalogue:{...directory,control:{...directory.control,can_operate:canOperate}},
    devMemoryDetail:null,devMemoryMessage:'',devMemoryError:'',devMemoryLoading:false,
    esc,view,currentSnapshot:snapshot,render:()=>{},root:{dataset:{controlToken:'fixture'}},
    fetch:async url=>{reads.push(url);return {ok:true,json:async()=>url.endsWith('/api/dev-memories')?{...directory,memories:[{...directory.memories[0],status}]}:{memory:{...directory.memories[0],status},history:[{status,actor:'alice',reason:'review'}]}};},
    controlFetch:async(url,options)=>{sent.push({url,body:JSON.parse(options.body)});if(error)return {ok:false,status:409,json:async()=>({error})};status={disable:'disabled',enable:'trusted',expiry:'trusted',rollback:'rolled_back'}[url.split('/').at(-1)];return {ok:true,json:async()=>({memory_id:form.dataset.memoryId,memory:{status}})};},
    confirm:message=>{confirmations.push(message);return true;},
  });
  vm.runInContext(panelSource+bindSource,ctx);
  vm.runInContext('bindAttentionPanels(currentSnapshot,null)',ctx);
  return {ctx,form,buttons,callbacks,sent,reads,confirmations,html:()=>vm.runInContext('memoryPanel(currentSnapshot)',ctx),state:name=>vm.runInContext(name,ctx)};
}

test('real-shaped directory shows status, source, scope and four controls in 04',()=>{
  const h=harness();const html=h.html();
  assert.match(html,/data-memory-panel/);
  assert.match(html,/data-dev-memory-directory/);
  assert.match(html,/c1-seeded-retrieve-disable-001 @ 1 · trusted/);
  assert.match(html,/seeded for tests, excluded from results/);
  assert.match(html,/scene-102/);
  for(const action of Object.keys(h.buttons))assert.match(html,new RegExp(`data-dev-memory-action="${action}"`));
  for(const action of Object.keys(h.buttons))assert.equal((html.match(new RegExp(`data-dev-memory-action="${action}"`,'g'))||[]).length,1);
  assert.match(source,/\$\{runSettings\(snapshot\)\}\$\{runOverview\(snapshot\)\}\$\{executionViewer\(snapshot\)\}\$\{requestPanel\(snapshot,chosen\)\}\$\{resourceDetails\(snapshot\)\}/);
  assert.match(source,/data-memory-operation/);
});

test('cache help is a Memory note in 04 and never an Operator reply or Help request',async()=>{
  const cell=structuredClone(real);
  cell.versions=cell.versions.slice(0,1);
  cell.versions[0].help_content=cached.help_content;
  cell.planner_memory=cached.planner_memory;
  cell.used.help_count=1;
  cell.jobs=[{kind:'cache',version:1,state:'settled',actual:{help_count:1},reservation:{help_count:1}}];
  const projectionSource=source.slice(source.indexOf('  function devV2State('),source.indexOf('  async function updateV2Snapshot('));
  const historySource=source.slice(source.indexOf('  function operatorHelpHistory('),source.indexOf('  function operatorRequestView('));
  const ctx=vm.createContext({
    launchOptions:{dev_v2:{}},v2MonitorIdentities:new Map(),devMemoryCatalogue:directory,
    devWorkspaceSnapshot:async session=>({dev_session:session,requests:[],responses:{},attempts:[],traces:{},station:{},run:{},resources:{help_count:{used:session.used.help_count,limit:session.budget.help_count}},eval:{native_outcomes:{}},response_evidence:{},dev_evidence_gaps:[]}),
    esc,operatorLast:()=>null,selectedMomentDetails:()=>'',operatorRequestView:()=>'',readableReply:()=>'',humanDuration:()=>'',
  });
  vm.runInContext(projectionSource+historySource,ctx);
  const session={session_id:'fixture',locked:{config:{eval_agent:{enabled:false}}},artifact_dir:cell.artifact_dir};
  const projected=await vm.runInContext('devV2WorkspaceSnapshot(session,cell,{state:"completed"})',vm.createContext({...ctx,session,cell}));
  assert.equal(projected.requests.length,0);
  assert.equal(projected.resources.help_count.used,0);
  const ledgerSource=source.slice(source.indexOf('  function attentionLedger('),source.indexOf('  function attentionTime('));
  const ledgerContext=vm.createContext({isTerminal:()=>false});
  vm.runInContext(ledgerSource,ledgerContext);
  ledgerContext.projected=projected;
  assert.equal(vm.runInContext('attentionLedger(projected).requests',ledgerContext),0);
  const history=vm.runInContext('operatorHelpHistory(projected)',vm.createContext({...ctx,projected}));
  assert.match(history,/Memory note used · v1/);
  assert.match(history,/c1-seeded-retrieve-disable-001/);
  assert.match(history,/seeded for tests, excluded from results/);
  assert.doesNotMatch(history,/Operator|op-who|&quot;authority&quot;|\[\{&quot;/);
  const memorySnapshot={...snapshot,memory:{...snapshot.memory,dev_v2:cached.planner_memory}};
  const h=harness();
  h.ctx.memorySnapshot=memorySnapshot;
  const panel=vm.runInContext('memoryPanel(memorySnapshot)',h.ctx);
  assert.match(panel,/data-dev-memory-jump/);
  for(const action of ['disable','enable','expiry','rollback'])assert.equal((panel.match(new RegExp(`data-dev-memory-action="${action}"`,'g'))||[]).length,1);
});

test('all actions submit actor and reason, refresh state and show lifecycle events',async()=>{
  const h=harness();
  for(const action of ['disable','enable','expiry','rollback']){
    if(action==='expiry')h.form.elements.expires_at.value='2099-01-01T12:00';
    await h.callbacks[action]();
  }
  assert.equal(h.sent.length,4);
  assert.deepEqual(h.sent.map(item=>item.url.split('/').at(-1)),['disable','enable','expiry','rollback']);
  for(const item of h.sent)assert.equal(item.body.actor,'alice');
  for(const item of h.sent)assert.equal(item.body.reason,'review');
  assert.equal(typeof h.sent[2].body.expires_at,'number');
  assert.ok(h.sent[2].body.expires_at>Date.now()/1000);
  assert.ok(h.confirmations[2].includes('Expiry and rollback cannot be undone'));
  assert.ok(h.confirmations[3].includes('Expiry and rollback cannot be undone'));
  assert.equal(h.reads.filter(url=>url==='/api/dev-memories').length,4);
  assert.match(h.html(),/rolled_back/);
  assert.match(h.html(),/data-dev-memory-history/);
  assert.match(h.html(),/&quot;actor&quot;: &quot;alice&quot;/);
});

test('missing operator authority disables buttons; blank fields and past expiry stop submission',async()=>{
  const noKey=harness({canOperate:false});
  assert.match(noKey.html(),/data-dev-memory-action="disable" disabled/);
  assert.match(noKey.html(),/Lifecycle controls disabled: Memory operator authority is not configured/);
  await noKey.callbacks.disable();
  assert.equal(noKey.sent.length,0);
  const h=harness();
  h.form.elements.actor.value='   ';await h.callbacks.disable();
  assert.match(h.html(),/role="alert">Actor and reason are required/);
  h.form.elements.actor.value='alice';h.form.elements.reason.value='  ';await h.callbacks.enable();
  assert.equal(h.sent.length,0);
  h.form.elements.reason.value='review';h.form.elements.expires_at.value='2020-01-01T00:00';await h.callbacks.expiry();
  assert.equal(h.sent.length,0);
  assert.match(h.html(),/role="alert">Expiry must be a future/);
});

test('service error is displayed verbatim in role=alert',async()=>{
  const h=harness({error:'StateConflict: note already rolled back'});
  await h.callbacks.enable();
  assert.match(h.html(),/role="alert">StateConflict: note already rolled back/);
});
