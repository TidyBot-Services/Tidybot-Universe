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
    group: ['task', 'person', 'status'].includes(read('attention.lobby.group')) ? read('attention.lobby.group') : 'task'};

  const minutes = seconds => seconds == null ? '' : seconds < 90 ? 'under 2 min' : `~${Math.round(seconds / 60)} min`;
  const clock = seconds => { const s = Math.max(0, Math.round(seconds || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const RESULT = {solved: 'Solved', failed: 'Failed', limit: 'Limit reached'};
  const SOURCE = {lab_codex: 'Lab Codex', lab_glm: 'Lab GLM', own_key: 'My own key'};

  function ingest(snap) {
    state.snap = snap;
    state.cards = new Map();
    const all = [snap.robot?.running, ...(snap.queue || []), ...(snap.recent || [])].filter(Boolean);
    for (const card of all) state.cards.set(card.experiment_id, card);
  }
  // Clocks that tick every second (waiting time, estimates) must not force a redraw of a form someone is typing in.
  const stable = c => c && {...c, help_waiting_seconds: null, eta_start_seconds: c.eta_start_seconds == null ? null : Math.round(c.eta_start_seconds / 60)};
  const fingerprint = () => JSON.stringify([(state.snap?.queue || []).map(stable), (state.snap?.recent || []).map(stable), stable(state.snap?.robot?.running), state.snap?.counts,
    state.snap?.me, state.snap?.clock?.speed, state.snap?.events?.[0]?.id]);

  async function poll() {
    // The lobby lives on the Remote page only; the Run and Replay pages keep their own runs.
    if (rootEl.dataset.remote !== '1') return stop();
    try {
      const response = await fetch('/api/lobby', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) { if (!state.enabled) return stop(); throw new Error(`lobby ${response.status}`); }
      const before = state.cards;
      ingest(await response.json());
      const first = !state.enabled;
      state.enabled = true;
      rootEl.dataset.lobby = '1'; doc.documentElement.dataset.lobby = '1';
      if (snapshot_demo() && !doc.querySelector('[data-lobby-demo]')) rootEl.insertAdjacentHTML('afterbegin', `<div class="lb-demo" data-lobby-demo><i></i>DEMO ENGINE · simulated time ×<span data-lb-speed-now>${esc(state.snap.clock?.speed)}</span> · no GPU, no real provider</div>`);
      doc.querySelectorAll('[data-lb-speed-now]').forEach(n => { n.textContent = String(state.snap.clock?.speed); });
      const next = fingerprint();
      for (const [id, card] of state.cards) notify(before.get(id), card);
      doc.querySelectorAll('[data-lb-wait]').forEach(node => { const c = state.cards.get(node.dataset.lbExp); if (c) node.textContent = `Waiting ${clock(c.help_waiting_seconds)} · auto-skip at ${clock(state.snap.limits.help_wait_seconds)}`; });
      // The waiting clock ticks every second without changing the data, so it does not force a redraw.
      if (first || next !== state.fp) { state.fp = next; state.hooks.changed?.(first); }
    } catch (_) { /* keep the last picture; the next poll tries again */ }
    state.timer = global.setTimeout(poll, doc.hidden ? 5000 : 1500);
  }
  const snapshot_demo = () => !!state.snap?.demo;
  function stop() { state.enabled = false; delete rootEl.dataset.lobby; delete doc.documentElement.dataset.lobby; }

  function notify(before, card) {
    if (!card.mine || !before || before.state === card.state) return;
    if (card.state === 'waiting_human') state.hooks.toast?.('The robot is asking you for help — answer in 04.', 'info');
    else if (card.state === 'running' && before.state === 'queued') state.hooks.toast?.('Your run is starting now.', 'info');
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
    return RESULT[c.result] || 'Finished';
  }
  const isActive = record => ['running', 'waiting_human'].includes(record.lobby?.state);
  const visible = record => !state.enabled || state.mode !== 'mine' || isMine(record);

  // ---- sidebar block: whose runs, grouping, and the one robot with its queue ----
  const seg = (attr, items, current) => `<div class="lb-seg" role="group">${items.map(([key, name]) => `<button type="button" ${attr}="${key}" aria-pressed="${current === key}">${name}</button>`).join('')}</div>`;
  // Rows are plain elements (not buttons): the page's own button styling would box and shade every line.
  const open = c => `role="button" tabindex="0" data-lb-open="${esc(c.experiment_id)}"`;
  const row = (c, extra = '') => `<div class="lb-line${c.mine ? ' me' : ''}" ${open(c)}><span class="lb-n">${c.state === 'queued' ? c.queue_position : c.state === 'finished' ? (c.result === 'solved' ? '✓' : '✕') : '●'}</span><b>${c.mine ? 'You' : esc(c.nickname)}</b><span class="lb-sub">${extra}</span></div>`;

  function pinned() {
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
    const snap = state.snap, running = snap.robot?.running, queue = snap.queue || [], recent = snap.recent || [];
    return `<section class="lb-queue" data-lobby-queue aria-label="Queue"><h3>Running</h3>${running ? row(running, `${running.attempt.current} / ${running.attempt.limit}`) : '<p class="lb-idle">Nobody. The robot is free.</p>'}
      <h3>Waiting · first come, first served</h3>${queue.map(c => row(c, minutes(c.eta_start_seconds))).join('') || '<p class="lb-idle">Nobody is waiting.</p>'}
      <h3>Just finished</h3>${recent.slice(0, 8).map(c => row(c, `${RESULT[c.result] || 'Finished'} · ${c.attempt.current} / ${c.attempt.limit}`)).join('')}${clockHtml()}</section>`;
  }
  function controlsHtml() {
    if (!state.enabled) return '';
    return `<div class="lb-controls" data-lobby-controls>${seg('data-lb-mode', [['mine', 'Mine'], ['all', 'All'], ['queue', 'Queue']], state.mode)}${state.mode === 'queue' ? '' : `<span class="lb-label">Group by</span>${seg('data-lb-group', [['task', 'Task'], ['person', 'Person'], ['status', 'Status']], state.group)}`}</div>`;
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
    if (state.group === 'person') return c ? [c.nickname + (c.mine ? ' (you)' : ''), c.nickname] : ['You · earlier runs', 'zz-earlier'];
    const word = c ? ({queued: 'Waiting in line', running: 'Running now', waiting_human: 'Running now', finished: 'Finished', cancelled: 'Finished'}[c.state]) : 'Finished';
    return [word, ({'Running now': '1', 'Waiting in line': '2', 'Finished': '3'})[word] + word];
  }

  // ---- 02, 04 and 05 for the states a shared run can be in ----
  const tiles = items => `<div class="lb-tiles">${items.map(([big, name]) => `<div><b>${esc(big)}</b>${esc(name)}</div>`).join('')}</div>`;
  function outcomeHtml(snapshot) {
    const c = snapshot.lobby;
    if (!c || !['queued', 'running', 'waiting_human'].includes(c.state)) return '';
    const k = c.attempt.current, n = c.attempt.limit, owner = c.mine ? 'You are' : `${esc(c.nickname)} is`;
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
  const privateBlock = (title, text) => `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" data-lobby-private aria-label="Help and replies"><div class="op-action none"><h4>🔒 ${esc(title)}</h4><p>${esc(text)}</p></div></section>`;

  // Returns the whole 04 section for the lobby states, or '' to let the page draw it from the run's own data.
  function helpHtml(snapshot, heading, HumanReply, replayMoments) {
    const c = snapshot.lobby;
    if (!c) return '';
    const head = heading || '';
    if (!c.mine) {
      if (c.state === 'finished' && c.share_replies) return '';
      return `<section class="panel detail ab-current-request" data-core-region="request" data-state="idle" data-lobby-private aria-label="Help and replies">${head}<div class="op-action none"><h4>🔒 Private</h4><p>${c.state === 'waiting_human' ? `The robot is asking ${esc(c.nickname)} for help right now.` : `${esc(c.nickname)}'s questions and replies are not shown.`}</p><p>${c.state === 'finished' ? 'Only the owner can share them.' : 'If the owner turns on sharing after the run ends, they appear here.'}</p></div></section>`;
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
  // The sharing switch of a finished run of mine, added under 04.
  function sharingHtml(snapshot) {
    const c = snapshot.lobby;
    if (!c?.mine || c.state !== 'finished') return '';
    return `<div class="lb-share" data-lb-share><label><input type="checkbox" data-lb-share-box ${c.share_replies ? 'checked' : ''}> Let others see this run's help requests and replies in its replay</label><small>${c.share_replies ? 'Shared · you can turn it off any time.' : 'Off · others see only status and result.'}</small></div>`;
  }
  const resourcesHtml = snapshot => snapshot.lobby && !snapshot.lobby.mine ? `<section class="panel ab-resource-details" data-resource-details data-lobby-private aria-label="Resources"><div class="op-action none"><h4>🔒 Only the owner sees resources</h4><p>Attempts: ${esc(snapshot.lobby.attempt.current)} of ${esc(snapshot.lobby.attempt.limit)}.</p></div></section>` : '';

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
    if (!c) return;
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
    view.querySelector('[data-lb-share-box]')?.addEventListener('change', async e => {
      await send(`/api/lobby/runs/${encodeURIComponent(c.run_id)}/share`, {share: e.target.checked});
      state.hooks.changed?.(false, true);
    });
    // Keep the waiting clock moving without redrawing the form the person is typing in.
    const wait = view.querySelector('[data-lb-wait]');
    if (wait) wait.textContent = `Waiting ${clock(c.help_waiting_seconds)} · auto-skip at ${clock(state.snap.limits.help_wait_seconds)}`;
  }

  // The marker for CSS: which kind of run is on screen decides which write controls the page shows.
  function markView(snapshot) {
    const c = snapshot?.lobby;
    if (c) { rootEl.dataset.lobbyView = c.mine ? 'mine' : 'other'; rootEl.dataset.lobbyState = c.state; } else { delete rootEl.dataset.lobbyView; delete rootEl.dataset.lobbyState; }
  }
  function banner(snapshot) {
    const c = snapshot?.lobby;
    if (!c || c.mine) return '';
    return `<div class="lb-readonly" data-lobby-readonly role="note"><b>Viewing ${esc(c.nickname)}'s run · read-only.</b><span>You can watch everything, but not answer, pause or stop it.</span><button type="button" class="ab-button-quiet" data-lb-back>Back to my run</button></div>`;
  }
  function bindBanner(view) {
    view.querySelector('[data-lb-back]')?.addEventListener('click', () => {
      const mine = [state.snap?.robot?.running, ...(state.snap?.queue || []), ...(state.snap?.recent || [])].find(c => c?.mine);
      if (mine) state.hooks.open?.(mine.experiment_id);
    });
  }

  // The extra field of the New run dialog: sharing, and where the new run would stand in the queue.
  function newRunHtml() {
    if (!state.enabled) return '';
    const snap = state.snap, waiting = (snap.queue || []).length, running = snap.robot?.running ? 1 : 0;
    const place = waiting + running + 1;
    const mineBusy = [snap.robot?.running, ...(snap.queue || [])].some(c => c?.mine);
    return `<section class="ab-launch-section ab-lobby-newrun" data-lobby-newrun><div class="ab-run-section-head"><h4>Sharing &amp; queue</h4><small>Everyone on this machine shares one robot, so runs wait in line.</small></div>
      <label class="ab-launch-field ab-check"><span><input type="checkbox" name="lb_share"> Let others see my help requests and replies in the replay</span><small>Off by default. Others always see status, progress and result, never your key.</small></label>
      <p class="ab-run-hint" data-lobby-queue-note>${running ? '1 running' : 'The robot is free'} · ${waiting} waiting. ${mineBusy ? 'You already have a run in line; finish or cancel it first.' : `Your run would be #${place}; the estimate appears after you join.`} One running and one waiting run per person. ${snap.demo ? 'Demo engine: simulated run, no GPU, no real provider.' : ''}</p></section>`;
  }
  const devSource = (provider, modelSource) => provider ? 'own_key' : modelSource === 'codex' ? 'lab_codex' : 'lab_glm';

  // The 01 cards of someone else's run name the source only, never the model or a key.
  function setupForOthers(snapshot, html) {
    const c = snapshot.lobby;
    if (!c) return html;
    const label = esc(SOURCE[c.dev_source] || 'Lab');
    const dev = /(<span>Dev agent<\/span><b>)[^<]*(<\/b><small>)([^<]*)(<\/small>)/;
    const eval_ = /(<span>Eval agent<\/span><b>)[^<]*(<\/b>)/;
    if (c.mine) return html.replace(dev, `$1${label}$2$3$4`).replace(eval_, `$1${label}$2`);
    return html.replace(dev, `$1${label}$2Source only — model and key are not shown$4`).replace(eval_, '$1Lab$2').replace(/(an automatic advisor answers) · [A-Za-z0-9.\-/]+/, '$1');
  }

  global.Lobby = {
    get enabled() { return state.enabled; }, get grouped() { return state.enabled && state.mode !== 'queue' && state.group !== 'task'; }, get mode() { return state.mode; }, get snap() { return state.snap; }, card, isMine, tone, statusWord, isActive, visible, groupOf,
    newRunHtml, devSource, open: experimentId => state.hooks.open?.(experimentId), setupForOthers, beforeRender, controlsHtml, pinnedHtml, bindSidebar, outcomeHtml, helpHtml, sharingHtml, resourcesHtml, bindHelp, markView, banner, bindBanner, send, errorText,
    fingerprintOf: experimentId => JSON.stringify(card(experimentId) ? [card(experimentId).state, card(experimentId).queue_position, card(experimentId).attempt, card(experimentId).share_replies, !!card(experimentId).waiting] : null),
    start(hooks) { state.hooks = hooks || {}; if (!state.started) { state.started = true; poll(); } },
  };
})(window);
