/* 02 HUMAN ATTENTION · conversation (experiment, 11-18 "ui改动").
 * The chart above is the map; this is the story. Built from LiteModel.build (the trial Run page's model, same API data).
 * Pure HTML strings, no network. live.js places the result inside 02 and wires the clicks.
 */
(function (global) {
  'use strict';
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  // Technical answers carry formula marks the page cannot draw.
  const tidy = s => String(s || '').replace(/\\\(|\\\)/g, '').replace(/\\text\{([^}]*)\}/g, '$1').replace(/\\_/g, '_').replace(/\s+/g, ' ').trim();
  const tryId = n => 'e' + String(n).padStart(6, '0');
  const clock = sec => global.LiteModel ? global.LiteModel.clock(sec) : '';
  const span = sec => global.LiteModel ? global.LiteModel.span(sec) : '';

  function long(text, limit = 260) {
    const t = tidy(text);
    if (t.length <= limit) return `<p>${esc(t)}</p>`;
    const cut = t.slice(0, limit).replace(/\s+\S*$/, '') + '…';
    return `<details class="c2-more"><summary><span class="c2-short">${esc(cut)}</span><span class="c2-show">Show more</span></summary><p>${esc(t)}</p></details>`;
  }
  const watch = (attr, id) => id ? `<button type="button" class="c2-watch" ${attr}="${esc(id)}">Watch →</button>` : '';

  // Tries before an attempt: one message, one row per reason (most frequent first, "ran through" last),
  // each row with the same mini marks as the chart and the try numbers to open.
  const MARK={no:'<svg class="c2-m no" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"/></svg>',
    ok:'<svg class="c2-m ok" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.2"/><circle class="d" cx="8" cy="8" r="2"/></svg>'};
  function practice(m, tries, label, recordings) {
    if (!tries.length) return '';
    const groups = new Map();
    tries.forEach(t => { const key = (t.ok ? 'ok|' : 'no|') + (t.ok ? '' : t.why); if (!groups.has(key)) groups.set(key, {ok: t.ok, why: t.why, items: []}); groups.get(key).items.push(t); });
    const rows = [...groups.values()].sort((a, b) => (a.ok - b.ok) || (b.items.length - a.items.length));
    const stopped = tries.filter(t => !t.ok).length;
    const body = rows.map(r => {
      const nums = r.items.map(t => `<button type="button" class="c2-try ${t.ok ? 'ok' : 'no'}" data-c2-try="${t.number}" title="Try ${t.number}${recordings.has(t.number) ? ' · click to watch' : ''}">${t.number}</button>`).join('');
      return `<li class="c2-row ${r.ok ? 'ok' : 'no'}" ${r.items.map(t => `data-feed-try-${t.number}`).join(' ')}>${MARK[r.ok ? 'ok' : 'no']}<span class="c2-why">${esc(r.ok ? 'Ran through (not a task check)' : r.why)}${r.items.length > 1 ? ` <small>×${r.items.length}</small>` : ''}</span><span class="c2-nums">${nums}</span></li>`;
    }).join('');
    return `<div class="c2-msg"><div class="c2-av">R</div><div class="c2-card slim"><div class="c2-meta">${esc(label)} · <b>${tries.length} ${tries.length === 1 ? 'try' : 'tries'}</b>${stopped ? ` · ${stopped} stopped` : ''}</div><ul class="c2-rows">${body}</ul></div></div>`;
  }

  function exchange(m, h, n) {
    const anchor = `data-feed-help="${n}"`;
    // 11-18: "nobody answered" was wrong for these. Before 11-14 (7f98b05) the whole help call was capped at 120 s, the same
    // as the wait for a person, so the AI advisor fallback was cut off before it could answer.
    if (h.failed) return `<div class="c2-sys bad" ${anchor} title="${esc(h.failText)}">${clock(h.at)} · Help #${n}: no answer arrived. ${/timed out/.test(h.failText) ? 'Nobody replied within the wait, and the request hit its time limit before the AI advisor could answer.' : 'The request could not be completed.'} The robot carried on without an answer.</div>`;
    const ask = `<div class="c2-msg" ${anchor}><div class="c2-av">R</div><div class="c2-card slim ask"><div class="c2-meta"><b>Help #${n} · the robot asks</b> · ${clock(h.at)}</div>${h.question ? long(h.question) : '<p class="c2-dim">The question text was not recorded.</p>'}</div></div>`;
    if (h.human) {
      return ask + `<div class="c2-msg you"><div class="c2-bubble"><div class="c2-meta">You · hint ${h.hintNo} · ${clock(h.at)}</div>${long(h.content)}</div><div class="c2-av you">Y</div></div>`;
    }
    const why = m.waitSeconds ? `you did not reply within ${m.waitSeconds} s` : 'answered for you';
    return ask + `<div class="c2-msg you"><div class="c2-bubble adv"><div class="c2-meta">AI advisor · ${clock(h.at)} · ${esc(why)} · <b>0 s of your attention</b></div>${h.content ? long(h.content) : '<p class="c2-dim">Answered, but no text was recorded.</p>'}</div><div class="c2-av adv">AI</div></div>`;
  }

  function attempt(m, a, ids) {
    const dur = a.end && a.start ? span(a.end - a.start) : '';
    const tag = a.solved ? '<span class="c2-tag good">solved</span>' : `<span class="c2-tag bad">${esc(a.headline || 'not solved')}</span>`;
    return `<div class="c2-msg" data-feed-attempt="${a.n}"><div class="c2-av">R</div><div class="c2-card"><div class="c2-head"><div class="c2-meta"><b>Attempt ${a.n}</b> · ${clock(a.start)}–${clock(a.end)}${dur ? ' · ' + dur : ''}</div>${tag}${watch('data-c2-attempt', ids.get(a.n))}</div>
      ${a.sentence ? `<p>${esc(a.sentence)}</p>` : ''}${a.next && !a.solved ? `<p class="c2-next"><b>Checker suggests</b> ${esc(a.next)}</p>` : ''}</div></div>`;
  }

  // Memory: what the system handed the robot and what the robot itself said about it.
  // The robot's own words decide "used / not used" ("Not using note …" was being counted as used, 11-18).
  const NOT_USING = /\b(not using|won'?t use|do not use|don'?t use|declin\w*|ignor\w*)\b[^.]*\bnote\b|\bnote\b[^.]*\b(not used|ignored|declined)\b/i;
  const cleanWords = t => tidy(String(t || '').replace(/`/g, '').replace(/\b[rp]-[0-9a-f]{12,}\b/g, '').replace(/^#\s*/, ''));
  function memoryVerdict(note) {
    const quotes = [...(note.considered_not_used_evidence || []), ...(note.evidence || [])].map(e => String(e.excerpt || ''));  // code comments are whole sentences; transcript excerpts are cut
    const refusal = quotes.find(q => NOT_USING.test(q));
    if (refusal) {
      const why = cleanWords(refusal.split(/not using note[^:]*:/i)[1] || '').replace(/\s+\S*$/, m => /[.;]$/.test(m) ? m : m);
      return {used: false, why};
    }
    if (note.referenced === 'explicit') return {used: true, why: ''};
    if (note.considered_not_used) return {used: false, why: ''};
    return {used: null, why: ''};
  }
  function memoryMsg(entry, n) {
    const notes = (entry.notes || []).filter(x => x.delivered !== false).map(note => {
      const v = memoryVerdict(note), title = cleanWords(String(note.title || '').replace(/^[^:]{0,80}:\s*/, ''));
      const verdict = v.used === true ? `<b>used it</b>` : v.used === false ? `<b>did not use it</b>${v.why ? ': ' + esc(v.why.replace(/[.;,]?\s*$/, '')) : ''}` : 'did not mention it';
      return `<li><span class="c2-note-q">“${esc(title)}”</span> → Attempt ${n} ${verdict}.</li>`;
    }).join('');
    const errors = (entry.errors || []).map(e => `<div class="c2-sys bad" role="alert">Memory analysis error before Attempt ${n}: ${esc(typeof e.error === 'object' ? JSON.stringify(e.error) : e.error)}</div>`).join('');
    return (notes ? `<div class="c2-msg c2-memory"><div class="c2-av mem" aria-hidden="true">M</div><div class="c2-card slim"><div class="c2-meta"><b>Memory gave ${entry.notes.length === 1 ? 'a note' : entry.notes.length + ' notes'}</b> · before Attempt ${n}</div><ul class="c2-notes">${notes}</ul></div></div>` : '') + errors;
  }

  /* opts: {attemptIds: Map(n -> attempt_id), tryRecordings: Map(tryNumber -> attempt_id), memory: [{version, notes, errors}], memoryFailures: [text]} */
  function feed(m, opts) {
    const ids = opts.attemptIds || new Map(), recs = opts.tryRecordings || new Map();
    const claimed = new Set(), helpNo = new Map(m.helps.map((h, i) => [h, i + 1]));
    let html = '';
    const helpsAfterTry = (tries) => {
      // A help asked right after one of these tries sits after that stretch.
      return m.helps.filter(h => !claimed.has(h) && tries.some(t => tryId(t.number) === h.executionId));
    };
    const stretch = (tries, label) => {
      let out = '', group = [];
      const flush = () => { if (group.length) { out += practice(m, group, label, recs); group = []; } };
      tries.forEach(t => {
        group.push(t);
        const hs = m.helps.filter(h => h.executionId === tryId(t.number) && !claimed.has(h));
        if (hs.length) { flush(); hs.forEach(h => { claimed.add(h); out += exchange(m, h, helpNo.get(h)); }); }
      });
      flush();
      return out;
    };
    const memory = new Map((opts.memory || []).map(e => [e.version, e]));
    m.attempts.forEach(a => {
      html += stretch(a.practice, `Before Attempt ${a.n}`);
      if (memory.has(a.version)) html += memoryMsg(memory.get(a.version), a.n);
      html += attempt(m, a, ids);
      m.helps.filter(h => h.executionId === a.executionId && !claimed.has(h)).forEach(h => { claimed.add(h); html += exchange(m, h, helpNo.get(h)); });
    });
    const st = m.status;
    if (!(st.kind === 'solved' && m.solvedAttempt) && (m.current.tries.length || !st.ended)) {
      const n = m.attempts.length + 1;
      html += stretch(m.current.tries, `Before Attempt ${n}`);
      if (!st.ended) html += `<div class="c2-sys" data-feed-attempt="${n}">Attempt ${n} · ${m.executing ? 'running try ' + Number(String(m.executing).slice(1)) + ' now' : m.current.tries.length ? m.current.tries.length + (m.current.tries.length === 1 ? ' try' : ' tries') + ' so far' : 'nothing has run yet'}</div>`;
    }
    m.helps.filter(h => !claimed.has(h)).forEach(h => { claimed.add(h); html += exchange(m, h, helpNo.get(h)); });
    // 11-18 ui改动 (as the trial page does): while the robot is running and not waiting for you, a spinner row says what it is doing.
    if (st.kind === 'running' && !m.wait) {
      const doing = m.executing ? 'Running try ' + Number(String(m.executing).slice(1)) : 'Thinking and writing its next code';
      html += `<div class="c2-working" role="status"><span class="c2-spin" aria-hidden="true"></span><b>Working</b><i class="c2-ell" aria-hidden="true"></i><span>${esc(doing)}</span></div>`;
    }
    (opts.memoryFailures || []).forEach(text => { html += `<div class="c2-sys bad" role="alert">Memory analysis error: ${esc(text)}</div>`; });
    if (m.wait) {
      html += `<div class="c2-msg" data-feed-wait><div class="c2-av">R</div><div class="c2-card ask live"><div class="c2-meta"><b>The robot asks you now</b>${m.wait.demo ? ' · <b class="c2-sample">SAMPLE, not a real request</b>' : ''}</div>${long(m.wait.question || '')}<div class="c2-count" data-c2-deadline="${esc(m.wait.deadline_at)}"></div></div></div>`;
    }
    if (st.ended) html += `<div class="c2-sys">${st.kind === 'solved' ? `Solved on Attempt ${m.solvedAttempt ? m.solvedAttempt.n : ''}.` : st.kind === 'stopped' ? 'You stopped this run.' : `The run ended without solving the task (${m.used} of ${m.limit} attempts used).`} The conversation stays here.</div>`;
    return html || '<div class="c2-sys">Nothing has happened yet.</div>';
  }

  // Composer shown when the robot is not asking: says why it is off.
  // 11-18 (user: the greyed-out input + Send looked broken): a hint can only answer the robot's question, so while
  // nothing is asked the box is one quiet status line, the same as after the run ends.
  function idleComposer(m) {
    if (m.status.ended) return '<div class="c2-composer off"><p class="c2-idle">The run has ended · hints are closed.</p></div>';
    return '<div class="c2-composer off"><p class="c2-idle"><i aria-hidden="true"></i>No question right now. When the robot needs a hint, it asks here.</p></div>';
  }
  function demoComposer(m) {
    return `<div class="c2-composer live"><label>Hint for Attempt ${m.attempts.length + 1} · <b class="c2-sample">SAMPLE</b> · the robot keeps running while you decide</label><div class="c2-row-in"><input type="text" placeholder="Tell it what to do differently" data-c2-demo-input><button type="button" class="c2-send" data-c2-demo-send>Send</button><button type="button" class="c2-alt" data-c2-demo-send>Let the AI advisor decide</button></div><small class="c2-err" data-c2-demo-err></small></div>`;
  }

  global.Chat02 = {feed, idleComposer, demoComposer, tryId};
})(typeof window !== 'undefined' ? window : globalThis);
