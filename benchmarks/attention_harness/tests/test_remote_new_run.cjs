const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const lobbySource=fs.readFileSync(path.join(__dirname,'../ui/lobby.js'),'utf8');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');

const snapshot=tasks=>({demo:true,demo_info:{recordings:7,history_limit:8,tasks},clock:{speed:20},me:{nickname:'Lynx-1'},robot:{state:'idle',running:null},queue:[],recent:[],counts:{},limits:{help_wait_seconds:300},events:[]});
async function loadLobby(tasks){
  const root={dataset:{remote:'1'},insertAdjacentHTML(){}};
  const document={getElementById:()=>root,documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],hidden:false};
  const window={document,localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({ok:true,json:async()=>snapshot(tasks)}),setTimeout:()=>0,clearTimeout(){}};
  window.window=window;
  vm.runInNewContext(lobbySource,{window,Event:class{constructor(type){this.type=type;}},queueMicrotask,JSON,Map,Set,fetch:window.fetch,HumanReply:{}} ,{});
  window.Lobby.start({});
  for(let i=0;i<5&&!window.Lobby.enabled;i++)await new Promise(r=>setImmediate(r));
  return window.Lobby;
}
test('outside Remote the lobby hooks change nothing',()=>{
  const root={dataset:{}},document={getElementById:()=>root,documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],hidden:false};
  const window={document,localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({ok:false}),setTimeout:()=>0};window.window=window;
  vm.runInNewContext(lobbySource,{window,Event:class{},queueMicrotask,JSON,Map,Set},{});
  assert.equal(window.Lobby.newRunLimits(),null);assert.equal(window.Lobby.newRunNotice(),'');assert.equal(window.Lobby.newRunHtml(),'');
  assert.doesNotThrow(()=>{window.Lobby.shapeNewRun(null);window.Lobby.syncJoin(null,null);});
});
test('the demo join needs a recording of the task; the task itself stays selectable',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink']);
  const join={textContent:'Start run',disabled:false},form={querySelector:()=>join};
  Lobby.syncJoin(form,{task_id:'pick_place_counter_to_sink'});
  assert.equal(join.textContent,'Join demo');assert.equal(join.disabled,false);
  Lobby.syncJoin(form,{task_id:'turn_on_microwave'});
  assert.equal(join.textContent,'No demo yet');assert.equal(join.disabled,true);
});
test('the source shown for a demo run follows who pays',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink']);
  const lab={roles:{dev:{credential:'lab',provider:'parcc'},eval:{credential:'lab',provider:'parcc'}}};
  assert.equal(Lobby.devSource(lab,'parcc'),'lab_glm');
  assert.equal(Lobby.devSource(null,'parcc'),'lab_glm');
  assert.equal(Lobby.devSource({roles:{dev:{credential:'lab',provider:'parcc'},eval:{credential:'mine'}}},'parcc'),'own_key');
});
test('Queue comes after main\'s six sections, with no sharing switch, and live.js hides nothing',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink']);
  const html=Lobby.newRunHtml();
  assert.match(html,/<h4><span class="op-number">07<\/span>Queue<\/h4>/);
  assert.doesNotMatch(html,/lb_share/);
  assert.doesNotMatch(html,/ab-check/);
  // Nothing may write into the first switch of the form by class: in Remote that is the Sharing switch.
  assert.doesNotMatch(live,/form\.querySelector\('\.ab-reviewer-toggle small'\)/);
  assert.match(live,/window\.Lobby\.shapeNewRun\(form\);/);
  assert.doesNotMatch(live,/ab-lobby-hide/);
});

test('on Remote only the owner is asked, and a run in line can be left',()=>{
  assert.match(live,/const wait=root\.dataset\.remote==='1'&&snapshot\.lobby\?\.mine!==true\?null:agent\.human_wait/);
  assert.match(live,/snapshot\.lobby\?\.mine && snapshot\.lobby\.state === 'queued' \? true/);
});
test('a real run keeps its own help panel: the lobby binds nothing there',async()=>{
  const Lobby=await loadLobby(['x']);
  let bound=0;
  Lobby.bindHelp({querySelector:()=>{bound++;return null;}},{lobby:{real:true,state:'waiting_human'}},()=>{});
  assert.equal(bound,0);
});
test('a run that could not start says so in 02',async()=>{
  const Lobby=await loadLobby(['x']);
  const html=Lobby.outcomeHtml({lobby:{real:true,state:'created',mine:true,start_error:'no <GPU>'}});
  assert.match(html,/data-operator-outcome="start-failed"/);
  assert.match(html,/no &lt;GPU&gt;/);
});

// 2026-11-07, U2 in the real crowd test: a run made a moment ago had no lobby card until the next poll (1.5 s, 5 s in a
// background tab), so 02 fell back to the harness's "prepared" lifecycle (can_stop false) and Stop was gray.
test('a run in line can be stopped the moment it is made: the page asks the lobby before it draws',async()=>{
  const id='devexp-'+'a'.repeat(24);
  let made=false;
  const queued={experiment_id:id,mine:true,real:true,state:'queued',queue_position:1};
  const root={dataset:{remote:'1'},insertAdjacentHTML(){}};
  const document={getElementById:()=>root,documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],hidden:false};
  const lobby=()=>({...snapshot(['x']),demo:false,robot:{state:'busy',running:{experiment_id:'devexp-'+'b'.repeat(24),state:'running'}},queue:made?[queued]:[]});
  const window={document,localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({ok:true,json:async()=>lobby()}),setTimeout:()=>0,clearTimeout(){}};
  window.window=window;
  vm.runInNewContext(lobbySource,{window,Event:class{},queueMicrotask,JSON,Map,Set,Promise,fetch:window.fetch,HumanReply:{}},{});
  window.Lobby.start({});
  for(let i=0;i<5&&!window.Lobby.enabled;i++)await new Promise(r=>setImmediate(r));
  assert.equal(window.Lobby.enabled,true);
  made=true;  // The host made the run; the next poll is still a while off.
  assert.equal(window.Lobby.card(id),null);
  assert.equal(typeof window.Lobby.refresh,'function','the lobby can be asked again at once');
  await window.Lobby.refresh();
  assert.equal(window.Lobby.card(id).state,'queued');
  // The page's own create path asks the lobby before its first draw of the new run.
  const create=live.slice(live.indexOf("controlFetch('/api/dev-v2/experiments'"));
  const asked=create.indexOf('await Lobby.refresh()'),shown=create.indexOf('selectedDev = '),drawn=create.indexOf('await refresh();');
  assert.ok(asked>0&&asked<shown&&shown<drawn,'live.js awaits Lobby.refresh() before it selects and draws the new run');
});
