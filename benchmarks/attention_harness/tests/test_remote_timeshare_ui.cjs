// Remote time-share on the page: open runs and the robot in the lobby, waiting for the robot in 02, the no-progress end.
// A lobby without robot.open (the 8769 demo lobby, an older gateway) must render as before.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const lobbySource=fs.readFileSync(path.join(__dirname,'../ui/lobby.js'),'utf8');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');

const card=(id,extra)=>({experiment_id:id,run_id:id,nickname:'Fox-'+id,mine:false,state:'running',attempt:{current:1,limit:3},queue_position:null,started_at:1,...extra});
const shared={demo:false,me:{nickname:'Fox-c'},clock:{speed:1},events:[],limits:{help_wait_seconds:120},recent:[],
  robot:{running:card('a',{robot:{state:'executing'}}),slots:3,waiting:1,
         open:[card('a',{robot:{state:'executing'}}),card('b',{robot:{state:'waiting',ahead:1}}),card('c',{mine:true,robot:{state:'thinking'}})]},
  queue:[card('d',{state:'queued',queue_position:1,robot:null})],counts:{running:3,queued:1,needs_you:0}};
const old={demo:false,me:null,clock:{speed:1},events:[],limits:{help_wait_seconds:120},recent:[],robot:{running:card('a')},queue:[card('d',{state:'queued',queue_position:1,eta_start_seconds:600})],counts:{}};

async function loadLobby(snap){
  const root={dataset:{remote:'1'},insertAdjacentHTML(){}};
  const document={getElementById:()=>root,documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],hidden:false};
  const window={document,localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({ok:true,json:async()=>snap}),setTimeout:()=>0,clearTimeout(){}};
  window.window=window;
  vm.runInNewContext(lobbySource,{window,Event:class{constructor(type){this.type=type;}},queueMicrotask,JSON,Map,Set,fetch:window.fetch,HumanReply:{}},{});
  window.Lobby.start({});
  for(let i=0;i<5&&!window.Lobby.enabled;i++)await new Promise(r=>setImmediate(r));
  return window.Lobby;
}

test('the lobby lists every open run and where it stands with the one robot',async()=>{
  const Lobby=await loadLobby(shared);
  const html=Lobby.pinnedHtml();
  assert.match(html,/One robot · 3 of 3 runs open/);
  assert.match(html,/On the robot<\/span><b>Fox-a/);
  assert.match(html,/Waiting for robot · 1 ahead<\/span><b>Fox-b/);
  assert.match(html,/Writing code<\/span><b>You/);
  assert.equal(Lobby.card('b').robot.state,'waiting');  // Open runs are cards too, so their pages find them.
});

test('a run in line waits for a slot, with no time estimate',async()=>{
  const Lobby=await loadLobby(shared);
  const html=Lobby.outcomeHtml({lobby:Lobby.card('d')});
  assert.match(html,/#1 in line for a run slot/);
  assert.match(html,/Up to 3 runs are open at once and take turns on the robot/);
  assert.doesNotMatch(html,/estimate/i);
  assert.match(Lobby.newRunHtml(),/3 of 3 runs open · 1 waiting for a slot.*there is no time limit/s);
});

test('a lobby without open runs renders as before',async()=>{
  const Lobby=await loadLobby(old);
  const html=Lobby.pinnedHtml();
  assert.match(html,/One robot · now &amp; next/);
  assert.doesNotMatch(html,/runs open/);
  assert.match(Lobby.outcomeHtml({lobby:Lobby.card('d')}),/The robot runs one task at a time/);
});

function extract(name,next){
  const start=live.indexOf(`function ${name}(`),end=live.indexOf(`function ${next}(`,start);
  assert.ok(start>0&&end>start,name);
  return live.slice(start,end);
}

test('a run that stopped for no progress says so, and never reads as a cancel',()=>{
  const devEndSummary=vm.runInNewContext(`(${extract('devEndSummary','mountPolicyController').trim()})`);
  const end=devEndSummary({reason:'no_progress',state:'failed'});
  assert.equal(end.key,'no_progress');
  assert.match(end.title,/Stopped automatically · No new Try, version or help request for 2 hours/);
  assert.match(end.explanation,/Nobody cancelled it/);
  assert.equal(devEndSummary({reason:'operator_cancel',state:'cancelled'}).title,'Stopped · Session closed');
});

test('02 says a Try is waiting for the robot and how many are ahead',()=>{
  assert.match(live,/robot_wait:\['○ WAITING FOR THE ROBOT',l\.robot_ahead>0\?`Waiting for the robot · \$\{l\.robot_ahead\} ahead`/);
  assert.match(live,/Waiting time does not count toward this run\./);
  assert.match(live,/\['model','advisor','approval','sdk','preparing','robot_wait'\]\.includes\(item\.lifecycle\.phase\)/);
});

test('the ended panel says a no-progress stop stopped itself, not a system problem or a cancel',()=>{
  const devEndSummary=vm.runInNewContext(`(${extract('devEndSummary','mountPolicyController').trim()})`);
  const operatorEndReason=vm.runInNewContext(`(()=>{const devEndSummary=${extract('devEndSummary','mountPolicyController').trim()};return ${extract('operatorEndReason','modelCallReview').trim()}})()`);
  const said=operatorEndReason({dev_session:{reason:'no_progress',state:'failed'},run:{status:'failed'},attempts:[]},10);
  assert.match(said,/stopped itself/);
  assert.doesNotMatch(said,/system problem \(not|someone stopped it/);
  assert.ok(devEndSummary);
});
