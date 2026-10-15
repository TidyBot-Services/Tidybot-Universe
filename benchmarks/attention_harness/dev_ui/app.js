'use strict';
const $=id=>document.getElementById(id), token=document.querySelector('meta[name="control-token"]').content;
let current=null, selected=null, data=null, liveSequence=-1, videoUrl=null, imageUrl=null;
const headers={'X-Attention-Token':token};
async function request(path,body){const r=await fetch(path,{headers:body===undefined?headers:{...headers,'Content-Type':'application/json'},method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw Error(v.error||`HTTP ${r.status}`);return v;}
function error(e){$('error').textContent=String(e.message||e);}
function text(id,v){$(id).textContent=typeof v==='string'?v:JSON.stringify(v,null,2);}
function option(select,value,label,chosen=false){const o=document.createElement('option');o.value=value;o.textContent=label;o.selected=chosen;select.append(o);}
function ep(path=''){if(!current)throw Error('请先选择实验');return `/api/dev/experiments/${current}${path}`;}
function cell(){return data?.cells.find(c=>c.key===selected);}
function filePath(uri){const c=cell();if(!c||!uri.startsWith(c.artifact_dir+'/'))throw Error('工件不属于所选格');return ep(`/cells/${selected}/files/${uri.slice(c.artifact_dir.length+1).split('/').map(encodeURIComponent).join('/')}`);}
async function file(uri){const r=await fetch(filePath(uri),{headers});if(!r.ok)throw Error(`工件读取 HTTP ${r.status}`);return await r.text();}
async function history(){const items=await request('/api/dev/experiments');$('history').replaceChildren();for(const e of items){const b=document.createElement('button');b.textContent=`${e.selection.mode} · ${e.experiment_id} · ${e.state}`;b.onclick=()=>selectExperiment(e.experiment_id);$('history').append(b);}}
async function selectExperiment(id){current=id;selected=null;liveSequence=-1;$('error').textContent='';await refresh();}
async function refresh(){if(!current){await history();return;}data=await request(ep());if(!selected||!data.cells.some(c=>c.key===selected))selected=data.cells[0]?.key;
text('binding',data.selection.mode==='experiment'?'实验策略已冻结；全部 planned 格进入报告分母':cell()?.mixed_policy?'混合策略 · 不进入单策略对比':'交互 Dev · 切换后标为混合策略');
$('run').disabled=data.selection.mode==='diagnostic'||data.cancelled||!!data.owner||['needs_review','condition_invalid'].includes(data.state)||!!data.resources.real_finished_at;$('cancel').disabled=data.cancelled;
const editable=data.selection.mode==='interactive'&&!data.cancelled; $('switch').disabled=!editable;$('switch-policy').disabled=!editable;
$('max-cells').max=data.cells.length;
text('binding',`${data.experiment_id} · goal ${data.goal_id||'legacy'} · ${data.selection.coverage||'v2'} · G=${data.selection.target_depth||2} / E=${data.selection.target_valid_executions||2} / max=${data.selection.max_versions||2} ${data.selection.policies.includes('retry_k_then_ask')&&data.selection.target_depth<(data.selection.policy_parameters?.retry_k_then_ask?.k??2)+1?' · 该配置不能覆盖 retry k='+String(data.selection.policy_parameters?.retry_k_then_ask?.k??2)+' 触发':''}`);
const table=document.createElement('table'),head=document.createElement('tr');for(const v of ['Task','Seed','Policy','State','Versions','Dev / Advisor','Tokens','Help']){const th=document.createElement('th');th.textContent=v;head.append(th);}table.append(head);
for(const c of data.cells){const row=document.createElement('tr');if(c.key===selected)row.className='selected';row.onclick=()=>{selected=c.key;liveSequence=-1;refresh().catch(error);};for(const v of [c.task_id,c.seed,c.policy_id,c.state,c.versions.length,`${c.used.dev_calls} / ${c.used.advisor_calls}`,c.used.provider_tokens,c.used.help_count]){const td=document.createElement('td');td.textContent=v;row.append(td);}table.append(row);}$('cells').replaceChildren(table);
text('resources',{goal_used:data.resources.used,goal_reserved:data.resources.reserved,goal_remaining:data.resources.remaining,wall_remaining_seconds:data.resources.wall_remaining,validation_closed:!!data.resources.real_finished_at,close_reason:data.resources.close_reason});
const c=cell(),old=$('version').value;$('version').replaceChildren();$('replay-version').replaceChildren();for(const v of c?.versions||[]){option($('version'),String(v.version),`v${v.version}${v.version>(data.selection.target_depth||2)?' · 补偿版':''}${v.valid?'':' · validation failed'}`);option($('replay-version'),String(v.version),`v${v.version}`);}if(old&&c?.versions.some(v=>String(v.version)===old))$('version').value=old;else if(c?.versions.length)$('version').value=String(c.versions.at(-1).version);
showPlanner();await showVersion();await showLive();}
async function showVersion(){const c=cell(),v=c?.versions.find(v=>String(v.version)===$('version').value);if(!v){for(const id of ['source','diff','feedback','native','version-resources'])text(id,'暂无版本');return;}
text('source',await file(v.source.uri));text('diff',await file(v.diff.uri));const j=c.jobs.find(j=>j.kind==='execute'&&j.version===v.version&&j.state==='settled');text('feedback',j?JSON.parse(await file(j.receipt.public_feedback.uri)):v.validation_error||(c.state==='completed'?'该版本仅生成；执行目标已满足':'等待执行'));text('native',j?{result:JSON.parse(await file(j.receipt.native_result.uri)),scene_condition:j.receipt.scene_condition}: '未评测');text('version-resources',{usage:v.usage,decision:JSON.parse(await file(v.decision.uri)),help:v.help?JSON.parse(await file(v.help.uri)):null,cell_used:c.used,cell_jobs:c.jobs});}
async function showLive(){const c=cell();if(!c||c.state!=='running'||!c.active_episode){$('live-image').hidden=true;text('live-status','当前没有在途仿真；历史请使用执行回放');return;}
try{const row=await request(ep(`/cells/${selected}/live`));text('live-status',`真实 live · v${row.active_version} · control step ${row.control_step} · seq ${row.sequence}`);if(row.sequence===liveSequence)return;liveSequence=row.sequence;const r=await fetch(ep(`/cells/${selected}/live/frame`),{headers});if(!r.ok)throw Error('Live frame unavailable');if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl=URL.createObjectURL(await r.blob());$('live-image').src=imageUrl;$('live-image').hidden=false;}catch(e){text('live-status','等待真实仿真帧');}}
$('create').onsubmit=async e=>{e.preventDefault();try{const tasks=[...$('tasks').selectedOptions].map(o=>o.value),policies=[...$('policies').selectedOptions].map(o=>o.value);const seeds=$('seeds').value.split(',').map(s=>Number(s.trim()));const selection={request_key:'ui-'+crypto.randomUUID(),tasks,seeds,policies,help_budget:Number($('help').value),mode:$('mode').value,target_depth:Number($('target-depth').value),target_valid_executions:Number($('target-executions').value),max_versions:Number($('max-versions').value),coverage:$('coverage').value,...($('planner-enabled').checked?{planner_features:{human_wait_seconds:Number($('human-wait').value),memory:$('memory-url').value.trim()?{base_url:$('memory-url').value.trim(),store_id:$('memory-authority').value.trim(),namespace:$('memory-namespace').value.trim()}:null}}:{}),...($('budget-config').value?JSON.parse($('budget-config').value):{})};const result=await request('/api/dev/experiments',selection);await history();await selectExperiment(result.experiment_id);}catch(e){error(e);}};
$('mode').onchange=()=>{const single=$('mode').value==='interactive';for(const id of ['tasks','policies']){const select=$(id);select.multiple=!single;if(single)select.selectedIndex=0;else for(const o of select.options)o.selected=true;}};
$('run').onclick=async()=>{try{await request(ep('/run'),{max_cells:Number($('max-cells').value)});await refresh();}catch(e){error(e);}};
$('cancel').onclick=async()=>{try{await request(ep('/cancel'),{});await refresh();}catch(e){error(e);}};
$('switch').onclick=async()=>{try{await request(ep('/switch'),{cell:selected,policy:$('switch-policy').value});await refresh();}catch(e){error(e);}};
$('version').onchange=()=>showVersion().catch(error);
$('load-replay').onclick=async()=>{try{const c=cell();const job=c?.jobs.find(j=>j.kind==='execute'&&j.version===Number($('replay-version').value)&&j.state==='settled');if(!job?.receipt.recording?.video?.uri)throw Error('此版本尚无可定位视频');const r=await fetch(filePath(job.receipt.recording.video.uri),{headers});if(!r.ok)throw Error('视频读取失败');if(videoUrl)URL.revokeObjectURL(videoUrl);videoUrl=URL.createObjectURL(await r.blob());$('replay').src=videoUrl;text('replay-status',`历史回放 v${job.version}，非 live`);}catch(e){error(e);}};
$('seek').onclick=()=>{$('replay').currentTime=Number($('seek-time').value);};
$('report').onclick=async()=>{try{text('report-data',await request(ep('/report')));}catch(e){error(e);}};
async function download(ext){const r=await fetch(ep(`/export.${ext}`),{headers});if(!r.ok)throw Error('导出失败');const a=document.createElement('a'),url=URL.createObjectURL(await r.blob());a.href=url;a.download=`${current}.${ext}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export-json').onclick=()=>download('json').catch(error);$('export-csv').onclick=()=>download('csv').catch(error);
(async()=>{try{const o=await request('/api/dev/options');for(const t of o.tasks)option($('tasks'),t.task_id,t.task_prompt,true);for(const p of o.policies){option($('policies'),p,p,true);option($('switch-policy'),p,p);}text('sdk',`${o.robot} · ${o.execution_arm} · SDK ${o.tasks[0].sdk.sdk_calls} calls / ${o.tasks[0].sdk.control_steps} control steps / ${o.tasks[0].sdk.attempt_seconds}s`);await history();}catch(e){error(e);}})();
setInterval(()=>refresh().catch(error),2000);

function budgetPreview(){const g=Number($('max-versions').value),e=Number($('target-executions').value),h=Number($('help').value),n=$('tasks').selectedOptions.length*$('policies').selectedOptions.length*$('seeds').value.split(',').length;const b={dev_calls:g,advisor_calls:h,executions:e,input_tokens:(g+h)*30000,output_tokens:g*8192+h*2048,provider_tokens:g*38192+h*32048,model_seconds:(g+h)*120,execution_seconds:e*540,help_count:h,help_credits:h,help_wait_seconds:h*120+($('planner-enabled').checked?g*Number($('human-wait').value):0),sdk_calls:e*80,control_steps:e*2400};text('budget-preview',{per_cell:b,cells:n,total:Object.fromEntries(Object.entries(b).map(([k,v])=>[k,v*n])),wall_seconds:n*(b.model_seconds+b.execution_seconds)+3600,retry_k2_reachable:Number($('target-depth').value)>=3});}
for(const id of ['max-versions','target-depth','target-executions','help','tasks','policies','seeds','coverage','planner-enabled','human-wait'])$(id).addEventListener('change',budgetPreview);setTimeout(budgetPreview,1000);

$('planner-enabled').addEventListener('change',()=>{if($('planner-enabled').checked){for(const o of $('policies').options)o.selected=o.value==='full_trace_aware_attention_planner';}budgetPreview();});
function showPlanner(){
  const p=cell()?.planner;
  $('planner-requests').replaceChildren();$('planner-work').replaceChildren();
  text('planner-memory',p?.memory||'未配置实时 Memory Service');
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
