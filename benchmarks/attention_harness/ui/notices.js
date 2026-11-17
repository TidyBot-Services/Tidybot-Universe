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
  // 11-18 (user, ui改动): no Notices button any more. Every error still shows, where it belongs:
  //   run / system / model problems -> the strip under the top bar (only while there is one),
  //   Memory: the run's own Memory errors show in its 02 section; of the global ones only 'Memory service down' reaches the strip (11-18 user: no Memory page),
  //   the page failing to load something -> a small toast at the bottom right, gone once it loads again.
  function stripRows(rows,{memory=false}={}) {
    return sortNotices(rows).filter(n=>(n.level==='error'||n.level==='warning')&&n.source!=='client'&&(memory||n.source!=='memory'||n.id==='memory-down'));
  }
  function renderStrip(rows,{canRunAgain=false}={}) {
    if(!rows.length)return '';
    const shown=rows.slice(0,3),more=rows.length-shown.length;
    const runError=rows.some(n=>n.level==='error'&&n.source==='run');
    const items=shown.map(n=>`<li data-level="${escape(n.level)}"><strong>${escape(n.title)}${n.detail?':':''}</strong> ${escape(n.detail)} <small>${escape(n.hint)}</small></li>`).join('');
    return `<ul>${items}${more>0?`<li class="ab-strip-more">+${more} more</li>`:''}</ul>${runError&&canRunAgain?'<button type="button" data-notice-run-again>Run again with the same settings</button>':''}`;
  }
  function toastHost() {
    let host=document.querySelector('.ab-notice-toasts');
    if(!host){host=document.createElement('div');host.className='ab-notice-toasts';host.setAttribute('role','status');document.body.append(host)}
    return host;
  }
  function createNotices({mount,banner,experiment,run,canRunAgain,onRunAgain,memoryInStrip=false}) {
    if(mount&&!banner){banner=document.createElement('div');banner.className='ab-run-notice-banner';banner.hidden=true;(mount.closest('header')||mount).after(banner)}
    const button=document.createElement('button');button.type='button';button.className='ab-notices-button';button.setAttribute('aria-label','Notices');button.setAttribute('aria-expanded','false');button.innerHTML='<span aria-hidden="true">♢</span> Notices <b hidden></b>';
    const panel=document.createElement('section');panel.className='ab-notices-panel';panel.hidden=true;panel.innerHTML='<h2>Notices</h2><ol></ol>';
    // Kept off the page: the button and panel are no longer shown (11-18), the strip/dot/toast replace them.

    let server=[],client=new Map(),known=new Set(),first=true,pulseTimer=null;
    function render() {
      const result=renderNotices([...server,...client.values()],{banner:!!banner,canRunAgain:!!canRunAgain?.()});
      button.querySelector('b').hidden=result.count===0;button.querySelector('b').textContent=result.count||'';
      button.dataset.level=result.severity;panel.querySelector('ol').innerHTML=result.list||'<li class="ab-notices-empty">No notices right now.</li>';
      const all=[...server,...client.values()],strip=renderStrip(stripRows(all,{memory:memoryInStrip}),{canRunAgain:!!canRunAgain?.()});
      if(banner){banner.hidden=!strip;banner.innerHTML=strip;banner.querySelector('[data-notice-run-again]')?.addEventListener('click',onRunAgain)}
      const failed=[...client.values()],toasts=typeof document.querySelector==='function'?toastHost():null;
      if(toasts){toasts.hidden=!failed.length;toasts.title='Click to dismiss';toasts.onclick=()=>{client.clear();render()};toasts.innerHTML=failed.map(n=>`<p><strong>${escape(n.title)}</strong> ${escape(n.hint)}</p>`).join('');}
      const newErrors=server.filter(n=>n.level==='error'&&!known.has(n.id));
      if(!first&&newErrors.length){button.classList.remove('pulse');void button.offsetWidth;button.classList.add('pulse');clearTimeout(pulseTimer);pulseTimer=setTimeout(()=>button.classList.remove('pulse'),2500)}
      known=new Set(server.map(n=>n.id));first=false;
    }
    button.onclick=()=>{panel.hidden=!panel.hidden;button.setAttribute('aria-expanded',String(!panel.hidden))};
    function clientError(thing) {const label=String(thing||'page data').replace(/[^a-zA-Z ]/g,'').slice(0,40)||'page data';const id='client-'+label;if(!client.has(id))client.set(id,{id,level:'warning',source:'client',title:`The page could not load ${label}`,detail:'Please try again in a moment.',hint:'Refresh the page if this continues.',since:new Date().toISOString()});render()}
    function clientOk(thing) {if(client.delete('client-'+thing))render()}
    async function poll() {try{const exp=experiment?.(),selectedRun=run?.();const query=exp?'?exp='+encodeURIComponent(exp):selectedRun?'?run='+encodeURIComponent(selectedRun):'';const response=await global.fetch('/api/notices'+query,{cache:'no-store',credentials:'same-origin'});if(!response.ok)throw Error('notices unavailable');const payload=await response.json();server=Array.isArray(payload.notices)?payload.notices:[];clientOk('notices');render()}catch(_){clientError('notices')}finally{setTimeout(poll,document.hidden?30000:10000)}}
    render();poll();
    return {clientError,clientOk,button,panel,update:render};
  }
  function installFetchCapture(controller) {
    const original=global.fetch.bind(global);
    global.fetch=async function(url,options) {const thing=routeThing(url);try{const response=await original(url,options);if(response.status>=500&&!(String(url?.url||url).includes('/api/orchestrator/snapshot')&&response.status===503))controller.clientError(thing);else if(response.ok)controller.clientOk(thing);return response}catch(error){controller.clientError(thing);throw error}};
    global.addEventListener('error',()=>controller.clientError('page display'));
    // A page switch the browser chose to skip (a view transition) is not a failed load.
    global.addEventListener('unhandledrejection',e=>{const r=e?.reason;if(r?.name==='AbortError'&&/transition/i.test(String(r.message)))return;controller.clientError('page data')});
  }
  const api={sortNotices,renderNotices,renderStrip,stripRows,routeThing,createNotices,installFetchCapture};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  global.AttentionNotices=api;
})(typeof window!=='undefined'?window:globalThis);
