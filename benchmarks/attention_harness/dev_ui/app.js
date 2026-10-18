'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name="control-token"]').content;
let current=null, selected=null, data=null, liveSequence=-1, videoUrl=null, imageUrl=null, publicSdk=null;
const headers={'X-Attention-Token':token};
async function request(path,body){const r=await fetch(path,{headers:body===undefined?headers:{...headers,'Content-Type':'application/json'},method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||`HTTP ${r.status}`);return v;}
function error(e){$('error').textContent=String(e.message||e);$('error').scrollIntoView({block:'nearest'});}
function text(id,v){$(id).textContent=typeof v==='string'?v:JSON.stringify(v,null,2);}
function option(select,value,label,chosen=false){const o=document.createElement('option');o.value=value;o.textContent=label;o.selected=chosen;select.append(o);}
function ep(path=''){if(!current)throw Error('请先选择实验');return `/api/dev/experiments/${current}${path}`;}
function cell(){return data?.cells.find(c=>c.key===selected);}
function filePath(uri){const c=cell();if(!c||!uri.startsWith(c.artifact_dir+'/'))throw Error('工件不属于所选格');return ep(`/cells/${selected}/files/${uri.slice(c.artifact_dir.length+1).split('/').map(encodeURIComponent).join('/')}`);}
async function file(uri){const r=await fetch(filePath(uri),{headers});if(!r.ok)throw Error(`工件读取 HTTP ${r.status}`);return await r.text();}
async function loadHistory(){const items=await request('/api/dev/experiments');$('history').replaceChildren();for(const e of items){const b=document.createElement('button');b.textContent=`${e.selection.mode} · ${e.experiment_id} · ${e.state}`;b.dataset.experimentId=e.experiment_id;if(e.experiment_id===current)b.setAttribute('aria-current','true');b.onclick=()=>selectExperiment(e.experiment_id);$('history').append(b);}return items;}
async function selectExperiment(id){current=id;selected=null;liveSequence=-1;$('error').textContent='';await refresh();location.hash=`exp=${id}`;$('experiment-status').dataset.selectedExperiment=id;for(const b of $('history').querySelectorAll('[data-experiment-id]')){if(b.dataset.experimentId===id)b.setAttribute('aria-current','true');else b.removeAttribute('aria-current');}}
function renderResources(goal){
  const body=$('resource-rows');body.replaceChildren();
  for(const resource of DevUiView.resourceRows(goal)){
    const row=document.createElement('tr');row.dataset.resource=resource.key;
    if(resource.key==='help_wait_seconds')row.className='resource-wait';
    for(const value of [resource.label,resource.used,resource.reserved,resource.remaining]){
      const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);
    }
    body.append(row);
  }
  text('resources',{goal_used:goal.used,goal_reserved:goal.reserved,goal_remaining:goal.remaining,
    wall_remaining_seconds:goal.wall_remaining,validation_closed:!!goal.real_finished_at,close_reason:goal.close_reason});
}
async function refresh(){if(!current){await loadHistory();return;}data=await request(ep());if(!selected||!data.cells.some(c=>c.key===selected))selected=data.cells[0]?.key;
text('binding',data.selection.mode==='experiment'?'实验策略已冻结；全部 planned 格进入报告分母':cell()?.mixed_policy?'混合策略 · 不进入单策略对比':'交互 Dev · 切换后标为混合策略');
$('run').disabled=data.selection.mode==='diagnostic'||data.cancelled||!!data.owner||['needs_review','condition_invalid'].includes(data.state)||!!data.resources.real_finished_at;$('cancel').disabled=data.cancelled;
const editable=data.selection.mode==='interactive'&&!data.cancelled; $('switch').disabled=!editable;$('switch-policy').disabled=!editable;
$('max-cells').max=data.cells.length;
text('binding',`${data.experiment_id} · goal ${data.goal_id||'legacy'} · ${data.selection.coverage||'v2'} · G=${data.selection.target_depth||2} / E=${data.selection.target_valid_executions||2} / max=${data.selection.max_versions||2} ${data.selection.policies.includes('retry_k_then_ask')&&data.selection.target_depth<(data.selection.policy_parameters?.retry_k_then_ask?.k??2)+1?' · 该配置不能覆盖 retry k='+String(data.selection.policy_parameters?.retry_k_then_ask?.k??2)+' 触发':''}`);
const table=document.createElement('table'),head=document.createElement('tr');for(const v of ['Task','Seed','Policy','State','Versions','Dev / Advisor','Tokens','Help']){const th=document.createElement('th');th.textContent=v;head.append(th);}table.append(head);
for(const c of data.cells){const row=document.createElement('tr');if(c.key===selected)row.className='selected';row.onclick=()=>{selected=c.key;liveSequence=-1;refresh().catch(error);};for(const v of [c.task_id,c.seed,c.policy_id,c.state,c.versions.length,`${c.used.dev_calls} / ${c.used.advisor_calls}`,c.used.provider_tokens,c.used.help_count]){const td=document.createElement('td');td.textContent=v;row.append(td);}table.append(row);}$('cells').replaceChildren(table);
renderResources(data.resources);
const c=cell(),old=$('version').value;$('version').replaceChildren();$('replay-version').replaceChildren();for(const v of c?.versions||[]){option($('version'),String(v.version),`v${v.version}${v.version>(data.selection.target_depth||2)?' · 补偿版':''}${v.valid?'':' · validation failed'}`);option($('replay-version'),String(v.version),`v${v.version}`);}if(old&&c?.versions.some(v=>String(v.version)===old))$('version').value=old;else if(c?.versions.length)$('version').value=String(c.versions.at(-1).version);
showPlanner();await showVersion();await showLive();}
async function showVersion(){const c=cell(),v=c?.versions.find(v=>String(v.version)===$('version').value);if(!v){for(const id of ['source','diff','feedback','native','version-resources'])text(id,'暂无版本');return;}
text('source',await file(v.source.uri));text('diff',await file(v.diff.uri));const j=c.jobs.find(j=>j.kind==='execute'&&j.version===v.version&&j.state==='settled');text('feedback',j?JSON.parse(await file(j.receipt.public_feedback.uri)):v.validation_error||(c.state==='completed'?'该版本仅生成；执行目标已满足':'等待执行'));text('native',j?{result:JSON.parse(await file(j.receipt.native_result.uri)),scene_condition:j.receipt.scene_condition}: '未评测');text('version-resources',{usage:v.usage,decision:JSON.parse(await file(v.decision.uri)),help:v.help?JSON.parse(await file(v.help.uri)):null,cell_used:c.used,cell_jobs:c.jobs});}
async function showLive(){const c=cell();if(!c||c.state!=='running'||!c.active_episode){$('live-image').hidden=true;text('live-status','当前没有在途仿真；历史请使用执行回放');return;}
try{const row=await request(ep(`/cells/${selected}/live`));text('live-status',`真实 live · v${row.active_version} · control step ${row.control_step} · seq ${row.sequence}`);if(row.sequence===liveSequence)return;liveSequence=row.sequence;const r=await fetch(ep(`/cells/${selected}/live/frame`),{headers});if(!r.ok)throw Error('Live frame unavailable');if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl=URL.createObjectURL(await r.blob());$('live-image').src=imageUrl;$('live-image').hidden=false;}catch(e){text('live-status','等待真实仿真帧');}}
function createSelection(){
  const tasks=[...$('tasks').selectedOptions].map(o=>o.value),policies=[...$('policies').selectedOptions].map(o=>o.value);
  const seeds=$('seeds').value.split(',').map(s=>Number(s.trim()));
  const overrides=$('budget-config').value.trim()?JSON.parse($('budget-config').value):{};
  return {request_key:'ui-'+crypto.randomUUID(),tasks,seeds,policies,help_budget:Number($('help').value),
    mode:$('mode').value,target_depth:Number($('target-depth').value),
    target_valid_executions:Number($('target-executions').value),max_versions:Number($('max-versions').value),
    coverage:$('coverage').value,...($('planner-enabled').checked?{planner_features:{
      human_wait_seconds:Number($('human-wait').value),memory:$('memory-url').value.trim()?{
        base_url:$('memory-url').value.trim(),store_id:$('memory-authority').value.trim(),
        namespace:$('memory-namespace').value.trim()}:null}}:{}),...overrides,
    eval_agent:DevUiView.evalSetting($('eval-enabled').checked)};
}
$('create').onsubmit=async e=>{e.preventDefault();try{$('error').textContent='';const result=await request('/api/dev/experiments',createSelection());await loadHistory();await selectExperiment(result.experiment_id);}catch(e){error(e);}};
$('mode').onchange=()=>{const single=$('mode').value==='interactive';$('eval-enabled').checked=!single;for(const id of ['tasks','policies']){const select=$(id);select.multiple=!single;if(single)select.selectedIndex=0;else for(const o of select.options)o.selected=true;}budgetPreview();};
$('run').onclick=async()=>{try{await request(ep('/run'),{max_cells:Number($('max-cells').value)});await refresh();}catch(e){error(e);}};
$('cancel').onclick=async()=>{try{await request(ep('/cancel'),{});await refresh();}catch(e){error(e);}};
$('switch').onclick=async()=>{try{await request(ep('/switch'),{cell:selected,policy:$('switch-policy').value});await refresh();}catch(e){error(e);}};
$('version').onchange=()=>showVersion().catch(error);
$('load-replay').onclick=async()=>{try{const c=cell();const job=c?.jobs.find(j=>j.kind==='execute'&&j.version===Number($('replay-version').value)&&j.state==='settled');if(!job?.receipt.recording?.video?.uri)throw Error('此版本尚无可定位视频');const r=await fetch(filePath(job.receipt.recording.video.uri),{headers});if(!r.ok)throw Error('视频读取失败');if(videoUrl)URL.revokeObjectURL(videoUrl);videoUrl=URL.createObjectURL(await r.blob());$('replay').src=videoUrl;text('replay-status',`历史回放 v${job.version}，非 live`);}catch(e){error(e);}};
$('seek').onclick=()=>{$('replay').currentTime=Number($('seek-time').value);};
$('report').onclick=async()=>{try{text('report-data',await request(ep('/report')));}catch(e){error(e);}};
async function download(ext){const r=await fetch(ep(`/export.${ext}`),{headers});if(!r.ok)throw Error('导出失败');const a=document.createElement('a'),url=URL.createObjectURL(await r.blob());a.href=url;a.download=`${current}.${ext}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export-json').onclick=()=>download('json').catch(error);$('export-csv').onclick=()=>download('csv').catch(error);
(async()=>{try{const o=await request('/api/dev/options');publicSdk=o.tasks[0].sdk;for(const t of o.tasks)option($('tasks'),t.task_id,t.task_prompt,true);for(const p of o.policies){option($('policies'),p,p,true);option($('switch-policy'),p,p);}text('sdk',`${o.robot} · ${o.execution_arm} · SDK ${publicSdk.sdk_calls} calls / ${publicSdk.control_steps} control steps / ${publicSdk.attempt_seconds}s`);budgetPreview();const items=await loadHistory();const restored=DevUiView.restoredExperiment(location.hash,items);if(restored)await selectExperiment(restored);else if(location.hash.startsWith('#exp='))window.history.replaceState(null,'',location.pathname+location.search);}catch(e){error(e);}})();
setInterval(()=>refresh().catch(error),2000);

let previewSequence=0;
async function budgetPreview(){if(!publicSdk)return;const sequence=++previewSequence;try{const preview=await request('/api/dev/preview',createSelection());if(sequence===previewSequence)text('budget-preview',preview);}catch(e){if(sequence===previewSequence)text('budget-preview',`预算预览失败：${e.message||e}`);}}
for(const id of ['max-versions','target-depth','target-executions','help','tasks','policies','seeds','coverage','planner-enabled','human-wait','eval-enabled','budget-config','memory-url','memory-authority','memory-namespace'])$(id).addEventListener('change',budgetPreview);

$('planner-enabled').addEventListener('change',()=>{if($('planner-enabled').checked){for(const o of $('policies').options)o.selected=o.value==='full_trace_aware_attention_planner';}budgetPreview();});
function showPlanner(){
  const p=cell()?.planner;
  $('planner-requests').replaceChildren();$('planner-work').replaceChildren();
  renderMemory(p?.memory||[]);
  if(!p){text('planner-requests','本记录未启用三功能；原冻结记录保持原条件');return;}
  for(const r of p.requests){
    const article=document.createElement('article'),title=document.createElement('h3'),detail=document.createElement('pre');
    title.textContent=`${r.request_type==='approval'?'人工动作审批':'Advisor 指导'} · ${r.state} · v${r.version}`;
    detail.textContent=JSON.stringify({request_id:r.request_id,scope:r.binding,deadline:new Date(r.deadline_epoch*1000).toISOString(),reply:r.response,adopted:r.adopted,consumed_by:r.consumed_by},null,2);
    article.append(title,detail);
    if(r.request_type==='approval'&&r.state==='waiting')for(const approved of [true,false]){
      const b=document.createElement('button');b.textContent=approved?'批准此版本动作':'拒绝此版本动作';
      b.onclick=async()=>{b.disabled=true;try{const res=await fetch(ep(`/approvals/${r.request_id}/decision`),{method:'POST',headers:{...headers,'Content-Type':'application/json','X-Attention-Human-Token':$('human-capability').value},body:JSON.stringify({binding:r.binding,approved,response_id:crypto.randomUUID()})});const result=await res.json();if(!res.ok)throw Error(result.error);$('human-capability').value='';await refresh();}catch(e){error(e);}finally{b.disabled=false;}};
      article.append(b);
    }
    $('planner-requests').append(article);
  }
  for(const w of p.work){const article=document.createElement('article'),t=document.createElement('p');t.textContent=`${w.kind} · ${w.state} · ${w.actual_seconds??'—'}s · ${w.dependency}`;article.append(t);if(w.result){const b=document.createElement('button');b.textContent='查看实际准备成果';b.onclick=async()=>{try{let pre=article.querySelector('pre');if(!pre){pre=document.createElement('pre');article.append(pre);}pre.textContent=await file(w.result.uri);}catch(e){error(e);}};article.append(b);}$('planner-work').append(article);}
}
function renderMemory(events){
  const host=$('planner-memory-cards');host.replaceChildren();text('planner-memory',events);
  const records=DevUiView.memoryRecords(events);
  if(!records.length){host.textContent='暂无 Memory 检索记录';return;}
  for(const [index,record] of records.entries()){
    const article=document.createElement('article');article.className='memory-card';article.dataset.memoryQuery=String(index);article.dataset.memoryStatus=record.status;
    const statusLabel={started:'已开始',retrieved:'已检索',finished:'已完成',failed:'失败'}[record.status];
    const title=document.createElement('h4');title.textContent=`第 ${index+1} 次检索 · ${statusLabel}`;article.append(title);
    const fields=document.createElement('dl');fields.className='memory-fields';
    const field=(key,label,value)=>{const wrapper=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');
      wrapper.dataset.memoryField=key;dt.textContent=label;dd.textContent=value==null?'—':String(value);wrapper.append(dt,dd);fields.append(wrapper);};
    field('status','状态',statusLabel);field('query-version','检索版本',record.version);
    field('authority','authority',record.authority);field('namespace','namespace',record.namespace);
    const at=record.retrievedAt??record.startedAt;
    field('retrieved-at','检索时间',at==null?'—':new Date(typeof at==='number'?at*1000:at).toLocaleString('zh-CN'));
    field('packet-sha','packet sha256 前 12 位',record.packetSha);
    field('duration','耗时（秒）',record.seconds);
    field('verification','验证结果',record.verification==null?'未记录':record.verification?'通过':'未通过');
    if(record.error)field('error','失败原因',record.error);
    article.append(fields);
    const table=document.createElement('table'),head=document.createElement('tr');
    for(const label of ['memory_id','版本','grant_id']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
    for(const source of record.sources){const row=document.createElement('tr');row.dataset.memoryId=source.memory_id||'';
      for(const value of [source.memory_id,source.version,source.grant_id]){const td=document.createElement('td');td.textContent=value??'—';row.append(td);}table.append(row);}
    if(!record.sources.length){const row=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.textContent='尚无已检索来源';row.append(td);table.append(row);}
    article.append(table);host.append(article);
  }
}
