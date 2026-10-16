(function () {
  const root = document.getElementById('attentionbench-clear');
  if (!root || root.dataset.mode !== 'live') return;

  const DEBUG_VIEW = new URLSearchParams(location.search).get('debug') === '1';
  const initialSelection = new URL(location.href).searchParams;
  const view = root.querySelector('.view');
  root.dataset.operatorView = String(!DEBUG_VIEW);
  const workspace=document.createElement('div');workspace.className='ab-workspace';
  view.before(workspace);workspace.append(view);
  const sidebar=document.createElement('aside');sidebar.className='ab-sidebar';sidebar.dataset.sidebar='';sidebar.setAttribute('aria-label','Tasks and Attention policy');workspace.prepend(sidebar);
  const sidebarToggle=document.createElement('button');sidebarToggle.type='button';sidebarToggle.dataset.sidebarToggle='';sidebarToggle.className='ab-sidebar-toggle';sidebarToggle.textContent='☰ Tasks';workspace.before(sidebarToggle);
  if(!DEBUG_VIEW){sidebarToggle.setAttribute('aria-label','Tasks');sidebarToggle.title='Show or hide the task list (T)';sidebarToggle.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10"/></svg><span>Tasks</span><i class="op-count" data-op-count hidden>0</i>';}
  root.querySelector('.ribbon').textContent = DEBUG_VIEW ? 'LIVE OPERATIONS · PERSISTED ATTENTION STORE' : 'TIDYBOT · LIVE OPERATIONS';
  root.querySelector('.demo').innerHTML = `${DEBUG_VIEW ? '● STORE' : ''}`;
  const devTag=document.createElement('span');devTag.className='op-dev-tag';devTag.textContent='Development test · not counted in comparisons';devTag.hidden=true;
  const alertsButton=document.createElement('button');alertsButton.type='button';alertsButton.className='op-alerts';alertsButton.title='Play a sound and show a notification when the robot needs your help';
  if (!DEBUG_VIEW && root.querySelector('.top')) {const top=root.querySelector('.top'),actions=document.createElement('div');actions.className='op-top-actions';actions.append(devTag,alertsButton);top.prepend(sidebarToggle);top.append(actions);}
  let runs = [];
  let launches = [];
  let devSessions = [];
  let selectedDev = initialSelection.get("dev");
  let launchOptions = null;
  let launchOptionsLoading = false;
  let selectedRun = initialSelection.get('run');
  let selectedRequest = null;
  let currentSnapshot = null;
  let lastRendered = '';
  let orchestrator = null;
  let orchestratorError = 'Orchestrator not connected';
  let sessionHistory = [];
  let sessions = [];
  let sessionFilter = 'all';
  let runFilter = 'current';
  let selectedSkill = null;
  let activeAttemptDag = null;
  let dagActionMessage = '';
  let historyGraph = null;
  let historyLoadedAt = 0;
  let observedRunPhase = null;
  let newlyLinkedRun = null;
  let refreshing = false;
  let refreshQueued = false;
  const draftCache = new Map();
  const runViewState=new Map(), replayPlayback=new Map(), switchRequests=new Map();
  let historyNotice=null;
  let sidebarQuery='',sidebarMessage='',linkedSource=null,linkedRelation='policy_change',linkedConfiguration=null;
  let sidebarFilter='all';
  const preference=(key,value)=>{try {if(value===undefined)return localStorage.getItem(key);localStorage.setItem(key,value);}catch(_){}};
  let sidebarOpen=preference('attention.sidebar-open')===null?!matchMedia('(max-width:900px)').matches:preference('attention.sidebar-open')!=='false';
  let policyControllerElement=null;
  const expandedHistory=new Set();
  const taskGroupChoices=new Map();
  const pins=new Set(JSON.parse(preference('attention.pinned-runs')||'[]'));
  sidebarFilter=['all','running','needs','failed','succeeded'].includes(preference('attention.sidebar-filter'))?preference('attention.sidebar-filter'):'all';
  function sidebarLayout(){sidebar.hidden=!sidebarOpen;workspace.dataset.sidebarOpen=String(sidebarOpen);sidebarToggle.setAttribute('aria-expanded',String(sidebarOpen));}
  sidebarToggle.addEventListener('pointerdown',event=>event.preventDefault());
  sidebarToggle.addEventListener('click',()=>{sidebarOpen=!sidebarOpen;preference('attention.sidebar-open',String(sidebarOpen));sidebarLayout();});
  sidebarLayout();
  const runningStatus=document.createElement('section');runningStatus.dataset.runningStatus='';runningStatus.className='ab-running-status';workspace.before(runningStatus);
  let deferredSelection = null;
  const pendingControls = new Set();
  const submissionReceipts = new Map();
  const previewTransfers = new WeakMap();
  const replaySelections = new Map();
  const cameraChoices = new Map();
  const handReplayCache = new Map();
  function cameraContext(snapshot) {
    return `${snapshot.run.run_id}:${stored(`attention.history:${snapshot.run.run_id}`)||'live'}`;
  }
  function cameraChoice(snapshot) { return cameraChoices.get(cameraContext(snapshot))||'external'; }
  function cameraStation(snapshot) {
    const chosen=snapshot.station.camera_views?.[cameraChoice(snapshot)];
    return chosen ? {...snapshot.station,camera_url:chosen.preview_url,camera_preview_url:chosen.preview_url,
      camera_state_url:chosen.state_url||chosen.preview_url.replace('/preview-frame?','/preview-state?')} : snapshot.station;
  }
  let previewPolling = false;
  let previewController = null;
  let previewHiddenReadAt = 0;
  let previewReading = null;
  let renderedPreview = null;
  const v2MonitorIdentities = new Map();
  function previewKey(snapshot) {
    return `${snapshot.run.run_id}:${snapshot.station.camera_monitor_identity || snapshot.station.camera_attempt_id}:${snapshot.station.camera_views?cameraChoice(snapshot):''}:${stored(`attention.history:${snapshot.run.run_id}`)||'live'}`;
  }
  function knownAge(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
  }
  function showPreviewState() {
    const badge = view.querySelector('[data-preview-freshness]');
    const frame = view.querySelector('#ab-camera-frame[data-live-stream-key]');
    if (!badge || !frame || !currentSnapshot) return;
    const key = previewKey(currentSnapshot);
    const source = previewReading?.key === key ? previewReading : null;
    const rendered = renderedPreview?.key === key ? renderedPreview : null;
    const meta = source?.meta || {state:'connecting',phase:'unknown'};
    const baseAge = knownAge(rendered?.age);
    const age = baseAge != null ? baseAge + (performance.now() - rendered.receivedAt)/1000
      : knownAge(meta.age_seconds) != null ? meta.age_seconds + (performance.now() - source.receivedAt)/1000 : null;
    const terminal = currentSnapshot.station.camera_monitor_terminal || ['ended','terminal_saved'].includes(meta.state);
    const paused = !currentSnapshot.station.camera_monitor_v2 && meta.state === 'waiting' && frame.naturalWidth;
    const state = terminal ? 'terminal_saved' : paused ? 'paused_saved' : rendered?.error ? 'error'
      : meta.state === 'live' && (!frame.naturalWidth || age == null || age > (meta.stale_after_s || 2))
        ? frame.naturalWidth ? 'stale' : 'connecting' : meta.state;
    frame.hidden = !frame.naturalWidth;
    badge.dataset.state = state;
    const seconds = age == null ? 'Frame age unavailable' : `about ${age.toFixed(1)} s ago`;
    const waited = source?.waitingAt == null ? '' : ` · waiting ${((performance.now()-source.waitingAt)/1000).toFixed(0)} s`;
    const phase = DEBUG_VIEW ? meta.phase || 'unknown' : ({env_create:'Getting ready',planner_prepare:'Planning movement',sdk_running:'Moving',execute:'Moving'}[meta.phase] || 'Preparing');
    badge.textContent = state === 'live' ? (DEBUG_VIEW ? `Live preview · frame ${seconds}` : age == null ? 'Live' : `Live · updated ${age.toFixed(1)} s ago`)
      : state === 'stale' ? (DEBUG_VIEW ? `Frame not updated · ${seconds}; phase: ${phase}` : stalePictureCopy(meta.phase, age))
      : state === 'paused_saved' ? 'Camera sampling paused · saved / non-live frame'
      : state === 'terminal_saved' ? frame.naturalWidth ? 'Last frame · saved / non-live' : 'Execution ended · no saved frame in this viewer; open historical recording'
      : ['waiting','waiting_first_frame','initializing','starting','connecting'].includes(state)
        ? `Waiting for the first real camera frame · phase: ${phase}${waited}`
      : state === 'error' ? `Preview error · ${DEBUG_VIEW ? rendered?.error || meta.error || meta.code || 'unknown' : plainError(rendered?.error || meta.error || meta.code)}; retrying`
      : state === 'offline' ? 'Preview disconnected · retrying; last frame is non-live'
      : 'Live preview unavailable · frame freshness unconfirmed';
    const warning = view.querySelector('[data-picture-warning]');
    if (warning && root.dataset.connection !== 'disconnected') {
      warning.hidden = state === 'live'; warning.textContent = warning.hidden ? '' : badge.textContent;
    }
  }
  function stopPreviewFrames(frame) {
    const transfer = previewTransfers.get(frame);
    if (transfer) {
      transfer.stopped = true; transfer.controller?.abort(); clearTimeout(transfer.timer);
      if (transfer.url) URL.revokeObjectURL(transfer.url);
      previewTransfers.delete(frame);
    }
    previewController?.abort();
    frame.onload = frame.onerror = null;
    frame.removeAttribute('src'); frame.hidden = true;
  }
  function startPreviewFrames(frame, station, key) {
    if (previewTransfers.has(frame)) return;
    frame.hidden = !frame.naturalWidth;
    const transfer = {stopped:false, sequence:null, url:null, timer:null, controller:null, failures:0};
    previewTransfers.set(frame, transfer);
    const current = () => !transfer.stopped && frame.isConnected && frame === view.querySelector('#ab-camera-frame') && key === previewKey(currentSnapshot);
    async function receive() {
      if (!current()) return;
      const startedAt = performance.now();
      transfer.controller = new AbortController();
      const timeout = setTimeout(() => transfer.controller?.abort(), 5000);
      try {
        // Dev v2 gets authoritative identity before every download. Old stations
        // retain their existing preview protocol and bounded after sequence.
        if (station.camera_monitor_v2) await refreshPreview();
        if (!current()) return;
        const meta = previewReading?.key === key ? previewReading.meta : null;
        if (station.camera_monitor_v2 && (!meta || !['live','stale'].includes(meta.state) || currentSnapshot.station.camera_monitor_terminal)) {
          transfer.failures++; showPreviewState(); return;
        }
        const url = new URL(frame.dataset.previewUrl || station.camera_preview_url, location.href);
        if (station.camera_monitor_v2) {
          for (const [param,field] of [['episode','episode_id'],['version','version'],['seq','sequence'],['sha','frame_sha256']]) {
            if (meta[field] == null) throw new Error('Missing frame identity');
            url.searchParams.set(param,meta[field]);
          }
        } else if (transfer.sequence != null) url.searchParams.set('after',transfer.sequence);
        const response = await fetch(url.href,{cache:'no-store',signal:transfer.controller.signal});
        if (!current()) return;
        if (response.status === 409 || response.status === 404) {
          // Metadata/frame publication or episode transition race. Drop the
          // response and read identity again; never install an unbound frame.
          transfer.failures++; return;
        }
        if (response.status !== 204) {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          if (station.camera_monitor_v2) {
            for (const [header,field] of [['X-Episode-Id','episode_id'],['X-Frame-Version','version'],['X-Frame-Sequence','sequence'],['X-Frame-Sha256','frame_sha256']]) {
              if (response.headers.get(header) !== String(meta[field])) throw new Error('Frame response identity mismatch');
            }
          }
          const blob = await response.blob();
          if (!blob.type.startsWith('image/') || !blob.size || blob.size > 8000000) throw new Error('Invalid camera frame');
          if (!current()) return;
          const url = URL.createObjectURL(blob), previousUrl = transfer.url;
          try {
            const candidate = new Image(); candidate.src = url; await candidate.decode();
            if (!current()) {URL.revokeObjectURL(url);return;}
            transfer.url = url; frame.src = url; frame.hidden = false;
          } catch (error) {URL.revokeObjectURL(url);throw error;}
          finally {if(previousUrl && transfer.url === url)URL.revokeObjectURL(previousUrl);}
          if (!current()) return;
          transfer.sequence = response.headers.get('X-Frame-Sequence');
          const headerAge = response.headers.get('X-Frame-Age');
          const age = headerAge == null || headerAge.trim() === '' ? null : knownAge(Number(headerAge));
          renderedPreview = {key,error:false,sequence:transfer.sequence,
            age:age == null ? null : age + (performance.now()-startedAt)/1000,receivedAt:performance.now()};
        } else if (renderedPreview?.key === key) renderedPreview.error = false;
        transfer.failures = 0; showPreviewState();
      } catch (error) {
        if (current()) {
          const previous = renderedPreview?.key === key ? renderedPreview : {};
          renderedPreview = {...previous,key,error:error.message || 'Network/decode failure'};
          transfer.failures++;showPreviewState();
        }
      } finally {
        clearTimeout(timeout);transfer.controller=null;
        if (current()) transfer.timer=setTimeout(receive,document.hidden?5000:Math.min(5000,200*Math.pow(2,Math.min(transfer.failures,5))));
      }
    }
    transfer.receive = receive;
    receive();
  }
  async function refreshPreview() {
    showPreviewState();
    const frame = view.querySelector('#ab-camera-frame[data-live-stream-key]');
    const snapshot = currentSnapshot;
    if (previewPolling || !frame || !cameraStation(snapshot)?.camera_state_url) return;
    if(document.hidden && performance.now()-previewHiddenReadAt<5000)return;
    previewHiddenReadAt=performance.now();
    const key=previewKey(snapshot),controller=new AbortController();previewController=controller;
    const timeout=setTimeout(()=>controller.abort(),3000);previewPolling=true;
    try {
      const response=await fetch(cameraStation(snapshot).camera_state_url,{cache:'no-store',signal:controller.signal});
      const meta=await response.json();
      if (!response.ok && meta.state !== 'error') throw new Error(`HTTP ${response.status}`);
      if (view.querySelector('#ab-camera-frame')!==frame || previewKey(currentSnapshot)!==key) return;
      if (!meta || typeof meta.state !== 'string') throw new Error('Invalid camera metadata');
      const waitingAt=previewReading?.key===key?previewReading.waitingAt:performance.now();
      previewReading={key,meta,receivedAt:performance.now(),waitingAt};
      // A good metadata read does not clear a decode/download error; only a
      // successfully decoded frame does. Thus bad images stay clearly errors.
      const resolution=view.querySelector('[data-camera-resolution]');
      if(resolution && meta.source_width && meta.source_height)resolution.textContent=`Source resolution ${meta.source_width} × ${meta.source_height} · live JPEG preview`;
      showPreviewState();
    } catch(error) {
      if(view.querySelector('#ab-camera-frame')===frame && previewKey(currentSnapshot)===key) {
        previewReading={key,meta:{state:'error',error:error.message,phase:'unknown'},receivedAt:performance.now(),waitingAt:previewReading?.key===key?previewReading.waitingAt:performance.now()};showPreviewState();
      }
    } finally {clearTimeout(timeout);previewPolling=false;if(previewController===controller)previewController=null;}
  }
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden){
      previewHiddenReadAt=0;
      const frame=view.querySelector('#ab-camera-frame[data-live-stream-key]'),transfer=frame&&previewTransfers.get(frame);
      refreshPreview();
      if(transfer && !transfer.controller){clearTimeout(transfer.timer);transfer.receive();}
    }
  });
  function stored(key, value) {
    try {
      if (arguments.length === 1) return sessionStorage.getItem(key);
      if (value == null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (_) { /* In-memory drafts still work when storage is disabled. */ }
  }
  let pendingLaunch = stored('attention.pending-launch');
  function selectRun(runId) {
    rememberRunView();
    if(selectedRun!==runId)sidebarMessage='';
    dismissRequestAlert();
    if(attemptDagDialog.open)closeAttemptDag();
    if(flowDetailDialog.open)closeFlowDetail();
    if(moreDialog.open)closeMore();
    selectedRun = runId;
    selectedRequest = null;
    const url = new URL(location.href);
    url.searchParams.delete('dev');
    if (runId) url.searchParams.set('run', runId);
    else url.searchParams.delete('run');
    history.replaceState(null, '', url);
  }
  function rememberRunView(){
    if(!currentSnapshot)return;
    const input=view.querySelector('#ab-hint-content');
    runViewState.set(currentSnapshot.run.run_id,{request:selectedRequest,hint:input?{key:input.dataset.draftKey,value:input.value,range:[input.selectionStart,input.selectionEnd,input.selectionDirection],focused:document.activeElement===input}:null});
    saveReplayPositions();
    view.querySelectorAll('video[data-history-replay]').forEach(v=>replayPlayback.set(v.dataset.replayKey,{paused:v.paused,rate:v.playbackRate}));
  }
  // Task navigation projection. Keep ledger identities out of visible names.
  const taskTitles = {cube_lift:'Lift the cube',cube_stack:'Stack the cubes',counter_to_sink:'Move to the sink',counter_to_cab:'Move into the cabinet',pick_place_counter_to_sink:'Pick Place: Counter → Sink',open_cabinet:'Open the cabinet'};
  function resolvedTask(item){
    if(!item.task_id || !item.suite || /^(unknown|undefined|null)$/i.test(item.task_id))return null;
    const saved=item.task_name||item.task_display_name;
    if(typeof saved==='string'&&saved.trim()&&!/^unknown\b/i.test(saved))return saved.trim();
    if(Object.hasOwn(taskTitles,item.task_id))return taskTitles[item.task_id];
    const profiles=[...(launchOptions?.profiles||[]),...(launchOptions?.dev_loop?.profiles||[])];
    return profiles.some(p=>p.suite===item.suite&&(p.task_id||p.task)===item.task_id)?label(item.task_id):null;
  }
  function taskDisplayName(item){return `${resolvedTask(item)||taskName(item.task_id)}${item.name_suffix?` -- ${item.name_suffix}`:''}`;}
  function customRunName(item){
    const fallback=item.name_suffix?taskDisplayName(item):'';
    const name=item.display_name?.trim();
    if(!name)return fallback;
    // The previous server generated robot / arm / base / ISO time / ID / digest.
    if(/^.+ · .+ · .+ · \d{4}-\d\d-\d\dT[^ ]+Z · [^ ]+(?: · [a-f0-9]{6})?$/.test(name))return fallback;
    if(name===`${taskName(item.task_id)} · ${item.run_id?.slice(-8)}`)return fallback;
    return name;
  }
  function recordTime(item){
    const value=item.created_at??item.started_at;
    if(value==null)return 'Time not recorded';
    const date=typeof value==='number'?new Date(value*1000):new Date(value);
    return Number.isNaN(date.getTime())?'Time not recorded':date.toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  }
  function displayRun(item){return customRunName(item)||`${resolvedTask(item)||'Unlinked record'} · ${recordTime(item)}`;}
  function devNavigationRecord(session){
    const cfg=session.locked?.config||{};
    return {run_id:session.session_id,session_id:session.session_id,suite:cfg.suite,task_id:cfg.task_id,task_name:cfg.task_name,
      created_at:session.created_at,seed:cfg.seed,name_suffix:session.name_suffix,history_deleted_at:session.history_deleted_at,robot:{model:cfg.robot,execution_arm:cfg.execution_arm},
      status:session.state,reason:session.reason,end_summary:devEndSummary(session),development:true,lifecycle:session.lifecycle,versions:session.versions||[],jobs:session.jobs||[]};
  }
  function allNavigationRecords(){
    const nativeIds=new Set(devSessions.map(s=>`run:attention-${s.session_id}`));
    return [...runs.filter(r=>!nativeIds.has(r.run_id)),...devSessions.map(devNavigationRecord)];
  }
  function navigationRecords(){return allNavigationRecords().filter(r=>!r.history_deleted_at||recordIsActive(r));}
  function deletedNavigationRecords(){return allNavigationRecords().filter(r=>r.history_deleted_at&&!recordIsActive(r));}
  function recordIsActive(item){if(item.lifecycle)return ['model','advisor','approval','sdk','preparing'].includes(item.lifecycle.phase);return item.development?['ready','running','stopping'].includes(item.status):item.status==='running';}
  function recordStatus(item){
    if(!DEBUG_VIEW&&item.development&&/infrastructure|dispatch unknown|outcome_unknown/i.test(String(item.reason||'')))return 'System problem';
    if(item.development&&item.lifecycle)return ({prepared:'Prepared / Not started',queued:'Queued / Next round',model:'Model working',advisor:'Waiting for Advisor',approval:'Needs human approval',sdk:'SDK running',preparing:'Preparing public work',review:'Needs review',terminal:'Ended'})[item.lifecycle.phase] || 'Unconfirmed';
    if(!DEBUG_VIEW&&item.development&&item.status==='running')return 'Running';
    if(!DEBUG_VIEW&&item.development&&item.status==='completed')return 'Finished';
    if(item.development&&item.status==='budget_exhausted'&&item.end_summary)return item.end_summary.short;
    if(item.development)return {ready:'Ready',running:'Developing',stopping:'Stopping',stopped:'Stopped',budget_exhausted:'Budget reached',needs_review:'Needs review',failed:'Failed'}[item.status]||label(item.status||'Not recorded');
    return {running:'Running',completed:'Finished',succeeded:'Succeeded',failed:'Failed',cancelled:'Stopped',timed_out:'Timed out'}[item.status]||label(item.status||'Not recorded');
  }
  // End task navigation projection.
  function policyTitle(id){return ({autonomous:'Autonomous',reactive_help:'Reactive help',trace_aware_hint_only:'Trace aware Hint',full_trace_aware_attention_planner:'Full trace aware planner',retry_k_then_ask:'Retry K then ask'})[id]||label(id);}
  function devEndSummary(s){
    const reason=String(s.reason||'');
    const approval=/^motion approval (rejected|timed_out|cancelled|invalidated|authorization_exhausted)(; unresolved work retained)?$/.exec(reason);
    if(approval){
      const exhausted=approval[1]==='authorization_exhausted',review=!!approval[2]||s.state==='needs_review';
      const text=({rejected:'declined',timed_out:'timed out',cancelled:'cancelled',invalidated:'invalidated by a version change'})[approval[1]];
      const explanation=({rejected:'the human declined the action permission',timed_out:'the action approval deadline expired',cancelled:'the action approval was cancelled',invalidated:'the action permission no longer matched the current code or configuration',authorization_exhausted:'the original authorization or budget was exhausted'})[approval[1]];
      return {title:(exhausted?'Ended · Action permission closed · Original authorization or budget exhausted · This action was not executed':`Ended · Action approval ${text}`)+(review?' · Review required for unresolved work':''),short:exhausted?'Action permission closed':`Approval ${text}`,key:'action_approval',explanation,review,authorization_exhausted:exhausted};
    }
    const keys=[['model_calls','Model call','Model limit'],['dev_calls','Model call','Model limit'],['executions','Execution','Execution limit'],['tokens','Token','Token limit'],['dev_tokens','Model token','Token limit'],['provider_tokens','Provider token','Token limit'],['sdk_calls','SDK call','SDK limit'],['execution_seconds','Execution time','Time limit']];
    const limited=s.state==='budget_exhausted'||/budget|quota|limit.*(?:reached|exhausted)/i.test(reason);
    if(limited){
      const scopes=[{used:s.used||{},budget:s.budget||{},prefix:''},{used:s.ledger?.goal?.used||{},budget:s.ledger?.goal?.limits||{},prefix:'Experiment '}];
      for(const scope of scopes)for(const [key,name,short] of keys){
        const limit=Number(scope.budget[key]),used=Number(scope.used[key]||0);
        if(limit>0&&used>=limit)return {title:`Ended · ${scope.prefix}${name} limit reached (${used}/${limit})`,short:`${short} reached`,key};
      }
      if(/wall|deadline|time budget/i.test(reason))return {title:'Ended · Session time limit reached',short:'Time limit reached',key:'wall_seconds'};
      return {title:'Ended · Resource budget exhausted',short:'Budget exhausted'};
    }
    if(s.state==='stopped'||s.state==='cancelled')return {title:'Stopped · Session closed',short:'Stopped'};
    if(s.state==='needs_review')return {title:'Review required · Execution outcome is unresolved',short:'Needs review'};
    if(s.state==='failed')return {title:'Ended · Execution or service failure',short:'Failed'};
    return {title:'Ended · Session completed',short:'Finished'};
  }
  function mountPolicyController(){
    if(!policyControllerElement)return;
    if(!currentSnapshot?.run){policyControllerElement.remove();return;}
    if (!DEBUG_VIEW) {
      const host=view.querySelector('[data-policy-host]');
      // Only swap in the fresh selector when something visible changed, so a click is never lost to a background refresh.
      if (!host) policyControllerElement.remove();
      else if (host.firstElementChild === policyControllerElement) { /* already mounted */ }
      else if (host.firstElementChild?.outerHTML === policyControllerElement.outerHTML) policyControllerElement.remove();
      else host.replaceChildren(policyControllerElement);
      return;
    }
    const status=view.querySelector('[data-core-region="status"]');if(!status)return;
    let host=view.querySelector('[data-policy-host]');
    if(!host){host=document.createElement('div');host.dataset.policyHost='';host.className='ab-runtime-policy';status.after(host);}
    if(host.firstElementChild!==policyControllerElement)host.replaceChildren(policyControllerElement);
  }
  function compactHistory(group,rows){
    const expanded=expandedHistory.has(group)||Boolean(sidebarQuery);
    const visible=expanded?rows:rows.filter((r,i)=>i<5||r.run_id===(selectedDev||selectedRun)||recordIsActive(r));
    return visible.map(sidebarRunCard).join('')+(rows.length>5?`<button type="button" class="ab-history-more" data-history-more="${esc(group)}" aria-expanded="${expanded}">${expanded?(DEBUG_VIEW?'Show fewer records':'Show fewer runs'):`Show ${rows.length-visible.length} more ${DEBUG_VIEW?'records':'runs'}`}</button>`:'');
  }
  function devPolicyDescription(id){
    if(currentSnapshot?.dev_v2)return ({autonomous:'Continue without requesting help.',demo_first:'Use the frozen public SDK example before v1.',reactive_help:'Ask the Advisor after a public execution failure.',retry_k_then_ask:'Ask the Advisor after 2 consecutive public failures.',budget_matched_random_escalation:'Use preregistered random failure slots within the help budget.',trace_aware_hint_only:'Use the public action trace to choose repair hints.',full_trace_aware_attention_planner:'Uses trusted Memory, requests human approval for motion and prepares public work while waiting.'})[id]||'';
    return ({
    autonomous:'Continue automatically without asking for human guidance.',
    reactive_help:'Ask for your guidance after each failed iteration.',
    trace_aware_hint_only:'Use the action trace to decide when human guidance is needed.',
    retry_k_then_ask:'Ask for your guidance after 2 consecutive failed iterations.'
  })[id]||'';}
  function devPolicyUnavailable(c,s){
    if(c?.binding||!s)return '';
    const ended=!['ready','running','stopping'].includes(s.state);
    return `<div class="ab-dev-policy-unavailable" data-dev-policy-unavailable><strong>${ended?esc(devEndSummary(s).title):'Policy control unavailable for this session'}</strong><p>No Attention policy was recorded for this session. History is read-only.</p></div>`;
  }
  function devPolicySummary(c,s,readOnly,history,pending){
    if(!c?.binding||!s)return '';
    if(currentSnapshot?.dev_v2)return `<div class="ab-dev-policy-live"><span class="ab-context-label">Recorded strategy</span><strong>${esc(policyTitle(c.active_policy))}</strong><p>Advisor help is automatic and uses public execution feedback. Memory starts empty; this Dev session grants no formal approvals.</p>${currentSnapshot.dev_v2.pending_policy?`<p>Pending: ${esc(policyTitle(currentSnapshot.dev_v2.pending_policy))} · applies before the next model version.</p>`:''}</div>`;
    const attempt=history?currentSnapshot.attempts.find(a=>a.attempt_id===stored(`attention.history:${s.session_id}`)):null;
    const policy=history?attempt?.attention_policy:c.active_policy;
    const waiting=!readOnly&&c.hint?.state==='pending';
    const job=s.jobs.find(j=>j.state==='claimed');
    const status=readOnly?(history?'Viewing a past execution. Return to Current to change the running session policy.':'This session is read-only.'):
      waiting?`Waiting for your reply before v${c.hint.version}.`:
      job?`${job.kind==='execute'?'Executing':'Generating'} v${job.version}. You can select another policy while this step runs.`:
      'The next iteration follows this policy. Human guidance is requested according to its rules.';
    return `<div class="ab-dev-policy-live" data-dev-policy-live data-waiting="${waiting}" role="status"><span class="ab-context-label">${readOnly?'Recorded policy':'Currently in effect'}</span><strong data-dev-effective-policy>${esc(policy?policyTitle(policy):'Not recorded')}</strong><p>${esc(status)}</p>${pending&&!readOnly?'<p data-dev-switch-timing>The current policy stays in effect until the executor confirms the switch. Any in-flight step finishes first.</p>':''}${waiting?'<button type="button" data-dev-open-guidance>Reply to request →</button>':''}</div>`;
  }
  function updateReplayPolicy(snapshot,video){
    updateReplayArms(snapshot,video);
    if(!snapshot?.attention_control||!video)return;
    if (!DEBUG_VIEW && !workspace.querySelector('[data-policy-controller]')) return;
    const id=stored(`attention.history:${snapshot.run.run_id}`),attempt=snapshot.attempts.find(a=>a.attempt_id===id);
    if(!attempt)return;
    if(snapshot.dev_session){
      const active=attempt.attention_policy;
      workspace.querySelectorAll('[data-attention-policy]').forEach(b=>{b.disabled=true;b.dataset.state=b.dataset.attentionPolicy===active?'Active':'Historical';b.setAttribute('aria-pressed',String(b.dataset.attentionPolicy===active));b.querySelector('small').textContent=b.dataset.attentionPolicy===active?'Active at execution':'Historical';});
      return;
    }
    const trace=Object.values(snapshot.traces).find(t=>t.attempt_id===id),timing=trace?.recording?.replay_timing;
    let traceSeconds=video.currentTime;
    if(timing){const n=timing.frame_video_seconds.findLastIndex(t=>t<=video.currentTime);traceSeconds=timing.frame_trace_seconds[Math.max(0,n)];}
    const segments=snapshot.attention_control.segments;
    let active=segments.filter(s=>!s.attempt_id&&s.effective_at<=attempt.started_at).at(-1)?.policy_id||segments[0]?.policy_id;
    for(const s of segments){if(s.attempt_id===id&&s.trace_seconds!=null&&s.trace_seconds<=traceSeconds)active=s.policy_id;}
    workspace.querySelectorAll('[data-attention-policy]').forEach(b=>{b.disabled=true;b.dataset.state=b.dataset.attentionPolicy===active?'Active':'Historical';b.setAttribute('aria-pressed',String(b.dataset.attentionPolicy===active));b.querySelector('small').textContent=b.dataset.attentionPolicy===active?'Active at replay':'Historical';});
    let state=workspace.querySelector('[data-policy-request]');if(!state){state=document.createElement('p');state.dataset.policyRequest='';workspace.querySelector('[data-policy-controller]').append(state);}state.textContent=`Read-only replay · ${policyTitle(active)} at ${replayTime(traceSeconds)}`;
  }
  function policyPath(item){
    const c=item.attention_control|| (item.run_id===currentSnapshot?.run.run_id?currentSnapshot.attention_control:null);
    const segments=c?.segments||[{policy_id:item.initial_policy||item.policy_id||currentSnapshot?.run.policy_id}];
    const path=segments.map(s=>policyTitle(s.policy_id));
    return `<details data-run-path="${esc(item.run_id)}"><summary>${esc(path.length>2?`${path[0]} → ${path.at(-1)} · ${path.length-1} switches`:path.join(' → '))}</summary><ol>${segments.map(s=>`<li>${esc(policyTitle(s.policy_id))}<small>${s.effective_at?esc(dateTime(s.effective_at)):'Start time not recorded'}</small></li>`).join('')}</ol></details>`;
  }
  function renderRunningStatus(){
    const executing=navigationRecords().filter(r=>recordIsActive(r)&&r.run_id!==(selectedDev||selectedRun));
    runningStatus.hidden=!executing.length;
    runningStatus.innerHTML=executing.map(r=>`<div><strong>${r.lifecycle?esc(recordStatus(r)):'Executing'} · ${esc(displayRun(r))}</strong><span>${esc(r.robot?.model||'')} · ${Number(r.pending_hints||0)} pending Hints · ${esc(r.interrupt?.state?label(r.interrupt.state):r.lifecycle?recordStatus(r):'Running')}</span><button type="button" ${r.development?'data-view-dev-running':'data-view-running'}="${esc(r.run_id)}">View executing run</button><button type="button" class="danger" ${r.development?'data-stop-dev':'data-stop-executing'}="${esc(r.run_id)}" ${r.interrupt?'disabled':''} title="Stop target: ${esc(displayRun(r))} (${esc(r.run_id)})">■ Stop</button></div>`).join('');
    runningStatus.querySelectorAll('button').forEach(b=>b.addEventListener('pointerdown',e=>e.preventDefault()));
    runningStatus.querySelectorAll('[data-view-dev-running]').forEach(b=>b.addEventListener('click',()=>navigateRecord(b.dataset.viewDevRunning)));
    runningStatus.querySelectorAll('[data-stop-dev]').forEach(b=>b.addEventListener('click',()=>{const session=devSessions.find(s=>s.session_id===b.dataset.stopDev);return sidebarWrite(session?.channel==='dev_v2'?`/api/dev-v2/experiments/${session.experiment_id}/cancel`:`/api/dev/sessions/${encodeURIComponent(b.dataset.stopDev)}/stop`,{});}));
    runningStatus.querySelectorAll('[data-view-running]').forEach(b=>b.addEventListener('click',()=>navigateRun(b.dataset.viewRunning)));
    runningStatus.querySelectorAll('[data-stop-executing]').forEach(b=>b.addEventListener('click',()=>sidebarWrite(`/api/runs/${encodeURIComponent(b.dataset.stopExecuting)}/interrupt`,{})));
  }
  function sidebarIcon(taskId){
    const paths=/sink/.test(taskId)?'<path d="M4 13h16v4a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3zM6 13V7a3 3 0 0 1 6 0v2m-2 0h4M3 13h18"/>':/cube/.test(taskId)?'<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9m-4-16.5 8 4.5"/>':/cab/.test(taskId)?'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M12 3v18M9 10v4m6-4v4"/>':'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v3H9zM9 11h6m-6 5h6"/>';
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  }
  function sidebarTaskSummary(rows,run){
    const task=rows[0].task_id;
    const viewing=rows.some(r=>r.development?r.session_id===selectedDev:!selectedDev&&r.run_id===selectedRun);
    const executing=rows.filter(recordIsActive).length;
    return `<summary class="ab-task-summary"><span class="ab-task-icon">${sidebarIcon(task)}</span><span class="ab-task-copy"><strong>${esc(resolvedTask(rows[0]))}</strong><span class="ab-task-meta">${esc({robocasa365:'RoboCasa365',robocasa:'RoboCasa',robosuite:'Robosuite'}[rows[0].suite]||rows[0].suite)}${viewing?'<span class="ab-task-viewing">Viewing</span>':''}${executing?`<span class="ab-task-executing">${executing} active</span>`:''}</span>${DEBUG_VIEW?'':`<span class="op-dots" aria-hidden="true">${rows.slice(0,14).reverse().map(r=>`<i class="op-dot ${recordTone(r)}" title="${esc(recordStatus(r))}"></i>`).join('')}</span>`}</span><span class="ab-task-count"><b>${rows.length}</b><span>${DEBUG_VIEW?(rows.length===1?'record':'records'):(rows.length===1?'run':'runs')}</span></span><span class="ab-task-chevron" aria-hidden="true">›</span></summary>`;
  }
  function sidebarRunCard(r){
    const viewing=r.development?r.session_id===selectedDev:!selectedDev&&r.run_id===selectedRun;
    const kind=r.development?'dev':'run';
    const target=r.development?`data-dev-session="${esc(r.session_id)}"`:`data-view-run="${esc(r.run_id)}"`;
    const arm={both:'Both arms',left:'Left arm',right:'Right arm',single:'Single arm'}[r.robot?.execution_arm];
    const meta=[r.robot?.model,arm].filter(Boolean).join(' · ')||(DEBUG_VIEW?'Robot details unavailable':'');
    const title=customRunName(r)||(DEBUG_VIEW?recordTime(r):relativeTime(r.created_at??r.started_at,recordTime(r)));
    return `<article data-run-entry="${esc(r.run_id)}" data-viewing="${viewing}" data-development="${Boolean(r.development)}" data-tone="${recordTone(r)}"><div class="ab-sidebar-run-head"><button type="button" ${target} aria-current="${viewing?'page':'false'}" title="${esc(displayRun(r))}"><strong data-run-name title="${esc(recordTime(r))}">${pins.has(r.run_id)?'★ ':''}${esc(title)}</strong><span class="ab-run-status" data-status="${esc(r.status||'unknown')}">${recordIsActive(r)?`<b data-executing-badge>${esc(recordStatus(r))}</b>`:esc(recordStatus(r))}</span></button>${r.session_id?.startsWith('v2-')?`${DEBUG_VIEW ? '<span title="Immutable experiment evidence">Retained</span>' : ''}`:r.history_deleted_at?`<button type="button" class="ab-record-restore" data-restore-record="${esc(r.run_id)}" data-record-kind="${kind}">Restore</button>`:`<button type="button" class="ab-record-delete" data-delete-record="${esc(r.run_id)}" data-record-kind="${kind}" aria-label="Delete ${esc(displayRun(r))}" title="${recordIsActive(r)?'Stop this record before deleting it':'Delete record · recoverable'}" ${recordIsActive(r)?'disabled':''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg></button>`}<details class="ab-record-more" data-record-more="${esc(r.run_id)}"><summary aria-label="More options for ${esc(displayRun(r))}">⋯</summary><div class="ab-record-menu"><button type="button" data-pin-run="${esc(r.run_id)}" aria-pressed="${pins.has(r.run_id)}">${pins.has(r.run_id)?'Unpin record':'Pin record'}</button>${!r.development?`<div class="ab-run-actions"><button type="button" data-copy-run="${esc(r.run_id)}">Copy configuration</button><button type="button" data-retry-run="${esc(r.run_id)}">Retry run</button></div><details data-run-name-editor="${esc(r.run_id)}"><summary>Edit name</summary><form data-run-rename="${esc(r.run_id)}"><input name="display_name" aria-label="Run display name" value="${esc(customRunName(r)||'')}" placeholder="Give this execution a name" maxlength="160" required><button>Save name</button></form></details>${policyPath(r)}`:`<small>${r.versions.length} policy versions · ${r.jobs.filter(j=>j.kind==='execute').length} executions</small>`}<small>Started ${esc(dateTime(r.created_at??r.started_at))} · Seed ${esc(r.seed??'Not recorded')}</small>${DEBUG_VIEW ? `<code>${esc(r.run_id)}</code><button type="button" data-copy-record-id="${esc(r.run_id)}">Copy full ID</button>` : ''}</div></details></div>${meta?`<small class="ab-run-metadata">${esc(meta)}</small>`:''}${r.development?'<span class="ab-development-tag">Development</span>':''}${customRunName(r)?`<small class="ab-record-time">${esc(recordTime(r))}</small>`:''}</article>`;
  }
  function sidebarControlIcon(name){
    const paths={
      controller:'<path d="M4 7h7m4 0h5M4 17h3m4 0h9"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
      autonomous:'<circle cx="12" cy="12" r="9"/><path d="m16 8-2 6-6 2 2-6z"/>',
      reactive_help:'<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l-3 3V11.5A7.5 7.5 0 0 1 9.5 4H16"/><path d="M18 2v6m-3-3h6M7 10h7m-7 4h5"/>',
      trace_aware_hint_only:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9zM14 3v6h6M8 13h8m-8 4h5"/>',
      full_trace_aware_attention_planner:'<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-5h14v5"/>',
      retry_k_then_ask:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
      lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
      activity:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
      link:'<path d="m10 14 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"/>',
      idle:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>'
    };
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.controller}</svg>`;
  }
  function renderSidebar(){
    renderRunningStatus();
    const c=currentSnapshot?.attention_control;
    const run=currentSnapshot?.run;
    const history=run&&stored(`attention.history:${run.run_id}`)&&stored(`attention.history:${run.run_id}`)!=='live';
    const readOnly=!c||c.read_only||history;
    const attentionEndpoint=selectedDev?`/api/dev/sessions/${encodeURIComponent(selectedDev)}`:`/api/runs/${encodeURIComponent(run?.run_id||'')}`;
    const searchFocus=document.activeElement?.hasAttribute('data-run-search');
    const searchRange=searchFocus?[document.activeElement.selectionStart,document.activeElement.selectionEnd]:null;
    const navScroll=sidebar.querySelector('nav')?.scrollTop||0;
    const renameDrafts=new Map([...sidebar.querySelectorAll('[data-run-name-editor][open] input')].map(e=>[e.closest('[data-run-rename]').dataset.runRename,{value:e.value,range:[e.selectionStart,e.selectionEnd,e.selectionDirection],focused:document.activeElement===e}]));
    const opened=[...sidebar.querySelectorAll('details[open]')].map(d=>d.dataset.taskGroup||d.dataset.runPath||d.dataset.runNameEditor||d.dataset.recordMore||(d.hasAttribute('data-deleted-history')?'trash':d.hasAttribute('data-unlinked-archive')?'archive':d.classList.contains('ab-record-jump')?'jump':null));
    const records=navigationRecords();
    const linked=records.filter(r=>resolvedTask(r));
    const archived=records.filter(r=>!resolvedTask(r));
    const deleted=deletedNavigationRecords();
    const candidates=linked.filter(r=>`${displayRun(r)} ${r.suite} ${r.task_id} ${r.seed} ${r.robot?.model||''} ${r.robot?.execution_arm||''}`.toLowerCase().includes(sidebarQuery.toLowerCase())).filter(sidebarMatches).sort((a,b)=>Number(pins.has(b.run_id))-Number(pins.has(a.run_id))||Number(recordIsActive(b))-Number(recordIsActive(a))||Number(b.created_at||b.started_at||0)-Number(a.created_at||a.started_at||0)||a.run_id.localeCompare(b.run_id));
    const groups=new Map();for(const r of candidates){const k=`${r.suite}:${r.task_id}`;if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r);}
    const request=c?.request;
    if(request&&['Pending','Active','Cancelled','Rejected'].includes(sidebarMessage))sidebarMessage=request.state;
    if(request&&run&&switchRequests.get(run.run_id)?.request_id===request.request_id)switchRequests.delete(run.run_id);
    const pendingV2=run&&switchRequests.get(run.run_id);
    if(pendingV2?.v2&&(c?.active_policy===pendingV2.policy_id||Date.now()-pendingV2.at>20*60*1000||readOnly))switchRequests.delete(run.run_id);
    const local=run&&switchRequests.get(run.run_id);
    const pending=request?.state==='Pending'||local?.state==='Requested';
    const executing=navigationRecords().filter(recordIsActive);
    sidebar.innerHTML=`<header class="ab-sidebar-header"><span class="ab-sidebar-eyebrow">WORKSPACE</span><h2>Tasks</h2><small>Choose a task to browse its history</small><div class="ab-sidebar-totals"><span>${new Set(linked.map(r=>`${r.suite}:${r.task_id}`)).size} tasks</span><span>${linked.length} ${DEBUG_VIEW?'records':'runs'}</span><span class="ab-total-executing" data-active="${records.some(recordIsActive)}"><i aria-hidden="true"></i>${records.filter(recordIsActive).length} ${DEBUG_VIEW?'active':'running'}</span></div></header><label class="ab-sidebar-search">Search tasks & history<span class="ab-search-field"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg><input data-run-search type="search" value="${esc(sidebarQuery)}" placeholder="Task, name or robot"></span></label>
      ${DEBUG_VIEW?'':sidebarFilters(linked)}
      ${historyNotice?`<p class="ab-history-notice" data-history-notice role="status">${esc(historyNotice.message)}${historyNotice.kind?` <button type="button" data-history-undo>Undo</button>`:''}</p>`:''}
      ${DEBUG_VIEW ? `<details class="ab-record-jump"><summary>Jump to a record</summary><label class="live-run-picker">Viewing record<select id="ab-live-run" aria-label="Viewing record">${linked.map(r=>`<option value="${esc(r.run_id)}" ${r.run_id===(selectedDev||selectedRun)?'selected':''}>${esc(displayRun(r))}</option>`).join('')}</select></label></details>` : ''}
      <div class="ab-task-library-label"><span>Task history</span><span>${sidebarQuery?`${candidates.length} matching records`:'Most recent first'}</span></div><nav aria-label="Tasks">${[...groups].map(([group,rows])=>`<details data-task-group="${esc(group)}" data-viewing="${rows.some(r=>r.run_id===(selectedDev||selectedRun))}" ${(sidebarQuery||(taskGroupChoices.get(`${selectedDev||selectedRun}:${group}`)??rows.some(r=>r.run_id===(selectedDev||selectedRun))))?'open':''}>${sidebarTaskSummary(rows,run)}<div class="ab-task-runs">${compactHistory(group,rows)}</div></details>`).join('')||'<p class="ab-sidebar-empty">No matching records.<small>Try another task, name or robot.</small></p>'}</nav>
      ${archived.length?`<details class="ab-unlinked-archive" data-unlinked-archive><summary>Archived · ${archived.length} unlinked records</summary><small>Task identity is unavailable. Original records are preserved.</small>${archived.map(sidebarRunCard).join('')}</details>`:''}
      ${deleted.length?`<details class="ab-deleted-history" data-deleted-history><summary>Deleted records · ${deleted.length}</summary><small>Restore a record to return it to task history.</small>${deleted.map(sidebarRunCard).join('')}</details>`:''}
      ${DEBUG_VIEW ? `<section class="ab-policy-controller" data-policy-controller aria-label="Policy controller"><header class="ab-control-header"><span class="ab-control-icon">${sidebarControlIcon('controller')}</span><div><h2>Policy controller</h2><h3>Choose how this run requests help</h3></div><span class="ab-control-mode" data-read-only="${Boolean(readOnly)}">${readOnly?sidebarControlIcon('lock'):''}${readOnly?'Read-only':'Live'}</span></header><div class="ab-policy-context" title="${esc(run?displayRun(run):'No run selected')}"><span class="ab-context-label">${selectedDev?(readOnly?'Viewing Dev history':'Controlling Dev session'):'Viewing run'}</span><div><strong>${esc(run?taskDisplayName(run):'No run selected')}</strong>${run?`<span>${esc(recordTime(run))}</span>`:''}</div></div>${DEBUG_VIEW ? `<small class="ab-policy-note">${esc(selectedDev?(c?.binding?c.reason||'':''):readOnly?'Read-only history or frozen binding.':'Changes apply after executor confirmation at a safe action boundary.')}</small>` : ''}
      ${DEBUG_VIEW && selectedDev?devPolicySummary(c,currentSnapshot?.dev_session,readOnly,history,pending)+devPolicyUnavailable(c,currentSnapshot?.dev_session):''}
      <div class="ab-policy-cards">${(c?.binding?.allowed_policies||(selectedDev?[]:['autonomous','reactive_help','trace_aware_hint_only','full_trace_aware_attention_planner','retry_k_then_ask'])).map(id=>`<button type="button" data-attention-policy="${esc(id)}" aria-pressed="${c?.active_policy===id}" data-state="${c?.active_policy===id?'Active':request?.policy_id===id?request.state:local?.policy_id===id?local.state:'Available'}" ${readOnly||pending||c?.active_policy===id?'disabled':''}><span class="ab-policy-icon">${sidebarControlIcon(id)}</span><strong>${esc(policyTitle(id))}${selectedDev?`<span class="ab-policy-description">${esc(devPolicyDescription(id))}</span>`:''}</strong><small class="ab-policy-state">${c?.active_policy===id?'Active':request?.policy_id===id?esc(request.state):local?.policy_id===id?esc(local.state):'Select'}</small></button>`).join('')}</div>
      ${DEBUG_VIEW&&selectedDev&&c?.binding&&!currentSnapshot?.dev_v2?'<p class="minor" data-dev-policy-limit>Full trace-aware planner is unavailable in Dev: Memory and approval workflows are not connected.</p>':''}
      ${request?`<p data-policy-request>${esc(request.state)} · ${esc(policyTitle(request.policy_id))}<br>${esc(selectedDev&&request.state==='Pending'?`${policyTitle(c.active_policy)} → ${policyTitle(request.policy_id)} · awaiting executor confirmation`:request.reason||'Awaiting safe boundary')}</p>`:''}${request?.state==='Pending'?'<button type="button" data-cancel-policy>Cancel pending switch</button>':''}
      ${readOnly&&run&&!selectedDev?`<button type="button" class="ab-linked-policy-run" data-linked-policy-run>${sidebarControlIcon('link')}<span>Create associated engineering run</span><span aria-hidden="true">↗</span></button>`:''}<p class="minor ab-policy-footnote">${selectedDev?(c?.binding?'Policy selection controls when this run requests help.':'This historical policy cannot be changed.'):'The model, action policy and DAG keep their own bindings.'}</p><p role="status" data-sidebar-message>${esc(sidebarMessage)}</p></section>` : operatorPolicyPanel({c,run,readOnly,pending,request,local,selectedDev})}
      <section class="ab-executing-runs" aria-label="Executing runs"><header class="ab-control-header"><span class="ab-control-icon">${sidebarControlIcon('activity')}</span><h3>Executing runs</h3><span class="ab-executing-count" data-active="${executing.length>0}">${executing.length}</span></header>${executing.map(r=>`<div class="ab-executing-card"><div class="ab-executing-identity"><span class="ab-executing-dot" aria-hidden="true"></span><strong title="${esc(displayRun(r))}">${esc(displayRun(r))}</strong></div><button type="button" class="danger" ${r.development?'data-stop-dev':'data-stop-executing'}="${esc(r.run_id)}" title="Stop target: ${esc(displayRun(r))} (${esc(r.run_id)})">${r.development?'Stop development':'Emergency stop'}</button></div>`).join('')||`<div class="ab-executing-empty">${sidebarControlIcon('idle')}<div><strong>No active runs</strong><small>No run is executing.</small></div></div>`}</section>`;
    sidebar.querySelectorAll('button').forEach(b=>b.addEventListener('pointerdown',e=>{if(document.activeElement?.id==='ab-hint-content')e.preventDefault();}));
    sidebar.querySelectorAll('[data-run-path],[data-run-name-editor],[data-record-more],[data-unlinked-archive],[data-deleted-history],.ab-record-jump').forEach(d=>{d.open=opened.includes(d.dataset.runPath||d.dataset.runNameEditor||d.dataset.recordMore||(d.hasAttribute('data-deleted-history')?'trash':d.hasAttribute('data-deleted-history')?'trash':d.hasAttribute('data-unlinked-archive')?'archive':'jump'));});
    sidebar.querySelector('nav').scrollTop=navScroll;
    for(const [id,draft] of renameDrafts){const input=[...sidebar.querySelectorAll('[data-run-rename]')].find(f=>f.dataset.runRename===id)?.querySelector('input');if(input){input.value=draft.value;input.setSelectionRange(...draft.range);if(draft.focused)input.focus({preventScroll:true});}}
    sidebar.querySelectorAll('[data-task-group]').forEach(d=>d.querySelector(':scope > summary').addEventListener('click',()=>taskGroupChoices.set(`${selectedDev||selectedRun}:${d.dataset.taskGroup}`,!d.open)));
    sidebar.querySelectorAll('[data-history-more]').forEach(b=>b.addEventListener('click',()=>{const key=b.dataset.historyMore;expandedHistory.has(key)?expandedHistory.delete(key):expandedHistory.add(key);renderSidebar();}));
    const search=sidebar.querySelector('[data-run-search]');search.addEventListener('input',()=>{sidebarQuery=search.value;renderSidebar();});
    sidebar.querySelectorAll('[data-op-filter]').forEach(b=>b.addEventListener('click',()=>{sidebarFilter=b.dataset.opFilter;preference('attention.sidebar-filter',sidebarFilter);renderSidebar();}));
    const runningCount=records.filter(recordIsActive).length,badge=sidebarToggle.querySelector('[data-op-count]');
    if(badge){badge.hidden=!runningCount;badge.textContent=String(runningCount);badge.title=`${runningCount} running`;}
    if(searchFocus){search.focus({preventScroll:true});search.setSelectionRange(...searchRange);}
    sidebar.querySelector('#ab-live-run')?.addEventListener('change',e=>navigateRecord(e.target.value));
    sidebar.querySelectorAll('[data-copy-record-id]').forEach(b=>b.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(b.dataset.copyRecordId);sidebarMessage='Full ID copied';}catch(_){sidebarMessage='Copy unavailable; select the full ID in the menu.';}renderSidebar();}));
    sidebar.querySelectorAll('[data-view-run]').forEach(b=>{b.addEventListener('pointerdown',e=>e.preventDefault());b.addEventListener('click',()=>navigateRun(b.dataset.viewRun));});
    sidebar.querySelectorAll('[data-pin-run]').forEach(b=>b.addEventListener('click',()=>{pins.has(b.dataset.pinRun)?pins.delete(b.dataset.pinRun):pins.add(b.dataset.pinRun);preference('attention.pinned-runs',JSON.stringify([...pins]));renderSidebar();}));
    sidebar.querySelectorAll('[data-run-rename]').forEach(f=>f.addEventListener('submit',async e=>{e.preventDefault();await sidebarWrite(`/api/runs/${encodeURIComponent(f.dataset.runRename)}/display-name`,{display_name:f.elements.namedItem('display_name').value});}));
    sidebar.querySelectorAll('[data-attention-policy]').forEach(b=>b.addEventListener('click',async()=>{
      if(currentSnapshot?.dev_v2){
        const wanted=b.dataset.attentionPolicy;
        if(!DEBUG_VIEW){switchRequests.set(run.run_id,{policy_id:wanted,state:'Requested',v2:true,at:Date.now()});renderSidebar();mountPolicyController();toast(`Change requested: ${operatorPolicyChoice(wanted)[0]} · applies from the next attempt`,'info');}
        await sidebarWrite(`/api/dev-v2/experiments/${currentSnapshot.dev_v2.experiment_id}/switch`,{cell:currentSnapshot.dev_v2.key,policy:wanted});return;}
      const old=switchRequests.get(run.run_id);const requestId=old?.state==='Unconfirmed'&&old.policy_id===b.dataset.attentionPolicy?old.request_id:(globalThis.crypto?.randomUUID?.()||`switch-${Date.now()}-${Math.random().toString(16).slice(2)}`);switchRequests.set(run.run_id,{request_id:requestId,policy_id:b.dataset.attentionPolicy,state:'Requested',expected_generation:old?.state==='Unconfirmed'?old.expected_generation:c.generation});renderSidebar();
      await sidebarWrite(`${attentionEndpoint}/attention-policy`,{request_id:requestId,policy_id:b.dataset.attentionPolicy,expected_generation:switchRequests.get(run.run_id).expected_generation},run.run_id);
    }));
    sidebar.querySelector('[data-dev-open-guidance]')?.addEventListener('click',()=>{
      const input=view.querySelector('[data-dev-guidance] textarea');
      if(!input)return;
      if(matchMedia('(max-width:760px)').matches){sidebarOpen=false;sidebarLayout();}
      input.scrollIntoView({block:'center'});input.focus({preventScroll:true});
    });
    sidebar.querySelector('[data-cancel-policy]')?.addEventListener('click',()=>sidebarWrite(`${attentionEndpoint}/attention-policy/cancel`,{request_id:request.request_id},run.run_id));
    sidebar.querySelector('[data-linked-policy-run]')?.addEventListener('click',()=>{openLinkedRun(run.run_id,'policy_change');});
    sidebar.querySelectorAll('[data-copy-run],[data-retry-run]').forEach(b=>b.addEventListener('click',()=>{openLinkedRun(b.dataset.copyRun||b.dataset.retryRun,b.dataset.copyRun?'copy':'retry');}));
    sidebar.querySelectorAll('[data-stop-dev]').forEach(b=>b.addEventListener('click',()=>{const session=devSessions.find(s=>s.session_id===b.dataset.stopDev);return sidebarWrite(session?.channel==='dev_v2'?`/api/dev-v2/experiments/${session.experiment_id}/cancel`:`/api/dev/sessions/${encodeURIComponent(b.dataset.stopDev)}/stop`,{});}));
    sidebar.querySelectorAll('[data-stop-executing]').forEach(b=>b.addEventListener('click',()=>sidebarWrite(`/api/runs/${encodeURIComponent(b.dataset.stopExecuting)}/interrupt`,{})));
    sidebar.querySelectorAll('[data-delete-record]').forEach(b=>b.addEventListener('click',()=>changeHistory(b.dataset.recordKind,b.dataset.deleteRecord,true)));
    sidebar.querySelectorAll('[data-restore-record]').forEach(b=>b.addEventListener('click',()=>changeHistory(b.dataset.recordKind,b.dataset.restoreRecord,false)));
    sidebar.querySelector('[data-history-undo]')?.addEventListener('click',()=>changeHistory(historyNotice.kind,historyNotice.id,false));
    sidebar.querySelectorAll('[data-dev-session]').forEach(b=>b.addEventListener('click',()=>navigateRecord(b.dataset.devSession)));
    // Keep active execution and its stop control above the scrollable history.
    sidebar.querySelector('nav').before(sidebar.querySelector('.ab-executing-runs'));
    policyControllerElement=sidebar.querySelector('[data-policy-controller]');
    if(!DEBUG_VIEW)mountPolicyController();
    mountPolicyController();
    updateReplayPolicy(currentSnapshot,view.querySelector('video[data-history-replay]'));
  }
  async function openLinkedRun(id,relation){
    try{
      const response=await fetch(`/api/runs/${encodeURIComponent(id)}`,{cache:'no-store'});
      if(!response.ok)throw new Error(`Source run HTTP ${response.status}`);
      linkedConfiguration=await response.json();linkedSource=id;linkedRelation=relation;
      view.querySelector('[data-open-launch]')?.click();
    }catch(e){sidebarMessage=e.message;renderSidebar();}
  }
  function navigateRecord(id){
    if(!devSessions.some(d=>d.session_id===id)){navigateRun(id);return;}
    rememberRunView();selectedDev=id;selectedRequest=null;lastRendered='';
    const url=new URL(location.href);url.searchParams.delete('run');url.searchParams.set('dev',id);history.replaceState(null,'',url);
    if(matchMedia('(max-width:760px)').matches){sidebarOpen=false;sidebarLayout();}
    refresh();
  }
  function navigateRun(id){selectedDev=null;selectRun(id);lastRendered='';if(matchMedia('(max-width:760px)').matches){sidebarOpen=false;sidebarLayout();}refresh();}
  async function changeHistory(kind,id,deleted){
    try{
      const response=await controlFetch(`/api/history/${deleted?'delete':'restore'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,record_id:id})});
      const result=await response.json();if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
      const items=kind==='dev'?devSessions:runs;const record=items.find(r=>(r.session_id||r.run_id)===id);if(record)record.history_deleted_at=result.history_deleted_at;
      historyNotice=deleted?{message:'Record deleted.',kind,id}:{message:'Record restored.'};
      if(deleted&&(selectedDev||selectedRun)===id){rememberRunView();selectedDev=null;selectRun(null);currentSnapshot=null;}
      lastRendered='';renderSidebar();await refresh();
    }catch(error){historyNotice={message:`Could not update history: ${DEBUG_VIEW ? error.message : plainError(error.message)}`};renderSidebar();}
  }
  async function sidebarWrite(url,payload,runId){
    try{const response=await controlFetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);if(runId)switchRequests.set(runId,result);sidebarMessage=result.state||'Saved';}
    catch(e){if(runId&&switchRequests.has(runId))switchRequests.set(runId,{...switchRequests.get(runId),state:'Unconfirmed'});if(switchRequests.get(currentSnapshot?.run?.run_id)?.v2)switchRequests.delete(currentSnapshot.run.run_id);sidebarMessage=`Request outcome unconfirmed: ${DEBUG_VIEW ? e.message : plainError(e.message)}. Refreshing persisted state.`;}
    lastRendered='';renderSidebar();await refresh();
  }
  async function controlFetch(url, options) {
    const send = () => fetch(url, {...options, headers: {...options.headers,
      'X-Attention-Token': root.dataset.controlToken || ''}});
    let response = await send();
    // Only a definite authentication rejection permits retry. Never retry an
    // ambiguous network failure or a business error (which may have committed).
    if (response.status !== 403 || (await response.clone().json()).error !== 'control token required') return response;
    const page = await fetch('/ui/', {cache: 'no-store', credentials: 'same-origin'});
    if (!page.ok || new URL(page.url).origin !== location.origin) return response;
    const token = new DOMParser().parseFromString(await page.text(), 'text/html')
      .querySelector('#attentionbench-clear[data-mode="live"]')?.dataset.controlToken;
    if (!token || token.length > 256 || token === root.dataset.controlToken) return response;
    root.dataset.controlToken = token;
    return send();
  }
  function connectionState(connected) {
    const wasDisconnected = root.dataset.connection === 'disconnected';
    root.dataset.connection = connected ? 'connected' : 'disconnected';
    if (!connected) {
      stopGuidancePreview();
      const status = view.querySelector('[data-guidance-preview]');
      if (status) status.textContent = 'Format check unavailable · Disconnected.';
    } else if (wasDisconnected) activeGuidancePreview?.schedule();
    if (connected) syncRequestAlert(currentSnapshot);
    else dismissRequestAlert();
    const badge = root.querySelector('.demo');
    badge.textContent = connected ? '● STORE' : '● STORE DISCONNECTED';
    badge.style.color = connected ? '' : 'var(--red, #c43b32)';
    let banner = root.querySelector('#ab-stale-warning');
    if (!connected && !banner) {
      banner = document.createElement('p'); banner.id = 'ab-stale-warning';
      banner.setAttribute('role', 'alert');
      (view.querySelector('[data-core-region="status"]') || view).prepend(banner);
    }
    if (connected) {
      banner?.remove();
      const message = view.querySelector('#ab-live-message');
      if (message?.dataset.readError) {
        if (message.textContent.startsWith('Could not read AttentionStore: ')) message.textContent = 'Connection restored.';
        delete message.dataset.readError;
      }
    }
    else if (banner) banner.textContent = 'Disconnected: showing an older snapshot. Status and camera frames are stale.';
    const action = view.querySelector('[data-action-needed]');
    if (action) {
      action.dataset.needed = connected ? action.dataset.connectedNeeded : 'unknown';
      action.textContent = connected ? action.dataset.connectedText : '⚠ Disconnected · action required is unknown';
    }
    const picture = view.querySelector('[data-picture-warning]');
    if (picture) {
      picture.hidden = connected;
      picture.textContent = connected ? '' : 'Stale frame · showing an older snapshot';
      if (connected) showPreviewState();
    }
    view.querySelector('[data-task-flow]')?.setAttribute('data-stale', String(!connected));
    for(const dialog of [flowDetailDialog,moreDialog]) {dialog.dataset.stale=String(!connected);const warning=dialog.querySelector('[data-detail-stale-warning]');if(warning)warning.hidden=connected;}
    attemptDagDialog.dataset.stale=String(!connected);
    const dagWarning=attemptDagDialog.querySelector('[data-dag-stale-warning]');
    if(dagWarning)dagWarning.hidden=connected;
    const camera = view.querySelector('.ab-station-camera');
    camera?.setAttribute('data-stale', String(!connected));
    const cameraLabel = camera?.querySelector('span');
    if (cameraLabel) {
      cameraLabel.dataset.liveLabel ||= cameraLabel.textContent;
      cameraLabel.textContent = connected ? cameraLabel.dataset.liveLabel : `Stale frame · ${cameraLabel.dataset.liveLabel}`;
    }
  }
  function traceTime(event, index) {
    if (event.timestamp_basis === 'epoch') return dateTime(event.timestamp);
    if (event.timestamp_basis === 'relative' && Number.isFinite(event.timestamp)) return `+${event.timestamp} s (relative to execution)`;
    return `Time not recorded · sequence ${event.sequence ?? index}`;
  }
  function replayTime(seconds) {
    return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
  }
  function replayTarget(event, recording, hasVideo) {
    if (!hasVideo) return {seconds:null, reason:'No replay video for this attempt'};
    if (event.timestamp_basis !== 'relative' || !Number.isFinite(event.timestamp) || event.timestamp < 0)
      return {seconds:null, reason:'No relative timestamp available for synchronization'};
    const timing = recording?.replay_timing;
    if (timing == null) return {seconds:event.timestamp, mode:'approximate'};
    const traces = timing.frame_trace_seconds, videos = timing.frame_video_seconds;
    if (timing.basis !== 'relative' || !Array.isArray(traces) || !Array.isArray(videos) ||
        !traces.length || traces.length !== videos.length ||
        traces.some((t, i) => !Number.isFinite(t) || (i && t <= traces[i - 1])) ||
        videos.some((t, i) => !Number.isFinite(t) || t < 0 || (i && t <= videos[i - 1])))
      return {seconds:null, reason:'Recording time mapping unavailable'};
    if (event.timestamp < traces[0] || event.timestamp > traces.at(-1))
      return {seconds:null, reason:'This event is outside the recorded sample range'};
    const index = traces.findLastIndex(t => t <= event.timestamp);
    return {seconds:videos[index], mode:'sample'};
  }
  function rememberReplay(video) {
    const key=video.dataset.replayKey;
    if(!key||video.dataset.replayRestoring==='true'||video.readyState<1||video.seeking||!Number.isFinite(video.currentTime))return;
    const selected=replaySelections.get(key);
    const selection=selected?.index!=null&&!selected.error&&!selected.pending&&!selected.seeking&&Math.abs(video.currentTime-selected.seconds)<.05
      ? {index:selected.index,seconds:selected.seconds,mode:selected.mode}:null;
    stored(`attention.replay:${key}`,JSON.stringify({seconds:video.currentTime,selection}));
  }
  function saveReplayPositions() {
    view.querySelectorAll('video[data-history-replay]').forEach(rememberReplay);
  }
  // Persist viewing state only. Restoring it never starts playback or a task.
  window.addEventListener('pagehide',saveReplayPositions);
  window.addEventListener('beforeunload',saveReplayPositions);
  function bindReplayTimeline(snapshot) {
    const panel = view.querySelector('[data-execution-viewer]');
    const video = panel?.querySelector('video[data-history-replay]');
    if (!video) return;
    const key = video.dataset.replayKey;
    const status = panel.querySelector('[data-replay-status]');
    const rows = [...panel.querySelectorAll('summary[data-replay-event]')];
    if(video.dataset.replayBound!==key) {
      video.dataset.replayBound=key;
      let saved;
      try {saved=JSON.parse(stored(`attention.replay:${key}`)||'null');} catch (_) {saved=null;}
      if(saved&&Number.isFinite(saved.seconds)&&saved.seconds>=0) {
        const choice=saved.selection;
        const row=choice&&Math.abs(saved.seconds-choice.seconds)<.05?rows.find(r=>r.dataset.replayEvent===choice.index&&r.hasAttribute('data-replay-seconds')&&
          Number(r.dataset.replaySeconds)===choice.seconds&&r.dataset.replayMode===choice.mode):undefined;
        replaySelections.set(key,{index:row?.dataset.replayEvent,seconds:saved.seconds,mode:row?choice.mode:'cursor',pending:true});
        video.dataset.replayRestoring='true';video.preload='auto';
      } else if(saved) stored(`attention.replay:${key}`,null);
    }
    const showSelection = () => {
      updateReplayPolicy(snapshot,video);
      const selected = replaySelections.get(key);
      rows.forEach(row => {
        const active = selected?.index === row.dataset.replayEvent && !selected.error;
        row.closest('[data-history-event]').dataset.selected = String(active);
        const link = row.querySelector('[data-replay-link]');
        if (active) link?.setAttribute('aria-current', 'true');
        else link?.removeAttribute('aria-current');
      });
      if(status)status.textContent=selected?.error||(selected?.pending?'Loading replay…':selected?.seeking?'Seeking to frame…':selected?.mode==='cursor'?`Replay position restored · ${replayTime(selected.seconds)}`:selected?`Located ${replayTime(selected.seconds)} · ${selected.mode==='sample'?'Synchronized sample frame':'Approximate position (legacy recording)'}`:'SDK call completion times · authorized trace');
    };
    const seek = () => {
      const selected = replaySelections.get(key);
      if (!selected?.pending || !panel.isConnected || !video.isConnected || video.readyState < 1) return;
      if (!Number.isFinite(video.duration) || selected.seconds > video.duration) {
        selected.pending = false; selected.error = selected.mode==='cursor'?'Saved replay position is outside the recording':'This action is outside the recorded replay';video.dataset.replayRestoring='false';
      } else {
        // Metadata alone may expose a zero-length seekable window. Load data
        // rather than accepting the browser's silent clamp back to frame zero.
        const available = Array.from({length:video.seekable.length}, (_, i) => i)
          .some(i => selected.seconds >= video.seekable.start(i) && selected.seconds <= video.seekable.end(i));
        if (!available) return;
        try {
          video.pause(); selected.seeking = true; video.currentTime = selected.seconds; selected.pending = false;
        } catch (_) { selected.pending = false; video.dataset.replayRestoring='false'; selected.error = 'Could not seek replay. Try again.'; }
      }
      showSelection();
    };
    // Video nodes survive snapshot refresh; replace handlers rather than stacking them.
    video.onloadedmetadata = seek;
    video.onloadeddata = seek;
    video.oncanplay = seek;
    video.onprogress = seek;
    video.onseeked = () => {
      const selected = replaySelections.get(key);
      if (!panel.isConnected || !video.isConnected || video.seeking) return;
      if(selected?.seeking) {
        selected.seeking=false;video.dataset.replayRestoring='false';
        const playback=replayPlayback.get(key);if(playback){video.playbackRate=playback.rate;if(!playback.paused)video.play().catch(()=>{});replayPlayback.delete(key);}
        if(Math.abs(video.currentTime-selected.seconds)>.05)selected.error='Could not reach the target frame. Try again.';
      } else if(selected&&!selected.pending&&Math.abs(video.currentTime-selected.seconds)>.05) {
        replaySelections.delete(key);
      }
      showSelection();rememberReplay(video);
    };
    video.ontimeupdate = () => {
      const selected=replaySelections.get(key);
      if(selected&&!selected.pending&&!selected.seeking&&!selected.error&&Math.abs(video.currentTime-selected.seconds)>.05) {
        replaySelections.delete(key);showSelection();
      }
      updateReplayPolicy(snapshot,video);rememberReplay(video);
    };
    video.onpause = () => rememberReplay(video);
    video.onerror = () => {
      const selected = replaySelections.get(key);
      if (selected?.pending || selected?.seeking) { selected.pending = false; selected.seeking = false; video.dataset.replayRestoring='false'; selected.error = 'Replay failed to load; seeking is unavailable'; showSelection(); }
    };
    rows.forEach(row => row.addEventListener('click', event => {
      if (event.target.closest('[data-replay-link]')) event.preventDefault();
      if (!row.hasAttribute('data-replay-seconds')) return;
      video.dataset.replayRestoring='true';
      replaySelections.set(key, {index:row.dataset.replayEvent,
        seconds:Number(row.dataset.replaySeconds), mode:row.dataset.replayMode, pending:true});
      video.preload = 'auto';
      video.pause();
      if (video.error) {
        const selected = replaySelections.get(key); selected.pending = false; video.dataset.replayRestoring='false'; selected.error = 'Replay failed to load; seeking is unavailable';
      } else seek();
      showSelection();
      row.scrollIntoView({block:'nearest', inline:'nearest'});
    }));
    seek(); showSelection();
  }
  // Presentation-only transport. The same video node and native controls remain
  // available, so recording, synchronization and viewing-state logic stay intact.
  // ---- operator enhancements: tone, title, loading stages, counters, alerts, shortcuts ----
  const animatedRuns=new Set(), lastOperatorState=new Map(), framesSeen=new Set();
  let loadingTicker=null, keysBound=false, audioContext=null;
  const alertsOn=()=>preference('attention.alerts')==='on';
  function paintAlertsButton(){alertsButton.setAttribute('aria-pressed',String(alertsOn()));alertsButton.textContent=alertsOn()?'🔔 Alerts on':'🔕 Alerts off';}
  alertsButton.addEventListener('click',async()=>{
    if(alertsOn()){preference('attention.alerts','off');paintAlertsButton();toast('Alerts turned off');return;}
    try{if('Notification' in window&&Notification.permission==='default')await Notification.requestPermission();}catch(_){}
    preference('attention.alerts','on');paintAlertsButton();beep();toast('Alerts on · you will hear a sound when the robot needs help');
  });
  paintAlertsButton();
  function beep(){
    try{audioContext=audioContext||new (window.AudioContext||window.webkitAudioContext)();const o=audioContext.createOscillator(),g=audioContext.createGain();
      o.type='sine';o.frequency.value=660;g.gain.setValueAtTime(.0001,audioContext.currentTime);g.gain.exponentialRampToValueAtTime(.18,audioContext.currentTime+.02);g.gain.exponentialRampToValueAtTime(.0001,audioContext.currentTime+.45);
      o.connect(g);g.connect(audioContext.destination);o.start();o.stop(audioContext.currentTime+.5);}catch(_){}
  }
  function stageReady(stage) {
    const media=stage.querySelector('video,img#ab-camera-frame');
    if(!media)return false;
    if(media.tagName==='VIDEO')return media.readyState>=2||media.error!=null;
    return !media.hidden&&media.complete&&media.naturalWidth>0;
  }
  function stageStatus(stage,age) {
    const text=stage.querySelector('[data-op-loading-text]');if(!text)return;
    if(stage.dataset.opStage==='replay'){text.textContent=age>20?'Still loading the replay…':'Loading replay…';return;}
    const fresh=view.querySelector('[data-preview-freshness]')?.textContent||'';
    const launching=stage.dataset.opStage==='launch';
    const phase=stage.dataset.opPhase;
    if(phase==='model'||phase==='advisor'){text.textContent=phase==='model'?"The AI is writing the robot's plan…":'The advisor is preparing guidance…';stage.dataset.slow='false';return;}
    // Environment and planner start-up can take a few minutes the first time; only warn after that.
    text.textContent=age>300?'Still starting — this is taking longer than usual. You can keep waiting or stop the run.':age>15||/Receiving|Waiting/i.test(fresh)?'Waiting for the first picture… the simulator can take a few minutes to start':age>5?'Connecting to camera…':age>2||!launching?'Starting simulator…':'Preparing your run…';
    stage.dataset.slow=String(age>300);
  }
  function initStages() {
    view.querySelectorAll('[data-op-stage]').forEach(stage=>{
      stage.dataset.since=String(Date.now());
      if(stage.querySelector('video')?.error){stage.dataset.state='loading';}
      else if(stage.dataset.opKey && framesSeen.has(stage.dataset.opKey))stage.dataset.state='ready';
      else if(stageReady(stage)){if(stage.dataset.opKey)framesSeen.add(stage.dataset.opKey);stage.dataset.state='ready';}
      else{stage.dataset.state='loading';stageStatus(stage,0);}
    });
    runLoadingTicker();
  }
  function runLoadingTicker() {
    if(loadingTicker)return;
    loadingTicker=setInterval(()=>{
      const stages=[...view.querySelectorAll('[data-op-stage][data-state="loading"]')];
      view.querySelectorAll('[data-op-stage]').forEach(stage=>{
        const media=stage.querySelector('video');
        if(media){const o=stage.querySelector('[data-op-loading]');if(o&&media.offsetHeight)o.style.height=`${media.offsetHeight}px`;}
      });
      for(const stage of stages){
        const age=(Date.now()-Number(stage.dataset.since||Date.now()))/1000;
        if(stage.dataset.opEnded==='true'){
          const img=stage.querySelector('img#ab-camera-frame');
          if(!stageReady(stage)&&(age>4||(img&&img.complete&&img.naturalWidth===0&&!img.hidden&&img.getAttribute('src')))){
            stage.dataset.state='error';const w=stage.querySelector('.op-loading-word'),t=stage.querySelector('[data-op-loading-text]');
            if(w)w.textContent='NO PICTURE';if(t)t.textContent='No picture was saved for this run. Pick an attempt above to see what the robot did.';continue;
          }
        }
        const vid=stage.querySelector('video');
        if(vid?.error){stage.dataset.state='error';const w=stage.querySelector('.op-loading-word'),t=stage.querySelector('[data-op-loading-text]');if(w)w.textContent='NO VIDEO';if(t)t.textContent='This recording is not available. The steps on the right still show what happened.';continue;}
        if(stageReady(stage)){if(stage.dataset.opKey)framesSeen.add(stage.dataset.opKey);stage.dataset.state='leaving';setTimeout(()=>{if(stage.dataset.state==='leaving')stage.dataset.state='ready';},520);}
        else stageStatus(stage,age);
      }
      // Actions are published when an attempt ends; until then the live preview's phase says what the robot is doing.
      view.querySelectorAll('[data-op-now][data-events="0"]').forEach(el=>{const m=/phase:\s*([^;·]+)/i.exec(view.querySelector('[data-preview-freshness]')?.textContent||'');if(m)el.textContent=m[1].trim();});
      view.querySelectorAll('[data-op-since]').forEach(el=>{el.textContent=`running for ${operatorTime(Date.now()/1000-Number(el.dataset.opSince))}`;});
      if(!stages.length&&!view.querySelector('[data-op-since]')){clearInterval(loadingTicker);loadingTicker=null;}
    },250);
  }
  function bindOperatorEnhancements(snapshot,runChanged) {
    if(DEBUG_VIEW)return;
    const state=operatorState(snapshot),name=taskDisplayName(snapshot.run);
    view.dataset.tone=state;
    document.title=operatorPageTitle(snapshot,name);
    const before=lastOperatorState.get(snapshot.run.run_id);lastOperatorState.set(snapshot.run.run_id,state);
    if(state==='waiting'&&before&&before!=='waiting'&&alertsOn()){
      beep();toast('The robot needs your help','warn');
      try{if('Notification' in window&&Notification.permission==='granted'&&document.hidden)new Notification('Robot needs your help',{body:name});}catch(_){}
    }
    initStages();
    view.querySelectorAll('[data-op-follow]').forEach(list=>{list.scrollTop=list.scrollHeight;});
    view.querySelectorAll('.ab-history-events:not([data-op-follow])').forEach(list=>{const bad=list.querySelector('.op-step.failed');if(bad)list.scrollTop=Math.max(0,bad.offsetTop-list.offsetTop-90);});
    if(!animatedRuns.has(snapshot.run.run_id)){
      animatedRuns.add(snapshot.run.run_id);
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)view.querySelectorAll('.op-meter').forEach((meter,index)=>{
        meter.style.setProperty('--fill',`${meter.dataset.fill||0}%`);meter.classList.add('op-animate');meter.style.animationDelay=`${index*70}ms`;
        const el=meter.querySelector('[data-count-to]'),to=Number(el?.dataset.countTo);
        if(el&&el.dataset.countTo!==''&&Number.isFinite(to)&&to>0){
          const seconds=el.dataset.countSeconds==='1',start=performance.now(),duration=700,final=el.textContent;
          const tick=now=>{const k=Math.min(1,(now-start)/duration),v=to*(1-Math.pow(1-k,3));el.textContent=k>=1?final:seconds?operatorTime(v):number(Math.round(v));if(k<1&&el.isConnected)requestAnimationFrame(tick);};
          requestAnimationFrame(tick);
        }
      });
    } else view.querySelectorAll('.op-meter').forEach(meter=>meter.style.setProperty('--fill',`${meter.dataset.fill||0}%`));
    runLoadingTicker();
    if(!keysBound){
      keysBound=true;
      document.addEventListener('click',event=>{if(!matchMedia('(max-width:900px)').matches||!sidebarOpen)return;if(sidebar.contains(event.target)||sidebarToggle.contains(event.target))return;sidebarOpen=false;preference('attention.sidebar-open','false');sidebarLayout();});
      document.addEventListener('keydown',event=>{
        if(event.ctrlKey||event.metaKey||event.altKey)return;
        const t=event.target;if(t&&(/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)||t.isContentEditable))return;
        if(event.key==='t'||event.key==='T'){sidebarOpen=!sidebarOpen;preference('attention.sidebar-open',String(sidebarOpen));sidebarLayout();return;}
        const tabs=[...view.querySelectorAll('.op-tabs [data-history-select]')];
        if((event.key==='ArrowLeft'||event.key==='ArrowRight')&&tabs.length){
          const at=Math.max(0,tabs.findIndex(b=>b.getAttribute('aria-pressed')==='true')),next=tabs[Math.min(tabs.length-1,Math.max(0,at+(event.key==='ArrowRight'?1:-1)))];
          if(next&&next!==tabs[at]){event.preventDefault();next.click();}
        }else if(event.key===' '){
          const video=view.querySelector('video[data-history-replay]');if(video&&t===document.body){event.preventDefault();video.paused?video.play():video.pause();}
        }
      });
    }
  }
  function bindOperatorPresentation() {
    view.querySelectorAll('[data-operator-expand]').forEach(button=>button.addEventListener('click',()=>{
      const copy=button.parentElement.querySelector('[data-operator-copy]');
      const expanded=copy.classList.toggle('expanded');button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?'Show less':button.dataset.operatorExpand || 'Show more';
    }));
    view.querySelectorAll('video[data-operator-failure-seconds],video[data-operator-failure-end]').forEach(video=>{
      const update=()=>{
        const scrubber=video.nextElementSibling?.querySelector('.ab-replay-scrubber');
        if (!scrubber || !(video.duration>0)) return;
        let marker=scrubber.querySelector('[data-operator-failure-marker]');
        if (!marker) {marker=document.createElement('span');marker.dataset.operatorFailureMarker='';marker.className='op-failure-marker';marker.textContent='failed';scrubber.append(marker);}
        marker.style.left=video.dataset.operatorFailureSeconds==null?'calc(100% - 4px)':`${pct(Number(video.dataset.operatorFailureSeconds),video.duration)}%`;
      };
      video.addEventListener('loadedmetadata',update,{once:true});update();
    });
  }

  function bindIndustrialReplayControls() {
    if (!root.classList.contains('ab-industrial')) return;
    const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{
      play:'<path d="m8 5 11 7-11 7Z" fill="currentColor" stroke="none"/>',
      pause:'<path d="M8 5v14M16 5v14" stroke-width="4"/>',
      volume:'<path d="m11 5-6 4H2v6h3l6 4ZM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',
      muted:'<path d="m11 5-6 4H2v6h3l6 4ZM17 9l5 6M22 9l-5 6"/>',
      fullscreen:'<path d="M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5"/>',
      more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    }[name]}</svg>`;
    view.querySelectorAll('video[data-history-replay]').forEach(video => {
      video.industrialTransportAbort?.abort();
      const controller = new AbortController();
      video.industrialTransportAbort = controller;
      const options = {signal:controller.signal};
      const bar = document.createElement('div');
      bar.className = 'ab-replay-transport';
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', 'Replay controls');
      bar.innerHTML = `<button type="button" data-replay-play aria-label="Play replay">${icon('play')}</button><time data-replay-clock>00:00</time><div class="ab-replay-scrubber"><input type="range" data-replay-seek min="0" max="0" step="0.01" value="0" aria-label="Replay position" disabled><output class="ab-replay-bubble" aria-hidden="true">00:00</output></div><time data-replay-duration>00:00</time><button type="button" data-replay-rate aria-label="Playback speed: 1×">1×</button><button type="button" data-replay-mute aria-label="Mute replay">${icon('volume')}</button><button type="button" data-replay-fullscreen aria-label="Fullscreen replay">${icon('fullscreen')}</button><button type="button" data-replay-native aria-label="More playback controls" aria-expanded="false">${icon('more')}</button>`;
      video.after(bar);
      // Full browser playback controls (volume, download, picture-in-picture)
      // are still accessible through More; avoid two permanent transport bars.
      video.controls = video.industrialNativeControls === true;
      const play = bar.querySelector('[data-replay-play]');
      const range = bar.querySelector('[data-replay-seek]');
      const scrubber = bar.querySelector('.ab-replay-scrubber');
      const rate = bar.querySelector('[data-replay-rate]');
      const mute = bar.querySelector('[data-replay-mute]');
      const native = bar.querySelector('[data-replay-native]');
      const clock = value => {
        const seconds = Math.max(0, Math.floor(Number(value) || 0));
        return `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
      };
      const update = () => {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        const position = Math.min(duration, video.currentTime || 0);
        range.max = String(duration);
        range.disabled = duration <= 0;
        range.value = String(position);
        range.setAttribute('aria-valuetext', `${clock(position)} of ${clock(duration)}`);
        scrubber.style.setProperty('--seek', `${duration ? position / duration * 100 : 0}%`);
        bar.querySelector('[data-replay-clock]').textContent = clock(position);
        bar.querySelector('[data-replay-duration]').textContent = clock(duration);
        bar.querySelector('.ab-replay-bubble').textContent = clock(position);
        const playing = !video.paused;
        if (play.dataset.playing !== String(playing)) {
          play.dataset.playing = String(playing);
          play.innerHTML = icon(playing ? 'pause' : 'play');
        }
        play.setAttribute('aria-label', video.paused ? 'Play replay' : 'Pause replay');
        rate.textContent = `${video.playbackRate}×`;
        rate.setAttribute('aria-label', `Playback speed: ${video.playbackRate}×`);
        if (mute.dataset.muted !== String(video.muted)) {
          mute.dataset.muted = String(video.muted);
          mute.innerHTML = icon(video.muted ? 'muted' : 'volume');
        }
        mute.setAttribute('aria-label', video.muted ? 'Unmute replay' : 'Mute replay');
        mute.setAttribute('aria-pressed', String(video.muted));
        native.setAttribute('aria-expanded', String(video.controls));
      };
      play.addEventListener('click', () => {
        if (!video.paused) video.pause();
        else video.play().catch(() => { play.setAttribute('aria-label','Play replay'); });
      }, options);
      range.addEventListener('input', () => {
        if (Number.isFinite(video.duration) && video.duration > 0) {
          video.currentTime = Math.max(0, Math.min(Number(range.value), video.duration));
          update();
        }
      }, options);
      rate.addEventListener('click', () => {
        const rates = [.5,1,1.5,2];
        video.playbackRate = rates[(rates.indexOf(video.playbackRate) + 1) % rates.length];
      }, options);
      mute.addEventListener('click', () => { video.muted = !video.muted; }, options);
      native.addEventListener('click', () => {
        video.controls = !video.controls;
        video.industrialNativeControls = video.controls;
        update();
      }, options);
      video.addEventListener('fullscreenchange', () => {
        video.controls = document.fullscreenElement === video || video.industrialNativeControls === true;
        update();
      }, options);
      bar.querySelector('[data-replay-fullscreen]').addEventListener('click', () => {
        if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
        else {
          video.controls = true;
          video.requestFullscreen?.().catch(() => { video.controls = video.industrialNativeControls === true; update(); });
        }
      }, options);
      for (const event of ['loadedmetadata','durationchange','timeupdate','play','pause','ended','ratechange','volumechange','seeked']) {
        video.addEventListener(event, update, options);
      }
      update();
    });
  }
  let graphOverride = initialSelection.get('graph');

  function esc(value) {
    return String(value ?? '—').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function label(value) {
    const names = {autonomous:'Autonomous',demo_first:'Demonstration first',reactive_help:'Reactive help',
      retry_k_then_ask:'Retry k times, then ask',budget_matched_random_escalation:'Budget-matched random escalation',
      trace_aware_hint_only:'Trace-aware hints only',full_trace_aware_attention_planner:'Full trace-aware attention planner',
      live_human_first:'Human-first assistance',
      benchmark_proxy:'Automatic Advisor assistance',robosuite_sim:'Robosuite simulator',
      robocasa365_sim:'RoboCasa365 simulator',robocasa_sim:'RoboCasa simulator',simulation:'Simulator',real_robot:'Real robot',
      assistance:'Assistance credits',execution_seconds:'Execution time',tokens:'Tokens',gpu_seconds:'GPU time'};
    return names[value] || String(value ?? '—').replaceAll('_', ' ');
  }

  // Exact display-only mappings. Never rewrite the captured graph, its SHA,
  // immutable binding, history, or unfamiliar operator/model descriptions.
  const displayText = {
    '执行已批准的完整 Public SDK 策略。内部函数和 SDK 动作没有独立 Skill DAG 调度定义或子技能事件；本图仅记录整体任务节点。':
      'Execute the full approved Public SDK policy. Internal functions and SDK actions have no separate Skill DAG dispatch definitions or child skill events; this graph records only the overall task node.',
    '立方体抬升':'Lift the cube','台面物体移至水槽':'Move the object from the counter to the sink','移至水槽':'Move to the sink','移至柜内':'Move into the cabinet','将物体从台面移至水槽':'Move the object from the counter to the sink',
    '定位':'Locate','接近':'Approach','抓取':'Grasp','抬起':'Lift','检查':'Check','张开':'Open','夹紧':'Close',
    '公共定位':'Locate using public observations','夹爪张开':'Open the gripper','实际抓取任务':'Execute the grasp task','对准物体后接近抓取位置':'Align with the object, then approach the grasp position','抓稳后抬起物体':'Secure the grasp, then lift the object','核对原生任务结果':'Check the native task outcome',
    '本次未记录 Skill DAG':'No Skill DAG recorded for this attempt',
    'Skill DAG 绑定或校验失败':'Skill DAG binding or verification failed'
  };
  function displayDescription(value, fallback = '') {
    return Object.hasOwn(displayText,value) ? displayText[value] : value || fallback;
  }

  function dateTime(value) {
    if (value == null) return '—';
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('en-US', { hour12: false });
  }

  const sessionDialog = document.createElement('dialog');
  sessionDialog.className = 'ab-session-dialog';
  sessionDialog.setAttribute('aria-label', 'Agent session conversation');
  root.appendChild(sessionDialog);
  sessionDialog.addEventListener('click', event => {
    if (event.target === sessionDialog || event.target.closest('[data-close-session]')) sessionDialog.close();
  });
  const launchDialog = document.createElement('dialog');
  launchDialog.className = 'ab-session-dialog ab-launch-dialog';
  launchDialog.setAttribute('aria-label', 'Configure Attention run');
  root.appendChild(launchDialog);
  launchDialog.addEventListener('close',()=>{linkedSource=null;linkedRelation='policy_change';linkedConfiguration=null;});
  launchDialog.addEventListener('click', event => {
    if (event.target === launchDialog || event.target.closest('[data-close-launch]')) launchDialog.close();
  });

  const attemptDagDialog = document.createElement('dialog');
  attemptDagDialog.className = 'ab-session-dialog ab-attempt-dag-dialog';
  attemptDagDialog.setAttribute('data-attempt-dag-dialog', '');
  attemptDagDialog.setAttribute('aria-labelledby', 'ab-attempt-dag-title');
  root.appendChild(attemptDagDialog);
  attemptDagDialog.addEventListener('click', event => {
    if (event.target.closest('[data-close-attempt-dag]')) closeAttemptDag();
    else if (event.target === attemptDagDialog) {
      const rect=attemptDagDialog.getBoundingClientRect();
      if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeAttemptDag();
    }
  });
  function closeAttemptDag() {
    const closed=activeAttemptDag;activeAttemptDag=null;
    attemptDagDialog.close();attemptDagDialog.replaceChildren();
    if(closed?.runId===selectedRun) {
      [...view.querySelectorAll('[data-flow-kind=attempt]')].find(e=>e.dataset.entityId===closed.attemptId)?.focus({preventScroll:true});
    }
  }
  attemptDagDialog.addEventListener('cancel',event=>{event.preventDefault();closeAttemptDag();});
  attemptDagDialog.addEventListener('close',()=>{if(!attemptDagDialog.open)closeAttemptDag();});


  let activeFlowDetail=null, activeMoreRun=null;
  function createDetailDialog(name,attribute,closeAttribute,close) {
    const dialog=document.createElement('dialog');
    dialog.className=`ab-session-dialog ab-detail-dialog ${name}`;
    dialog.setAttribute(attribute,'');dialog.setAttribute('aria-labelledby',`${attribute}-title`);
    root.appendChild(dialog);
    dialog.addEventListener('click',event=>{
      const rect=dialog.getBoundingClientRect();
      if(event.target.closest(`[${closeAttribute}]`)||event.target===dialog&&
        (event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom))close();
    });
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    return dialog;
  }
  const flowDetailDialog=createDetailDialog('ab-flow-detail-dialog','data-flow-detail-dialog','data-close-flow-detail',closeFlowDetail);
  const moreDialog=createDetailDialog('ab-more-dialog','data-more-dialog','data-close-more',closeMore);
  function closeFlowDetail() {
    const closed=activeFlowDetail;activeFlowDetail=null;
    flowDetailDialog.close();flowDetailDialog.replaceChildren();
    if(closed?.runId===currentSnapshot?.run.run_id) {
      [...view.querySelectorAll(`[${closed.returnAttribute}]`)].find(e=>e.getAttribute(closed.returnAttribute)===closed.key)?.focus({preventScroll:true});
    }
  }
  function closeMore() {
    activeMoreRun=null;moreDialog.close();moreDialog.replaceChildren();
    view.querySelector('[data-open-more]')?.focus({preventScroll:true});
  }
  function detailIdentity(details) {
    return details.dataset.persistDetail||`${details.closest('[data-secondary]')?.dataset.secondary||''}:${details.closest('[data-feedback-layer]')?.dataset.feedbackLayer||''}:${details.querySelector(':scope > summary')?.textContent}`;
  }
  function replaceDialog(dialog,html) {
    const expanded=new Set([...dialog.querySelectorAll('details[open]')].map(detailIdentity));
    const active=document.activeElement,inside=dialog.contains(active);
    const detail=inside&&active?.tagName==='SUMMARY'?detailIdentity(active.parentElement):null;
    const identity=inside?[...active.attributes].find(a=>a.name==='id'||a.name==='name'||a.name.startsWith('data-')):null;
    const input=inside&&active?.tagName==='INPUT'?{value:active.value,start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection}:null;
    const scroll=dialog.scrollTop;
    dialog.innerHTML=html;
    for(const item of dialog.querySelectorAll('details'))item.open=expanded.has(detailIdentity(item));
    dialog.scrollTop=scroll;
    if(inside) {
      const target=detail?[...dialog.querySelectorAll('details')].find(e=>detailIdentity(e)===detail)?.querySelector(':scope > summary'):
        identity?[...dialog.querySelectorAll(`[${identity.name}]`)].find(e=>e.getAttribute(identity.name)===identity.value):null;
      (target||dialog.querySelector('[data-close-flow-detail],[data-close-more]'))?.focus({preventScroll:true});
      if(target&&input) {target.value=input.value;if(input.start!=null)target.setSelectionRange(input.start,input.end,input.direction);}
    }
  }
  function detailHeader(title,attribute,closeAttribute) {
    return `<header class="ab-dag-dialog-header"><div><span class="label">${attribute==='data-more-dialog'?'Developer tools':'Task flow'}</span><h3 id="${attribute}-title">${esc(title)}</h3></div><button type="button" ${closeAttribute} aria-label="Close ${esc(title)}">Close</button></header><p class="ab-detail-stale" data-detail-stale-warning role="alert" ${root.dataset.connection!=='disconnected'?'hidden':''}>Disconnected · showing older records</p>`;
  }
  function openFlowDetail(snapshot,key) {
    const node=flowModel(snapshot).nodes.find(n=>n.key===key);if(!node)return;
    if(node.attemptId) {openAttemptDag(snapshot,node.attemptId);return;}
    const returnAttribute=document.activeElement?.hasAttribute('data-open-flow-node')?'data-open-flow-node':'data-flow-id';
    activeFlowDetail={runId:snapshot.run.run_id,key,returnAttribute};
    refreshFlowDetail(snapshot,true);flowDetailDialog.showModal();
    flowDetailDialog.querySelector('[data-close-flow-detail]')?.focus({preventScroll:true});
  }
  function refreshFlowDetail(snapshot,opening=false) {
    if(!activeFlowDetail||(!flowDetailDialog.open&&!opening))return;
    const node=flowModel(snapshot).nodes.find(n=>n.key===activeFlowDetail.key);
    if(snapshot.run.run_id!==activeFlowDetail.runId||!node) {closeFlowDetail();return;}
    const title={response:node.title+' · Feedback',request:node.title+' · Details',end:'Run outcome',result:'Result records',planned:node.title+' · Not started'}[node.kind]||node.title;
    replaceDialog(flowDetailDialog,detailHeader(title,'data-flow-detail-dialog','data-close-flow-detail')+`<div class="ab-detail-body">${nodeDetail(snapshot,node)}</div>`);
    bindNodeActions(flowDetailDialog,snapshot);
  }
  function moreButton() {return '<button type="button" class="ab-button-quiet" data-open-more aria-haspopup="dialog">More ···</button>';}
  function openMore() {
    activeMoreRun=currentSnapshot?.run.run_id||'empty';
    refreshMoreDialog(true);moreDialog.showModal();
    moreDialog.querySelector('[data-close-more]')?.focus({preventScroll:true});
  }
  function refreshMoreDialog(opening=false) {
    if(!activeMoreRun||(!moreDialog.open&&!opening))return;
    if(activeMoreRun!==(currentSnapshot?.run.run_id||'empty')) {closeMore();return;}
    const content=secondaryPanel(currentSnapshot);
    replaceDialog(moreDialog,detailHeader('More','data-more-dialog','data-close-more')+`<div class="ab-detail-body">${content}</div>`);
    bindOrchestratorControls(moreDialog);
  }
  function bindNodeActions(scope,snapshot) {
    scope.querySelectorAll('[data-node-replay]').forEach(button=>button.addEventListener('click',()=>{
      if(!snapshot.attempts.some(a=>a.attempt_id===button.dataset.nodeReplay))return;
      stored(`attention.history:${snapshot.run.run_id}`,button.dataset.nodeReplay);
      if(flowDetailDialog.open)closeFlowDetail();if(attemptDagDialog.open)closeAttemptDag();
      render(currentSnapshot);view.querySelector('[data-execution-viewer]')?.scrollIntoView({block:'start'});
    }));
    scope.querySelector('[data-open-flow-request]')?.addEventListener('click',event=>{
      selectedRequest=event.currentTarget.dataset.openFlowRequest;closeFlowDetail();render(currentSnapshot);
      const history=view.querySelector('[data-request-history]');
      if(!actionableRequests(snapshot).some(r=>r.request_id===selectedRequest)&&history)history.open=true;
      view.querySelector('[data-core-region=request]')?.scrollIntoView({block:'start'});
      view.querySelector('#ab-hint-content')?.focus({preventScroll:true});
    });
  }
  function attemptSummary(snapshot,attempt) {
    const native=snapshot.eval?.native_outcomes?.[attempt.attempt_id];
    const outcome=native?.evaluated===true?(native.native_success===true?'Task successful':native.native_success===false?'Task unsuccessful':'Outcome unconfirmed'):'Task outcome unconfirmed';
    return `<section class="ab-attempt-summary" data-attempt-summary><div class="ab-node-summary"><b>${esc(outcome)}</b><button type="button" data-node-replay="${esc(attempt.attempt_id)}">View this replay</button></div><details data-node-technical data-persist-detail="attempt-technical:${esc(attempt.attempt_id)}"><summary>Attempt details</summary><dl><dt>Attempt</dt><dd>${esc(attempt.attempt_id)}</dd><dt>Started</dt><dd>${esc(dateTime(attempt.started_at))}</dd><dt>Ended</dt><dd>${esc(dateTime(attempt.ended_at))}</dd><dt>Status source</dt><dd>${esc(attempt.status_source||'Persisted attempt')}</dd></dl></details></section>`;
  }

  const runNotice = document.createElement('aside');
  runNotice.id = 'ab-run-notice';
  runNotice.hidden = true;
  runNotice.setAttribute('role', 'alert');
  root.appendChild(runNotice);
  function showRunNotice(text) {
    runNotice.replaceChildren();
    const message = document.createElement('strong'); message.textContent = text;
    const close = document.createElement('button'); close.type = 'button';
    close.textContent = 'Close'; close.setAttribute('aria-label', 'Close run notification');
    close.addEventListener('click', () => { runNotice.hidden = true; });
    runNotice.append(message, close); runNotice.hidden = false;positionRunNotice();
  }

  function positionRunNotice(){
    if(runNotice.hidden)return;
    runNotice.style.top='18px';
    const card=view.querySelector('[data-run-overview]');
    if(!card)return;
    const a=card.getBoundingClientRect(),b=runNotice.getBoundingClientRect();
    if(a.bottom>b.top&&b.bottom>a.top&&a.right>b.left&&b.right>a.left){
      runNotice.style.top=`${a.bottom+b.height+26<innerHeight?a.bottom+8:Math.max(18,a.top-b.height-8)}px`;
    }
  }

  function launchButton(snapshot=null) {
    if(snapshot?.dev_v2?.lifecycle?.can_create_new===false)return '<button class="ab-button-primary" type="button" data-open-launch disabled title="Original goal resources or worker availability prevent a new run">New run</button>';
    return `<button class="ab-button-primary" type="button" data-open-launch ${(launchOptions?.dev_v2?.profiles?.some(p=>p.enabled) || launchOptions?.dev_loop?.profiles?.length || launchOptions?.profiles?.some(p => p.certified_bimanual_v2)) ? '' : 'disabled'}>New run</button>`;
  }
  function attemptLabel(status) {
    return {running:'Attempt running', succeeded:'Attempt succeeded', failed:'Attempt unsuccessful', timed_out:'Attempt timed out', cancelled:'Attempt cancelled'}[status] || 'Not executed yet';
  }
  function isTerminal(snapshot) {
    return ['completed', 'failed', 'cancelled'].includes(snapshot.run.status);
  }
  function executionFailure(trace) {
    const failure = trace?.failure;
    // Authorized traces carry an explicit no-failure summary with a message.
    return failure?.stage === 'none' && failure?.error_type === 'none' ? null : failure;
  }
  function replyExpired(request) {
    return request?.deadline_at != null && request.deadline_at * 1000 <= Date.now();
  }
  function actionableRequests(snapshot) {
    return isTerminal(snapshot) || snapshot.station.interrupt ? [] : snapshot.requests.filter(request =>
      request.state === 'pending' && request.mode === 'live_human_first' && !replyExpired(request));
  }

  // Keep the alert outside the replaced view. It never focuses, scrolls or
  // handles a click, and its receipt belongs only to this browser tab.
  const requestAlert = document.createElement('aside');
  requestAlert.id = 'ab-request-alert'; requestAlert.hidden = true;
  requestAlert.setAttribute('popover', 'manual');
  requestAlert.setAttribute('role', 'status');
  requestAlert.setAttribute('aria-live', 'polite');
  requestAlert.setAttribute('aria-atomic', 'true');
  requestAlert.innerHTML = '<div class="ab-alert-pulse" aria-hidden="true"></div><div class="ab-alert-scan" aria-hidden="true"></div><div class="ab-alert-message"><strong>HUMAN INPUT REQUIRED</strong><p></p></div>';
  root.appendChild(requestAlert);
  const seenRequestAlerts = new Set();
  const reducedAlertMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let requestAlertKey = null, requestAlertTimer = null;
  function dismissRequestAlert() {
    clearTimeout(requestAlertTimer); requestAlertTimer = null;
    if (typeof requestAlert.hidePopover === 'function' && requestAlert.matches(':popover-open')) requestAlert.hidePopover();
    requestAlert.hidden = true; requestAlertKey = null;
  }
  function syncRequestAlert(snapshot) {
    if (!snapshot || snapshot.run.run_id !== selectedRun || root.dataset.connection === 'disconnected') {
      dismissRequestAlert(); return;
    }
    const pending = actionableRequests(snapshot);
    const receipt = request => `attention.request-alert:${JSON.stringify([snapshot.run.run_id,request.request_id])}`;
    if (requestAlertKey && pending.some(request => receipt(request) === requestAlertKey)) return;
    dismissRequestAlert();
    const request = [...pending].sort((a,b) => Number(b.created_at)-Number(a.created_at))
      .find(request => !seenRequestAlerts.has(receipt(request)) && stored(receipt(request)) !== 'seen');
    if (!request) return;
    requestAlertKey = receipt(request);
    seenRequestAlerts.add(requestAlertKey); stored(requestAlertKey, 'seen');
    requestAlert.querySelector('p').textContent = request.request_type === 'hint'
      ? 'Review the request and send your hint.' : 'Review the request and choose a reply.';
    requestAlert.dataset.motion = reducedAlertMotion.matches ? 'reduced' : 'full';
    requestAlert.hidden = false;
    // Manual popovers sit above dialogs without making the workspace inert.
    // Older browsers retain the fixed-position fallback.
    if (typeof requestAlert.showPopover === 'function') requestAlert.showPopover();
    requestAlertTimer = setTimeout(dismissRequestAlert, 2500);
  }
  reducedAlertMotion.addEventListener('change', () => {
    requestAlert.dataset.motion = reducedAlertMotion.matches ? 'reduced' : 'full';
  });
  root.addEventListener('input', event => {
    if (event.target.matches('input,textarea,[contenteditable=true]')) dismissRequestAlert();
  });
  root.addEventListener('keydown', event => {
    if (event.target.matches('input,textarea,[contenteditable=true]') &&
        (event.key.length === 1 || ['Backspace','Delete'].includes(event.key))) dismissRequestAlert();
  });
  root.addEventListener('click', event => {
    if (event.target.closest('[data-answer-current],[data-respond],[data-defer-request],[data-cancel-request],#ab-emergency-interrupt')) dismissRequestAlert();
  });
  function stopText(snapshot) {
    const verb = snapshot.station.interrupt?.operator_action === 'cancel' ? 'Cancel' : 'Stop';
    const state = snapshot.station.interrupt?.state;
    const observed = state === 'requested' && snapshot.run.status !== 'running' ? 'stop_unconfirmed' : state;
    return {requested:`${verb} requested · awaiting executor confirmation`, stopped:`${verb} confirmed · physical stop verified`,
      stop_unconfirmed:`${verb} unconfirmed · check executor status`, too_late:`${verb} request had no effect · execution already ended`}[observed] || '';
  }
  function timedOutRequest(snapshot) {
    return snapshot.requests.find(request => request.mode === 'live_human_first' &&
      ((request.state === 'pending' && replyExpired(request)) || request.state === 'fallback' ||
       (snapshot.request_lifecycle?.[request.request_id] || []).some(e => ['request.timeout', 'request.fallback'].includes(e.event_type))));
  }
  function countdown(request) {
    if (request.deadline_at == null) return 'No reply deadline';
    const seconds = Math.max(0, Math.ceil(request.deadline_at - Date.now() / 1000));
    if (!DEBUG_VIEW) return seconds ? `⏱ Reply within ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}` : 'Reply time ran out';
    return seconds ? `Reply time remaining: ${seconds} s · Reply deadline: ${dateTime(request.deadline_at)}` : 'Reply timed out · awaiting runtime handling';
  }
  let graphObserver = null;
  const flowKey = snapshot => `attention.flow:${snapshot.run.run_id}`;
  const skillKey = (snapshot, attemptId) => `attention.skill:${snapshot.run.run_id}:${attemptId}`;
  function taskName(value) {return taskTitles[value]||label(value);}
  function attemptCopy(attempt) {
    return {running:'Running',failed:'Unsuccessful',succeeded:'Succeeded',cancelled:'Stopped',timed_out:'Timed out',unknown:'Unconfirmed'}[attempt.status] || 'Unconfirmed';
  }
  function flowModel(snapshot) {
    const nodes=[], edges=[], attempts=[...snapshot.attempts].sort((a,b)=>a.index-b.index);
    const node=(kind,id,title,state,copy,extra={})=>{const value={kind,id,key:`${kind}:${id}`,title,state,copy,...extra};nodes.push(value);return value;};
    const edge=(from,to,state='confirmed',copy='')=>edges.push({from:from.key,to:to.key,state,copy});
    let previous=null, previousRequests=[];
    for(const attempt of attempts) {
      const current=node('attempt',attempt.attempt_id,`Attempt ${attempt.index+1}`,attempt.status,attemptCopy(attempt),{attemptId:attempt.attempt_id});
      if(previous) {
        const linked=previousRequests.filter(r=>snapshot.response_evidence?.[r.request_id]?.association?.status==='linked'&&snapshot.response_evidence[r.request_id].association.attempt_id===attempt.attempt_id);
        if(linked.length) for(const request of linked) {
          const reply=nodes.find(n=>n.key===`response:${request.request_id}`);
          if(reply) edge(reply,current,'confirmed','Linked');
        }
        else if(!previousRequests.length) edge(previous,current);
        else {
          const reply=nodes.findLast(n=>n.kind==='response'&&previousRequests.some(r=>r.request_id===n.id));
          edge(reply||previous,current,'unconfirmed','Unconfirmed');
        }
      }
      const requests=snapshot.requests.filter(r=>r.attempt_id===attempt.attempt_id).sort((a,b)=>a.created_at-b.created_at);
      for(const request of requests) {
        const pending=actionableRequests(snapshot).some(r=>r.request_id===request.request_id);
        const requestNode=node('request',request.request_id,request.request_type==='approval'?'Approval':'Help request',pending?'waiting':request.state,
          pending?'Needs your input':{answered:'Answered',fallback:'Handled by system',cancelled:'Cancelled',timeout:'Timed out'}[request.state]||'Unconfirmed',{requestId:request.request_id});
        edge(current,requestNode);
        const response=snapshot.responses[request.response_id];
        const receipt=submissionReceipts.get(`${snapshot.run.run_id}:${request.request_id}`);
        if(response||receipt) {
          const source=response?.responder==='human'?'Your reply':response?.responder==='advisor_proxy'?'System reply':response?.responder?'Other reply':'Reply';
          const reply=node('response',request.request_id,source,'answered','Received',{requestId:request.request_id});
          edge(requestNode,reply);
        }
      }
      previous=current;previousRequests=requests;
    }
    if(!attempts.length) node('preparing','prepare','Prepare','running','Starting');
    const max=snapshot.task_plan?.max_attempts ?? launches.find(l=>l.run_id===snapshot.run.run_id)?.locked?.max_attempts;
    const last=attempts.at(-1);
    if(!isTerminal(snapshot)&&!snapshot.station.interrupt&&last&&last.index+1<max&&last.status!=='succeeded'&&!(snapshot.work_items||[]).some(w=>w.state==='blocked')) {
      const planned=node('planned',String(last.index+1),`Attempt ${last.index+2}`,'planned','Possible retry');
      edge(nodes.findLast(n=>n.kind==='response')||nodes.findLast(n=>n.kind==='request')||previous,planned,'planned','Possible');
    }
    if(!isTerminal(snapshot)&&(snapshot.eval?.native_success!=null||snapshot.eval?.diagnosis||snapshot.eval?.error)) {
      const result=node('result','result','Result records','unknown','Available');
      if(previous)edge(previous,result,'confirmed','Recorded');
    }
    if(isTerminal(snapshot)) {
      const end=node('end','end','Run ended',snapshot.run.status,snapshot.run.status==='cancelled'?'Cancelled':snapshot.eval?.native_success===true?'Task successful':snapshot.eval?.native_success===false?'Unsuccessful':'Outcome unconfirmed');
      if(previous) edge(previous,end,'confirmed','Ended');
    }
    return {nodes,edges};
  }
  function flowSelection(snapshot, model=flowModel(snapshot)) {
    const saved=stored(flowKey(snapshot));
    const chosen=model.nodes.find(n=>n.key===saved);
    if(chosen)return chosen;
    const pending=actionableRequests(snapshot)[0];
    return model.nodes.find(n=>pending&&n.key===`request:${pending.request_id}`)||
      model.nodes.find(n=>n.kind==='end')||model.nodes.findLast(n=>n.kind==='response')||
      model.nodes.findLast(n=>n.kind==='attempt')||model.nodes[0];
  }
  function graphEdges(edges,type) {
    return `<svg class="ab-graph-lines" style="position:absolute;left:0;top:0;pointer-events:none" aria-hidden="true"><defs><marker id="ab-${type}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>${edges.map(e=>`<g data-${type}-edge data-from="${esc(e.from)}" data-to="${esc(e.to)}" data-state="${esc(e.state||'confirmed')}"><path marker-end="url(#ab-${type}-arrow)"/><text>${esc(e.copy||'')}</text></g>`).join('')}</svg>`;
  }
  function taskFlow(snapshot) {
    const model=flowModel(snapshot), selected=flowSelection(snapshot,model);
    const attempts=model.nodes.filter(n=>n.kind==='attempt'), older=attempts.slice(0,-2);
    const canFold=attempts.length>3, expanded=stored(`attention.flow-expanded:${snapshot.run.run_id}`)==='true';
    let nodes=model.nodes,edges=model.edges;
    if(canFold&&!expanded) {
      const boundary=model.nodes.findIndex(n=>n.key===attempts.at(-2).key);
      const hidden=new Set(model.nodes.slice(0,boundary).map(n=>n.key));
      const history={kind:'history',id:'history',key:'history:history',title:'Earlier attempts',state:'recorded',copy:`${older.length} attempts · click to expand`};
      nodes=[history,...model.nodes.slice(boundary)];
      edges=model.edges.filter(e=>!hidden.has(e.to)).map(e=>hidden.has(e.from)?{...e,from:history.key,copy:'Folded'}:e);
    }
    return `<div class="ab-task-flow" data-task-flow data-stale="${root.dataset.connection==='disconnected'}"><div class="ab-flow-caption"><strong>Task progress</strong><span>Solid: recorded · dashed: possible or unconfirmed</span>${canFold&&expanded?'<button type="button" data-toggle-flow-history>Collapse earlier attempts</button>':''}</div><div class="ab-flow-scroll" tabindex="0" aria-label="Attempt flow; scroll horizontally"><div class="ab-graph-canvas" style="position:relative" data-graph-canvas="flow">${graphEdges(edges,'flow')}<div class="ab-flow-track">${nodes.map(n=>`<button type="button" class="ab-flow-node" data-flow-kind="${esc(n.kind)}" data-entity-id="${esc(n.id)}" ${n.kind!=='history'?'aria-haspopup="dialog"':''} ${n.kind==='history'?'data-toggle-flow-history':`data-flow-id="${esc(n.key)}"`} data-graph-node="${esc(n.key)}" data-state="${esc(n.state)}" aria-pressed="${selected?.key===n.key}"><span class="ab-node-symbol">${['failed','timed_out'].includes(n.state)?'!':n.state==='waiting'?'◆':n.state==='planned'?'○':n.state==='unknown'?'?':'●'}</span><b>${esc(n.title)}</b><small>${esc(n.copy)}</small></button>`).join('')}</div></div></div></div>`;
  }
  function skillLabel(node) {
    const known={detect:'Locate',find_objects:'Locate',approach:'Approach',grasp:'Grasp',lift:'Lift',evaluate:'Check',open:'Open',close:'Close'};
    return known[node.name] || displayDescription(node.label || node.display_name || node.name, `Skill ${node.ordinal||''}`);
  }
  function skillCopy(node) {
    if(node.goal_status==='succeeded')return 'Goal achieved';
    if(node.goal_status==='failed')return 'Goal not achieved';
    return {running:'Running',executed:'Executed · effect unconfirmed',failed:'Execution failed',blocked:'Blocked',stopped:'Stopped',planned:'Planned',not_executed:'Not executed',unknown:'Progress unconfirmed'}[node.execution_status]||'Progress unconfirmed';
  }
  function skillStates(node, isRoot) {
    const development = {done:'Complete',review:'Awaiting review',planned:'Planned',running:'In development',failed:'Development failed'}[node.development_status] || 'Unconfirmed';
    const execution = {running:'Running',executed:'Executed',failed:'Execution failed',blocked:'Blocked',stopped:'Stopped',planned:'Planned',not_executed:'Not executed',unknown:'Unconfirmed'}[node.execution_status] || 'Unconfirmed';
    const goal = {succeeded:'Succeeded',failed:'Unsuccessful',unconfirmed:'Unconfirmed'}[node.goal_status] || 'Unconfirmed';
    return `<p data-skill-state-summary><span data-skill-state="development">Development: ${development}</span> · <span data-skill-state="execution">Execution: ${execution}</span> · <span data-skill-state="goal">${isRoot?'Native task':'Outcome'}: ${goal}</span></p>`;
  }
  function skillIcon(node) {
    // Fixed local SVGs only; names and descriptions never become SVG markup.
    const icons={
      detect:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5M10 7v6M7 10h6"/>',
      approach:'<path d="M4 12h15m-5-5 5 5-5 5M4 5v14"/>',
      grasp:'<path d="M5 4v8l4 4m10-12v8l-4 4M9 8v4m6-4v4M9 19h6"/><rect x="9" y="13" width="6" height="6" rx="1"/>',
      lift:'<path d="M12 15V3m-4 4 4-4 4 4M5 19h14"/><path d="M7 15v6h10v-6"/>',
      evaluate:'<path d="m7 12 3 3 7-7"/><circle cx="12" cy="12" r="9"/>',
      open:'<path d="M9 4v7l-5 5m11-12v7l5 5M4 11v5h5m11-5v5h-5"/>',
      close:'<path d="M4 4v7l5 5m11-12v7l-5 5M4 16h5v-5m11 5h-5v-5"/>',
    };
    const names={find_objects:'detect',定位:'detect',接近:'approach',抓取:'grasp',抬起:'lift',检查:'evaluate',张开:'open',夹紧:'close'};
    const path=icons[names[node.name]||node.name]||icons[names[node.label]]||'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>';
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
  }
  function skillNode(node,selected) {
    const state=node.goal_status==='succeeded'?'succeeded':node.goal_status==='failed'?'failed':node.execution_status;
    return `<button type="button" class="ab-flow-node" data-skill-node="${esc(node.node_id)}" data-graph-node="${esc(node.node_id)}" data-runtime="${esc(node.execution_status)}" data-state="${esc(state)}" aria-pressed="${selected}" title="${esc(node.name)}"><span class="ab-skill-shell"><span class="ab-skill-face"><span class="ab-skill-icon" aria-hidden="true">${skillIcon(node)}</span><b>${esc(skillLabel(node))}</b><small><i class="ab-skill-status-dot" aria-hidden="true"></i>${esc(skillCopy(node))}</small></span></span>${node.dependencies?.length?'<span class="ab-skill-port" data-port="in" aria-hidden="true"></span>':''}<span class="ab-skill-port" data-port="out" aria-hidden="true"></span></button>`;
  }
  function attemptDag(snapshot, attemptId) {
    const bound=snapshot.attempt_dags?.[attemptId];
    if(bound?.status!=='bound'||bound.run_id!==snapshot.run.run_id||bound.attempt_id!==attemptId||!bound.dag?.nodes?.length)return `<div class="ab-dag-gap" data-dag-gap>No skill graph recorded for this attempt <small>${esc(displayDescription(bound?.reason,'No Skill DAG bound to this attempt'))}</small></div>`;
    const dag={...bound.dag,nodes:bound.dag.nodes.map((n,i)=>({...n,ordinal:i+1}))},byId=new Map(dag.nodes.map(n=>[n.node_id,n]));
    const depths=new Map(),visiting=new Set();
    const depth=id=>{if(depths.has(id))return depths.get(id);if(visiting.has(id)||!byId.has(id))return 0;visiting.add(id);const value=Math.max(0,...(byId.get(id).dependencies||[]).map(d=>depth(d)+1));visiting.delete(id);depths.set(id,value);return value;};
    const levels=new Map();for(const n of dag.nodes){const level=depth(n.node_id);if(!levels.has(level))levels.set(level,[]);levels.get(level).push(n);}
    const selected=stored(skillKey(snapshot,attemptId));
    const skill=byId.get(selected)||dag.nodes.find(n=>n.execution_status==='running')||byId.get(dag.root_skill)||dag.nodes[0];
    const edges=dag.nodes.flatMap(n=>(n.dependencies||[]).map(dep=>({from:dep,to:n.node_id,state:dep===skill.node_id||n.node_id===skill.node_id?'focused':'confirmed'})));
    const trace=Object.values(snapshot.traces).find(t=>t.attempt_id===attemptId&&(!skill.trace_id||t.trace_id===skill.trace_id));
    const referenced=(trace?.events||[]).map((event,index)=>({event,index})).filter(({event})=>(skill.event_ids||[]).includes(event.event_id));
    const hasVideo=(trace?.evidence||[]).some(e=>e.mime_type?.startsWith('video/')&&e.media_url);
    const timed=referenced.map(item=>({...item,target:replayTarget(item.event,trace.recording,hasVideo)})).find(item=>item.target.seconds!=null);
    return `<div class="ab-attempt-dag" data-attempt-dag data-attempt-id="${esc(attemptId)}" data-dag-revision="${esc(dag.revision)}"><div class="ab-flow-caption"><strong>Skill dependencies · ${dag.nodes.length} ${dag.nodes.length === 1 ? 'node' : 'nodes'}</strong><div class="ab-dag-legend" aria-label="Skill status legend"><span data-legend="running"><i aria-hidden="true"></i>Running</span><span data-legend="recorded"><i aria-hidden="true"></i>Executed</span><span data-legend="unknown"><i aria-hidden="true"></i>Unconfirmed</span></div></div><div class="ab-flow-scroll" tabindex="0" aria-label="Skill dependency graph"><div class="ab-graph-canvas" style="position:relative" data-graph-canvas="skill">${graphEdges(edges,'skill')}<div class="ab-skill-track">${[...levels.keys()].sort((a,b)=>a-b).map(level=>`<div class="ab-skill-level">${levels.get(level).map(n=>skillNode(n,skill.node_id===n.node_id)).join('')}</div>`).join('')}</div></div></div><div class="ab-skill-detail" data-skill-detail><div class="ab-skill-detail-head"><span class="ab-skill-icon" aria-hidden="true">${skillIcon(skill)}</span><div><b>${esc(skillLabel(skill))}</b><span>${esc(skillCopy(skill))}</span></div></div>${skillStates(skill,skill.node_id===dag.root_skill)}<p>${esc(displayDescription(skill.description,'No description provided for this skill'))}</p><small data-skill-basis>Evidence basis: ${esc({executor_dispatch:'Executor record',persisted_attempt:'Attempt record',verified_native_result:'Native result',advisor_visible_skill_event:'Skill event'}[skill.evidence_source]||'No execution evidence yet')}</small>${timed?`<button type="button" data-skill-replay="${timed.index}" data-skill-attempt="${esc(attemptId)}"><svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="m5 3 8 5-8 5Z"/></svg>View linked segment · ${timed.target.mode==='approximate'?'Approximate position':'Synchronized position'}</button>`:`<small>${hasVideo?'No timestamp recorded for this skill; seeking is unavailable':'No recording available for this attempt'}</small>`}<details data-persist-detail="skill-technical:${esc(attemptId)}"><summary>Skill technical details</summary><p>${esc(displayDescription(skill.description,'No full description provided'))}</p><p>Skill: ${esc(skill.name)} · Development status: ${esc(skill.development_status)} (independent of this execution)</p><p>Graph: ${esc(dag.graph_id)} · Revision: ${esc(dag.revision)}</p><p>Status source: ${esc(skill.evidence_source||'Not recorded')}</p><p>Evidence: ${esc((skill.event_ids||[]).join(' · ')||'Not recorded')}</p></details></div></div>`;
  }
  function nodeDetail(snapshot,selected=flowSelection(snapshot)) {
    if(!selected)return '<div data-node-detail>Waiting for the task to start.</div>';
    const request=snapshot.requests.find(r=>r.request_id===selected.requestId);
    const attempt=snapshot.attempts.find(a=>a.attempt_id===selected.attemptId);
    let content='';
    if(attempt) {
      content=attemptSummary(snapshot,attempt);
    } else if(request) {
      const response=snapshot.responses[request.response_id];
      const fallback=(response?.responder==='advisor_proxy'&&!snapshot.dev_v2)||request.state==='fallback'||(snapshot.request_lifecycle?.[request.request_id]||[]).some(e=>e.event_type==='request.fallback');
      content=`${fallback?'<div class="ab-source-badge" data-fallback>Automatic system reply · fallback</div>':response?.responder==='human'?'<div class="ab-source-badge">Your reply</div>':''}${selected.kind==='request'?`<p class="ab-node-request-copy">${actionableRequests(snapshot).some(r=>r.request_id===request.request_id)?'Awaiting your decision':esc(label(request.state))}</p><button type="button" data-open-flow-request="${esc(request.request_id)}">View request${request.state==='pending'?' and reply':''}</button>`:''}${observationPanel(snapshot,request)}<details data-node-technical data-persist-detail="node-technical:${esc(selected.key)}"><summary>Technical details</summary><pre>${esc(JSON.stringify({request_id:request.request_id,response_id:request.response_id,evidence:snapshot.response_evidence?.[request.request_id],response_use:snapshot.response_uses?.[request.request_id]},null,2))}</pre></details>`;
    } else if(selected.kind==='end'||selected.kind==='result')content=evalPanel(snapshot);
    else if(selected.kind==='planned')content='<p>This permitted follow-up attempt has not started. It depends on the outcome and run conditions.</p>';
    else content='<p>Launch accepted; awaiting executor dispatch.</p>';
    return `<div class="ab-node-detail" data-node-detail>${content}</div>`;
  }
  function drawGraphs() {
    for(const canvas of root.querySelectorAll('[data-graph-canvas]')) {
      const svg=canvas.querySelector('svg'),rect=canvas.getBoundingClientRect();
      svg.setAttribute('width',canvas.scrollWidth);svg.setAttribute('height',canvas.scrollHeight);
      const nodes=new Map([...canvas.querySelectorAll('[data-graph-node]')].map(n=>[n.dataset.graphNode,n.getBoundingClientRect()]));
      for(const group of svg.querySelectorAll('g[data-from]')) {
        const a=nodes.get(group.dataset.from),b=nodes.get(group.dataset.to);if(!a||!b)continue;
        const x1=a.right-rect.left+2,y1=a.top+a.height/2-rect.top,x2=b.left-rect.left-3,y2=b.top+b.height/2-rect.top;
        const bend=Math.max(18,(x2-x1)/2),path=group.querySelector('path');
        path.setAttribute('d',`M${x1},${y1} C${x1+bend},${y1} ${x2-bend},${y2} ${x2},${y2}`);
        const text=group.querySelector('text');text.setAttribute('x',(x1+x2)/2);text.setAttribute('y',(y1+y2)/2-9);
      }
    }
  }
  function bindTaskFlow(snapshot) {
    graphObserver?.disconnect();
    for(const button of view.querySelectorAll('[data-toggle-flow-history]'))button.addEventListener('click',()=>{
      const key=`attention.flow-expanded:${snapshot.run.run_id}`;
      stored(key,stored(key)==='true'?'false':'true');render(currentSnapshot);
    });
    for(const button of view.querySelectorAll('[data-flow-id]'))button.addEventListener('click',()=>{
      const model=flowModel(snapshot),node=model.nodes.find(n=>n.key===button.dataset.flowId);if(!node)return;
      stored(flowKey(snapshot),node.key);
      if(node.attemptId)stored(`attention.history:${snapshot.run.run_id}`,node.attemptId);
      render(currentSnapshot);
      if(node.attemptId)openAttemptDag(currentSnapshot,node.attemptId);
      else openFlowDetail(currentSnapshot,node.key);
    });
    if(typeof ResizeObserver==='function') {
      graphObserver=new ResizeObserver(drawGraphs);
      root.querySelectorAll('[data-graph-canvas]').forEach(c=>graphObserver.observe(c));
    }
    requestAnimationFrame(drawGraphs);
  }

  function openAttemptDag(snapshot,attemptId) {
    if(!snapshot.attempts.some(a=>a.attempt_id===attemptId))return;
    activeAttemptDag={runId:snapshot.run.run_id,attemptId};
    refreshAttemptDag(snapshot,true);
    if(!attemptDagDialog.open)attemptDagDialog.showModal();
    attemptDagDialog.querySelector('[data-close-attempt-dag]')?.focus({preventScroll:true});
    requestAnimationFrame(drawGraphs);
  }
  function refreshAttemptDag(snapshot,opening=false) {
    if(!activeAttemptDag||(!attemptDagDialog.open&&!opening))return;
    const {runId,attemptId}=activeAttemptDag;
    const attempt=snapshot.attempts.find(a=>a.attempt_id===attemptId);
    if(runId!==snapshot.run.run_id||!attempt) {
      if(attemptDagDialog.open)closeAttemptDag();
      activeAttemptDag=null;attemptDagDialog.replaceChildren();return;
    }
    const focused=document.activeElement?.dataset.skillNode;
    const closeFocused=document.activeElement?.hasAttribute('data-close-attempt-dag');
    const expanded=[...attemptDagDialog.querySelectorAll('[data-persist-detail][open]')].map(e=>e.dataset.persistDetail);
    const scroll=attemptDagDialog.querySelector('.ab-flow-scroll')?.scrollLeft||0;
    const top=attemptDagDialog.scrollTop;
    attemptDagDialog.innerHTML=`<header class="ab-dag-dialog-header"><div class="ab-dag-heading"><div class="ab-dag-emblem" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="6" height="6" rx="1"/><rect x="2" y="15" width="6" height="6" rx="1"/><rect x="16" y="9" width="6" height="6" rx="1"/><path d="M8 6h4v12H8m4-6h4"/></svg></div><div><h3 id="ab-attempt-dag-title">Attempt ${attempt.index+1} · Skill flow</h3><span>${esc(attemptCopy(attempt))} · Execution record for this attempt</span></div></div><button type="button" data-close-attempt-dag aria-label="CloseSkill flow">Close</button></header><div class="ab-dag-dialog-body"><p data-dag-stale-warning role="alert" ${root.dataset.connection!=='disconnected'?'hidden':''}>Disconnected · showing an older skill graph</p>${attemptSummary(snapshot,attempt)}${attemptDag(snapshot,attemptId)}</div>`;
    attemptDagDialog.querySelectorAll('[data-persist-detail]').forEach(e=>{e.open=expanded.includes(e.dataset.persistDetail);});
    bindNodeActions(attemptDagDialog,snapshot);
    const flowScroll=attemptDagDialog.querySelector('.ab-flow-scroll');if(flowScroll)flowScroll.scrollLeft=scroll;
    attemptDagDialog.scrollTop=top;
    for(const button of attemptDagDialog.querySelectorAll('[data-skill-node]'))button.addEventListener('click',()=>{
      stored(skillKey(snapshot,attemptId),button.dataset.skillNode);
      stored(`attention.history:${runId}`,attemptId);render(currentSnapshot);
      const replay=attemptDagDialog.querySelector('[data-skill-replay]');
      if(replay)view.querySelector(`[data-execution-viewer] summary[data-replay-event="${Number(replay.dataset.skillReplay)}"]`)?.click();
    });
    attemptDagDialog.querySelector('[data-skill-replay]')?.addEventListener('click',event=>{
      const index=Number(event.currentTarget.dataset.skillReplay);
      stored(`attention.history:${runId}`,attemptId);
      closeAttemptDag();render(currentSnapshot);
      view.querySelector(`[data-execution-viewer] summary[data-replay-event="${index}"]`)?.click();
      view.querySelector('[data-execution-viewer]')?.scrollIntoView({block:'start'});
    });
    if(focused)[...attemptDagDialog.querySelectorAll('[data-skill-node]')].find(e=>e.dataset.skillNode===focused)?.focus({preventScroll:true});
    else if(closeFocused)attemptDagDialog.querySelector('[data-close-attempt-dag]')?.focus({preventScroll:true});
    attemptDagDialog.querySelectorAll('[data-graph-canvas]').forEach(c=>graphObserver?.observe(c));
    requestAnimationFrame(drawGraphs);
  }

  function runOverview(snapshot) {
    if (snapshot.dev_session) return devOverview(snapshot);
    const run = snapshot.run, terminal = isTerminal(snapshot);
    const pending = actionableRequests(snapshot)[0];
    const latest = [...snapshot.attempts].sort((a,b) => b.index-a.index)[0];
    const liveAttempt = run.status === 'running' && snapshot.station.camera_attempt_id &&
      !snapshot.attempts.some(a => a.attempt_id === snapshot.station.camera_attempt_id);
    const liveIndex = Number(snapshot.station.camera_attempt_id?.split(':').at(-1));
    const index = latest?.status === 'running' ? latest.index + 1 : liveAttempt
      ? Number.isInteger(liveIndex) && liveIndex >= 0 ? liveIndex + 1 : snapshot.attempts.length + 1 : snapshot.attempts.length;
    const maximum = launches.find(item => item.run_id === run.run_id)?.locked?.max_attempts;
    const native = snapshot.eval?.native_success;
    const failure = latest?.attempt_id && executionFailure(Object.values(snapshot.traces).find(trace => trace?.attempt_id === latest.attempt_id));
    const failureSummary = failure?.message ? String(failure.message).split('\n')[0].slice(0, 160) : '';
    const stop = stopText(snapshot);
    const timeout = timedOutRequest(snapshot);
    let phase, title;
    if (terminal) {
      phase = run.status === 'cancelled' ? 'cancelled' : native === true ? 'success' : 'ended';
      title = `Run ended · ${run.status === 'cancelled' ? 'Cancelled' : native === true ? 'Task successful' : native === false ? 'Task unsuccessful' : 'Native outcome needs verification'}`;
    } else if (snapshot.station.interrupt) { phase = 'stopping'; title = stop; }
    else if (pending) { phase = 'waiting'; title = 'Awaiting your reply'; }
    else if (timeout?.state === 'pending' || timeout?.state === 'fallback') { phase = 'timeout'; title = 'Reply timed out · awaiting runtime handling'; }
    else if (latest?.status === 'running' || liveAttempt) { phase = 'running'; title = 'Execution in progress'; }
    else { phase = 'preparing'; title = run.status === 'created' ? 'Launch request accepted' : 'Preparing the next execution'; }
    const humanReplies = Object.values(snapshot.responses || {}).filter(item => item.responder === 'human').length;
    const humanRequests = snapshot.requests.filter(item => item.mode === 'live_human_first').length;
    const participation = humanReplies ? `Human replies submitted: ${humanReplies}. Adoption still requires subsequent evidence.`
      : humanRequests ? 'No human replies submitted yet.' : 'The human reply stage has not started yet.';
    if (observedRunPhase?.runId !== run.run_id) runNotice.hidden = true;
    if ((observedRunPhase?.runId === run.run_id && observedRunPhase.phase !== phase && (terminal || pending)) ||
        (newlyLinkedRun === run.run_id && terminal)) showRunNotice(`${title}. ${pending ? 'Review the request and submit a reply.' : participation}`);
    observedRunPhase = {runId:run.run_id, phase}; newlyLinkedRun = null;
    const shortages = (snapshot.work_items || []).filter(item => item.state === 'blocked');
    const exhausted = ['tokens', 'gpu_seconds', 'execution_seconds'].filter(key =>
      snapshot.resources[key]?.limit > 0 && snapshot.resources[key].remaining <= 0);
    const needed = pending ? 'yes' : 'no';
    const canInterrupt = run.status === 'running' && ['robocasa_sim', 'robosuite_sim', 'robocasa365_sim'].includes(run.execution_target) && !snapshot.station.interrupt;
    return `${DEBUG_VIEW ? `<section class="panel ab-run-overview" data-core-region="status" data-run-overview="${esc(run.run_id)}" data-phase="${phase}" aria-label="Task Status & Progress">
      <div class="ab-status-panel-heading" aria-hidden="true">Task Status & Progress</div>
      <div class="ab-status-head"><button type="button" class="ab-overview-drag" data-overview-drag hidden aria-label="Move floating task bar" title="Drag to move, or use arrow keys">⠿</button><div><span class="label">${esc(run.suite)} · Current task</span><h2>${esc(taskDisplayName(run))}</h2></div><div class="ab-status-controls"><button type="button" data-overview-open-sidebar>Tasks</button>${launchButton()}${moreButton()}<button id="ab-cancel-run" type="button" ${canInterrupt ? '' : 'disabled'}>Cancel run</button><button class="danger" id="ab-emergency-interrupt" type="button" ${canInterrupt ? '' : 'disabled'} title="Stop target: ${esc(displayRun(run))} (${esc(run.run_id)})">■ Stop ${esc(run.run_id.slice(-8))}</button><button type="button" class="ab-overview-toggle" data-overview-toggle aria-expanded="true" aria-controls="ab-overview-details">Collapse progress</button></div></div>
      <div class="ab-status-main"><div class="ab-action-needed" data-action-needed data-needed="${needed}" role="status" data-connected-needed="${needed}" data-connected-text="${pending ? '⚠ Action required · review the current request' : '✓ No action required'}">${pending ? '⚠ Action required · review the current request' : '✓ No action required'}</div><div class="ab-phase-copy"><strong>${esc(title)}</strong><span data-attempt-count>Attempt ${index || '—'}${maximum ? ` / ${maximum}` : ''}</span></div>${pending ? `<button class="ab-button-primary" type="button" data-answer-current="${esc(pending.request_id)}">Review request & reply</button>` : ''}</div>
      <div id="ab-overview-details" data-overview-details>${taskFlow(snapshot)}<div class="ab-status-meta"><span>${run.execution_target === 'simulation' || run.execution_target?.endsWith('_sim') ? 'Simulator' : 'Real robot'}</span><span>${run.assistance_mode === 'live_human_first' ? 'Human-first assistance' : 'Automatic Advisor assistance'}</span><span data-assistance-balance>Assistance remaining <b>${esc(number(snapshot.resources.assistance?.remaining))}</b> · Reserved ${esc(number(snapshot.resources.assistance?.reserved))}</span><span data-human-stage data-phase="${pending ? 'active' : humanReplies ? 'done' : terminal ? 'skipped' : 'upcoming'}">${esc(participation)}</span></div></div>
      <div class="ab-status-alerts">${stop ? `<p data-stop-state role="status">${esc(stop)}</p>` : ''}${terminal ? `<p data-run-stop-reason>Stop reason: ${esc(shortStopReason(snapshot))}</p>` : ''}${timeout ? `<p data-timeout-warning>${terminal ? 'Reply deadline passed · this run has ended. Replies are closed.' : timeout.state === 'pending' ? 'Reply timed out · awaiting runtime handling. This request no longer accepts replies.' : 'Reply deadline passed · the human reply window is closed. See request history for runtime handling.'}</p>` : ''}${pending ? `<p data-status-countdown>${esc(countdown(pending))}</p>` : ''}<p data-picture-warning hidden></p>${terminal && failureSummary ? `<p data-overview-extra>${failure.error_type === 'TaskOutcomeFailure' ? 'Attempt result' : 'Execution error'}: ${esc(failureSummary)} · See execution details for the full error</p>` : ''}${snapshot.resources.assistance?.limit === 0 ? '<p>Assistance budget is 0; this run will not request help.</p>' : ''}${shortages.length || exhausted.length ? `<p data-resource-warning>Insufficient resources: ${esc([...shortages.map(item => `${label(item.resource)} requires ${number(item.required)}, remaining ${number(item.remaining)}`), ...exhausted.map(label)].join(' · '))} · See Resources</p>` : ''}</div>
    </section>` : operatorOverview(snapshot)}`;
  }

  const overviewUI={runId:null,floating:false,expanded:false,homeExpanded:true,homeHeight:0,position:null,drag:null,frame:null};
  function placeOverview(card) {
    if (!DEBUG_VIEW) return;
    if(!overviewUI.floating)return;
    const bounds=card.getBoundingClientRect(),margin=12;
    const form=view.querySelector('.ab-response-form')?.getBoundingClientRect();
    // Keep the default float away from reply controls while the form is visible.
    const replying=form&&form.bottom>0&&form.top<innerHeight;
    const position=overviewUI.position||{x:innerWidth-bounds.width-margin,y:replying?margin:innerHeight-bounds.height-margin};
    const x=Math.max(margin,Math.min(position.x,innerWidth-bounds.width-margin));
    const y=Math.max(margin,Math.min(position.y,innerHeight-bounds.height-margin));
    card.style.left=`${x}px`;card.style.top=`${y}px`;
  }
  function updateOverview() {
    if (!DEBUG_VIEW) return;
    overviewUI.frame=null;
    const card=view.querySelector('[data-run-overview]');
    const spacer=view.querySelector('[data-overview-spacer]');
    if(!card||!spacer)return;
    if(!overviewUI.floating)overviewUI.homeHeight=card.offsetHeight;
    // Use the card's own position; keep an active float until its header returns.
    // This also preserves a dragged float when responsive settings grow on resize.
    const homeBounds=(spacer.hidden?card:spacer).getBoundingClientRect();
    const floating=overviewUI.floating?homeBounds.top<8:homeBounds.bottom<8;
    if(floating!==overviewUI.floating) {
      overviewUI.floating=floating;overviewUI.expanded=false;
    }
    const expanded=floating?overviewUI.expanded:overviewUI.homeExpanded;
    card.dataset.floating=String(floating);card.dataset.expanded=String(expanded);
    spacer.hidden=!floating;spacer.style.height=`${overviewUI.homeHeight}px`;
    const toggle=card.querySelector('[data-overview-toggle]');
    toggle.setAttribute('aria-expanded',String(expanded));
    toggle.textContent=expanded?'Collapse progress':'Expand progress';
    card.querySelector('[data-overview-drag]').hidden=!floating;
    if(floating)placeOverview(card);
    else {card.style.left='';card.style.top='';}
    positionRunNotice();
    requestAnimationFrame(drawGraphs);
  }
  function scheduleOverview() {
    if(overviewUI.frame==null)overviewUI.frame=requestAnimationFrame(updateOverview);
  }
  window.addEventListener('scroll',scheduleOverview,{passive:true});
  window.addEventListener('resize',scheduleOverview,{passive:true});
  function bindOverview(snapshot) {
    if (!DEBUG_VIEW) return;
    const card=view.querySelector('[data-run-overview]');if(!card)return;
    if(overviewUI.runId!==snapshot.run.run_id) {
      overviewUI.runId=snapshot.run.run_id;overviewUI.expanded=false;
      overviewUI.homeExpanded=stored(`attention.overview-expanded:${overviewUI.runId}`)!=='false';
    }
    const spacer=document.createElement('div');
    spacer.dataset.overviewSpacer='';spacer.hidden=true;spacer.setAttribute('aria-hidden','true');
    card.before(spacer);
    // Measure its normal-flow height before it becomes a compact floating card.
    card.dataset.expanded=String(overviewUI.homeExpanded);
    overviewUI.homeHeight=card.offsetHeight;
    card.querySelector('[data-overview-toggle]').addEventListener('click',()=>{
      if(overviewUI.floating)overviewUI.expanded=!overviewUI.expanded;
      else {
        overviewUI.homeExpanded=!overviewUI.homeExpanded;
        stored(`attention.overview-expanded:${overviewUI.runId}`,String(overviewUI.homeExpanded));
        card.dataset.expanded=String(overviewUI.homeExpanded);overviewUI.homeHeight=card.offsetHeight;
      }
      updateOverview();
    });
    const handle=card.querySelector('[data-overview-drag]');
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0||!overviewUI.floating)return;
      event.preventDefault();handle.focus({preventScroll:true});
      handle.setPointerCapture(event.pointerId);
      const rect=card.getBoundingClientRect();
      overviewUI.drag={pointer:event.pointerId,offsetX:event.clientX-rect.left,offsetY:event.clientY-rect.top};
      card.dataset.dragging='true';
    });
    handle.addEventListener('keydown',event=>{
      if(!overviewUI.floating||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();
      const rect=card.getBoundingClientRect(),step=event.shiftKey?40:12;
      overviewUI.position={x:rect.left+({ArrowLeft:-step,ArrowRight:step}[event.key]||0),y:rect.top+({ArrowUp:-step,ArrowDown:step}[event.key]||0)};
      placeOverview(card);
    });
    updateOverview();
  }
  window.addEventListener('pointermove',event=>{
    const drag=overviewUI.drag;if(!drag||event.pointerId!==drag.pointer)return;
    overviewUI.position={x:event.clientX-drag.offsetX,y:event.clientY-drag.offsetY};
    const card=view.querySelector('[data-run-overview]');if(card)placeOverview(card);
  });
  function finishOverviewDrag() {
    overviewUI.drag=null;
    view.querySelector('[data-run-overview]')?.removeAttribute('data-dragging');
  }
  window.addEventListener('pointerup',finishOverviewDrag);
  window.addEventListener('pointercancel',finishOverviewDrag);

  function handStatusContent(control,history) {
    const phase=({pickup:'Pickup',handoff:'Handoff',placement:'Placement'})[control?.task_phase]||'Not recorded';
    return `${['left','right'].map(side=>{const arm=control?.arms?.[side], grip=control?.grippers?.[side];return `<article data-arm="${side}"><b>${side[0].toUpperCase()+side.slice(1)} hand</b><p>${arm?.action_label?esc(arm.action_label):arm?arm.active?'Executing':arm.grasped||arm.holding?'Holding':'Idle':'State unavailable'} · ${arm?.action_id?esc(arm.action_id):'No active action recorded'}</p><small>grasped: ${arm?String(arm.grasped):'unavailable'} · holding: ${arm?String(arm.holding):'unavailable'} · ownership: ${esc(control?.ownership??'none')}</small><br><small>${grip?`Gripper ${Number(grip.driver_position_m).toFixed(4)} m · ${arm?.grasped?'Target contact confirmed':arm?.holding?'Physical hold verified · intermittent contact sample':'No bilateral target contact'}`:'Gripper state unavailable'}</small></article>`;}).join('')}<p data-cooperation-phase>Task phase: ${esc(phase)} · Handoff phase: ${esc(control?.handoff?.phase||'Not recorded')} · Physical step ${esc(control?.step??'Not recorded')}${history?' · Replay sample':''}</p>`;
  }
  function handReplayRows(snapshot,trace) {
    if(trace?.recording?.control_samples)return trace.recording.control_samples;
    const binding=trace?.recording?.control_recording;
    if(!binding)return null;
    const key=JSON.stringify([snapshot.run.run_id,trace.attempt_id,binding.url,binding.sha256]);
    let cached=handReplayCache.get(key);
    if(!cached){
      cached={rows:null};handReplayCache.set(key,cached);
      fetch(binding.url).then(async response=>{
        if(!response.ok)throw new Error('Hand replay unavailable');
        const value=await response.json();
        if(value.schema!=='attentionbench.bimanual-control-recording.v1'||value.run_id!==snapshot.run.run_id||value.attempt_id!==trace.attempt_id||!Array.isArray(value.samples)||!value.samples.length||value.samples.length>5000)throw new Error('Hand replay identity mismatch');
        let last=-1;
        for(const row of value.samples){if(!Number.isFinite(row.video_seconds)||row.video_seconds<=last||!Number.isInteger(row.control?.step)||!Number.isInteger(row.control?.epoch))throw new Error('Invalid hand replay clock');last=row.video_seconds;}
        cached.rows=value.samples;
        if(currentSnapshot?.run.run_id===snapshot.run.run_id&&stored(`attention.history:${snapshot.run.run_id}`)===trace.attempt_id)updateReplayArms(currentSnapshot,view.querySelector('video[data-history-replay]'));
      }).catch(error=>{cached.error=error.message;});
    }
    return cached.rows;
  }
  function updateReplayArms(snapshot,video) {
    if(snapshot?.run.robot?.execution_arm!=='both'||!video)return;
    const id=stored(`attention.history:${snapshot.run.run_id}`),trace=Object.values(snapshot.traces).find(t=>t.attempt_id===id);
    const rows=handReplayRows(snapshot,trace),panel=view.querySelector('[data-arm-status]');
    if(!panel)return;
    const sample=rows?.findLast(row=>row.video_seconds<=video.currentTime);
    panel.innerHTML=handStatusContent(sample?.control,true);
  }
  function armStatus(snapshot) {
    if (snapshot.dev_session && !snapshot.station?.arm_control) return '';
    if(snapshot.run.robot?.execution_arm!=='both')return '';
    const id=stored(`attention.history:${snapshot.run.run_id}`),historical=snapshot.attempts.some(a=>a.attempt_id===id);
    const trace=historical?Object.values(snapshot.traces).find(t=>t.attempt_id===id):null;
    const control=historical?handReplayRows(snapshot,trace)?.[0]?.control:snapshot.station?.arm_control;
    return `<div class="ab-arm-status" data-arm-status>${handStatusContent(control,historical)}</div>`;
  }

  // Fixed launch selection shares the server's profile identity and budgets.
  function fixedLaunchSelection(profile, values) {
    if (!profile?.id || !String(profile.execution_target).endsWith('_sim')) throw new Error('A registered simulator profile is required');
    return {profile_id:profile.id,seed:profile.seed,attention_policy:values.policy,
      execution_target:profile.execution_target,assistance_mode:values.assistance_mode,
      max_attempts:Number(values.attempts),assistance_credits:Number(values.credits),
      token_limit:Number(values.tokens),human_deadline_seconds:Number(values.human_deadline),
      overall_deadline_seconds:Number(values.wall_seconds),request_mode:values.request_mode,
      gpu_seconds:Number(values.gpu_seconds)};
  }

  function bindLaunch() {
    view.querySelector('[data-open-launch]')?.addEventListener('click', () => {
      const options = launchOptions;
      const certifiedProfiles = (options?.profiles || []).filter(p => p.certified_bimanual_v2).map(p => ({...p,task_id:p.task,robot:p.robot.model,execution_arm:p.robot.execution_arm,enabled:true,channel:'certified_v2',wall_seconds:600,attempt_wall_seconds:600,sdk_call_limit:200,base_mode:'stationary'}));
      const fixedProfiles=(options?.profiles || []).filter(p=>!p.certified_bimanual_v2).map(p=>({...p,
        task_id:p.task,robot:p.robot?.model || 'Approved configuration',execution_arm:p.robot?.execution_arm || 'single',
        enabled:true,channel:'formal',wall_seconds:300,attempt_wall_seconds:p.attempt_wall_seconds || 120,sdk_call_limit:200}));
      const devProfiles = [...certifiedProfiles,...fixedProfiles,...(options?.dev_loop?.profiles || options?.dev_v2?.profiles || [])];
      const executionLocations=(options?.execution_targets || []).map(target=>({...target,
        available:target.available || target.id==='simulation' && devProfiles.some(profile=>profile.enabled && String(profile.execution_target).endsWith('_sim'))}));
      if (!devProfiles.length) return;
      const benchmarkNames = {robosuite:'Robosuite',robocasa:'RoboCasa',robocasa365:'RoboCasa365'};
      const armNames = {single:'Single arm',left:'Left only',right:'Right only',both:'Both arms'};
      const source = linkedConfiguration?.run || currentSnapshot?.run;
      const isFixed=p=>['certified_v2','formal'].includes(p.channel);
      const matchingProfiles=source?devProfiles.filter(p=>p.suite===source.suite&&p.task_id===source.task_id&&p.seed===source.seed&&p.robot===source.robot?.model&&p.execution_arm===source.robot?.execution_arm&&p.enabled):[];
      const preferDev=Boolean(currentSnapshot?.dev_session);
      const sourceProfile=matchingProfiles.find(p=>isFixed(p)!==preferDev)||matchingProfiles[0];
      launchDialog.innerHTML = `<div class="ab-launch-shell">
        <header class="ab-launch-header"><div><span class="kicker">NEW RUN / SIMULATION</span><h3>Run configuration</h3><p>Choose a run type, then configure its task and resources.</p></div><button class="ab-button-quiet" type="button" data-close-launch aria-label="Close configuration">Close</button></header>
        <form id="ab-launch-form"><div class="ab-launch-body">
          ${source?`<p class="ab-launch-note" data-launch-source>${sourceProfile?'Task, robot and arm copied from the current record.':'The current configuration is unavailable for a new run. Choose a supported configuration below.'} Source: ${esc(displayRun(source))}</p>`:''}
          <section class="ab-launch-section"><h4><span>01</span>Run settings</h4><div class="ab-launch-grid">
            <label class="ab-launch-field ab-launch-type">Run type<select name="run_type" aria-label="Run type">${devProfiles.some(p=>!isFixed(p))?'<option value="dev">Dev automatic iteration</option>':''}${devProfiles.some(isFixed)?'<option value="fixed">Fixed policy execution</option>':''}</select><small data-run-type-description></small></label>
            <label class="ab-launch-field">Benchmark<select name="benchmark" aria-label="Benchmark" required></select></label>
            <label class="ab-launch-field">Task<select name="task_id" aria-label="Task" required></select></label>
            <label class="ab-launch-field ab-launch-name">Name suffix (optional)<input name="name_suffix" aria-label="Name suffix" maxlength="80" placeholder="e.g. lower-grasp-v2"><small data-launch-name-preview aria-live="polite"></small></label>
            <label class="ab-launch-field">Robot<select name="robot_model" aria-label="Robot" required></select></label>
            <label class="ab-launch-field">Execution mode<select name="execution_arm" aria-label="Execution mode" required></select></label>
            <label class="ab-launch-field">Development seed<input name="seed" type="number" value="101" readonly></label>
            <label class="ab-launch-field">Execution target<input name="execution_target" value="RoboCasa365 simulator" readonly></label>
            <label class="ab-launch-field">Execution location<select name="execution_target_choice">${executionLocations.map(target=>`<option value="${esc(target.id)}" ${target.available?'':'disabled'}>${esc(label(target.id))}${target.available?'':` · Unavailable: ${esc(target.reason || 'No runner available')}`}</option>`).join('')}</select></label>
            <label class="ab-launch-field">Request mode<select name="request_mode">${(options?.request_modes || []).map(mode=>`<option value="${esc(mode.id)}" ${mode.default?'selected':''}>${esc(label(mode.id))}</option>`).join('')}</select><small>Fixed-policy runs; Dev iterations use their registered help workflow.</small></label>
            <label class="ab-launch-field">GPU time budget (seconds)<input name="gpu_seconds" type="number" min="0" max="${esc(options?.limits?.gpu_seconds ?? 0)}" value="0"><small>0: track usage without a GPU budget.</small></label>
            <label class="ab-launch-field">Policy model<input name="model" value="${esc(options?.dev_loop?.model || 'Approved execution policy')}" readonly></label>
          </div></section>
          <section class="ab-launch-section" data-fixed-config hidden><h4>Fixed policy assistance</h4><div class="ab-launch-grid">
            <label class="ab-launch-field">Attention policy<select name="fixed_policy">${(options?.attention_policies || []).map(policy=>`<option value="${esc(policy)}">${esc(policyTitle(policy))}</option>`).join('')}</select></label>
            <label class="ab-launch-field">Assistance mode<select name="fixed_assistance">${(options?.assistance_modes || []).map(mode=>`<option value="${esc(mode)}">${esc(label(mode))}</option>`).join('')}</select></label>
            <label class="ab-launch-field">Assistance credits<input name="fixed_credits" type="number" min="0" max="${esc(options?.limits?.assistance_credits ?? 0)}" value="1"></label>
            <label class="ab-launch-field">Human reply deadline (seconds)<input name="fixed_human_deadline" type="number" min="1" max="${esc(options?.limits?.human_deadline_seconds ?? 0)}" value="60"></label>
          </div></section>
          <section class="ab-launch-section" data-v2-config hidden><h4>Dev v2 experiment</h4><p class="minor">Development validation only. Scene reproducibility across resets is unverified; do not use these records for a formal policy comparison.</p><div class="ab-launch-grid">
            <label class="ab-launch-field"><span>Dispatch</span><select name="v2_start"><option value="yes">Start real model and simulation</option><option value="no">Create only · no resource dispatch</option></select></label><label class="ab-launch-field">New goal ID<input name="goal_id" value="${esc(options?.dev_v2?.goal_key||`daily-v2-${Date.now()}`)}" ${options?.dev_v2?.goal_key?'readonly':''} required></label>
            <label class="ab-launch-field">Session type<select name="v2_mode"><option value="experiment">Frozen policy comparison</option><option value="interactive">Interactive · excluded from comparison</option></select></label>
            <label class="ab-launch-field">Attention policy<select name="v2_policy"><option value="all">All seven · frozen comparison</option>${(options?.dev_v2?.policies||[]).map(p=>`<option value="${esc(p)}">${esc(policyTitle(p))}</option>`).join('')}</select></label>
            <label class="ab-launch-field op-eval-toggle"><input name="eval_agent_enabled" type="checkbox" checked ${options?.dev_v2?.eval_agent_supported===false?'disabled':''}>HumanProxy — simulated human; answers review and hint requests from the recording</label>
            <label class="ab-launch-field">Help count / credits<input name="help_count" type="number" min="0" max="2" value="2"></label>
            <label class="ab-launch-field">Human approval wait (seconds)<input name="human_wait_seconds" type="number" min="1" max="600" value="60"></label>
          </div><details><summary>Independent resource limits</summary><p>Each column is frozen at creation. Units: counts, tokens, credits, seconds.</p><table><thead><tr><th>Resource</th><th>Cell limit</th><th>Experiment limit</th></tr></thead><tbody>${Object.entries(options?.dev_v2?.default_cell_budget||{}).map(([k,v])=>`<tr><td>${esc(label(k))}</td><td><input aria-label="Cell ${esc(k)}" data-v2-cell="${k}" type="number" min="0" value="${v}"></td><td><input aria-label="Experiment ${esc(k)}" data-v2-experiment="${k}" type="number" min="0" value="${options?.dev_v2?.goal_limits?.[k]??v}"></td></tr>`).join('')}</tbody></table></details>
          <details><summary>Entry support matrix</summary>${(options?.dev_v2?.matrix||[]).map(r=>`<p>${esc(r.robot)} / ${esc(r.arm)} · ${esc(r.task)}: ${r.dev_v2_supported?'Supported':esc(r.reason)}</p>`).join('')}</details></section>
          <section class="ab-launch-section"><h4><span>02</span>Resource budgets</h4><div class="ab-launch-grid budgets-grid">
            <label class="ab-launch-field"><span data-iterations-label>Maximum iterations / executions</span><input name="max_attempts" type="number" min="1" max="10" value="3" required></label>
            <label class="ab-launch-field">Token budget<input name="token_limit" type="number" min="0" max="100000" value="60000" required></label>
            <label class="ab-launch-field">Total SDK calls<input name="sdk_calls" type="number" readonly></label>
          </div></section>
          <section class="ab-launch-section"><h4><span>03</span>Time limits</h4><div class="ab-launch-grid">
            <label class="ab-launch-field">Session time limit<span class="ab-launch-unit"><input name="wall_seconds" type="number" min="30" max="1800" required><span aria-hidden="true">seconds</span></span></label>
            <label class="ab-launch-field">Execution deadline per attempt<span class="ab-launch-unit"><input name="attempt_wall_seconds" type="number" readonly><span aria-hidden="true">seconds</span></span></label>
          </div></section>
        </div><footer class="ab-launch-footer"><p class="ab-launch-note" data-launch-preview aria-live="polite"></p><div class="ab-launch-actions"><button class="ab-button-quiet" type="button" data-close-launch>Cancel</button><button class="ab-button-primary" type="submit">Start run</button></div><p role="status" id="ab-launch-message"></p></footer></form></div>`;
      const form = launchDialog.querySelector('form');
      form.eval_agent_enabled.checked = options?.dev_v2?.eval_agent_supported !== false;
      const requestKey = globalThis.crypto?.randomUUID?.() || `dev-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      let submitting = false;
      function fillOptions(select, values, title, unavailable = () => false) {
        const previous = select.value;
        select.innerHTML = values.map(value => `<option value="${esc(value)}" ${unavailable(value)?'disabled':''}>${esc(title(value))}</option>`).join('');
        if (values.includes(previous)) select.value = previous;
        else select.value = values.find(value => !unavailable(value)) || values[0] || '';
      }
      function taskProfiles() {
        return devProfiles.filter(p => isFixed(p)===(form.run_type.value==='fixed') && p.suite === form.benchmark.value && p.task_id === form.task_id.value);
      }
      function selectedProfile() {
        return taskProfiles().find(p => p.robot === form.robot_model.value && p.execution_arm === form.execution_arm.value);
      }
      function updatePreview() {
        const profile = selectedProfile();
        form.querySelector('[data-launch-name-preview]').textContent = `${taskName(form.task_id.value)}${form.name_suffix.value.trim()?` -- ${form.name_suffix.value.trim()}`:''}`;
        const iterations = Number(form.max_attempts.value);
        form.sdk_calls.value = profile?.channel==='dev_v2'?Number(form.querySelector('[data-v2-cell="sdk_calls"]')?.value||0):profile ? profile.sdk_call_limit * iterations : 0;
        const description = profile ? `${profile.robot} · ${armNames[profile.execution_arm] || profile.execution_arm} · Seed ${profile.seed}${profile.layout_id!=null?` · Layout ${profile.layout_id} / Style ${profile.style_id}`:''}${profile.base_mode==='stationary'?' · Stationary base':''}. ` : '';
        const unavailable = profile && !profile.enabled ? profile.unavailable_reason || 'This configuration is unavailable.' : '';
        form.querySelector('[data-launch-preview]').textContent = unavailable || description + (profile?.channel === 'certified_v2' ? 'Official V2 · approved arm and gripper execution · original SAPIEN scene. Execution completion and native task success are reported separately. Development only · excluded from formal evaluation.' : profile?.channel==='formal' ? 'Runs the approved policy with locked execution and assistance conditions.' : `Up to ${form.max_attempts.value} iterations and model calls share one budget. Robot and scene stay fixed throughout the session.`);
        form.querySelector('[data-run-type-description]').textContent=isFixed(profile||{})?'Runs an approved policy with fixed resource limits.':'Generates and revises policy code within the selected budgets.';
        for(const input of form.querySelectorAll('input[name]')){
          let output=form.querySelector(`[data-fixed-for="${input.name}"]`);
          if(input.readOnly&&!output){output=document.createElement('output');output.dataset.fixedFor=input.name;output.className='ab-fixed-value';input.after(output);}
          input.hidden=input.readOnly;
          if(output){output.hidden=!input.readOnly;output.textContent=input.value;output.setAttribute('aria-label',`${input.name}: ${input.value} (fixed configuration)`);}
        }
        form.querySelector('button[type=submit]').disabled = submitting || !profile?.enabled;
        if(profile?.channel==='dev_v2')form.querySelector('[data-launch-preview]').textContent = description + `Generated version limit: ${form.max_attempts.value}. Cell and experiment resource quotas are independent. ` + ' Registered Dev v2 · help is supplied by the public-only Advisor. Independent task results are display and offline analysis only.';
      }
      function syncProfile(resetWall) {
        const profile = selectedProfile();
        const certified = profile?.channel === 'certified_v2';
        const v2=profile?.channel==='dev_v2';
        const formal=profile?.channel==='formal';
        form.querySelector('[data-fixed-config]').hidden=!formal;
        form.fixed_credits.disabled=!formal;
        form.fixed_human_deadline.disabled=!formal;
        form.querySelector('[data-v2-config]').hidden=!v2;
        if(v2)form.v2_policy.value=options.dev_v2.bridge_schema?'full_trace_aware_attention_planner':'autonomous';
        form.querySelector('[data-iterations-label]').textContent=v2?'Maximum generated versions':'Maximum iterations / executions';
        form.goal_id.required=v2;
        form.v2_start.value=options?.dev_v2?.authorization?.mode==='existing_goal'||options?.dev_v2?.authorization?.mode==='per_run_budget'||options?.dev_v2?.authorization?.deadline_epoch>Date.now()/1000?'yes':'no';
        form.max_attempts.removeAttribute('max');
        form.token_limit.max=v2?Number.MAX_SAFE_INTEGER:100000;
        form.max_attempts.readOnly = certified;
        form.token_limit.readOnly = certified;
        form.wall_seconds.readOnly = certified;
        form.wall_seconds.max = isFixed(profile||{}) ? 600 : profile?.channel==='dev_v2' ? 86400 : 1800;
        form.model.value = isFixed(profile||{}) ? 'Approved execution policy' : (options?.dev_loop?.model || '');
        form.max_attempts.value = certified ? 1 : v2?(options.dev_v2.default_max_iterations||3):3;
        form.token_limit.value = certified ? 0 : formal?30000:v2?(options.dev_v2.default_cell_budget?.provider_tokens||220000):60000;
        form.seed.value = profile?.seed ?? 101;
        form.execution_target.value = profile ? label(profile.execution_target) : '';
        form.request_mode.disabled = !isFixed(profile||{});
        form.gpu_seconds.disabled = !isFixed(profile||{});
        form.attempt_wall_seconds.value = profile?.attempt_wall_seconds ?? '';
        if (resetWall) form.wall_seconds.value = profile?.wall_seconds ?? 600;
        form.querySelector('#ab-launch-message').textContent = '';
        updatePreview();
      }
      function syncArm() {
        const profiles = taskProfiles().filter(p => p.robot === form.robot_model.value);
        fillOptions(form.execution_arm,[...new Set(profiles.map(p => p.execution_arm))],arm => armNames[arm] || arm,
          arm => !profiles.some(p => p.execution_arm === arm && p.enabled));
        syncProfile(true);
      }
      function syncTask() {
        const profiles = taskProfiles();
        fillOptions(form.robot_model,[...new Set(profiles.map(p => p.robot))],model => model,
          model => !profiles.some(p => p.robot === model && p.enabled));
        syncArm();
      }
      function syncBenchmark() {
        fillOptions(form.task_id,[...new Set(devProfiles.filter(p => isFixed(p)===(form.run_type.value==='fixed') && p.suite === form.benchmark.value).map(p => p.task_id))],task => taskName(task));
        syncTask();
      }
      function syncRunType(){
        fillOptions(form.benchmark,[...new Set(devProfiles.filter(p=>isFixed(p)===(form.run_type.value==='fixed')).map(p=>p.suite))],suite=>benchmarkNames[suite]||suite);
        syncBenchmark();
      }
      form.run_type.value=sourceProfile?(isFixed(sourceProfile)?'fixed':'dev'):devProfiles.some(p=>!isFixed(p)&&p.enabled)?'dev':'fixed';
      syncRunType();
      form.run_type.addEventListener('change',syncRunType);
      if (sourceProfile) {
        form.benchmark.value = sourceProfile.suite;
        syncBenchmark();
        form.task_id.value = sourceProfile.task_id;
        syncTask();
        form.robot_model.value = sourceProfile.robot;
        syncArm();
        form.execution_arm.value = sourceProfile.execution_arm;
        syncProfile(true);
      }
      form.benchmark.addEventListener('change',syncBenchmark);
      form.task_id.addEventListener('change',syncTask);
      form.robot_model.addEventListener('change',syncArm);
      form.execution_arm.addEventListener('change',() => syncProfile(true));
      form.addEventListener('input',updatePreview);
      form.addEventListener('change',updatePreview);
      form.addEventListener('submit',async event => {
        event.preventDefault();
        const profile = selectedProfile();
        if (submitting || !profile?.enabled || !form.reportValidity()) return;
        submitting = true;
        updatePreview();
        try {
          const certified = profile.channel === 'certified_v2';
          let selection = certified ? {profile_id:profile.id,seed:profile.seed,attention_policy:'autonomous',execution_target:profile.execution_target,assistance_mode:'benchmark_proxy',max_attempts:1,assistance_credits:0,token_limit:0,human_deadline_seconds:60,overall_deadline_seconds:600,robot_model:profile.robot,execution_arm:profile.execution_arm} : {channel:'dev_loop',profile_id:profile.id,request_key:requestKey,max_attempts:Number(form.max_attempts.value),token_limit:Number(form.token_limit.value),wall_seconds:Number(form.wall_seconds.value),seed:profile.seed};
          if(profile.channel==='formal')selection=fixedLaunchSelection(profile,{policy:form.fixed_policy.value,assistance_mode:form.fixed_assistance.value,attempts:form.max_attempts.value,credits:form.fixed_credits.value,tokens:form.token_limit.value,human_deadline:form.fixed_human_deadline.value,wall_seconds:form.wall_seconds.value,request_mode:form.request_mode.value || 'async',gpu_seconds:form.gpu_seconds.value});
          if(certified){selection.request_mode=form.request_mode.value || 'async';selection.gpu_seconds=Number(form.gpu_seconds.value);}
          if(profile.channel==='dev_v2'){
            const n=Number(form.max_attempts.value),help=Number(form.help_count.value);
            const cell=Object.fromEntries([...form.querySelectorAll('[data-v2-cell]')].map(e=>[e.dataset.v2Cell,Number(e.value)]));
            const experiment=Object.fromEntries([...form.querySelectorAll('[data-v2-experiment]')].map(e=>[e.dataset.v2Experiment,Number(e.value)]));
            if(options.dev_v2.eval_agent_supported!==false){cell.eval_calls=cell.executions;experiment.eval_calls=experiment.executions;}
            cell.provider_tokens=Number(form.token_limit.value);cell.help_count=help;cell.help_credits=help;cell.advisor_calls=help;
            selection={eval_agent:{enabled:form.eval_agent_enabled.checked,model:'parcc/Qwen'},request_key:requestKey,tasks:[profile.task_id],seeds:[Number(form.seed.value)],policies:form.v2_policy.value==='all'?(options.dev_v2.policies):[form.v2_policy.value],mode:form.v2_mode.value,help_budget:help,human_wait_seconds:Number(form.human_wait_seconds.value),configuration:{goal_id:form.goal_id.value,experiment_budget:experiment,cell_budget:cell,max_iterations:n,wall_seconds:Number(form.wall_seconds.value)}};
          }
          if(form.name_suffix.value.trim()&&options?.dev_v2?.bridge_schema!=='main-dev-v3')selection.name_suffix=form.name_suffix.value.trim();
          const response = await controlFetch(profile.channel==='dev_v2'?'/api/dev-v2/experiments':isFixed(profile) ? '/api/runs/start' : '/api/dev/start',{method:'POST',headers:{'Content-Type':'application/json','X-Attention-Token':root.dataset.controlToken || ''},body:JSON.stringify(profile.channel==='dev_v2'?{selection,start:form.v2_start.value==='yes'}:selection)});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          selectedDev = profile.channel==='dev_v2'?'v2-'+result.cells[0].key:isFixed(profile) ? null : result.session_id;
          linkedSource = null;
          linkedConfiguration = null;
          pendingLaunch = isFixed(profile) ? result.launch_id : null;
          stored('attention.pending-launch',pendingLaunch);
          selectedRun = null;
          const url = new URL(location.href);
          url.searchParams.delete('run');
          if (selectedDev) url.searchParams.set('dev',selectedDev);
          else url.searchParams.delete('dev');
          history.replaceState(null,'',url);
          launchDialog.close();
          lastRendered = '';
          await refresh();
        } catch (error) {
          submitting = false;
          form.querySelector('#ab-launch-message').textContent = `Launch failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
          updatePreview();
        }
      });
      launchDialog.showModal();
    });
  }

  function sessionKey(session) {
    return `${session.session_id || ''}|${session.agent_id || ''}`;
  }

  function hasHint(session) {
    return (session.log || []).some(item => item && typeof item === 'object' && item.role === 'user');
  }

  function mergeSessions() {
    const byKey = new Map();
    sessionHistory.forEach(item => byKey.set(sessionKey(item), item));
    const activeGraph = (graphOverride || orchestrator?.graph) === orchestrator?.graph;
    (activeGraph ? orchestrator?.live_sessions || [] : []).forEach(item => {
      if (item.in_progress || !byKey.has(sessionKey(item))) byKey.set(sessionKey(item), item);
    });
    sessions = [...byKey.values()].sort((a, b) => {
      const aTime = typeof a.timestamp === 'number' ? a.timestamp * 1000 : Date.parse(a.timestamp || '') || 0;
      const bTime = typeof b.timestamp === 'number' ? b.timestamp * 1000 : Date.parse(b.timestamp || '') || 0;
      return bTime - aTime;
    });
  }

  function filteredSessions() {
    return sessions.filter(session => {
      if (runFilter === 'current') {
        if (orchestrator?.run_id && session.run_id) {
          if (session.run_id !== orchestrator.run_id) return false;
        } else if (!session.in_progress) return false;
      }
      if (sessionFilter === 'hint') return hasHint(session);
      return sessionFilter === 'all' || session.agent_type === sessionFilter;
    });
  }

  function sessionPanel() {
    const graph = graphOverride || orchestrator?.graph || '';
    const filtered = filteredSessions();
    const groups = new Map();
    filtered.forEach(session => {
      const target = session.target || 'default';
      if (!groups.has(target)) groups.set(target, []);
      groups.get(target).push(session);
    });
    const status = orchestrator ? `GRAPH ${graph} · ${filtered.length} / ${sessions.length} SESSIONS${orchestratorError ? ' · ' + orchestratorError : ''}` : orchestratorError;
    return `<section class="panel ab-sessions" id="ab-sessions"><div class="panel-title"><h3>Agent sessions</h3><span>${esc(status)}</span></div>
      <p class="minor">Developer sessions are available to operators and are excluded from AdvisorProxy request evidence. Historical Human Hints do not automatically become Attention inbox requests.</p>
      <form class="ab-graph-form" id="ab-graph-form"><label>SKILL GRAPH <input name="graph" value="${esc(graph)}" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" maxlength="128" aria-label="Skill graph name"></label><button type="submit">View graph</button></form>
      <div class="ab-session-toolbar" aria-label="Session filters">
        ${['all', 'dev', 'evaluator', 'hint'].map(type => `<button type="button" data-session-filter="${type}" aria-pressed="${sessionFilter === type}">${{all:'All',dev:'Dev',evaluator:'Evaluator',hint:'Has Hint'}[type]}</button>`).join('')}
        <span class="ab-filter-divider"></span>
        ${['current', 'all'].map(type => `<button type="button" data-run-filter="${type}" aria-pressed="${runFilter === type}">${type === 'current' ? 'Current Run' : 'All History'}</button>`).join('')}
      </div><p class="minor">Current Run shows active sessions when the Orchestrator provides no run_id. The Skill DAG follows the selected Orchestrator graph.</p>
      <div class="ab-session-groups">${[...groups.keys()].sort().map(target => `<div class="ab-session-group"><h4>${esc(target)} <small>${groups.get(target).length} sessions</small></h4><div class="ab-session-grid">${groups.get(target).map(session => {
        const index = sessions.indexOf(session);
        const cost = Number(session.cost_usd || 0).toFixed(3);
        return `<button class="ab-session-card" type="button" data-session-index="${index}"><span class="card-top"><span class="label">${esc(String(session.agent_type || 'agent').toUpperCase())} · #${String(index + 1).padStart(2, '0')}</span>${session.in_progress ? chip(session.status || 'running') : hasHint(session) ? '<span class="status-chip answered">HINT</span>' : ''}</span><strong>${esc(session.skill || 'Unknown skill')}</strong><span class="minor">${esc(session.num_turns || 0)} turns · $${cost} · ${esc(dateTime(session.timestamp))}</span></button>`;
      }).join('')}</div></div>`).join('') || `<p class="live-empty-copy">${orchestrator ? 'No sessions match these filters.' : esc(orchestratorError)}</p>`}</div></section>`;
  }

  function openSession(index) {
    const session = sessions[index];
    if (!session) return;
    const log = (session.log || []).map(item => typeof item === 'string' ? {role:'agent',text:item} : item || {});
    const linkedSkill = (graphOverride || orchestrator?.graph) === orchestrator?.graph &&
      (orchestrator?.entries || []).some(entry => entry.name === session.skill);
    sessionDialog.innerHTML = `<div class="ab-session-modal"><div class="panel-title"><h3>${esc(session.skill || 'Agent session')}</h3><button type="button" data-close-session aria-label="Close session">✕</button></div>
      <p class="minor">${esc(String(session.agent_type || 'agent').toUpperCase())} · ${esc(session.target || 'default')} · ${esc(dateTime(session.timestamp))} · ${esc(session.num_turns || log.length)} turns · $${Number(session.cost_usd || 0).toFixed(4)}</p>
      <p class="minor">SESSION ${esc(session.session_id)} · AGENT ${esc(session.agent_id)} ${hasHint(session) ? '· HUMAN HINT' : ''}</p>
      ${linkedSkill ? '<button type="button" class="ab-session-dag-link" data-open-dag-from-session>View Skill DAG nodes & dependencies ↗</button>' : ''}
      <div class="ab-conversation">${log.map(message => `<div class="ab-message ${message.role === 'user' ? 'human' : ''}"><span class="label">${message.role === 'user' ? '★ HUMAN HINT' : esc(String(message.role || 'AGENT').toUpperCase())}</span><pre>${esc(message.text || '')}</pre></div>`).join('') || '<p class="minor">No messages yet.</p>'}</div></div>`;
    sessionDialog.querySelector('[data-open-dag-from-session]')?.addEventListener('click', () => {
      selectedSkill = session.skill;
      sessionDialog.close();
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
      view.querySelector('.work-detail')?.scrollIntoView({block:'nearest'});
    });
    if (!sessionDialog.open) sessionDialog.showModal();
  }

  function dagPanel() {
    const entries = orchestrator?.entries || [];
    if (!entries.length) return `<section class="panel work-detail"><div class="panel-title"><h3>Work source detail</h3><span>SKILL DAG</span></div><p class="live-empty-copy">${esc(orchestratorError || 'No Skill DAG nodes in the current graph.')}</p></section>`;
    const selected = entries.find(entry => entry.name === selectedSkill) ||
      entries.find(entry => entry.name === orchestrator?.agents?.[0]?.skill) || entries[0];
    const downstream = entries.filter(entry => (entry.dependencies || []).includes(selected.name));
    const done = new Set(entries.filter(entry => entry.status === 'done').map(entry => entry.name));
    const active = new Set((orchestrator.agents || []).filter(agent => ['starting', 'running'].includes(agent.status)).map(agent => agent.skill));
    const ready = entries.filter(entry => ['planned', 'failed'].includes(entry.status) &&
      (entry.dependencies || []).every(dep => done.has(dep)) && !active.has(entry.name));
    const activeGraph = !graphOverride || graphOverride === orchestrator.graph;
    const linked = selected.attentionbench_last_run;
    const inStore = linked && runs.some(item => item.run_id === linked.run_id);
    const attentionLink = !linked ? '' : `<p class="minor">AttentionBench · ${linked.native_success ? 'Native success' : 'Native failure'} · ${inStore ? `<a href="/ui/?run=${encodeURIComponent(linked.run_id)}">Open run ${esc(linked.run_id)}</a>` : 'This run is not in the current UI store'}</p>`;
    return `<section class="panel work-detail"><div class="panel-title"><h3>Skill DAG · ${esc(orchestrator.graph)}</h3><span>${entries.length} NODES · ORCHESTRATOR</span></div>
      <div class="ab-dag-dispatch"><span class="label">${orchestrator.autonomous_mode ? 'AUTO DISPATCH ACTIVE' : 'AUTO DISPATCH INACTIVE'} · ${ready.length} READY</span>
        <button type="button" data-dag-dispatch ${activeGraph ? '' : 'disabled'}>${orchestrator.autonomous_mode ? 'Dispatch ready nodes' : 'Start automatic dispatch'}</button></div>
      ${!activeGraph ? '<p class="minor">Viewing a historical graph. Switch to the active Orchestrator graph to dispatch nodes.</p>' : ''}
      ${dagActionMessage ? `<p class="minor" role="status">${esc(dagActionMessage)}</p>` : ''}
      <div class="ab-dag-nodes">${entries.map(entry => `<button type="button" data-dag-skill="${esc(entry.name)}" aria-pressed="${selected.name === entry.name}"><b>${esc(entry.name)}</b>${chip(entry.status || 'planned')}</button>`).join('')}</div>
      <div class="ab-dag-detail"><span class="label">SELECTED NODE · ${esc(selected.name)}</span><p>${esc(displayDescription(selected.description, 'No description provided'))}</p><p>${['planned', 'failed'].includes(selected.status) ? (selected.dependencies || []).every(dep => done.has(dep)) ? 'Dependencies satisfied; awaiting dispatch.' : `Waiting for dependencies: ${esc((selected.dependencies || []).filter(dep => !done.has(dep)).join(' · '))}` : 'The Orchestrator manages this node and downstream dispatch.'}</p>${attentionLink}<div class="dag-chain"><span class="dag-node">DEPENDENCIES<b>${(selected.dependencies || []).map(esc).join(' · ') || 'None'}</b></span><span class="dag-arrow">→</span><span class="dag-node current">${esc(selected.name)}<b>${esc(selected.status || 'planned')}</b></span><span class="dag-arrow">→</span><span class="dag-node">DOWNSTREAM<b>${downstream.map(entry => esc(entry.name)).join(' · ') || 'None'}</b></span></div></div></section>`;
  }

  function number(value, suffix = '') {
    if (value == null) return '—';
    const rounded = Math.round(Number(value) * 10) / 10;
    return `${rounded}${suffix}`;
  }

  function pct(value, limit) {
    return limit > 0 ? Math.max(0, Math.min(100, Number(value || 0) / limit * 100)) : 0;
  }

  function stateClass(status) {
    return ['running', 'pending', 'answered', 'queued'].includes(status) ? status :
      ['failed', 'cancelled', 'timeout', 'blocked'].includes(status) ? 'blocked' : 'queued';
  }

  function chip(status, text) {
    const value = String(status ?? 'unknown');
    return `<span class="status-chip ${stateClass(value)}">${esc(text ?? value.toUpperCase())}</span>`;
  }

  function budgetCard(name, value, unit, primary = false) {
    const used = value?.used == null ? null : Number(value.used);
    const reserved = value?.reserved;
    const remaining = Number(value?.remaining || 0);
    const limit = Number(value?.limit || 0);
    return `<div class="budget ${primary ? 'primary' : ''}" data-zero="${limit === 0}" data-used="${used > 0}"><span class="label">${esc(name)}</span>
      <strong>${esc(number(used))} <small>/ ${esc(number(limit))}${esc(unit)}</small></strong>
      <div class="budget-track" role="img" aria-label="Used ${esc(number(used))}, Reserved ${reserved == null ? 'Not tracked' : esc(number(reserved))}, Remaining ${esc(number(remaining))}">
        <span class="used" style="width:${pct(used, limit)}%"></span>
        ${reserved == null ? '' : `<span class="reserved" style="width:${pct(reserved, limit)}%"></span>`}
        <span class="free" style="width:${pct(remaining, limit)}%"></span></div>
      <div class="budget-legend"><span><i></i>Used<b>${esc(number(used))}</b></span><span><i></i>Reserved<b>${reserved == null ? 'Not tracked' : esc(number(reserved))}</b></span><span><i></i>Remaining<b>${esc(number(remaining))}</b></span></div></div>`;
  }

  function budgetHistory(snapshot) {
    const events = snapshot.budget_events || [];
    const reason = value => ({run_terminal:'Run ended', request_closed:'Help request closed'}[value] || 'Reservation released');
    const latest = events.at(-1);
    const summary = latest ? `${reason(latest.payload?.reason)} · Assistance credits released: ${latest.payload?.credits ?? 'Not recorded'}` : 'No reservation release records yet';
    const heading = `<span class="ab-budget-icon" aria-hidden="true">↺</span><span class="ab-budget-copy"><strong>Credit records</strong><small>${esc(summary)}</small></span>`;
    if (!events.length) return `<section class="panel ab-budget-history" data-budget-events><div class="ab-budget-summary">${heading}</div></section>`;
    return `<details class="panel ab-budget-history" data-budget-events data-budget-history data-budget-run="${esc(snapshot.run.run_id)}">
      <summary class="ab-budget-summary">${heading}<span class="ab-budget-count">${events.length} records</span><span class="ab-budget-expand">View details <i aria-hidden="true">⌄</i></span></summary>
      <div class="ab-budget-records"><p class="ab-budget-note">Unused reserved credits return to the available balance. Used credits stay the same.</p>
        ${[...events].reverse().map(event => `<article class="ab-budget-record"><div class="ab-budget-record-head"><strong>Assistance credits released: ${esc(event.payload?.credits)}</strong><span>${esc(reason(event.payload?.reason))}</span></div>
          <dl><dt>Event sequence</dt><dd>${esc(event.sequence)}</dd><dt>Event type</dt><dd><code>${esc(event.event_type)}</code></dd><dt>Reservation ID</dt><dd><code>${esc(event.payload?.reservation_id)}</code></dd><dt>Original reason</dt><dd><code>${esc(event.payload?.reason)}</code></dd></dl></article>`).join('')}
      </div></details>`;
  }

  function diagnosticInline(text) {
    // Escape model output token by token. HTML and links remain plain text;
    // only these fixed presentation tags can be emitted.
    return (String(text).match(/`[^`\n]+`|\*\*[^*\n]+\*\*|[^`*]+|[`*]/g) || []).map(token =>
      token.startsWith('`') && token.length > 1 ? `<code>${esc(token.slice(1, -1))}</code>` :
      token.startsWith('**') && token.endsWith('**') && token.length > 4 ? `<strong>${diagnosticInline(token.slice(2, -2))}</strong>` : esc(token)).join('');
  }

  function diagnosticMarkdown(text) {
    const headings = {};
    const blocks = [], paragraph = [], items = [], code = [];
    let listTag = 'ul', fenced = false;
    const flushParagraph = () => { if (paragraph.length) blocks.push(`<p>${diagnosticInline(paragraph.splice(0).join(' '))}</p>`); };
    const flushList = () => { if (items.length) blocks.push(`<${listTag}>${items.splice(0).map(item => `<li>${diagnosticInline(item)}</li>`).join('')}</${listTag}>`); };
    for (const line of String(text).replaceAll('\r\n', '\n').split('\n')) {
      if (/^\s*```/.test(line)) {
        flushParagraph(); flushList();
        if (fenced) { blocks.push(`<pre><code>${esc(code.splice(0).join('\n'))}</code></pre>`); }
        fenced = !fenced; continue;
      }
      if (fenced) { code.push(line); continue; }
      if (!line.trim()) { flushParagraph(); flushList(); continue; }
      const heading = line.match(/^\*\*([^*]+):\*\*\s*(.*)$/) || line.match(/^#{1,6}\s+(.+)$/);
      if (heading) {
        flushParagraph(); flushList();
        blocks.push(`<h4>${esc(headings[heading[1]] || heading[1])}</h4>`);
        if (heading[2]) paragraph.push(heading[2]);
        continue;
      }
      const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.+)$/);
      if (item) {
        flushParagraph();
        const tag = item[1] ? 'ul' : 'ol';
        if (tag !== listTag) flushList();
        listTag = tag; items.push(item[2]);
      } else { flushList(); paragraph.push(line); }
    }
    flushParagraph(); flushList();
    if (code.length) blocks.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
    return blocks.join('');
  }

  function diagnosticStatus(status) {
    return {native_only:'Native evaluation only', completed:'Diagnostic complete', pending:'Awaiting diagnosis', failed:'Diagnostic failed', evidence_invalid:'Evidence verification failed', unavailable:'No diagnosis available'}[status] || status || 'No diagnosis available';
  }
  function shortStopReason(snapshot) {
    const result = snapshot.eval || {}, native = result.native_success;
    const reason = String(result.stopped_reason || '');
    return reason.startsWith('advisor_error:')
      ? /credential unavailable|credentials? (?:missing|unavailable)/i.test(reason) ? 'Automatic assistance unavailable · Advisor credentials unavailable'
        : /valid Advisor JSON|not valid JSON|invalid JSON/i.test(reason) ? 'Invalid Advisor reply format' : 'Advisor call failed'
      : ({max_attempts:'Maximum attempts reached', whole_case_deadline:'Run timed out', emergency_interrupt:'Ended following an emergency stop request',
          interrupt_too_late:'Execution ended before the stop request arrived', resource_blocked:'Insufficient resources', advisor_token_budget_exhausted:'Insufficient tokens for automatic assistance',
          sdk_call_budget_exhausted:'Action call budget exhausted', human_request_cancelled:'Human cancelled the help request', approval_not_granted:'Request was not approved',
          independent_safety_stop:'Independent safety check requested a stop', independent_safety_interrupt:'Independent safety check requested an interrupt',
          attempt_executor_failed:'Executor did not complete this attempt', formal_attempt_incomplete_or_evaluator_unavailable:'Execution or native outcome evidence incomplete'}[reason]
        || (reason ? reason.split('\n')[0].slice(0,120) : native === true ? 'Task completed' : isTerminal(snapshot) ? 'Stop reason not recorded' : 'Stop reason not recorded yet'));
  }
  function evalPanel(snapshot) {
    if (snapshot.dev_session) {
      const s = snapshot.dev_session, outcomes = Object.entries(snapshot.eval.native_outcomes || {});
      return `<section class="panel ab-eval-panel" data-eval-panel><div class="panel-title"><h3>Dev session result</h3>${chip(s.state,label(s.state))}</div><p>${esc(s.reason||'Iterations continue within the remaining budget.')}</p><div class="ab-eval-overview">${outcomes.map(([id,outcome])=>`<div class="ab-eval-result" data-native="${outcome.native_success?'success':'failure'}"><span class="label">Attempt ${snapshot.attempts.find(a=>a.attempt_id===id).index+1}</span><strong>${outcome.native_success?'Native task successful':'Native task unsuccessful'}</strong><small>Independent native evaluation</small></div>`).join('')||'<p>Native outcome awaiting verification.</p>'}</div><p class="minor">Policy generation and execution are separate from native task success.</p></section>`;
    }
    const result = snapshot.eval || {};
    const native = result.native_success;
    const status = result.status || 'unavailable';
    const reason = String(result.stopped_reason || '');
    const shortReason = shortStopReason(snapshot);
    const attempts = result.attempt_ids || [];
    return `<section class="panel ab-eval-panel" data-eval-panel><div class="panel-title"><h3>Final run outcome</h3>${chip(status === 'evidence_invalid' ? 'failed' : status, diagnosticStatus(status))}</div>
      <div class="ab-eval-overview"><div class="ab-eval-result" data-native="${native === true ? 'success' : native === false ? 'failure' : 'unknown'}"><span class="label">Native Task Outcome</span><strong>${native === true ? 'Task successful' : native === false ? 'Task unsuccessful' : 'Outcome awaiting verification'}</strong><small>${native == null ? 'Awaiting valid native outcome evidence' : 'From verified native task evidence'}</small></div>
        <div class="ab-eval-stop"><span class="label">Stop reason</span><strong>${esc(shortReason)}</strong><small>${attempts.length ? `Full error in the details below` : 'Awaiting execution result artifacts'}</small></div></div>
      <details class="ab-eval-details" data-eval-details data-eval-run="${esc(snapshot.run.run_id)}"><summary>Full Eval diagnosis & verification details <span>Configuration SHA · execution IDs · original errors & diagnosis</span></summary>      <div class="ab-eval-diagnostic"><div class="ab-eval-diagnostic-head"><h4>Diagnosis</h4><span>${esc(result.model || 'Model not recorded')}</span></div>
        <div class="ab-diagnosis-body" data-eval-diagnosis>${result.diagnosis ? diagnosticMarkdown(result.diagnosis) : `<p${result.error ? ' class="ab-eval-error"' : ''}>${esc(result.error || 'Awaiting diagnostic receipt')}</p>`}</div></div>
<dl><dt>Run ID</dt><dd><code>${esc(result.run_id || snapshot.run.run_id)}</code></dd><dt>Stop reason</dt><dd><code>${esc(reason || 'Not recorded')}</code></dd><dt>Configuration SHA</dt><dd><code>${esc(result.approved_config_sha256 || 'Unverified')}</code></dd><dt>Execution IDs</dt><dd>${attempts.map(id => `<code>${esc(id)}</code>`).join('<br>') || 'Awaiting artifacts'}</dd></dl>
        <div class="ab-eval-source"><span class="label">Original diagnosis</span><pre>${esc(result.diagnosis || result.error || 'Awaiting diagnostic receipt')}</pre></div></details>
    </section>`;
  }

  function requestType(request) {
    return {hint:'Help request',approval:'Approval',interrupt:'Interrupt confirmation'}[request.request_type] || 'Request';
  }
  function requestState(request) {
    if (request.state === 'pending' && request.mode === 'live_human_first' && replyExpired(request)) return 'Timed out';
    return {pending:'Awaiting reply',answered:'Answered',fallback:'Handled by system',cancelled:'Cancelled',expired:'Timed out',timed_out:'Timed out'}[request.state] || 'Status unconfirmed';
  }
  function requestAttempt(snapshot, request) {
    const attempt = snapshot.attempts.find(a => a.attempt_id === request.attempt_id);
    return attempt ? `Attempt ${attempt.index + 1}` : 'No associated attempt';
  }
  function requestReason(request, trace) {
    const raw = String(request.reason || trace?.failure?.message || '').trim();
    const reasons = {
      request_after_each_failure:'Previous attempt unsuccessful',
      budget_matched_random_slot:'Guidance requested after this attempt',
      repeated_grounded_failure:'Consecutive attempts unsuccessful',
      cause_unknown_after_inspection:'Failure cause still unclear',
      same_failure_after_code_change:'Same problem after the change',
      unchanged_repeated_failure:'Same problem occurred again',
      repeated_failure_code_unknown:'Same problem occurred again',
      cause_unresolved:'Failure cause unresolved',
      irreversible_action_requires_approval:'Your approval is required',
      trace_safety_interrupt:'Safety check requested an interrupt',
      trace_safety_stop:'Safety check requested a stop',
      independent_safety_stop:'Safety check requested a stop'
    };
    if (Object.hasOwn(reasons,raw)) return reasons[raw];
    if (/^retry_threshold_\d+_reached$/.test(raw)) return 'Retry threshold reached; your guidance is needed';
    // Unknown machine identifiers are kept in evidence details. Never invent
    // a failure or a safety conclusion from an unfamiliar reason code.
    if (!raw || /^[a-z][a-z0-9]*(?:[_:.-][a-z0-9]+)+$/i.test(raw)) return 'Your decision is needed';
    return raw;
  }
  function requestPriority(request) {
    return request.priority === 'critical' ? 'Urgent' : request.priority === 'high' ? 'High priority' : '';
  }
  function requestRow(snapshot, request) {
    const id = String(request.request_id);
    const active = id === selectedRequest;
    const priority = requestPriority(request);
    const hint = request.state === 'pending' ? null : recordedHint(snapshot,request);
    const preview = hint ? Array.from(hint.text).slice(0,80).join('') + (Array.from(hint.text).length > 80 ? '…' : '') : null;
    return `<button class="inboxitem" data-live-request="${esc(id)}" aria-pressed="${active}" type="button">
      <span class="card-top"><span class="request-id">${esc(requestAttempt(snapshot,request))} · ${esc(requestType(request))}</span>${chip(request.state,requestState(request))}</span>
      <strong>${hint ? `<span class="ab-history-hint-source">${esc(hint.title)}</span>${esc(preview)}` : esc(requestReason(request,snapshot.traces[request.trace_id]))}</strong>
      <span class="inbox-meta">${priority ? `<span data-request-priority>${esc(priority)}</span>` : ''}${request.deadline_at == null ? '' : `<span>Deadline ${esc(dateTime(request.deadline_at))}</span>`}</span></button>`;
  }


  function latestExecutionId(snapshot, attempt) {
    // An absent attempt must never match an absent station binding.
    if (!attempt?.attempt_id) return null;
    const execution = snapshot.station.latest_execution;
    if (execution?.attempt_id === attempt.attempt_id) return execution.execution_id;
    return Object.values(snapshot.traces).find(trace => trace?.attempt_id === attempt.attempt_id)?.execution_id;
  }

  function executionViewer(snapshot) {
    const run = snapshot.run;
    const attempts = [...snapshot.attempts].sort((a, b) => a.index - b.index);
    const latest = attempts.at(-1);
    const key = `attention.history:${run.run_id}`;
    const explicitView = stored(key);
    let selected = explicitView || 'live';
    if (selected !== 'live' && !attempts.some(a => a.attempt_id === selected)) selected = 'live';
    const latestRecorded = Object.values(snapshot.traces).find(t => t.attempt_id === latest?.attempt_id && (t.evidence || []).some(e => e.media_url && e.mime_type?.startsWith('video/')));
    const currentReplay = run.suite === 'robocasa365' && selected === 'live' && (latest?.ended_at != null || latest?.execution_complete) && latestRecorded && (!snapshot.station.camera_attempt_id || snapshot.station.camera_attempt_id === latest.attempt_id);
    const attempt = attempts.find(a => a.attempt_id === selected) || (currentReplay || (!DEBUG_VIEW && explicitView !== 'live' && (latest?.ended_at != null || latest?.execution_complete)) ? latest : null);
    const trace = Object.values(snapshot.traces).find(t => t.attempt_id === attempt?.attempt_id);
    const media = (trace?.evidence || []).filter(e => e.media_url);
    const recordingViews=trace?.recording?.views;
    const viewOptions=attempt ? recordingViews : snapshot.station.camera_views;
    const wantedView=cameraChoice(snapshot);
    const chosenView=viewOptions && !viewOptions[wantedView] ? 'external' : wantedView;
    const allVideos=media.filter(e=>e.mime_type?.startsWith('video/'));
    const videos=recordingViews ? allVideos.filter(e=>e.kind===(chosenView==='external'?'advisor_replay':`advisor_replay_${chosenView}`)) : allVideos;
    const viewPicker=viewOptions ? `<div class="ab-camera-views" role="group" aria-label="Camera view">${Object.entries(viewOptions).map(([key,row])=>`<button type="button" data-camera-view="${esc(key)}" aria-pressed="${key===chosenView}">${esc(row.name)}</button>`).join('')}</div><p class="minor" data-camera-name>${esc(viewOptions[chosenView]?.name||'Camera not recorded')} · native ${esc(viewOptions[chosenView]?.source_width||viewOptions[chosenView]?.width||'Unknown')} × ${esc(viewOptions[chosenView]?.source_height||viewOptions[chosenView]?.height||'Unknown')}</p>` : '';

    const images = media.filter(e => !e.mime_type?.startsWith('video/'));
    // Dev v2 live/frame can return terminal 404; it is not saved evidence.
    if (attempt && !snapshot.station.camera_monitor_v2 && !images.length && snapshot.station.camera_attempt_id === attempt.attempt_id && cameraStation(snapshot).camera_url) images.push({kind:'camera_saved', media_url:cameraStation(snapshot).camera_url});
    const pending = actionableRequests(snapshot)[0];
    const active = run.status === 'running' && !pending && snapshot.station.interrupt?.state !== 'stopped' && (latest?.status === 'running' || (snapshot.station.camera_attempt_id && !attempts.some(a => a.attempt_id === snapshot.station.camera_attempt_id)));
    const stationState = stopText(snapshot) || (run.status !== 'running' ? 'Run ended' : pending ? 'Awaiting human reply' : active ? 'Running' : 'Waiting for the next execution');
    const streaming = !attempt && snapshot.station.camera_preview_url && (active || snapshot.station.camera_monitor_v2);
    const cameraLabel = snapshot.station.camera_monitor_terminal ? 'Last frame · non-live' : streaming ? 'PUBLIC RGB · live preview' : active ? 'Public RGB · image preview' : 'Saved frame · not an execution recording';
    const cameraSuite = run.suite === 'robocasa365' ? 'RoboCasa365' : run.suite === 'robocasa' ? 'RoboCasa' : run.suite === 'robosuite' ? 'Robosuite' : 'Simulator';
    const currentViewText = snapshot.station.camera_monitor_terminal ? 'Current view · last frame (non-live). ' : streaming ? 'Current view · public RGB during execution, with actual frame age. '
      : active && cameraStation(snapshot).camera_url ? 'Current view · public RGB preview. Frame age is unavailable. '
      : active ? 'Current view · execution camera. '
      : cameraStation(snapshot).camera_url ? 'Current view · saved static frame. '
      : 'No execution image is available yet. ';
    const recordingHelp = run.suite === 'robosuite'
      ? 'Legacy runs without recordings provide their original trace. New human test runs save camera sample replays.'
      : 'Replays are shown only when a recording is available. Otherwise, review saved images and the action timeline.';
    const latestExecution = latestExecutionId(snapshot, latest);
    const recording = trace?.recording;
    const flipStaticRows = run.suite === 'robocasa365' && recording?.source !== 'official_simulator_display_rgb_after_each_control_step';
    const native = snapshot.eval?.native_outcomes?.[attempt?.attempt_id];
    const nativeText = native?.evaluated === true ? (native.native_success === true ? 'Native task successful' : 'Native task unsuccessful') : 'Native outcome awaiting verification';
    const nextAttempt=attempt&&attempts.find(a=>a.index===attempt.index+1);
    const switchMarkers=(snapshot.attention_control?.segments||[]).filter(s=>attempt&&(s.attempt_id===attempt.attempt_id||(!s.attempt_id&&s.generation>0&&s.effective_at>=attempt.started_at&&(!nextAttempt||s.effective_at<nextAttempt.started_at)))).map(s=>{
      let target=s.trace_seconds!=null?replayTarget({timestamp:s.trace_seconds,timestamp_basis:'relative'},recording,videos.length>0):{seconds:null};
      const timing=recording?.replay_timing;if(timing&&s.trace_seconds!=null){const index=timing.frame_trace_seconds.findIndex(t=>t>=s.trace_seconds);target={...target,seconds:index<0?null:timing.frame_video_seconds[index]+0.000001};}
      const clock=s.trace_seconds!=null?replayTime(s.trace_seconds):'Attention decision · no recorded frame';
      return `<button type="button" data-policy-switch-marker data-policy-marker-seconds="${target.seconds??''}" ${target.seconds==null?'disabled':''}>Attention policy → ${esc(policyTitle(s.policy_id))} · ${esc(clock)}<small>${esc(dateTime(s.effective_at))} · ${esc(s.boundary_kind)}</small></button>`;
    }).join('');
    const eventView = (trace?.events || []).map((e, i) => {
      const target = replayTarget(e, recording, videos.length > 0);
      const timed = e.timestamp_basis === 'relative' && Number.isFinite(e.timestamp) && e.timestamp >= 0;
      const eventStatus = e.event_type === 'skill.started' ? 'started' : e.status;
      const time = timed ? `+${e.timestamp.toFixed(2)}s` : `Time not recorded · #${e.sequence ?? i}`;
      return `<details data-history-event><summary data-replay-event="${i}" ${target.seconds == null ? '' : `data-replay-seconds="${target.seconds}" data-replay-mode="${target.mode}"`} title="${esc(target.seconds == null ? target.reason : `${traceTime(e, i)} · Click to seek${target.mode === 'sample' ? 'sample frame' : 'replay (approximate for legacy recordings)'}`)}"><time>${target.seconds == null ? esc(time) : `<a href="#ab-history-replay" data-replay-link>${esc(time)} <span aria-hidden="true">↗</span></a>`}</time><span class="ab-trace-state" data-state="${esc(eventStatus)}">${esc(eventStatus)}</span><b class="ab-trace-operation">${esc(e.operation || e.event_type)}</b>${e.arguments?.participating_arms?.length?`<small data-event-arms>${esc(e.arguments.participating_arms.map(a=>a[0].toUpperCase()+a.slice(1)).join(" + "))}${e.arguments.coordination_group?` · Group ${esc(e.arguments.coordination_group)}`:""}</small>`:""}</summary><pre>${esc(JSON.stringify(e.arguments || {}, null, 2))}</pre></details>`;
    }).join('');
    return `${DEBUG_VIEW ? `<section class="panel ab-execution-viewer" data-core-region="execution" data-execution-viewer data-replay-attempt="${esc(attempt?.attempt_id || '')}">
      <div class="panel-title"><h3>Execution & History</h3>${chip(active ? 'running' : pending ? 'pending' : run.status, stationState)}</div>
      <nav class="ab-history-tabs" aria-label="Execution view selection"><button type="button" data-history-select="live" aria-pressed="${selected === 'live'}">Current view</button>${attempts.map(a => `<button type="button" data-history-select="${esc(a.attempt_id)}" aria-pressed="${a.attempt_id === selected}">Attempt ${esc(a.index + 1)} · ${esc(attemptLabel(a.status))}</button>`).join('')}</nav>
      ${attempt ? `<div class="ab-history-heading"><strong>${videos.length ? currentReplay ? 'Current attempt replay' : 'History replay' : 'Execution history'} · Attempt ${esc(attempt.index + 1)}</strong><b>${esc(nativeText)}</b>${recording?.truncated ? '<span class="ab-history-failure">Recording limit reached; later frames were not recorded</span>' : ''}</div>` : cameraStation(snapshot).camera_url ? `<p class="minor ab-current-view-caption">${esc(currentViewText)}</p>` : ''}
      ${viewPicker}<div class="ab-viewer-layout"><div class="ab-viewer-media">
      ${attempt ? `${videos.length ? videos.map((e, index) => `<figure><video data-history-replay ${index === 0 ? 'id="ab-history-replay"' : ''} data-replay-key="${esc(JSON.stringify([run.run_id,attempt.attempt_id,trace?.trace_id,...(recordingViews?["synchronized_views"]:[e.evidence_id||null,e.sha256||null,e.media_url])]))}" controls playsinline preload="metadata" src="${esc(e.media_url)}" aria-label="Attempt ${esc(attempt.index + 1)} execution replay"></video><figcaption>Recorded public RGB samples · drag the playhead; check the trace for actions between samples.</figcaption></figure>`).join('') : `<div class="ab-history-empty"><strong>${recordingViews?`No recording for ${esc(recordingViews[chosenView]?.name||chosenView)}`:'No execution video recorded'}</strong><p>${esc(recording?.reason || recording?.error || 'No video can be recovered for this view. Review saved actions and parameters in the timeline.')}</p></div>`}${images.filter(e => !['camera_initial', 'initial_observation', 'camera_final', 'final_observation'].includes(e.kind)).map(e => `<figure><img data-history-image loading="lazy" ${flipStaticRows ? 'style="transform:scaleY(-1)"' : ''} src="${esc(e.media_url)}" alt="Attempt ${esc(attempt.index + 1)} ${esc(e.kind === 'camera_initial' || e.kind === 'initial_observation' ? 'before execution' : e.kind === 'camera_final' || e.kind === 'final_observation' ? 'after execution' : 'evidence') } frame"><figcaption>${esc(e.kind === 'camera_initial' || e.kind === 'initial_observation' ? 'Static frame before execution' : e.kind === 'camera_final' || e.kind === 'final_observation' ? 'Static frame after execution' : e.kind === 'camera_saved' ? 'Saved static frame for this attempt · not a video' : label(e.kind))} · <a href="${esc(e.media_url)}" target="_blank" rel="noopener">Open original image</a><span data-media-resolution></span></figcaption></figure>`).join('')}`
        : cameraStation(snapshot).camera_url ? `<div class="ab-station-camera"><img id="ab-camera-frame" ${cameraStation(snapshot).camera_image_transform === 'flip_y' ? 'style="transform:scaleY(-1)"' : ''} ${streaming ? `hidden data-live-stream-key="${esc(previewKey(snapshot))}" data-preview-url="${esc(cameraStation(snapshot).camera_preview_url)}"` : `src="${esc(cameraStation(snapshot).camera_url)}"`} alt="${esc(cameraSuite)} public RGB camera frame"><span>${esc(cameraLabel)}</span></div><div class="ab-camera-caption"><span data-camera-resolution>Reading source resolution…</span>${streaming ? '<span data-preview-freshness role="status" title="Frame age starts when the executor receives public RGB and conservatively includes this download. It does not measure camera exposure time.">Connecting to live preview…</span>' : active ? '<span data-preview-freshness data-state="unavailable">Frame age is unavailable for this camera preview.</span>' : ''}<a href="${esc(cameraStation(snapshot).camera_url)}" target="_blank" rel="noopener">Open original image</a></div>` : `<div class="ab-camera-empty" data-camera-empty data-state="${active ? 'waiting' : 'unavailable'}" role="status" aria-live="polite"><span class="ab-camera-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5.5 9.5 3.5h5L16 5.5h3a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z"/><circle cx="12" cy="12.5" r="4"/></svg></span><span class="ab-camera-empty-label">Execution camera</span><strong>${active ? snapshot.dev_session ? 'Execution in progress' : 'Waiting for the execution camera' : 'No camera feed connected'}</strong><p>${esc(snapshot.dev_session ? snapshot.station.preview_state?.error || (active ? 'Simulator initializing. The live preview will appear when the first real camera frame arrives.' : snapshot.dev_session.state==='needs_review' ? 'Execution could not start or requires review. Open the attempt execution details for the error.' : 'No camera frame was recorded for this attempt. Open execution details for the result.') : active ? 'The live preview will appear when the first frame arrives.' : 'No camera image has been published for this run.')}</p>${active && !snapshot.dev_session ? '<span class="ab-camera-empty-state"><i aria-hidden="true"></i>Waiting for frames</span>' : ''}</div>`}
      </div><aside class="ab-viewer-trace">${attempt ? `<div class="ab-timeline-head"><h4>Action timeline</h4><span class="ab-timing-badge">${videos.length ? recording?.replay_timing ? 'Synchronized samples' : 'Approximate position' : 'No replay'}</span></div><p class="ab-timeline-help">${videos.length ? 'Click a time or action to seek the replay' : 'No replay recorded; expand actions to view parameters'}</p><p class="ab-replay-status" data-replay-status role="status" aria-live="polite">${videos.length ? 'SDK call completion times · authorized trace' : 'Authorized trace'}</p><div class="ab-history-events">${switchMarkers}${eventView || '<p class="minor">No persisted action records yet.</p>'}</div><details class="ab-trace-meta"><summary>Execution details · ${esc(attemptLabel(attempt.status))}</summary><p class="minor ab-history-identity">${esc(trace?.execution_id || 'Execution ID not recorded yet')}</p>${executionFailure(trace)?.message ? `<p class="ab-history-failure">${esc(executionFailure(trace).message)}</p>` : ''}</details>` : `<div class="ab-timeline-head"><h4>Execution history</h4></div><p class="ab-history-intro">Select an attempt above to review its replay, actions and outcome.</p><p class="ab-history-note">Your selection is retained across refreshes.</p><p class="ab-history-recording-note">${esc(recordingHelp)}</p>`}</aside></div>
      <details class="ab-viewer-details" data-persist-detail="execution"><summary>Execution & recording details</summary><div class="station-tile"><span>LATEST EXECUTION</span><b>${esc(latestExecution || 'Current attempt execution ID not recorded')}</b></div>${attempt ? `<p>${esc(dateTime(attempt.started_at))} → ${attempt.ended_at == null ? 'Not ended yet' : esc(dateTime(attempt.ended_at))}</p>` : ''}${recording?.control_interface_status ? `<p data-execution-status>Control interface: ${esc(recording.control_interface_status)} · Execution: ${esc(recording.execution_status || 'Not recorded')} · Recording: ${esc(recording.status)} · native_success: ${esc(native?.evaluated === true ? String(native.native_success) : 'not evaluated')}</p>` : ''}${recording ? `<p data-recording-summary>Recording ${esc(recording.status || (recording.video_saved ? 'saved' : 'not available'))} · ${esc(recording.reason || '')} · Source ${esc(recording.source_width || 'Not recorded')}×${esc(recording.source_height || 'Not recorded')} · actual capture ${esc(recording.actual_capture_fps==null?"Not recorded":number(recording.actual_capture_fps))} FPS · camera samples ${esc(recording.frame_count ?? 'Unknown')} frames / ${esc(number(recording.sampled_seconds))} s${recording.truncated ? ' · Recording limit reached; later frames were not recorded' : ''}${recording.error ? ` · ${esc(recording.error)}` : ''}</p>` : ''}</details>
      ${snapshot.dev_session?operatorAIReview(snapshot.dev_session.versions.find(v=>v.version===(attempt?.dev_version || snapshot.dev_session.versions.at(-1)?.version))?.evaluator_review,attempt?.dev_version,true):''}</section>` : operatorExecution(snapshot,{attempt,selected,attempts,trace,recording,videos,images,recordingViews,viewOptions,chosenView,streaming,active,flipStaticRows,cameraSuite,cameraLabel})}`;
  }

  let guidancePreviewTimer = null, guidancePreviewController = null, guidancePreviewGeneration = 0;
  let activeGuidancePreview = null;
  const guidancePreviewCache = new Map();
  function stopGuidancePreview() {
    clearTimeout(guidancePreviewTimer);
    guidancePreviewController?.abort(); guidancePreviewController = null;
    guidancePreviewGeneration++;
  }
  function bindGuidancePreview(snapshot, request, input) {
    const status = view.querySelector('[data-guidance-preview]');
    if (!status) return;
    const show = result => {
      const parameters = Object.entries(result.parameters || {}).map(([key,value]) => `${key}=${value}`).join('; ');
      status.textContent = result.status === 'unsupported'
        ? 'Unsupported controls for OpenArm. The reply remains public text; check the subsequent execution.'
        : parameters
        ? `Recognized parameters: ${parameters}. Format only; this does not confirm action changes.`
        : 'No parameter rule matched. You can still submit guidance; it may not change actions.';
    };
    const schedule = () => {
      stopGuidancePreview();
      const content = input.value.trim(), generation = guidancePreviewGeneration;
      if (root.dataset.connection === 'disconnected') { status.textContent = 'Format check unavailable · Disconnected.'; return; }
      if (!content) { status.textContent = 'Enter guidance to check supported formats.'; return; }
      const scope=snapshot.run.robot?.execution_arm==='both'?{arm:view.querySelector('[data-hint-arm]')?.value,phase:view.querySelector('[data-hint-phase]')?.value,attempt_id:request.attempt_id}:null;
      const key = JSON.stringify([snapshot.run.run_id, request.request_id, content,scope]);
      if (guidancePreviewCache.has(key)) { show(guidancePreviewCache.get(key)); return; }
      status.textContent = 'Checking guidance format…';
      const current = () => generation === guidancePreviewGeneration && input.isConnected &&
        status.isConnected && input.value.trim() === content && root.dataset.connection !== 'disconnected' &&
        currentSnapshot?.run.run_id === snapshot.run.run_id &&
        actionableRequests(currentSnapshot).some(item => item.request_id === request.request_id);
      guidancePreviewTimer = setTimeout(async () => {
        const controller = new AbortController(); guidancePreviewController = controller;
        try {
          if (!current()) return;
          const response = await controlFetch(`/api/requests/${encodeURIComponent(request.request_id)}/guidance-preview`, {
            method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(scope?{content,guidance_scope:scope}:{content}), signal:controller.signal,
          });
          const result = await response.json();
          if (!current()) return;
          if (!response.ok || result.run_id !== snapshot.run.run_id || result.request_id !== request.request_id ||
              !['supported','translated','unrecognized','unsupported'].includes(result.status)) throw new Error('Format check unavailable');
          if (guidancePreviewCache.size >= 64) guidancePreviewCache.delete(guidancePreviewCache.keys().next().value);
          guidancePreviewCache.set(key,result); show(result);
        } catch (_) {
          if (current()) status.textContent = 'Format check unavailable. You can still submit guidance.';
        } finally {
          if (guidancePreviewController === controller) guidancePreviewController = null;
        }
      },250);
    };
    activeGuidancePreview = {schedule};
    input.addEventListener('input',schedule);
    view.querySelectorAll('[data-hint-arm],[data-hint-phase]').forEach(select=>select.addEventListener('change',schedule));
    schedule();
  }

  function hintFormats(snapshot, request) {
    const cube = snapshot.run.suite === 'robosuite' && snapshot.run.task_id === 'cube_lift';
    const openarm = snapshot.run.suite === 'robocasa' && snapshot.run.robot?.model === 'OpenArm';
    const supportedSuite = ['robosuite', 'robocasa'].includes(snapshot.run.suite);
    const limits = snapshot.run.robot?.execution_arm==='both' ? 'Left hand / Pickup: grasp_offset_m 0–0.020. Right hand / Handoff: receiving_offset_m 0.025–0.035. Other hand and phase combinations have no parameter rule.' : openarm ? 'grasp_offset_m: 0–0.020; base motion and settling overrides are unavailable'
      : snapshot.run.suite === 'robosuite'
      ? 'grasp_offset_m: 0–0.04; approach_tolerance_m: 0.003–0.020; close_settle_steps: integer 10–40'
      : 'grasp_offset_m: 0.030–0.050; base_forward_m: 0.090–0.125; open_settle_steps: integer 10–30; close_settle_steps: integer 10–40';
    const control = openarm ? 'AB_CONTROL_V1 grasp_offset_m=0.005;'
      : snapshot.run.suite === 'robosuite'
      ? 'AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;close_settle_steps=30;'
      : 'AB_CONTROL_V1 grasp_offset_m=0.045;open_settle_steps=20;close_settle_steps=30;';
    return `<aside class="ab-hint-format" data-hint-format><p id="ab-hint-format">Free-text guidance may not change actions. Check the subsequent execution.</p>
      <p id="ab-guidance-preview" data-guidance-preview role="status" aria-live="polite">Enter guidance to check supported formats.</p>
      ${cube ? '<button type="button" data-hint-preset="en" data-preset-text="Check the grasp height and lower it slightly. Close the gripper and confirm a secure grip before lifting.">Insert example hint</button>' : ''}
      <details class="ab-hint-advanced" data-hint-advanced data-persist-detail="hint-format:${esc(request.request_id)}"><summary>Advanced parameters</summary>
        ${cube ? '<p>This example hint is supported for Lift the cube. Other free text may not change actions.</p>' : '<p>No verified example hint for this task.</p>'}
        ${supportedSuite ? `<button type="button" data-hint-preset="control" data-preset-text="${esc(control)}">Insert parameter example</button><code>${esc(control)}</code><p>Use one marker, no duplicate parameters, and an ASCII semicolon after each value. Ranges: ${esc(limits)}.</p>` : '<p>No evidence of parameter-format support for this execution target.</p>'}
        <p>Examples only fill the draft. Check subsequent execution to confirm any effect on actions.</p></details></aside>`;
  }
  function traceDetail(snapshot, request, active = true, showHint = true) {
    const trace = snapshot.traces[request.trace_id];
    const lifecycle = snapshot.request_lifecycle?.[request.request_id] || [];
    const deferred = lifecycle.some(item => item.event_type === 'request.deferred');
    const canRespond = active && actionableRequests(snapshot).some(r => r.request_id === request.request_id);
    const actions = canRespond ? request.request_type === 'hint'
      ? `<div class="ab-response-form"><label for="ab-hint-content">Your guidance</label><textarea id="ab-hint-content" aria-describedby="ab-hint-format ab-guidance-preview" maxlength="4000" placeholder="Describe the problem you observed and the changes you suggest"></textarea>${hintFormats(snapshot,request)}${snapshot.run.robot?.execution_arm==='both'?`<div data-hint-scope><label>Target hand<select data-hint-arm aria-label="Hint target hand"><option value="left">Left hand</option><option value="right">Right hand</option><option value="both">Both hands</option></select></label><label>Task phase<select data-hint-phase aria-label="Hint task phase"><option value="pickup">Pickup</option><option value="handoff">Handoff</option><option value="placement">Placement</option></select></label><p>Guidance applies to this hand and phase in the next attempt.</p></div>`:''}<div class="ab-response-actions"><button class="ab-button-primary" type="button" data-respond="hint">Submit guidance</button><button type="button" data-defer-request ${deferred ? 'disabled' : ''}>${deferred ? 'Deferred' : 'Handle later'}</button><button type="button" data-cancel-request>Cancel request</button></div></div>`
      : request.request_type === 'approval'
        ? `<div class="ab-response-form"><div><button class="ab-button-primary" type="button" data-respond="approve">Approve</button><button type="button" data-respond="deny">Deny</button><button type="button" data-defer-request ${deferred ? 'disabled' : ''}>${deferred ? 'Deferred' : 'Handle later'}</button><button type="button" data-cancel-request>Cancel request</button></div></div>`
        : '<div class="ab-response-form"><button type="button" data-respond="interrupt">Confirm interrupt request</button></div>' : '';
    const sameTimeline = stored(`attention.history:${snapshot.run.run_id}`) === trace?.attempt_id;
    const events = (trace?.events || []).slice(-3);
    const offset = (trace?.events?.length || 0) - events.length;
    const videos = (trace?.evidence || []).some(e => e.media_url && e.mime_type?.startsWith('video/'));
    const association=snapshot.response_evidence?.[request.request_id]?.association;
    const related = snapshot.attempts.filter(a => a.attempt_id === request.attempt_id ||
      association?.status==='linked'&&a.attempt_id===association.attempt_id);
    const hypothesis = String(trace?.hypothesis || '').trim();
    const hasHypothesis = hypothesis && !/^(unknown|unavailable|none|未知(?:[（(].*)?|未提供|未记录)$/i.test(hypothesis);
    const question = request.request_type === 'approval' ? 'Review the evidence, then approve or deny.'
      : request.request_type === 'interrupt' ? 'Review the execution evidence and confirm whether to interrupt.' : 'What changes do you suggest for the next attempt?';
    const priority = requestPriority(request);
    const technical = `<details class="ab-request-technical" data-request-technical data-persist-detail="request-evidence:${esc(request.request_id)}"><summary>Evidence details</summary>
      <dl><dt>Original request reason</dt><dd>${esc(request.reason || trace?.failure?.message || 'Not recorded')}</dd><dt>Request ID</dt><dd>${esc(request.request_id)}</dd><dt>Attempt ID</dt><dd>${esc(request.attempt_id)}</dd><dt>Original priority</dt><dd>${esc(request.priority || 'Not recorded')}</dd></dl>
      <div class="trace">${sameTimeline ? '<p>The full action timeline is open in Execution & History.</p>' : events.map((event, i) => {const index = offset + i; const target = replayTarget(event, trace?.recording, videos); const text = `${esc(traceTime(event, index))} · ${esc(event.operation || event.event_type)} · ${esc(event.status)}`; return `<div>${target.seconds == null ? text : `<a href="#ab-history-replay" data-request-replay="${index}" data-replay-attempt="${esc(trace.attempt_id)}" title="View the replay linked to this attempt">${text}</a>`}</div>`;}).join('') || `<p>${esc(trace?.failure?.message || 'No visible actions recorded')}</p>`}</div>
      ${trace?.memory_refs?.length ? `<p>Related memory: ${trace.memory_refs.map(esc).join(' · ')}</p>` : ''}</details>`;
    const historicalHint = !active && recordedHint(snapshot,request);
    const hintSummary = historicalHint && `<div class="request-card ab-submitted-hint" data-request-hint-summary data-hint-source="${historicalHint.source}"><header><strong>${esc(historicalHint.title)}</strong><span>${esc(requestAttempt(snapshot,request))} · Help request · ${esc(requestState(request))}</span></header><blockquote>${esc(historicalHint.preview)}</blockquote></div>`;
    const summary = historicalHint ? showHint ? submittedHint(snapshot,request,true) : hintSummary : `<div class="request-card"><span class="label">${esc(requestAttempt(snapshot,request))} · ${esc(requestType(request))}${active ? '' : ` · ${esc(requestState(request))}`}</span>${priority ? `<span class="ab-request-priority" data-request-priority>${esc(priority)}</span>` : ''}<h4 data-request-reason>${esc(requestReason(request,trace))}</h4>${active ? `<p class="ab-request-question">${question}</p>` : ''}${hasHypothesis ? `<p class="ab-request-hypothesis" data-request-hypothesis>Current hypothesis: ${esc(hypothesis)}</p>` : ''}${active ? `<p class="ab-countdown" data-reply-countdown data-deadline="${esc(request.deadline_at ?? '')}" data-expired="${replyExpired(request)}" role="status">${esc(countdown(request))}</p>` : ''}</div>`;
    return `<div class="ab-request-context">${summary}
      <div class="evidence-card"><span class="label">Execution evidence</span><div class="ab-request-evidence-links">${related.map(a => { const hasReplay = Object.values(snapshot.traces).some(t => t.attempt_id === a.attempt_id && (t.evidence || []).some(e => e.media_url && e.mime_type?.startsWith('video/'))); return `<button type="button" data-history-select="${esc(a.attempt_id)}">View attempt ${esc(a.index + 1)}${hasReplay ? ' replay' : ' actions'}</button>`; }).join('')}</div>${!videos ? `<p class="ab-request-gap">${events.length ? 'No video recorded. Expand the evidence to view recorded actions.' : 'No visible actions recorded; the failure location is unknown.'}</p>` : ''}${technical}</div></div>${active || historicalHint || !showHint ? '' : submittedHint(snapshot,request)}${actions}`;
  }
  function feedbackShortcut(snapshot) {
    const node=flowModel(snapshot).nodes.findLast(n=>n.kind==='response');
    if(!node)return '';
    const request=snapshot.requests.find(r=>r.request_id===node.requestId),response=snapshot.responses[request?.response_id];
    return `<button type="button" data-open-flow-node="${esc(node.key)}">${response?.responder==='advisor_proxy'?'System reply':'Reply received'} · View feedback</button>`;
  }
  function recordedHint(snapshot, request) {
    if (!request || request.request_type !== 'hint') return null;
    const response = snapshot.responses[request.response_id];
    const raw = typeof response?.content === 'string' ? response.content.trim() : '';
    if (!raw) return null;
    const source = response.responder === 'human' ? 'human' : response.responder === 'advisor_proxy' ? 'system' : 'unknown';
    const title = {human:'Your guidance',system:'System guidance',unknown:'Guidance · source unconfirmed'}[source];
    let text = raw, structured = false;
    if (source !== 'human') {
      try {
        const value = JSON.parse(raw);
        if (value && typeof value === 'object') {
          structured = true;
          text = ['guidance','diagnosis','caution'].map(key => value[key]).find(part => typeof part === 'string' && part.trim()) || 'Guidance recorded; expand to view the content.';
        }
      } catch (_) { /* Plain reply text is escaped by the caller. */ }
    }
    const characters = Array.from(text), long = characters.length > 140;
    return {response,source,title,text,structured,long,preview:long ? characters.slice(0,140).join('') + '…' : text};
  }
  function submittedHint(snapshot, request, historical = false) {
    if (!request || request.request_type !== 'hint') return '';
    // Drafts and other requests never supply missing recorded hint content.
    const hint = recordedHint(snapshot,request);
    if (!hint) {
      const hasReceipt = submissionReceipts.has(`${snapshot.run.run_id}:${request.request_id}`);
      return request.state === 'answered' || request.state === 'fallback' || request.response_id || hasReceipt
        ? '<p class="ab-hint-content-gap" data-hint-content-gap>No guidance content available</p>' : '';
    }
    return `<article class="ab-submitted-hint" data-request-reply="${esc(request.request_id)}" data-hint-source="${hint.source}"><header><strong>${hint.title}</strong><span>${esc(requestAttempt(snapshot,request))} · Help request${historical ? ` · ${esc(requestState(request))}` : ''}</span></header><blockquote data-hint-preview>${esc(hint.preview)}</blockquote>${hint.long || hint.structured ? `<details data-hint-full data-persist-detail="hint-content:${esc(request.request_id)}"><summary>View full guidance</summary><div data-hint-body>${readableReply(hint.response)}</div></details>` : ''}</article>`;
  }
  function idleGuidance(snapshot) {
    if(snapshot.dev_v2){const advisor=snapshot.dev_v2.jobs.find(j=>j.kind==='advisor'&&j.state==='claimed');return isTerminal(snapshot)?'This session has ended. Recorded assistance is available in Request history.':advisor?`Automatic Advisor is preparing guidance for v${advisor.version}. No human reply is requested.`:'Automatic Advisor assistance follows the selected policy. No human reply is requested.';}
    if (isTerminal(snapshot)) return 'This run has ended. Use New run to start another human-first test.';
    if (snapshot.station.interrupt) return stopText(snapshot) || 'Stopping execution; replies are closed.';
    if (snapshot.run.assistance_mode !== 'live_human_first') return 'Automatic Advisor assistance is enabled. Human replies are disabled for this run.';
    if (snapshot.requests.some(r => r.state === 'pending' && replyExpired(r))) return 'The reply deadline passed. Awaiting runtime handling; this request is closed.';
    if (snapshot.resources.assistance?.limit === 0) return 'Assistance budget is 0. Set a positive budget in New run to enable help requests.';
    if (snapshot.resources.assistance?.remaining <= 0) return 'No assistance credits remain. Use New run with a positive assistance budget.';
    if (feedbackRequest(snapshot)) return 'Waiting for the next help request.';
    return 'Guidance opens when the runtime sends a help request. Watch the execution while you wait.';
  }
  const devGuidancePending=new Set();
  function devGuidancePanel(snapshot){
    const s=snapshot.dev_session,c=snapshot.attention_control,hint=c?.hint;
    const waiting=hint?.state==='pending'&&!c.read_only;
    const key=hint?`${s.session_id}:${hint.request_id}`:'';
    return `${DEBUG_VIEW ? `<section class="panel detail ab-current-request" data-core-region="request" data-state="${waiting?'waiting':'idle'}" aria-label="Current Request & Reply"><div class="panel-title"><h3>Current Request & Reply</h3>${waiting?'<span>Guidance requested</span>':''}</div>
      ${waiting?`<p class="lead">Dev is waiting for your guidance before version ${esc(hint.version)}.</p><p>${esc(label(hint.reason))}</p><p><strong>Public execution feedback:</strong> ${esc(hint.feedback?.public_error||'No specific error was recorded.')}</p><details data-persist-detail="dev-guidance-trace:${esc(hint.request_id)}"><summary>Recorded actions for this request</summary><pre>${esc(JSON.stringify(hint.feedback?.action_trace||[],null,2))}</pre></details><form data-dev-guidance data-request-id="${esc(hint.request_id)}"><label>Guidance for the next attempt<textarea name="content" maxlength="2000" required placeholder="Describe a correction to the next attempt.">${esc(stored('attention.dev-guidance:'+key)||'')}</textarea></label><div class="ab-run-actions"><button type="submit" value="answer" ${devGuidancePending.has(key)?'disabled':''}>Send guidance & continue</button><button type="submit" value="skip" formnovalidate ${devGuidancePending.has(key)?'disabled':''}>Continue without guidance</button></div><p role="status" data-dev-guidance-message></p></form><p class="minor">Waiting counts towards the session time limit. You can change Attention policy or stop the session.</p>`:`<p class="ab-idle-request">No human reply required</p><p>${s.state==='needs_review'?'Execution outcome requires review. Automatic resumption is disabled.':c?.binding?`Attention: ${esc(policyTitle(c.active_policy))}. Guidance opens when the selected policy requests help after a public execution or code-validation failure.`:'Attention was not recorded for this older Dev session.'}</p>${hint?.content?`<blockquote data-dev-submitted-guidance>${esc(hint.content)}</blockquote>`:''}`}
      ${c?.binding?`<small>Help requests: ${esc(c.hint_count||0)} / ${esc(c.hint_limit)} · Native task outcomes stay in execution history.</small>`:''}</section>` : operatorHelp(snapshot,null)}`;
  }
  function plannerPanel(snapshot) {
    const p=snapshot.dev_v2?.planner;
    if(!p)return '';
    const end=devEndSummary(snapshot.dev_session||{});
    const closed=['completed','failed','stopped','cancelled','needs_review','budget_exhausted'].includes(snapshot.dev_session?.state);
    const rows=p.requests.map(r=>{
      const scope=r.binding.scope||{},v=snapshot.dev_v2.versions.find(v=>v.version===r.version);
      return `<article><h4>${r.request_type==='approval'?'Human action approval':'Advisor guidance'} · ${esc(r.state)} · version ${r.version}</h4>
        <p>${r.request_type==='approval'?`Allow this bounded simulation plan for ${esc(taskName(scope.task_id))}, seed ${esc(scope.seed)}: ${esc((scope.operations||[]).map(op=>op.replaceAll('_',' ').replace('.',' · ')).join(', '))}. Up to ${scope.sdk_calls} SDK calls, ${scope.control_steps} control steps and ${scope.wall_seconds}s. ${esc(r.binding.risk)}`:'Advice is recorded separately from permission to move.'}</p>
        <p>Deadline: ${esc(new Date(r.deadline_epoch*1000).toLocaleString())} · ${r.request_type==='approval'?`Action permission: ${r.state==='consumed'?`Consumed once by ${esc(r.consumed_by)}`:r.state==='approved'?'Approved, not consumed':r.state==='waiting'?'Awaiting human decision; not consumed':`Closed (${esc(r.state)}); not consumed`}`:`Guidance adoption: ${r.adopted===true?'Included in generation input':r.adopted===false?'Not adopted':['waiting','answered'].includes(r.state)?closed?'No generation association recorded (run ended)':'Pending generation association':'Not adopted (request closed)'}`}</p>
        ${r.request_type==='approval'&&r.state==='invalidated'&&r.terminal_reason==='authorization_exhausted'?'<p>Action permission closed · Original authorization or budget exhausted. This action was not executed.</p>':''}
        ${r.request_type==='approval'?`<p>Waiting owner: ${esc(snapshot.dev_v2.lifecycle?.waiting_owner?.pid??'No live owner')}${snapshot.dev_v2.lifecycle?.recovery_available?' · Continue experiment to resume the original wait; its deadline is unchanged.':''}</p>`:''}
        ${r.request_type==='approval'&&r.state==='waiting'?`<label>Independent human capability<input type="password" autocomplete="off" data-planner-capability="${esc(r.request_id)}"></label><button data-planner-decision="${esc(r.request_id)}" data-approved="true">Approve this action</button><button data-planner-decision="${esc(r.request_id)}" data-approved="false">Reject this action</button>`:''}
        <details data-persist-detail="planner-${esc(r.request_id)}"><summary>Source and exact permission binding</summary><pre>${esc(v?.source_text||'Source unavailable')}</pre><pre>${esc(JSON.stringify(r,null,2))}</pre></details></article>`;
    }).join('');
    return `<div data-planner-panel data-planner-details><h4>Action permissions and work while waiting</h4>${end.review?'<p>Review required · Unresolved work and any unknown paid holds remain recorded.</p>':''}${rows||'<p>No action approval requested yet.</p>'}
      ${p.work.map(w=>`<p>${esc(w.kind)} preparation: ${esc(w.state)} · ${w.actual_seconds!=null?`${esc(w.actual_seconds)}s`:closed?'Not settled · Review required':'pending'}. ${esc(w.dependency)} ${w.result?`<a href="${esc(devFile(snapshot.dev_session,w.result.uri))}" target="_blank" rel="noopener">Read actual work</a>`:''}</p>`).join('')}
      <details data-persist-detail="planner-memory"><summary>Memory sources, exact grants and generation/result links</summary><pre>${esc(JSON.stringify(p.memory,null,2))}</pre></details></div>`;
  }
  function bindPlanner(snapshot) {
    view.querySelectorAll('[data-planner-decision]').forEach(b=>b.addEventListener('click',async()=>{
      const r=snapshot.dev_v2.planner.requests.find(r=>r.request_id===b.dataset.plannerDecision);
      const input=[...view.querySelectorAll('[data-planner-capability]')].find(i=>i.dataset.plannerCapability===r.request_id);
      b.disabled=true;
      try {
        const response=await controlFetch(`/api/dev-v2/experiments/${snapshot.dev_v2.experiment_id}/approvals/${r.request_id}/decision`,{method:'POST',headers:{'Content-Type':'application/json','X-Attention-Human-Token':input.value},body:JSON.stringify({binding:r.binding,approved:b.dataset.approved==='true',response_id:crypto.randomUUID()})});
        input.value='';const result=await response.json();if(!response.ok)throw Error(result.error||response.status);
        lastRendered='';await refresh();
      } catch(error) {view.querySelector('#ab-live-message').textContent=error.message;b.disabled=false;}
    }));
  }
  // Attention mechanism presentation: server permissions and persisted records.
  let memoryCatalogue = null, memorySelected = null, memoryMessage = '';
  // The server decides what may still be done with a request (permitted_actions). A server or
  // record that predates the field keeps the controls it always had; actions added later, such as
  // ignoring a request, are only ever shown when the server says they exist.
  const LEGACY_REQUEST_ACTIONS = new Set(['respond_hint','approve_or_deny','confirm_interrupt','defer','cancel']);
  function requestAllowed(request, action) {
    if (!request) return false;
    if (!Array.isArray(request.permitted_actions)) return LEGACY_REQUEST_ACTIONS.has(action);
    return request.permitted_actions.includes(action);
  }
  function requestMechanismDetails(snapshot, chosen) {
    const rows = snapshot.request_timeline?.requests || [];
    return `<div data-attention-requests>${(snapshot.requests || []).map(request => {
      const explanation=request.explanation || {}, timeline=rows.find(row=>row.request_id===request.request_id);
      const disposition=snapshot.request_dispositions?.[request.request_id]?.disposition || timeline?.disposition;
      const memories=(snapshot.memory?.uses || []).filter(item=>item.attempt_id===request.attempt_id);
      const active=chosen?.request_id===request.request_id;
      return `<details data-persist-detail="mechanism:${esc(request.request_id)}"><summary>${esc(requestType(request))} · ${esc(request.state)}${disposition?` · Reply ${esc(disposition)}`:''}</summary>
        <p>${esc(explanation.why || request.reason || 'Reason not recorded')}</p>
        <p>Priority: ${esc(request.priority || 'Not recorded')} · Deadline: ${request.deadline_at==null?'None':esc(dateTime(request.deadline_at))}</p>
        ${explanation.assistance_requested?`<p>${esc(explanation.assistance_requested)}</p>`:''}${explanation.while_waiting?`<p>${esc(explanation.while_waiting)}</p>`:''}
        ${explanation.agent_hypothesis?`<p>Hypothesis: ${esc(explanation.agent_hypothesis)}</p>`:''}
        ${memories.map(item=>{const record=snapshot.memory?.records?.[item.memory_id]?.record;return `<article data-request-memory><b>${esc(item.memory_id)} @ ${esc(item.memory_version ?? 'unrecorded version')}</b><p>${esc(record?.guidance || 'Memory content unavailable')}</p></article>`;}).join('')}
        ${timeline?`<p>Response latency: ${timeline.response_latency_seconds==null?'No reply recorded':`${esc(number(timeline.response_latency_seconds))} s`}</p><ol>${timeline.transitions.map(item=>`<li>${esc(item.state)} · ${item.at==null?'Time unrecorded':esc(dateTime(item.at))}</li>`).join('')}</ol>${timeline.used_response?`<p>Reply used: ${esc(timeline.used_response.content)}</p>`:''}${timeline.execution_links.map(item=>`<p>Guided execution: ${esc(item.execution_id)}</p>`).join('')}${timeline.candidate_memories.map(item=>`<p>Candidate: ${esc(item.memory_id)}</p>`).join('')}`:''}
        ${active?`<div class="op-controls">${requestAllowed(request,'defer')?'<button type="button" data-defer-request>Handle later</button>':''}${requestAllowed(request,'ignore')?'<button type="button" data-ignore-request>Ignore · use Advisor fallback</button>':''}${requestAllowed(request,'cancel')?'<button type="button" data-cancel-request>Cancel request</button>':''}</div>`:''}</details>`;
    }).join('')}</div>`;
  }
  function memoryPanel(snapshot) {
    const memory=snapshot.memory;
    if (!memory) return '';
    const records=memoryCatalogue?.memories || Object.values(memory.records || {}).map(item=>({...item.record,memory_id:item.memory_id}));
    const canOperate=memoryCatalogue?.control?.can_operate ?? memory.control?.can_operate;
    const detail=memorySelected || null, record=detail?.record;
    return `<details data-memory-panel data-persist-detail="memory-panel"><summary>Memory · candidates & validation queue</summary>
      <p>${memory.control?'Reads the same Memory records as the agent.':'Memory service is not configured.'}</p>
      ${(memory.ingestion || []).map(item=>`<p data-memory-ingestion>${esc(item.memory_id || item.request_id)} · ${esc(item.decision)}${item.reason?` · ${esc(item.reason)}`:''}</p>`).join('')}
      ${memory.control?'<button type="button" data-memory-refresh>Refresh Memory catalogue</button>':''}
      <p data-memory-message role="status">${esc(memoryMessage)}</p>
      ${['Candidates / validation','Other memories'].map((group,index)=>`<h4>${group}</h4><ul>${records.filter(item=>['candidate','validated'].includes(item.status)===(index===0)).map(item=>`<li><button type="button" data-memory-open="${esc(item.memory_id)}">${esc(item.memory_id)} @ ${esc(item.version ?? '?')} · ${esc(item.status || 'Unknown')}</button></li>`).join('') || '<li>No records loaded</li>'}</ul>`).join('')}
      ${detail?`<article data-memory-detail><h4>${esc(detail.memory_id)} @ ${esc(record?.version ?? '?')} · ${esc(record?.status || 'Unavailable')}</h4><p>${esc(record?.guidance || detail.error || 'Content unavailable')}</p><details data-persist-detail="memory-validation:${esc(detail.memory_id)}"><summary>Validation, lifecycle and uses</summary><pre>${esc(JSON.stringify({validation:detail.validation,history:detail.history,uses:detail.uses},null,2))}</pre></details>
        ${canOperate && record?`<form data-memory-operation data-memory-id="${esc(detail.memory_id)}"><label>Operator<input name="actor" maxlength="200" required></label><label>Reason<input name="reason" maxlength="200" required></label><label>Operation<select name="action"><option value="disable">Disable</option><option value="enable">Enable</option><option value="rollback">Roll back permanently</option><option value="expiry">Set expiry</option></select></label><label>Expiry (Unix seconds, only for set expiry)<input name="expires_at" type="number" min="0" step="any"></label><button type="submit">Apply lifecycle operation</button></form>`:''}</article>`:''}</details>`;
  }
  function attentionRunContext(snapshot) {
    const lock=snapshot.lock, work=snapshot.autonomous_work;
    const workState=work?.state || work?.status || (work?.blocked?.length?'blocked':work?.continuing_while_waiting?.length?'continuing while waiting':work?.running?.length?'running':'idle');
    return `${lock?`<details data-run-lock data-persist-detail="run-lock"><summary>Locked run conditions</summary><p>Policy switchable: ${esc(String(lock.policy_switchable))} · Ranking eligible: ${esc(String(lock.ranking_eligible))}</p><pre>${esc(JSON.stringify(lock.conditions || lock,null,2))}</pre></details>`:''}${work?`<p data-autonomous-work>Autonomous work: ${esc(workState)}${work.reason?` · ${esc(work.reason)}`:''}${work.blocked_by?` · ${esc(typeof work.blocked_by==='string'?work.blocked_by:JSON.stringify(work.blocked_by))}`:''}</p>${(work.blocked || []).map(item=>`<p>Blocked: ${esc(item.kind)} · ${esc(item.reason || item.waiting_for || item.depends_on || 'Details unavailable')}</p>`).join('')}`:''}`;
  }
  function resourceContext(snapshot) {
    return `<div data-resource-reservations>${Object.entries(snapshot.resources || {}).map(([name,value])=>`<p>${esc(label(name))} reserved: ${value.reserved==null?'Not tracked':esc(number(value.reserved))}${name==='gpu_seconds'?` · GPU used: ${value.used==null?'Not tracked':esc(number(value.used))+' s'} · ${value.budgeted===false?'No budget set':'Budgeted'}<br><small>${esc(value.coverage || 'GPU coverage unavailable')}</small>`:''}</p>`).join('')}</div>`;
  }
  function bindAttentionPanels(snapshot,chosen) {
    const region=view.querySelector('[data-core-region="request"]');
    if(region && !snapshot.dev_session) {
      // Folded while idle so the request region stays compact; open when someone has to act.
      const waiting=actionableRequests(snapshot).length>0;
      // A sibling after the region, not inside it: the idle request region has a height budget.
      region.insertAdjacentHTML('afterend',`<details class="panel ab-attention-mechanism" data-attention-mechanism data-persist-detail="attention-mechanism:${esc(snapshot.run.run_id)}"${waiting?' open':''}><summary>Evidence, explanations and Memory</summary>${requestMechanismDetails(snapshot,chosen)}${memoryPanel(snapshot)}</details>`);
    }
    if(!snapshot.dev_session) {
      view.querySelector('[data-run-settings]')?.insertAdjacentHTML('beforeend',attentionRunContext(snapshot));
      view.querySelector('[data-resource-details]')?.insertAdjacentHTML('beforeend',resourceContext(snapshot));
      const allowed={hint:'respond_hint',approve:'approve_or_deny',deny:'approve_or_deny',interrupt:'confirm_interrupt'};
      view.querySelectorAll('button[data-respond]').forEach(button=>{if(!requestAllowed(chosen,allowed[button.dataset.respond]))button.remove();});
      for(const [selector,action] of [['[data-defer-request]','defer'],['[data-cancel-request]','cancel'],['[data-ignore-request]','ignore']]) {
        [...view.querySelectorAll(selector)].forEach((button,index)=>{if(!requestAllowed(chosen,action)||index>0)button.remove();});
      }
    }
    async function read(path) {const response=await fetch(path,{cache:'no-store'});const result=await response.json();if(!response.ok)throw new Error(result.error || `HTTP ${response.status}`);return result;}
    function redraw() {if(currentSnapshot?.run.run_id===snapshot.run.run_id)render(currentSnapshot);}
    view.querySelector('[data-memory-refresh]')?.addEventListener('click',async event=>{
      event.currentTarget.disabled=true;
      try{memoryCatalogue=await read('/api/memories');memoryMessage='Catalogue refreshed';}catch(error){memoryMessage=error.message;}redraw();
    });
    view.querySelectorAll('[data-memory-open]').forEach(button=>button.addEventListener('click',async()=>{
      button.disabled=true;
      try{memorySelected=await read('/api/memories/'+encodeURIComponent(button.dataset.memoryOpen));memoryMessage='';}catch(error){memoryMessage=error.message;}redraw();
    }));
    view.querySelector('[data-memory-operation]')?.addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
      const action=form.elements.action.value,body={actor:form.elements.actor.value.trim(),reason:form.elements.reason.value.trim()};
      if(action==='expiry'){const raw=form.elements.expires_at.value;if(raw===''){memoryMessage='Expiry time is required';redraw();return;}body.expires_at=Number(raw);}
      form.querySelector('button').disabled=true;
      try{const response=await controlFetch(`/api/memories/${encodeURIComponent(form.dataset.memoryId)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-Attention-Token':root.dataset.controlToken || ''},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(result.error || `HTTP ${response.status}`);memorySelected=await read('/api/memories/'+encodeURIComponent(form.dataset.memoryId));memoryCatalogue=await read('/api/memories');memoryMessage='Lifecycle operation saved';lastRendered='';await refresh();}catch(error){memoryMessage=error.message;}redraw();
    });
  }
  // End Attention mechanism presentation.

  function bindDevGuidance(snapshot){
    const form=view.querySelector('[data-dev-guidance]');if(!form)return;
    const sid=snapshot.dev_session.session_id,key=`${sid}:${form.dataset.requestId}`;
    form.elements.content.addEventListener('input',()=>stored('attention.dev-guidance:'+key,form.elements.content.value));
    form.addEventListener('submit',async event=>{
      event.preventDefault();const skip=event.submitter?.value==='skip';
      if(devGuidancePending.has(key)||(!skip&&!form.reportValidity()))return;
      devGuidancePending.add(key);form.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{
        const response=await controlFetch(`/api/dev/sessions/${encodeURIComponent(sid)}/attention-guidance`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:form.dataset.requestId,content:skip?'':form.elements.content.value.trim(),skip})});
        const result=await response.json();if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
        stored('attention.dev-guidance:'+key,null);lastRendered='';await refresh();
      }catch(error){form.querySelector('[data-dev-guidance-message]').textContent=DEBUG_VIEW ? error.message : plainError(error.message);}
      finally{devGuidancePending.delete(key);form.querySelectorAll('button').forEach(b=>b.disabled=false);}
    });
  }
  function requestPanel(snapshot, chosen) {
    if (snapshot.dev_session && !snapshot.dev_v2) return devGuidancePanel(snapshot);
    const history = snapshot.requests.filter(request => !actionableRequests(snapshot).some(r => r.request_id === request.request_id));
    const historical = history.find(request => request.request_id === selectedRequest);
    const expired = snapshot.requests.find(r => r.state === 'pending' && r.mode === 'live_human_first' && replyExpired(r));
    const otherPending = actionableRequests(snapshot).filter(r => r.request_id !== chosen?.request_id);
    const feedback = chosen ? null : feedbackRequest(snapshot);
    const reply = submittedHint(snapshot,feedback);
    return `${DEBUG_VIEW ? `<section class="panel detail ab-current-request" data-core-region="request" data-state="${chosen ? 'active' : 'idle'}" aria-label="Current Request & Reply"><div class="panel-title"><h3>Current Request & Reply</h3>${chosen ? chip('pending', 'Awaiting your decision') : ''}</div>${chosen ? `${otherPending.length ? `<nav aria-label="Other pending requests">${otherPending.map(r => requestRow(snapshot,r)).join('')}</nav>` : ''}${traceDetail(snapshot, chosen)}` : `<div class="ab-idle-row"><p class="ab-idle-request">No action required</p>${feedbackShortcut(snapshot)}</div><p class="ab-idle-guidance" data-idle-guidance>${esc(idleGuidance(snapshot))}</p>${expired ? `<p data-reply-countdown data-deadline="${esc(expired.deadline_at)}" data-expired="true">${esc(countdown(expired))}</p>` : ''}`}${reply}
      ${history.length ? `<details class="ab-request-history" data-request-history data-persist-detail="requests"><summary>Request history · ${history.length} </summary><div class="inbox-list">${history.map(r => requestRow(snapshot,r)).join('')}</div>${historical ? traceDetail(snapshot, historical, false, Boolean(chosen) || feedback?.request_id !== historical.request_id) : '<p class="minor">Select a historical request to view its evidence.</p>'}</details>` : ''}</section>` : operatorHelp(snapshot,chosen)}`;
  }
  function mdLite(html) {
    return DEBUG_VIEW?html:html.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'$1');
  }
  function readableReply(response) {
    return mdLite(readableReplyRaw(response));
  }
  function readableReplyRaw(response) {
    if(!response?.content)return '';
    if(response.responder==='human')return `<p>${esc(response.content)}</p>`;
    try {
      const value=JSON.parse(response.content);
      if(value&&typeof value==='object'&&!Array.isArray(value)) {
        const parts=[['guidance','Guidance'],['diagnosis','Explanation'],['caution','Caution']].filter(([key])=>typeof value[key]==='string');
        if(parts.length)return parts.map(([key,title])=>`<div class="ab-reply-part"><b>${title}</b><p>${esc(value[key])}</p></div>`).join('');
      }
    }catch(_){ /* Original text remains safe and accessible. */ }
    return `<p>${esc(response.content)}</p>`;
  }
  function observationPanel(snapshot, request) {
    const evidence=snapshot.response_evidence?.[request.request_id];
    const response=snapshot.responses[request.response_id];
    const receipt=submissionReceipts.get(`${snapshot.run.run_id}:${request.request_id}`);
    const submitted=evidence?.submission?.response_id||response?.response_id||receipt?.response_id;
    const hasSubmission=submitted||receipt||request.state==='answered';
    const linked=evidence?.association?.status==='linked'&&snapshot.attempts.find(a=>a.attempt_id===evidence.association.attempt_id);
    const comparison=evidence?.control_comparison;
    const controls=comparison?.status==='visible_commands_differ'?'Visible commands changed':comparison?.status==='visible_commands_unchanged'?'Visible commands unchanged':'Insufficient evidence';
    const native=evidence?.native_outcome;
    const compilation=snapshot.response_uses?.[request.request_id]?.guidance_compilation;
    return `<div class="ab-feedback-layers" data-adoption-evidence><article data-feedback-layer="submission"><h4>Submission</h4><strong>${hasSubmission?'Submitted · Received':'Submission unconfirmed'}</strong>${response?.content?`<details data-persist-detail="reply:${esc(request.request_id)}"><summary>View reply</summary>${readableReply(response)}</details>`:''}</article>
      <article data-feedback-layer="association"><h4>Execution Association</h4><strong>${linked?`Linked to attempt ${linked.index+1}`:'Not associated · Unconfirmed'}</strong>${linked?`<button type="button" data-node-replay="${esc(linked.attempt_id)}">View replay</button>`:''}</article>
      <article data-feedback-layer="control"><h4>Action Changes</h4><strong>${esc(controls)}</strong><small>Actual action changes unconfirmed</small>${compilation?`<details data-guidance-compilation><summary>Guidance processing record</summary><p>Original: ${esc(compilation.original)}</p><p>Passed to policy: ${esc(compilation.compiled)}</p><p>Parsed parameters: ${esc(JSON.stringify(compilation.parameters))} · Rule ${esc(compilation.rule||'No match')}</p><p>The example hint and legacy Chinese preset are supported only for Lift the cube. Other free text may not change actions. Compare parsed parameters with actual commands.</p></details>`:''}<details><summary>Command comparison details</summary><pre>${esc(JSON.stringify({before:comparison?.before||[],after:comparison?.after||[],trace_digests:evidence?.trace_digests||{}},null,2))}</pre></details></article>
      <article data-feedback-layer="native"><h4>Native Task Outcome</h4><strong>${native?.status==='verified'&&typeof native.value==='boolean'?native.value?'Task successful':'Task unsuccessful':'Unconfirmed · outcome pending'}</strong></article></div><p class="ab-evidence-limit">Whether this reply caused action changes still needs verification.</p>`;
  }
  function feedbackRequest(snapshot) {
    const hasFeedback = r => r.state === 'answered' || r.state === 'fallback' || r.response_id ||
      snapshot.response_evidence?.[r.request_id] || snapshot.response_uses?.[r.request_id] || submissionReceipts.has(`${snapshot.run.run_id}:${r.request_id}`);
    return snapshot.requests.find(r => r.request_id === selectedRequest && hasFeedback(r)) ||
      [...snapshot.requests].sort((a,b) => b.created_at - a.created_at).find(hasFeedback);
  }
  // Operator presentation: no changes to evidence, decisions or request payloads.
  // ---- operator polish helpers ----
  function epoch(value) {if(value==null||value==='')return null;if(typeof value==='number')return value>1e12?value/1000:value;const t=Date.parse(value);return Number.isNaN(t)?null:t/1000;}
  function humanDuration(seconds) {const v=Math.max(0,Math.round(Number(seconds)||0));return v<60?`${v} s`:v<3600?`${Math.round(v/60)} min`:`${Math.round(v/360)/10} h`;}
  function relativeTime(value,fallback='') {
    const at=epoch(value);if(at==null)return fallback;
    const diff=Date.now()/1000-at;
    if(diff<45)return 'just now';if(diff<3600)return `${Math.max(1,Math.round(diff/60))} min ago`;
    if(diff<86400)return `${Math.round(diff/3600)} h ago`;if(diff<86400*14)return `${Math.round(diff/86400)} days ago`;
    return fallback||new Date(at*1000).toLocaleDateString('en-US',{month:'short',day:'numeric'});
  }
  function recordTone(r) {
    const status=String(r.status||'');
    if(recordIsActive(r))return Number(r.pending_hints||0)>0?'waiting':'running';
    if(status==='needs_review')return /infrastructure|dispatch unknown|outcome_unknown/i.test(String(r.reason||''))?'failed':'waiting';
    if(['failed','timed_out','budget_exhausted'].includes(status))return 'failed';
    if(status==='succeeded')return 'success';
    return 'idle';
  }
  function sidebarMatches(r) {
    const tone=recordTone(r);
    return sidebarFilter==='all'||(sidebarFilter==='running'&&tone==='running')||(sidebarFilter==='needs'&&tone==='waiting')||(sidebarFilter==='failed'&&tone==='failed')||(sidebarFilter==='succeeded'&&tone==='success');
  }
  function sidebarFilters(records) {
    const count=key=>key==='all'?records.length:records.filter(r=>({running:'running',needs:'waiting',failed:'failed',succeeded:'success'})[key]===recordTone(r)).length;
    const items=[['all','All'],['running','Running'],['needs','Needs you'],['failed','Failed'],['succeeded','Succeeded']];
    return `<div class="op-filter" role="group" aria-label="Filter runs">${items.map(([key,name])=>`<button type="button" data-op-filter="${key}" aria-pressed="${sidebarFilter===key}">${name}<i>${count(key)}</i></button>`).join('')}</div>`;
  }
  // The backend could not dispatch/finish the execution: not something the robot did wrong.
  function systemFailure(snapshot) {
    const s=snapshot.dev_session;
    if(!s)return false;
    return /infrastructure|dispatch unknown|outcome_unknown/i.test(String(s.reason||''));
  }
  function neverStarted(snapshot) {
    return systemFailure(snapshot) && !Number(snapshot.resources?.executions?.used ?? snapshot.dev_session?.used?.executions ?? 0);
  }
  function operatorState(snapshot) {
    const last=operatorLast(snapshot),outcome=snapshot.eval?.native_outcomes?.[last?.attempt_id];
    const success=(outcome?.evaluated===true&&outcome.native_success===true)||snapshot.eval?.native_success===true;
    const phase=snapshot.dev_v2?.lifecycle?.phase;
    return phase && !['sdk','terminal'].includes(phase) ? phase : success?'success':isTerminal(snapshot)?'failed':operatorWaiting(snapshot)?'waiting':'running';
  }
  let toastTimer=null;
  function toast(message,tone='info') {
    let el=document.querySelector('.op-toast');
    if(!el){el=document.createElement('div');el.className='op-toast';el.setAttribute('role','status');el.setAttribute('aria-live','polite');root.append(el);}
    el.textContent=message;el.dataset.tone=tone;el.dataset.show='true';
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>{el.dataset.show='false';},3200);
  }
  // Frames are published once per simulator control step. While the planner thinks (often 20-60 s) the simulator is
  // not stepped, the robot does not move and no new picture exists: that is not a broken feed, so say so.
  function stalePictureCopy(phase, age) {
    const wait = age == null ? '' : ` · ${Math.round(age)} s`;
    if (phase === 'planner_prepare') return `The robot is planning its next move. The picture stays still until it moves${wait}`;
    if (phase === 'env_create') return `Setting up the simulator${wait}`;
    if (phase === 'sdk_running' || phase === 'execute') return `Waiting for the next picture from the robot${wait}`;
    return `Waiting for the next picture${wait}`;
  }
  function operatorPolicyChoice(id) {
    return ({autonomous:['Never asks for help','The robot works on its own.'],
      demo_first:['Starts from a worked example','Begins with the standard example solution.'],
      reactive_help:['Asks for help after each failed attempt','An advisor reviews every failure and suggests a fix.'],
      retry_k_then_ask:['Asks after repeated failures','Asks only after several failures in a row.'],
      budget_matched_random_escalation:['Asks at random moments','A test control that spends the same help budget at random.'],
      trace_aware_hint_only:['Asks using what the robot just did','Picks the hint from the robot\'s recent actions.'],
      full_trace_aware_attention_planner:['Plans help from the full action history','Uses everything the robot did; stops if it needs an approval.']})[id] || [label(id),''];
  }
  function operatorPolicyPanel({c,run,readOnly,pending,request,local,selectedDev}) {
    const ids=(c?.binding?.allowed_policies||(selectedDev?[]:['autonomous','reactive_help','trace_aware_hint_only','full_trace_aware_attention_planner','retry_k_then_ask']));
    const requested=local?.state==='Requested'?local.policy_id:request?.state==='Pending'?request.policy_id:null;
    const intro=readOnly?'This help strategy is read-only for this record.':requested?`Change requested: ${operatorPolicyChoice(requested)[0]}. It takes effect from the next attempt, once the robot reaches a safe point.`:'You can change this while the run is going. A change takes effect from the next attempt.';
    const items=ids.map(id=>{
      const [title,text]=operatorPolicyChoice(id),current=c?.active_policy===id,wanted=requested===id;
      const chip=current?(requested&&!wanted?'Current':'Current'):wanted?'Requested':'';
      return `<button type="button" role="radio" aria-checked="${current||wanted}" data-attention-policy="${esc(id)}" aria-pressed="${current}" data-state="${current?'Active':wanted?'Requested':'Available'}" ${readOnly||pending||current?'disabled':''}><i class="op-radio" aria-hidden="true"></i><span class="op-pol-text"><b>${esc(title)}</b><small>${esc(text)}</small></span>${chip?`<em class="op-pol-chip" data-chip="${current?'current':'requested'}">${chip}</em>`:''}</button>`;
    }).join('');
    return `<section class="ab-policy-controller op-policy" data-policy-controller data-run="${esc(run?.run_id||'')}" aria-label="Help strategy"><p class="op-policy-intro" data-state="${readOnly?'ended':requested?'requested':'open'}">${esc(intro)}</p><div class="ab-policy-cards op-policy-list" role="radiogroup" aria-label="Help strategy">${items}</div>${request?.state==='Pending'?'<button type="button" data-cancel-policy>Cancel pending switch</button>':''}${request?`<p data-policy-request hidden>${esc(request.state)} · ${esc(policyTitle(request.policy_id))}</p>`:''}${/unconfirmed|could not|failed/i.test(sidebarMessage)?`<p role="status" data-sidebar-message>${esc(sidebarMessage)}</p>`:''}</section>`;
  }
  function operatorHeading(number, title, question) {
    return `<div class="panel-title op-heading"><span class="op-number">${number}</span><h3>${title}</h3><span>${question}</span></div>`;
  }
  function plainAction(operation, debug = DEBUG_VIEW) {
    if (debug) return String(operation || 'Robot action');
    const bare={find_objects:'Looked for the objects',get_observation:'Checked arm position',pixel_to_world:'Located a point in the camera image',move_to_position:'Moved the arm to a target',move_delta:'Moved the arm a small step',move_normalized:'Moved the base',plan_to_position:'Planned the arm path',open:'Opened the gripper',close:'Closed the gripper',run_policy:'Ran the robot plan'};
    if (bare[operation]) return bare[operation];
    const exact = {'sensors.find_objects':'Looked for the objects','sensors.get_observation':'Checked arm position','sensors.pixel_to_world':'Located a point in the camera image','base.move_delta':'Moved the base','base.move_normalized':'Moved the base','base.get_state':'Checked base position','base.get_full_state':'Checked base position'};
    if (exact[operation]) return exact[operation];
    const match = String(operation || '').match(/^(?:(left|right)_)?(gripper|arm)\.(open|close|move_delta|move_to_position|move_to_pose|plan_to_position)$/);
    if (!match) return 'Robot action';
    const side = match[1] ? match[1] + ' ' : '';
    const actions = {open:`Opened ${side}gripper`,close:`Closed ${side}gripper`,move_delta:`Moved ${side}arm a small step`,move_to_position:`Moved ${side}arm to a target`,move_to_pose:`Moved ${side}arm to a target`,plan_to_position:`Planned ${side}arm path`};
    return ((match[2] === 'gripper' && ['open','close'].includes(match[3])) || (match[2] === 'arm' && !['open','close'].includes(match[3]))) ? actions[match[3]] : 'Robot action';
  }
  function plainEvent(event) {
    const op=event?.operation||event?.event_type, type=String(event?.event_type||'');
    if (DEBUG_VIEW) return String(op || 'Robot action');
    const skill={left_motion:'Moved the left arm',right_motion:'Moved the right arm',synchronous_motion:'Moved both arms together',left_gripper_open_close:'Operated the left gripper',right_gripper_open_close:'Operated the right gripper'};
    if (skill[op]) return skill[op];
    if (op==='run_policy') return event.status==='failed'?'The robot plan stopped':/finished/.test(type)?'Finished the robot plan':'Started the robot plan';
    if (type==='sdk.bimanual_control') return op==='state'?'Checked both arms':'Moved both arms';
    if (type==='sdk.gripper_command' && op==='grasp') return 'Grasped with the gripper';
    if (type==='sdk.arm_command' && op==='move_joints') return 'Moved the arm';
    return plainAction(op);
  }
  function plainError(error) {
    let text = String(error || '').trim();
    while (/^(RuntimeError|ValueError|Exception):\s*/i.test(text)) text = text.replace(/^(RuntimeError|ValueError|Exception):\s*/i,'').trim();
    const map = [
      [/joint tracking exceeds|mrad/i,'the arm moved too abruptly, so the safety controller halted it'],
      [/PlanningRejected|rejected by curobo|read_only_arm_plan_rejected|continuous_command_validation_rejected/i,'the motion planner refused the move (out of reach or would collide)'],
      [/slipped|grasp lost during/i,'the item slipped out of the gripper'],
      [/not uniquely visible|is required/i,"the robot couldn't clearly see the item"],
      [/infrastructure|dispatch unknown|outcome_unknown/i,"a system problem stopped it (not the robot's fault)"],
      [/finished but the task goal was not verified/i,'the robot finished its moves, but the task was not completed'],
      [/timeout|timed out/i,'the action took too long'],
      [/budget|limit/i,'it ran out of its allowed budget'],
    ];
    for (const [pattern,copy] of map) if (pattern.test(text)) return copy;
    // A short English sentence is the only permitted verbatim fallback.
    return text.length < 120 && /[A-Za-z]/.test(text) && /^[\x20-\x7e]+$/.test(text) && !/[_{}(]|\b[a-z][a-z\d]*\.[a-z][a-z\d]*/i.test(text) && !/\b[0-9a-f]{12,}\b|SHA256/i.test(text) ? text : 'the robot hit an execution error';
  }
  function operatorTime(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2,'0')}`;
  }
  function operatorPolicy(snapshot) {
    const id = snapshot.attention_control?.active_policy || snapshot.run.policy_id;
    const config = snapshot.attention_control?.binding || snapshot.dev_session?.locked?.config || {};
    const retry = config.retry_k ?? config.policy_config?.retry_k ?? config.policy_parameters?.retry_k ?? snapshot.task_plan?.retry_k;
    return ({autonomous:'Never asks for help',demo_first:'Starts from a worked example',reactive_help:'Ask for help after each failed attempt',retry_k_then_ask:retry == null ? 'Ask for help after repeated failures' : `Ask for help after ${retry} failures in a row`,budget_matched_random_escalation:'Asks for help at random moments (test control)',trace_aware_hint_only:'Asks for help using what the robot just did',full_trace_aware_attention_planner:'Plans help using the full action history'})[id] || 'Recorded help strategy';
  }
  function operatorLifecycleCopy(snapshot) {
    const l=snapshot.dev_v2?.lifecycle;
    if(!l)return null;
    const scope=(l.scope||[]).map(row=>`${taskDisplayName({task_id:row.task_id,suite:'robocasa365'})} (seed ${row.seed})`).join('; ');
    const rows={
      prepared:['○ PREPARED / NOT STARTED','Prepared / Not started',`This experiment is prepared. No model or robot work has started. Start schedules all ${l.scope_count} cells: ${scope}. Specific motion still requires separate human approval.`],
      queued:['○ QUEUED / NEXT ROUND','Queued / Next round',l.scheduler_active?'Waiting for the scheduler to reach this cell or the next round. No model or SDK job is active in this cell.':'Waiting for explicit continuation. No model or SDK job is active in this cell.'],
      model:['● MODEL WORKING','Model working',"The AI is writing the robot's plan. The robot is not executing it."],
      advisor:['● WAITING FOR ADVISOR','Waiting for Advisor','The Advisor is preparing guidance. The robot is not executing.'],
      approval:['⚠ WAITING FOR HUMAN APPROVAL','Needs human approval',l.recovery_available?'The scheduler stopped during this approval wait. Continue experiment to resume the same request and original deadline. A separate human decision is still required in section 04.':'A specific robot action is waiting for a separate human decision in section 04. No motion is authorized by starting the experiment.'],
      preparing:['○ PREPARING PUBLIC WORK','Preparing public work','The scheduler is preparing public inputs or checking Memory. No SDK execution is active.'],
      review:['⚠ NEEDS REVIEW','Needs review','Work or ownership is unresolved. Review the persistent records before continuing; no automatic retry is available.'],
      sdk:['● SDK RUNNING','SDK running','A claimed SDK execution has a live owner.'],
      terminal:['○ ENDED','Ended','This run has ended. Recorded work and replies remain available.']
    };
    const row=rows[l.phase];return row?{title:row[0],short:row[1],text:row[2]}:null;
  }
  function operatorPageTitle(snapshot,name) {
    const copy=operatorLifecycleCopy(snapshot);
    const end=snapshot.dev_session&&devEndSummary(snapshot.dev_session);
    if(end?.key==='action_approval')return `${end.title} · ${name} · Attention Bench`;
    return `${copy?.short || ({waiting:'⚠ Needs you',running:'● Running',failed:'✕ Failed',success:'✓ Done'})[operatorState(snapshot)]} · ${name} · Attention Bench`;
  }
  function devStartButton(snapshot) {
    const l=snapshot.dev_v2?.lifecycle;
    if(l)return `<button type="button" data-dev-resume ${l.can_start?'':'disabled'}>${esc(l.start_label)}</button><small>Schedules the existing experiment · all ${l.scope_count} cells</small>`;
    const s=snapshot.dev_session;
    return s?`<button type="button" data-dev-resume ${(snapshot.dev_v2?s.state==='not_started':['ready','stopped'].includes(s.state))?'':'disabled'}>Resume</button>`:'';
  }
  function operatorSettings(snapshot) {
    const run = snapshot.run, config = snapshot.dev_session?.locked?.config || {};
    const base = run.robot_configuration?.base || run.base || {};
    const mobile = ['mobile_base_joint_drive','mujoco_joint_driven_planar_base','official_hybrid_mobile_base'].includes(base.capability) || config.base_mode === 'mobile';
    const arm = run.robot?.execution_arm || config.execution_arm;
    const arms = {both:'both arms',left:'left arm',right:'right arm',single:'single arm'}[arm];
    const robot = String(run.robot?.model || config.robot || (run.suite==='robosuite'?'Simulated robot arm':'Robot')).replace('OpenArm365','OpenArm');
    const benchmark = {robocasa365:'RoboCasa365',robocasa:'RoboCasa',robosuite:'Robosuite'}[run.suite] || 'Simulator';
    const kitchen = {robocasa365:'RoboCasa365 kitchen',robocasa:'RoboCasa kitchen',robosuite:'Robosuite'}[run.suite] || 'Benchmark not recorded';
    const simulator = run.execution_target === 'simulation' || run.execution_target?.endsWith('_sim');
    const model = String(snapshot.dev_session?.model || run.developer_model || '').split('/').at(-1);
    const policy = snapshot.attention_control?.active_policy || run.policy_id;
    const history = stored(`attention.history:${run.run_id}`);
    const changeable = snapshot.attention_control && !snapshot.attention_control.read_only && !isTerminal(snapshot) && (!history || history === 'live');
    const field = (title,main,sub='',wide=false,extra='') => `<div class="op-field${wide?' op-wide':''}${title==='Task'?' op-task':''}"><span>${title}</span><b>${esc(main)}</b>${sub?`<small>${esc(sub)}</small>`:''}${extra}</div>`;
    return `<section class="panel ab-run-settings" data-run-settings data-settings-run="${esc(run.run_id)}" aria-label="Task setup">${operatorHeading('01','TASK SETUP','What is the robot trying to do?')}<div class="op-fields">
      ${field('Task',taskDisplayName(run),(snapshot.task_plan?.definition?.description && snapshot.task_plan.definition.description !== taskDisplayName(run)) ? snapshot.task_plan.definition.description : kitchen,true)}
      ${field('Robot',`${robot}${arms?' · '+arms:''}`,mobile?'Base can move':'Base stays in place')}
      ${field('Where it runs',simulator?`${benchmark} simulator`:'Real robot',run.seed == null?'Scene seed not recorded':`Scene seed ${run.seed}`)}
      ${field('Help strategy',policyTitle(policy),`${operatorPolicy(snapshot)} · ${snapshot.dev_v2 || (snapshot.dev_session && !snapshot.attention_control?.binding) || run.assistance_mode !== 'live_human_first' ? 'an automatic advisor answers' : 'a person answers'}`,true,changeable?'<details class="op-strategy" data-persist-detail="operator-strategy"><summary>Change</summary><div data-policy-host></div></details>':'')}
      ${field('Dev agent',snapshot.dev_session?(model&&!/^formal-|-policy$/.test(model)?model:'AI-written plan'):'Fixed plan',snapshot.dev_session?'AI rewrites its plan after each attempt':'Uses an approved fixed plan')}
      ${snapshot.dev_eval_enabled?(textOnlyReviews(snapshot).length?field('HumanProxy','GLM · text only','Qwen was unavailable, so recordings were not reviewed visually · the simulator still decides success').replace('class="op-field','class="op-field op-warn'):field('HumanProxy','Qwen',"Answers review and hint requests from the recording and camera views · the simulator still decides success")):field('HumanProxy',"Simulator's own task check",'Success is decided by the simulator, not by an AI model')}</div></section>`;
  }
  function operatorWaiting(snapshot) {
    const control = snapshot.attention_control;
    return snapshot.dev_session ? control?.hint?.state === 'pending' && !control.read_only : actionableRequests(snapshot).length > 0;
  }
  function operatorLast(snapshot) {
    return [...snapshot.attempts].sort((a,b)=>a.index-b.index).at(-1);
  }
  function operatorFailureTime(trace, hasVideo = true) {
    const failure = executionFailure(trace);
    const event = (trace?.events || []).findLast(e=>e.status === 'failed') || (failure && (trace?.events || []).at(-1));
    if (!event) return null;
    const target = replayTarget(event,trace?.recording,hasVideo);
    if (hasVideo) return target.seconds;
    return event.timestamp_basis === 'relative' && Number.isFinite(event.timestamp) ? event.timestamp : null;
  }
  function operatorEndReason(snapshot, maximum) {
    const s = snapshot.dev_session;
    const key = s ? devEndSummary(s).key : snapshot.eval?.stopped_reason;
    const reason = String(s?.reason || snapshot.eval?.stopped_reason || '');
    const end=s&&devEndSummary(s);
    if(end?.key==='action_approval')return end.explanation+(end.review?'; review required for unresolved work':'');
    if (['executions','model_calls','dev_calls','max_attempts'].includes(key)) return `it used all ${maximum} allowed attempts`;
    if (/help|assistance/.test(key || '')) return 'it used all help requests';
    if (/tokens|AI/.test(key || '')) return 'it ran out of AI budget';
    if (/wall|deadline|seconds/.test(key || '') || /wall|deadline/.test(reason)) return 'it ran out of time';
    if (['stopped','cancelled'].includes(s?.state || snapshot.run.status) || /interrupt|cancel|stopped/.test(reason)) return 'someone stopped it';
    if (s?.state === 'failed' || /executor_failed|service|system error|infrastructure|dispatch unknown|outcome_unknown/.test(reason)) return "of a system problem (not the robot's fault)";
    if (s?.state === 'needs_review' || /needs_review|incomplete|unavailable/.test(reason)) return 'the result needs a human check';
    if (/budget|limit/.test(reason)) return 'it ran out of its allowed budget';
    if (maximum && snapshot.attempts.length >= maximum) return `it used all ${maximum} allowed attempts`;
    return 'the run ended';
  }
  function operatorOutcome(snapshot) {
    const lifecycle=snapshot.dev_v2?.lifecycle,copyState=operatorLifecycleCopy(snapshot);
    const end=snapshot.dev_session&&devEndSummary(snapshot.dev_session),permissionStopped=end?.key==='action_approval';
    if(copyState && !['sdk','terminal'].includes(lifecycle.phase))return `<div class="op-outcome" data-operator-outcome="${esc(lifecycle.phase)}" role="status"><strong>${copyState.title}</strong><div>${permissionStopped?`<p>Action permission closed · ${esc(end.explanation)}. This action was not executed.</p>`:''}<p>${esc(copyState.text)}</p></div></div>`;
    const last = operatorLast(snapshot), trace = Object.values(snapshot.traces || {}).find(t=>t.attempt_id === last?.attempt_id);
    const k = last ? last.index + 1 : 0;
    const maximum = snapshot.dev_session?.budget?.executions ?? snapshot.task_plan?.max_attempts ?? launches.find(l=>l.run_id === snapshot.run.run_id)?.locked?.max_attempts ?? snapshot.attempts.length;
    const outcome = snapshot.eval?.native_outcomes?.[last?.attempt_id];
    const success = (outcome?.evaluated === true && outcome.native_success === true) || snapshot.eval?.native_success === true;
    const waiting = operatorWaiting(snapshot), terminal = isTerminal(snapshot);
    const state = success ? 'success' : terminal ? 'failed' : waiting ? 'waiting' : 'running';
    const system = state==='failed' && systemFailure(snapshot);
    const title = system ? '⚠ SYSTEM PROBLEM' : {success:'✓ COMPLETED',failed:'✕ NOT COMPLETED',waiting:'● WAITING FOR YOU',running:'● RUNNING'}[state];
    const failure = plainError(executionFailure(trace)?.message || snapshot.attention_control?.hint?.feedback?.public_error);
    const time = operatorFailureTime(trace,(trace?.evidence || []).some(e=>e.media_url && e.mime_type?.startsWith('video/')));
    const copy = permissionStopped ? (end.authorization_exhausted?'Action permission closed · Original authorization or budget exhausted. This action was not executed.':'The run ended at the action approval gate. This action was not executed.') : system ? (neverStarted(snapshot) ? "The robot never started because of a system problem. This is not the robot's fault." : "The run stopped because of a system problem. This is not the robot's fault.") : success ? `Completed on attempt ${k} of ${maximum}.` : terminal ? k ? `All ${k} attempts failed. In the last attempt ${failure}${time == null ? '' : ` at ${operatorTime(time)}`}.` : 'This run ended without a recorded attempt.' : waiting ? `Attempt ${k} failed. The robot is paused and asking for help, so please reply in section 04.` : (() => {
      const jobs=snapshot.dev_session?.jobs || [], next=Math.min(k + 1,maximum);
      if (last?.status === 'running' || !snapshot.dev_session) return `Attempt ${last?.status === 'running' ? k : next} of ${maximum} is running.`;
      if (jobs.some(j=>j.state==='claimed' && j.kind==='advisor')) return `Attempt ${k} did not succeed. The advisor is preparing guidance before attempt ${next}.`;
      if (jobs.some(j=>j.state==='claimed' && j.kind==='model')) return k ? `Attempt ${k} did not succeed. The AI is rewriting the robot's plan for attempt ${next}.` : "The AI is writing the robot's plan.";
      return `Attempt ${k ? next : 1} of ${maximum} is starting.`;
    })();
    const pending = snapshot.attention_control?.hint || actionableRequests(snapshot)[0];
    const startedAt=snapshot.run.started_at || snapshot.dev_session?.created_at,endedAt=snapshot.run.ended_at || snapshot.dev_session?.ended_at;
    const endReason=operatorEndReason(snapshot,maximum),took=epoch(startedAt)!=null&&epoch(endedAt)!=null?` · took ${humanDuration(epoch(endedAt)-epoch(startedAt))}`:'';
    const sub = system ? `${dateTime(startedAt)} · please tell the research team` : terminal ? `${endReason==='the run ended'?'Ended':`Ended because ${endReason}`}${startedAt==null?'':` · ${dateTime(startedAt)}`}${took}` : waiting ? `${Math.max(0,maximum-k)} attempt(s) left${pending?.created_at == null ? '' : ` · paused for ${operatorTime(Date.now()/1000-pending.created_at)}`}` : '';
    return `<div class="op-outcome ${state}${system?' system':''}" data-operator-outcome="${state}" role="status"><strong>${title}</strong><div><p>${esc(copy)}</p>${sub?`<p>${esc(sub)}</p>`:''}</div></div>`;
  }
  function operatorFlow(snapshot) {
    const nodes=[];
    for (const attempt of [...snapshot.attempts].sort((a,b)=>a.index-b.index)) {
      const success = snapshot.eval?.native_outcomes?.[attempt.attempt_id]?.native_success === true || attempt.status === 'succeeded';
      const copy = success ? '✓ Succeeded' : attempt.status === 'running' ? '● Running' : neverStarted(snapshot) ? '⚠ Not started' : '✕ Failed';
      nodes.push(`<button type="button" class="op-node ${success?'success':attempt.status==='running'?'running':'failed'}" data-operator-attempt="${esc(attempt.attempt_id)}"><small>${copy}</small><b>Attempt ${attempt.index+1}</b></button>`);
      const requests = snapshot.requests.filter(r=>r.attempt_id === attempt.attempt_id);
      for (const request of requests) nodes.push(`<button type="button" class="op-node" data-operator-help><small>${snapshot.responses?.[request.response_id]?'✓ Answered':request.state==='pending'?'● Waiting for reply':requestState(request)}</small><b>Asked for help</b></button>`);
      if (!requests.length && operatorWaiting(snapshot) && attempt === operatorLast(snapshot)) nodes.push('<button type="button" class="op-node waiting" data-operator-help><small>● Waiting for reply</small><b>Asked for help</b></button>');
    }
    if (isTerminal(snapshot)) nodes.push(`<div class="op-node ended"><small>Ended</small><b>${snapshot.eval?.native_success === true?'Completed':systemFailure(snapshot)?'System problem':'Not completed'}</b></div>`);
    else if (operatorLast(snapshot)?.status !== 'running' && snapshot.attempts.length < (snapshot.dev_session?.budget?.executions ?? snapshot.task_plan?.max_attempts ?? 0)) nodes.push('<div class="op-node next"><small>Next</small><b>Attempt '+(snapshot.attempts.length+1)+'</b></div>');
    return `<div class="op-flow" data-task-flow aria-label="Task progress">${nodes.join('<span class="op-arrow" aria-hidden="true"></span>')}</div>`;
  }
  function operatorOverview(snapshot) {
    const terminal = isTerminal(snapshot), s = snapshot.dev_session;
    const canStop = snapshot.dev_v2?.lifecycle ? snapshot.dev_v2.lifecycle.can_stop : s ? !terminal && s.state !== 'stopping' : snapshot.run.status === 'running' && ['robocasa_sim','robosuite_sim','robocasa365_sim'].includes(snapshot.run.execution_target) && !snapshot.station.interrupt;
    return `<section class="panel ab-run-overview" data-core-region="status" data-run-overview="${esc(snapshot.run.run_id)}" data-expanded="true" data-tone="${operatorState(snapshot)}" aria-label="Status and progress">${operatorHeading('02','STATUS &amp; PROGRESS','How is it going?')}${operatorOutcome(snapshot)}${operatorFlow(snapshot)}${attentionEfficiency(snapshot)}<div class="op-controls"><button type="button" class="danger" id="${s?'ab-emergency-interrupt':'ab-cancel-run'}" ${canStop?'':'disabled'}>■ Stop run</button>${devStartButton(snapshot)}${launchButton(snapshot)}</div><p data-picture-warning hidden></p></section>`;
  }

  function operatorEvents(trace, hasVideo) {
    const groups=[];
    (trace?.events || []).forEach((event,index)=>{
      if (!DEBUG_VIEW && event.status!=='failed' && (event.status==='started' || event.event_type==='sdk.sensor_read' || /(^|\.)get_observation$/.test(String(event.operation||'')) || (event.event_type==='sdk.bimanual_control' && event.operation==='state'))) return;
      const previous=groups.at(-1), operation=event.operation || event.event_type, text=plainEvent(event);
      if (previous && previous.text === text && previous.event.status === event.status) previous.count++;
      else groups.push({operation,event,index,count:1,text});
    });
    return groups.map(({operation,event,index,count,text})=>{
      const target=replayTarget(event,trace?.recording,hasVideo);
      const seconds=hasVideo ? target.seconds : (event.timestamp_basis==='relative' && Number.isFinite(event.timestamp) ? event.timestamp : null);
      const failed=event.status==='failed'||event.status==='blocked';
      return `<details data-history-event class="op-step${failed?' failed':''}"><summary data-replay-event="${index}" ${target.seconds==null?'':`data-replay-seconds="${target.seconds}" data-replay-mode="${target.mode}"`}><time>${seconds==null?'':operatorTime(seconds)}</time><span>${failed?'✕':event.status==='running'?'●':'✓'}</span><div><b>${esc(text)}</b>${count>1?`<small>${count} ${/^Checked/.test(text)?'checks':'times'}</small>`:''}${failed?`<small class="op-why">${event.status==='blocked'?'Blocked':'Stopped'}: ${esc(plainError(event.error?.message || (typeof event.error === 'string' ? event.error : null) || executionFailure(trace)?.message))}</small>`:''}</div></summary></details>`;
    }).join('');
  }
  function loadingOverlay(kind) {
    return `<div class="op-loading" data-op-loading role="status" aria-live="polite"><div class="op-loading-word" aria-hidden="true">LOADING</div><div class="op-loading-text" data-op-loading-text>${kind==='replay'?'Loading replay…':'Starting simulator…'}</div><i class="op-loading-bar" aria-hidden="true"></i></div>`;
  }
  function operatorExecution(snapshot, data) {
    const {attempt,selected,attempts,trace,recording,videos,images,recordingViews,viewOptions,chosenView,streaming,active,flipStaticRows,cameraSuite,cameraLabel}=data;
    const run=snapshot.run;
    const last=operatorLast(snapshot);
    const liveTrace=Object.values(snapshot.traces || {}).find(t=>t.attempt_id===last?.attempt_id);
    const shownTrace=attempt?trace:liveTrace;
    const failureTime=operatorFailureTime(trace,videos.length>0);
    const failed=attempt && !neverStarted(snapshot) && snapshot.eval?.native_outcomes?.[attempt.attempt_id]?.native_success !== true && ['failed','timed_out'].includes(attempt.status);
    const version=snapshot.dev_session?.versions.find(v=>v.version === ((attempt||last)?.dev_version ?? ((attempt||last)?.index+1)));
    const views=viewOptions?`<div class="ab-camera-views" role="group" aria-label="Camera view">${Object.entries(viewOptions).map(([key,row])=>`<button type="button" data-camera-view="${esc(key)}" aria-pressed="${key===chosenView}">${esc(row.name)}</button>`).join('')}</div>`:'';
    const videoHTML=videos.map((e,index)=>`<figure class="op-video" data-op-stage="replay" data-state="loading">${failed?`<span class="op-failure-tag">Attempt ${attempt.index+1} · failed${failureTime==null?'':` at ${operatorTime(failureTime)}`}</span>`:''}${loadingOverlay('replay')}<video data-history-replay ${index===0?'id="ab-history-replay"':''} data-replay-key="${esc(JSON.stringify([run.run_id,attempt.attempt_id,trace?.trace_id,...(recordingViews?["synchronized_views"]:[e.evidence_id||null,e.sha256||null,e.media_url])]))}" controls playsinline preload="metadata" src="${esc(e.media_url)}" aria-label="Attempt ${attempt.index+1} execution replay" ${failed&&failureTime!=null?`data-operator-failure-seconds="${failureTime}"`:failed?'data-operator-failure-end':''}></video></figure>`).join('');
    const stills=images.filter(e=>!['camera_initial','initial_observation','camera_final','final_observation'].includes(e.kind)).map(e=>`<figure><img data-history-image loading="lazy" ${flipStaticRows?'style="transform:scaleY(-1)"':''} src="${esc(e.media_url)}" alt="Saved attempt frame"><figcaption>Saved static frame · not a video</figcaption></figure>`).join('');
    const station=cameraStation(snapshot);
    const devJobs=(!isTerminal(snapshot) && snapshot.dev_session?.jobs) || [];
    const lifecycle=snapshot.dev_v2?.lifecycle, lifecycleCopy=operatorLifecycleCopy(snapshot);
    const prep=!lifecycle && !active && !isTerminal(snapshot) && snapshot.dev_session ? (devJobs.some(j=>j.state==='claimed' && j.kind==='model') ? 'model' : devJobs.some(j=>j.state==='claimed' && j.kind==='advisor') ? 'advisor' : 'start') : '';
    const busy=active || Boolean(prep);
    const nowEvent=(liveTrace?.events||[]).filter(e=>e.event_type!=='skill.started').at(-1);
    const prepText={model:"The AI is writing the robot's plan…",advisor:'The advisor is preparing guidance for the next attempt…',start:'Getting the robot ready…'}[prep];
    const strip=`<div class="op-live-strip" data-state="${active?'live':prep?'prep':'idle'}"><i class="op-live-dot" aria-hidden="true"></i><b>${active?'LIVE':prep?'WORKING':'CURRENT VIEW'}</b><span>${active?`Attempt ${(last?.index??0)+1} · Now: <em data-op-now data-events="${nowEvent?1:0}">${esc(nowEvent?plainEvent(nowEvent):'Getting ready')}</em>`:prep?esc(prepText):'The robot is not running right now'}</span>${active&&epoch(last?.started_at)!=null?`<time data-op-since="${epoch(last.started_at)}"></time>`:''}</div>`;
    let live;
    if (lifecycle && lifecycle.phase!=='sdk') live=`${strip}<div class="op-idle" data-lifecycle="${esc(lifecycle.phase)}"><b>${esc(lifecycleCopy?.short)}</b><p>${esc(lifecycleCopy?.text)}</p>${lifecycle.phase==='prepared'?'<p>No simulator or camera has been started.</p>':''}</div>`;
    else if (station.camera_url) live=`${strip}<div class="op-stage" data-op-stage="live" data-op-phase="${prep}" data-op-ended="${busy?'false':'true'}" data-op-key="${esc(previewKey(snapshot))}" data-state="loading"><div class="ab-station-camera"><img id="ab-camera-frame" ${station.camera_image_transform==='flip_y'?'style="transform:scaleY(-1)"':''} ${streaming?`hidden data-live-stream-key="${esc(previewKey(snapshot))}" data-preview-url="${esc(station.camera_preview_url)}"`:`src="${esc(station.camera_url)}"`} alt="${esc(cameraSuite)} camera frame"><span>${esc(cameraLabel)}</span></div>${loadingOverlay('live')}</div>${streaming?'<span data-preview-freshness role="status">Connecting to live preview…</span>':''}`;
    else if (busy) live=`${strip}<div class="op-stage" data-op-stage="live" data-op-phase="${prep}" data-state="loading"><div class="ab-camera-empty" data-camera-empty></div>${loadingOverlay('live')}</div>`;
    else live=`${strip}<div class="op-idle" data-camera-empty><b>Nothing is running right now</b><p>${last?'Pick an attempt above to watch what the robot did.':'Start a new run to see the robot live.'}</p>${last?`<button type="button" data-operator-attempt="${esc(last.attempt_id)}">Watch the last attempt</button>`:''}</div>`;
    const noVideo=neverStarted(snapshot)?'<div class="op-idle"><b>Nothing to replay</b><p>The robot never started, so there is no video and no steps.</p></div>':'<div class="op-idle"><b>No video was recorded for this attempt</b><p>The steps on the right still show what the robot did.</p></div>';
    const revision=version?`<details class="op-revision" data-persist-detail="operator-revision:${esc(version.version)}"><summary>What changed in this attempt (version ${version.version})</summary><div class="op-clamp six" data-operator-copy>${esc(version.reason || 'No change description recorded.')}</div><button type="button" class="op-more" data-operator-expand>Show more</button></details>`:'';
    const heading=!attempt&&active?'What the robot is doing':'What the robot did';
    return `<section class="panel ab-execution-viewer" data-core-region="execution" data-execution-viewer data-replay-attempt="${esc(attempt?.attempt_id || '')}">${operatorHeading('03','REPLAY &amp; ATTEMPTS','What did the robot do, and where did it go wrong?')}<nav class="ab-history-tabs op-tabs" aria-label="Attempt selection"><button type="button" data-history-select="live" aria-pressed="${!attempt}" class="op-tab-live"><i class="op-live-dot${active?' on':''}" aria-hidden="true"></i>Current view</button>${attempts.map(a=>`<button type="button" data-history-select="${esc(a.attempt_id)}" aria-pressed="${a.attempt_id===attempt?.attempt_id}">${snapshot.eval?.native_outcomes?.[a.attempt_id]?.native_success===true || a.status==='succeeded'?'✓':a.status==='running'?'●':neverStarted(snapshot)?'⚠':'✕'} Attempt ${a.index+1}</button>`).join('')}</nav>${views}<div class="ab-viewer-layout"><div class="ab-viewer-media">${attempt?(videoHTML || noVideo)+stills:live}</div><aside class="ab-viewer-trace"><h4>${heading}</h4><p class="op-hint">${attempt&&videos.length?'Click a step to jump to that moment in the video.':attempt?'Steps are listed in the order they happened.':active?'New steps appear here as the robot works.':'Steps from the latest attempt.'}</p><div class="ab-history-events" ${!attempt?'data-op-follow':''}>${operatorEvents(shownTrace,attempt?videos.length>0:false) || '<p>No recorded actions yet.</p>'}</div>${operatorAIReview(version?.evaluator_review,version?.version)}${snapshot.dev_session && !neverStarted(snapshot)?revision:''}</aside></div></section>`;
  }

  // Reviews that fell back from the vision model to the text model: nobody viewed the recording.
  function textOnlyReviews(snapshot) {
    return (snapshot?.dev_session?.versions || []).filter(v=>v?.evaluator_review?.mode==='text_only');
  }
  const FALLBACK_WHY={qwen_unreachable:'Qwen did not respond',qwen_error:'Qwen returned an error',qwen_vision_timeout:'Qwen took too long to view the recording',qwen_vision_error:'Qwen could not view the recording',qwen_skipped_after_vision_timeout:'Qwen was too slow earlier in this run',no_frames:'no recording was available'};
  function operatorAIReview(review,version,debug=false) {
    if(!review)return '';
    if(debug)return `<details data-persist-detail="ai-review-json:${esc(version)}"><summary>Raw AI review JSON</summary><pre>${esc(JSON.stringify(review,null,2))}</pre></details>`;
    const done=review.status==='completed',textOnly=review.mode==='text_only';
    const result=!done?'Review unavailable':textOnly?'No verdict':(review.passed?'Looks right':'Problems found');
    const kind=!done?'unavailable':textOnly?'textonly':(review.passed?'pass':'fail');
    const why=FALLBACK_WHY[review.fallback_reason];
    const sub=textOnly?`Text-only review (${esc(String(review.model || 'GLM').replace(/^.*\//,''))}) · no recording was viewed, so this is not a visual check${why?' · '+esc(why):''}`:'AI opinion — the simulator decides success';
    return `<div class="op-ai-review" data-ai-review data-review-mode="${textOnly?'text_only':'vision'}"><h4>AI review <span class="op-review-tag" data-result="${kind}">${result}</span></h4><small>${sub}</small><details data-persist-detail="ai-review:${esc(version)}"><summary>Read full review</summary><p>${esc(review.full_text || review.feedback || 'AI review unavailable.')}</p></details><p class="op-review-preview">${esc(review.feedback || 'AI review unavailable.')}</p></div>`;
  }

  function operatorQuickReplies() {
    return `<div class="op-chips">${['Move more slowly','Wrong object picked','Target is out of reach','Looks fine, try again'].map(text=>`<button type="button" data-hint-preset="operator" data-preset-text="${esc(text)}">${text}</button>`).join('')}</div>`;
  }
  function operatorHelpHistory(snapshot) {
    const seen=new Set();
    const rows=snapshot.requests.filter(r=>{if(seen.has(r.request_id))return false;seen.add(r.request_id);return true;}).sort((a,b)=>(a.created_at || 0)-(b.created_at || 0));
    const thread=rows.map(request=>{
      const attempt=snapshot.attempts.find(a=>a.attempt_id===request.attempt_id),response=snapshot.responses?.[request.response_id];
      const k=attempt?attempt.index+1:null;
      const seconds=response?.created_at!=null && request.created_at!=null ? Math.max(0,Math.round(response.created_at-request.created_at)) : null;
      const author=response?.responder==='advisor_proxy'?'Advisor':response?.responder==='human'?'You':'Operator';
      return `<li data-operator-request="${esc(request.request_id)}"><div><b>${k==null?'Before the first attempt':`After attempt ${k}`}</b><small>${response?seconds==null?'Answered':`Answered in ${humanDuration(seconds)}`:request.state==='pending'?'Waiting for a reply…':'Closed without a reply'}</small></div><div><p class="op-asked">Asked: "${k==null?'The robot needs help':`Attempt ${k} failed`}. How should I fix it?"</p>${response?`<span class="op-who">${author}</span><div class="op-clamp" data-operator-copy>${readableReply(response)}</div><button type="button" class="op-more" data-operator-expand="Read full reply">Read full reply</button>`:request.state==='pending'?'<p class="op-quiet">No reply yet.</p>':'<p class="op-quiet">This request was closed before anyone answered it.</p>'}</div></li>`;
    }).join('');
    const hint=snapshot.dev_session && !snapshot.dev_v2 && snapshot.attention_control?.hint;
    const devReply=hint?.content?`<li><div><b>After attempt ${Math.max(1,hint.version-1)}</b><small>${hint.answered_at != null && hint.created_at != null ? `Answered in ${humanDuration(hint.answered_at-hint.created_at)}`:'Answered'}</small></div><div><p class="op-asked">Asked: "Attempt ${Math.max(1,hint.version-1)} failed. How should I fix it?"</p><span class="op-who">You</span><div class="op-clamp" data-operator-copy>${esc(hint.content)}</div><button type="button" class="op-more" data-operator-expand="Read full reply">Read full reply</button></div></li>`:'';
    return thread||devReply?`<ul class="op-thread">${thread}${devReply}</ul>`:'';
  }
  function operatorHelp(snapshot,chosen) {
    const end=snapshot.dev_session&&devEndSummary(snapshot.dev_session);
    if(end?.key==='action_approval')return `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" aria-label="Help and replies">${operatorHeading('04','HELP &amp; REPLIES','Do I need to do anything?')}<div class="op-action"><h4>Action permission closed</h4><p>${esc(end.explanation)}. This action was not executed.</p>${end.review?'<p>Review required · Unresolved work and any unknown paid holds remain recorded.</p>':''}</div>${operatorHelpHistory(snapshot)}</section>`;
    if(snapshot.dev_v2?.lifecycle?.phase==='approval')return `<section class="panel detail ab-current-request" data-core-region="request" data-state="waiting" aria-label="Help and replies">${operatorHeading('04','HELP &amp; REPLIES','Do I need to do anything?')}<div class="op-action need"><h4>A robot action needs your decision</h4><p>${esc(operatorLifecycleCopy(snapshot).text)}</p></div>${operatorHelpHistory(snapshot)}</section>`;
    const waiting=operatorWaiting(snapshot),s=snapshot.dev_session,control=snapshot.attention_control;
    const dev=s && !snapshot.dev_v2;
    const hint=dev?control?.hint:chosen;
    const k=operatorLast(snapshot)?.index+1 || 1;
    const trace=Object.values(snapshot.traces || {}).find(t=>t.attempt_id===operatorLast(snapshot)?.attempt_id);
    const failure=plainError(dev?hint?.feedback?.public_error:executionFailure(trace)?.message);
    const placeholder='Write a short tip in plain words, e.g. "Use smaller steps when moving the arm toward the cup."';
    const prompt=`Attempt ${k} failed: ${failure}. What should it do differently?`;
    const key=dev && hint?`${s.session_id}:${hint.request_id}`:'';
    const actions=dev?`<form data-dev-guidance data-request-id="${esc(hint?.request_id)}">${operatorQuickReplies()}<label class="op-guidance-label">Your reply<textarea name="content" maxlength="2000" required placeholder="${esc(placeholder)}">${esc(stored('attention.dev-guidance:'+key)||'')}</textarea></label><div class="op-controls"><button class="ab-button-primary" type="submit" value="answer" ${devGuidancePending.has(key)?'disabled':''}>Send reply</button><small>Watch the replay in 03 before replying</small></div><p role="status" data-dev-guidance-message></p></form>`:chosen?.request_type==='hint'?`<div class="ab-response-form">${operatorQuickReplies()}<label for="ab-hint-content">Your reply</label><textarea id="ab-hint-content" maxlength="4000" placeholder="${esc(placeholder)}"></textarea>${snapshot.run.robot?.execution_arm==='both'?'<div data-hint-scope><label>Target hand<select data-hint-arm aria-label="Hint target hand"><option value="left">Left hand</option><option value="right">Right hand</option><option value="both">Both hands</option></select></label><label>Task phase<select data-hint-phase aria-label="Hint task phase"><option value="pickup">Pickup</option><option value="handoff">Handoff</option><option value="placement">Placement</option></select></label></div>':''}<div class="op-controls"><button class="ab-button-primary" type="button" data-respond="hint">Send reply</button><small>Watch the replay in 03 before replying</small></div></div>`:chosen?.request_type==='approval'?'<div class="op-controls"><button type="button" data-respond="approve">Approve</button><button type="button" data-respond="deny">Deny</button></div>':'<button type="button" data-respond="interrupt">Confirm interrupt request</button>';
    return `<section class="panel detail ab-current-request" data-core-region="request" data-state="${waiting?'waiting':'idle'}" aria-label="Help and replies">${operatorHeading('04','HELP &amp; REPLIES','Do I need to do anything?')}<div class="op-action ${waiting?'need':'none'}"><h4>${waiting?'The robot needs your help':'✓ Nothing to do'}</h4>${waiting?`<p>${esc(prompt)}</p>${!dev&&chosen?.deadline_at!=null?`<p class="ab-countdown" data-reply-countdown data-deadline="${chosen.deadline_at}">${esc(countdown(chosen))}</p>`:''}${actions}`:`<p>${operatorLifecycleCopy(snapshot)?.text || (isTerminal(snapshot)?'This run has ended. The help the robot received is listed below.':"The robot is working on its own. You'll be asked here if it needs help.")}</p>`}</div>${operatorHelpHistory(snapshot)}</section>`;
  }

  function operatorBudgetCard(name,value,seconds=false,ended=false,showZero=false) {
    if (!value || (value.used==null && value.limit==null) || (Number(value.limit)===0 && !Number(value.used) && !showZero)) return '';
    const used=value.used==null?null:Number(value.used),limit=value.limit==null?null:Number(value.limit),out=value.budgeted!==false && used!=null && limit!=null && limit>=0 && used>=limit;
    const warn=!out && used!=null && limit>0 && used/limit>=0.7;
    const format=v=>v==null?'—':seconds?operatorTime(v):number(v);
    return `<div class="op-meter${out?' out':warn?' warn':''}" data-fill="${limit>0&&used!=null?Math.min(100,Math.round(used/limit*100)):0}"><span>${name}</span><b><em data-count-to="${used==null?'':used}" data-count-seconds="${seconds?'1':''}">${format(used)}</em> <small>/ ${format(limit)}</small></b><div class="op-budget-track"><i style="width:${pct(used,limit)}%"></i></div>${out?`<small>Used up${ended?' · this ended the run':''}</small>`:''}</div>`;
  }
  function operatorResources(snapshot) {
    const resources=snapshot.resources,s=snapshot.dev_session;
    const attempts=s?resources.executions:{used:snapshot.attempts.length,limit:snapshot.task_plan?.max_attempts ?? launches.find(l=>l.run_id===snapshot.run.run_id)?.locked?.max_attempts};
    const endedKey=s?devEndSummary(s).key:snapshot.eval?.stopped_reason;
    const fields=[['executions','Attempts',attempts],['eval_calls','Reviews requested',snapshot.dev_eval_enabled?resources.eval_calls:null],['help_count','Help requests',resources.help_count || resources.assistance || (snapshot.attention_control?.hint_limit != null?{used:snapshot.attention_control.hint_count,limit:snapshot.attention_control.hint_limit}:null)],['model_calls','AI plan rewrites',s?(resources.model_calls || resources.dev_calls):null],['execution_seconds','Robot run time',resources.execution_seconds,true],['wall_seconds','Total time',resources.wall_seconds || (!s?{used:null,limit:null}:null),true]];
    const kFormat=value=>value==null?'—':Number(value)>=1000?`${Math.round(Number(value)/100)/10}k`:number(value);
    const tokens=resources.tokens;
    const textOnlyCount=textOnlyReviews(snapshot).length;
    const reviewNote=snapshot.dev_eval_enabled && textOnlyCount?`<strong class="op-warn-text" data-text-only-note>${textOnlyCount} HumanProxy answer${textOnlyCount===1?' was':'s were'} text-only (Qwen unavailable)</strong> `:'';
    const ledger=snapshot.dev_v2?.ledger;
    const exportLink=snapshot.dev_v2?`<a href="/api/dev-v2/experiments/${encodeURIComponent(snapshot.dev_v2.experiment_id)}/export.csv" target="_blank" rel="noopener">Download data (CSV)</a>`:'';
    // Wall-clock time keeps running after a run has ended; it only matters when it is why the run ended.
    const shown=fields.filter(([key])=>!(key==='wall_seconds' && isTerminal(snapshot) && endedKey!=='wall_seconds'));
    return `<section class="panel ab-resource-details" data-resource-details data-resources-run="${esc(snapshot.run.run_id)}" aria-label="Resources">${operatorHeading('05','RESOURCES','How much budget is left?')}<div class="op-meters">${shown.map(([key,name,value,seconds])=>value?`${s?`<div data-dev-budget-key="${key}">`:''}${operatorBudgetCard(name,value,seconds,isTerminal(snapshot) && (endedKey===key || (key==='executions' && endedKey==='max_attempts')),key==='eval_calls')}${s?'</div>':''}`:'').join('')}</div><div class="op-budget-foot"><span>${reviewNote}${tokens && Number(tokens.limit)>0?`AI usage: ${kFormat(tokens.used)} of ${kFormat(tokens.limit)} tokens`:''}</span><span>${ledger?.recompute_matches===false?'<strong class="op-data-failure">Data check failed · tell the research team</strong> ':''}${exportLink}</span></div></section>`;
  }

  // End operator presentation.

  function runSettings(snapshot = null, launch = null) {
    const title = '<h3>Task & Run Settings</h3>';
    if (!snapshot && !DEBUG_VIEW) return `<section class="panel ab-run-settings" data-run-settings>${operatorHeading('01','TASK SETUP','What is the robot trying to do?')}<p>Use New run to choose a task.</p></section>`;
    if (!snapshot) return `<section class="panel ab-run-settings" data-run-settings aria-label="Task & Run Settings"><div class="panel-title">${title}</div><p class="minor">${launch ? 'Launch request accepted; waiting for the executor to register this run.' : 'No run selected. Use New run to configure the task, assistance mode and budgets.'}</p></section>`;
    const run = snapshot.run;
    // Describe known execution mechanisms in user language without inventing a model.
    const developerActivity = snapshot.dev_session ? 'Automatic policy iteration' : new Map([['formal-generated-policy','Using an approved policy'],
      ['trusted-policy-callback','Using a supplied policy'], ['scripted','Using a scripted policy']]).get(run.developer_model);
    const nativeEvaluator = run.evaluator_model === 'native_evaluator';
    const evaluatorModel = snapshot.eval?.model || (nativeEvaluator ? null : run.evaluator_model);
    const field = (value, attr) => value ? `<dl class="ab-agent-fields"><div><dt>Model</dt><dd class="model-tag" ${attr}>${esc(value)}</dd></div></dl>` : '';
    const developerFields = developerActivity && !snapshot.dev_session ? '' : field(run.developer_model, 'data-developer-model');
    const evaluatorFields = field(evaluatorModel, 'data-evaluator-model');
    const settings = [['Task',taskDisplayName(run)],['Benchmark',{robocasa365:'RoboCasa365',robocasa:'RoboCasa',robosuite:'Robosuite'}[run.suite] || run.suite],
      ['Attention policy',policyTitle(snapshot.attention_control?.active_policy||run.policy_id)],['Execution target',label(run.execution_target)],
      ['Assistance mode',label(run.assistance_mode)]];
    if (snapshot.dev_session) { settings.splice(2,1,['Iteration mode','Automatic policy revision']); settings.pop(); }
    if (['robocasa','robocasa365'].includes(run.suite)) settings.splice(2,0,
      ['Robot',run.robot?.model || 'Not recorded'],
      ['Execution mode',run.robot?.execution_arm === 'left' ? 'Left only' : run.robot?.execution_arm === 'right' ? 'Right only' : run.robot?.execution_arm === 'both' ? 'Both arms' : run.robot?.execution_arm === 'single' ? 'Single arm' : 'Not recorded']);
    const base = run.robot_configuration?.base || run.base;
    if (['mobile_base_joint_drive','mujoco_joint_driven_planar_base','official_hybrid_mobile_base'].includes(base?.capability)) settings.push(['Base','Mobile']);
    else if (base?.capability === 'stationary') settings.push(['Base','Stationary']);
    else if (run.suite === 'robocasa' && run.robot?.model === 'OpenArm' && !base) settings.push(['Base','Fixed mount']);
    const technical = [['Full record ID',run.run_id],['Seed',run.seed]];
    if (snapshot.dev_session) technical.push(['Layout / style',`${snapshot.dev_session.locked.config.layout_id} / ${snapshot.dev_session.locked.config.style_id}`],['Session',snapshot.dev_session.session_id],['Formal eligible','false']);
    if (run.robot_configuration) {
      if (run.robot_configuration.controller != null) technical.push(['Controller',run.robot_configuration.controller]);
      if (run.robot_configuration.config_sha256 != null) technical.push(['Configuration SHA-256',run.robot_configuration.config_sha256]);
    }
    if (base?.model != null) technical.push(['Base model',base.model]);
    if (base?.capability != null) technical.push(['Base capability',base.capability]);
    const details = `<details class="ab-run-details" data-run-details data-persist-detail="run-details:${esc(run.run_id)}"><summary>Run details<span>Seed · technical configuration</span></summary><dl>${technical.map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl></details>`;
    const lockIcon = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 7V5a3 3 0 0 1 6 0v2M4 7h8v7H4zM8 10v1"/></svg>';
    return `${DEBUG_VIEW ? `<section class="panel ab-run-settings" data-run-settings data-settings-run="${esc(run.run_id)}" aria-label="Task & Run Settings">
      <div class="panel-title">${title}<span class="ab-settings-state" aria-label="Configuration status">${run.locked ? lockIcon : ''}${run.locked ? 'Configuration locked' : 'Not locked yet'}</span></div>
      <div class="ab-settings-grid">${parameterDecks(settings)}</div>
      ${details}
      <div class="ab-settings-agents" data-settings-agents aria-label="Developer and evaluator">
        ${armStatus(snapshot)}<div class="agent-row" data-agent-role="developer"><span class="agent-icon" aria-hidden="true">DEV</span><div class="agent-info"><b>Developer</b><small>${esc(developerActivity || (run.developer_model ? 'Policy development' : 'Model information unavailable'))}</small>${developerFields}</div></div>
        <div class="agent-row eval" data-agent-role="evaluator"><span class="agent-icon" aria-hidden="true">EVAL</span><div class="agent-info"><b>Evaluator</b><small>${snapshot.dev_session?'Independent native task evaluation':esc(diagnosticStatus(snapshot.eval?.status))}</small>${snapshot.eval?.status === 'failed' ? '<small data-diagnostic-note>Diagnosis only; the native task outcome is reported separately.</small>' : ''}${evaluatorFields}</div></div>
      </div></section>` : operatorSettings(snapshot)}`;
  }
  function parameterDecks(settings) {
    const decks = [
      ['task', ['Task', 'Benchmark']],
      ['execution', ['Robot', 'Execution mode', 'Base', 'Execution target']],
      ['policy', ['Attention policy', 'Assistance mode', 'Iteration mode']],
    ];
    return decks.map(([name, keys]) => {
      const fields = keys.map(key => settings.find(field => field[0] === key)).filter(Boolean);
      if (!fields.length) return '';
      return `<dl class="ab-param-group ab-param-${name}" style="--param-columns:${fields.length}">${fields.map(([key,value]) =>
        `<div data-setting="${esc(key)}"${key === 'Task' ? ' class="ab-setting-task"' : ''}><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
    }).join('');
  }
  function devV2LedgerDetails(snapshot) {
    const c=snapshot.dev_v2,g=c.ledger.goal,base=`/api/dev-v2/experiments/${encodeURIComponent(c.experiment_id)}`;
    return `<details data-persist-detail="v2-ledger:${esc(c.key)}"><summary>Experiment ledger & exports</summary><p>${esc(c.mode==='interactive'?'Interactive session · excluded from comparison':'Frozen experimental policy')} · Development only</p><p>Goal: <code>${esc(c.ledger.goal_id)}</code> · Experiment: <code>${esc(c.experiment_id)}</code></p><p><a href="${base}/export.json" target="_blank">Export JSON</a> · <a href="${base}/export.csv" target="_blank">Export CSV</a> · <a href="${base}/ledger.json" target="_blank">Resource ledger</a></p><p>Recomputed ledger: ${c.ledger.recompute_matches?'matches':'MISMATCH · review required'}</p><pre>${esc(JSON.stringify({limits:g.limits,used:g.used,reserved:g.reserved},null,2))}</pre></details>`;
  }

  // Attention efficiency: task progress per unit of human attention.
  // Progress is the native pass/fail of each attempt (no partial credit is recorded).
  // Attention is the time between a help request and a person's reply, plus the logical credit each reply costs.
  function attentionLedger(snapshot) {
    const attempts=[...snapshot.attempts].sort((a,b)=>a.index-b.index);
    const succeeded=a=>snapshot.eval?.native_outcomes?.[a.attempt_id]?.native_success===true||a.status==='succeeded';
    const solved=attempts.find(succeeded)||null;
    const rows=[], control=snapshot.attention_control, dev=snapshot.dev_session;
    if (dev && !snapshot.dev_v2) {
      const events=control?.events||[];
      for (const e of events.filter(x=>x.kind==='hint_requested')) {
        const closed=events.find(x=>x.request_id===e.request_id && /^hint_(answered|skipped)$/.test(x.kind));
        rows.push({id:e.request_id,kind:'hint',reason:control?.hint?.request_id===e.request_id?control.hint.reason:null,requestedAt:e.at,answeredAt:closed&&closed.kind==='hint_answered'?closed.at:null,state:closed?closed.kind.slice(5):'pending',responder:'human',nextIndex:e.version});
      }
    } else if (dev) {
      for (const job of (dev.jobs||[]).filter(j=>['advisor','demo','cache'].includes(j.kind)).sort((a,b)=>a.version-b.version))
        rows.push({id:job.job_id||`${job.kind}:${job.version}`,kind:job.kind==='demo'?'demo':'advisor',reason:null,requestedAt:null,answeredAt:null,state:job.state==='settled'?'answered':'pending',responder:'advisor',nextIndex:job.version-1});
    } else {
      for (const r of snapshot.requests||[]) {
        const response=snapshot.responses?.[r.response_id], linked=snapshot.response_evidence?.[r.request_id]?.association?.attempt_id;
        const from=attempts.find(a=>a.attempt_id===r.attempt_id), next=attempts.find(a=>a.attempt_id===linked);
        rows.push({id:r.request_id,kind:r.request_type||'hint',reason:r.reason,requestedAt:r.created_at,answeredAt:response?.created_at??null,state:response?'answered':r.state,responder:response?.responder==='human'?'human':response?'advisor':(r.mode==='live_human_first'?'human':'advisor'),nextIndex:next?next.index:from?from.index+1:null});
      }
    }
    const now=Date.now()/1000;
    for (const row of rows) {
      row.seconds=row.responder==='human'&&row.requestedAt!=null?(row.answeredAt!=null?Math.max(0,row.answeredAt-row.requestedAt):row.state==='pending'?Math.max(0,now-row.requestedAt):0):0;
      const next=attempts.find(a=>a.index===row.nextIndex);
      row.effect=row.state!=='answered'?(row.state==='pending'?'awaiting_reply':'no_reply'):!next?'waiting':succeeded(next)?'fixed':next.status==='running'?'running':'still_failed';
    }
    const humanSeconds=rows.reduce((t,r)=>t+r.seconds,0);
    const humanRows=rows.filter(r=>r.responder==='human'&&r.state==='answered').length;
    const credits=snapshot.resources?.help_count||snapshot.resources?.assistance||null;
    const judged=rows.filter(r=>r.effect==='fixed'||r.effect==='still_failed');
    const maximum=snapshot.dev_session?.budget?.executions??snapshot.task_plan?.max_attempts??attempts.length;
    return {rows,attempts,solved,attemptsRun:attempts.length,maximum,humanSeconds,humanRows,requests:rows.length,
      credits:credits&&credits.used!=null?credits:{used:rows.length,limit:null},hits:rows.filter(r=>r.effect==='fixed').length,judged:judged.length,
      anyHuman:rows.some(r=>r.responder==='human'),terminal:isTerminal(snapshot)||(dev&&['stopped','budget_exhausted','failed','needs_review','completed','cancelled'].includes(dev.state))};
  }
  function attentionTime(seconds) {
    const v=Math.max(0,Number(seconds)||0);
    return v<60?`${Math.round(v)}s`:operatorTime(v);
  }
  function attentionFrontier(snapshot) {
    const policies=snapshot.comparison?.policies||[];
    if (!policies.length) return '';
    const useTime=policies.some(p=>p.average_human_seconds>0), key=useTime?'average_human_seconds':'average_assistance_used';
    const xmax=Math.max(1,...policies.map(p=>Number(p[key])||0))*1.15, W=560, H=230, L=48, R=16, T=14, B=40;
    const x=v=>L+(Number(v)||0)/xmax*(W-L-R), y=v=>T+(1-v)*(H-T-B);
    const active=snapshot.attention_control?.active_policy||snapshot.run.policy_id;
    const dots=policies.map(p=>{
      const cx=x(p[key]),cy=y(p.success_rate),on=p.policy_id===active;
      return `<g class="op-frontier-pt${on?' active':''}"><circle cx="${cx}" cy="${cy}" r="${on?8:6}"/><text x="${cx+(cx>W-120?-12:12)}" y="${cy+4}" text-anchor="${cx>W-120?'end':'start'}">${esc(policyTitle(p.policy_id))} · ${Math.round(p.success_rate*100)}% (${p.successes}/${p.runs})</text></g>`;
    }).join('');
    const grid=[0,.5,1].map(v=>`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L-8}" y="${y(v)+4}" text-anchor="end">${v*100}%</text>`).join('');
    const ticks=[0,.5,1].map(f=>`<text x="${x(xmax*f)}" y="${H-20}" text-anchor="middle">${useTime?attentionTime(xmax*f):number(xmax*f)}</text>`).join('');
    return `<div class="op-frontier" data-attention-frontier><h4>Success vs. human attention <small>across finished runs of this task · each dot is one help strategy</small></h4><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Success rate against average ${useTime?'human time':'help requests'} per run"><g class="op-frontier-grid">${grid}</g>${ticks}<text x="${(L+W-R)/2}" y="${H-3}" text-anchor="middle" class="op-frontier-axis">${useTime?'Average human time per run':'Average help requests per run'} →</text>${dots}</svg>${snapshot.comparison.comparable?'':'<p class="minor">These strategies were not run on the same seeds, so read the chart as a rough guide, not a fair comparison.</p>'}</div>`;
  }
  function attentionEfficiency(snapshot) {
    const g=attentionLedger(snapshot);
    const state=g.solved?'solved':g.terminal?'unsolved':'running';
    const progress={solved:'Solved ✓',unsolved:'Not solved',running:'In progress'}[state];
    const progressNote=g.solved?`on attempt ${g.solved.index+1} of ${g.maximum||'?'}`:g.attemptsRun?`${g.attemptsRun} attempt${g.attemptsRun===1?'':'s'} so far`:'no attempt yet';
    const spent=g.humanSeconds>0||g.anyHuman?attentionTime(g.humanSeconds):'0s';
    const who=!g.requests?'no help requested':g.anyHuman?`${g.requests} request${g.requests===1?'':'s'} · ${g.humanRows} answered by a person`:`${g.requests} request${g.requests===1?'':'s'} · answered by the automatic advisor`;
    let rate, rateNote;
    if (g.solved && !g.requests) { rate='No help needed'; rateNote='solved with zero human attention'; }
    else if (g.solved && g.humanSeconds>0) { rate=`${(60/g.humanSeconds).toFixed(g.humanSeconds<6?0:1)}`; rateNote='tasks solved per human-minute'; }
    else if (g.solved) { rate=`1 per ${g.requests}`; rateNote=`task solved per help request${g.requests===1?'':'s'}`; }
    else { rate='0'; rateNote=g.requests?`tasks solved · ${g.anyHuman?spent+' of human time':g.requests+' help request'+(g.requests===1?'':'s')} spent`:'tasks solved so far'; }
    const hit=g.judged?`${g.hits} / ${g.judged}`:'—';
    const effect={fixed:['✓ Next attempt succeeded','good'],still_failed:['✕ Next attempt still failed','bad'],running:['● Next attempt running','mid'],waiting:['… Waiting for the next attempt','mid'],awaiting_reply:['● Waiting for a reply','mid'],no_reply:['— No reply given','mid']};
    const kind={hint:'Hint',approval:'Approval',interrupt:'Interrupt',advisor:'Automatic advisor',demo:'Example'};
    const table=g.rows.length?`<table class="op-attn-table" data-attention-requests><thead><tr><th>#</th><th>Help</th><th>Why it was asked</th><th>Attention spent</th><th>Effect</th></tr></thead><tbody>${g.rows.map((r,i)=>`<tr><td>${i+1}</td><td>${esc(kind[r.kind]||label(r.kind))}<small>${r.responder==='human'?'person':'automatic'}</small></td><td>${esc(r.reason?label(r.reason):'—')}</td><td>${r.responder==='human'?`${attentionTime(r.seconds)}${r.state==='pending'?' · waiting':''}`:'1 credit'}</td><td class="${effect[r.effect][1]}">${effect[r.effect][0]}</td></tr>`).join('')}</tbody></table>`:`<p class="op-attn-empty">${g.terminal?'No help was requested in this run.':'No help has been requested yet. Once the robot asks, each request shows up here with the attention it cost and what happened next.'}</p>`;
    const credits=Number(g.credits.limit)>0?` · credits ${g.credits.used} / ${g.credits.limit}`:'';
    return `<div class="op-attention" data-attention-efficiency data-attention-state="${state}" aria-label="Attention efficiency"><h4>Attention efficiency <small>Was the human attention worth it?</small></h4>
      <div class="op-attn-kpis"><div class="op-attn-kpi"><span>Task progress</span><b data-kpi="progress">${progress}</b><small>${esc(progressNote)}</small></div>
      <div class="op-attn-kpi"><span>Human attention spent</span><b data-kpi="spent">${esc(spent)}</b><small>${esc(who)}${esc(credits)}</small></div>
      <div class="op-attn-kpi headline"><span>Progress per attention</span><b data-kpi="rate">${esc(rate)}</b><small>${esc(rateNote)}</small></div>
      <div class="op-attn-kpi"><span>Help that paid off</span><b data-kpi="hit">${hit}</b><small>${g.judged?'requests followed by a solved attempt':'no help has been judged yet'}</small></div></div>
      <details class="op-attn-more" data-persist-detail="attention-efficiency"><summary>Each help request and how strategies compare</summary>${table}${attentionFrontier(snapshot)}
      <p class="op-attn-foot">Progress per attention = tasks solved ÷ human attention. Attention is the time from a help request to a person's reply; automatic-advisor replies count as credits, not human time. Task progress is pass/fail per attempt; no partial credit is recorded.</p></details></div>`;
  }

  function resourceDetails(snapshot = null) {
    const title = '<div class="panel-title"><h3>Resources</h3><span>Run usage / total budget</span></div>';
    if (snapshot?.dev_session) {
      const fields = [['executions','Executions',''],['model_calls','Model calls',''],['tokens','Tokens',''],['sdk_calls','SDK calls',''],['execution_seconds','Execution time','s'],['wall_seconds',snapshot.dev_v2?'Goal wall time':'Session time','s']];
      return `${DEBUG_VIEW ? `<section class="panel ab-resource-details" data-resource-details data-resources-run="${esc(snapshot.run.run_id)}" aria-label="Resources">${title}<div class="budgets" aria-label="Resource budget">${(snapshot.dev_v2?[['help_count','Help requests',''],...(snapshot.dev_eval_enabled?[['eval_calls','Reviews requested','']]:[]),...fields]:fields).map(([key,name,unit])=>`<div data-dev-budget-key="${key}" data-limit-reached="${snapshot.dev_session.state==='budget_exhausted'&&devEndSummary(snapshot.dev_session).key===key}">${budgetCard(name,snapshot.resources[key],unit)}</div>`).join('')}</div>${snapshot.dev_v2?devV2LedgerDetails(snapshot):''}</section>` : operatorResources(snapshot)}`;
    }
    if (!snapshot && !DEBUG_VIEW) return `<section class="panel ab-resource-details" data-resource-details>${operatorHeading('05','RESOURCES','How much budget is left?')}<p>Choose a run to see its resources.</p></section>`;
    if (!snapshot) return `<section class="panel ab-resource-details" data-resource-details aria-label="Resources">${title}<p class="minor">Resource usage and remaining budgets appear after run registration.</p></section>`;
    return `${DEBUG_VIEW ? `<section class="panel ab-resource-details" data-resource-details data-resources-run="${esc(snapshot.run.run_id)}" aria-label="Resources">${title}<div class="budgets" aria-label="Resource budget">${budgetCard('Assistance credits',snapshot.resources.assistance,'',true)}${budgetCard('Execution time',snapshot.resources.execution_seconds,'s')}${budgetCard('Tokens',snapshot.resources.tokens,'')}${budgetCard('GPU time',snapshot.resources.gpu_seconds,'s')}</div>${budgetHistory(snapshot)}</section>` : snapshot ? operatorResources(snapshot) : `<section class="panel ab-resource-details" data-resource-details>${operatorHeading("05","RESOURCES","How much budget is left?")}<p>Choose a run to see its resources.</p></section>`}`;
  }
  function secondaryPanel(snapshot) {
    const agentRecords = snapshot ? `<section class="panel" data-agent-debug><h3>Agent records</h3><dl class="ab-settings-list"><dt>Developer record</dt><dd><code>${esc(snapshot.run.developer_model || 'Not recorded')}</code></dd><dt>Evaluator record</dt><dd><code>${esc(snapshot.run.evaluator_model || 'Not recorded')}</code></dd><dt>Diagnostic model</dt><dd><code>${esc(snapshot.eval?.model || 'Not recorded')}</code></dd></dl></section>` : '';
    return `<aside class="ab-secondary" aria-label="Developer tools"><details data-secondary="debug" data-persist-detail="debug"><summary>Developer debug</summary>${agentRecords}${snapshot ? `<section class="panel"><h3>Agent work & scheduling</h3><div class="work-list">${(orchestrator?.agents || []).map(agent=>`<button class="work-card" type="button" data-dag-skill="${esc(agent.skill)}"><strong>${esc(agent.skill)}</strong><p>${esc(agent.agent_id)} · ${esc(agent.model)} · ${esc(agent.status)}</p></button>`).join('')}${(snapshot.work_items || []).map(item=>`<div class="work-card"><strong>${esc(label(item.resource))} · ${esc(item.state)}</strong><p>Requires ${esc(number(item.required))}, remaining ${esc(number(item.remaining))} · ${esc(item.reason)}</p></div>`).join('') || '<p>No active work records.</p>'}</div></section>` : ''}${dagPanel()}${sessionPanel()}<p class="minor">The live projection reads the persisted SQLite AttentionStore. Requests, budgets, executions and traces survive restarts.</p></details></aside>`;
  }
  function render(snapshot) {
    stopGuidancePreview(); activeGuidancePreview = null;
    const devDraft=view.querySelector('[data-dev-guidance] textarea');
    const devDraftFocus=devDraft&&document.activeElement===devDraft?[devDraft.selectionStart,devDraft.selectionEnd,devDraft.selectionDirection]:null;
    const previousRun = currentSnapshot?.run?.run_id;
    const overviewFocus=document.activeElement?.hasAttribute("data-overview-toggle")?"[data-overview-toggle]":document.activeElement?.hasAttribute("data-overview-drag")?"[data-overview-drag]":null;
    const activeGraph=document.activeElement;
    const graphFocus=activeGraph?.dataset.flowId?['data-flow-id',activeGraph.dataset.flowId]:
      activeGraph?.dataset.skillNode?['data-skill-node',activeGraph.dataset.skillNode]:null;
    const graphScroll = [...view.querySelectorAll('.ab-flow-scroll')].map(e=>[e.scrollLeft,e.scrollTop]);
    const expandedDetails = [...view.querySelectorAll('[data-persist-detail][open]')].map(e => e.dataset.persistDetail);
    const focusedDetail = view.contains(document.activeElement) && document.activeElement?.tagName === 'SUMMARY'
      ? detailIdentity(document.activeElement.parentElement) : null;
    const oldInput = view.querySelector('#ab-hint-content');
    if (oldInput?.dataset.draftKey) {
      draftCache.set(oldInput.dataset.draftKey, oldInput.value);
      stored(oldInput.dataset.draftKey, oldInput.value);
    }
    const hintSelectFocus=document.activeElement?.matches?.('[data-hint-arm],[data-hint-phase]')
      ? document.activeElement.hasAttribute('data-hint-arm')?'arm':'phase':null;
    const focused = document.activeElement === oldInput && oldInput;
    const selection = focused ? [oldInput.selectionStart, oldInput.selectionEnd, oldInput.selectionDirection]
      : deferredSelection && deferredSelection.key === oldInput?.dataset.draftKey ? deferredSelection.range : null;
    deferredSelection = null;
    const expandedEvents = [...view.querySelectorAll('[data-history-event][open]')].map(e => e.querySelector('summary')?.textContent);
    const expandedBudgetRun = view.querySelector('[data-budget-history][open]')?.dataset.budgetRun;
    const expandedEvalRun = view.querySelector('[data-eval-details][open]')?.dataset.evalRun;
    saveReplayPositions();
    const videos = new Map([...view.querySelectorAll('video')].map(video => [video.dataset.replayKey, video]));
    const previousPreview = view.querySelector('#ab-camera-frame[data-live-stream-key]');
    currentSnapshot = snapshot;
    devTag.hidden = !snapshot.dev_session || DEBUG_VIEW;
    renderSidebar();
    const run = snapshot.run;
    const savedRun=previousRun!==run.run_id?runViewState.get(run.run_id):null;
    if(savedRun?.request)selectedRequest=savedRun.request;
    const sortedRequests = [...snapshot.requests].sort((a, b) => Number(b.created_at) - Number(a.created_at));
    if (!sortedRequests.some(item => item.request_id === selectedRequest)) {
      selectedRequest = sortedRequests.find(item => item.state === 'pending')?.request_id || sortedRequests[0]?.request_id || null;
    }
    const active = actionableRequests(snapshot);
    const chosen = active.find(item => item.request_id === selectedRequest) || active[0];
    view.innerHTML = `${runSettings(snapshot)}${runOverview(snapshot)}${executionViewer(snapshot)}${requestPanel(snapshot,chosen)}${resourceDetails(snapshot)}<p class="minor" id="ab-live-message" aria-live="polite"></p>`;
    if(snapshot.dev_v2?.planner){
      const region=view.querySelector('[data-core-region="request"]');
      region?.insertAdjacentHTML('beforeend',plannerPanel(snapshot));
      const pending=snapshot.dev_v2.planner.requests.some(r=>r.request_type==='approval'&&r.state==='waiting');
      if(pending&&region){region.dataset.state='waiting';const action=region.querySelector('.op-action');if(action){action.className='op-action need';action.innerHTML='<h4>A robot action needs your decision</h4><p>Review the exact source and bounded permission below. Recorded public preparation is available below; the action remains blocked.</p>';}}
    }
    bindAttentionPanels(snapshot, chosen);
    bindPlanner(snapshot);
    mountPolicyController();
    if (snapshot.dev_session) view.querySelector('[data-execution-viewer]').insertAdjacentHTML('beforeend',`${DEBUG_VIEW ? devVersionHistory(snapshot.dev_session) : ''}`);
    if (previousRun === run.run_id) view.querySelectorAll('.ab-flow-scroll').forEach((e,i)=>{if(graphScroll[i]){e.scrollLeft=graphScroll[i][0];e.scrollTop=graphScroll[i][1];}});
    bindTaskFlow(snapshot);
    view.querySelectorAll('[data-operator-attempt]').forEach(button=>button.addEventListener('click',()=>{stored(`attention.history:${snapshot.run.run_id}`,button.dataset.operatorAttempt);render(currentSnapshot);view.querySelector('[data-execution-viewer]')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}));
    view.querySelectorAll('[data-operator-help]').forEach(button=>button.addEventListener('click',()=>view.querySelector('[data-core-region="request"]')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})));
    if(previousRun===run.run_id&&graphFocus) {
      [...view.querySelectorAll(`[${graphFocus[0]}]`)].find(e=>e.getAttribute(graphFocus[0])===graphFocus[1])?.focus({preventScroll:true});
    }
    if (previousRun === run.run_id) view.querySelectorAll('[data-persist-detail]').forEach(e => {
      e.open = expandedDetails.includes(e.dataset.persistDetail);
    });
    view.querySelectorAll('[data-history-event]').forEach(e => { if (expandedEvents.includes(e.querySelector('summary')?.textContent)) e.open = true; });
    if (expandedBudgetRun === run.run_id) {
      const budgetDetails = view.querySelector('[data-budget-history]');
      if (budgetDetails) budgetDetails.open = true;
    }
    if (expandedEvalRun === run.run_id) {
      const evalDetails = view.querySelector('[data-eval-details]');
      if (evalDetails) evalDetails.open = true;
    }
    if (previousRun === run.run_id && focusedDetail) {
      [...view.querySelectorAll('details')].find(e => detailIdentity(e) === focusedDetail)?.querySelector(':scope > summary')?.focus({preventScroll:true});
    }
    const input = view.querySelector('#ab-hint-content');
    if (input && chosen) {
      const key = `attention.draft:${run.run_id}:${chosen.request_id}`;
      input.dataset.draftKey = key;
      input.value = draftCache.get(key) ?? stored(key) ?? '';
      input.addEventListener('input', () => { draftCache.set(key, input.value); stored(key, input.value); });
      for (const part of ['arm','phase']) {
        const select = view.querySelector(`[data-hint-${part}]`);if(!select)continue;
        const scopeKey=key+':scope:'+part, saved=stored(scopeKey);if(saved)select.value=saved;
        select.addEventListener('change',()=>stored(scopeKey,select.value));
      }
      if (selection && oldInput.dataset.draftKey === key) { input.focus(); input.setSelectionRange(...selection); }
      if(savedRun?.hint?.key===key){input.value=savedRun.hint.value;input.setSelectionRange(...savedRun.hint.range);if(savedRun.hint.focused)input.focus({preventScroll:true});}
      if(hintSelectFocus&&oldInput?.dataset.draftKey===key&&previousRun===run.run_id)view.querySelector(`[data-hint-${hintSelectFocus}]`)?.focus({preventScroll:true});
      bindGuidancePreview(snapshot,chosen,input);
    }
    view.querySelectorAll('[data-hint-preset]').forEach(button => button.addEventListener('click', () => {
      const draft = input || view.querySelector('[data-dev-guidance] textarea');
      if (!draft) return;
      draft.value = button.dataset.presetText;
      draft.dispatchEvent(new Event('input', {bubbles:true}));
      draft.focus();
    }));
    view.querySelectorAll('video').forEach(video => {
      const previous = videos.get(video.dataset.replayKey);
      if (previous) {
        if(previous.getAttribute('src')!==video.getAttribute('src')) {
          const time=previous.currentTime, paused=previous.paused, rate=previous.playbackRate;
          const selected=replaySelections.get(previous.dataset.replayKey);
          replaySelections.set(previous.dataset.replayKey,{...(selected||{}),seconds:time,pending:true,mode:selected?.mode||'cursor'});
          previous.dataset.replayRestoring='true';
          previous.pause(); previous.src=video.getAttribute('src'); previous.load(); previous.playbackRate=rate;
          const expected=previous.src;
          previous.addEventListener('seeked',()=>{if(previous.src===expected&&!paused)previous.play().catch(()=>{});},{once:true});
        }
        video.replaceWith(previous);
      }
    });
    for(const previous of videos.values())if(!previous.isConnected)previous.pause();
    bindReplayTimeline(snapshot);
    bindIndustrialReplayControls();
    bindOperatorPresentation();
    bindOperatorEnhancements(snapshot,previousRun!==run.run_id);
    const replayForPolicy=view.querySelector('video[data-history-replay]');if(replayForPolicy)updateReplayPolicy(snapshot,replayForPolicy);
    view.querySelectorAll('[data-policy-marker-seconds]').forEach(b=>b.addEventListener('click',()=>{const v=view.querySelector('video[data-history-replay]');if(v&&b.dataset.policyMarkerSeconds!==''){v.pause();v.currentTime=Number(b.dataset.policyMarkerSeconds);}}));
    view.querySelectorAll('[data-camera-view]').forEach(button=>{
      button.addEventListener('pointerdown',event=>event.preventDefault());
      button.addEventListener('click',()=>{cameraChoices.set(cameraContext(snapshot),button.dataset.cameraView);render(snapshot);});
    });
    view.querySelector('[data-overview-open-sidebar]')?.addEventListener('click',()=>{sidebarOpen=true;sidebarLayout();});
    bindDevGuidance(snapshot);
    if(devDraftFocus){const input=view.querySelector('[data-dev-guidance] textarea');if(input){input.focus({preventScroll:true});input.setSelectionRange(...devDraftFocus);}}
    refreshAttemptDag(snapshot);
    refreshFlowDetail(snapshot);
    refreshMoreDialog();
    if (chosen && pendingControls.has(chosen.request_id)) view.querySelectorAll('[data-respond], [data-defer-request], [data-cancel-request]').forEach(button => { button.disabled = true; });
    view.querySelector('#ab-live-run')?.addEventListener('change' , event => {
      dismissRequestAlert();
      selectedRun = event.target.value;
      selectedRequest = null;
      const url = new URL(location.href);
      url.searchParams.set('run', selectedRun);
      history.replaceState(null, '', url);
      lastRendered = '';
      refresh();
    });

    view.querySelector('[data-answer-current]')?.addEventListener('click', event => {
      selectedRequest = event.currentTarget.dataset.answerCurrent;
      render(currentSnapshot);
      const input = view.querySelector('#ab-hint-content');
      view.querySelector('.detail')?.scrollIntoView({block:'start'});
      input?.focus({preventScroll:true});
    });

    view.querySelectorAll('[data-history-select]').forEach(button => button.addEventListener('click', () => {
      stored(`attention.history:${run.run_id}`, button.dataset.historySelect);
      render(currentSnapshot);
      view.querySelector('[data-execution-viewer]')?.scrollIntoView({block:'start'});
    }));
    view.querySelectorAll('[data-request-replay]').forEach(link => link.addEventListener('click', event => {
      event.preventDefault();
      const attemptId = link.dataset.replayAttempt;
      if (!snapshot.attempts.some(a => a.attempt_id === attemptId)) return;
      stored(`attention.history:${run.run_id}`, attemptId);
      const index = Number(link.dataset.requestReplay);
      render(currentSnapshot);
      view.querySelector(`[data-execution-viewer] summary[data-replay-event="${index}"]`)?.click();
      view.querySelector('[data-execution-viewer]')?.scrollIntoView({block:'start'});
    }));
    const placeholder = view.querySelector('#ab-camera-frame[data-live-stream-key]');
    if (previousPreview && placeholder?.dataset.liveStreamKey === previousPreview.dataset.liveStreamKey) {
      placeholder.replaceWith(previousPreview);
    } else {
      if (previousPreview) stopPreviewFrames(previousPreview);
      if (placeholder) startPreviewFrames(placeholder, snapshot.station, previewKey(snapshot));
    }
    const cameraFrame = view.querySelector('#ab-camera-frame');
    const resolution = view.querySelector('[data-camera-resolution]');
    const updateResolution = () => {
      if (resolution && cameraFrame?.naturalWidth) resolution.textContent = `Source resolution ${cameraFrame.naturalWidth} × ${cameraFrame.naturalHeight}${cameraFrame.naturalWidth < 1920 || cameraFrame.naturalHeight < 1080 ? ' · recorded below 1080p' : ' · 1080p or higher'}`;
      if (cameraFrame?.naturalWidth && cameraFrame.naturalWidth < 512) cameraFrame.style.maxWidth = `${Math.max(256, cameraFrame.naturalWidth)}px`;
    };
    // Reused stream elements keep one pair of listeners across snapshot renders.
    if (cameraFrame) {
      cameraFrame.onload = () => {
        updateResolution(); cameraFrame.dataset.streamError = 'false';
      };
      cameraFrame.onerror = () => {
        cameraFrame.dataset.streamError = 'true';
        if (cameraFrame.dataset.liveStreamKey) {cameraFrame.hidden = !cameraFrame.naturalWidth; showPreviewState();}
        else if (resolution) resolution.textContent = 'Saved camera image unavailable';
      };
    }
    updateResolution(); showPreviewState();
    view.querySelectorAll('[data-history-image]').forEach(image => {
      const update = () => {
        if (!image.naturalWidth) return;
        const caption = image.parentElement.querySelector('[data-media-resolution]');
        if (caption) caption.textContent = ` · source ${image.naturalWidth} × ${image.naturalHeight}`;
        if (image.naturalWidth < 512) image.style.maxWidth = `${Math.max(256, image.naturalWidth)}px`;
      };
      image.addEventListener('load', update); update();
    });

    view.querySelectorAll('[data-live-request]').forEach(button => button.addEventListener('click', () => {
      selectedRequest = button.dataset.liveRequest;
      render(currentSnapshot);
    }));
    view.querySelectorAll('[data-respond]').forEach(button => button.addEventListener('click', async () => {
      if (pendingControls.has(chosen.request_id)) return;
      pendingControls.add(chosen.request_id);
      const decision = button.dataset.respond;
      const payload = decision === 'hint'
        ? {content: view.querySelector('#ab-hint-content')?.value || ''}
        : {decision};
      if (decision === 'hint' && run.robot?.execution_arm === 'both') payload.guidance_scope = {arm:view.querySelector('[data-hint-arm]').value,phase:view.querySelector('[data-hint-phase]').value,attempt_id:chosen.attempt_id};
      button.disabled = true;
      try {
        const response = await controlFetch(`/api/requests/${encodeURIComponent(chosen.request_id)}/respond`, {
          method: 'POST', headers: {'Content-Type':'application/json',
            'X-Attention-Token': root.dataset.controlToken || ''},
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        submissionReceipts.set(`${run.run_id}:${chosen.request_id}`, {response_id:result.response_id, success:true});
        const key = `attention.draft:${run.run_id}:${chosen.request_id}`;
        draftCache.delete(key); stored(key, null);
        const old = view.querySelector('#ab-hint-content'); if (old?.dataset.draftKey === key) old.dataset.draftKey = '';
        lastRendered = '';
        await refresh();
      } catch (error) {
        view.querySelector('#ab-live-message').textContent = `Reply not submitted: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
        const feedback = view.querySelector('[data-core-region="request"]');
        if (feedback) {
          let failure = feedback.querySelector('[data-submission-error]');
          if (!failure) { failure = document.createElement('p'); failure.dataset.submissionError = ''; failure.setAttribute('role', 'alert'); feedback.append(failure); }
          failure.textContent = `Submission not confirmed: ${DEBUG_VIEW ? error.message : plainError(error.message)}. No evidence of adoption.`;
        }
        button.disabled = false;
      } finally {
        pendingControls.delete(chosen.request_id);
      }
    }));
    async function requestAction(action, button) {
      const input = view.querySelector('#ab-hint-content');
      if (action === 'defer' && input?.dataset.draftKey) deferredSelection = {key: input.dataset.draftKey, range: [input.selectionStart, input.selectionEnd, input.selectionDirection]};
      button.disabled = true;
      try {
        const response = await controlFetch(`/api/requests/${encodeURIComponent(chosen.request_id)}/${action}`, {
          method: 'POST', headers: {'Content-Type':'application/json','X-Attention-Token': root.dataset.controlToken || ''},
          body:JSON.stringify({action:button.id==='ab-cancel-run'?'cancel':'stop'}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        lastRendered = '';
        await refresh();
      } catch (error) {
        deferredSelection = null;
        view.querySelector('#ab-live-message').textContent = `Request action failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
        button.disabled = false;
      }
    }
    view.querySelectorAll('[data-ignore-request]').forEach(button => button.addEventListener('click', () => requestAction('ignore', button)));
    view.querySelectorAll('[data-defer-request]').forEach(button => button.addEventListener('click', () => requestAction('defer', button)));
    view.querySelectorAll('[data-cancel-request]').forEach(button => button.addEventListener('click', () => requestAction('cancel', button)));
    view.querySelectorAll('#ab-emergency-interrupt,#ab-cancel-run').forEach(button => button.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const response = await controlFetch(snapshot.dev_v2 ? `/api/dev-v2/experiments/${encodeURIComponent(snapshot.dev_v2.experiment_id)}/cancel` : snapshot.dev_session ? `/api/dev/sessions/${encodeURIComponent(run.run_id)}/stop` : `/api/runs/${encodeURIComponent(run.run_id)}/interrupt`, {
          method: 'POST', headers: {'Content-Type':'application/json','X-Attention-Token': root.dataset.controlToken || ''},
          body:JSON.stringify(snapshot.dev_session?{}:{action:button.id==='ab-cancel-run'?'cancel':'stop'}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        view.querySelector('#ab-live-message').textContent = 'Stop request recorded; awaiting executor confirmation.';
        lastRendered = '';
        await refresh();
      } catch (error) {
        view.querySelector('#ab-live-message').textContent = `Stop request failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
        button.disabled = false;
      }
    }));
    view.querySelector('[data-dev-resume]')?.addEventListener('click',async event=>{
      const button=event.currentTarget;button.disabled=true;
      try {
        const response=await controlFetch(snapshot.dev_v2?`/api/dev-v2/experiments/${encodeURIComponent(snapshot.dev_v2.experiment_id)}/run`:`/api/dev/sessions/${encodeURIComponent(run.run_id)}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        const result=await response.json();if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
        lastRendered='';await refresh();
      } catch(error) {view.querySelector('#ab-live-message').textContent=`Start / continue failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;button.disabled=false;}
    });
    mountPolicyController();
    bindLaunch();
    view.querySelector('[data-open-more]')?.addEventListener('click',openMore);
    view.querySelectorAll('[data-open-flow-node]').forEach(button=>button.addEventListener('click',()=>openFlowDetail(currentSnapshot,button.dataset.openFlowNode)));
    bindOverview(snapshot);
    connectionState(root.dataset.connection !== 'disconnected');
    if(previousRun===run.run_id&&overviewFocus)view.querySelector(overviewFocus)?.focus({preventScroll:true});
  }

  function updateReplyClock() {
    if (!currentSnapshot) return;
    if (requestAlertKey && !actionableRequests(currentSnapshot).some(request =>
      `attention.request-alert:${JSON.stringify([currentSnapshot.run.run_id,request.request_id])}` === requestAlertKey)) dismissRequestAlert();
    const deadline = view.querySelector('[data-reply-countdown]');
    if (!deadline || deadline.dataset.expired === 'true') return;
    const request = currentSnapshot.requests.find(r => r.request_id ===
      (actionableRequests(currentSnapshot).find(r => r.request_id === selectedRequest)?.request_id ||
       actionableRequests(currentSnapshot)[0]?.request_id)) ||
      currentSnapshot.requests.find(r => r.state === 'pending' && replyExpired(r));
    if (!request) return;
    if (replyExpired(request)) { render(currentSnapshot); return; }
    deadline.textContent = countdown(request);
    const top = view.querySelector('[data-status-countdown]');
    if (top) top.textContent = countdown(request);
  }

  function bindOrchestratorControls(scope=view) {
    scope.querySelector('[data-dag-dispatch]')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const response = await controlFetch('/api/orchestrator/dispatch', {
          method: 'POST', headers: {'Content-Type': 'application/json',
            'X-Attention-Token': root.dataset.controlToken || ''},
          body: JSON.stringify({graph: orchestrator.graph}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        dagActionMessage = result.spawned.length ? `Dispatched: ${result.spawned.join(' · ')}` : 'Automatic dispatch started; no new ready nodes.';
        lastRendered = '';
        await refresh();
      } catch (error) {
        dagActionMessage = `Dispatch failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
        if (currentSnapshot) render(currentSnapshot);
        else renderNoRuns();
      }
    });
    scope.querySelectorAll('[data-dag-skill]').forEach(button => button.addEventListener('click', () => {
      selectedSkill = button.dataset.dagSkill;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
      scope.querySelector('.work-detail')?.scrollIntoView({block:'nearest'});
    }));
    scope.querySelectorAll('[data-session-filter]').forEach(button => button.addEventListener('click', () => {
      sessionFilter = button.dataset.sessionFilter;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
    }));
    scope.querySelectorAll('[data-run-filter]').forEach(button => button.addEventListener('click', () => {
      runFilter = button.dataset.runFilter;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
    }));
    scope.querySelectorAll('[data-session-index]').forEach(button => button.addEventListener('click', () => openSession(Number(button.dataset.sessionIndex))));
    scope.querySelector('#ab-graph-form')?.addEventListener('submit', event => {
      event.preventDefault();
      const value = String(new FormData(event.currentTarget).get('graph') || '').trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) return;
      graphOverride = value;
      historyLoadedAt = 0;
      sessionHistory = [];
      historyGraph = null;
      const url = new URL(location.href);
      url.searchParams.set('graph', value);
      history.replaceState(null, '', url);
      refresh();
    });
  }

  function renderNoRuns(){renderInactive(null);}
  function renderInactive(launch = null) {
    stopGuidancePreview(); activeGuidancePreview = null;
    dismissRequestAlert();
    saveReplayPositions();
    if(activeAttemptDag||attemptDagDialog.open)closeAttemptDag();
    if(activeFlowDetail||flowDetailDialog.open)closeFlowDetail();
    if(moreDialog.open&&(activeMoreRun!=='empty'||launch))closeMore();
    const phase = launch ? 'launching' : 'empty';
    const expanded = view.querySelector('[data-core-region=status]')?.dataset.phase === phase
      ? [...view.querySelectorAll('[data-persist-detail][open]')].map(e => e.dataset.persistDetail) : [];
    const preview = view.querySelector('#ab-camera-frame[data-live-stream-key]');
    if (preview) stopPreviewFrames(preview);
    view.querySelectorAll('video').forEach(v => v.pause());
    currentSnapshot = null; runNotice.hidden = true;devTag.hidden=true;
    const title = launch ? launch.state === 'failed' ? 'Launch failed' : 'Waiting for run registration' : 'No Attention runs';
    view.innerHTML = `${DEBUG_VIEW ? `${runSettings(null,launch)}<section class="panel ab-run-overview" data-core-region="status" data-phase="${launch ? 'launching' : 'empty'}" aria-label="Task Status & Progress"><div class="ab-status-panel-heading" aria-hidden="true">Task Status & Progress</div><div class="ab-status-head"><h2>${title}</h2>${!launch ? launchButton() : '<button data-dismiss-launch>View existing runs</button>'}${moreButton()}<button id="ab-cancel-run" type="button" disabled>Cancel run</button><button class="danger" id="ab-emergency-interrupt" disabled title="No executing run">■ Emergency stop</button></div><div class="ab-status-main"><div class="ab-action-needed" data-action-needed data-needed="no" role="status" data-connected-needed="no" data-connected-text="✓ No action required">✓ No action required</div></div><p>${launch ? `${esc(pendingLaunch)} · ${esc(launch.reason || 'Launch request accepted; waiting for the executor to create the run.')}` : 'Use New run to choose a robot, task and Dev iteration budgets.'}</p></section>
      <section class="panel" data-core-region="execution"><h3>Execution & History</h3><p class="minor">Waiting for a run and execution frames.</p></section>
      <section class="panel ab-current-request" data-core-region="request" data-state="idle"><div class="panel-title"><h3>Current Request & Reply</h3></div><p class="ab-idle-request">No action required</p></section>
      ${resourceDetails()}<p id="ab-live-message" class="minor" aria-live="polite"></p>` : `${runSettings(null,launch)}<section class="panel ab-run-overview" data-core-region="status" data-phase="${phase}">${operatorHeading('02','STATUS &amp; PROGRESS','How is it going?')}${launch?(launch.state==='failed'?'<div class="op-outcome failed" role="status"><strong>✕ NOT STARTED</strong><div><p>The run could not be started.</p><p>Nothing was run. You can try again.</p></div></div>':'<div class="op-outcome running" role="status"><strong>● STARTING</strong><div><p>Setting up the simulator. This usually takes a few seconds.</p><p>Waiting for run registration</p></div></div>'):'<div class="op-outcome" role="status"><strong>○ NO RUN SELECTED</strong><div><p>Choose a task from the list on the left, or start a new run.</p></div></div>'}<div class="op-controls">${!launch?launchButton():'<button data-dismiss-launch>View existing runs</button>'}</div></section><section class="panel ab-execution-viewer" data-core-region="execution" data-execution-viewer>${operatorHeading('03','REPLAY &amp; ATTEMPTS','What did the robot do, and where did it go wrong?')}${launch&&launch.state!=='failed'?`<div class="op-stage op-launch-stage" data-op-stage="launch" data-state="loading">${loadingOverlay('live')}</div>`:`<div class="op-idle"><b>${launch?'The run could not start':'Nothing to show yet'}</b><p>${launch?'Close this message and try again, or pick another task.':'Pick a task on the left, or start a new run to watch the robot live.'}</p></div>`}</section><section class="panel ab-current-request" data-core-region="request">${operatorHeading('04','HELP &amp; REPLIES','Do I need to do anything?')}<p>✓ Nothing to do</p></section>${resourceDetails()}<p id="ab-live-message" class="minor" aria-live="polite"></p>`}`;
    view.querySelector('[data-dismiss-launch]')?.addEventListener('click', () => { pendingLaunch = null; stored('attention.pending-launch', null); lastRendered = ''; refresh(); });
    view.querySelectorAll('[data-persist-detail]').forEach(e => { e.open = expanded.includes(e.dataset.persistDetail); });
    mountPolicyController();
    if(!DEBUG_VIEW){document.title=launch?'◌ Starting · Attention Bench':'Attention Bench';initStages();}
    bindLaunch();view.querySelector('[data-open-more]')?.addEventListener('click',openMore);refreshMoreDialog();
  }
  // Dev workspace projection: read-only, version-bound display data.
  const devEvidenceCache = new Map();
  function devFile(session, uri) {
    const prefix = session.artifact_dir + '/';
    if (typeof uri !== 'string' || !uri.startsWith(prefix)) return null;
    const parts = uri.slice(prefix.length).split('/');
    if (parts.some(p => !p || p.startsWith('.') || p.includes('\\'))) return null;
    const base=session.channel==='dev_v2'?`/api/dev-v2/experiments/${encodeURIComponent(session.experiment_id)}/cells/${encodeURIComponent(session.key)}`:`/api/dev/sessions/${encodeURIComponent(session.session_id)}`;
    return `${base}/files/${parts.map(encodeURIComponent).join('/')}`;
  }
  async function readDevEvidence(session, ref) {
    const url = devFile(session, ref?.uri);
    if (!url || !/^[a-f0-9]{64}$/.test(ref.sha256)) throw new Error('Invalid evidence reference');
    const response = await fetch(url, {cache:'no-store'});
    if (!response.ok) throw new Error(`Evidence HTTP ${response.status}`);
    const bytes = await response.arrayBuffer();
    const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2,'0')).join('');
    if (digest !== ref.sha256) throw new Error('Evidence digest mismatch');
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  // Extra camera angles (side, top, both wrists) for an attempt: display evidence beside the recording. Optional:
  // runs from before this existed, or a failed capture, simply have no manifest and show the single view.
  const CAMERA_VIEW_VIDEO = /^advisor_replay(_[a-z_]+)?\.mp4$/;
  async function readCameraViews(session, artifactDir) {
    try {
      const url = devFile(session, artifactDir + '/camera_views.json');
      if (!url) return null;
      const response = await fetch(url, {cache:'no-store'});
      if (!response.ok) return null;
      const manifest = await response.json();
      const rows = Object.entries(manifest?.views || {}).filter(([key,row]) => /^[a-z_]+$/.test(key) && row?.status === 'saved' && CAMERA_VIEW_VIDEO.test(row.video || ''));
      // The manifest is written with sorted keys; the picker always reads overview, side, top, left wrist, right wrist.
      const order = ['external','side','top','left_wrist','right_wrist'], rank = key => order.includes(key) ? order.indexOf(key) : order.length;
      rows.sort((a,b) => rank(a[0]) - rank(b[0]));
      return rows.length > 1 ? Object.fromEntries(rows) : null;
    } catch (_) { return null; }
  }
  async function devWorkspaceSnapshot(s) {
    const cfg = s.locked.config, v2=s.channel==='dev_v2', nativeRun=v2?'run:'+s.key:'run:attention-'+s.session_id;
    const attempts = [], traces = {}, outcomes = {}, dags = {}, gaps = [];
    const executions = s.jobs.filter(j => j.kind === 'execute').sort((a,b) => a.version-b.version);
    for (const job of executions) {
      const version = s.versions.find(v => v.version === job.version);
      const attemptId = v2?`attempt:${s.key}:${job.version-1}`:`attempt:attention-${s.session_id}:${job.version-1}`;
      const configDigest=v2?version?.config_sha256:s.locked.config_sha256;
      const receipt = job.receipt || job.review_evidence;
      let packet = null;
      if (receipt?.bundle) {
        const key = JSON.stringify([s.session_id,job.version,receipt.bundle.sha256,receipt.trace?.sha256,configDigest,version?.source.sha256]);
        try {
          packet = devEvidenceCache.get(key);
          if (!packet) {
            const bundle = await readDevEvidence(s, receipt.bundle);
            const traceRef = receipt.trace || bundle.artifacts?.trace;
            const trace = await readDevEvidence(s, traceRef);
            const bound = value => value.run_id === nativeRun && value.attempt_id === attemptId && value.config_sha256 === configDigest && value.policy_sha256 === version?.source.sha256;
            if (!bound(bundle) || !bound(trace) || bundle.dev_version !== job.version || bundle.formal_eligible !== false) throw new Error('Execution evidence identity mismatch');
            const recording = bundle.recording;
            if (recording && (recording.run_id !== nativeRun || recording.attempt_id !== attemptId)) throw new Error('Recording identity mismatch');
            packet = {bundle,trace,views:v2?await readCameraViews(s, bundle.artifact_dir):null};
            devEvidenceCache.set(key, packet);
          }
        } catch (error) { packet = null; gaps.push(`v${job.version}: ${error.message}`); }
      }
      const bundle = packet?.bundle, raw = packet?.trace;
      const settled = job.state === 'settled';
      const status = settled ? ({completed:'succeeded',failed:'failed',cancelled:'cancelled',timed_out:'timed_out'}[receipt.execution_status] || 'unknown') : job.state === 'claimed' && (!v2 || !s.lifecycle || s.lifecycle.phase==='sdk') ? 'running' : 'unknown';
      attempts.push({attempt_id:attemptId,index:job.version-1,status,started_at:null,ended_at:null,status_source:'Persisted Dev dispatch and settlement',dev_version:job.version});
      const traceId = `${s.session_id}:v${job.version}`;
      if (packet) {
        const recording = bundle.recording;
        const videoPath = recording?.video || recording?.video_path || recording?.video_uri || (recording?.video_saved ? bundle.artifact_dir + '/advisor_replay.mp4' : null);
        const videoUrl = devFile(s, typeof videoPath === 'string' ? videoPath : videoPath?.uri || videoPath?.path);
        const events = (raw.sdk_events || []).map((event,index) => ({...event,event_id:`${traceId}:${index}`,sequence:index,event_type:'sdk.call',operation:[event.source,event.operation].filter(Boolean).join('.')}));
        const error = raw.error;
        const cameraViews = packet.views;
        const viewRows = cameraViews ? Object.fromEntries(Object.entries(cameraViews).map(([key,row]) => [key,{name:row.label || key,width:row.width,height:row.height}])) : null;
        const extraVideos = cameraViews ? Object.entries(cameraViews).filter(([key]) => key !== 'external').map(([key,row]) => ({kind:'advisor_replay_'+key,mime_type:'video/mp4',media_url:devFile(s, bundle.artifact_dir + '/' + row.video),evidence_id:(recording?.frame_manifest_sha256 || '')+':'+key})).filter(e => e.media_url) : [];
        traces[traceId] = {trace_id:traceId,execution_id:bundle.artifact_dir?.split('/').at(-1),attempt_id:attemptId,events,recording:viewRows?{...recording,views:viewRows}:recording,
          failure:error?{stage:'execution',error_type:typeof error === 'object'?error.type||'ExecutionError':'ExecutionError',message:typeof error === 'string'?error:error.message||JSON.stringify(error)}:null,
          evidence:videoUrl&&recording?.video_saved?[{kind:'advisor_replay',mime_type:'video/mp4',media_url:videoUrl,evidence_id:recording.frame_manifest_sha256},...extraVideos]:[]};
        const native = bundle.native_evaluator;
        if (native?.run_id === nativeRun && native.attempt_id === attemptId && native.evaluated === true && typeof bundle.native_success === 'boolean') outcomes[attemptId] = {evaluated:true,native_success:bundle.native_success};
        // End time is not fabricated. A separate persisted completion flag permits
        // the existing current-view replay without inventing an epoch timestamp.
        attempts.at(-1).execution_complete = settled;
        attempts.at(-1).attention_policy = v2?bundle.policy_id:bundle.attention_policy;
      }
      dags[attemptId] = {status:'bound',run_id:s.session_id,attempt_id:attemptId,dag:{graph_id:`dev-policy:${s.session_id}:v${job.version}`,revision:version?.source.sha256,root_skill:'policy',nodes:[{node_id:'policy',name:'generated_policy',label:`Policy v${job.version}`,description:'Complete generated policy. Individual SDK calls appear in the action timeline.',dependencies:[],development_status:version?.valid?'done':'failed',execution_status:status==='running'?'running':settled?'executed':'unknown',goal_status:outcomes[attemptId]?.evaluated?(outcomes[attemptId].native_success?'succeeded':'failed'):'unconfirmed',evidence_source:'persisted_attempt',event_ids:(traces[traceId]?.events||[]).map(e=>e.event_id)}]}};
    }
    const terminal = ['stopped','budget_exhausted','failed','needs_review','completed','cancelled','not_started'].includes(s.state);
    const latest = attempts.at(-1), latestOutcome = outcomes[latest?.attempt_id];
    const resources = Object.fromEntries(Object.keys(s.budget).map(key => [key,{limit:s.budget[key],used:s.used[key],reserved:s.reserved[key],remaining:s.remaining[key]}]));
    return {dev_session:s,dev_evidence_gaps:gaps,attention_control:s.attention_control,
      run:{run_id:s.session_id,suite:cfg.suite||'robocasa365',task_id:cfg.task_id,seed:cfg.seed,created_at:s.created_at,name_suffix:s.name_suffix,history_deleted_at:s.history_deleted_at,status:s.state==='stopped'?'cancelled':terminal?'completed':'running',locked:true,robot:{model:cfg.robot,execution_arm:cfg.execution_arm||'right'},robot_configuration:{controller:cfg.controller,config_sha256:s.locked.config_sha256,base:cfg.base_mode==='stationary'?{capability:'stationary'}:null},developer_model:s.model,evaluator_model:'native_evaluator',execution_target:'robocasa365_sim',policy_id:s.attention_control?.active_policy||'autonomous',assistance_mode:'autonomous',formal_eligible:false},
      attempts,traces,attempt_dags:dags,requests:[],responses:{},request_lifecycle:{},response_evidence:{},response_uses:{},resources,work_items:s.state==='needs_review'?[{state:'blocked'}]:[],budget_events:[],
      station:{status:terminal?'ended':'running',camera_url:null,...(s.preview_station||{}),camera_attempt_id:latest?.attempt_id,latest_execution:null,interrupt:s.stop_requested?{state:s.state==='stopped'?'stopped':'requested'}:null},
      task_plan:{max_attempts:s.budget.executions,definition:{description:cfg.task_prompt}},
      eval:{status:latestOutcome?'completed':'unavailable',native_success:latestOutcome?.native_success??null,native_outcomes:outcomes,stopped_reason:s.reason,attempt_ids:attempts.map(a=>a.attempt_id)}};
  }
  // End Dev workspace projection.

  function devOverview(snapshot) {
    const s = snapshot.dev_session;
    const execute = s.jobs.find(j => j.kind === 'execute' && j.state === 'claimed');
    const model = s.jobs.find(j => j.kind === 'model' && j.state === 'claimed');
    const advisor = snapshot.dev_v2 && s.jobs.find(j=>j.kind==='advisor'&&j.state==='claimed');
    const terminal = isTerminal(snapshot);
    const needsGuidance=s.attention_control?.hint?.state==='pending'&&!s.attention_control.read_only;
    const phase = needsGuidance?'waiting':s.state === 'needs_review' ? 'review' : s.stop_requested ? 'stopping' : terminal ? 'ended' : execute ? 'running' : model ? 'developing' : 'preparing';
    const title = needsGuidance?`Waiting for your guidance before version ${s.attention_control.hint.version}`:s.state === 'needs_review' ? 'Execution outcome requires review' : s.state === 'stopping' || (s.stop_requested && !terminal) ? 'Stop requested · awaiting executor confirmation' : terminal ? (s.state==='not_started'?'Ready to start':devEndSummary(s).title) : advisor ? `Advisor preparing guidance for v${advisor.version}` : execute ? `Simulator executing v${execute.version}` : model ? `Model generating v${model.version}` : 'Ready to start the next iteration';
    return `${DEBUG_VIEW ? `<section class="panel ab-run-overview" data-core-region="status" data-run-overview="${esc(s.session_id)}" data-phase="${phase}" aria-label="Task Status & Progress">
      <div class="ab-status-panel-heading" aria-hidden="true">Task Status & Progress</div>
      <div class="ab-status-head"><button type="button" class="ab-overview-drag" data-overview-drag hidden aria-label="Move floating task bar">⠿</button><div><span class="label">RoboCasa365 · Dev automatic iteration</span><h2>${esc(taskDisplayName(snapshot.run))}</h2></div><div class="ab-status-controls"><button type="button" data-overview-open-sidebar>Tasks</button>${launchButton()}${moreButton()}<button class="danger" id="ab-emergency-interrupt" type="button" ${terminal||s.state==='stopping'?'disabled':''}>■ Stop Dev session</button><button type="button" data-dev-resume ${(snapshot.dev_v2?s.state==='not_started':['ready','stopped'].includes(s.state))?'':'disabled'}>Resume session</button><button type="button" class="ab-overview-toggle" data-overview-toggle aria-expanded="true" aria-controls="ab-overview-details">Collapse progress</button></div></div>
      <div class="ab-status-main"><div class="ab-action-needed" data-action-needed data-needed="${needsGuidance||s.state==='needs_review'?'yes':'no'}" data-connected-needed="${needsGuidance||s.state==='needs_review'?'yes':'no'}" data-connected-text="${needsGuidance?'Reply required · Dev iteration paused':s.state==='needs_review'?'Review required':terminal?'No pending reply':'No human reply required'}" role="status">${needsGuidance?'Reply required · Dev iteration paused':s.state==='needs_review'?'Review required':terminal?'No pending reply':'No human reply required'}</div><div class="ab-phase-copy"><strong>${esc(title)}</strong><span data-attempt-count>Version ${s.versions.at(-1)?.version||'—'} · ${s.used.executions} / ${s.budget.executions} executions</span></div></div>
      <div id="ab-overview-details" data-overview-details>${taskFlow(snapshot)}<div class="ab-status-meta"><span>Simulator</span><span>Automatic policy revision</span><span>${esc(s.model)}</span></div></div>
      <div class="ab-status-alerts">${s.reason?`<details data-run-stop-reason><summary>End details</summary><p>${esc(s.reason)}</p></details>`:''}${snapshot.dev_v2?'<p class="minor" data-v2-reproducibility-warning>Independent results are display only. Scene reproducibility across resets is unverified. These development records do not certify a fair policy comparison.</p>':''}${snapshot.dev_evidence_gaps.length?`<p data-dev-evidence-gap>Some execution evidence is unavailable: ${esc(snapshot.dev_evidence_gaps.join(' · '))}. No outcome is inferred.</p>`:''}<p data-picture-warning hidden></p></div></section>` : operatorOverview(snapshot)}`;
  }

  function devVersionHistory(s) {
    const link = (ref,title) => devFile(s,ref?.uri) ? `<a href="${esc(devFile(s,ref.uri))}" target="_blank" rel="noopener">${esc(title)}</a>` : '';
    return `<details data-dev-revisions data-persist-detail="dev-revisions:${esc(s.session_id)}"><summary>Policy revisions · ${s.versions.length} versions</summary>${s.versions.map(v=>`<details data-dev-version="${v.version}" data-persist-detail="dev-version:${esc(s.session_id)}:${v.version}"><summary>v${v.version} · ${esc(v.actual_model||s.model||'Model not recorded')} · ${v.valid?'Validated source':'Source validation failed'}</summary><p>${esc(v.reason)}</p>${v.validation_error?`<p>${esc(v.validation_error)}</p>`:''}<p>${link(v.source,'Complete source')} · ${link(v.diff,'Diff')} · ${link(v.model_input,'Model input')} · ${link(v.model_output,'Model output')}</p><p class="minor">Source SHA256: ${esc(v.source.sha256)}</p></details>`).join('')||'<p>No model version published yet.</p>'}</details>`;
  }
  function renderDev(snapshot) {
    selectedRun = snapshot.run.run_id;
    render(snapshot);
  }
  // Adapt persisted Dev v2 records to the original five-region workspace.
  async function devV2WorkspaceSnapshot(s, cell) {
    const lifecycle=cell.lifecycle;
    const terminal = lifecycle ? ['terminal','review'].includes(lifecycle.phase) : ['completed','failed','cancelled','needs_review'].includes(cell.state);
    const reserved = Object.fromEntries(Object.keys(cell.budget).map(k => [k,0]));
    for (const job of cell.jobs.filter(j => ['reserved','claimed','needs_review'].includes(j.state))) {
      for (const [key,value] of Object.entries(job.reservation || {})) reserved[key] = (reserved[key] || 0) + value;
    }
    const budget = {...cell.budget,model_calls:cell.budget.dev_calls,tokens:cell.budget.provider_tokens,wall_seconds:(cell.ledger.goal.wall_limit??cell.ledger.goal.limits.wall_seconds)};
    const used = {...cell.used,model_calls:cell.used.dev_calls,tokens:cell.used.provider_tokens,wall_seconds:cell.ledger.goal.wall_used};
    reserved.model_calls=reserved.dev_calls;reserved.tokens=reserved.provider_tokens;reserved.wall_seconds=0;
    const remaining = Object.fromEntries(Object.keys(budget).map(k=>[k,Math.max(0,(budget[k]||0)-(used[k]||0)-(reserved[k]||0))]));
    remaining.wall_seconds=cell.ledger.goal.wall_remaining;
    const control = {binding:{allowed_policies:launchOptions?.dev_v2?.policies||[]},active_policy:cell.policy_id,
      read_only:terminal||cell.mode!=='interactive',reason:cell.mode==='interactive'?'Switches apply before the next model version; this session is excluded from comparison.':'Frozen experimental policy; independent results are display only.'};
    const session = {...s,channel:'dev_v2',key:cell.key,experiment_id:cell.experiment_id,artifact_dir:cell.artifact_dir,
      versions:cell.versions,jobs:cell.jobs,budget,used,reserved,remaining,reason:cell.reason,state:cell.state,lifecycle:cell.lifecycle,
      model:s.model||cell.model||launchOptions?.dev_loop?.model,attention_control:control};
    const snapshot = await devWorkspaceSnapshot(session);
    snapshot.dev_v2=cell;
    snapshot.dev_eval_enabled=s.locked?.config?.eval_agent?.enabled===true;
    snapshot.dev_session.versions=cell.versions;
    if(snapshot.dev_eval_enabled && !snapshot.resources.eval_calls)snapshot.resources.eval_calls={used:cell.used.eval_calls||0,limit:cell.budget.eval_calls||0};
    snapshot.run.status=cell.state==='cancelled'?'cancelled':terminal?'completed':lifecycle && lifecycle.phase!=='sdk'?lifecycle.phase:'running';
    snapshot.run.policy_id=cell.policy_id;
    snapshot.station.status=terminal?'ended':lifecycle && lifecycle.phase!=='sdk'?lifecycle.phase:'running';
    const monitorBase=`/api/dev-v2/experiments/${encodeURIComponent(cell.experiment_id)}/cells/${encodeURIComponent(cell.key)}/live`;
    const previousIdentity=v2MonitorIdentities.get(cell.key);
    const version=cell.active_version ?? cell.versions.at(-1)?.version ?? null;
    const episode=cell.active_episode || (terminal && previousIdentity?.version===version ? previousIdentity.episode : '');
    v2MonitorIdentities.set(cell.key,{episode,version});
    // Extra live angles exist only while an attempt runs; a finished run is browsed through its saved replays.
    const liveViews=terminal?null:Object.fromEntries([['external','Overview',1920,1080],['side','Side',960,540],['top','Top-down',960,540],['left_wrist','Left wrist',960,540],['right_wrist','Right wrist',960,540]]
      .filter(([key])=>!launchOptions?.dev_v2?.bridge_schema||key==='external').map(([key,name,width,height]) => [key,{name,width,height,preview_url:monitorBase+'/frame'+(key==='external'?'':'?view='+key),state_url:monitorBase+(key==='external'?'':'?view='+key)}]));
    if(!lifecycle || lifecycle.phase==='sdk' || terminal && snapshot.attempts.length)Object.assign(snapshot.station,{camera_url:monitorBase+'/frame',camera_preview_url:monitorBase+'/frame',camera_views:liveViews,
      camera_state_url:monitorBase,camera_monitor_v2:true,camera_monitor_terminal:terminal,
      camera_monitor_identity:`${cell.key}:${episode}:${version}`});
    snapshot.requests=[];snapshot.responses={};
    for(const version of cell.versions) {
      const help=version.help_content, decision=version.decision_content;
      if(!help && decision?.action!=='request')continue;
      const helpJob=cell.jobs.find(j=>j.version===version.version && ['advisor','demo','cache','human'].includes(j.kind));
      const before=snapshot.attempts.find(a=>a.dev_version===version.version-1);
      const after=snapshot.attempts.find(a=>a.dev_version===version.version);
      const requestId=`${cell.key}:v${version.version}:help`,responseId=help&&!help.rejected?requestId+':response':null;
      const request={request_id:requestId,response_id:responseId,run_id:s.session_id,attempt_id:before?.attempt_id||after?.attempt_id,
        trace_id:Object.values(snapshot.traces).find(t=>t.attempt_id===before?.attempt_id)?.trace_id,
        request_type:'hint',state:help?'answered':terminal?'cancelled':'pending',mode:'automatic',created_at:helpJob?.reserved_at??null,
        reason:decision?.reason||'Recorded assistance',deadline_at:null};
      if(help?.rejected){request.state='rejected';request.reason='Advisor response rejected: '+(help.reason||'Public help boundary rejected the response');}
      snapshot.requests.push(request);
      if(help && !help.rejected) {
        snapshot.responses[responseId]={response_id:responseId,request_id:requestId,responder:help.provider==='advisor'?'advisor_proxy':help.provider||'unknown',
          response_type:'hint',content:typeof help.content==='string'?help.content:JSON.stringify(help.content??''),created_at:helpJob?.settled_at??null};
        const evidence={submission:{response_id:responseId}};
        // Link only when the recorded next model input contains this exact help.
        if(!help.rejected && typeof help.content==='string' && help.content && version.model_input && after) {
          try {
            const cacheKey=JSON.stringify(['v2-input',cell.key,version.model_input.sha256]);
            let input=devEvidenceCache.get(cacheKey);
            if(!input){input=await readDevEvidence(session,version.model_input);devEvidenceCache.set(cacheKey,input);}
            const contains=(value)=>typeof value==='string'?value.includes(help.content)||(()=>{try{return contains(JSON.parse(value));}catch(_){return false;}})():Array.isArray(value)?value.some(contains):value&&typeof value==='object'?Object.values(value).some(contains):false;
            if(contains(input.messages))evidence.association={status:'linked',attempt_id:after.attempt_id};
          }catch(error){snapshot.dev_evidence_gaps.push(`v${version.version} help input: ${error.message}`);}
        }
        if(evidence.association && snapshot.eval.native_outcomes[after.attempt_id])evidence.native_outcome={status:'verified',value:snapshot.eval.native_outcomes[after.attempt_id].native_success};
        snapshot.response_evidence[requestId]=evidence;
      }
    }
    return snapshot;
  }

  async function updateV2Snapshot(s) {
    const cellBase=`/api/dev-v2/experiments/${encodeURIComponent(s.experiment_id)}/cells/${encodeURIComponent(s.key)}`;
    const response=await fetch(`${cellBase}/view`,{cache:'no-store'});
    if(!response.ok)throw new Error(`Dev v2 HTTP ${response.status}`);
    const cell=await response.json();if(selectedDev!==s.session_id)return;
    const fingerprint=JSON.stringify({...cell,ledger:{...cell.ledger,goal:{...cell.ledger.goal,wall_used:0,wall_remaining:0}}});
    if(lastRendered===fingerprint && currentSnapshot?.dev_v2) {
      await refreshPreview();
      const wall=view.querySelector('[data-dev-budget-key="wall_seconds"]');
      if(wall)wall.innerHTML=DEBUG_VIEW?budgetCard('Goal wall time',{limit:(cell.ledger.goal.wall_limit??cell.ledger.goal.limits.wall_seconds),used:cell.ledger.goal.wall_used,reserved:0,remaining:cell.ledger.goal.wall_remaining},'s'):operatorBudgetCard('Total time',{limit:(cell.ledger.goal.wall_limit??cell.ledger.goal.limits.wall_seconds),used:cell.ledger.goal.wall_used},true);
      return;
    }
    const snapshot=await devV2WorkspaceSnapshot(s,cell);if(selectedDev!==s.session_id)return;
    selectedRun=s.session_id;render(snapshot);lastRendered=fingerprint;
  }

  async function updateDevSnapshot(s) {
    if(s.channel==='dev_v2'){await updateV2Snapshot(s);return;}
    const snapshot = await devWorkspaceSnapshot(s);
    if (selectedDev !== s.session_id) return;
    const fingerprint = JSON.stringify({...snapshot,dev_session:{...s,used:{...s.used,wall_seconds:0},remaining:{...s.remaining,wall_seconds:0}},resources:{...snapshot.resources,wall_seconds:{...snapshot.resources.wall_seconds,used:0,remaining:0}}});
    if (fingerprint !== lastRendered) { renderDev(snapshot); lastRendered = fingerprint; }
    else {
      currentSnapshot = snapshot;
      const wall = view.querySelector('[data-dev-budget-key="wall_seconds"]');
      if (wall) wall.innerHTML = DEBUG_VIEW ? budgetCard('Session time',snapshot.resources.wall_seconds,'s') : operatorBudgetCard('Total time',snapshot.resources.wall_seconds,true);
    }
  }

  async function refreshOrchestrator() {
    try {
      const response = await fetch('/api/orchestrator/snapshot', {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      orchestrator = await response.json();
      orchestratorError = '';
      const graph = graphOverride || orchestrator.graph;
      if (graph && (historyGraph !== graph || Date.now() - historyLoadedAt > 10000)) {
        if (historyGraph !== graph) sessionHistory = [];
        const historyResponse = await fetch(`/api/orchestrator/sessions?graph=${encodeURIComponent(graph)}`, {cache:'no-store'});
        if (!historyResponse.ok) throw new Error(`Session history HTTP ${historyResponse.status}`);
        sessionHistory = (await historyResponse.json()).sessions;
        historyGraph = graph;
        historyLoadedAt = Date.now();
      }
      mergeSessions();
    } catch (error) {
      orchestratorError = `Orchestrator ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
      if (!orchestrator) { sessionHistory = []; sessions = []; }
      else mergeSessions();
    }
  }

  async function refresh() {
    if (refreshing) { refreshQueued = true; return; }
    refreshing = true;
    let phase = 'read';
    try {
      await refreshOrchestrator();
      if (!launchOptions && !launchOptionsLoading) {
        // Launch admission checks can be slow; browsing existing history does
        // not depend on them. Load choices once in the background.
        launchOptionsLoading = true;
        fetch('/api/run-options', {cache:'no-store'}).then(async response=>{
          if(response.ok){launchOptions=await response.json();lastRendered='';refresh();}
        }).catch(()=>{}).finally(()=>{launchOptionsLoading=false;});
      }
      const listResponse = await fetch('/api/runs', { cache: 'no-store' });
      if (!listResponse.ok) throw new Error(`Run list HTTP ${listResponse.status}`);
      const listed = await listResponse.json();
      runs = listed.runs;
      devSessions = listed.dev_sessions || [];
      if(allNavigationRecords().some(r=>r.run_id===(selectedDev||selectedRun)&&r.history_deleted_at&&!recordIsActive(r))){
        selectedDev=null;selectRun(null);currentSnapshot=null;lastRendered='';
      }
      renderSidebar();
      if(selectedDev){
        const dev=devSessions.find(d=>d.session_id===selectedDev&&!d.history_deleted_at);
        if(dev){await updateDevSnapshot(dev);connectionState(true);return;}
        const response=await fetch(`/api/dev/sessions/${encodeURIComponent(selectedDev)}`,{cache:'no-store'});
        if(response.ok){const session=await response.json();if(!session.history_deleted_at){await updateDevSnapshot(session);connectionState(true);return;}}
        selectedDev=null;
      }
      launches = listed.launches || [];
      if (pendingLaunch) {
        const launch = launches.find(item => item.launch_id === pendingLaunch);
        if (launch?.run_id && runs.some(item => item.run_id === launch.run_id)) {
          selectRun(launch.run_id); newlyLinkedRun = launch.run_id; pendingLaunch = null; stored('attention.pending-launch', null);
        } else {
          currentSnapshot = null;
          renderInactive(launch || {state:'starting'});
          connectionState(true); return;
        }
      }
      const visibleRuns=runs.filter(r=>!r.history_deleted_at||recordIsActive(r));
      const visibleDevs=devSessions.filter(d=>!d.history_deleted_at||recordIsActive(devNavigationRecord(d)));
      if (!visibleRuns.length && !selectedDev) {
        const latest=visibleDevs.find(d=>resolvedTask(devNavigationRecord(d)));
        if(latest){navigateRecord(latest.session_id);return;}
      }
      if (!visibleRuns.length) {
        const fingerprint = JSON.stringify({orchestrator, sessions, launches, launchOptions,
          orchestratorError, graphOverride});
        if (fingerprint !== lastRendered) {
          phase = 'render';
          renderNoRuns();
          lastRendered = fingerprint;
        }
        connectionState(true);
        return;
      }
      if (!visibleRuns.some(item => item.run_id === selectedRun)) {
        const latest=navigationRecords().filter(r=>resolvedTask(r)).sort((a,b)=>Number(b.created_at||0)-Number(a.created_at||0))[0];
        if(latest?.development){navigateRecord(latest.run_id);return;}
        selectedRun=latest?.run_id||visibleRuns[0].run_id;
      }
      const response = await fetch(`/api/runs/${encodeURIComponent(selectedRun)}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Run snapshot HTTP ${response.status}`);
      const snapshot = await response.json();
      if (selectedDev || snapshot.run.run_id !== selectedRun) return;
      const fingerprint = JSON.stringify({ snapshot, runs, launches, orchestrator: orchestrator && {
        ...orchestrator, live_sessions: (orchestrator.live_sessions || []).map(({timestamp, ...session}) => session),
      }, sessions: sessions.map(session => session.in_progress ? {...session, timestamp: null} : session),
      orchestratorError, graphOverride });
      if (fingerprint !== lastRendered) {
        phase = 'render';
        render(snapshot);
        lastRendered = fingerprint;
      }
      connectionState(true);
    } catch (error) {
      connectionState(phase === 'render');
      const message = phase === 'render'
        ? `Run data loaded, but rendering failed: ${DEBUG_VIEW ? error.message : plainError(error.message)}; retrying on the next refresh.`
        : `Could not read AttentionStore: ${DEBUG_VIEW ? error.message : plainError(error.message)}`;
      const status = view.querySelector('#ab-live-message');
      if (status) { status.textContent = message; status.dataset.readError = 'true'; }
      else view.innerHTML = `<section class="panel"><h2>Could not load run data</h2><p class="lead">${esc(message)}</p></section>`;
    } finally {
      refreshing = false;
      if (refreshQueued) { refreshQueued = false; queueMicrotask(refresh); }
    }
  }

  view.innerHTML = '<section class="panel"><h2>Reading AttentionStore…</h2></section>';
  root.classList.add('ready');
  refresh();
  setInterval(refresh, 3000);
  setInterval(refreshPreview, 1000);
  setInterval(updateReplyClock, 1000);
})();
