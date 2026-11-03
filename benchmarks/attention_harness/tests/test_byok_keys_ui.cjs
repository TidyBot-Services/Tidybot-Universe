// Offline tests of the production routing helpers; no browser, sockets or dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../ui/live.js'), 'utf8');
const section = source.slice(source.indexOf('  const BYOK_ALL_ROLES'), source.indexOf('  function bindLaunch()'));
function load(allRoles = true) {
  const context = vm.createContext({});
  vm.runInContext(section.replace(/BYOK_ALL_ROLES = (true|false)/, `BYOK_ALL_ROLES = ${allRoles}`) +
    '\nthis.api={keysSelection,keyRoleAssignments,keysSectionHtml,BYOK_ALL_ROLES};', context);
  return context.api;
}
const api = load();
const plain = value => JSON.parse(JSON.stringify(value));
function state(mode = 'lab') {
  return {mode, shared: {credential:'mine', model:' shared-model '},
    choice: {dev:'codex', eval:'codex', advisor:'codex'}, model: {dev:'dev-model', eval:'eval-model', advisor:'advisor-model'},
    creds: [{credential_id:'mine', provider:'deepseek', expires_at:1000},
      {credential_id:'second', provider:'openai-compatible', expires_at:1000}]};
}
const route = (credential, provider, model) => ({credential, provider, model});
const select = (value, allRoles = true) => plain(load(allRoles).keysSelection(value, undefined, 100));

