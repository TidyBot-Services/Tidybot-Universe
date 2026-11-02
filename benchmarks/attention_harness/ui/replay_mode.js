(function (global) {
  'use strict';
  // Replay mode of the Run page (data-replay="1"): a recorded run is opened read-only and each help
  // moment of panel 04 can be answered. An answer never changes the recorded result; it is sent to the
  // reply collector (through the fixed /api/replay/* paths) and the recorded advice is shown afterwards.
  // live.js builds the moments from the run's own data; this file renders panel 04 and records the answers.
  const doc = global.document, rootEl = doc.getElementById('attentionbench-clear') || doc.documentElement;
  const enabled = rootEl.dataset.replay === '1';
  const UI_VERSION = 'run-replay-1', CONSENT_VERSION = 'draft-1';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const state = {config: null, moments: new Map(), consent: false, code: ''};
  const read = (store, key) => { try { return global[store].getItem(key); } catch (_) { return null; } };
  const write = (store, key, value) => { try { global[store].setItem(key, value); } catch (_) { /* storage can be blocked */ } };

  // One moment's local state lives in memory so a redraw keeps the draft and the receipt.
  function moment(id) {
    if (!state.moments.has(id)) state.moments.set(id, {draft: '', category: '', receipt: '', outcome: '', busy: false, error: '', count: null, shownAt: Date.now(), firstAt: null});
    return state.moments.get(id);
  }

  const config = !enabled ? Promise.resolve(null) : fetch('/api/replay/config', {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(value => {
    state.config = value; return value;
  }).catch(() => null);

  // Experiment id -> public label (run-a, run-b ...). Unknown runs are not part of the replay.
  const label = experimentId => state.config?.runs?.[experimentId] || null;
  const allows = experimentId => !enabled || !!label(experimentId);
  const momentId = (experimentId, ordinal) => `${label(experimentId)}-m${ordinal + 1}`;

  const CONSENT_TEXT = [
    'This is a replay of a past run. Your answer does not change what the robot did.',
    'We record your answer text, the problem type you pick, how long you looked, and the time. We do not record your name, email, address or browser details.',
    'Answers are used for research on how people help robots. You receive a receipt code; with it you can delete your answer from this page at any time.',
    'Please do not write personal information in your answer.'];

  function panel(ctx) {
    const HR = global.HumanReply, run = label(ctx.experimentId);
    const items = ctx.moments.map((m, ordinal) => {
      const id = momentId(ctx.experimentId, ordinal), s = moment(id), done = !!s.receipt;
      const a = `<p>After attempt ${esc(m.k)}: ${esc(m.failure)}</p><p><button type="button" class="ab-button-quiet" data-rp-watch="${esc(m.attemptId)}">Watch attempt ${esc(m.k)} in 03</button></p>`;
      const b = `<p>${esc(m.question)}</p>`;
      const form = done ? `<p class="op-quiet">Your answer is recorded. Receipt <b data-rp-receipt>${esc(s.receipt)}</b></p>` : `${HR.categoryChips('rp-cat-' + id)}
        <label class="op-guidance-label">Your reply<textarea data-rp-text maxlength="2000" placeholder="Write a short tip in plain words, e.g. &quot;Use smaller steps when moving the arm toward the cup.&quot;">${esc(s.draft)}</textarea></label>
        ${state.config?.requires_code ? `<label class="op-guidance-label">Access code<input data-rp-code autocomplete="off" spellcheck="false" value="${esc(state.code)}" placeholder="XXXX-XXXX-XXXX-XXXX"></label>` : ''}
        <div class="op-controls"><button type="button" class="ab-button-primary" data-rp-send ${s.busy ? 'disabled' : ''}>Send reply</button>${HR.skipButton()}<small>Watch the replay in 03 before replying</small></div>
        <p role="status" data-rp-message class="${s.error ? 'hr-bad' : ''}">${esc(s.error)}</p>`;
      const next = done
        ? ''
        : '<p>This is a replay: your answer does not change the result. It is recorded anonymously for research; you get a receipt code to delete it later.</p>';
      const reveal = done ? `<div class="hr-reveal" data-rp-reveal><p><b>What happened then</b></p><span class="op-who">Advisor (simulated human)</span><div class="op-clamp" data-operator-copy>${m.adviceHtml}</div><button type="button" class="op-more" data-operator-expand="Read full reply">Read full reply</button>
        <p>${esc(m.next)}</p><p class="op-quiet">${s.count == null ? '' : `${esc(s.count)} ${s.count === 1 ? 'person has' : 'people have'} answered this moment.`} <button type="button" class="ab-button-quiet" data-rp-delete>Delete my answer</button></p></div>` : '';
      return `<div class="hr-moment" data-rp-moment="${esc(id)}"><h4>Moment ${ordinal + 1} of ${ctx.moments.length} · ${esc(run)}</h4>${HR.blocks({a, b, c: form, d: next, e: reveal}, {e: 'What the advisor said'})}</div>`;
    });
    return `<section class="panel detail ab-current-request" data-core-region="request" data-state="replay" aria-label="Help and replies">${ctx.heading}<div class="op-action none"><h4>Replay · answer as if you were the helper</h4><p>The robot asked for help at these moments. Answer each one, then see what the advisor said at the time.</p></div>${items.join('') || '<p class="op-quiet">No help was requested in this run.</p>'}</section>`;
  }

  function consentDialog() {
    return new Promise(resolve => {
      const dialog = doc.createElement('dialog');
      dialog.className = 'ab-replay-consent'; dialog.setAttribute('aria-labelledby', 'rp-consent-title');
      dialog.innerHTML = `<form method="dialog"><h3 id="rp-consent-title">Before you send</h3>${CONSENT_TEXT.map(t => `<p>${esc(t)}</p>`).join('')}<div class="op-controls"><button value="no" class="ab-button-quiet">Cancel</button><button value="yes" class="ab-button-primary" autofocus>I agree · send</button></div></form>`;
      dialog.addEventListener('close', () => { const ok = dialog.returnValue === 'yes'; dialog.remove(); resolve(ok); });
      doc.body.appendChild(dialog); dialog.showModal();
    });
  }

  async function post(path, body) {
    const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), cache: 'no-store'});
    const value = await response.json().catch(() => ({}));
    return {ok: response.ok, status: response.status, value};
  }

  const ERRORS = {invalid_code: 'That access code is not valid, or it has been used up.', rate_limited: 'Too many answers just now. Please try again later.',
    empty_content: 'Write something first, or choose “skip”.', bad_content: 'That text could not be accepted.', stale_dataset: 'This replay was updated. Reload the page.',
    unknown_moment: 'This moment is not open for answers.', collector_unavailable: 'The answer could not be recorded right now.', storage_unavailable: 'The answer could not be recorded right now.'};

  // view: the rendered Run page; ctx: the same moments passed to panel(); redraw: re-renders panel 04.
  function bind(view, ctx, redraw) {
    view.querySelectorAll('[data-rp-moment]').forEach(node => {
      const id = node.dataset.rpMoment, s = moment(id), ordinal = Number(id.split('-m').pop()) - 1, m = ctx.moments[ordinal];
      const text = node.querySelector('[data-rp-text]'), message = node.querySelector('[data-rp-message]');
      const touch = () => { if (s.firstAt == null) s.firstAt = Date.now() - s.shownAt; };
      node.querySelectorAll('[data-hr-category] input').forEach(input => { input.checked = input.value === s.category; input.addEventListener('change', () => { touch(); s.category = input.value; }); });
      text?.addEventListener('input', () => { touch(); s.draft = text.value; });
      node.querySelector('[data-rp-code]')?.addEventListener('input', event => { state.code = event.target.value.trim(); });
      node.querySelector('[data-rp-watch]')?.addEventListener('click', () => ctx.watch(m.attemptId));
      async function submit(outcome) {
        if (s.busy) return;
        s.error = '';
        if (outcome === 'answered' && !s.draft.trim()) { s.error = ERRORS.empty_content; message.textContent = s.error; message.className = 'hr-bad'; return; }
        if (!state.consent && read('sessionStorage', 'attention.replay.consent') !== CONSENT_VERSION) {
          if (!await consentDialog()) return;
        }
        state.consent = true; write('sessionStorage', 'attention.replay.consent', CONSENT_VERSION);
        s.busy = true; redraw();
        const body = {moment_id: id, dataset_version: state.config?.dataset_version || undefined, ui_version: UI_VERSION, consent_version: CONSENT_VERSION, outcome,
          content: outcome === 'answered' ? s.draft.trim() : '', first_interaction_ms: Math.min(s.firstAt ?? 0, 3600000)};
        if (s.category) body.category = s.category;
        if (state.config?.requires_code) body.code = state.code;
        const result = await post('/api/replay/reply', body).catch(() => ({ok: false, value: {error: 'collector_unavailable'}}));
        s.busy = false;
        if (result.ok) {
          s.receipt = result.value.receipt || ''; s.outcome = outcome;
          const count = await fetch(`/api/replay/count?moment=${encodeURIComponent(id)}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : null).catch(() => null);
          s.count = count?.count ?? null;
          post('/api/replay/viewed', {receipt: s.receipt}).catch(() => {});
        } else {
          const code = typeof result.value.error === 'string' ? result.value.error : result.value.error?.code;
          s.error = ERRORS[code] || 'The answer could not be recorded.';
        }
        redraw();
      }
      node.querySelector('[data-rp-send]')?.addEventListener('click', () => submit('answered'));
      node.querySelector('[data-hr-skip]')?.addEventListener('click', () => submit('skipped'));
      node.querySelector('[data-rp-delete]')?.addEventListener('click', async () => {
        const result = await post('/api/replay/delete', {receipt: s.receipt}).catch(() => ({ok: false}));
        if (result.ok) { Object.assign(s, {receipt: '', outcome: '', draft: '', category: '', count: null, error: ''}); redraw(); }
        else { s.error = 'The answer could not be deleted right now.'; redraw(); }
      });
    });
  }

  global.ReplayMode = {enabled, config, allows, label, momentId, panel, bind};
})(window);
