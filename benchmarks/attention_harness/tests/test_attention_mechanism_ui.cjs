// Renderer contracts; real-browser acceptance requires separate screenshots.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const block=source.slice(source.indexOf('  // Attention mechanism presentation:'),source.indexOf('  // End Attention mechanism presentation.'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function api(){return vm.runInNewContext(block+'\n({requestAllowed,requestMechanismDetails,attentionRunContext,resourceContext})',{esc,label:String,number:String,dateTime:String,requestType:r=>r.request_type});}
test('request controls derive from server actions; disposition does not rename state',()=>{
 const request={request_id:'r',request_type:'hint',state:'answered',priority:'high',explanation:{why:'<script>unsafe</script>',while_waiting:'safe work'},permitted_actions:['ignore']};
 const snapshot={requests:[request],request_dispositions:{r:{disposition:'ignored'}}};
 const html=api().requestMechanismDetails(snapshot,request);
 assert.match(html,/answered · Reply ignored/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
 assert.match(html,/data-ignore-request/);assert.doesNotMatch(html,/data-defer-request|data-cancel-request/);
 assert.equal(api().requestAllowed({...request,permitted_actions:[]},'ignore'),false);
 // A request without permitted_actions (older server or record) keeps the controls it always had;
 // only an action that did not exist before, ignoring, requires the server to name it.
 for(const action of ['respond_hint','approve_or_deny','confirm_interrupt','defer','cancel'])assert.equal(api().requestAllowed({request_id:'old'},action),true,action);
 assert.equal(api().requestAllowed({request_id:'old'},'ignore'),false);
 // When the server does send the list it is authoritative, including for the long-standing actions.
 for(const action of ['respond_hint','approve_or_deny','defer','cancel','ignore'])assert.equal(api().requestAllowed({permitted_actions:[]},action),false,action);
 assert.equal(api().requestAllowed({permitted_actions:['defer']},'respond_hint'),false);
 assert.equal(api().requestAllowed(null,'respond_hint'),false);
});
test('timeline shows persisted reply, execution and candidate identities',()=>{
 const request={request_id:'r',request_type:'hint',state:'answered'};
 const snapshot={requests:[request],request_timeline:{requests:[{request_id:'r',response_latency_seconds:3,transitions:[{state:'fallback',at:null}],used_response:{content:'Move left'},execution_links:[{execution_id:'execution-2'}],candidate_memories:[{memory_id:'memory-1'}]}]}};
 const html=api().requestMechanismDetails(snapshot,null);
 for(const text of ['Time unrecorded','Move left','execution-2'])assert.ok(html.includes(text));
});
test('request mechanism excludes note identities from the main page',()=>{
 const html=api().requestMechanismDetails({requests:[{request_id:'r',request_type:'hint',state:'answered'}],memory:{uses:[{memory_id:'private-note',attempt_id:'a'}]}},null);
 assert.doesNotMatch(html,/private-note|data-memory-operation/);
});
test('null GPU and reserved amounts remain distinct from zero and no budget',()=>{
 const html=api().resourceContext({resources:{tokens:{reserved:100},gpu_seconds:{reserved:0,used:null,budgeted:false,coverage:'host GPU utilization'}}});
 assert.match(html,/tokens reserved: 100/);assert.match(html,/GPU used: Not tracked/);assert.match(html,/No budget set/);assert.match(html,/host GPU utilization/);
});
test('run lock and autonomous block explain server facts',()=>{
 const html=api().attentionRunContext({lock:{policy_switchable:false,ranking_eligible:false,conditions:{seed:101}},autonomous_work:{state:'blocked',blocked_by:'resource',reason:'tokens exhausted'}});
 assert.match(html,/Policy switchable: false/);assert.match(html,/Ranking eligible: false/);assert.match(html,/tokens exhausted/);assert.match(html,/resource/);
});

test('ordinary fixed profile sends independent execution, assistance and request modes',()=>{
 const start=source.indexOf('  function fixedLaunchSelection('),end=source.indexOf('  function bindLaunch()',start);
 const build=vm.runInNewContext(source.slice(start,end)+'\nfixedLaunchSelection');
 const result=build({id:'cube',seed:101,execution_target:'robosuite_sim'},
  {policy:'full_trace_aware_attention_planner',assistance_mode:'live_human_first',attempts:'4',credits:'1',tokens:'4096',human_deadline:'30',wall_seconds:'300',request_mode:'blocking',gpu_seconds:'12'});
 assert.equal(result.execution_target,'robosuite_sim');assert.equal(result.assistance_mode,'live_human_first');assert.equal(result.request_mode,'blocking');
 assert.equal(result.assistance_credits,1);assert.equal(result.gpu_seconds,12);assert.equal(result.profile_id,'cube');
 assert.throws(()=>build({id:'real',seed:101,execution_target:'real_robot'},{}),/registered simulator/);
});
