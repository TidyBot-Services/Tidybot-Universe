(function () {
  'use strict';
  // The Run page for a visitor without an invite code (Remote, 11-14 user: "the gallery, and a New run that goes to the
  // invite page"). Reads only what any visitor may read: /api/runs, /api/lobby, /api/home/status and the task pictures.
  // Every card opens the normal run page (?dev=…#exp=…), which shows that run's replay and result.
  const $ = id => document.getElementById(id);
  const shell = window.AttentionShell;
  const REFRESH_MS = 15000;
  const LIVE = {running: ['run', 'Live'], waiting_human: ['run', 'Waiting for help'], queued: ['run', 'In line']};
  const FILTERS = [['solved', 'Solved'], ['live', 'Live'], ['not', 'Not solved'], ['all', 'All']];
  let manifest = null, rows = [], lobby = null, filter = null, bgSet = false;
  const errors = new Map();

  // 11-18 (user): a signed-in person sees the same gallery without any guest wording; New run opens the run page's
  // New run dialog instead of the invite page.
  let signedInShown = null;
  function signedIn(yes) {
    if (yes === signedInShown) return; signedInShown = yes;
    const h1 = document.querySelector('#guest-gallery h1'), lead = document.querySelector('#guest-gallery .gg-lead'), go = document.querySelector('[data-guest-new-run]');
    if (yes) {
      if (h1) h1.innerHTML = 'Pick a run, <em>or start one</em>';
      if (lead) lead.textContent = 'Every run on this robot is open to watch. Pick one below to see its replay and result, or start your own.';
      if (go) go.setAttribute('href', '/ui/#new-run');
    }
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function showErrors() {
    const box = $('gg-error'), text = [...errors.values()].join(' ');
    box.textContent = text; box.hidden = !text;
  }

  async function getJson(name, url) {
    try {
      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin'});
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error((body && (body.message || body.error)) || `HTTP ${response.status}`);
      errors.delete(name); return body;
    } catch (error) {
      errors.set(name, `Could not read ${name}: ${error.message}. Retrying every ${REFRESH_MS / 1000} seconds.`);
      return null;
    } finally { showErrors(); }
  }

  // What a run is now, from its lobby card when it has one, else from its own row.
  function kindOf(row) {
    const card = row.lobby || {};
    if (LIVE[card.state]) return 'live';
    if (card.result === 'solved' || row.reason === 'native_success') return 'solved';
    if (card.state === 'created') return 'idle';
    if (card.state === 'cancelled' || ['cancelled', 'stopped'].includes(row.experiment_state)) return 'stopped';
    return 'failed';
  }

  function tagOf(row, kind) {
    if (kind === 'live') return LIVE[row.lobby.state];
    return {solved: ['ok', 'Solved'], failed: ['bad', 'Not solved'], stopped: ['', 'Stopped'], idle: ['', 'Not started']}[kind];
  }

  function attemptsOf(row, kind) {
    const attempt = row.lobby?.attempt, used = attempt?.current, limit = attempt?.limit;
    if (!(Number(used) >= 0) || !(limit > 0)) return '';
    if (kind === 'solved') return `on attempt ${used} of ${limit}`;
    if (kind === 'live') return `attempt ${Math.min(used + 1, limit)} of ${limit}`;
    return `${used} of ${limit} attempts`;
  }

  function taskOf(row) {
    const task = manifest?.tasks?.find(t => t.task_id === row.task_id);
    const seed = task?.seeds?.find(s => s.seed === row.seed && s.pictures !== false);
    const sentence = seed?.sentence || task?.sentence;
    return {title: task?.title || String(row.task_id || 'Unknown task').replace(/_/g, ' '),
      goal: Array.isArray(sentence) ? sentence.map(part => part.text).join('') : '', seed};
  }

  function card(row) {
    const kind = kindOf(row), [tone, word] = tagOf(row, kind), task = taskOf(row);
    const a = el('a', 'gg-card'); a.href = shell.runUrl(row);
    if (task.seed) {
      const pics = el('div', 'gg-pics');
      for (const [file, label] of [[task.seed.start, 'START'], [task.seed.done, 'GOAL']]) {
        const figure = el('figure'), img = el('img'); img.src = `/ui/tasks/${encodeURIComponent(file)}`; img.alt = `${label === 'START' ? 'Start' : 'Goal'} of ${task.title}, seed ${row.seed}`; img.loading = 'lazy';
        figure.append(img, el('figcaption', null, label)); pics.append(figure);
      }
      a.append(pics);
    } else a.append(el('div', 'gg-nopic', `NO PICTURE FOR SEED ${row.seed ?? '?'}`));
    const body = el('div', 'gg-body');
    body.append(el('b', null, task.title));
    if (task.goal) body.append(el('p', 'gg-goal', task.goal));
    const meta = el('div', 'gg-meta');
    meta.append(el('span', null, row.lobby?.nickname || 'Someone'), el('span', `gg-tag ${tone}`, word));
    const attempts = attemptsOf(row, kind); if (attempts) meta.append(el('span', null, attempts));
    body.append(meta); a.append(body);
    return a;
  }

  function counts() {
    const c = {solved: 0, live: 0, not: 0, all: rows.length};
    for (const row of rows) { const k = kindOf(row); if (k === 'solved') c.solved++; else if (k === 'live') c.live++; else if (k === 'failed' || k === 'stopped') c.not++; }
    return c;
  }

  function renderFilters(c) {
    if (!filter || (filter !== 'all' && !c[filter])) filter = c.live ? 'live' : c.solved ? 'solved' : 'all';
    const box = $('gg-filters'); box.replaceChildren();
    for (const [id, label] of FILTERS) {
      if (id !== 'all' && !c[id]) continue;
      const b = el('button', null, `${label} · ${c[id]}`); b.type = 'button'; b.setAttribute('aria-pressed', String(filter === id));
      b.addEventListener('click', () => { filter = id; render(); }); box.append(b);
    }
  }

  function renderGrid() {
    const pick = {solved: k => k === 'solved', live: k => k === 'live', not: k => k === 'failed' || k === 'stopped', all: () => true}[filter];
    const shown = rows.filter(row => pick(kindOf(row)));
    const grid = $('gg-grid');
    grid.replaceChildren(...(shown.length ? shown.map(card) : [el('p', 'gg-empty', manifest && lobby ? 'No runs yet. Start the first one with New run.' : 'Loading runs…')]));
  }

  function renderHero(c) {
    const robot = lobby?.robot, eyebrow = $('gg-robot');
    if (robot) {
      const open = (robot.open || []).length, onRobot = robot.running;
      const [dot, text] = onRobot ? ['ok', `LIVE · ${open} OF ${robot.slots} RUN SLOTS IN USE`] : open ? ['warn', `${open} OF ${robot.slots} RUN SLOTS IN USE · ROBOT BETWEEN TURNS`] : ['dim', `ROBOT IDLE · 0 OF ${robot.slots} RUN SLOTS IN USE`];
      eyebrow.replaceChildren(el('span', `gg-dot ${dot}`), document.createTextNode(text));
      eyebrow.firstChild.setAttribute('aria-hidden', 'true');
    }
    const people = new Set(rows.map(row => row.lobby?.person).filter(Boolean)).size;
    const tasks = (manifest?.tasks || []).filter(t => !t.defect).length;
    const stats = $('gg-stats'); stats.replaceChildren();
    for (const [n, label, ok] of [[c.all, 'PUBLIC RUNS'], [c.solved, 'SOLVED', true], [people, 'PEOPLE'], [tasks, 'TASKS']]) {
      const d = el('div'); d.append(el('div', ok ? 'n ok' : 'n', String(n)), el('div', 'l', label)); stats.append(d);
    }
    const solved = rows.find(row => kindOf(row) === 'solved'), watch = $('gg-watch');
    watch.hidden = !solved; if (solved) watch.href = shell.runUrl(solved);
    const wait = lobby?.limits?.help_wait_seconds, tries = rows.find(row => row.lobby?.attempt?.limit)?.lobby.attempt.limit;
    if (wait > 0) document.querySelector('[data-gg-wait]').textContent = wait % 60 === 0 ? `${wait / 60} minute${wait === 60 ? '' : 's'}` : `${wait} seconds`;
    if (tries > 0) document.querySelector('[data-gg-tries]').textContent = String(tries);
  }

  function render() { const c = counts(); renderHero(c); renderFilters(c); renderGrid(); }

  async function background() {
    // A recorded frame from a real run, as on the cover; decoration only.
    const status = await getJson('the cover scenes', '/api/home/status');
    const scene = status?.scenes?.[0]; if (!scene) return;
    $('gg-bg').style.backgroundImage = `url("/api/home/scene?run=${encodeURIComponent(scene.experiment_id)}&view=${encodeURIComponent(scene.view)}")`;
    bgSet = true;
  }

  async function refresh() {
    const [runs, board] = await Promise.all([getJson('the runs', '/api/runs'), getJson('the robot', '/api/lobby')]);
    if (runs) rows = (runs.dev_sessions || []).filter(row => row.key && row.experiment_id).sort((a, b) =>
      (kindOf(b) === 'live') - (kindOf(a) === 'live') || (b.created_at || 0) - (a.created_at || 0));
    if (board) lobby = board;
    signedIn(Boolean(lobby && lobby.me));
    render();
  }

  (async () => {
    manifest = await getJson('the task pictures', '/ui/tasks/manifest.json');
    await refresh();
    if (!bgSet) background();
    setInterval(refresh, REFRESH_MS);
  })();
})();
