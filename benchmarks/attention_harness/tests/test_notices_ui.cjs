const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'ui/notices.js'),'utf8');
const api=require('../ui/notices.js');
const at=(second)=>`2026-10-22T14:00:${String(second).padStart(2,'0')}Z`;
const item=(id,level,source,second)=>({id,level,source,title:id,detail:'One detail',hint:'One hint',since:at(second)});

test('notice sorting, escaped panel, count and selected run banner',()=>{
  const rows=[item('old','warning','model',1),item('new','error','run',3),item('<script>','warning','system',4),item('Run stopped','error','run',2)];
  assert.deepEqual(api.sortNotices(rows).map(n=>n.id),['new','Run stopped','<script>','old']);
  const rendered=api.renderNotices(rows,{banner:true,canRunAgain:true});
  assert.equal(rendered.count,4);assert.equal(rendered.severity,'error');
  assert.match(rendered.list,/&lt;script&gt;/);assert.match(rendered.list,/since /);
  assert.match(rendered.banner,/Run again with the same settings/);
  assert.match(rendered.banner,/Run stopped/);
  assert.equal(api.renderNotices([]).count,0);
  assert.equal(api.renderNotices([], {banner:true}).banner,'');
});

test('vm bell hides zero, displays panel, captures and throttles client warnings',async()=>{
  class Element {
    constructor(){this.hidden=false;this.dataset={};this.children=[];this.innerHTML='';this.textContent='';this.attrs={};this.classList={add(){},remove(){}};this.badge={hidden:true,textContent:''};this.list={innerHTML:''};this.offsetWidth=10}
    append(...xs){this.children.push(...xs)}
    setAttribute(k,v){this.attrs[k]=v}
    querySelector(k){return k==='b'?this.badge:k==='ol'?this.list:null}
  }
  const context=vm.createContext({document:{createElement:()=>new Element(),hidden:false},fetch:async()=>({ok:true,json:async()=>({notices:[]})}),setTimeout:()=>1,clearTimeout:()=>{},Date,encodeURIComponent});
  vm.runInContext(source,context);
  const mount=new Element(),banner=new Element();
  const controller=context.AttentionNotices.createNotices({mount,banner,experiment:()=>null});
  await Promise.resolve();await Promise.resolve();
  assert.equal(controller.button.badge.hidden,true);
  controller.clientError('run data');controller.clientError('run data');
  assert.equal(controller.button.badge.textContent,1);
  assert.match(controller.panel.list.innerHTML,/The page could not load run data/);
  controller.button.onclick();assert.equal(controller.panel.hidden,false);
  controller.clientOk('run data');assert.equal(controller.button.badge.hidden,true);
  assert.equal(api.routeThing('/api/dev-v2/memory-monitor?exp=x'),'Memory');
});

test('fetch capture reports 5xx once and ignores expected orchestrator 503',async()=>{
  const warnings=[];
  const context=vm.createContext({fetch:async url=>({ok:false,status:url.includes('orchestrator')?503:502}),addEventListener(){}});
  vm.runInContext(source,context);
  context.AttentionNotices.installFetchCapture({clientError:thing=>warnings.push(thing),clientOk(){}});
  await context.fetch('/api/runs');
  await context.fetch('/api/orchestrator/snapshot');
  assert.deepEqual(warnings,['run data']);
});

test('sidebar pills have one status layer and stay on one line at phone width',()=>{
  const live=fs.readFileSync(path.join(root,'ui/live.js'),'utf8');
  const css=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
  assert.match(live,/class="ab-run-status" data-active=/);
  assert.doesNotMatch(live,/ab-run-status[^`]*<b data-executing-badge>/);
  assert.match(css,/\.ab-task-executing\{[^}]*display:inline-flex[^}]*height:20px[^}]*white-space:nowrap/);
  assert.match(css,/\.ab-sidebar \.ab-run-status\{[^}]*display:inline-flex[^}]*height:20px[^}]*white-space:nowrap/);
  assert.match(css,/\.ab-sidebar \[data-run-name\]\{[^}]*text-overflow:ellipsis/);
});
