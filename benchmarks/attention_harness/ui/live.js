(function () {
  const root = document.getElementById('attentionbench-clear');
  if (!root || root.dataset.mode !== 'live') return;

  const view = root.querySelector('.view');
  root.querySelector('.ribbon').textContent = 'LIVE OPERATIONS · PERSISTED ATTENTION STORE';
  root.querySelector('.demo').textContent = '● STORE';
  let runs = [];
  let launches = [];
  let launchOptions = null;
  let selectedRun = new URLSearchParams(location.search).get('run');
  let selectedRequest = null;
  let currentSnapshot = null;
  let lastRendered = '';
  let orchestrator = null;
  let orchestratorError = '未连接 Orchestrator';
  let sessionHistory = [];
  let sessions = [];
  let sessionFilter = 'all';
  let runFilter = 'current';
  let selectedSkill = null;
  let dagActionMessage = '';
  let historyGraph = null;
  let historyLoadedAt = 0;
  let refreshing = false;
  let graphOverride = new URLSearchParams(location.search).get('graph');

  function esc(value) {
    return String(value ?? '—').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[char]));
  }

  function label(value) {
    return String(value ?? '—').replaceAll('_', ' ');
  }

  function dateTime(value) {
    if (value == null) return '—';
    const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false });
  }

  const sessionDialog = document.createElement('dialog');
  sessionDialog.className = 'ab-session-dialog';
  sessionDialog.setAttribute('aria-label', 'Agent session conversation');
  root.appendChild(sessionDialog);
  sessionDialog.addEventListener('click', event => {
    if (event.target === sessionDialog || event.target.closest('[data-close-session]')) sessionDialog.close();
  });
  const launchDialog = document.createElement('dialog');
  launchDialog.className = 'ab-session-dialog';
  launchDialog.setAttribute('aria-label', 'Configure Attention run');
  root.appendChild(launchDialog);
  launchDialog.addEventListener('click', event => {
    if (event.target === launchDialog || event.target.closest('[data-close-launch]')) launchDialog.close();
  });

  function launchPanel() {
    const recent = launches.slice(-3).reverse();
    return `<section class="panel work-detail"><div class="panel-title"><h3>Run configuration</h3><span>BACKEND LOCKS CONDITIONS AT START</span></div>
      <button type="button" data-open-launch ${launchOptions?.profiles?.length ? '' : 'disabled'}>配置并启动真实 Service run</button>
      <p class="minor">${launchOptions?.profiles?.length ? '开发 seed 101–125；仅已批准的模拟器配置可启动。' : '服务端未配置批准的 run catalog 与产物目录。'}</p>
      ${recent.map(item => `<p class="minor">${esc(item.launch_id)} · ${esc(item.state)} · ${esc(item.locked?.suite)} / ${esc(item.locked?.task)} · seed ${esc(item.locked?.seed)} · formal_eligible=false</p>`).join('')}</section>`;
  }

  function bindLaunch() {
    view.querySelector('[data-open-launch]')?.addEventListener('click', () => {
      const options = launchOptions;
      if (!options?.profiles?.length) return;
      launchDialog.innerHTML = `<div class="ab-session-dialog-inner"><div class="panel-title"><h3>Run configuration</h3><button type="button" data-close-launch>关闭</button></div>
        <form id="ab-launch-form"><label>任务与已批准配置<select name="profile_id">${options.profiles.map(item => `<option value="${esc(item.id)}">${esc(item.suite)} / ${esc(item.task)}</option>`).join('')}</select></label>
        <label>开发 seed<input name="seed" type="number" min="101" max="125" value="101" required></label>
        <label>Attention 策略<select name="attention_policy">${options.attention_policies.map(item => `<option value="${esc(item)}">${esc(label(item))}</option>`).join('')}</select></label>
        <label>执行目标<select name="execution_target">${[...new Set(options.profiles.map(item => item.execution_target))].map(item => `<option value="${esc(item)}">${esc(item)}</option>`).join('')}</select></label>
        <label>求助模式<select name="assistance_mode">${options.assistance_modes.map(item => `<option value="${esc(item)}">${esc(label(item))}</option>`).join('')}</select></label>
        <label>最大 attempts<input name="max_attempts" type="number" min="1" max="10" value="3" required></label>
        <label>求助额度<input name="assistance_credits" type="number" min="0" max="10" value="1" required></label>
        <label>Token 预算<input name="token_limit" type="number" min="0" max="100000" value="30000" required></label>
        <label>真人截止秒数<input name="human_deadline_seconds" type="number" min="1" max="600" value="60" required></label>
        <label>每次执行截止秒数<input name="overall_deadline_seconds" type="number" min="30" max="600" value="300" required></label>
        <p class="minor">启动后以上条件由后端锁定；此处无运行中修改入口。</p><button type="submit">启动</button><p role="status" id="ab-launch-message"></p></form></div>`;
      const form = launchDialog.querySelector('form');
      form.profile_id.addEventListener('change', () => {
        const profile = options.profiles.find(item => item.id === form.profile_id.value);
        form.execution_target.value = profile.execution_target;
        form.seed.value = profile.seed;
      });
      form.execution_target.value = options.profiles[0].execution_target;
      form.seed.value = options.profiles[0].seed;
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const values = Object.fromEntries(new FormData(form));
        for (const key of ['seed', 'max_attempts', 'assistance_credits', 'token_limit', 'human_deadline_seconds', 'overall_deadline_seconds']) values[key] = Number(values[key]);
        const submit = form.querySelector('button[type=submit]');
        submit.disabled = true;
        try {
          const response = await fetch('/api/runs/start', {method:'POST',
            headers:{'Content-Type':'application/json', 'X-Attention-Token':root.dataset.controlToken || ''},
            body:JSON.stringify(values)});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
          launchDialog.close();
          lastRendered = '';
          await refresh();
        } catch (error) {
          form.querySelector('#ab-launch-message').textContent = `启动失败：${error.message}`;
          submit.disabled = false;
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
      <p class="minor">开发者会话记录仅供操作员查看，不进入 AdvisorProxy 的请求证据。历史中的 Human Hint 不会自动变成 Attention inbox 请求。</p>
      <form class="ab-graph-form" id="ab-graph-form"><label>SKILL GRAPH <input name="graph" value="${esc(graph)}" pattern="[A-Za-z0-9][A-Za-z0-9_-]*" maxlength="128" aria-label="Skill graph name"></label><button type="submit">查看 Graph</button></form>
      <div class="ab-session-toolbar" aria-label="Session filters">
        ${['all', 'dev', 'evaluator', 'hint'].map(type => `<button type="button" data-session-filter="${type}" aria-pressed="${sessionFilter === type}">${{all:'All',dev:'Dev',evaluator:'Evaluator',hint:'Has Hint'}[type]}</button>`).join('')}
        <span class="ab-filter-divider"></span>
        ${['current', 'all'].map(type => `<button type="button" data-run-filter="${type}" aria-pressed="${runFilter === type}">${type === 'current' ? 'Current Run' : 'All History'}</button>`).join('')}
      </div><p class="minor">Current Run 在 Orchestrator 未提供 run_id 时显示进行中的会话。Skill DAG 始终对应 Orchestrator 当前 graph。</p>
      <div class="ab-session-groups">${[...groups.keys()].sort().map(target => `<div class="ab-session-group"><h4>${esc(target)} <small>${groups.get(target).length} sessions</small></h4><div class="ab-session-grid">${groups.get(target).map(session => {
        const index = sessions.indexOf(session);
        const cost = Number(session.cost_usd || 0).toFixed(3);
        return `<button class="ab-session-card" type="button" data-session-index="${index}"><span class="card-top"><span class="label">${esc(String(session.agent_type || 'agent').toUpperCase())} · #${String(index + 1).padStart(2, '0')}</span>${session.in_progress ? chip(session.status || 'running') : hasHint(session) ? '<span class="status-chip answered">HINT</span>' : ''}</span><strong>${esc(session.skill || '未知技能')}</strong><span class="minor">${esc(session.num_turns || 0)} turns · $${cost} · ${esc(dateTime(session.timestamp))}</span></button>`;
      }).join('')}</div></div>`).join('') || `<p class="live-empty-copy">${orchestrator ? '此筛选条件下暂无会话。' : esc(orchestratorError)}</p>`}</div></section>`;
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
      ${linkedSkill ? '<button type="button" class="ab-session-dag-link" data-open-dag-from-session>查看 Skill DAG 节点与依赖 ↗</button>' : ''}
      <div class="ab-conversation">${log.map(message => `<div class="ab-message ${message.role === 'user' ? 'human' : ''}"><span class="label">${message.role === 'user' ? '★ HUMAN HINT' : esc(String(message.role || 'AGENT').toUpperCase())}</span><pre>${esc(message.text || '')}</pre></div>`).join('') || '<p class="minor">暂无消息。</p>'}</div></div>`;
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
    if (!entries.length) return `<section class="panel work-detail"><div class="panel-title"><h3>Work source detail</h3><span>SKILL DAG</span></div><p class="live-empty-copy">${esc(orchestratorError || '当前 graph 尚无 Skill DAG 节点。')}</p></section>`;
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
    const attentionLink = !linked ? '' : `<p class="minor">AttentionBench · ${linked.native_success ? '原生成功' : '原生失败'} · ${inStore ? `<a href="/ui/?run=${encodeURIComponent(linked.run_id)}">打开 Run ${esc(linked.run_id)}</a>` : '当前 UI store 未包含此 Run'}</p>`;
    return `<section class="panel work-detail"><div class="panel-title"><h3>Skill DAG · ${esc(orchestrator.graph)}</h3><span>${entries.length} NODES · ORCHESTRATOR</span></div>
      <div class="ab-dag-dispatch"><span class="label">${orchestrator.autonomous_mode ? 'AUTO DISPATCH ACTIVE' : 'AUTO DISPATCH INACTIVE'} · ${ready.length} READY</span>
        <button type="button" data-dag-dispatch ${activeGraph ? '' : 'disabled'}>${orchestrator.autonomous_mode ? '派发就绪节点' : '启动自动派发'}</button></div>
      ${!activeGraph ? '<p class="minor">当前查看的是历史 graph；切回 Orchestrator 的活动 graph 后可派发。</p>' : ''}
      ${dagActionMessage ? `<p class="minor" role="status">${esc(dagActionMessage)}</p>` : ''}
      <div class="ab-dag-nodes">${entries.map(entry => `<button type="button" data-dag-skill="${esc(entry.name)}" aria-pressed="${selected.name === entry.name}"><b>${esc(entry.name)}</b>${chip(entry.status || 'planned')}</button>`).join('')}</div>
      <div class="ab-dag-detail"><span class="label">SELECTED NODE · ${esc(selected.name)}</span><p>${esc(selected.description || '暂无描述')}</p><p>${['planned', 'failed'].includes(selected.status) ? (selected.dependencies || []).every(dep => done.has(dep)) ? '前置依赖已满足，等待派发。' : `等待前置技能完成：${esc((selected.dependencies || []).filter(dep => !done.has(dep)).join(' · '))}` : '由 Orchestrator 管理当前节点与下游派发。'}</p>${attentionLink}<div class="dag-chain"><span class="dag-node">DEPENDENCIES<b>${(selected.dependencies || []).map(esc).join(' · ') || '无'}</b></span><span class="dag-arrow">→</span><span class="dag-node current">${esc(selected.name)}<b>${esc(selected.status || 'planned')}</b></span><span class="dag-arrow">→</span><span class="dag-node">DOWNSTREAM<b>${downstream.map(entry => esc(entry.name)).join(' · ') || '无'}</b></span></div></div></section>`;
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

  function chip(status) {
    const value = String(status ?? 'unknown');
    return `<span class="status-chip ${stateClass(value)}">${esc(value.toUpperCase())}</span>`;
  }

  function budgetCard(name, value, unit, primary = false) {
    const used = Number(value?.used || 0);
    const reserved = value?.reserved;
    const remaining = Number(value?.remaining || 0);
    const limit = Number(value?.limit || 0);
    return `<div class="budget ${primary ? 'primary' : ''}"><span class="label">${esc(name)}</span>
      <strong>${esc(number(used))} <small>/ ${esc(number(limit))}${esc(unit)}</small></strong>
      <div class="budget-track" role="img" aria-label="已用 ${esc(number(used))}，预留 ${reserved == null ? '未追踪' : esc(number(reserved))}，剩余 ${esc(number(remaining))}">
        <span class="used" style="width:${pct(used, limit)}%"></span>
        ${reserved == null ? '' : `<span class="reserved" style="width:${pct(reserved, limit)}%"></span>`}
        <span class="free" style="width:${pct(remaining, limit)}%"></span></div>
      <div class="budget-legend"><span><i></i>已用<b>${esc(number(used))}</b></span><span><i></i>预留<b>${reserved == null ? '未追踪' : esc(number(reserved))}</b></span><span><i></i>剩余<b>${esc(number(remaining))}</b></span></div></div>`;
  }

  function requestRow(request) {
    const id = String(request.request_id);
    const active = id === selectedRequest;
    const deadline = request.deadline_at == null ? '无截止时间' : dateTime(request.deadline_at);
    return `<button class="inboxitem" data-live-request="${esc(id)}" aria-pressed="${active}" type="button">
      <span class="card-top"><span class="request-id">${esc(label(request.request_type).toUpperCase())} · ${esc(id)}</span>${chip(request.state)}</span>
      <strong>${esc(request.reason)}</strong><p>attempt · ${esc(request.attempt_id)}</p>
      <span class="inbox-meta"><span>PRIORITY · ${esc(String(request.priority).toUpperCase())}</span><span>DEADLINE · ${esc(deadline)}</span><span class="source-chip">REQUEST EVENT</span></span></button>`;
  }

  function traceDetail(snapshot, request) {
    if (!request) return `<section class="panel detail"><div class="panel-title"><h3>Request detail · Advisor-visible evidence</h3><span>NO REQUEST SELECTED</span></div><p class="minor">当前 run 尚未发出求助请求。</p></section>`;
    const trace = snapshot.traces[request.trace_id];
    const response = snapshot.responses[request.response_id];
    const adopted = snapshot.response_uses?.[request.request_id];
    const lifecycle = snapshot.request_lifecycle?.[request.request_id] || [];
    const deferred = lifecycle.some(item => item.event_type === 'request.deferred');
    const canRespond = request.state === 'pending' && request.mode === 'live_human_first';
    const actions = canRespond ? request.request_type === 'hint'
      ? `<div class="ab-response-form"><label>HUMAN HINT<textarea id="ab-hint-content" maxlength="4000" placeholder="基于可见 trace 和回放给出具体指导"></textarea></label><div><button type="button" data-respond="hint">提交 Hint</button><button type="button" data-defer-request ${deferred ? 'disabled' : ''}>${deferred ? '已延后' : '稍后处理'}</button><button type="button" data-cancel-request>取消请求</button></div></div>`
      : request.request_type === 'approval'
        ? `<div class="ab-response-form"><div><button type="button" data-respond="approve">批准</button><button type="button" data-respond="deny">拒绝</button><button type="button" data-defer-request ${deferred ? 'disabled' : ''}>${deferred ? '已延后' : '稍后处理'}</button><button type="button" data-cancel-request>取消请求</button></div></div>`
        : '<div class="ab-response-form"><button type="button" data-respond="interrupt">确认中断请求</button></div>'
      : `<p class="live-readonly-note">${request.mode === 'benchmark_proxy' ? 'Benchmark · Proxy 请求由运行端自动处理，不接受真人答复。' : '该请求已离开待答复状态。'}</p>`;
    const attempts = snapshot.attempts.filter(item => item.index <=
      (snapshot.attempts.find(attempt => attempt.attempt_id === request.attempt_id)?.index ?? -1));
    const evidence = trace?.evidence || [];
    const replay = attempts.map(attempt => {
      const linked = Object.values(snapshot.traces).find(item => item.attempt_id === attempt.attempt_id);
      const media = (linked?.evidence || []).filter(item => item.media_url);
      return `<div class="ab-replay-attempt"><span class="label">ATTEMPT ${esc(attempt.index + 1)} · ${esc(attempt.status)}</span>${media.map(item =>
        item.mime_type?.startsWith('video/')
          ? `<video controls preload="metadata" src="${esc(item.media_url)}" aria-label="Attempt ${esc(attempt.index + 1)} replay"></video>`
          : `<figure><img loading="lazy" src="${esc(item.media_url)}" alt="Attempt ${esc(attempt.index + 1)} ${esc(label(item.kind))} camera frame"><figcaption>${esc(label(item.kind))}</figcaption></figure>`
      ).join('') || '<p class="minor">此 attempt 没有授权的相机证据。</p>'}</div>`;
    }).join('');
    const events = trace?.events || [];
    return `<section class="panel detail"><div class="panel-title"><h3>Request detail · Advisor-visible evidence</h3><span>仅展示持久化 Advisor packet 字段</span></div>
      <div class="detail-layout"><div class="replay"><div class="attempt-heading"><span>EXECUTION ATTEMPTS</span><span>${attempts.length} recorded</span></div>
        <div class="attempt-strip">${attempts.map(attempt => `<span>${esc(String(attempt.index + 1).padStart(2, '0'))} · ${esc(attempt.status)}</span>`).join('') || '<span>暂无 attempt</span>'}</div>
        <div class="replay-head"><span>EVIDENCE REFERENCES</span><span>${evidence.length} visible</span></div>
        <div class="ab-replay-gallery">${replay || '<p class="minor">尚无可显示的 attempt 回放。</p>'}</div>
        <div class="live-evidence-list">${evidence.map(item => `<span>${esc(item.kind || 'evidence')} · ${esc(String(item.sha256 || '').slice(0, 12))}</span>`).join('') || '<span>此 trace 未提供可显示的证据引用</span>'}</div></div>
      <div class="evidence-side"><div class="request-card"><div class="card-top"><span class="request-id">${esc(label(request.request_type).toUpperCase())} · ${esc(request.request_id)}</span>${chip(request.state)}</div>
        <strong>${esc(request.reason)}</strong><p>Agent state: ${esc(trace?.agent_state || '未记录')}</p>
        <div class="detail-meta"><span>PRIORITY<b>${esc(request.priority)}</b></span><span>DEADLINE<b>${esc(dateTime(request.deadline_at))}</b></span><span>MODE<b>${esc(label(request.mode))}</b></span></div></div>
      <div class="evidence-card"><span class="label">AGENT HYPOTHESIS · ${esc(trace?.hypothesis_status || 'unknown')}</span><p>${esc(trace?.hypothesis || '未知（Dev Agent 未提供）')}</p></div>
      <div class="evidence-card"><span class="label">VISIBLE TRACE · ${esc(request.trace_id)}</span>
        <div class="trace">${events.map(event => `<div>${esc(dateTime(event.timestamp))} · ${esc(event.operation || event.event_type)} · ${esc(event.status)}</div>`).join('') || `<div>${esc(trace?.failure?.stage || 'failure')} · ${esc(trace?.failure?.message || '暂无可显示事件')}</div>`}</div></div>
      <div class="evidence-card"><span class="label">MEMORY REFERENCES</span><p class="memory-ref">${(trace?.memory_refs || []).map(esc).join(' · ') || '无'}</p></div>
      <div class="evidence-card"><span class="label">REQUEST LIFECYCLE</span><p>${esc(['request.created', ...lifecycle.map(item => item.event_type), response ? `response.${response.responder}` : 'waiting', adopted ? `adopted.${adopted.use}` : 'not adopted'].join(' → '))}</p></div>
      <div class="evidence-card"><span class="label">RESPONSE / LINKED EXECUTION</span><div class="detail-meta"><span>RESPONSE<b>${esc(response ? `${response.responder}: ${response.content}` : '尚无')}</b></span><span>ADOPTED<b>${esc(adopted?.use || '尚未采用')}</b></span><span>EXECUTION<b>${esc(adopted?.execution_id || '未关联')}</b></span><span>BOUNDARY<b>Advisor-visible</b></span></div></div>
      ${actions}</div></div></section>`;
  }

  function render(snapshot) {
    currentSnapshot = snapshot;
    const run = snapshot.run;
    const sortedRequests = [...snapshot.requests].sort((a, b) => Number(b.created_at) - Number(a.created_at));
    if (!sortedRequests.some(item => item.request_id === selectedRequest)) {
      selectedRequest = sortedRequests.find(item => item.state === 'pending')?.request_id || sortedRequests[0]?.request_id || null;
    }
    const chosen = sortedRequests.find(item => item.request_id === selectedRequest);
    const latestAttempt = [...snapshot.attempts].sort((a, b) => b.index - a.index)[0];
    const latestTrace = Object.values(snapshot.traces).at(-1);
    const liveAgents = orchestrator?.agents || [];
    const evalStatus = snapshot.eval?.status || 'unavailable';
    const mode = String(run.assistance_mode || '');
    const target = String(run.execution_target || '');
    const canInterrupt = run.status === 'running' && ['robocasa_sim', 'robosuite_sim'].includes(target)
      && !snapshot.station.interrupt;
    const comparison = snapshot.comparison || {policies:[], comparable:false};
    view.innerHTML = `<div class="intro-head"><div><div class="kicker">OPERATIONS / ${esc(run.run_id)}</div><h2>Attention workspace<span style="color:var(--orange)">_</span></h2><p class="lead">持久化 run 状态，每 3 秒刷新；收件箱只显示已发出的请求。</p></div>
      <label class="live-run-picker">RUN <select id="ab-live-run">${runs.map(item => `<option value="${esc(item.run_id)}" ${item.run_id === run.run_id ? 'selected' : ''}>${esc(item.run_id)} · ${esc(item.task_id)}</option>`).join('')}</select></label></div>
      <section class="panel run-board"><div class="run-head"><div class="run-identity"><span class="label">CURRENT RUN · ${esc(run.run_id)}</span><strong>${esc(run.suite)} / ${esc(run.task_id)}</strong><small>suite ${esc(run.suite)} · task ${esc(run.task_id)} · seed ${esc(run.seed)}</small></div><div class="run-head-right">${chip(run.status)}<span class="run-progress">ATTEMPTS <b>${snapshot.attempts.length}</b></span><span class="locked">${run.locked ? '▣ CONDITIONS LOCKED' : 'NOT STARTED'}</span></div></div>
      <div class="run-grid"><div class="run-module"><div class="module-heading"><span class="label">AGENTS</span><span class="minor">角色 · 状态 · 模型</span></div>
      <div class="agent-row"><span class="agent-icon">DEV</span><span class="agent-info"><b>Developer</b><small>${esc(latestAttempt?.status || '暂无 attempt')}</small><span class="model-tag">${esc(run.developer_model || '未记录')}</span></span>${chip(latestAttempt?.status || 'queued')}</div>
      <div class="agent-row eval"><span class="agent-icon">EVAL</span><span class="agent-info"><b>Evaluator</b><small>${esc(run.evaluator_model === 'native_evaluator' ? '每次 attempt 后由原生 evaluator 判定' : '模型状态随 Eval 会话更新')}</small><span class="model-tag">${esc(run.evaluator_model || '未记录')}</span></span>${chip(evalStatus)}</div></div>
      <div class="run-module"><div class="module-heading"><span class="label">ATTENTION POLICY</span><span class="locked">${run.locked ? 'LOCKED' : 'PRE-RUN'}</span></div><div class="policy-name">${esc(label(run.policy_id))}</div><p class="policy-desc">策略标识取自当前 run；执行条件在启动后由后端锁定。</p></div>
      <div class="run-module"><div class="module-heading"><span class="label">EXECUTION TARGET</span><span class="locked">${run.locked ? 'LOCKED' : 'PRE-RUN'}</span></div><div class="segmented"><span class="${target === 'simulation' || target.endsWith('_sim') ? 'selected' : ''}">Simulation</span><span class="${target !== 'simulation' && !target.endsWith('_sim') ? 'selected' : ''}">Real robot</span></div></div>
      <div class="run-module"><div class="module-heading"><span class="label">ASSISTANCE MODE</span><span class="locked">${run.locked ? 'LOCKED' : 'PRE-RUN'}</span></div><div class="segmented"><span class="${mode === 'benchmark_proxy' ? 'selected' : ''}">Benchmark<br>Proxy</span><span class="${mode === 'live_human_first' ? 'selected' : ''}">Live<br>Human-first</span></div></div></div></section>
      <div class="budgets" aria-label="Resource budget">${budgetCard('ASSISTANCE CREDITS', snapshot.resources.assistance, '', true)}${budgetCard('SIM / ROBOT TIME', snapshot.resources.execution_seconds, 's')}${budgetCard('TOKENS', snapshot.resources.tokens, '')}${budgetCard('GPU TIME', snapshot.resources.gpu_seconds, 's')}</div>
      <section class="panel outcome"><div class="panel-title"><h3>Success ↔ assistance</h3><span>TERMINAL RUNS · PERSISTED STORE</span></div><div class="outcome-grid"><div class="outcome-run"><span class="label">CURRENT RUN / ${esc(run.run_id)}</span><strong>${esc(label(run.status))}</strong><p>协助额度已用 ${esc(number(snapshot.resources.assistance.used))} / ${esc(number(snapshot.resources.assistance.limit))}。本轮结果与多轮成功率分别记录。</p></div><div class="outcome-summary"><span class="label">同任务 / 模式 / 目标 / 模型 / 预算</span>${comparison.policies.length ? `<div class="outcome-row outcome-head"><span>ATTENTION POLICY</span><span>成功 / 完成</span><span>平均实际求助</span></div>${comparison.policies.map(item => `<div class="outcome-row"><span>${esc(label(item.policy_id))}</span><span>${esc(item.successes)} / ${esc(item.runs)} · ${Math.round(item.success_rate * 100)}%</span><span>${esc(number(item.average_assistance_used))} credits</span></div>`).join('')}<p class="outcome-foot">${comparison.comparable ? '各策略 seed 集一致，可比较。' : '各策略 seed 集未对齐或不足两种策略；当前数字只作进度查看。'}</p>` : '<p class="live-empty-copy">暂无满足相同实验条件的已结束 run。</p>'}</div></div></section>
      <section class="panel"><div class="panel-title"><h3>Eval 诊断与最终结果</h3>${chip(evalStatus)}</div><div class="evidence-card"><span class="label">RUNNER / ${esc(run.run_id)}</span><p>原生结果：${snapshot.eval?.native_success == null ? '等待校验证据' : snapshot.eval.native_success ? '成功' : '失败'} · 停止原因：${esc(snapshot.eval?.stopped_reason || '无')} · 配置 SHA：${esc(snapshot.eval?.approved_config_sha256 || '未验证')}</p><p>Attempts：${esc((snapshot.eval?.attempt_ids || []).join(' · ') || '等待产物')}</p></div><div class="evidence-card"><span class="label">DIAGNOSTIC / ${esc(snapshot.eval?.model || 'parcc/GLM')}</span><p>${esc(snapshot.eval?.diagnosis || snapshot.eval?.error || '等待诊断收据')}</p></div></section>
      <div class="columns"><section class="panel"><div class="panel-title"><h3>Autonomous work</h3><span>${liveAgents.length} AGENTS · ${snapshot.attempts.length} ATTEMPTS</span></div><div class="work-list">${liveAgents.map(agent => `<button type="button" class="work-card ${agent.status === 'running' ? 'active' : 'waiting'} ab-work-agent" data-dag-skill="${esc(agent.skill)}"><span class="card-top"><span class="label">${esc(String(agent.agent_type || 'agent').toUpperCase())} · ${esc(agent.target || 'default')}</span>${chip(agent.status)}</span><strong>${esc(agent.skill)}</strong><p>${esc(agent.agent_id)} · ${esc(agent.model || '模型未记录')} · 打开 Skill DAG 节点</p></button>`).join('')}${(snapshot.work_items || []).map(item => `<div class="work-card blocked"><div class="card-top"><span class="label">RESOURCE BLOCK · ${esc(item.work_id)}</span>${chip(item.state)}</div><strong>${item.state === 'blocked' ? `${esc(label(item.resource))}不足` : '试验执行失败'}</strong><p>${item.state === 'blocked' ? `需要 ${esc(number(item.required))}，剩余 ${esc(number(item.remaining))}。` : ''}${esc(item.reason)}</p></div>`).join('')}${snapshot.attempts.map(item => `<div class="work-card ${item.status === 'running' ? 'active' : item.status === 'failed' ? 'blocked' : 'waiting'}"><div class="card-top"><span class="label">EXECUTION ATTEMPT · ${esc(item.index + 1)}</span>${chip(item.status)}</div><strong>${esc(item.attempt_id)}</strong><p>开始于 ${esc(dateTime(item.started_at))}</p></div>`).join('') || (liveAgents.length ? '' : '<p class="live-empty-copy">暂无活动 agent 或执行 attempt。</p>')}</div></section>
      <section class="panel"><div class="panel-title"><h3>Attention inbox</h3><span>REQUEST EVENTS ONLY</span></div><p class="inbox-explainer">只列已持久化的 request_id；Dev / Eval 进度不会自动成为审批。</p><div class="inbox-list">${sortedRequests.map(requestRow).join('') || '<p class="live-empty-copy">当前 run 尚无升级请求。</p>'}</div></section>
      <section class="panel"><div class="panel-title"><h3>Live station</h3>${chip(snapshot.station.status)}</div>${snapshot.station.camera_url ? `<div class="ab-station-camera"><img id="ab-camera-frame" src="${esc(snapshot.station.camera_url)}&t=${Date.now()}" alt="Robosuite public RGB camera frame"><span>PUBLIC RGB · ${esc(run.execution_target)}</span></div>` : `<div class="live-evidence-empty"><strong>未接入实时画面</strong><p>执行目标：${esc(run.execution_target)}。启动 UI 时配置模拟器相机服务。</p></div>`}<div class="station-data"><div class="station-tile"><span>RUN STATE</span><b>${esc(run.status)}</b></div><div class="station-tile"><span>LATEST EXECUTION</span><b>${esc(latestTrace?.execution_id || '未记录')}</b></div><div class="station-tile"><span>INTERRUPT</span><b>${esc(snapshot.station.interrupt?.state || '未请求')}</b></div></div><button class="danger" id="ab-emergency-interrupt" type="button" ${canInterrupt ? '' : 'disabled'}><b>■ 紧急中断</b><small>${canInterrupt ? '停止模拟器 run' : snapshot.station.interrupt ? esc(snapshot.station.interrupt.state) : '仅运行中的受控模拟器可用'}</small></button></section></div>
      ${launchPanel()}
      ${dagPanel()}
      ${sessionPanel()}
      ${traceDetail(snapshot, chosen)}<p class="minor" id="ab-live-message" aria-live="polite">实时投影 · ${esc(run.run_id)} · 请求、预算与 trace 在刷新后从 SQLite 恢复。</p>`;
    view.querySelector('#ab-live-run')?.addEventListener('change', event => {
      selectedRun = event.target.value;
      selectedRequest = null;
      const url = new URL(location.href);
      url.searchParams.set('run', selectedRun);
      history.replaceState(null, '', url);
      lastRendered = '';
      refresh();
    });
    view.querySelectorAll('[data-live-request]').forEach(button => button.addEventListener('click', () => {
      selectedRequest = button.dataset.liveRequest;
      render(currentSnapshot);
    }));
    view.querySelectorAll('[data-respond]').forEach(button => button.addEventListener('click', async () => {
      const decision = button.dataset.respond;
      const payload = decision === 'hint'
        ? {content: view.querySelector('#ab-hint-content')?.value || ''}
        : {decision};
      button.disabled = true;
      try {
        const response = await fetch(`/api/requests/${encodeURIComponent(chosen.request_id)}/respond`, {
          method: 'POST', headers: {'Content-Type':'application/json',
            'X-Attention-Token': root.dataset.controlToken || ''},
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        lastRendered = '';
        await refresh();
      } catch (error) {
        view.querySelector('#ab-live-message').textContent = `答复未提交：${error.message}`;
        button.disabled = false;
      }
    }));
    async function requestAction(action, button) {
      button.disabled = true;
      try {
        const response = await fetch(`/api/requests/${encodeURIComponent(chosen.request_id)}/${action}`, {
          method: 'POST', headers: {'X-Attention-Token': root.dataset.controlToken || ''},
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        lastRendered = '';
        await refresh();
      } catch (error) {
        view.querySelector('#ab-live-message').textContent = `请求操作失败：${error.message}`;
        button.disabled = false;
      }
    }
    view.querySelectorAll('[data-defer-request]').forEach(button => button.addEventListener('click', () => requestAction('defer', button)));
    view.querySelectorAll('[data-cancel-request]').forEach(button => button.addEventListener('click', () => requestAction('cancel', button)));
    view.querySelector('#ab-emergency-interrupt')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(run.run_id)}/interrupt`, {
          method: 'POST', headers: {'X-Attention-Token': root.dataset.controlToken || ''},
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        view.querySelector('#ab-live-message').textContent = '中断请求已写入运行态；等待执行器确认停止。';
        lastRendered = '';
        await refresh();
      } catch (error) {
        view.querySelector('#ab-live-message').textContent = `中断请求失败：${error.message}`;
        button.disabled = false;
      }
    });
    bindOrchestratorControls();
    bindLaunch();
  }

  function bindOrchestratorControls() {
    view.querySelector('[data-dag-dispatch]')?.addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const response = await fetch('/api/orchestrator/dispatch', {
          method: 'POST', headers: {'Content-Type': 'application/json',
            'X-Attention-Token': root.dataset.controlToken || ''},
          body: JSON.stringify({graph: orchestrator.graph}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        dagActionMessage = result.spawned.length ? `已派发：${result.spawned.join(' · ')}` : '自动派发已启动；当前没有新的就绪节点。';
        lastRendered = '';
        await refresh();
      } catch (error) {
        dagActionMessage = `派发失败：${error.message}`;
        if (currentSnapshot) render(currentSnapshot);
        else renderNoRuns();
      }
    });
    view.querySelectorAll('[data-dag-skill]').forEach(button => button.addEventListener('click', () => {
      selectedSkill = button.dataset.dagSkill;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
      view.querySelector('.work-detail')?.scrollIntoView({block:'nearest'});
    }));
    view.querySelectorAll('[data-session-filter]').forEach(button => button.addEventListener('click', () => {
      sessionFilter = button.dataset.sessionFilter;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
    }));
    view.querySelectorAll('[data-run-filter]').forEach(button => button.addEventListener('click', () => {
      runFilter = button.dataset.runFilter;
      if (currentSnapshot) render(currentSnapshot);
      else renderNoRuns();
    }));
    view.querySelectorAll('[data-session-index]').forEach(button => button.addEventListener('click', () => openSession(Number(button.dataset.sessionIndex))));
    view.querySelector('#ab-graph-form')?.addEventListener('submit', event => {
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

  function renderNoRuns() {
    currentSnapshot = null;
    view.innerHTML = `<div class="intro-head"><div><div class="kicker">OPERATIONS / SKILL GRAPH</div><h2>Attention workspace<span style="color:var(--orange)">_</span></h2><p class="lead">暂无 Attention run；可选择已批准的真实 Service 配置启动。</p></div></div>${launchPanel()}${dagPanel()}${sessionPanel()}`;
    bindOrchestratorControls();
    bindLaunch();
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
      orchestratorError = `Orchestrator ${error.message}`;
      if (!orchestrator) { sessionHistory = []; sessions = []; }
      else mergeSessions();
    }
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      await refreshOrchestrator();
      if (!launchOptions) {
        const optionsResponse = await fetch('/api/run-options', {cache:'no-store'});
        if (optionsResponse.ok) launchOptions = await optionsResponse.json();
      }
      const listResponse = await fetch('/api/runs', { cache: 'no-store' });
      if (!listResponse.ok) throw new Error(`Run list HTTP ${listResponse.status}`);
      const listed = await listResponse.json();
      runs = listed.runs;
      launches = listed.launches || [];
      if (!runs.length) {
        const fingerprint = JSON.stringify({orchestrator, sessions, launches, launchOptions,
          orchestratorError, graphOverride});
        if (fingerprint !== lastRendered) {
          lastRendered = fingerprint;
          renderNoRuns();
        }
        return;
      }
      if (!runs.some(item => item.run_id === selectedRun)) selectedRun = runs[0].run_id;
      const response = await fetch(`/api/runs/${encodeURIComponent(selectedRun)}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`Run snapshot HTTP ${response.status}`);
      const snapshot = await response.json();
      const fingerprint = JSON.stringify({ snapshot, runs, launches, orchestrator: orchestrator && {
        ...orchestrator, live_sessions: (orchestrator.live_sessions || []).map(({timestamp, ...session}) => session),
      }, sessions: sessions.map(session => session.in_progress ? {...session, timestamp: null} : session),
      orchestratorError, graphOverride });
      if (fingerprint !== lastRendered) {
        lastRendered = fingerprint;
        render(snapshot);
      }
    } catch (error) {
      const message = `读取 AttentionStore 失败：${error.message}`;
      const status = view.querySelector('#ab-live-message');
      if (status) status.textContent = message;
      else view.innerHTML = `<section class="panel"><h2>无法加载运行数据</h2><p class="lead">${esc(message)}</p></section>`;
    } finally {
      refreshing = false;
    }
  }

  view.innerHTML = '<section class="panel"><h2>读取 AttentionStore…</h2></section>';
  root.classList.add('ready');
  refresh();
  setInterval(refresh, 3000);
  setInterval(() => {
    const frame = view.querySelector('#ab-camera-frame');
    if (frame && currentSnapshot?.station?.camera_url) {
      frame.src = `${currentSnapshot.station.camera_url}&t=${Date.now()}`;
    }
  }, 1000);
})();
