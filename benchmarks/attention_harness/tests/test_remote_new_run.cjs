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
// A Task select as live.js builds it: main's own options and groups; shapeTasks only prunes and greys.
function fakeForm(values,selected){
  const groups=[];const options=[];
  for(const [value,text,group] of values){
    const option={value,textContent:text,disabled:false,parentGroup:group,remove(){options.splice(options.indexOf(option),1);if(group){group.children=group.children.filter(o=>o!==option);}}};
    options.push(option);
    if(group){if(!groups.includes(group))groups.push(group);group.children.push(option);}
  }
  const events=[];const listeners={};
  const select={options,value:selected??options[0].value,
    get selectedOptions(){return [options.find(o=>o.value===select.value)||options[0]];},
    querySelectorAll:()=>groups.map(group=>({remove(){groups.splice(groups.indexOf(group),1);},get children(){return group.children;}})),
    addEventListener:(type,fn)=>{(listeners[type]??=[]).push(fn);},dispatchEvent:event=>{events.push(event.type);(listeners[event.type]||[]).forEach(fn=>fn(event));}};
  const seed={disabled:false,value:'',innerHTML:''};const note={textContent:''};
  const form={task_id:select,seed,querySelector:()=>note};
  return {form,select,seed,note,events,groups};
}
const grp=label=>({label,children:[]});
const MAIN_OPTIONS=()=>{const presets=grp('Presets'),a=grp('A'),d=grp('D');return [
  ['pick_place_counter_to_sink','Pick Place: Counter → Sink'],['open_cabinet','Open the cabinet'],
  ['turn_on_microwave@r5','turn on microwave',presets],['turn_off_stove@r5','turn off stove',presets],
  ['open_cabinet@r5','Open the cabinet',a],['pick_place_counter_to_cabinet@r5','pick place counter to cabinet',d],['weigh_ingredients@r5','weigh ingredients',d]];};

test('Remote offers the same three tasks as main; only the ones with a demo recording can be chosen',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink','turn_on_microwave']);
  const f=fakeForm(MAIN_OPTIONS());Lobby.shapeTasks(f.form);
  assert.deepEqual(f.select.options.map(o=>o.value),['pick_place_counter_to_sink','turn_on_microwave@r5','pick_place_counter_to_cabinet@r5']);
  assert.deepEqual(f.select.options.map(o=>o.disabled),[false,false,true]);
  assert.match(f.select.options[2].textContent,/^pick place counter to cabinet · no demo recording yet$/);
  assert.equal(f.select.options[1].textContent,'turn on microwave');          // main's own label
  assert.equal(f.groups.length,2);                                              // emptied groups are gone
});
test('with a recording for every task nothing is greyed; the one recorded task without a preset gets a locked seed 102',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink','turn_on_microwave','pick_place_counter_to_cabinet']);
  const f=fakeForm(MAIN_OPTIONS());Lobby.shapeTasks(f.form);
  assert.deepEqual(f.select.options.map(o=>o.disabled),[false,false,false]);
  assert.equal(f.seed.disabled,true);assert.equal(f.seed.value,'102');assert.match(f.seed.innerHTML,/Seed 102/);
  // A preset keeps main's own lock (live.js disabled it); the Remote rule does not touch it.
  const g=fakeForm(MAIN_OPTIONS(),'turn_on_microwave@r5');g.seed.disabled=true;g.seed.value='101';g.seed.innerHTML='<option value="101">Seed 101</option>';
  Lobby.shapeTasks(g.form);g.select.dispatchEvent({type:'change'});await new Promise(r=>queueMicrotask(r));
  assert.equal(g.seed.value,'101');
});
test('a greyed task is never the selection',async()=>{
  const Lobby=await loadLobby(['turn_on_microwave']);
  const f=fakeForm(MAIN_OPTIONS());Lobby.shapeTasks(f.form);
  assert.equal(f.select.options[0].disabled,true);assert.equal(f.select.value,'turn_on_microwave@r5');assert.ok(f.events.includes('change'));
});
test('outside Remote (lobby off) the Task list is left alone',()=>{
  const root={dataset:{}},document={getElementById:()=>root,documentElement:{dataset:{}},querySelector:()=>null,querySelectorAll:()=>[],hidden:false};
  const window={document,localStorage:{getItem:()=>null,setItem(){}},fetch:async()=>({ok:false}),setTimeout:()=>0};window.window=window;
  vm.runInNewContext(lobbySource,{window,Event:class{},queueMicrotask,JSON,Map,Set},{});
  const f=fakeForm(MAIN_OPTIONS());window.Lobby.shapeTasks(f.form);
  assert.equal(f.select.options.length,7);
});
test('Sharing & queue is a numbered section with the same switch as 04, and live.js keeps its text and Remote wiring apart',async()=>{
  const Lobby=await loadLobby(['pick_place_counter_to_sink']);
  const html=Lobby.newRunHtml();
  assert.match(html,/<h4><span class="op-number">02<\/span>Sharing &amp; queue<\/h4>/);
  assert.match(html,/<label class="ab-reviewer-toggle"><input name="lb_share" type="checkbox"><span class="ab-switch"/);
  assert.doesNotMatch(html,/ab-check/);
  // Nothing may write into the first switch of the form by class: in Remote that is the Sharing switch.
  assert.doesNotMatch(live,/form\.querySelector\('\.ab-reviewer-toggle small'\)/);
  assert.match(live,/window\.Lobby\.shapeTasks\(form\);/);
  assert.match(live,/\/\^\(Benchmark\|Session type\|Robot\|Name suffix\)\/i/); // Scene seed stays visible in Remote
});