test('every role can use a saved key in production', () => {
  assert.match(section, /const BYOK_ALL_ROLES = true;/);
  assert.equal(api.BYOK_ALL_ROLES, true);
});
for (const allRoles of [false, true]) {
  test(`Lab mode ignores saved and per-role keys (phase 2: ${allRoles})`, () => {
    const s = state();
    s.choice = {dev:'key:mine', eval:'key:second', advisor:'key:mine'};
    assert.equal(select(s, allRoles), null);
  });
  test(`all-lab custom routing returns null (phase 2: ${allRoles})`, () => {
    assert.equal(select(state('different'), allRoles), null);
  });
  test(`shared key reaches exactly the supported roles (phase 2: ${allRoles})`, () => {
    const roles = {dev:route('mine', 'deepseek', 'shared-model')};
    if (allRoles) Object.assign(roles, {eval:roles.dev, advisor:roles.dev});
    assert.deepEqual(select(state('all'), allRoles), {roles});
  });
  test(`different roles keep their own credential and model (phase 2: ${allRoles})`, () => {
    const s = state('different');
    s.choice = {dev:'key:mine', eval:'key:second', advisor:'key:mine'};
    const roles = {dev:route('mine', 'deepseek', 'dev-model')};
    if (allRoles) Object.assign(roles, {eval:route('second', 'openai-compatible', 'eval-model'), advisor:route('mine', 'deepseek', 'advisor-model')});
    assert.deepEqual(select(s, allRoles), {roles});
  });
}
test('with the flag off, stale Eval / Advisor choices are ignored', () => {
  const s = state('different');s.choice.eval = 'key:missing';s.choice.advisor = 'key:mine';
  assert.equal(select(s, false), null);
});
test('Eval-only and Advisor-only overrides with Dev on lab', () => {
  for (const role of ['eval', 'advisor']) {
    const s = state('different');s.choice[role] = 'key:second';
    assert.deepEqual(select(s), {roles:{[role]:route('second', 'openai-compatible', `${role}-model`)}});
  }
});
test('mode switches restore independent custom choices without mutating state', () => {
  const s = state('different');s.choice.dev = 'key:second';
  const before = JSON.stringify(s), custom = select(s);
  s.mode = 'all';assert.equal(select(s).roles.dev.credential, 'mine');
  s.mode = 'lab';assert.equal(select(s), null);
  s.mode = 'different';assert.deepEqual(select(s), custom);assert.equal(JSON.stringify(s), before);
});
for (const mode of ['all', 'different']) {
  test(`${mode}: missing, deleted and expired keys cannot silently fall back to lab`, () => {
    const s = state(mode);s.choice.dev = 'key:mine';s.creds = [];
    assert.throws(() => select(s), /missing or has expired/);
    s.creds = [{credential_id:'mine', provider:'deepseek', expires_at:100}];
    assert.throws(() => select(s), /expired/);
  });
  test(`${mode}: empty model is rejected; model whitespace is trimmed`, () => {
    const s = state(mode);s.choice.dev = 'key:mine';s.model.dev = s.shared.model = '  ';
    assert.throws(() => select(s), /model name/);
    s.model.dev = s.shared.model = ' custom-model ';
    assert.equal(select(s).roles.dev.model, 'custom-model');
  });
}
test('unsaved shared mode cannot start; pending saves cannot launch with old routing', () => {
  const s = state('all');s.shared.credential = '';
  assert.throws(() => select(s), /Save a key/);
  s.shared.credential = 'mine';s.saving = true;
  assert.throws(() => select(s), /finish saving/);
  s.mode = 'lab';assert.equal(select(s), null);
});
test('selection exposes only the public credential, provider and model', () => {
  const s = state('all');s.creds[0].base_url_host = 'example.invalid';s.creds[0].key_set = true;
  assert.deepEqual(Object.keys(select(s).roles.dev).sort(), ['credential', 'model', 'provider']);
});
test('template defaults to Lab and collapsed roles, retaining named lab models and password input', () => {
  const html = api.keysSectionHtml();
  assert.match(html, /value="lab" data-keys-mode checked/);
  assert.match(html, /<details[^>]*data-keys-details>/);
  assert.equal((html.match(/type="radio"/g) || []).length, 3);
  for (const role of ['dev', 'eval', 'advisor']) {
    assert.equal((html.match(new RegExp(`name="${role}_model"`, 'g')) || []).length, 1);
    const options = html.match(new RegExp(`<select name="${role}_model"[^>]*>(.*?)</select>`))[1];
    assert.match(options, /<option selected>gpt-6\.1-sol<\/option><option>gpt-6-astra<\/option>/);
    assert.equal(options.includes('gpt-6-sol'), role !== 'advisor');
  }
  assert.match(html, /data-key-value type="password" autocomplete="off"/);
  assert.equal((html.match(/data-key-value/g) || []).length, 1);
});
test('the shared-key explanation follows the feature flag', () => {
  assert.match(load(false).keysSectionHtml(), /data-keys-phase2 >Eval and Advisor use the lab's models until phase 2/);
  assert.match(api.keysSectionHtml(), /data-keys-phase2 hidden>/);
});

// Minimal event/element doubles execute bindKeys itself without network or DOM dependencies.
// Browser layout still requires the separate Chromium suite.
function keyFixture() {
  const nodes=new Map(), events=[], requests=[], handlers=new Map();
  let timer, failSave=false, failModels=null;
  let creds=[];
  const offered=['deepseek-chat','deepseek-reasoner','deepseek-v4'];
  const presets=['deepseek','openai','parcc','openai-compatible'].map(id=>({id,label:id,needs_key:true,suggested_models:['deepseek-chat'],thinking:id==='deepseek'?'off':null}));
  function node(selector) {
    if(nodes.has(selector))return nodes.get(selector);
    const element={value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,dataset:{},
      classList:{toggle(){}},querySelector:node,
      querySelectorAll:query=>query==='[data-keys-mode]'?modes:[],
      appendChild(child){child.parentElement=this;},
      closest(query){return query===selector?this:null;},
      addEventListener(type,callback){handlers.set(selector+':'+type,callback);},
      dispatchEvent(event){events.push(event.type);handlers.get(selector+':'+event.type)?.(event);return true;}};
    nodes.set(selector,element);return element;
  }
  const modes=['lab','all','different'].map(value=>Object.assign(node('mode-'+value),{value,closest:q=>q==='[data-keys-mode]'?modes.find(m=>m.value===value):null}));
  const form=node('form'),box=node('[data-keys]'),dialog=node('dialog');
  node('[data-tokens-unlimited]').textContent='Not limited · free models';
  node('[data-key-provider]').value='deepseek';
  const context=vm.createContext({esc:String,launchDialog:dialog,
    setInterval:fn=>{timer=fn;return 1;},clearInterval(){},
    CustomEvent:class {constructor(type){this.type=type;}},
    controlFetch:async (url,options)=>{
      const body=options.body?JSON.parse(options.body):null;
      requests.push({url,method:options.method,body});
      let value;
      if(url==='/api/provider/presets')value=presets;
      else if(options.method==='GET'&&url.endsWith('/models')){
        if(failModels)throw new Error(failModels);
        value={models:offered};
      }else if(options.method==='GET')value=creds;
      else if(options.method==='PUT'){
        if(failSave)throw new Error('Save failed');
        value={credential_id:url.split('/').pop(),provider:body.provider,expires_at:Date.now()/1000+86400};
        creds=creds.filter(c=>c.credential_id!==value.credential_id).concat(value);
      }else if(options.method==='DELETE'){creds=creds.filter(c=>c.credential_id!==url.split('/').pop());value={ok:true};}
      else value={checks:{auth:'ok',chat:'ok'}};
      return {ok:true,json:async()=>value};
    }});
  vm.runInContext(section+'\nthis.bind=bindKeys;',context);
  const binding=context.bind(form);
  const emit=async(type,target)=>handlers.get('[data-keys]:'+type)({target,key:''});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  const api2={};
  return Object.assign(api2,{binding,form,node,events,requests,context,flush,
    mode:async value=>emit('change',modes.find(m=>m.value===value)),
    save:()=>emit('click',node('[data-key-save]')),
    input:async(selector,value)=>{const n=node(selector);n.value=value;await emit('input',n);},
    pick:async(role,value)=>{const n=node(`[data-role-choice="${role}"]`);n.dataset.roleChoice=role;n.value=value;n.closest=q=>q==='[data-role-choice]'?n:null;await emit('change',n);},
    expire(){creds.forEach(c=>c.expires_at=0);dialog.open=true;timer();},
    failSave(){failSave=true;},
    failModels(message='models failed'){failModels=message;},
    menu:async id=>{const n=node(`[data-model-menu="${id}"]`);n.dataset.modelMenu=id;n.closest=q=>q==='[data-model-menu]'?n:null;await emit('click',n);},
    option:async value=>{const n=node('option-'+value);n.dataset.modelOption=value;n.closest=q=>q==='[data-model-option]'?n:null;await emit('click',n);},
    choose:async(id,value)=>{await api2.menu(id);await api2.option(value);},
    emitClick:n=>emit('click',n),
    sharedKey:async value=>{const n=node('[data-shared-key]');n.value=value;n.closest=q=>q==='[data-shared-key]'?n:null;await emit('change',n);},
    roleModel:async(role,value)=>{const n=node(`[data-role-model="${role}"]`);n.dataset.roleModel=role;n.value=value;n.closest=q=>q==='[data-role-model]'?n:null;await emit('input',n);},
    key:async key=>handlers.get('[data-keys]:keydown')({target:node('[data-keys]'),key}),
    removeShared:async()=>{const n=node('[data-keys-shared-remove]');n.closest=q=>q==='[data-key-remove]'?n:null;await emit('click',n);},
    remove:name=>{const n=node('[data-key-remove]');n.dataset.keyRemove=name;return emit('click',n);},
  });
}
async function saveKey(f,provider='deepseek',name='') {
  f.node('[data-key-provider]').value=provider;
  f.node('[data-key-name]').value=name;
  f.node('[data-key-model]').value='deepseek-chat';
  f.node('[data-key-value]').value='test-only-secret';
  await f.save();
}

test('bindKeys auto-names each provider, avoids collisions and respects explicit names',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');
  for(const [provider,prefix] of [['deepseek','deepseek'],['openai','openai'],['parcc','parcc'],['openai-compatible','custom']]){
    await saveKey(f,provider);
    assert.equal(f.binding.summary().dev.keyName,`${prefix}-1`);
    await saveKey(f,provider);
    assert.equal(f.binding.summary().dev.keyName,`${prefix}-2`);
    assert.equal(f.node('[data-key-value]').value,'');
  }
  await f.mode('different');await saveKey(f,'deepseek','  my-ds  ');
  assert.equal(f.requests.filter(r=>r.method==='PUT').at(-1).url,'/api/provider/credentials/my-ds');
  await saveKey(f);
  assert.equal(f.requests.filter(r=>r.method==='PUT').at(-1).url,'/api/provider/credentials/deepseek-3');
});

