const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../ui/live.js'),'utf8');
const block=source.slice(source.indexOf('  // Attention efficiency: task progress'),source.indexOf('  function resourceDetails(',source.indexOf('  // Attention efficiency: task progress')));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function setup(extra={}){
 const saved=new Map();
 const context={Date:{now:()=>210000},Math,Map,Number,String,esc,label:String,number:String,operatorTime:n=>`${Math.floor(n/60)}:${String(Math.round(n%60)).padStart(2,'0')}`,isTerminal:s=>s.run.status==='completed',policyTitle:String,stored:(key,value)=>{if(value===undefined)return saved.get(key)||null;saved.set(key,value);},...extra};
 const api=vm.runInNewContext(block+'\n({attentionLedger,attentionStripModel,attentionFilterMatches,attentionEfficiency,bindAttentionEfficiency})',context);
 return {api,saved,context};
}
function snapshot({statuses=['failed','succeeded'],maximum=4,requests=[],terminal=false}={}){
 const attempts=statuses.map((status,index)=>({attempt_id:`a${index}`,index,status}));
 return {run:{run_id:'story-run',status:terminal?'completed':'running',policy_id:'planner'},attempts,task_plan:{max_attempts:maximum},requests:requests.map((r,i)=>({request_id:r.id||`h${i}`,request_type:r.kind||'hint',attempt_id:`a${r.after??0}`,response_id:`reply${i}`,state:r.pending?'pending':'answered',mode:r.person?'live_human_first':'automatic',reason:r.reason||null,created_at:10+i*100})),responses:Object.fromEntries(requests.flatMap((r,i)=>r.pending?[]:[[ `reply${i}`,{responder:r.person?'human':'advisor_proxy',created_at:10+i*100+(r.seconds||0)} ]])),response_evidence:{},resources:{help_count:{used:requests.length,limit:12}},eval:{native_outcomes:{}},comparison:{policies:[]}};
}
test('ledger becomes ordered attempts and fanned help, with dashed waiting marks',()=>{
 const {api}=setup();const snap=snapshot({statuses:['failed','failed','running'],requests:[{id:'h1',after:0},{id:'h2',after:0,person:true,seconds:42},{id:'h3',after:1,pending:true,person:true}]});
 const model=api.attentionStripModel(api.attentionLedger(snap));
 assert.deepEqual(Array.from(model.attempts,a=>a.state),['failed','failed','running','future']);
 assert.deepEqual(Array.from(model.requests,r=>[r.id,r.after,r.stack,r.dashed]),[['h1',0,0,false],['h2',0,1,false],['h3',1,0,true]]);
 assert.deepEqual(Array.from(model.attempts,a=>a.x),[model.left,model.left+model.step,model.left+2*model.step,model.left+3*model.step]);
 assert.ok(model.requests[0].x<model.requests[1].x);assert.ok(model.requests[1].top<model.requests[0].top);
 assert.ok(model.step>=2*92+48);assert.ok(model.width>320);
 const html=api.attentionEfficiency(snap);assert.match(html,/data-attn-help="h3"[^>]*dashed|class="[^"]*dashed"[^>]*data-attn-help="h3"/);
 assert.match(html,/data-attn-attempt="4"/);assert.match(html,/42 s of human time/);
});
test('help requests are ordered by the attempt they follow and request time',()=>{
 const {api}=setup();const snap=snapshot({statuses:['failed','failed','succeeded'],requests:[{id:'first',after:0},{id:'second',after:1},{id:'third',after:0}]});
 snap.requests=[snap.requests[1],snap.requests[2],snap.requests[0]];
 assert.deepEqual(Array.from(api.attentionLedger(snap).rows,r=>r.id),['first','third','second']);
});
test('four filter buttons match the correct help requests',()=>{
 const {api,saved}=setup();const snap=snapshot({requests:[{id:'auto',after:0},{id:'person',after:0,person:true,seconds:42}]});const rows=api.attentionLedger(snap).rows;
 assert.deepEqual(['all','human','paid','rate'].map(f=>Array.from(rows,r=>api.attentionFilterMatches(r,f))),[[true,true],[false,true],[true,true],[true,true]]);
 saved.set('attention.efficiency.filter:story-run','human');const html=api.attentionEfficiency(snap);
 assert.match(html,/data-attn-filter="human"[^>]*aria-pressed="true"/);
 assert.match(html,/class="op-attn-help automatic dim/);assert.match(html,/data-kpi="progress"/);assert.match(html,/data-kpi="spent"/);assert.match(html,/data-kpi="rate"/);assert.match(html,/data-kpi="hit"/);
});
test('detail persists by request id, updates live, and escapes reasons',()=>{
 const {api,saved}=setup();const snap=snapshot({requests:[{id:'chosen',after:0,person:true,seconds:42,reason:'<unsafe>'}]});
 saved.set('attention.efficiency.help:story-run','chosen');let html=api.attentionEfficiency(snap);
 assert.match(html,/data-attn-step="-1"[^>]*disabled/);assert.match(html,/data-attn-step="1"[^>]*disabled/);assert.match(html,/data-attn-close/);assert.match(html,/class="op-attn-outcome solved">Solved</);assert.match(html,/on attempt 2 of 4/);assert.match(html,/&lt;unsafe&gt;/);assert.doesNotMatch(html,/<unsafe>/);assert.match(html,/<a href="#" class="op-attn-watch"[^>]*>Watch Attempt 2<\/a>/);assert.match(html,/Next attempt succeeded/);
 snap.attempts[1].status='running';html=api.attentionEfficiency(snap);assert.match(html,/Next attempt running/);
 assert.doesNotMatch(html,/Compare with other strategies/);assert.doesNotMatch(html,/No other strategies recorded yet/);
 snap.comparison={comparable:true,policies:[{policy_id:'planner',average_assistance_used:1,success_rate:.5,successes:1,runs:2},{policy_id:'reactive_help',average_assistance_used:2,success_rate:.25,successes:1,runs:4}]};html=api.attentionEfficiency(snap);
 assert.match(html,/data-persist-detail="attention-compare:story-run"/);assert.match(html,/<summary>Compare with other strategies<\/summary>/);
});
test('all effect words and request kinds remain available',()=>{
 const {api,saved}=setup();const snap=snapshot({statuses:['failed','failed'],requests:[{id:'e',kind:'demo',after:0}]});saved.set('attention.efficiency.help:story-run','e');
 for(const [status,word] of [['succeeded','Next attempt succeeded'],['failed','Next attempt still failed'],['running','Next attempt running']]){snap.attempts[1].status=status;assert.ok(api.attentionEfficiency(snap).includes(word));}
 snap.attempts.pop();assert.match(api.attentionEfficiency(snap),/Waiting for the next attempt/);
 snap.requests[0].state='pending';delete snap.responses.reply0;assert.match(api.attentionEfficiency(snap),/Waiting for a reply/);
 snap.requests[0].state='skipped';assert.match(api.attentionEfficiency(snap),/No reply given/);
 assert.match(api.attentionEfficiency(snap),/Example/);
});
test('empty, automatic-only, ended, and twelve-attempt budgets render',()=>{
 const {api}=setup();let snap=snapshot({statuses:[],maximum:4});let html=api.attentionEfficiency(snap);
 assert.match(html,/No attempts yet/);assert.match(html,/no help requested/);assert.match(html,/data-attn-attempt="4"/);
 snap=snapshot({statuses:['failed'],requests:[{after:0}],terminal:true});html=api.attentionEfficiency(snap);
 assert.match(html,/not solved/);assert.match(html,/answered by the automatic advisor, 0 s of human time/);
 assert.match(html,/class="op-attn-end unsolved"/);assert.match(html,/>Not completed</);assert.doesNotMatch(html,/data-attn-attempt="2"/);assert.match(html,/data-attn-watch="a0"/);
 assert.doesNotMatch(html,/Was the human attention worth it/);assert.doesNotMatch(html,/op-attn-foot/);
 snap=snapshot({statuses:['failed'],maximum:12,requests:Array.from({length:7},(_,i)=>({id:`h${i}`,after:0}))});
 const model=api.attentionStripModel(api.attentionLedger(snap));assert.equal(model.attempts.length,12);assert.equal(model.requests[6].stack,6);assert.ok(model.width>1900);
});
test('help before the first attempt has room in the strip',()=>{
 const {api}=setup();const snap=snapshot({statuses:[],maximum:2});
 snap.requests=[{request_id:'early',request_type:'demo',attempt_id:null,response_id:'reply',state:'answered',mode:'automatic',created_at:10}];
 snap.responses.reply={responder:'advisor_proxy',created_at:10};
 snap.response_evidence.early={association:{attempt_id:null}};
 const ledger=api.attentionLedger(snap);ledger.rows[0].nextIndex=0;
 const model=api.attentionStripModel(ledger);assert.equal(model.requests[0].after,-1);assert.ok(model.left>model.step);
});
test('filter and help clicks store the selected values',()=>{
 const saved=new Map(),handlers={},snap=snapshot({requests:[{id:'h0',after:0}]});
 const buttons={filter:{dataset:{attnFilter:'human'},getAttribute:()=> 'human',addEventListener:(type,fn)=>{handlers.filter=fn;},focus:()=>{}},help:{dataset:{attnHelp:'h0'},getAttribute:()=> 'h0',addEventListener:(type,fn)=>{handlers[`help:${type}`]=fn;},focus:()=>{}}};
 const panel={querySelectorAll:selector=>selector==='[data-attn-filter]'?[buttons.filter]:selector==='[data-attn-help]'?[buttons.help]:[],querySelector:selector=>selector==='.op-attn-scroll'?{scrollLeft:0}:null,set outerHTML(value){assert.match(value,/data-attention-efficiency/);}};
 const view={querySelector:selector=>selector==='[data-attention-efficiency]'?panel:null};
 const {api}=setup({view,stored:(key,value)=>{if(value===undefined)return saved.get(key)||null;saved.set(key,value);}});
 api.bindAttentionEfficiency(snap);handlers.filter();assert.equal(saved.get('attention.efficiency.filter:story-run'),'human');
 handlers['help:click']();assert.equal(saved.get('attention.efficiency.help:story-run'),'h0');
});
test('Watch Attempt selects the same stored history key as a viewer tab',()=>{
 const saved=new Map(),listeners={};let rendered=false,scrolled=false;
 const watch={dataset:{historySelect:'a1'},addEventListener:(name,fn)=>listeners[name]=fn};
 const panel={querySelectorAll:()=>[],querySelector:selector=>selector==='[data-history-select]'?watch:null};
 const view={querySelector:selector=>selector==='[data-attention-efficiency]'?panel:selector==='[data-execution-viewer]'?{scrollIntoView:()=>{scrolled=true;}}:null};
 const {api}=setup({view,stored:(key,value)=>{if(value===undefined)return saved.get(key)||null;saved.set(key,value);},render:()=>{rendered=true;},currentSnapshot:{}});
 api.bindAttentionEfficiency(snapshot());listeners.click({currentTarget:watch,preventDefault:()=>{}});
 assert.equal(saved.get('attention.history:story-run'),'a1');assert.equal(rendered,true);assert.equal(scrolled,true);
});
