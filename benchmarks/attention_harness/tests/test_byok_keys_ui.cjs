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
  let timer, failSave=false;
  let creds=[];
  const presets=['deepseek','openai','parcc','openai-compatible'].map(id=>({id,label:id,needs_key:true,suggested_models:['deepseek-chat']}));
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
      else if(options.method==='GET')value=creds;
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
  return {binding,form,node,events,requests,context,flush,
    mode:async value=>emit('change',modes.find(m=>m.value===value)),
    save:()=>emit('click',node('[data-key-save]')),
    input:async(selector,value)=>{const n=node(selector);n.value=value;await emit('input',n);},
    pick:async(role,value)=>{const n=node(`[data-role-choice="${role}"]`);n.dataset.roleChoice=role;n.value=value;n.closest=q=>q==='[data-role-choice]'?n:null;await emit('change',n);},
    expire(){creds.forEach(c=>c.expires_at=0);dialog.open=true;timer();},
    failSave(){failSave=true;},
    remove:name=>{const n=node('[data-key-remove]');n.dataset.keyRemove=name;return emit('click',n);},
  };
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
  const mine={model:'deepseek-chat',keyName:'my-ds'},route={credential:'my-ds',provider:'deepseek',model:'deepseek-chat'};
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
  assert.match(read(),/Dev deepseek-chat \(your key my-ds\)/);assert.equal(read().split('\n').length,2);
  assert.ok(!read().includes('test-only-secret'));
  assert.match(read(),/Eval deepseek-chat \(your key my-ds\) · Advisor deepseek-chat \(your key my-ds\)/);
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