test('summary uses only public metadata and refreshes across save, model edits and modes',async()=>{
  const f=keyFixture();await f.flush();
  assert.deepEqual(plain(f.binding.summary()),{dev:null,eval:null,advisor:null});
  await f.mode('all');let count=f.events.length;await saveKey(f,'deepseek','my-ds');
  assert.ok(f.events.length>count);
  const mine={model:'deepseek-chat',keyName:'my-ds',thinking:'off'},route={credential:'my-ds',provider:'deepseek',model:'deepseek-chat'};
  assert.deepEqual(plain(f.binding.summary()),{dev:mine,eval:mine,advisor:mine}); // One shared key reaches all three roles.
  assert.deepEqual(plain(f.binding.selection()),{roles:{dev:route,eval:route,advisor:route}});
  assert.ok(!JSON.stringify(f.binding.summary()).includes('test-only-secret'));
  f.node('[data-key-value]').value='another-unsaved-secret';
  assert.ok(!JSON.stringify(f.binding.summary()).includes('another-unsaved-secret'));
  count=f.events.length;await f.input('[data-key-model]',' changed-model ');
  assert.equal(f.binding.summary().dev.model,'changed-model');assert.ok(f.events.length>count);
  await f.mode('lab');assert.equal(f.binding.summary().dev,null);assert.equal(f.binding.selection(),null);
  await f.mode('all');assert.equal(f.binding.summary().dev.model,'changed-model');
  f.binding.clearSecrets();assert.equal(f.node('[data-key-value]').value,'');
});

