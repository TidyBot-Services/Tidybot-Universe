(function (global) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const priority = {error:0, warning:1, info:2};
  function sortNotices(rows) {return [...rows].sort((a,b)=>(priority[a.level]??2)-(priority[b.level]??2)||Date.parse(b.since||0)-Date.parse(a.since||0)).slice(0,20)}
  function timeLabel(value) {const date=new Date(value);return Number.isNaN(date.getTime())?'':`since ${date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})}`}
  function renderNotices(rows, {banner=false, canRunAgain=false}={}) {
    const notices=sortNotices(rows), severity=notices.some(n=>n.level==='error')?'error':notices.length?'warning':'none';
    const list=notices.map(n=>`<li class="ab-notice ${escape(n.level)}"><strong>${escape(n.title)}</strong><span>${escape(n.detail)}</span><small>${escape(n.hint)}</small><time>${escape(timeLabel(n.since))}</time></li>`).join('');
    const runError=notices.find(n=>n.level==='error'&&n.source==='run'&&n.title.startsWith('Run stopped'))||notices.find(n=>n.level==='error'&&n.source==='run');
    return {count:notices.length,severity,list, banner:banner&&runError?`<strong>${escape(runError.title)}:</strong> ${escape(runError.detail)} ${escape(runError.hint)}${canRunAgain?' <button type="button" data-notice-run-again>Run again with the same settings</button>':''}`:''};
  }
  function routeThing(url) {
    const path=String(url?.url||url||'').split('?')[0];
    if(path.includes('/api/orchestrator/'))return 'orchestrator';
    if(path.includes('/api/notices'))return 'notices';
    if(path.includes('/api/dev-v2/memory-monitor')||path.includes('/api/dev-memories'))return 'Memory';
    if(path.includes('/api/run-options'))return 'run settings';
    if(path.includes('/api/runs')||path.includes('/api/dev-v2/')||path.includes('/api/dev/'))return 'run data';
    return 'page data';
  }
  function createNotices({mount,banner,experiment,run,canRunAgain,onRunAgain}) {
    const button=document.createElement('button');button.type='button';button.className='ab-notices-button';button.setAttribute('aria-label','Notices');button.setAttribute('aria-expanded','false');button.innerHTML='<span aria-hidden="true">♢</span> Notices <b hidden></b>';
    const panel=document.createElement('section');panel.className='ab-notices-panel';panel.hidden=true;panel.innerHTML='<h2>Notices</h2><ol></ol>';
    mount.append(button,panel);
    let server=[],client=new Map(),known=new Set(),first=true,pulseTimer=null;
    function render() {
      const result=renderNotices([...server,...client.values()],{banner:!!banner,canRunAgain:!!canRunAgain?.()});
      button.querySelector('b').hidden=result.count===0;button.querySelector('b').textContent=result.count||'';
      button.dataset.level=result.severity;panel.querySelector('ol').innerHTML=result.list||'<li class="ab-notices-empty">No notices right now.</li>';
      if(banner){banner.hidden=!result.banner;banner.innerHTML=result.banner;banner.querySelector('[data-notice-run-again]')?.addEventListener('click',onRunAgain)}
      const newErrors=server.filter(n=>n.level==='error'&&!known.has(n.id));
      if(!first&&newErrors.length){button.classList.remove('pulse');void button.offsetWidth;button.classList.add('pulse');clearTimeout(pulseTimer);pulseTimer=setTimeout(()=>button.classList.remove('pulse'),2500)}
      known=new Set(server.map(n=>n.id));first=false;
    }
    button.onclick=()=>{panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden))};
    function clientError(thing) {const label=String(thing||'page data').replace(/[^a-zA-Z ]/g,'').slice(0,40)||'page data';const id='client-'+label;if(!client.has(id))client.set(id,{id,level:'warning',source:'system',title:`The page could not load ${label}`,detail:'Please try again in a moment.',hint:'Refresh the page if this continues.',since:new Date().toISOString()});render()}
    function clientOk(thing) {if(client.delete('client-'+thing))render()}
    async function poll() {try{const exp=experiment?.(),selectedRun=run?.();const query=exp?'?exp='+encodeURIComponent(exp):selectedRun?'?run='+encodeURIComponent(selectedRun):'';const response=await global.fetch('/api/notices'+query,{cache:'no-store',credentials:'same-origin'});if(!response.ok)throw Error('notices unavailable');const payload=await response.json();server=Array.isArray(payload.notices)?payload.notices:[];clientOk('notices');render()}catch(_){clientError('notices')}finally{setTimeout(poll,document.hidden?30000:10000)}}
    render();poll();
    return {clientError,clientOk,button,panel,update:render};
  }
  function installFetchCapture(controller) {
    const original=global.fetch.bind(global);
    global.fetch=async function(url,options) {const thing=routeThing(url);try{const response=await original(url,options);if(response.status>=500&&!(String(url?.url||url).includes('/api/orchestrator/snapshot')&&response.status===503))controller.clientError(thing);else if(response.ok)controller.clientOk(thing);return response}catch(error){controller.clientError(thing);throw error}};
    global.addEventListener('error',()=>controller.clientError('page display'));
    global.addEventListener('unhandledrejection',()=>controller.clientError('page data'));
  }
  const api={sortNotices,renderNotices,routeThing,createNotices,installFetchCapture};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  global.AttentionNotices=api;
})(typeof window!=='undefined'?window:globalThis);
