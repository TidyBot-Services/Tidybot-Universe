const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const live=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const monitor=require('../ui/memory.js');
function functions(start,end){const ctx=vm.createContext({});vm.runInContext(live.slice(live.indexOf('  function '+start),live.indexOf('  function '+end)),ctx);return ctx;}

test('Interactive main-page scope notice is independent of actual note use',()=>{
  const ctx=functions('unvalidatedMemoryTag','operatorOverview');
  const snapshot={dev_session:{mode:'interactive'},memory:{dev_v2:{scope:{level:'reference_only',reason:'no_scene_contract'},unvalidated_used:false}}};
  const html=ctx.memoryScopeNotice(snapshot);
  assert.match(html,/data-memory-reference-only/);
  assert.match(html,/Memory: reference only — this seed has no scene contract/);
  assert.equal(ctx.unvalidatedMemoryTag(snapshot),'');
  snapshot.memory.dev_v2.unvalidated_used=true;
  assert.match(ctx.unvalidatedMemoryTag(snapshot),/Used unvalidated memory/);
  snapshot.memory.dev_v2.scope.reason='scene_contract_missing';
  assert.match(ctx.memoryScopeNotice(snapshot),/Memory unavailable — this seed has no scene contract/);
  snapshot.memory.dev_v2.scope.level='scene';
  assert.equal(ctx.memoryScopeNotice(snapshot),'');
  snapshot.memory.dev_v2.scope={level:'unknown',reason:'manifest_unavailable'};
  assert.equal(ctx.memoryScopeNotice(snapshot),'');
  assert.equal(ctx.memoryScopeNotice({}),'');
  assert.match(live,/\$\{unvalidatedMemoryTag\(snapshot\)\}\$\{memoryScopeNotice\(snapshot\)\}/);
});

test('run dialog annotates only uncovered Interactive seeds when Memory is configured',()=>{
  const ctx=functions('runConfigSeedOptions','runConfigSummary');
  const options=(mode,memory)=>ctx.runConfigSeedOptions([102,104],[101,102,103],mode,memory);
  assert.equal(options('interactive',true)[0].title,'102');
  assert.equal(options('interactive',true)[1].title,'104 (no scene contract · Memory reference only)');
  assert.equal(options('interactive',true)[1].unregistered,false);
  assert.equal(options('interactive',false)[1].title,'104');
  for(const memory of [true,false]){
    assert.equal(options('experiment',memory)[1].title,'104 (no scene contract · interactive only)');
    assert.equal(options('experiment',memory)[1].unregistered,true);
  }
  assert.equal(ctx.runConfigSeedNote(104,[102],'interactive',true),'Memory: reference only — this seed has no scene contract.');
  assert.equal(ctx.runConfigSeedNote(102,[102],'interactive',true),'');
  assert.equal(ctx.runConfigSeedNote(104,[102],'interactive',false),'');
  assert.equal(ctx.runConfigSeedNote(104,[102],'experiment',true),'No scene contract for this seed. Choose Interactive or a registered seed.');
  assert.match(live,/runConfigSeedOptions\(seeds,registered,form.v2_mode.value,v2&&!!options\?\.dev_v2\?\.memory\)/);
  assert.match(live,/\[data-seed-note\].*runConfigSeedNote/);
});

test('Memory page retains scope and renders both reference-only reasons',()=>{
  for(const [reason,text] of [['no_scene_contract','Memory: reference only — this seed has no scene contract'],
                            ['scene_contract_missing','Memory unavailable — this seed has no scene contract']]){
    const data=monitor.adapt({run:{mode:'interactive',memory_scope:{level:'reference_only',reason}}});
    assert.equal(monitor.memoryScopeText(data.run),text);
  }
  assert.equal(monitor.memoryScopeText({memory_scope:{level:'scene'}}),'');
  assert.equal(monitor.memoryScopeText({memory_scope:{level:'unknown',reason:'manifest_unavailable'}}),'');
  assert.equal(monitor.memoryScopeText({}),'');
  const page=fs.readFileSync(path.join(__dirname,'../ui/memory.html'),'utf8');
  const script=fs.readFileSync(path.join(__dirname,'../ui/memory.js'),'utf8');
  assert.match(page,/id="memory-scope"[^>]*data-memory-reference-only[^>]*role="status" hidden/);
  assert.match(script,/scopeBanner.textContent=memoryScopeText\(data.run\)/);
  assert.match(script,/scopeBanner.hidden=!scopeBanner.textContent/);
});

test('Interactive Memory badge still counts distinct projected notes',()=>{
  const start=live.indexOf('  function updateMemoryLink(snapshot){'),end=live.indexOf('  if (!DEBUG_VIEW',start);
  const badge={hidden:true,textContent:''};
  const link={href:'',classList:{add(){},remove(){}},querySelector:()=>badge,offsetWidth:10};
  const ctx=vm.createContext({memoryLink:link,memoryCounts:new Map(),memoryPulseTimer:null,clearTimeout(){},setTimeout:()=>1,encodeURIComponent});
  vm.runInContext(live.slice(start,end),ctx);
  ctx.updateMemoryLink({dev_session:{mode:'interactive'},dev_v2:{experiment_id:'devexp-'+'a'.repeat(24)},
    memory:{dev_v2:{retrievals:[{entries:[{memory_id:'a'},{memory_id:'a'},{memory_id:'b'}]}]}}});
  assert.equal(badge.hidden,false);assert.equal(badge.textContent,2);
});