test('different-agent selection, deletion and expiry refresh the summary without stale keys',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');await saveKey(f,'deepseek','my-ds');
  assert.equal(f.binding.summary().dev.keyName,'my-ds');
  let count=f.events.length;await f.pick('dev','codex');assert.equal(f.binding.summary().dev,null);assert.ok(f.events.length>count);
  await f.pick('dev','key:my-ds');assert.equal(f.binding.summary().dev.keyName,'my-ds');
  count=f.events.length;await f.remove('my-ds');assert.equal(f.binding.summary().dev,null);assert.ok(f.events.length>count);
  await saveKey(f,'deepseek','my-ds');count=f.events.length;f.expire();
  assert.equal(f.binding.summary().dev,null);assert.ok(f.events.length>count);
  assert.throws(()=>f.binding.selection(),/missing or has expired/);
});

test('failed saves clear the password and do not publish a key in the summary',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');f.failSave();
  const count=f.events.length;await saveKey(f);
  assert.equal(f.node('[data-key-value]').value,'');assert.equal(f.binding.summary().dev,null);
  assert.ok(f.events.length>count);assert.match(f.node('[data-keys-note]').textContent,/Save failed/);
});

test('the real footer writer shows the BYOK Dev model/name, lab fallback and two summary lines',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');await saveKey(f,'deepseek','my-ds');
  Object.assign(f.form,{seed:{value:'102'},dev_model:{value:'gpt-6.1-sol'},eval_model:{value:'gpt-6-astra'},
    dev_mode:{value:'agent'},max_attempts:{value:'10'},help_count:{value:'5'},eval_agent_enabled:{checked:true},model_source:{value:'codex'}});
  Object.assign(f.context,{form:f.form,keys:f.binding,armNames:{both:'Both arms'},selectedProfile:()=>({channel:'dev_v2',enabled:true,robot:'OpenArm365',execution_arm:'both',layout_id:11,style_id:34})});
  const writer=source.slice(source.indexOf('      function updateSummary()'),source.indexOf('      function syncProfile(resetWall)'));
  const baseWriter=source.slice(source.indexOf('  function runConfigSummary('),source.indexOf('  function launchSourceProfile('));
  vm.runInContext(baseWriter+writer+'\nthis.refreshSummary=updateSummary;',f.context);
  const read=()=>{f.context.refreshSummary();return f.node('[data-launch-preview]').textContent;};
  assert.match(read(),/Dev deepseek-chat \(your key my-ds · thinking off\)/);assert.equal(read().split('\n').length,2);
  assert.ok(!read().includes('test-only-secret'));
  assert.match(read(),/Eval deepseek-chat \(your key my-ds · thinking off\) · Advisor deepseek-chat \(your key my-ds · thinking off\)/);
  await f.mode('lab');assert.match(read(),/Dev gpt-6\.1-sol/);assert.match(read(),/Eval gpt-6-astra/);assert.ok(!read().includes('your key')&&!read().includes('Advisor'));
  await f.mode('all');f.form.dev_mode.value='one_call';assert.ok(!read().includes('your key')); // Only Agent submissions carry BYOK routing.
});

