const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const monitor=require('../ui/memory.js');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
const directory=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui_real_20261019/dev_v2_memory_directory_with_title.json'),'utf8'));
const base={id:directory.memories[0].memory_id,title:directory.memories[0].title,summary:'Use a bounded grasp.',group:'Grasp',status:'active',effect:'not_tested',reads:2,last_used_at:Date.now()/1000-60,version:2,versions:[2],test_seeded:true};

test('operator controls moved into the ball card and ID stays folded',()=>{
 const html=monitor.cardHtml(base,true);
 assert.match(html,/<summary>Manage…<\/summary>/);
 assert.match(html,/<summary>Details<\/summary><p>Note ID:/);
 assert.match(html,/Cabinet opening tip|Test note/);
 assert.match(html,/Setting an expiry and withdrawing a note cannot be undone/);
 for(const action of ['disable','enable','expiry','rollback'])assert.match(html,new RegExp(`data-action="${action}"`));
 assert.doesNotMatch(html.split('<details class="details">')[0],new RegExp(base.id));
 assert.doesNotMatch(html,/[\u3400-\u9fff]/);
});

test('same four endpoints receive actor, reason, future expiry and confirmation',()=>{
 const sent=[],confirmations=[];
 for(const action of ['disable','enable','expiry','rollback']){
  const request=monitor.manageRequest(base,action,'alice','review',action==='expiry'?'2099-01-01T12:00':'',message=>{confirmations.push(message);return true});
  sent.push({url:request.url,body:JSON.parse(request.options.body)});
 }
 assert.deepEqual(sent.map(x=>x.url.split('/').at(-1)),['disable','enable','expiry','rollback']);
 assert.ok(sent.every(x=>x.url.startsWith('/api/dev-memories/')));
 assert.ok(sent.every(x=>x.body.actor==='alice'&&x.body.reason==='review'));
 assert.deepEqual(Object.keys(sent[2].body).sort(),['actor','expires_at','reason']);
 const oldDocument=global.document;global.document={body:{dataset:{controlToken:'fixture-token'}}};try{assert.equal(monitor.manageRequest(base,'disable','alice','review','',()=>true).options.headers['X-Attention-Token'],'fixture-token')}finally{global.document=oldDocument}
 assert.ok(sent[2].body.expires_at>Date.now()/1000);
 assert.match(confirmations[2],/cannot be undone/);
 assert.match(confirmations[3],/cannot be undone/);
});

test('declined confirmation and invalid fields never produce a request',()=>{
 for(const action of ['disable','enable','expiry','rollback'])assert.equal(monitor.manageRequest(base,action,'alice','review','2099-01-01T12:00',()=>false),null);
 assert.throws(()=>monitor.manageRequest(base,'disable','','review','',()=>true),/Actor and reason/);
 assert.throws(()=>monitor.manageRequest(base,'enable','alice',' ','',()=>true),/Actor and reason/);
 assert.throws(()=>monitor.manageRequest(base,'expiry','alice','review','2020-01-01T00:00',()=>true),/future/);
});

test('missing operator authority disables every action and main view has no lifecycle card',()=>{
 const html=monitor.cardHtml(base,false);
 for(const action of ['disable','enable','expiry','rollback'])assert.match(html,new RegExp(`data-action="${action}" disabled`));
 assert.match(html,/operator key is not configured/);
 assert.doesNotMatch(source,/data-dev-memory-operation|data-dev-memory-action|data-memory-panel|data-memory-note-used/);
});
