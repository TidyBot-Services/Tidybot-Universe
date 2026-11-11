(function (global) {
  'use strict';
  // The shared lobby of the Run page: everyone's runs on the one robot, the queue, and the read-only views of other
  // people's runs. The page keeps its five modules (01-05); this file only supplies the sidebar block, the queue and
  // privacy variants of 02/04/05, and the calls for creating, cancelling and answering. When /api/lobby is not served
  // the whole file stays inert (enabled is false and every hook returns nothing).
  const doc = global.document, rootEl = doc.getElementById('attentionbench-clear') || doc.documentElement;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const read = key => { try { return global.localStorage.getItem(key); } catch (_) { return null; } };
  const write = (key, value) => { try { global.localStorage.setItem(key, value); } catch (_) { /* storage can be blocked */ } };
  const state = {enabled: false, snap: null, cards: new Map(), hooks: {}, timer: null, fp: '', started: false, seen: new Map(),
    mode: ['mine', 'all', 'queue'].includes(read('attention.lobby.mode')) ? read('attention.lobby.mode') : 'all',
    group: ['task', 'person', 'status'].includes(read('attention.lobby.group')) ? read('attention.lobby.group') : 'person'};

  const minutes = seconds => seconds == null ? '' : seconds < 90 ? 'under 2 min' : `~${Math.round(seconds / 60)} min`;
  const clock = seconds => { const s = Math.max(0, Math.round(seconds || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const RESULT = {solved: 'Solved', failed: 'Failed', limit: 'Limit reached'};
  const SOURCE = {lab_codex: 'Lab Codex', lab_glm: 'Lab GLM', own_key: 'My own key'};

  function ingest(snap) {
    state.snap = snap;
    state.cards = new Map();
    const all = [...(snap.robot?.open || []), snap.robot?.running, ...(snap.queue || []), ...(snap.recent || [])].filter(Boolean);
    for (const card of all) state.cards.set(card.experiment_id, card);
  }
  // Clocks that tick every second (waiting time, estimates) must not force a redraw of a form someone is typing in.
  const stable = c => c && {...c, help_waiting_seconds: null, eta_start_seconds: c.eta_start_seconds == null ? null : Math.round(c.eta_start_seconds / 60)};
  const fingerprint = () => JSON.stringify([(state.snap?.queue || []).map(stable), (state.snap?.recent || []).map(stable), stable(state.snap?.robot?.running), (state.snap?.robot?.open || []).map(stable), state.snap?.counts,
    state.snap?.me, state.snap?.clock?.speed, state.snap?.events?.[0]?.id]);

  async function poll() {
    // The lobby lives on the Remote page only; the Run and Replay pages keep their own runs.
    if (rootEl.dataset.remote !== '1') return stop();
    try {
      const response = await fetch('/api/lobby', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) { if (!state.enabled) return stop(); throw new Error(`lobby ${response.status}`); }
      const before = state.cards;
      ingest(await response.json());
      paintMe();
      const first = !state.enabled;
      state.enabled = true;
      rootEl.dataset.lobby = '1'; doc.documentElement.dataset.lobby = '1';
      if (snapshot_demo() && !doc.querySelector('[data-lobby-demo]')) rootEl.insertAdjacentHTML('afterbegin', `<div class="lb-demo" data-lobby-demo><i></i>DEMO · replays ${esc(state.snap.demo_info?.recordings ?? '')} real recordings · simulated time ×<span data-lb-speed-now>${esc(state.snap.clock?.speed)}</span> · no GPU, no real provider</div>`);
      doc.querySelectorAll('[data-lb-speed-now]').forEach(n => { n.textContent = String(state.snap.clock?.speed); });
      const next = fingerprint();
      for (const [id, card] of state.cards) notify(before.get(id), card);
      doc.querySelectorAll('[data-lb-wait]').forEach(node => { const c = state.cards.get(node.dataset.lbExp); if (c) node.textContent = `Waiting ${clock(c.help_waiting_seconds)} · auto-skip at ${clock(state.snap.limits.help_wait_seconds)}`; });
      // The waiting clock ticks every second without changing the data, so it does not force a redraw.
      if (first || next !== state.fp) { state.fp = next; state.hooks.changed?.(first); }
    } catch (_) { /* keep the last picture; the next poll tries again */ }
    state.timer = global.setTimeout(next, doc.hidden ? 5000 : 1500);
  }
  // One poll at a time: a poll asked for now waits for the one in flight, then reads again, so it sees what was just made.
  function next() { state.inflight = poll().finally(() => { state.inflight = null; }); return state.inflight; }
  async function refresh() {
    if (!state.started || rootEl.dataset.remote !== '1') return;
    global.clearTimeout(state.timer);
    if (state.inflight) await state.inflight;
    global.clearTimeout(state.timer);
    return next();
  }
  const snapshot_demo = () => !!state.snap?.demo;
  function stop() { state.enabled = false; delete rootEl.dataset.lobby; delete doc.documentElement.dataset.lobby; }

  function notify(before, card) {
    if (!card.mine || !before || before.state === card.state) return;
    if (card.state === 'waiting_human') state.hooks.toast?.('The robot is asking you for help — answer in 04.', 'info');
    else if (card.state === 'running' && before.state === 'queued') state.hooks.toast?.('Your run is starting now.', 'info');
    else if (card.start_error && before.state === 'queued') state.hooks.toast?.('Your run could not start. See 02.', 'error');
  }

  const card = experimentId => (state.enabled && experimentId ? state.cards.get(experimentId) : null) || null;
  const isMine = record => !record?.lobby || record.lobby.mine === true;

  // ---- record helpers used by the sidebar (the page's own run records carry the card as .lobby) ----
  function tone(record) {
    const c = record.lobby;
    if (!c) return null;
    return {queued: 'queued', running: 'running', waiting_human: c.mine ? 'waiting' : 'running', finished: c.result === 'solved' ? 'success' : 'failed', cancelled: 'idle'}[c.state] || 'idle';
  }
  function statusWord(record) {
    const c = record.lobby;
    if (!c) return null;
    if (c.state === 'queued') return `Queued #${c.queue_position}`;
    if (c.state === 'running') return 'Running';
    if (c.state === 'waiting_human') return c.mine ? 'Needs you' : 'Needs a person';
    if (c.state === 'cancelled') return 'Cancelled';
    if (c.state === 'created') return 'Not started';
    return RESULT[c.result] || 'Finished';
  }
  const isActive = record => ['running', 'waiting_human'].includes(record.lobby?.state);
  const visible = record => !state.enabled || state.mode !== 'mine' || isMine(record);

  // ---- sidebar block: whose runs, grouping, and the one robot with its queue ----
  const seg = (attr, items, current) => `<div class="lb-seg" role="group">${items.map(([key, name]) => `<button type="button" ${attr}="${key}" aria-pressed="${current === key}">${name}</button>`).join('')}</div>`;
  // Rows are plain elements (not buttons): the page's own button styling would box and shade every line.
  const open = c => `role="button" tabindex="0" data-lb-open="${esc(c.experiment_id)}"`;
  const row = (c, extra = '') => `<div class="lb-line${c.mine ? ' me' : ''}" ${open(c)}><span class="lb-n">${c.state === 'queued' ? c.queue_position : c.state === 'finished' ? (c.result === 'solved' ? '✓' : '✕') : '●'}</span><b>${c.mine ? 'You' : esc(c.nickname)}</b><span class="lb-sub">${extra}</span></div>`;

  // Remote time-share: several runs are open at once and take turns on the one robot. Each says where it stands.
  const shared = () => Array.isArray(state.snap?.robot?.open);
  const robotWord = c => c.state === 'waiting_human' ? (c.mine ? 'Needs you' : 'Needs a person') : c.robot?.state === 'executing' ? 'On the robot'
    : c.robot?.state === 'waiting' ? (c.robot.ahead > 0 ? `Waiting for robot · ${c.robot.ahead} ahead` : 'Waiting for robot') : 'Writing code';
  const openLine = c => `<div class="lb-line lb-run${c.mine ? ' me' : ''}" ${open(c)} data-lb-robot="${esc(c.robot?.state || '')}"><span class="lb-pill${c.state === 'waiting_human' || c.robot?.state === 'waiting' ? ' wait' : ''}">${esc(robotWord(c))}</span><b>${c.mine ? 'You' : esc(c.nickname)}</b><span class="lb-sub">${c.attempt.current} / ${c.attempt.limit}</span></div>`;
  function sharedPinned() {
    const snap = state.snap, opened = snap.robot.open, queue = snap.queue || [];
    const head = opened.length ? opened.map(openLine).join('') : '<p class="lb-idle">No run is open. The robot is free.</p>';
    return `<section class="lb-pin" data-lobby-pin data-lobby-shared aria-label="The robot and the queue"><h3>One robot · ${opened.length} of ${esc(snap.robot.slots)} runs open</h3>${head}${queue.slice(0, 4).map(c => row(c, 'in line')).join('')}${queue.length > 4 ? `<p class="lb-more">+ ${queue.length - 4} more waiting</p>` : ''}</section>`;
  }
  function pinned() {
    if (shared()) return sharedPinned();
    const snap = state.snap, running = snap.robot?.running, queue = snap.queue || [];
    const head = running ? `<div class="lb-line lb-run${running.mine ? ' me' : ''}" ${open(running)}><span class="lb-pill${running.state === 'waiting_human' ? ' wait' : ''}">${running.state === 'waiting_human' ? (running.mine ? 'Needs you' : 'Needs a person') : 'Running'}</span><b>${running.mine ? 'You' : esc(running.nickname)}</b><span class="lb-sub">${running.attempt.current} / ${running.attempt.limit}</span></div>` : '<p class="lb-idle">The robot is free.</p>';
    return `<section class="lb-pin" data-lobby-pin aria-label="The robot and the queue"><h3>One robot · now &amp; next</h3>${head}${queue.slice(0, 4).map(c => row(c, minutes(c.eta_start_seconds))).join('')}${queue.length > 4 ? `<p class="lb-more">+ ${queue.length - 4} more waiting</p>` : ''}${clockHtml()}</section>`;
  }
  // Demo only: how fast the simulated robot works compared with the clock on the wall.
  function clockHtml() {
    if (!state.snap.demo) return '';
    return `<div class="lb-clock" data-lobby-clock><span class="lb-label">Demo speed</span>${seg('data-lb-speed', [1, 5, 20, 60].map(n => [String(n), `×${n}`]), String(state.snap.clock?.speed))}</div>`;
  }
  function queueView() {
    if (shared()) {
      const snap = state.snap, opened = snap.robot.open, queue = snap.queue || [], recent = snap.recent || [];
      return `<section class="lb-queue" data-lobby-queue data-lobby-shared aria-label="Queue"><h3>Open · ${opened.length} of ${esc(snap.robot.slots)} · they take turns on the robot</h3>${opened.map(openLine).join('') || '<p class="lb-idle">Nobody. The robot is free.</p>'}
      <h3>Waiting for a slot · first come, first served</h3>${queue.map(c => row(c, `#${c.queue_position}`)).join('') || '<p class="lb-idle">Nobody is waiting.</p>'}
      <h3>Just finished</h3>${recent.slice(0, 8).map(c => row(c, `${RESULT[c.result] || 'Finished'} · ${c.attempt.current} / ${c.attempt.limit}`)).join('')}</section>`;
    }
    const snap = state.snap, running = snap.robot?.running, queue = snap.queue || [], recent = snap.recent || [];
    return `<section class="lb-queue" data-lobby-queue aria-label="Queue"><h3>Running</h3>${running ? row(running, `${running.attempt.current} / ${running.attempt.limit}`) : '<p class="lb-idle">Nobody. The robot is free.</p>'}
      <h3>Waiting · first come, first served</h3>${queue.map(c => row(c, minutes(c.eta_start_seconds))).join('') || '<p class="lb-idle">Nobody is waiting.</p>'}
      <h3>Just finished</h3>${recent.slice(0, 8).map(c => row(c, `${RESULT[c.result] || 'Finished'} · ${c.attempt.current} / ${c.attempt.limit}`)).join('')}${clockHtml()}</section>`;
  }
  function controlsHtml() {
    if (!state.enabled) return '';
    return `${snapshot_demo() ? '<p class="lb-demo-note" data-lobby-demo-note><b>DEMO DATA</b> · simulated runs and times, not real results</p>' : ''}<div class="lb-controls" data-lobby-controls>${seg('data-lb-mode', [['mine', 'Mine'], ['all', 'All'], ['queue', 'Queue']], state.mode)}${state.mode === 'queue' ? '' : `<span class="lb-label">Group by</span>${seg('data-lb-group', [['task', 'Task'], ['person', 'Person'], ['status', 'Status']], state.group)}`}</div>`;
  }
  function pinnedHtml() {
    if (!state.enabled) return '';
    return state.mode === 'queue' ? queueView() : pinned();
  }
  function bindSidebar(sidebar, rerender) {
    sidebar.dataset.lobbyMode = state.enabled ? state.mode : '';
    sidebar.querySelectorAll('[data-lb-mode]').forEach(b => b.addEventListener('click', () => { state.mode = b.dataset.lbMode; write('attention.lobby.mode', state.mode); rerender(); }));
    sidebar.querySelectorAll('[data-lb-group]').forEach(b => b.addEventListener('click', () => { state.group = b.dataset.lbGroup; write('attention.lobby.group', state.group); rerender(); }));
    sidebar.querySelectorAll('[data-lb-speed]').forEach(b => b.addEventListener('click', async () => { await send('/api/lobby/clock', {speed: Number(b.dataset.lbSpeed)}); state.hooks.changed?.(false, true); }));
    sidebar.querySelectorAll('[data-lb-open]').forEach(b => { b.addEventListener('pointerdown', e => e.preventDefault()); const go = () => state.hooks.open?.(b.dataset.lbOpen); b.addEventListener('click', go); b.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } }); });
  }
  // Group key and title for the sidebar's list when it is not grouped by task.
  function groupOf(record) {
    if (!state.enabled || state.group === 'task') return null;
    const c = record.lobby;
    // 11-09 (user): your own runs first as "My runs", then one group per person (all of that person's runs together).
    if (state.group === 'person') return c ? (c.mine ? ['My runs', '0me'] : [personOf(c), `1${personOf(c)}`]) : ['You · earlier runs', 'zz-earlier'];
    const word = c ? ({queued: 'Waiting in line', running: 'Running now', waiting_human: 'Running now', finished: 'Finished', cancelled: 'Finished'}[c.state]) : 'Finished';
    return [word, ({'Running now': '1', 'Waiting in line': '2', 'Finished': '3'})[word] + word];
  }

  // ---- 02, 04 and 05 for the states a shared run can be in ----
  const tiles = items => `<div class="lb-tiles">${items.map(([big, name]) => `<div><b>${esc(big)}</b>${esc(name)}</div>`).join('')}</div>`;
  function outcomeHtml(snapshot) {
    const c = snapshot.lobby;
    if (c?.start_error && c.state === 'created') return `<div class="op-outcome failed" data-operator-outcome="start-failed" role="alert"><strong>✕ DID NOT START</strong><div><p>${c.mine ? 'Your run' : `${esc(c.nickname)}'s run`} reached the front of the line but could not start, so it left the line.</p><p>${esc(c.start_error)}</p></div></div>`;
    if (!c || !['queued', 'running', 'waiting_human'].includes(c.state) || (c.real && c.state !== 'queued')) return '';
    const k = c.attempt.current, n = c.attempt.limit, owner = c.mine ? 'You are' : `${esc(c.nickname)} is`;
    if (c.state === 'queued' && shared()) {
      const ahead = (state.snap.queue || []).filter(q => q.queue_position < c.queue_position).length, slots = state.snap.robot.slots;
      return `<div class="op-outcome queued" data-operator-outcome="queued" role="status"><strong>▣ QUEUED</strong><div><p>${owner} #${c.queue_position} in line for a run slot.</p><p>Up to ${esc(slots)} runs are open at once and take turns on the robot.${c.mine ? ' Your place is saved; you can close this page.' : ''}</p></div></div>${tiles([[`#${c.queue_position}`, 'Place in line'], [`${state.snap.robot.open.length} / ${slots}`, 'Runs open'], [String(ahead), 'Ahead of you']])}`;
    }
    if (c.state === 'queued') {
      const ahead = (state.snap.queue || []).filter(q => q.queue_position < c.queue_position).length + (state.snap.robot?.running ? 1 : 0);
      return `<div class="op-outcome queued" data-operator-outcome="queued" role="status"><strong>▣ QUEUED</strong><div><p>${owner} #${c.queue_position} in line · ${minutes(c.eta_start_seconds)} (estimate).</p><p>The robot runs one task at a time.${c.mine ? ' Your place is saved; you can close this page.' : ''}</p></div></div>${tiles([[`#${c.queue_position}`, 'Place in line'], [minutes(c.eta_start_seconds) || '—', 'Estimated start'], [state.snap.robot?.running?.nickname || '—', 'Running now'], [String(ahead), 'Ahead (running + waiting)']])}`;
    }
    if (c.state === 'waiting_human' && c.mine) return `<div class="op-outcome waiting" data-operator-outcome="waiting" role="status"><strong>● NEEDS YOU</strong><div><p>Attempt ${k} of ${n} failed. The robot is asking for help.</p><p>Answer in 04 · auto-skip at ${clock(state.snap.limits.help_wait_seconds)}</p></div></div>`;
    if (c.state === 'waiting_human') {
      const ago = c.started_at && c.started_at > 1e9 ? Date.now() / 1000 - c.started_at : -1;
      return `<div class="op-outcome running" data-operator-outcome="running" role="status"><strong>● RUNNING</strong><div><p>Attempt ${k} of ${n} failed. Waiting for a person to answer.</p>${ago >= 0 && ago < 86400 ? `<p>Started ${clock(ago)} ago</p>` : ''}</div></div>`;
    }
    return `<div class="op-outcome running" data-operator-outcome="running" role="status"><strong>● RUNNING</strong><div><p>Attempt ${Math.min(k + 1, n)} of ${n} is running.</p></div></div>`;
  }
  // Returns the whole 04 section for the lobby states, or '' to let the page draw it from the run's own data.
  function helpHtml(snapshot, heading, HumanReply, replayMoments) {
    const c = snapshot.lobby;
    if (!c || c.real) return '';  // A real run answers from its own panel.
    const head = heading || '';
    if (!c.mine) {
      if (c.state !== 'waiting_human') return '';
      const w = c.waiting || {};
      return `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" aria-label="Help and replies">${head}<div class="op-action none"><h4>${esc(c.nickname)} is being asked for help</h4><p>After attempt ${esc(w.attempt ?? c.attempt.current)}: ${esc(w.failure || 'the attempt did not succeed')}.</p><p>Asked: “${esc(w.question || 'What should the robot do differently on its next attempt?')}”</p></div></section>`;
    }
    if (c.state === 'queued') return `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" aria-label="Help and replies">${head}<div class="op-action none"><h4>Nothing to do yet</h4><p>When the robot asks for help it appears here and a notice shows in the top bar.</p></div></section>`;
    if (c.state === 'running') return `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" aria-label="Help and replies">${head}<div class="op-action none"><h4>Nothing to do right now</h4><p>The robot is working on attempt ${esc(c.attempt.current + 1)}. If it asks for help, it appears here.</p></div></section>`;
    if (c.state === 'waiting_human') {
      const w = c.waiting || {};
      const a = `<p>After attempt ${esc(w.attempt ?? c.attempt.current)}: ${esc(w.failure || 'the attempt did not succeed')}.</p>`;
      const b = `<p>Asked: “${esc(w.question || 'What should the robot do differently on its next attempt?')}”</p>`;
      const form = `${HumanReply.categoryChips('lb-cat')}<label class="op-guidance-label">Your reply<textarea data-lb-text maxlength="4000" placeholder="Write a short tip in plain words, e.g. &quot;Use smaller steps when moving the arm toward the cup.&quot;">${esc(state.draft || '')}</textarea></label>
        <div class="op-controls"><button type="button" class="ab-button-primary" data-lb-send>Send reply</button>${HumanReply.skipButton()}<small data-lb-wait data-lb-exp="${esc(c.experiment_id)}">Waiting ${esc(clock(c.help_waiting_seconds))} · auto-skip at ${esc(clock(state.snap.limits.help_wait_seconds))}</small></div><p role="status" data-lb-message></p>`;
      const next = '<p>Simulated run: your reply does not change the recorded result. The next attempt starts right after you answer.</p>';
      return `<section class="panel detail ab-current-request" data-core-region="request" data-state="waiting" aria-label="Help and replies">${head}<div class="op-action need"><h4>The robot needs your help</h4>${HumanReply.blocks({a, b, c: form, d: next})}</div></section>`;
    }
    return '';
  }
  // Everything in the lobby is public: there is no switch, and everyone reads the same panels.
  const sharingHtml = () => '';
  const resourcesHtml = () => '';

  async function send(path, body, token) {
    const response = await fetch(path, {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json', 'X-Attention-Token': token || rootEl.dataset.controlToken || ''}, body: JSON.stringify(body || {})});
    const value = await response.json().catch(() => ({}));
    return {ok: response.ok, status: response.status, value};
  }
  const errorText = value => value?.error?.message || ({not_waiting: 'The robot is not waiting for you any more.', not_owner: 'This run belongs to someone else.', already_queued: 'You already have a run waiting.', no_demo_source: 'This task has no demo recording.'})[value?.error?.code] || 'That did not work. Please try again.';

  // A redraw (a poll that changed something) must not take the form away from someone who is typing in it.
  function beforeRender(view) {
    const field = view.querySelector('[data-lb-text]');
    state.focus = null;
    if (!field) return;
    state.draft = field.value;
    if (doc.activeElement === field) state.focus = [field.selectionStart, field.selectionEnd];
  }
  function bindHelp(view, snapshot, rerender) {
    const c = snapshot.lobby;
    if (!c || c.real) return;  // A real run answers from its own panel, with its own skip button.
    const typing = view.querySelector('[data-lb-text]');
    if (typing && state.focus) { typing.focus({preventScroll: true}); typing.setSelectionRange(...state.focus); state.focus = null; }
    view.querySelector('[data-lb-text]')?.addEventListener('input', e => { state.draft = e.target.value; });
    const submit = async outcome => {
      const message = view.querySelector('[data-lb-message]'), text = (state.draft || '').trim();
      if (outcome === 'answered' && !text) { message.textContent = 'Write something first, or choose “skip”.'; message.className = 'hr-bad'; return; }
      const category = view.querySelector('[data-hr-category] input:checked')?.value;
      view.querySelectorAll('[data-lb-send],[data-hr-skip]').forEach(b => { b.disabled = true; });
      const result = await send(`/api/lobby/runs/${encodeURIComponent(c.run_id)}/reply`, {outcome, ...(category ? {category} : {}), ...(outcome === 'answered' ? {content: text} : {})});
      if (result.ok) { state.draft = ''; state.hooks.changed?.(false, true); }
      else { message.textContent = errorText(result.value); message.className = 'hr-bad'; view.querySelectorAll('[data-lb-send],[data-hr-skip]').forEach(b => { b.disabled = false; }); }
    };
    view.querySelector('[data-lb-send]')?.addEventListener('click', () => submit('answered'));
    view.querySelector('[data-hr-skip]')?.addEventListener('click', () => submit('skipped'));
    // Keep the waiting clock moving without redrawing the form the person is typing in.
    const wait = view.querySelector('[data-lb-wait]');
    if (wait) wait.textContent = `Waiting ${clock(c.help_waiting_seconds)} · auto-skip at ${clock(state.snap.limits.help_wait_seconds)}`;
  }

  // The marker for CSS: which kind of run is on screen decides which write controls the page shows.
  function markView(snapshot) {
    const c = snapshot?.lobby;
    if (c) { rootEl.dataset.lobbyView = c.mine ? 'mine' : 'other'; rootEl.dataset.lobbyState = c.state; } else { delete rootEl.dataset.lobbyView; delete rootEl.dataset.lobbyState; }
  }
  // Which real recording a simulated run replays (shown in the run's detail, never in the sidebar list).
  const provenance = c => c.recording ? `<p class="lb-prov" data-lobby-prov><b>Simulated</b> · replays the recording of <code>${esc(String(c.recording.id).slice(0, 16))}…</code> (${esc(c.recording.attempts)} recorded attempts)${c.mine ? '. Your answers do not change what happens next.' : '.'}</p>` : '';
  // 11-09 (user): one avatar per person: a letter in a colour fixed by when they joined; you are always orange.
  const personOf = c => String(c?.person || c?.nickname || 'Someone').split(' · ')[0];
  const avatar = (c, size = '') => {
    if (!c) return '';
    const name = personOf(c), n = Number((name.match(/-(\d+)$/) || [])[1]) || [...name].reduce((h, ch) => (h * 31 + ch.codePointAt(0)) % 9973, 0) + 1;
    return `<span class="lb-av ${c.mine ? 'me' : `p${n > 0 ? (n - 1) % 8 : 0}`}${size ? ` ${size}` : ''}" aria-hidden="true">${esc(name[0] || '?')}</span>`;
  };
  const groupAvatar = rows => rows?.groupTitle && rows[0]?.lobby ? avatar(rows[0].lobby) : '';
  const groupDivider = (key, index, list) => state.enabled && state.group === 'person' && String(key).startsWith('lobby:1') && !String(list[index - 1]?.[0] || '').startsWith('lobby:1') ? '<p class="lb-divider" data-lb-divider>Other people</p>' : '';
  // The header says who you are, with the same avatar the lists use.
  function paintMe() {
    const name = state.snap?.me?.nickname, bar = doc.querySelector('.op-top-actions');
    let chip = doc.querySelector('[data-lb-me]');
    if (!name || !bar) { chip?.remove(); return; }
    if (!chip) { chip = doc.createElement('span'); chip.className = 'lb-me'; chip.dataset.lbMe = ''; bar.insertBefore(chip, bar.querySelector('[data-sign-out]')); }
    const html = `<span class="lb-me-k">You</span><span class="lb-me-v">${avatar({nickname: name, mine: true})}${esc(name)}</span>`;
    if (chip.innerHTML !== html) chip.innerHTML = html;
    chip.title = `Other people see you as ${name}`;
  }
  const mineCard = () => [...(state.snap?.robot?.open || []), state.snap?.robot?.running, ...(state.snap?.queue || []), ...(state.snap?.recent || [])].find(c => c?.mine);
  function banner(snapshot, taskName = value => value) {
    const c = snapshot?.lobby;
    if (!c) return '';
    if (c.mine) return provenance(c);
    // 11-09 (user): one bar. Your own open run is shown here, so the separate "Running" bar above is not drawn as well.
    const mine = mineCard(), open = mine && ['queued', 'running', 'waiting_human'].includes(mine.state);
    const since = Number(mine?.started_at || 0), minutes = since ? Math.max(0, Math.round((Date.now() / 1000 - since) / 60)) : null;
    const task = mine && (mine.task_key || mine.task_title) ? taskName(mine.task_key || mine.task_title) : '';
    const yours = open ? `<span class="lb-mine-run"><span class="lb-mine-dot" aria-hidden="true"></span><span class="lb-mine-label">Your run</span><b>${esc(statusWord({lobby: mine}))}</b>${task ? `<span>${esc(task)}</span>` : ''}${minutes === null ? '' : `<span>${minutes} min</span>`}</span>` : '';
    return `<div class="lb-readonly" data-lobby-readonly role="note">${avatar(c, 'lg')}<span class="lb-readonly-text"><small class="lb-readonly-chip">Watching someone else · Read-only</small><b>${esc(personOf(c))}'s run</b><small>You can watch, but not answer or stop it.</small></span>${yours}${mine ? '<button type="button" class="lb-back" data-lb-back>Back to my run →</button>' : ''}</div>${provenance(c)}`;
  }
  // The sidebar's times are on the demo's own clock, not the wall clock.
  const simAgo = text => state.snap?.demo ? `sim · ${text}` : text;
  function bindBanner(view) {
    view.querySelector('[data-lb-back]')?.addEventListener('click', () => {
      const mine = mineCard();
      if (mine) state.hooks.open?.(mine.experiment_id);
    });
  }

  // Remote's New run is main's form. Four things differ: no Codex (live.js), the shared host's limits, a goal ID the host
  // sets, and, until real runs are connected, a join that replays a recording instead of Start run.
  const NEW_RUN_LIMITS = {versions: 3, wall: 3600, help: 5, maxHelp: 5, versionsRange: '1–3 · Remote limit', wallRange: '1 hour · Remote limit',
    note: 'Remote limit: 3 versions · 1 hour · 5 help requests, because everyone shares one robot. Results are not comparable with the official 10-version runs.'};
  const newRunLimits = () => state.enabled ? NEW_RUN_LIMITS : null;
  function newRunNotice() {
    return state.enabled && state.snap?.demo ? '<p class="ab-launch-note ab-launch-source" data-remote-note><span>REMOTE</span><strong>Real runs are not connected yet.</strong><small>Set everything as in the main UI. Join demo replays a recording of this task and does not use these settings.</small></p>' : '';
  }
  // The extra section of the New run dialog: where the new run would stand in the queue.
  function newRunHtml() {
    if (!state.enabled) return '';
    const snap = state.snap, waiting = (snap.queue || []).length, running = snap.robot?.running ? 1 : 0;
    const place = waiting + running + 1;
    // 11-11 (user): a person may have more than one run at once; the gateway says how many (limits.active_runs).
    const mineIds = new Set([...(snap.robot?.open || []), snap.robot?.running, ...(snap.queue || [])].filter(c => c?.mine).map(c => c.experiment_id || c.id || c));
    const mineLimit = Number(snap.limits?.active_runs) > 0 ? Number(snap.limits.active_runs) : 1, mineBusy = mineIds.size >= mineLimit;
    const mineMany = `You already have ${mineLimit} runs open or in line; finish or cancel one first.`;
    if (shared()) {
      // 11-10 (user): no Queue section. Say one line only when it matters: you already have a run, or yours would wait.
      const opened = snap.robot.open.length, slots = snap.robot.slots, free = opened < slots && !waiting;
      const line = mineBusy ? mineLimit > 1 ? mineMany : 'You already have a run open or in line; finish or cancel it first.' : free ? '' : `All ${esc(slots)} run slots are in use; your run would be #${waiting + 1} in line.`;
      return line ? `<p class="ab-run-hint" data-lobby-newrun data-lobby-shared data-lobby-queue-note>${line}</p>` : '';
    }
    const line = mineBusy ? mineLimit > 1 ? mineMany : 'You already have a run in line; finish or cancel it first.' : place > 1 ? `The robot is busy; your run would be #${place} in line.` : '';
    return line || snap.demo ? `<p class="ab-run-hint" data-lobby-newrun data-lobby-queue-note>${line}${snap.demo ? `${line ? ' ' : ''}Demo engine: simulated run, no GPU, no real provider.` : ''}</p>` : '';
  }
  function shapeNewRun(form) {
    if (!state.enabled) return;
    const join = form.querySelector('[data-start-yes]');
    if (state.snap?.demo) join.insertAdjacentHTML('beforebegin', '<button type="button" class="ab-button-quiet" data-remote-start disabled title="Real runs are not connected yet">Start run</button>');
  }
  // Any task can be configured; only the demo join needs a recording of it.
  function syncJoin(form, profile) {
    if (!state.enabled || !state.snap?.demo) return;
    const join = form.querySelector('[data-start-yes]'), recorded = (state.snap?.demo_info?.tasks || []).includes(profile?.task_id);
    join.textContent = recorded ? 'Join demo' : 'No demo yet';
    if (!recorded) join.disabled = true;
  }
  const devSource = (provider, modelSource) => Object.values(provider?.roles || {}).some(role => role.credential !== 'lab') ? 'own_key' : modelSource === 'codex' ? 'lab_codex' : 'lab_glm';

  // The 01 cards of someone else's run name the source only, never the model or a key.
  function setupForOthers(snapshot, html) {
    const c = snapshot.lobby;
    if (!c || c.real) return html;
    const label = esc(SOURCE[c.dev_source] || 'Lab');
    const dev = /(<span>Dev agent<\/span><b>)[^<]*(<\/b><small>)([^<]*)(<\/small>)/;
    const eval_ = /(<span>Eval agent<\/span><b>)[^<]*(<\/b>)/;
    if (c.mine) return html.replace(dev, `$1${label}$2$3$4`).replace(eval_, `$1${label}$2`);
    return html.replace(dev, `$1${label}$2Source only — model and key are not shown$4`).replace(eval_, '$1Lab$2').replace(/(an automatic advisor answers) · [A-Za-z0-9.\-/]+/, '$1');
  }

  global.Lobby = {
    get enabled() { return state.enabled; }, get grouped() { return state.enabled && state.mode !== 'queue' && state.group !== 'task'; }, get mode() { return state.mode; }, get snap() { return state.snap; }, card, isMine, tone, statusWord, isActive, visible, groupOf, personOf, avatar, groupAvatar, groupDivider,
    newRunHtml, newRunNotice, newRunLimits, shapeNewRun, syncJoin, devSource, simAgo, open: experimentId => state.hooks.open?.(experimentId), setupForOthers, beforeRender, controlsHtml, pinnedHtml, bindSidebar, outcomeHtml, helpHtml, sharingHtml, resourcesHtml, bindHelp, markView, banner, bindBanner, send, errorText,
    fingerprintOf: experimentId => JSON.stringify(card(experimentId) ? [card(experimentId).state, card(experimentId).queue_position, card(experimentId).attempt, card(experimentId).share_replies, !!card(experimentId).waiting, card(experimentId).start_error] : null),
    start(hooks) { state.hooks = hooks || {}; if (!state.started) { state.started = true; next(); } },
    // A run made a moment ago has no card until the next poll (1.5 s, 5 s in a background tab); without it 02 greys out Stop.
    refresh,
  };
})(window);