test('run type buttons dispatch the original bubbling change and follow programmatic changes',()=>{
  const listeners={},buttons=['dev','fixed'].map(runType=>({dataset:{runType},attrs:{},
    setAttribute(name,value){this.attrs[name]=value;},addEventListener(type,fn){this[type]=fn;}}));
  const select={value:'dev',disabled:false,addEventListener(type,fn){listeners[type]=fn;},dispatchEvent(event){assert.equal(event.bubbles,true);listeners[event.type]();}};
  const form={run_type:select,querySelectorAll:()=>buttons};
  const code=source.slice(source.indexOf('      function syncRunTypeButtons()'),source.indexOf("      form.addEventListener('ab-keys-change'"));
  const context=vm.createContext({form,Event});vm.runInContext(code+'\nsyncRunTypeButtons();',context);
  assert.equal(buttons[0].attrs['aria-pressed'],'true');buttons[1].click();
  assert.equal(select.value,'fixed');assert.equal(buttons[1].attrs['aria-pressed'],'true');
  select.value='dev';select.dispatchEvent(new Event('change',{bubbles:true}));
  assert.equal(buttons[0].attrs['aria-pressed'],'true');assert.equal(buttons[1].attrs['aria-pressed'],'false');
});

const optionsOf=list=>[...list.innerHTML.matchAll(/data-model-option="([^"]+)"/g)].map(m=>m[1]);

async function saveKeyOnly(f,provider='deepseek',name='') {
  f.node('[data-key-provider]').value=provider;f.node('[data-key-name]').value=name;
  f.node('[data-key-model]').value='';f.node('[data-key-value]').value='test-only-secret';
  await f.save();await f.flush();
}

test('the model field is grey until the key is accepted; then it lists the provider\'s models and picking one tests it',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');
  const input=f.node('[data-key-model]');
  assert.equal(input.disabled,true);assert.equal(input.placeholder,'Save key first');assert.equal(f.node('[data-model-menu="add"]').hidden,true);
  await saveKeyOnly(f,'deepseek','my-ds');
  assert.ok(f.requests.some(r=>r.method==='GET'&&r.url==='/api/provider/credentials/my-ds/models'));
  assert.equal(input.disabled,false);assert.equal(input.placeholder,'Model name');
  assert.match(f.node('[data-keys-shared-test]').innerHTML,/✓ Key accepted · 3 models/);
  assert.ok(!f.requests.some(r=>r.url.endsWith('/test')),'no model was chosen yet, so nothing is probed');
  await f.menu('add');
  const list=f.node('[data-model-list="add"]');
  assert.equal(list.hidden,false);assert.deepEqual(optionsOf(list),['deepseek-chat','deepseek-reasoner','deepseek-v4']);
  await f.option('deepseek-reasoner');
  assert.equal(input.value,'deepseek-reasoner');assert.equal(list.hidden,true);
  assert.equal(f.requests.filter(r=>r.url.endsWith('/test')).at(-1).body.model,'deepseek-reasoner'); // Picking completes Save and test.
  assert.match(f.node('[data-keys-shared-test]').innerHTML,/✓ All checks passed/);
  assert.equal(f.binding.summary().dev.model,'deepseek-reasoner');assert.equal(f.binding.selection().roles.eval.model,'deepseek-reasoner');
  assert.equal(f.node('[data-keys-models]').hidden,true); // The three read-only copies of one shared model are gone.
});

test('a rejected key keeps the model field grey; a provider that cannot list models still allows typing',async()=>{
  let f=keyFixture();await f.flush();await f.mode('all');f.failModels('HTTP 401: invalid key');
  await saveKeyOnly(f,'deepseek','my-ds');
  assert.equal(f.node('[data-key-model]').disabled,true);assert.match(f.node('[data-keys-shared-test]').innerHTML,/HTTP 401/);
  f=keyFixture();await f.flush();await f.mode('all');f.failModels('This provider does not list models');
  await saveKeyOnly(f,'openai-compatible','mine');
  assert.equal(f.node('[data-key-model]').disabled,false);
  await f.input('[data-key-model]','anything-i-type');
  assert.equal(f.binding.summary().dev.model,'anything-i-type');
});

