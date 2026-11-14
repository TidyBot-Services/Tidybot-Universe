const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const bind=source.slice(source.indexOf('  function bindAgentReply('),source.indexOf('  function bindDevGuidance('));
function setup(fail=false){
 const handlers={},stored=new Map(),posts=[],message={textContent:''},buttons=[{disabled:false}];
 const input={value:'Lift slowly.',style:{},scrollHeight:20,addEventListener(){}};
 const assisted={checked:true,addEventListener(){}};
 const form={dataset:{executionId:'e000001',cell:'cell'},elements:{content:input,assisted},reportValidity:()=>true,
  addEventListener:(kind,fn)=>handlers[kind]=fn,querySelectorAll:()=>buttons,
  querySelector:selector=>selector==='[data-agent-reply-message]'?message:{addEventListener:(kind,fn)=>handlers.skip=fn}};
 const context={view:{querySelector:()=>form},controlFetch:async(url,options)=>{posts.push({url,body:JSON.parse(options.body)});if(fail)throw Error('Request closed');return {ok:true,json:async()=>({status:'received'})}},
  stored:(key,value)=>value===undefined?stored.get(key):value===null?stored.delete(key):stored.set(key,value),
  DEBUG_VIEW:false,plainError:x=>x,refresh:async()=>{},lastRendered:'old',updateAgentClock(){}};
 vm.runInNewContext(bind+'\nbindAgentReply({dev_v2:{experiment_id:"exp"}})',context);
 return {handlers,posts,message,buttons};
}
test('reply checkbox is transmitted as a boolean without browser-chosen identity',async()=>{
 const s=setup();s.handlers.submit({preventDefault(){}});await new Promise(setImmediate);
 assert.deepEqual(s.posts[0],{url:'/api/dev-v2/experiments/exp/help-reply',body:{cell:'cell',execution_id:'e000001',content:'Lift slowly.',assisted:true}});
});
test('reply failures remain visible and the user can retry',async()=>{
 const s=setup(true);s.handlers.submit({preventDefault(){}});await new Promise(setImmediate);
 assert.equal(s.message.textContent,'Reply not submitted: Request closed');assert.equal(s.buttons[0].disabled,false);
});
