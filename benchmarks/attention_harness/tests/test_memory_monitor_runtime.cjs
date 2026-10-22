const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const original=fs.readFileSync(path.join(__dirname,'../ui/memory.js'),'utf8');
const monitor=require('../ui/memory.js');

function element(tag='div'){
  const handlers={},children=[],classes=new Set();
  const el={tagName:tag,children,handlers,dataset:{},style:{},attributes:{},hidden:false,clientWidth:780,clientHeight:500,offsetWidth:100,textContent:'',innerHTML:'',
    className:'',classList:{add(x){classes.add(x)},remove(x){classes.delete(x)},toggle(x,on){if(on===undefined? !classes.has(x):on)classes.add(x);else classes.delete(x)},contains(x){return classes.has(x)}},
    append(child){children.push(child);child.parent=el;return child},appendChild(child){return el.append(child)},removeChild(child){const i=children.indexOf(child);if(i>=0)children.splice(i,1)},remove(){this.parent?.removeChild(this)},replaceChildren(){children.splice(0)},
    addEventListener(name,fn){handlers[name]=fn},setAttribute(k,v){this.attributes[k]=v},getBoundingClientRect(){return {left:0,top:0}},setPointerCapture(){},
    querySelectorAll(sel){if(sel==='button')return children.filter(x=>x.tagName==='button');if(sel==='.n')return children.filter(x=>x.className.startsWith('n '));return []},
    querySelector(sel){if(sel==='[data-view="3d"]')return children.find(x=>x.dataset.view==='3d');return null},
    closest(){return this}};
  return el;
}
function fakeThree(){
  const meshes=[],cameras=[];
  class Vector3{
    constructor(x=0,y=0,z=0){this.x=x;this.y=y;this.z=z}
    clone(){return new Vector3(this.x,this.y,this.z)}
    copy(v){this.x=v.x;this.y=v.y;this.z=v.z;return this}
    multiplyScalar(n){this.x*=n;this.y*=n;this.z*=n;return this}
    applyMatrix4(){return this}
    project(){return this}
    normalize(){return this}
  }
  class Quaternion{setFromEuler(){return this}setFromUnitVectors(){return this}setFromAxisAngle(){return this}slerp(){return this}premultiply(){return this}copy(){return this}}
  class Group{constructor(){this.children=[];this.rotation={x:0,z:0};this.matrix={};this.matrixWorld={};this.quaternion=new Quaternion();this.position=new Vector3();this.scale={setScalar(){}};this.userData={}}
    add(x){this.children.push(x)}remove(x){this.children.splice(this.children.indexOf(x),1)}updateMatrixWorld(){}updateMatrix(){}}
  class Geometry{setFromPoints(){return this}rotateX(){return this}dispose(){}}
  class Color{constructor(v){this.value=v}set(v){this.value=v;return this}setHex(v){this.value=v;return this}getHex(){return typeof this.value==='number'?this.value:parseInt(String(this.value).replace('#',''),16)}}
  class Material{constructor(options={}){Object.assign(this,options);this.color=new Color(options.color||0)}dispose(){}}
  class Mesh extends Group{constructor(geometry,material){super();this.geometry=geometry;this.material=material;meshes.push(this)}}
  class Renderer{constructor(){this.domElement=element('canvas');this.domElement.width=0;this.domElement.height=0}setPixelRatio(){}getPixelRatio(){return 1}setSize(w,h){this.domElement.width=w;this.domElement.height=h}render(){}}
  class Camera extends Group{constructor(){super();cameras.push(this)}updateProjectionMatrix(){}}
  return {api:{Vector3,Quaternion,Euler:class{},Group,Scene:Group,PerspectiveCamera:Camera,WebGLRenderer:Renderer,Mesh,SphereGeometry:Geometry,TorusGeometry:Geometry,BufferGeometry:Geometry,MeshBasicMaterial:Material,LineBasicMaterial:Material,Line:Mesh},meshes,cameras};
}
function harness(hash,source=original,width=780){
  const ids=new Map(),get=id=>{if(!ids.has(id))ids.set(id,element());return ids.get(id)};
  const themes=get('themes'),views=get('views');
  for(const theme of ['light','dark']){const b=element('button');b.dataset.theme=theme;themes.append(b)}
  for(const view of ['3d','2d']){const b=element('button');b.dataset.view=view;views.append(b)}
  get('flow').classList.add('on');
  const frames=[],intervals=[],calls=[],three=fakeThree();
  const document={body:element('body'),documentElement:{clientWidth:width},getElementById:get,createElement:element,createElementNS:(_,tag)=>element(tag),addEventListener(){}};
  const handlers={};
  const window={document,innerWidth:width,THREE:three.api,location:{hash,reload(){}},addEventListener(name,fn){handlers[name]=fn},confirm:()=>true,
    AttentionNotices:{createNotices:()=>({clientError(){}}),installFetchCapture(){}}};
  const context=vm.createContext({window,document,location:window.location,matchMedia:()=>({matches:false}),localStorage:{getItem:()=>null,setItem(){}},devicePixelRatio:1,
    requestAnimationFrame:fn=>{frames.push(fn)},setInterval:fn=>{intervals.push(fn);return intervals.length},setTimeout:()=>1,clearTimeout(){},
    fetch:(url)=>{calls.push(url);return Promise.resolve({ok:true,json:async()=>window.payload})},console});
  const instrumented=source.replace('  init();','  global.__memoryTest={init,createGlobe,render,renderFlow,sampleTick,applyTheme,setView,select,recordReads,startGlobe(){globe=createGlobe();globe.applyTheme3D();setView("3d");return globe},load(payload){data=adapt(payload);render();feed()},get data(){return data}};');
  assert.notEqual(instrumented,source);
  vm.runInContext(instrumented,context,{filename:'memory.js'});
  return {api:window.__memoryTest,window,get,handlers,frames,intervals,calls,meshes:three.meshes,cameras:three.cameras,flush(){const fn=frames.shift();assert.ok(fn,'an animation frame was requested');fn()}};
}
test('3D camera fits sphere and labels with margin and responds to aspect changes',()=>{
  for(const [w,h] of [[1080,780],[390,460],[1600,1000]]){
    const distance=monitor.cameraFitDistance(w,h),halfVertical=42*Math.PI/360;
    const halfHorizontal=Math.atan(Math.tan(halfVertical)*w/h);
    assert.ok(1.28/distance/Math.sin(Math.min(halfVertical,halfHorizontal))<=.94+1e-10);
  }
  const h=harness('#demo=150');h.get('globe').clientWidth=1080;h.get('globe').clientHeight=780;
  h.api.startGlobe();h.flush();const camera=h.cameras[0];
  assert.ok(camera.position.z>2.7);
  const before=camera.position.z;
  h.get('globe').clientWidth=390;h.flush();
  assert.ok(camera.position.z>before);
  assert.equal(camera.aspect,390/780);
});
function realPayload(){
  const seed='c2-seeded-small-steps-001';
  const notes=[
    {id:seed,title:'Take small, retried arm steps',summary:'Use bounded retries.',group:'Motion',status:'active',effect:'not_tested',test_seeded:true,reads:5},
    {id:'n2',title:'Keep the wrist aligned',group:'Grasp',status:'active',effect:'helps',reads:4},
    {id:'n3',title:'Lift before turning',group:'Motion',status:'active',effect:'helps',reads:3},
    {id:'n4',title:'Avoid the edge',group:'Safety',status:'active',effect:'not_helps',reads:2},
    {id:'n5',title:'Pause before gripping',group:'Grasp',status:'active',effect:'not_tested',reads:1},
    {id:'n6',title:'Disabled note',group:'Safety',status:'off',effect:'not_tested',reads:0}
  ];
  return {run:{state:'running',versions:[1,2,3,4,5].map(n=>({n,state:n===5?'running':'completed',note_ids:n===1?[seed,'n2']:[seed]}))},notes,events:[]};
}
test('feed labels skipped results by reason with a legacy fallback',()=>{
  const cases=[
    ['test_seeded_excluded_from_results','test note excluded'],
    ['not_trusted_here_excluded_from_results','not counted in results'],
    ['reference_only_no_scene_contract','reference only · not counted'],
    ['unknown_reason','test note excluded'],
    [undefined,'test note excluded'],
  ];
  const h=harness('#exp=devexp-'+'a'.repeat(24));
  const payload=realPayload();
  payload.events=cases.map(([reason],i)=>({seq:i+1,version:i+1,type:'planner_memory_result_skipped',note_ids:[],...(reason===undefined?{}:{reason})}));
  h.api.load(payload);
  const labels=[...h.get('feed').innerHTML.matchAll(/<div[^>]*>([^<]*)<\/div>/g)].map(match=>match[1]);
  assert.deepEqual(labels,cases.map(([,label],i)=>`v${i+1} ${label}`).reverse());
});
function exercise(hash,payload){
  const h=harness(hash);h.api.startGlobe();h.api.load(payload);h.flush();
  assert.equal(h.meshes.filter(m=>m.userData.id).length,payload.notes.length,'every note has a 3D ball');
  const mesh=h.meshes.find(m=>m.userData.id===payload.notes[0].id);
  const canvas=h.get('globe').children[0],p=mesh.position;
  canvas.handlers.pointermove({clientX:(p.x*.5+.5)*780,clientY:(-p.y*.5+.5)*500});
  canvas.handlers.pointerdown({clientX:0,clientY:0,pointerId:1});
  canvas.handlers.pointerup({});
  assert.match(h.get('card').innerHTML,/Take small|Slow|Retry|Align|Lift|Open|Check|Back off|Tilt|Pause|Re-grip/);
  h.get('filters').onclick({target:{dataset:{version:'1'},closest(){return this}}});
  assert.match(h.get('filters').innerHTML,/data-version="1"/);
  h.api.setView('2d');
  const flow=h.get('flow').innerHTML;
  assert.match(flow,/flow-svg/);assert.match(flow,/flow-connection/);assert.match(flow,/Memory service/);
  assert.equal((flow.match(/class="flow-connection/g)||[]).length,h.api.data.groups.length+5);
  assert.equal((flow.match(/<ellipse /g)||[]).length,2);
  assert.match(flow,/flow-pill-svg/);assert.match(flow,/ C[0-9]+,/);
  assert.match(flow,/Big bright balls were used/);
  h.api.applyTheme('dark');assert.match(h.get('flow').innerHTML,/stop-color="#ffd23f"/);
  h.api.recordReads([{type:'planner_memory_retrieved',version:1,note_ids:[payload.notes[0].id]}]);
  h.api.render();
  assert.match(h.get('flow').innerHTML,/<animateMotion/);
  assert.match(h.get('flow').innerHTML,/flow-connection hot/);
  h.api.setView('3d');h.flush();
  return h;
}
test('fake THREE executes real shaped notes, click, filter, view, theme and new read',()=>{
  const h=exercise('#exp=devexp-'+'a'.repeat(24),realPayload());
  assert.equal(h.get('used-count').textContent,2);
  assert.equal(h.get('error').textContent,'');
});
test('failed and pending versions keep truthful 2D text and ring colors in both views',()=>{
  const h=harness('#exp=devexp-'+'a'.repeat(24));h.api.startGlobe();
  const payload=realPayload();
  payload.run.versions[0].state='failed';
  payload.run.versions[1].state='passed';
  payload.run.versions[2].state='finished';
  h.api.load(payload);
  assert.equal(h.meshes[2].material.color.value,'hsl(195, 70%, 44%)');
  assert.notEqual(h.meshes[2].material.color.value,h.meshes[3].material.color.value);
  h.api.setView('2d');
  const cards=h.get('flow').innerHTML;
  assert.match(cards,/class="version-ring state-failed"[^>]*stroke="#d94545"/);
  assert.match(cards,/Dev v1<\/text><text[^>]*>Failed<\/text>/);
  assert.match(cards,/class="version-ring state-passed"[^>]*stroke="#1f9d57"/);
  assert.match(cards,/class="version-ring state-finished"[^>]*stroke="#4f7be0"/);
  assert.doesNotMatch(cards,/Dev v1<\/text><text[^>]*>Passed<\/text>/);
  h.api.applyTheme('dark');
  assert.equal(h.meshes[2].material.color.value,'hsl(195, 84%, 68%)');
  assert.match(h.get('flow').innerHTML,/class="version-ring state-failed"[^>]*stroke="#ff5d5d"/);
});
test('fake THREE executes 150 note demo and advances all five versions into a new run',()=>{
  const h=exercise('#demo=150',{...realPayload(),notes:realPayload().notes});
  const demo=harness('#demo=150');
  demo.api.startGlobe();
  demo.api.init();
  assert.equal(demo.calls.length,0,'demo never fetches');
  assert.equal(demo.api.data.notes.length,150);
  assert.ok(Number(demo.get('used-count').textContent)>0,'first read is immediate');
  assert.match(demo.get('feed').innerHTML,/used/);
  for(let i=0;i<4;i++)demo.intervals[0]();
  assert.ok(demo.api.data.versions.every(v=>v.note_ids.length>=1),'v1 through v5 read notes');
  assert.ok(demo.api.data.versions.some(v=>v.state==='passed'||v.state==='failed'));
  for(let i=0;i<5;i++)demo.intervals[0]();
  assert.equal(demo.api.data.versions[0].state,'reading','new run starts after v5');
  assert.ok(Number(demo.get('used-count').textContent)>0);
  assert.equal(h.calls.length,0);
});
test('3 and 8 version runs render matching rings, cards, chips and responsive flow',()=>{
  assert.equal(monitor.flowLayout(699),'vertical');
  assert.equal(monitor.flowLayout(700),'wide');
  for(const count of [3,8]){
    const radii=monitor.orbitRadii(count);
    assert.equal(radii[0],.34);
    assert.ok(Math.abs(radii.at(-1)-.86)<1e-12);
    const payload=realPayload();
    payload.run.max_versions=count;
    payload.run.versions=Array.from({length:count},(_,i)=>({n:i+1,state:i?'waiting':'passed',note_ids:i===0?['n2']:[]}));
    for(const width of [780,390]){
      const h=harness('#exp=devexp-'+'a'.repeat(24),original,width);
      h.get('globe').clientWidth=width;
      h.api.startGlobe();h.api.load(payload);h.flush();
      assert.equal(h.get('labels').children.filter(x=>x.className==='rl').length,count);
      assert.equal(new Set(h.get('labels').children.filter(x=>x.className==='rl').map(x=>x.style.color)).size,count);
      assert.equal((h.get('filters').innerHTML.match(/data-version=/g)||[]).length,count+1);
      h.api.setView('2d');
      const flow=h.get('flow').innerHTML;
      assert.equal((flow.match(/class="version-ring/g)||[]).length,count);
      assert.equal((flow.match(/class="flow-connection/g)||[]).length,h.api.data.groups.length+count);
      assert.match(flow,width<700?/viewBox="0 0 390 900"/:/viewBox="0 0 780 456"/);
      assert.match(flow,/1 used/);
      h.window.innerWidth=width<700?780:390;h.handlers.resize();
      assert.match(h.get('flow').innerHTML,width<700?/viewBox="0 0 780 456"/:/viewBox="0 0 390 900"/);
    }
  }
});
test('phone 3D shows four latest callouts and card height is capped',()=>{
  const h=harness('#exp=devexp-'+'a'.repeat(24),original,390);
  h.get('globe').clientWidth=390;
  const payload=realPayload();
  payload.notes=Array.from({length:9},(_,i)=>({id:'n'+i,title:'Note '+i,group:'Motion',status:'active',effect:'helps',reads:1,last_used_at:i+1}));
  payload.run.versions[0].note_ids=payload.notes.map(n=>n.id);
  h.api.startGlobe();h.api.load(payload);h.flush();
  const labels=h.get('labels').children.filter(x=>x.className.startsWith('n '));
  assert.equal(labels.length,4);
  assert.ok(labels.some(x=>x.textContent.includes('Note 8')));
  assert.ok(!labels.some(x=>x.textContent.includes('Note 0')));
  const css=fs.readFileSync(path.join(__dirname,'../ui/memory.css'),'utf8');
  assert.match(css,/@media\(max-width:699px\)/);
  assert.match(css,/#monitor \.flow-svg\{min-width:0;width:100%;height:auto\}/);
  assert.match(css,/#monitor \.card\{max-height:45%\}/);
});
test('demo n selects 3 or 8 versions and wraps after that budget',()=>{
  for(const count of [3,8]){
    const h=harness(`#demo=150&n=${count}`);
    h.api.init();
    assert.equal(h.api.data.run.max_versions,count);
    assert.equal(h.api.data.versions.length,count);
    for(let i=0;i<count+4;i++)h.intervals[0]();
    assert.equal(h.api.data.versions.length,count);
    assert.equal(h.api.data.versions[0].state,'reading');
    assert.equal(h.calls.length,0);
  }
  for(const [n,expected] of [[0,1],[99,12]]){
    const h=harness(`#demo=40&n=${n}`);h.api.init();
    assert.equal(h.api.data.versions.length,expected);
  }
});
test('runtime harness detects the original color shadowing regression',()=>{
  const broken=original.replace('const ballColor=n.status===\'active\'?color(n.effect):color(\'off\');const ball=',"const color=n.status==='active'?color(n.effect):color('off');const ball=").replace('color:ballColor','color');
  assert.notEqual(broken,original);
  const h=harness('#demo=150',broken);h.api.startGlobe();
  assert.throws(()=>h.api.load(realPayload()),/Cannot access 'color' before initialization/);
});

test('reference-only banner appears before any reads and clears for scene scope',()=>{
  const h=harness('#exp=devexp-'+'a'.repeat(24));
  const payload={run:{versions:[],memory_scope:{level:'reference_only',reason:'no_scene_contract'}},notes:[],events:[]};
  h.api.load(payload);
  assert.equal(h.get('memory-scope').hidden,false);
  assert.equal(h.get('memory-scope').textContent,'Memory: reference only — this seed has no scene contract');
  payload.run.memory_scope.reason='scene_contract_missing';h.api.load(payload);
  assert.equal(h.get('memory-scope').textContent,'Memory unavailable — this seed has no scene contract');
  payload.run.memory_scope={level:'scene'};h.api.load(payload);
  assert.equal(h.get('memory-scope').hidden,true);
});

test('2D service counts exclude local run notes in both themes and layouts',()=>{
 for(const width of [780,390]){
   const h=harness('#exp=devexp-'+'a'.repeat(24),original,width);
   const payload=realPayload();
   payload.run_notes=Array.from({length:3},(_,i)=>({id:'r-'+i,cell:'cell',title:'Run note '+i,help_version:2,uses:0,off:false}));
   h.api.load(payload);h.api.setView('2d');
   for(const theme of ['light','dark']){
     h.api.applyTheme(theme);
     const flow=h.get('flow').innerHTML;
     assert.match(flow,/Memory service<\/text><text[^>]*>5 notes on<\/text>/);
     assert.match(flow,/<text[^>]*>\+ 3 run notes \(this run, local\)<\/text>/);
     assert.equal((flow.match(/class="flow-connection/g)||[]).length,h.api.data.groups.length+payload.run.versions.length);
     if(width>=700){
       const legend=flow.match(/<g class="flow-legend"[^>]*>(.*?)<\/g>/)[1];
       assert.match(legend,new RegExp(`<circle[^>]*fill="${monitor.themes[theme].efc.r}"/><text[^>]*>This run</text>`));
       for(const label of ['Helps','Does not help','Not tested','Unvalidated'])assert.ok(legend.includes('>'+label+'</text>'));
     }
   }
   payload.run_notes[0].off=true;payload.run_notes[1].off=true;
   h.api.load(payload);
   assert.match(h.get('flow').innerHTML,/>5 notes on<\/text>/);
   assert.match(h.get('flow').innerHTML,/>\+ 1 run note \(this run, local\)<\/text>/);
   payload.notes=[];h.api.load(payload);
   assert.match(h.get('flow').innerHTML,/>0 notes on<\/text>/);
   payload.run_notes=[];h.api.load(payload);
   assert.doesNotMatch(h.get('flow').innerHTML,/\(this run, local\)/);
 }
});

test('run-note balls, counts and local controls render in both themes without Memory authority',async()=>{
 const h=harness('#exp=devexp-'+'a'.repeat(24));
 const payload={run:{can_operate_run_notes:true,versions:[{n:1,note_ids:[]},{n:2,note_ids:[]},{n:3,note_ids:['r-one']} ]},notes:[],run_notes:[{id:'r-one',cell:'cell',title:'Keep the grasp',help_version:2,uses:1,failure_gone:1,off:false}],events:[{type:'planner_memory_run_note_created',text:'v2 learned: Keep the grasp',note_ids:['r-one']}]};
 h.api.load(payload);h.api.startGlobe();
 for(const theme of ['light','dark']){
   h.api.applyTheme(theme);h.api.setView('2d');
   assert.match(h.get('flow').innerHTML,/ball-run-note/);
   assert.match(h.get('flow').innerHTML,new RegExp(monitor.themes[theme].efc.r));
   assert.equal(h.get('count-run-note').textContent,1);
   assert.match(h.get('feed').innerHTML,/v2 learned: Keep the grasp/);
   h.api.select(h.api.data.byId.get('r-one'));
   assert.match(h.get('card').innerHTML,/Turn off for this run/);
   assert.ok(h.meshes.some(m=>m.userData.id==='r-one'&&m.material.color.value===monitor.themes[theme].efc.r));
 }
 h.window.payload={...payload,run_notes:[{...payload.run_notes[0],off:true}]};
 const button={disabled:false};
 await h.get('card').onclick({target:{closest:s=>s==='[data-run-note-off]'?button:null}});
 assert.ok(h.calls.some(url=>url.endsWith('/run-note-off')));
 assert.doesNotMatch(h.get('card').innerHTML,/Turn off for this run/);
});