test('per-agent model menus list the chosen key\'s models and write only that role',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');await saveKey(f,'deepseek','my-ds');await f.flush();
  assert.equal(f.node('[data-role-model="eval"]').disabled,false);
  await f.pick('eval','key:my-ds');
  assert.equal(f.node('[data-model-menu="eval"]').hidden,false);assert.equal(f.node('[data-role-model="eval"]').disabled,false);
  assert.equal(f.node('[data-model-menu="advisor"]').hidden,true); // Advisor still uses the lab.
  await f.menu('eval');assert.deepEqual(optionsOf(f.node('[data-model-list="eval"]')),['deepseek-chat','deepseek-reasoner','deepseek-v4']);
  await f.option('deepseek-v4');
  assert.equal(f.binding.selection().roles.eval.model,'deepseek-v4');
  assert.notEqual(f.binding.selection().roles.dev.model,'deepseek-v4');
  assert.equal(f.node('[data-role-act="eval"]').hidden,false);assert.equal(f.node('[data-role-act="advisor"]').hidden,true);
});

test('Escape closes an open model menu',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');await saveKeyOnly(f,'deepseek','my-ds');
  await f.menu('add');assert.equal(f.node('[data-model-list="add"]').hidden,false);
  await f.key('Escape');assert.equal(f.node('[data-model-list="add"]').hidden,true);
});

test('the saved-key line shows name and expiry; Remove deletes only that key and never the whole collection',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');
  assert.equal(f.node('[data-keys-status]').hidden,true);
  await saveKey(f,'deepseek','my-ds');await f.flush();
  assert.equal(f.node('[data-keys-status]').hidden,false);
  assert.match(f.node('[data-keys-shared-saved]').innerHTML,/<b>my-ds<\/b> · deepseek · .*expires in <em data-key-left=/);
  assert.equal(f.node('[data-keys-shared-remove]').dataset.keyRemove,'my-ds');
  await f.removeShared();
  assert.deepEqual(f.requests.filter(r=>r.method==='DELETE').map(r=>r.url),['/api/provider/credentials/my-ds']);
  assert.equal(f.node('[data-keys-status]').hidden,true);
  f.node('[data-keys-shared-remove]').dataset.keyRemove='';
  await f.removeShared();
  assert.equal(f.requests.filter(r=>r.method==='DELETE').length,1); // An empty id is ignored, not sent as DELETE on the collection.
});

test('a per-agent key needs no model of its own; the roles take theirs from the provider',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');
  f.node('[data-key-provider]').value='deepseek';f.node('[data-key-value]').value='test-only-secret';f.node('[data-key-model]').value='';
  await f.save();
  assert.ok(f.requests.some(r=>r.method==='PUT'));assert.equal(f.binding.summary().dev.model,'deepseek-chat');
  assert.equal(f.node('[data-key-model-field]').hidden,true);
});

test('test results are compact: unknown checks are left out, failures are named',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');await saveKey(f,'deepseek','my-ds');await f.flush();
  const html=f.node('[data-keys-shared-test]').innerHTML;
  assert.match(html,/✓ All checks passed/);assert.ok(!html.includes('Images')&&!html.includes('fail'));
});

test('a role\'s Test control reports Passed in place and only a failure opens a detail line',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');await saveKey(f,'deepseek','my-ds');await f.flush();
  await f.pick('dev','key:my-ds');await f.input('[data-role-model="dev"]','deepseek-chat');
  const button=f.node('[data-role-test="dev"]'),status=f.node('[data-key-test="dev"]');
  assert.equal(button.textContent,'Test');assert.equal(status.hidden,true);
  await emitTest(f);
  assert.equal(button.textContent,'✓ Passed');assert.equal(button.dataset.result,'ok');assert.equal(status.hidden,true);
});
async function emitTest(f){
  const n=f.node('[data-role-test="dev"]');n.dataset.roleTest='dev';n.closest=q=>q==='[data-role-test]'?n:null;
  await f.emitClick(n);await f.flush();
}

