(function (global) {
  'use strict';
  const TD={name:'dark',amber:'#ffd23f',amberHex:0xffd23f,onAmber:'#1a1400',flashLine:'#fff',ink:'#ffffff',mut:'#a3a199',mut2:'#8e8c85',mut3:'#7a7870',card:'#14181d',cardLine:'#3a3f45',pill:'#0d0f12',pillInk:'#ffffff',stroke:'#6f9bff',coreGlow2:'#5f8dff',stWait:'#3a3f45',stGo:'#6f93ff',pass:'#3ddc84',fail:'#ff5d5d',bk:['#dfe7ff','#7f9dff','#2c4aa8'],ec:{u:0x3ddc84,x:0xff5d5d,k:0x9db4ff},sc:{u:0x2fd37a,x:0xff4d4d,k:0x6f93ff},efc:{u:'#3ddc84',x:'#ff5d5d',k:'#9db4ff',v:'#c79bff',r:'#51ddd0'},off:0x59606e,vc:[0x5fd4ff,0x7fa8ff,0x9a8cff,0xc58cff,0xff8cd0],ringOp:.45,shell:0x8a96aa,shellOp:.07,fill:0x161b22,fillOp:.18,core:0x2d63d9,coreGlow:0x5f8dff,additive:true,bg:0x0d0f12,gc:['#3d8bf0','#25c48d','#e977a6','#8f7cf0','#6fbf3a','#9aa1ad']};
  // One drawing palette: the canvas is an instrument surface and ignores Light | Dark; only the page around it follows the theme.
  const theme=TD;
  const LAYERS=['run','scene','task','family'];
  const layerName=layer=>layer[0].toUpperCase()+layer.slice(1);
  const color=kind=>kind==='off'?`#${theme.off.toString(16).padStart(6,'0')}`:theme.efc[{helps:'u',not_helps:'x',not_tested:'k',unvalidated:'v',run_note:'r'}[kind]||'k'];
  const groupColor=i=>theme.gc[i%6];
  const versionColor=i=>`hsl(${Math.round((195+i*137.5)%360)}, ${theme===TD?84:70}%, ${theme===TD?68:44}%)`;
  const versionStroke=state=>state==='passed'?theme.pass:state==='failed'?theme.fail:state==='waiting'?theme.stWait:theme.stGo;
  const orbitRadii=count=>Array.from({length:count},(_,i)=>count===1?.60:.34+i*.52/(count-1));
  const flowLayout=width=>width<700?'vertical':'wide';
  const phoneCallouts=(items,width)=>width<700?[...items].sort((a,b)=>(Number(b.n.last_used_at)||0)-(Number(a.n.last_used_at)||0)).slice(0,4):items.slice(0,10);
  function cameraFitDistance(width,height){const aspect=Math.max(1,width)/Math.max(1,height),vertical=42*Math.PI/360,horizontal=Math.atan(Math.tan(vertical)*aspect);return 1.28/(Math.sin(Math.min(vertical,horizontal))*.94)}
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function adapt(payload){
    const rows=[...(payload.run_notes||[]).map(n=>({...n,run_note:true,group:'This run',status:n.off?'off':'active',unvalidated:true,reads:n.uses,uses:{grants:n.uses,failure_gone:n.failure_gone}})),...(payload.notes||[])].slice(0,600);
    const groupCounts=new Map();
    rows.forEach(row=>{const key=String(row.group||'General').trim().toLowerCase()||'general';const old=groupCounts.get(key)||{name:row.group||'General',count:0};old.count++;groupCounts.set(key,old)});
    const main=[...groupCounts].filter(([key])=>key!=='other').sort((a,b)=>b[1].count-a[1].count||a[0].localeCompare(b[0])).slice(0,5).map(([key])=>key);
    const groups=main.map((key,i)=>({key,name:groupCounts.get(key).name,color:groupColor(i)}));
    if(groupCounts.size>main.length)groups.push({key:'other',name:'Other',color:groupColor(5)});
    const versions=(payload.run?.versions||[]).map(v=>({...v,n:Number(v.n),note_ids:(v.note_ids||[]).slice(0,600)}));
    const byId=new Map();
    const notes=rows.map((row,i)=>{
      const key=String(row.group||'General').trim().toLowerCase()||'general';
      const unvalidated=row.status==='unvalidated'||row.unvalidated===true;
      const uses=row.uses&&typeof row.uses==='object'?{grants:Math.max(0,Number(row.uses.grants)||0),failure_gone:Math.max(0,Number(row.uses.failure_gone)||0)}:null;
      const note={id:String(row.id||''),title:String(row.title||'Untitled note').slice(0,80),summary:row.summary?String(row.summary).slice(0,180):null,
        group:main.includes(key)?groupCounts.get(key).name:'Other',groupIndex:main.includes(key)?main.indexOf(key):5,
        status:row.status==='unvalidated'?'active':['active','off','expired','withdrawn'].includes(row.status)?row.status:'off',
        layer:row.run_note?'run':row.layer===null?null:LAYERS.includes(row.layer)?row.layer:row.reference?'task':'scene',path:String(row.path||''),
        run_note:row.run_note===true,cell:row.cell,help_version:row.help_version,
        effect:row.run_note?'run_note':unvalidated&&row.status!=='off'?'unvalidated':row.test_seeded?'not_tested':['helps','not_helps','not_tested'].includes(row.effect)?row.effect:'not_tested',
        lift_pt:row.test_seeded?null:Number.isFinite(row.lift_pt)?row.lift_pt:null,reads:Math.max(0,Number(row.reads)||0),
        last_used_at:row.last_used_at||null,test_seeded:row.test_seeded===true,index:i,flash:0,
        unvalidated,uses,expires_in_days:Number.isFinite(row.expires_in_days)?Math.max(0,Math.round(row.expires_in_days)):null,
        reference:row.reference===true,scene_seed:Number.isInteger(row.scene_seed)?row.scene_seed:null};
      byId.set(note.id,note);return note;
    });
    notes.forEach(note=>{note.versions=versions.filter(v=>v.note_ids.includes(note.id)).map(v=>v.n);note.version=note.versions.at(-1)||0});
    return {run:payload.run||{state:'unknown',versions:[]},versions,notes,groups,byId,events:(payload.events||[]).slice(-500),warnings:payload.warnings||[]};
  }
  function eventKey(event){return event.seq!=null?String(event.seq):JSON.stringify([event.ts,event.type,event.version,event.note_ids]);}
  function newEvents(previous,next,now=Date.now()/1000){
    if(!previous)return (next||[]).filter(e=>['planner_memory_retrieved','planner_memory_run_notes_delivered'].includes(e.type)&&Number(e.ts)>now-4.5&&Number(e.ts)<=now);
    const seen=new Set(previous.map(eventKey));return (next||[]).filter(e=>!seen.has(eventKey(e)));
  }
  function visibleNotes(data,version,usedOnly,layer='all'){return data.notes.filter(note=>(layer==='all'||note.layer===layer)&&(!usedOnly||note.versions.length>0)&&(!version||note.versions.includes(version)));}
  function versionCount(data,v,version,usedOnly,layer){return visibleNotes(data,version,usedOnly,layer).filter(n=>v.note_ids.includes(n.id)).length;}
  function layerLegend(data){return LAYERS.map(layer=>`<span><i class="layer-badge">${layer[0].toUpperCase()}</i>${layerName(layer)}<b>${layer==='family'&&!data.run.family_reference?'off':data.run.layer_counts?.[layer]??data.notes.filter(n=>n.layer===layer).length}</b></span>`).join('');}
  function ago(ts,now=Date.now()/1000){if(!ts)return 'never';const s=Math.max(0,Math.round(now-Number(ts)));return s<60?`${s} s ago`:s<3600?`${Math.floor(s/60)} min ago`:`${Math.floor(s/3600)} h ago`;}
  function cardHtml(note,canOperate=false,demo=false){
    if(!note)return '';
    const layer=note.run_note?'run':note.layer;
    const layerChip=LAYERS.includes(layer)?`<span class="layer-chip"><i class="layer-badge">${layer[0].toUpperCase()}</i>${layerName(layer)} layer</span>`:'';
    const pathLine=note.path?`<p class="note-path">${escapeHtml(note.path)}</p>`:'';
    if(note.run_note){
      return `<button class="close" type="button" data-close aria-label="Close">×</button><h4>${escapeHtml(note.title)}</h4>${pathLine}<div class="chips">${layerChip}<span class="r">This run (v${note.help_version})</span><span>Unvalidated · not counted in results</span>${note.status==='off'?'<span>Off</span>':''}</div><div class="meta" data-uses>This run · used ${note.uses?.grants??0} · failure gone ${note.uses?.failure_gone??0}</div>${canOperate&&!demo&&note.status!=='off'?'<button type="button" data-run-note-off>Turn off for this run</button>':''}<details class="details"><summary>Details</summary><p>Note ID: ${escapeHtml(note.id)}</p></details>`;
    }
    const effect=note.status==='active'?note.effect:'off';
    const label={helps:'Helps',not_helps:'Does not help',not_tested:'Not tested',unvalidated:'Unvalidated',off:'Off'}[effect];
    const seed=note.scene_seed==null?'':` ${note.scene_seed}`;
    const scope=note.layer==='family'?'Reference · other task':note.reference?(note.unvalidated?`Unvalidated · from scene${seed}`:note.test_seeded?`Reference · test note from scene${seed}`:`Reference · validated on scene${seed}`):'';
    const excluded=note.unvalidated||note.reference?'<span class="nr">Not counted in results</span>':'';
    const uses=note.unvalidated?`Used ${note.uses?.grants??0} · failure gone ${note.uses?.failure_gone??0}${note.expires_in_days==null?'':` · Expires in ${note.expires_in_days} day${note.expires_in_days===1?'':'s'}`}`:'';
    const where=note.version?`Used by Dev ${note.versions.map(v=>'v'+v).join(', ')}`:'Not used in this run';
    const statusText={off:'Off',expired:'Expired',withdrawn:'Withdrawn'}[note.status]||where;
    return `<button class="close" type="button" data-close aria-label="Close">×</button><h4>${escapeHtml(note.title)}</h4><p>${escapeHtml(note.summary||'No summary yet.')}</p>${pathLine}<div class="chips">${layerChip}<span class="${effect==='helps'?'u':effect==='not_helps'?'x':effect==='unvalidated'?'v':'k'}">${label}${note.lift_pt!=null&&effect==='helps'?` (+${escapeHtml(note.lift_pt)} pt)`:''}</span><span>${escapeHtml(note.status==='active'?where:statusText)}</span><span>${escapeHtml(note.group)}</span>${note.test_seeded?'<span>Test note</span>':''}${scope?`<span class="ref">${escapeHtml(scope)}</span>`:''}${excluded}</div>${uses?`<div class="meta" data-uses>${escapeHtml(uses)}</div>`:''}<div class="meta">Read ${note.reads} times · last used ${ago(note.last_used_at)}</div>${demo?'':`<details class="manage"><summary>Manage…</summary><form data-manage><label>Actor<input name="actor" maxlength="200" required></label><label>Reason<input name="reason" maxlength="200" required></label><label>Expiry (local time)<input name="expires_at" type="datetime-local"></label><small>Setting an expiry and withdrawing a note cannot be undone. Both ask for confirmation.</small>${canOperate?'':'<small>Actions are unavailable because the operator key is not configured on this server.</small>'}<div class="actions">${[['disable','Turn off'],['enable','Turn back on'],['expiry','Set expiry…'],['rollback','Withdraw…']].map(([a,t])=>`<button type="button" data-action="${a}" ${canOperate?'':'disabled'}>${t}</button>`).join('')}</div></form></details><details class="details"><summary>Details</summary><p>Note ID: ${escapeHtml(note.id)}</p></details>`}`;
  }
  function manageRequest(note,action,actor,reason,expiryRaw,confirmFn=global.confirm){
    if(!actor?.trim()||!reason?.trim())throw Error('Actor and reason are required');
    const body={actor:actor.trim(),reason:reason.trim()};
    if(action==='expiry'){const value=new Date(expiryRaw).getTime()/1000;if(!expiryRaw||!Number.isFinite(value)||value<=Date.now()/1000)throw Error('Expiry must be a future local date and time');body.expires_at=value;}
    const irreversible=action==='expiry'||action==='rollback';
    const label={expiry:'Set expiry',rollback:'Roll back',disable:'Disable',enable:'Enable'}[action];
    if(!label)throw Error('Unknown action');
    if(!confirmFn(`${label} ${note.title}?\nActor: ${body.actor}\nReason: ${body.reason}${irreversible?'\nExpiry and rollback cannot be undone.':''}`))return null;
    return {url:`/api/dev-memories/${encodeURIComponent(note.id)}/${action}`,options:{method:'POST',headers:{'Content-Type':'application/json','X-Attention-Token':global.document?.body?.dataset?.controlToken||''},body:JSON.stringify(body)}};
  }
  function runNoteOffRequest(experimentId,note){
    return {url:`/api/dev-v2/experiments/${encodeURIComponent(experimentId)}/run-note-off`,options:{method:'POST',headers:{'Content-Type':'application/json','X-Attention-Token':global.document?.body?.dataset?.controlToken||''},body:JSON.stringify({cell:note.cell,note_id:note.id})}};
  }
  function memoryScopeText(run) {
    const scope=run?.memory_scope;
    if(scope?.level!=='reference_only')return '';
    return scope.reason==='scene_contract_missing'?'Memory unavailable — this seed has no scene contract':'Memory: reference only — this seed has no scene contract';
  }
  const warningText=warnings=>Array.isArray(warnings)&&warnings.length?'Some note details are unavailable right now.':'';
  const api={layerLegend,versionCount,runNoteOffRequest,feedLabel,themes:{dark:TD,light:TD},memoryScopeText,adapt,newEvents,visibleNotes,cardHtml,manageRequest,ago,warningText,orbitRadii,flowLayout,phoneCallouts,versionColor,cameraFitDistance};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(!global.document)return;
  const $=id=>document.getElementById(id),reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
  function applyTheme(name){const dark=name==='dark';document.body.classList.toggle('dark',dark);$('monitor').classList.toggle('light',!dark);render();}
  const shell=global.AttentionShell;
  let savedTheme='light';try{savedTheme=localStorage.getItem('attention.theme')||localStorage.getItem('attention.memory.theme')||'light'}catch(_){}
  shell?.onTheme(applyTheme);
  const hash=location.hash,match=/^#exp=(devexp-[a-f0-9]{24})$/.exec(hash),demo=/^#demo=(40|150|400)(?:&n=(\d+))?$/.exec(hash);
  const demoVersions=demo?Math.max(1,Math.min(12,Number(demo[2]||5))):5;
  let experimentId=match?.[1]||null,data=null,previous=null,view='2d',version=0,layer='all',usedOnly=false,selected=null,canOperate=false,globe=null,flashUntil=new Map(),pollTimer=null;
  let readFlights=[],demoTick=0,demoSeq=0;
  const noticeController=global.AttentionNotices.createNotices({mount:$('memory-notices'),experiment:()=>experimentId});
  global.AttentionNotices.installFetchCapture(noticeController);
  if(shell&&$('side')){
    shell.drawer({button:$('tasks'),wrap:$('wrap'),key:'attention.memory.tasks',open:false});
    $('tasks').addEventListener('click',()=>setTimeout(()=>global.dispatchEvent(new Event('resize')),360));
    const rail=async()=>{try{const r=await fetch('/api/home/status',{cache:'no-store'});const status=await r.json();if(!r.ok)throw Error(status.error||'HTTP '+r.status);shell.taskRail({list:$('recent'),meta:$('side-meta'),status,href:x=>'/ui/memory.html#exp='+encodeURIComponent(x.experiment_id),current:experimentId})}catch(e){$('side-meta').textContent='Could not read the run list: '+e.message}};
    rail();setInterval(rail,30000);
  }
  $('real-data').hidden=!demo;
  applyTheme(shell?shell.theme():savedTheme);
  if(experimentId)$('back').href='/ui/#exp='+encodeURIComponent(experimentId);
  function setView(next){view=next;const is3=next==='3d'&&!!globe;$('globe').style.display=is3?'':'none';$('labels').style.display=is3?'':'none';$('flow').classList.toggle('on',!is3);$('views').querySelectorAll('button').forEach(b=>b.classList.toggle('act',b.dataset.view===(is3?'3d':'2d')));renderFlow();}
  $('views').onclick=e=>{const v=e.target.dataset.view;if(v)setView(v)};
  global.addEventListener('resize',()=>renderFlow());
  $('filters').onclick=e=>{const b=e.target.closest('[data-version]');if(!b)return;version=Number(b.dataset.version);render();};
  $('layer-filters').onclick=e=>{const b=e.target.closest('[data-layer]');if(!b)return;layer=b.dataset.layer;render();};
  $('used-toggle').onclick=()=>{usedOnly=!usedOnly;$('used-toggle').textContent=usedOnly?'Show all notes':'Show only used notes';render();};
  $('card').onclick=async e=>{
    if(e.target.closest('[data-close]')){selected=null;$('card').classList.remove('on');$('hint').style.display='';globe?.reset();return;}
    const offButton=e.target.closest('[data-run-note-off]');
    if(offButton&&selected?.run_note&&data?.run.can_operate_run_notes){
      try{offButton.disabled=true;const req=runNoteOffRequest(experimentId,selected);const response=await fetch(req.url,{...req.options,credentials:'same-origin'});if(!response.ok)throw Error('Run note update failed');await poll();}catch(_){$('error').textContent='Run note could not be turned off.';}finally{offButton.disabled=false;}return;
    }
    const button=e.target.closest('[data-action]');if(!button||!selected)return;
    const form=button.closest('form');try{if(!form.elements.actor.value.trim()||!form.elements.reason.value.trim()){form.reportValidity();return;}const req=manageRequest(selected,button.dataset.action,form.elements.actor.value,form.elements.reason.value,form.elements.expires_at.value);if(!req)return;button.disabled=true;const response=await fetch(req.url,{...req.options,credentials:'same-origin'});const result=await response.json();if(!response.ok)throw Error(result.error||`HTTP ${response.status}`);$('error').textContent='Note updated';await poll();}catch(error){$('error').textContent='Memory data is unavailable right now.';noticeController.clientError('Memory');}finally{button.disabled=false;}
  };
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){selected=null;$('card').classList.remove('on');$('hint').style.display='';globe?.reset()}});
  function select(note){selected=note;$('card').innerHTML=cardHtml(note,note.run_note?data?.run.can_operate_run_notes:canOperate,!!demo);$('card').classList.add('on');$('hint').style.display='none';globe?.select(note);}
  function render(){if(!data)return;
    $('warning').textContent=warningText(data.warnings);
    const scopeBanner=$('memory-scope');
    scopeBanner.textContent=memoryScopeText(data.run);
    scopeBanner.hidden=!scopeBanner.textContent;
    const notes=data.notes,active=notes.filter(n=>n.status==='active'),used=active.filter(n=>n.version>0),counts={helps:0,not_helps:0,not_tested:0,unvalidated:0,run_note:0,off:notes.length-active.length};
    active.forEach(n=>counts[n.effect]++);$('used-count').textContent=used.length;
    Object.keys(counts).forEach(k=>{const el=$('count-'+k.replace('_','-'));if(el)el.textContent=counts[k]});
    const kinds=['helps','not_helps','not_tested','unvalidated','run_note'];kinds.forEach(k=>{$('bar-'+k.replace('_','-')).style.width=(used.length?used.filter(n=>n.effect===k).length/used.length*100:0)+'%'});
    $('layer-legend').innerHTML=layerLegend(data);
    $('layer-filters').innerHTML=['all',...LAYERS].map(l=>`<button class="vc ${layer===l?'on':''}" data-layer="${l}">${layerName(l)}</button>`).join('');
    $('filters').innerHTML=`<button class="vc ${version===0?'on':''}" data-version="0">All</button>`+data.versions.map(v=>`<button class="vc ${version===v.n?'on':''}" data-version="${v.n}">v${v.n}<b>${versionCount(data,v,version,usedOnly,layer)}</b></button>`).join('');
    $('empty').hidden=notes.length>0;renderFlow();globe?.update(data,version,usedOnly,flashUntil,layer);
    if(selected){selected=data.byId.get(selected.id)||null;if(selected){$('card').innerHTML=cardHtml(selected,selected.run_note?data?.run.can_operate_run_notes:canOperate,!!demo)}else $('card').classList.remove('on')}
  }
  function versionStatus(value){return ({reading:'Reading notes',generating:'Generating code',running:'Running',finished:'Finished · outcome pending',passed:'Passed',failed:'Failed',waiting:'Waiting'})[value]||'Waiting'}
  function renderVerticalFlow(){
    const flow=$('flow'),now=Date.now(),visible=visibleNotes(data,version,usedOnly,layer),selectedId=selected?.id;
    const cx=195,cy=350,groupY=i=>38+i*44,slot=420/Math.max(1,data.versions.length),cardH=Math.min(58,slot-4),versionY=i=>440+i*slot,notePos=new Map();
    const gradient=(id,a,b,c)=>`<radialGradient id="${id}" cx="35%" cy="30%" r="80%"><stop stop-color="${a}"/><stop offset=".55" stop-color="${b}"/><stop offset="1" stop-color="${c}"/></radialGradient>`;
    let svg=`<defs>${gradient('ball-help','#c8ffe0',theme.pass,'#12703c')}${gradient('ball-harm','#ffd0d0',theme.fail,'#8a1f1f')}${gradient('ball-unknown',...theme.bk)}${gradient('ball-unvalidated','#efe2ff',theme.efc.v,'#4b2385')}${gradient('ball-run-note','#d4fff8',theme.efc.r,'#064d48')}${gradient('ball-off','#a9b0c0','#59606e','#2a2f3a')}${gradient('ball-gold','#fff6c4',theme.amber,'#b57c00')}${gradient('ball-core','#a9c4ff','#2d63d9','#10306e')}<radialGradient id="core-glow"><stop stop-color="${theme.coreGlow2}" stop-opacity=".55"/><stop offset="1" stop-color="${theme.coreGlow2}" stop-opacity="0"/></radialGradient></defs>`;
    svg+=`<g class="flow-head" fill="${theme.mut2}"><text x="16" y="23">1 · NOTES, BY GROUP</text><text x="${cx}" y="302" text-anchor="middle">2 · MEMORY SERVICE</text><text x="16" y="435">3 · DEV VERSIONS OF THIS RUN</text></g>`;
    let paths='',cards='',balls='';
    data.groups.forEach((g,gi)=>{const ns=visible.filter(n=>n.groupIndex===gi),y=groupY(gi),usedCount=ns.filter(n=>n.version).length,reads=ns.reduce((sum,n)=>sum+n.versions.length,0),hot=ns.some(n=>flashUntil.get(n.id)>now),width=2+Math.sqrt(reads)*1.8;
      paths+=`<path class="flow-connection ${hot?'hot':''}" d="M${cx},${y+36} C${cx+55},${y+65} ${cx-65},${cy-75} ${cx},${cy-36}" fill="none" stroke="${hot?theme.amber:groupColor(gi)}" stroke-opacity="${hot?1:.48}" stroke-width="${hot?width+1.5:width}"/>`;
      cards+=`<rect x="16" y="${y}" width="358" height="38" rx="9" fill="${theme.card}" stroke="${groupColor(gi)}" stroke-opacity=".65"/><text x="27" y="${y+15}" class="flow-title" fill="${theme.ink}">${escapeHtml(g.name)}</text><text x="364" y="${y+15}" text-anchor="end" class="flow-small" fill="${theme.mut}">${ns.length} notes · ${usedCount} used</text>`;
      const space=ns.length>90?5:ns.length>50?6:ns.length>30?8:11,cols=Math.floor(330/space);
      ns.forEach((n,i)=>{const x=30+(i%cols)*space,yy=y+25+Math.floor(i/cols)*Math.min(space,6),hotNote=flashUntil.get(n.id)>now,isUsed=n.version>0,isOff=n.status!=='active',r=selectedId===n.id?6:isOff?2:isUsed?4:2.5;
        notePos.set(n.id,{x,y:yy});const fill=hotNote?'ball-gold':isOff?'ball-off':n.effect==='helps'?'ball-help':n.effect==='not_helps'?'ball-harm':n.effect==='unvalidated'?'ball-unvalidated':n.effect==='run_note'?'ball-run-note':'ball-unknown';
        balls+=`<circle data-note="${escapeHtml(n.id)}" cx="${x}" cy="${yy}" r="${r}" fill="url(#${fill})" opacity="${isOff?.65:isUsed||hotNote?1:.58}" class="flow-note"><title>${escapeHtml(n.title)}</title></circle>`;
      });
    });
    data.versions.forEach((v,i)=>{const y=versionY(i),mid=y+cardH/2,hot=v.note_ids.some(id=>flashUntil.get(id)>now),dim=version&&version!==v.n,stroke=versionStroke(v.state),width=2+Math.sqrt(v.note_ids.length)*1.8;
      paths+=`<path class="flow-connection ${hot?'hot':''}" d="M${cx},${cy+36} C${cx-65},${cy+84} ${cx+65},${mid-54} ${cx},${mid}" fill="none" stroke="${hot?theme.amber:theme.stroke}" stroke-opacity="${hot?1:dim?.14:.5}" stroke-width="${hot?width+1.5:width}"/>`;
      cards+=`<g opacity="${dim?.4:1}"><rect x="16" y="${y}" width="358" height="${cardH}" rx="${Math.min(9,cardH/3)}" fill="${theme.card}" stroke="${hot?theme.amber:version===v.n?theme.ink:theme.cardLine}"/><circle class="version-ring state-${escapeHtml(v.state)}" cx="39" cy="${mid}" r="${Math.min(15,cardH/2-3)}" fill="url(#ball-core)" stroke="${stroke}" stroke-width="2"/><text x="39" y="${mid+4}" text-anchor="middle" class="flow-small" fill="${theme.ink}">v${v.n}</text><text x="64" y="${mid+4}" class="flow-title" fill="${theme.ink}">Dev v${v.n}</text><text x="174" y="${mid+4}" class="flow-small" fill="${theme.mut}">${escapeHtml(versionStatus(v.state))}</text><text x="360" y="${mid+4}" text-anchor="end" class="flow-small" fill="${theme.mut}">${versionCount(data,v,version,usedOnly,layer)} used</text></g>`;
    });
    svg+=paths+cards+balls;
    const active=data.notes.filter(n=>n.status==='active'&&!n.run_note).length,runNotes=data.notes.filter(n=>n.status==='active'&&n.run_note).length,coreHot=[...flashUntil.values()].some(t=>t>now);
    svg+=`<circle cx="${cx}" cy="${cy}" r="55" fill="url(#core-glow)"/><circle cx="${cx}" cy="${cy}" r="32" fill="url(#ball-core)" ${coreHot?`stroke="${theme.amber}" stroke-width="3"`:''}/><text x="${cx}" y="${cy+45}" text-anchor="middle" class="flow-title" fill="${theme.ink}">Memory service</text><text x="${cx}" y="${cy+58}" text-anchor="middle" class="flow-small" fill="${theme.mut2}">${active} notes on</text>`;
    if(runNotes)svg+=`<text x="${cx}" y="${cy+71}" text-anchor="middle" class="flow-small" fill="${theme.efc.r}">+ ${runNotes} run ${runNotes===1?'note':'notes'} (this run, local)</text>`;
    readFlights=readFlights.filter(f=>f.until>now);
    readFlights.forEach((f,i)=>{const pos=notePos.get(f.id),j=f.version-1;if(!pos||j<0||j>=data.versions.length)return;const mid=versionY(j)+cardH/2,path=`M${pos.x},${pos.y} C${cx+55},${pos.y+45} ${cx-65},${cy-75} ${cx},${cy} C${cx-65},${cy+84} ${cx+65},${mid-54} ${cx},${mid}`;
      svg+=`<circle r="6" fill="url(#ball-gold)" stroke="${theme.ink}" stroke-width="1"><animateMotion dur="1.6s" begin="${i*.2}s" fill="freeze" path="${path}"/></circle>`;
    });
    flow.innerHTML=`<svg class="flow-svg vertical" viewBox="0 0 390 900" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Vertical flow of memory notes through the Memory service to Dev versions">${svg}</svg>`;
    flow.onclick=e=>{const hit=e.target.closest?.('[data-note]');if(hit){const n=data.byId.get(hit.dataset.note);if(n)select(n)}};
  }
  function renderFlow(){if(!data)return;const flow=$('flow');if(!flow.classList.contains('on'))return;
    const now=Date.now(),visible=visibleNotes(data,version,usedOnly,layer),selectedId=selected?.id;
    if(flowLayout(global.innerWidth||document.documentElement?.clientWidth||780)==='vertical'){renderVerticalFlow();return;}
    const CX=392,CY=226,RX=520,groupY=i=>34+i*66,slot=390/Math.max(1,data.versions.length),cardH=Math.min(68,slot-7),versionY=i=>34+i*slot,notePos=new Map();
    const pathVersion=i=>{const y=versionY(i)+cardH/2;return `M426,${CY} C492,${CY} 450,${y} ${RX},${y}`};
    const gradient=(id,a,b,c)=>`<radialGradient id="${id}" cx="35%" cy="30%" r="80%"><stop stop-color="${a}"/><stop offset=".55" stop-color="${b}"/><stop offset="1" stop-color="${c}"/></radialGradient>`;
    let svg=`<defs>${gradient('ball-help','#c8ffe0',theme.pass,'#12703c')}${gradient('ball-harm','#ffd0d0',theme.fail,'#8a1f1f')}${gradient('ball-unknown',...theme.bk)}${gradient('ball-unvalidated','#efe2ff',theme.efc.v,'#4b2385')}${gradient('ball-run-note','#d4fff8',theme.efc.r,'#064d48')}${gradient('ball-off','#a9b0c0','#59606e','#2a2f3a')}${gradient('ball-gold','#fff6c4',theme.amber,'#b57c00')}${gradient('ball-core','#a9c4ff','#2d63d9','#10306e')}<radialGradient id="core-glow"><stop stop-color="${theme.coreGlow2}" stop-opacity=".55"/><stop offset="1" stop-color="${theme.coreGlow2}" stop-opacity="0"/></radialGradient></defs>`;
    svg+=`<g class="flow-head" fill="${theme.mut2}"><text x="10" y="18">1 · NOTES, BY GROUP</text><text x="${CX}" y="18" text-anchor="middle">2 · MEMORY SERVICE</text><text x="${RX}" y="18">3 · DEV VERSIONS OF THIS RUN</text></g>`;
    svg+=`<ellipse cx="${CX}" cy="${CY}" rx="88" ry="30" transform="rotate(-18 ${CX} ${CY})" fill="none" stroke="${theme.stroke}" stroke-opacity=".35"/><ellipse cx="${CX}" cy="${CY}" rx="66" ry="22" transform="rotate(24 ${CX} ${CY})" fill="none" stroke="${theme.stroke}" stroke-opacity=".35"/>`;
    let paths='',cards='',balls='';
    data.groups.forEach((g,gi)=>{const ns=visible.filter(n=>n.groupIndex===gi),y=groupY(gi),usedCount=ns.filter(n=>n.version).length,reads=ns.reduce((sum,n)=>sum+n.versions.length,0),hot=ns.some(n=>flashUntil.get(n.id)>now);
      const width=2+Math.sqrt(reads)*1.8;
      paths+=`<path class="flow-connection ${hot?'hot':''}" d="M250,${y+28} C320,${y+28} 292,${CY} 358,${CY}" fill="none" stroke="${hot?theme.amber:groupColor(gi)}" stroke-opacity="${hot?1:.48}" stroke-width="${hot?width+1.5:width}"/>`;
      cards+=`<rect x="10" y="${y}" width="240" height="56" rx="12" fill="${theme.card}" stroke="${groupColor(gi)}" stroke-opacity=".65"/><text x="24" y="${y+19}" class="flow-title" fill="${theme.ink}">${escapeHtml(g.name)}</text><text x="238" y="${y+19}" text-anchor="end" class="flow-small" fill="${theme.mut}">${ns.length} notes · ${usedCount} used</text>`;
      const space=ns.length>180?5:ns.length>110?6:ns.length>70?7:ns.length>40?8:ns.length>25?10:ns.length>15?12:15,cols=Math.floor(214/space);
      ns.forEach((n,i)=>{const x=26+(i%cols)*space,yy=y+34+Math.floor(i/cols)*space,hotNote=flashUntil.get(n.id)>now,isUsed=n.version>0,isOff=n.status!=='active',r=selectedId===n.id?8:isOff?space*.26:isUsed?Math.min(6.5,space*.5):space*.33;
        notePos.set(n.id,{x,y:yy});const fill=hotNote?'ball-gold':isOff?'ball-off':n.effect==='helps'?'ball-help':n.effect==='not_helps'?'ball-harm':n.effect==='unvalidated'?'ball-unvalidated':n.effect==='run_note'?'ball-run-note':'ball-unknown';
        if(hotNote||selectedId===n.id)balls+=`<circle cx="${x}" cy="${yy}" r="${r+9}" fill="url(#core-glow)" class="flow-glow"/>`;
        balls+=`<circle data-note="${escapeHtml(n.id)}" cx="${x}" cy="${yy}" r="${r}" fill="url(#${fill})" opacity="${isOff?.65:isUsed||hotNote?1:.58}" class="flow-note"><title>${escapeHtml(n.title)}</title></circle>`;
      });
    });
    data.versions.forEach((v,i)=>{const y=versionY(i),hot=v.note_ids.some(id=>flashUntil.get(id)>now),dim=version&&version!==v.n,stroke=versionStroke(v.state);
      const w=2+Math.sqrt(v.note_ids.length)*1.8;
      paths+=`<path class="flow-connection ${hot?'hot':''}" d="${pathVersion(i)}" fill="none" stroke="${hot?theme.amber:theme.stroke}" stroke-opacity="${hot?1:dim?.14:.5}" stroke-width="${hot?w+1.5:w}"/>`;
      const compact=cardH<55,mid=y+cardH/2;
      cards+=`<g opacity="${dim?.4:1}"><rect x="${RX}" y="${y}" width="250" height="${cardH}" rx="${Math.min(12,cardH/3)}" fill="${theme.card}" stroke="${hot?theme.amber:version===v.n?theme.ink:theme.cardLine}" stroke-width="${hot||version===v.n?2:1}"/><circle class="version-ring state-${escapeHtml(v.state)}" cx="${RX+28}" cy="${compact?mid:y+25}" r="${Math.min(17,cardH/2-3)}" fill="url(#ball-core)" stroke="${stroke}" stroke-width="2.5"/><text x="${RX+28}" y="${compact?mid+4:y+29}" text-anchor="middle" class="flow-title" fill="${theme.ink}">v${v.n}</text><text x="${RX+54}" y="${compact?mid+4:y+21}" class="flow-title" fill="${theme.ink}">Dev v${v.n}</text><text x="${compact?RX+115:RX+54}" y="${compact?mid+4:y+36}" class="flow-small" fill="${theme.mut}">${escapeHtml(compact?versionStatus(v.state).split(' · ')[0]:versionStatus(v.state))}</text><text x="758" y="${compact?mid+4:y+21}" text-anchor="end" class="flow-small" fill="${theme.mut}">${compact?versionCount(data,v,version,usedOnly,layer)+' used':versionCount(data,v,version,usedOnly,layer)?versionCount(data,v,version,usedOnly,layer)+' notes used':'no notes yet'}</text>`;
      let px=RX+54;
      if(cardH>=55)v.note_ids.filter(id=>visible.some(n=>n.id===id)).slice(-2).forEach(id=>{const n=data.byId.get(id);if(!n)return;const label=escapeHtml(n.title.slice(0,17)),ww=Math.min(125,20+n.title.slice(0,17).length*6),fl=flashUntil.get(id)>now;
        cards+=`<g data-note="${escapeHtml(id)}" class="flow-pill-svg"><rect x="${px}" y="${y+45}" width="${ww}" height="18" rx="9" fill="${fl?theme.amber:theme.pill}" stroke="${fl?theme.flashLine:color(n.status==='active'?n.effect:'off')}" stroke-width="1.5"/><text x="${px+8}" y="${y+58}" class="flow-small" fill="${fl?theme.onAmber:theme.pillInk}">${label}</text></g>`;px+=ww+5;
      });
      if(cardH>=55&&versionCount(data,v,version,usedOnly,layer)>2)cards+=`<text x="${px}" y="${y+58}" class="flow-small" fill="${theme.mut}">+${versionCount(data,v,version,usedOnly,layer)-2}</text>`;
      cards+='</g>';
    });
    svg+=paths+cards+balls;
    const active=data.notes.filter(n=>n.status==='active'&&!n.run_note).length,runNotes=data.notes.filter(n=>n.status==='active'&&n.run_note).length,coreHot=[...flashUntil.values()].some(t=>t>now);
    svg+=`<circle cx="${CX}" cy="${CY}" r="64" fill="url(#core-glow)"/><circle cx="${CX}" cy="${CY}" r="32" fill="url(#ball-core)" ${coreHot?`stroke="${theme.amber}" stroke-width="3"`:''}/><text x="${CX}" y="${CY+54}" text-anchor="middle" class="flow-title" fill="${theme.ink}">Memory service</text><text x="${CX}" y="${CY+69}" text-anchor="middle" class="flow-small" fill="${theme.mut2}">${active} notes on</text>`;
    if(runNotes)svg+=`<text x="${CX}" y="${CY+84}" text-anchor="middle" class="flow-small" fill="${theme.efc.r}">+ ${runNotes} run ${runNotes===1?'note':'notes'} (this run, local)</text>`;
    svg+=`<g class="flow-legend" fill="${theme.mut}"><circle cx="22" cy="440" r="4.5" fill="${theme.efc.u}"/><text x="32" y="444">Helps</text><circle cx="86" cy="440" r="4.5" fill="${theme.efc.x}"/><text x="96" y="444">Does not help</text><circle cx="196" cy="440" r="4.5" fill="${theme.efc.k}"/><text x="206" y="444">Not tested</text><circle cx="278" cy="440" r="4.5" fill="${theme.efc.v}"/><text x="288" y="444">Unvalidated</text><circle cx="374" cy="440" r="4.5" fill="${theme.efc.r}"/><text x="384" y="444">This run</text><text x="450" y="444" class="flow-small" fill="${theme.mut3}">Big bright balls were used in this run · line width = how often</text></g>`;
    readFlights=readFlights.filter(f=>f.until>now);
    readFlights.forEach((f,i)=>{const n=data.byId.get(f.id),pos=notePos.get(f.id);if(!n||!pos)return;const j=f.version-1;if(j<0||j>=data.versions.length)return;const y=versionY(j)+cardH/2,path=`M${pos.x},${pos.y} C${pos.x+60},${pos.y} 292,${CY} 358,${CY} L426,${CY} C492,${CY} 450,${y} ${RX},${y}`;
      svg+=`<circle r="6" fill="url(#ball-gold)" stroke="${theme.ink}" stroke-width="1"><animateMotion dur="1.6s" begin="${i*.2}s" fill="freeze" path="${path}"/></circle>`;
    });
    flow.innerHTML=`<svg class="flow-svg" viewBox="0 0 780 456" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Flow of memory notes from their groups through the Memory service to Dev versions">${svg}</svg>`;
    flow.onclick=e=>{const hit=e.target.closest?.('[data-note]');if(hit){const n=data.byId.get(hit.dataset.note);if(n)select(n)}};
  }
  function recordReads(events){const now=Date.now();events.filter(e=>['planner_memory_retrieved','planner_memory_run_notes_delivered'].includes(e.type)).forEach(e=>(e.note_ids||[]).forEach(id=>{flashUntil.set(id,now+4500);readFlights.push({id,version:Number(e.version),until:now+2000})}));}
  function feedLabel(ev,name){
    if(ev.type.startsWith('planner_memory_run_note'))return ev.text||(ev.type==='planner_memory_run_notes_delivered'?`v${ev.version} used run notes`:'');
    if(ev.type==='planner_memory_result_skipped'&&ev.reason==='run_note_excluded_from_results')return `v${ev.version} not counted in results`;
    return ev.type==='planner_memory_retrieved'?`v${ev.version} used ${name||'notes'}`:ev.type==='planner_memory_result_skipped'?`v${ev.version} ${ev.reason==='not_trusted_here_excluded_from_results'?'not counted in results':ev.reason==='reference_only_no_scene_contract'?'reference only · not counted':'test note excluded'}`:ev.type==='planner_memory_query_finished'?`v${ev.version} note check finished`:ev.type==='eval_failed'?`v${ev.version} failed`:ev.type==='eval_passed'?`v${ev.version} passed`:ev.type==='version_started'?`v${ev.version} started`:`v${ev.version} checking notes`;
  }
  function feed(fresh=[]){if(!data)return;const newKeys=new Set(fresh.map(eventKey));$('feed').innerHTML=data.events.slice(-5).reverse().map(ev=>{const name=ev.note_ids?.length?data.byId.get(ev.note_ids[0])?.title:null;const label=feedLabel(ev,name);return `<div class="e ${ev.type==='planner_memory_retrieved'?'u':''} ${newKeys.has(eventKey(ev))?'new':''}">${escapeHtml(label)}</div>`}).join('');}
  function sample(count){const verbs=['Slow','Retry','Align','Lift','Open','Check','Back off','Tilt','Pause','Re-grip'],nouns=['grasp','wrist','sink','handle','mug','lid','door','tray','cup','jar','approach','release'],intros=['Move more slowly near the object so the gripper does not overshoot.','If the grasp misses, back off a little and try again.','Line up the wrist with the object before moving in.','Lift the object a little before turning, to clear the counter.','Open the gripper early so it is ready when it arrives.','Check the distance to the object after each small step.','Back away first, then approach again from a new angle.','Tilt the wrist a few degrees to avoid hitting the edge.','Pause briefly before closing the gripper.','If the object slips, let go and grip it again lower.'],groups=['Grasp','Reach','Motion','Release','Vision','Safety'];let seed=11+count;const rand=()=>{seed=(seed*9301+49297)%233280;return seed/233280};const notes=Array.from({length:count},(_,i)=>{const vi=(i*3)%10,ni=Math.floor(i/10)%12,k=Math.floor(i/120);return {id:'sample-'+i,title:verbs[vi]+' '+nouns[(ni+vi)%12]+(k?' '+(k+1):''),summary:intros[vi],group:groups[Math.floor(Math.pow(rand(),1.3)*6)],status:rand()>.14?'active':'off',effect:rand()<.3?'not_tested':rand()<.6?'helps':'not_helps',reads:Math.floor(rand()*30),last_used_at:Date.now()/1000-rand()*3600,test_seeded:false}});return {run:{experiment_id:'sample',state:'running',max_versions:demoVersions,versions:Array.from({length:demoVersions},(_,i)=>({n:i+1,state:i===0?'running':'waiting',note_ids:[]}))},notes,events:[]};}
  function sampleTick(){if(!data?.notes.length)return;
    const tick=demoTick++,versions=data.run.versions,now=Date.now(),fresh=[];
    const span=demoVersions+4;if(tick>0&&tick%span===0){data.run={...data.run,experiment_id:'sample-'+Math.floor(tick/span),versions:Array.from({length:demoVersions},(_,i)=>({n:i+1,state:'waiting',note_ids:[]}))};data.events=[];flashUntil.clear();readFlights=[];}
    const current=data.run.versions,cycle=tick%span;
    current.forEach((v,i)=>{const age=cycle-i;if(age===1){v.state='generating';fresh.push({type:'version_generating',version:v.n,note_ids:[]})}
      else if(age===2){v.state='running';fresh.push({type:'planner_memory_query_finished',version:v.n,note_ids:[]})}
      else if(age===3){v.state=Math.random()<.08?'failed':'passed';fresh.push({type:v.state==='passed'?'eval_passed':'eval_failed',version:v.n,note_ids:[]})}});
    if(cycle<demoVersions){const v=current[cycle],available=data.notes.filter(n=>n.status==='active'),ids=[];
      for(let i=0,count=1+Math.floor(Math.random()*3);i<count&&available.length;i++){const index=Math.floor(Math.random()*available.length),n=available.splice(index,1)[0];ids.push(n.id);n.reads++;n.last_used_at=now/1000;}
      v.note_ids.push(...ids);v.state='reading';fresh.push({type:'planner_memory_retrieved',version:v.n,note_ids:ids});}
    fresh.forEach(e=>{e.seq=++demoSeq;e.ts=now/1000;data.events.push(e)});
    data=adapt(data);recordReads(fresh);render();feed(fresh);
  }
  async function poll(){if(!experimentId)return;try{const response=await fetch('/api/dev-v2/memory-monitor?exp='+encodeURIComponent(experimentId),{cache:'no-store',credentials:'same-origin'});const payload=await response.json();if(!response.ok)throw Error(payload.error||`HTTP ${response.status}`);const fresh=newEvents(previous,payload.events);previous=payload.events||[];recordReads(fresh);data=adapt(payload);$('error').textContent='';render();feed(fresh);clearTimeout(pollTimer);pollTimer=setTimeout(poll,['running','planned'].includes(payload.run?.state)?2000:10000);}catch(error){$('error').textContent='Memory data is unavailable right now.';noticeController.clientError('Memory');if(!data){data=adapt({run:{state:'unavailable',versions:[]},notes:[],events:[]});render()}clearTimeout(pollTimer);pollTimer=setTimeout(poll,10000)}}
  async function init(){if(!demo&&!experimentId){try{const r=await fetch('/api/dev-v2/experiments',{credentials:'same-origin'});const sessions=await r.json();const items=Array.isArray(sessions)?sessions:sessions.sessions||[];experimentId=items.filter(x=>x.experiment_id).sort((a,b)=>(b.created_at||0)-(a.created_at||0))[0]?.experiment_id||null;if(experimentId)$('back').href='/ui/#exp='+encodeURIComponent(experimentId);}catch(_){}}
    if(!demo)fetch('/api/dev-memories',{credentials:'same-origin'}).then(async c=>{if(c.ok){canOperate=(await c.json()).control?.can_operate===true;if(selected)$('card').innerHTML=cardHtml(selected,selected.run_note?data?.run.can_operate_run_notes:canOperate,false)}}).catch(()=>{});
    if(global.THREE){try{globe=createGlobe();globe.applyTheme3D();setView('3d')}catch(_){globe=null}}
    if(!globe){$('views').querySelector('[data-view="3d"]').hidden=true;setView('2d')}
    if(demo){$('sample').hidden=false;data=adapt(sample(Number(demo[1])));sampleTick();setInterval(sampleTick,3000)}else if(experimentId)poll();else{data=adapt({run:{versions:[]},notes:[],events:[]});render();$('error').textContent='No Dev run found';}
  }
  function createGlobe(){const T=global.THREE,mount=$('globe'),labels=$('labels'),svg=$('leaders'),tip=$('tip');const renderer=new T.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));mount.appendChild(renderer.domElement);const scene=new T.Scene(),camera=new T.PerspectiveCamera(42,1,.05,50),root=new T.Group();scene.add(root);camera.position.z=cameraFitDistance(mount.clientWidth,mount.clientHeight);const shell=new T.Mesh(new T.SphereGeometry(1,28,18),new T.MeshBasicMaterial({color:theme.shell,wireframe:true,transparent:true,opacity:theme.shellOp}));root.add(shell);const core=new T.Mesh(new T.SphereGeometry(.14,24,16),new T.MeshBasicMaterial({color:theme.core}));root.add(core);const ringLabels=[];const ringOpacity=(v,i)=>v?(v===i+1?1:.12):Math.max(theme.ringOp,.85);function syncRings(count){if(ringLabels.length===count)return;ringLabels.forEach(({ring,label})=>{root.remove(ring);ring.geometry.dispose();ring.material.dispose();label.remove()});ringLabels.length=0;orbitRadii(count).forEach((r,i)=>{const geo=new T.TorusGeometry(r,.0062,6,200);geo.rotateX(Math.PI/2);const ring=new T.Mesh(geo,new T.MeshBasicMaterial({color:versionColor(i),transparent:true,opacity:ringOpacity(0,i)}));ring.rotation.x=.25*i-.5;ring.rotation.z=.35*(i%2?1:-1)*(i+1)/3;ring.updateMatrix();root.add(ring);const label=document.createElement('div');label.className='rl';label.textContent='v'+(i+1);label.style.color=versionColor(i);labels.append(label);ringLabels.push({ring,label,r})})}const balls=new T.Group();root.add(balls);const q=new T.Quaternion().setFromEuler(new T.Euler(-.35,0,0)),Y=new T.Vector3(0,1,0),X=new T.Vector3(1,0,0),Z=new T.Vector3(0,0,1);let drag=null,mouse=null,zoom=2.7,selectedId=null,items=[],signature='';const positions=new Map(),callouts=new Map();function project(p){const v=p.clone().applyMatrix4(root.matrixWorld).project(camera);return{x:(v.x*.5+.5)*mount.clientWidth,y:(-v.y*.5+.5)*mount.clientHeight,z:v.z}}function rebuild(model){while(balls.children.length){const c=balls.children[0];balls.remove(c);c.geometry.dispose();c.material.dispose()}items=[];positions.clear();syncRings(model.versions.length);const perVersion=new Map(),slot=new Map();model.notes.forEach(n=>{if(n.status==='active'&&n.version)perVersion.set(n.version,(perVersion.get(n.version)||0)+1)});model.notes.forEach((n,i)=>{const a=(n.groupIndex*60+(i*47)%48-90)*Math.PI/180,h=(((i*37)%101)/100*2-1)*.9,t=Math.sqrt(1-h*h),dir=new T.Vector3(Math.cos(a)*t,h,Math.sin(a)*t),r=n.status!=='active'?1.06:n.version?orbitRadii(model.versions.length)[n.version-1]||1:1;const ballColor=n.status==='active'?color(n.effect):color('off');const ball=new T.Mesh(new T.SphereGeometry(1,12,10),new T.MeshBasicMaterial({color:ballColor}));const rl=n.status==='active'&&n.version?ringLabels[n.version-1]:null;if(rl){const k=slot.get(n.version)||0;slot.set(n.version,k+1);const th=(k/perVersion.get(n.version))*Math.PI*2+(n.version-1)*.9+.35;rl.ring.updateMatrix();ball.position.copy(new T.Vector3(Math.cos(th)*rl.r,0,Math.sin(th)*rl.r).applyMatrix4(rl.ring.matrix));dir.copy(ball.position).normalize()}else ball.position.copy(dir).multiplyScalar(r);ball.scale.setScalar(n.status!=='active'?.014:n.version?.052:.02);ball.userData.id=n.id;balls.add(ball);const halo=new T.Mesh(new T.SphereGeometry(1,12,8),new T.MeshBasicMaterial({color:theme.amberHex,transparent:true,opacity:0,depthWrite:false}));halo.position.copy(ball.position);halo.visible=false;balls.add(halo);items.push({ball,halo,dir,n});positions.set(n.id,ball.position)});callouts.forEach(x=>{x.label.remove();x.line?.remove()});callouts.clear();model.groups.forEach((g,i)=>{const l=document.createElement('div');l.className='g';l.textContent=g.name;l.style.color=groupColor(i);labels.append(l);callouts.set('group'+i,{label:l,group:i})})}function update(model,v,only,flashes,layerFilter=layer){syncRings(model.versions.length);ringLabels.forEach(({ring,label},i)=>{const stroke=versionColor(i);ring.material.color.set(stroke);ring.material.opacity=ringOpacity(v,i);label.style.color=stroke;label.style.opacity=v&&v!==i+1?'.3':''});const sig=model.versions.length+'|'+model.notes.map(n=>`${n.id}:${n.version}:${n.status}:${n.effect}`).join('|');if(sig!==signature){signature=sig;rebuild(model)}const now=Date.now();items.forEach(({ball,halo,n})=>{const show=(layerFilter==='all'||n.layer===layerFilter)&&(!only||n.version)&&(!v||n.versions.includes(v));ball.visible=!!show;halo.visible=!!show&&flashes.get(n.id)>now&&!reduced;const hot=flashes.get(n.id)>now&&!reduced;ball.material.color.set(hot?theme.amber:n.status==='active'?color(n.effect):color('off'));const size=n.status!=='active'?.014:n.version?.052:.02;ball.scale.setScalar(size*(hot?1.65:1));if(halo.visible){const phase=1-(flashes.get(n.id)-now)/4500;halo.scale.setScalar(size*(1.5+phase*4));halo.material.color.setHex(theme.amberHex);halo.material.opacity=(1-phase)*.32;}});}function reset(){selectedId=null;zoom=2.7}function select(n){selectedId=n.id}function pick(m){let best=null,dist=17;items.forEach(item=>{if(!item.ball.visible)return;const p=project(item.ball.position);const d=Math.hypot(p.x-m.x,p.y-m.y);if(d<dist){dist=d;best=item.n}});return best}function animate(){const w=mount.clientWidth,h=mount.clientHeight;if(w&&h&&(renderer.domElement.width!==Math.round(w*renderer.getPixelRatio())||renderer.domElement.height!==Math.round(h*renderer.getPixelRatio()))){renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();camera.position.z=Math.max(camera.position.z,cameraFitDistance(w,h))}if(view==='3d'){items.forEach(({ball,halo,n})=>{const remaining=flashUntil.get(n.id)-Date.now();const hot=remaining>0&&!reduced;if(halo.visible!==(hot&&ball.visible))halo.visible=hot&&ball.visible;if(hot){const phase=1-remaining/4500;const size=n.status!=='active'?.014:n.version?.052:.02;halo.scale.setScalar(size*(1.5+phase*4));halo.material.opacity=(1-phase)*.32;ball.material.color.set(theme.amber)}else if(ball.material.color.getHex()===theme.amberHex){ball.material.color.set(n.status==='active'?color(n.effect):color('off'))}});if(selectedId){const item=items.find(x=>x.n.id===selectedId);if(item){const target=new T.Quaternion().setFromUnitVectors(item.dir,Z);q.slerp(target,reduced?1:.07)}}else if(!drag&&!reduced)q.premultiply(new T.Quaternion().setFromAxisAngle(Y,.0016));root.quaternion.copy(q);camera.position.z+=(Math.max(zoom,cameraFitDistance(w,h))-camera.position.z)*(reduced?1:.1);root.updateMatrixWorld(true);renderer.render(scene,camera);const hub=project(new T.Vector3(0,0,0));ringLabels.forEach(({ring,label,r},i)=>{const p=project(new T.Vector3(Math.cos(.9+i*1.25)*r,0,Math.sin(.9+i*1.25)*r).applyMatrix4(ring.matrix));label.style.transform=`translate(${p.x}px,${p.y}px)`;label.style.opacity=version&&version!==i+1?.25:1});const wants=phoneCallouts(items.filter(x=>x.ball.visible&&x.n.version&&x.n.status==='active'&&(!version||x.n.versions.includes(version))),w);labels.querySelectorAll('.n').forEach(n=>n.remove());svg.replaceChildren();wants.forEach((item,i)=>{const p=project(item.ball.position),label=document.createElement('div');label.className='n '+({helps:'u',not_helps:'x',not_tested:'k',unvalidated:'v',run_note:'r'}[item.n.effect]);label.textContent=`v${item.n.version}  ${item.n.title}`;label.style.transform=`translate(10px,${10+i*25}px)`;labels.append(label);const line=document.createElementNS('http://www.w3.org/2000/svg','line');line.setAttribute('x1',10+label.offsetWidth);line.setAttribute('y1',20+i*25);line.setAttribute('x2',p.x);line.setAttribute('y2',p.y);line.setAttribute('stroke',color(item.n.effect));line.setAttribute('stroke-opacity','.6');svg.append(line)});data?.groups.forEach((g,i)=>{const p=project(new T.Vector3(Math.cos((i*60-60)*Math.PI/180)*1.17,0,Math.sin((i*60-60)*Math.PI/180)*1.17));const c=callouts.get('group'+i);if(c)c.label.style.transform=`translate(${p.x}px,${p.y}px)`});if(mouse&&!drag){const hit=pick(mouse);tip.style.display=hit?'block':'none';if(hit){tip.textContent=hit.title+' · '+(hit.version?'Used by Dev v'+hit.version:'Not used in this run');tip.style.left=Math.min(mouse.x+15,w-200)+'px';tip.style.top=mouse.y+'px'}}}requestAnimationFrame(animate)}const cvs=renderer.domElement;cvs.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,moved:0};cvs.setPointerCapture(e.pointerId)});cvs.addEventListener('pointermove',e=>{const b=cvs.getBoundingClientRect();mouse={x:e.clientX-b.left,y:e.clientY-b.top};if(drag){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.moved+=Math.abs(dx)+Math.abs(dy);q.premultiply(new T.Quaternion().setFromAxisAngle(Y,dx*.006)).premultiply(new T.Quaternion().setFromAxisAngle(X,dy*.006));drag.x=e.clientX;drag.y=e.clientY}});cvs.addEventListener('pointerup',()=>{if(drag?.moved<6&&mouse){const n=pick(mouse);if(n)selectNote(n)}drag=null});cvs.addEventListener('pointerleave',()=>{mouse=null;tip.style.display='none'});cvs.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(2.2,Math.min(6,zoom+e.deltaY*.003))},{passive:false});cvs.addEventListener('dblclick',()=>{selected=null;$('card').classList.remove('on');$('hint').style.display='';reset()});function selectNote(n){select(n);globalSelect(n)}function globalSelect(n){selected=n;$('card').innerHTML=cardHtml(n,n.run_note?data?.run.can_operate_run_notes:canOperate,!!demo);$('card').classList.add('on');$('hint').style.display='none'}requestAnimationFrame(animate);return{update,reset,select,applyTheme3D(){shell.material.color.setHex(theme.shell);shell.material.opacity=theme.shellOp;core.material.color.setHex(theme.core);ringLabels.forEach(({ring,label},i)=>{ring.material.color.set(versionColor(i));ring.material.opacity=ringOpacity(0,i);label.style.color=versionColor(i)});callouts.forEach(c=>{if(c.group!=null)c.label.style.color=groupColor(c.group)});items.forEach(({ball,halo,n})=>{halo.material.color.setHex(theme.amberHex);ball.material.color.set(n.status==='active'?color(n.effect):color('off'));});}};}
  global.addEventListener('hashchange',()=>global.location.reload());
  init();
})(typeof window!=='undefined'?window:globalThis);
