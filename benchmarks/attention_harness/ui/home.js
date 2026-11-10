(function () {
  'use strict';
  // The general cover. Reads /api/home/status only; New run opens the main UI's run dialog,
  // so nothing on this page is tied to one task or starts anything by itself.
  const $ = id => document.getElementById(id);
  const WORDS = {ready: ['ok', 'Ready'], unstable: ['warn', 'Unstable'], blocked: ['bad', 'Blocked'], down: ['bad', 'Down'],
    low: ['warn', 'Low'], checking: ['dim', 'Checking'], unknown: ['dim', 'Unknown']};
  const shell = window.AttentionShell;
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const SCENE_SECONDS = 14;
  let status = null, firstPaint = true, sceneKeys = '', sceneTimer = null;

  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function dot(kind, pulse) { return el('span', `dot ${kind}${pulse ? ' pulse' : ''}`); }

  const notices = window.AttentionNotices?.createNotices({mount: $('home-notices'), experiment: () => status?.running?.[0]?.experiment_id || null});
  if (notices) window.AttentionNotices.installFetchCapture(notices);

  // Title entrance: letters rise one by one, then the second line gets a slow light sweep.
  (function title() {
    let i = 0;
    const lines = [...document.querySelectorAll('#headline [data-split]')];
    for (const line of lines) {
      const text = line.textContent; line.textContent = '';
      for (const ch of text) { const s = el('span', 'ch', ch); s.style.setProperty('--i', i++); line.append(s); }
    }
    setTimeout(() => { for (const line of lines) line.textContent = line.textContent; lines[1].classList.add('glint'); }, calm ? 0 : i * 42 + 1100);
  })();

  // Background: recorded first frames (each task's overview, then other views), crossfading slowly.
  function playScenes(list) {
    const keys = list.map(s => `${s.experiment_id}:${s.view}`).join('|');
    if (keys === sceneKeys) return;
    sceneKeys = keys; clearInterval(sceneTimer);
    if (!list.length) return;
    const urls = list.map(s => `/api/home/scene?run=${encodeURIComponent(s.experiment_id)}&view=${encodeURIComponent(s.view)}`);
    let index = 0, front = 0;
    const show = url => { const img = new Image(); img.onload = () => {
      const next = $(`bg${1 - front}`), prev = $(`bg${front}`); next.style.backgroundImage = `url("${url}")`; next.style.opacity = '1'; prev.style.opacity = '0'; front = 1 - front;
    }; img.src = url; };
    show(urls[0]);
    if (urls.length > 1 && !calm) sceneTimer = setInterval(() => { index = (index + 1) % urls.length; show(urls[index]); }, SCENE_SECONDS * 1000);
  }

  function instrument(label, state, value, detail) {
    const [kind, word] = WORDS[state] || WORDS.unknown;
    const box = el('div'); box.append(el('div', 'k', label));
    const s = el('div', 's'); s.append(dot(kind), document.createTextNode(value || word)); box.append(s, el('div', 'd', detail || ''));
    return box;
  }

  function renderInstruments(s) {
    const r = s.readiness, inst = $('inst');
    const planner = s.planner_seconds == null ? instrument('PLANNER', 'unknown', 'No data', 'No planner timing in recent runs.')
      : instrument('PLANNER', 'ready', `~${s.planner_seconds} s`, 'Per plan in recent runs · limit 180 s');
    // The public gateway leaves out this machine's Codex account and disk.
    const rows = [r.codex && instrument('CODEX', r.codex.state, null, r.codex.detail), instrument('PARCC GLM', r.glm.state, null, r.glm.detail),
      instrument('PARCC QWEN', r.qwen.state, null, r.qwen.detail), planner, r.disk && instrument('DISK', r.disk.state, r.disk.value, r.disk.detail)].filter(Boolean);
    inst.replaceChildren(...rows);inst.style.gridTemplateColumns = `repeat(${rows.length},1fr)`;
    if (firstPaint && !calm) {  // Staggered entrance once; later refreshes just update in place.
      [...inst.children].forEach((c, i) => c.style.setProperty('--i', i)); inst.classList.add('enter');
      setTimeout(() => inst.classList.remove('enter'), 1600);
    }
  }

  function renderHero(s) {
    const running = s.running[0], go = $('go');
    $('eyebrow').replaceChildren(dot(running ? 'ok' : 'warn', true), document.createTextNode(running ? 'LIVE · A RUN IS GOING' : 'STANDBY · READY FOR THE NEXT RUN'));
    // Remote (11-10, user): several runs share the robot, so New run is always offered; a visitor is sent to the invite page first.
    const remote = document.body.dataset.gateway === '1', guest = document.body.dataset.guest === '1';
    const open = running && !remote;
    go.href = open ? shell.runUrl(running) : guest ? '/login' : '/ui/#new-run';
    $('go-label').textContent = open ? 'OPEN THE RUN' : 'NEW RUN';
  }

  function countUp(node, target) {
    // Animation frames pause in hidden tabs, so the final value is also set by a timer.
    if (document.hidden) { node.textContent = String(target); return; }
    const span = 900;
    setTimeout(() => {
      const begun = performance.now();
      const step = now => { const t = Math.min(1, (now - begun) / span); node.textContent = String(Math.round(target * (1 - Math.pow(1 - t, 3)))); if (t < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }, 450);
    setTimeout(() => { node.textContent = String(target); }, 450 + span + 100);
  }

  function renderStats(s) {
    const t = s.today, box = $('today'); box.replaceChildren();
    for (const [n, l, bad] of [[t.runs, 'RUNS TODAY'], [t.solved, 'SOLVED', t.solved === 0 && t.runs > 0], [t.attempts, 'ATTEMPTS']]) {
      const d = el('div'), num = el('div', 'n', firstPaint && !calm ? '0' : String(n)); if (bad) num.style.color = 'var(--bad)'; d.append(num, el('div', 'l', l)); box.append(d);
      if (firstPaint && !calm) countUp(num, n);
    }
  }

  function render() { if (!status) return; renderHero(status); renderInstruments(status); renderStats(status); playScenes(status.scenes); firstPaint = false; }

  function showError(text) { const e = $('error'); e.textContent = text; e.hidden = !text; }

  async function load(refresh) {
    try {
      const response = await fetch(`/api/home/status${refresh ? '?refresh=1' : ''}`, {cache: 'no-store'});
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
      status = body; render(); showError('');
    } catch (error) { showError(`Could not read the current status: ${error.message}. Retrying every 10 seconds.`); }
  }

  load(true);
  setInterval(() => load(false), 10000);
})();