test('a rejected key says so in the role row and Test re-checks it instead of asking for a model',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');
  f.failModels('HTTP 401: {"error":{"message":"Authentication Fails, Your api key: ****1226 is invalid"}}');
  await saveKey(f,'deepseek','my-ds');await f.flush();
  await f.roleModel('dev',''); // The preset's suggestion is gone for real providers like DeepSeek.
  const input=f.node('[data-role-model="dev"]'),status=f.node('[data-key-test="dev"]'),button=f.node('[data-role-test="dev"]');
  assert.equal(input.disabled,true);assert.equal(input.placeholder,'Key rejected'); // Not "Checking key…" forever.
  assert.equal(status.hidden,false);assert.match(status.innerHTML,/The provider rejected this key · HTTP 401/);
  assert.equal(button.textContent,'Retry');
  f.failModels(null); // The user pasted a good key / the provider recovered.
  const listings=()=>f.requests.filter(r=>r.url.endsWith('/models')).length,before=listings();
  await emitTest(f);
  assert.equal(listings(),before+1);assert.equal(input.disabled,false);
  assert.ok(!f.requests.some(r=>r.url.endsWith('/test')),'no model yet, so nothing is probed');
  assert.match(status.innerHTML,/Pick a model first\./);assert.equal(button.textContent,'Test'); // A clear instruction, not a failure or a dead end.
});

test('saved keys are shared between "My key for all agents" and "Different per agent"',async()=>{
  const f=keyFixture();await f.flush();await f.mode('different');
  await saveKey(f,'deepseek','from-different');await f.flush();
  await f.mode('all'); // A key added per agent is offered here, selected, without typing it again.
  const pick=f.node('[data-shared-key]');
  assert.equal(f.node('[data-shared-key-field]').hidden,false);assert.equal(pick.value,'from-different');
  assert.match(pick.innerHTML,/value="from-different">from-different · deepseek<\/option>.*<option value="">\+ New key…/);
  assert.equal(f.binding.summary().dev.keyName,'from-different');
  for(const field of ['[data-key-provider-field]','[data-key-secret-field]','[data-key-save]'])assert.equal(f.node(field).hidden,true,field);
  assert.equal(f.node('[data-key-model-field]').hidden,false);
  // Choose "+ New key…": the add fields come back and nothing is selected until the key is saved.
  await f.sharedKey('');
  for(const field of ['[data-key-provider-field]','[data-key-secret-field]','[data-key-save]'])assert.equal(f.node(field).hidden,false,field);
  assert.equal(f.binding.summary().dev,null);assert.equal(f.node('[data-key-model]').disabled,true);
  await saveKey(f,'deepseek','from-all');await f.flush();
  assert.equal(f.binding.summary().dev.keyName,'from-all');assert.equal(f.node('[data-shared-key]').value,'from-all');
  assert.equal(f.node('[data-key-save]').hidden,true); // Saved: back to choosing among keys.
  // Both keys are available per agent too.
  await f.mode('different');
  const options=f.node('[data-role-choice="eval"]').innerHTML;
  assert.ok(options.includes('value="key:from-different"')&&options.includes('value="key:from-all"'));
  // Switching keys in "all" remembers each key's model; removing the selected key falls back to another.
  await f.mode('all');await f.sharedKey('from-different');await f.input('[data-key-model]','model-a');
  await f.sharedKey('from-all');assert.equal(f.node('[data-key-model]').value,'deepseek-chat');
  await f.sharedKey('from-different');assert.equal(f.node('[data-key-model]').value,'model-a');
  await f.mode('different');await f.remove('from-different');await f.mode('all');
  assert.equal(f.node('[data-shared-key]').value,'from-all');
});

test('a provider that switches thinking off says so in the saved-key line, the role source and the summary',async()=>{
  const f=keyFixture();await f.flush();await f.mode('all');await saveKey(f,'deepseek','my-ds');await f.flush();
  assert.match(f.node('[data-keys-shared-saved]').innerHTML,/DeepSeek|deepseek[^<]* · thinking off · <span/);
  await f.mode('different');
  assert.match(f.node('[data-role-choice="dev"]').innerHTML,/My key · my-ds \(deepseek · thinking off\)/);
  // A provider without the switch stays unlabelled.
  const g=keyFixture();await g.flush();await g.mode('all');await saveKey(g,'openai','mine');await g.flush();
  assert.ok(!g.node('[data-keys-shared-saved]').innerHTML.includes('thinking'));assert.ok(!('thinking' in g.binding.summary().dev));
});
