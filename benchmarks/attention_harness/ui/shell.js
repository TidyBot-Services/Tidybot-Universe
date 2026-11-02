(function (global) {
  'use strict';
  // Shared shell for the cover, the main UI and the Memory monitor: the Home · Run · Memory
  // switcher, the Light | Dark switch (one theme key for all three pages), the logo diamond
  // and the task rail. Each page keeps its own content and its own data.
  const doc = global.document;
  const THEME_KEY = 'attention.theme', OLD_MEMORY_KEY = 'attention.memory.theme';
  const PAGES = [['home', 'Home', '/ui/home.html'], ['run', 'Run', '/ui/'], ['memory', 'Memory', '/ui/memory.html']];
  // Run status words, the same as the main UI's task list (live.js recordStatus).
  const TAGS = {completed: ['bad', 'Not solved'], needs_review: ['', 'Needs review'], failed: ['bad', 'Failed'], cancelled: ['', 'Stopped'], stopped: ['', 'Stopped'],
    condition_invalid: ['bad', 'Condition invalid'], budget_exhausted: ['', 'Budget reached'], running: ['run', 'Running'], planned: ['run', 'Waiting'], not_started: ['', 'Not started']};
  const POLICIES = {autonomous: 'Autonomous', reactive_help: 'Reactive help', full_trace_aware_attention_planner: 'Attention planner'};

  function read(store, key) { try { return global[store].getItem(key); } catch (_) { return null; } }
  function write(store, key, value) { try { global[store].setItem(key, value); } catch (_) { /* private window: keep the default */ } }
  const calm = () => !!global.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

  let theme = (read('localStorage', THEME_KEY) || read('localStorage', OLD_MEMORY_KEY)) === 'dark' ? 'dark' : 'light';
  const listeners = [], switches = [];
  if (doc) doc.documentElement.dataset.theme = theme;
  // Where the last click in a switcher came from, so the pill and the diamond can move on arrival.
  const from = read('sessionStorage', 'attention.shell.from');
  write('sessionStorage', 'attention.shell.from', '');

  // Run fn now, after layout, after web fonts and on every resize, so the pill always sits on its target.
  function settle(node, fn) {
    global.requestAnimationFrame?.(() => fn());
    doc.fonts?.ready?.then(() => fn());
    if (global.ResizeObserver) new global.ResizeObserver(() => fn()).observe(node);
  }

  function slide(group, target, animate) {
    const pill = group.querySelector('.ind');
    if (!pill || !target || !target.offsetWidth) return;
    pill.classList.toggle('sh-anim', !!animate && !calm());
    pill.style.width = `${target.offsetWidth}px`;
    pill.style.transform = `translateX(${target.offsetLeft}px)`;
  }

  function paintTheme(group) {
    group.querySelectorAll('button[data-theme]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.theme === theme)));
    slide(group, group.querySelector(`button[data-theme="${theme}"]`), group.dataset.ready === '1');
  }

  function setTheme(name) {
    theme = name === 'dark' ? 'dark' : 'light';
    doc.documentElement.dataset.theme = theme;
    write('localStorage', THEME_KEY, theme);
    write('localStorage', OLD_MEMORY_KEY, theme);
    switches.forEach(paintTheme);
    listeners.forEach(fn => fn(theme));
  }

  function themeSwitch(group) {
    if (!group) group = doc.createElement('div');
    if (!group.querySelector('button[data-theme]')) group.innerHTML = '<button type="button" data-theme="light">Light</button><button type="button" data-theme="dark">Dark</button>';
    if (!group.querySelector('.ind')) group.insertAdjacentHTML('afterbegin', '<span class="ind" aria-hidden="true"></span>');
    group.classList.add('ab-themes');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Theme');
    group.addEventListener('click', e => { const b = e.target.closest('button[data-theme]'); if (b) setTheme(b.dataset.theme); });
    switches.push(group);
    settle(group, () => { paintTheme(group); group.dataset.ready = '1'; });
    return group;
  }

  function pageSwitcher(page, nav) {
    if (!nav) nav = doc.createElement('nav');
    // Home, Run and Memory; Remote joins them when the server has a lobby.
    const flags = (doc.getElementById('attentionbench-clear') || doc.body || {}).dataset || {}, bodyFlags = (doc.body || {}).dataset || {};
    const remote = flags.remote === '1';
    const remoteTab = flags.remoteTab === '1' || bodyFlags.remoteTab === '1';
    const extras = remoteTab ? [['remote', 'Remote', '/ui/remote']] : [];
    const pages = [...PAGES, ...extras];
    if (remote) page = 'remote';
    if (!nav.querySelector('a[data-page]')) nav.innerHTML = pages.map(([id, label, href]) => `<a href="${href}" data-page="${id}">${label}${id === 'memory' ? '<b class="ab-badge" hidden></b>' : ''}</a>`).join('');
    for (const [id, label, href] of extras) if (!nav.querySelector(`a[data-page="${id}"]`)) nav.insertAdjacentHTML('beforeend', `<a href="${href}" data-page="${id}">${label}</a>`);  // pages with a fixed tab list
    if (!nav.querySelector('.ind')) nav.insertAdjacentHTML('afterbegin', '<span class="ind" aria-hidden="true"></span>');
    nav.classList.add('ab-pages');
    nav.setAttribute('aria-label', 'Pages');
    nav.querySelectorAll('a[data-page]').forEach(a => {
      if (a.dataset.page === page) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.addEventListener('click', e => { if (a.dataset.page === page) e.preventDefault(); else write('sessionStorage', 'attention.shell.from', page); });
    });
    const here = nav.querySelector(`a[data-page="${page}"]`);
    const before = from && from !== page ? nav.querySelector(`a[data-page="${from}"]`) : null;
    let first = true;
    settle(nav, () => {
      if (first && before && !calm()) {  // Arrived from another page: start on its pill, then slide here.
        slide(nav, before, false);
        global.requestAnimationFrame(() => global.requestAnimationFrame(() => slide(nav, here, true)));
      } else slide(nav, here, !first);
      first = false;
    });
    return nav;
  }

  // The logo diamond turns a quarter on each page switch: a small tell that you moved, not reloaded.
  function turnLogo(logo, page) {
    if (!logo) return;
    const turns = Number(read('sessionStorage', 'attention.shell.turns')) || 0;
    logo.style.setProperty('--sh-turn', `${45 + turns * 90}deg`);
    if (!from || from === page || calm()) return;
    write('sessionStorage', 'attention.shell.turns', String(turns + 1));
    global.requestAnimationFrame(() => global.requestAnimationFrame(() => logo.style.setProperty('--sh-turn', `${45 + (turns + 1) * 90}deg`)));
  }

  function setMemory({href, count, pulse} = {}) {
    for (const a of doc.querySelectorAll('.ab-pages a[data-page="memory"]')) {
      if (href) a.href = href;
      const badge = a.querySelector('.ab-badge');
      if (badge && count != null) { badge.hidden = !count; badge.textContent = count || ''; }
      if (pulse && !calm()) { a.classList.remove('pulse'); void a.offsetWidth; a.classList.add('pulse'); }
    }
  }

  function setRunHref(href) { for (const a of doc.querySelectorAll('.ab-pages a[data-page="run"]')) a.href = href; }

  // Tasks shows or hides the rail; the choice is remembered per page, T toggles it like in the main UI.
  function drawer({button, wrap, key, open}) {
    const set = value => { wrap.classList.toggle('closed', !value); button.setAttribute('aria-expanded', String(value)); write('localStorage', key, value ? 'open' : 'closed'); };
    button.addEventListener('click', () => set(wrap.classList.contains('closed')));
    global.addEventListener('keydown', e => {
      if (e.key.toLowerCase() === 't' && !e.ctrlKey && !e.metaKey && !e.altKey && !/input|textarea|select/i.test(e.target.tagName)) set(wrap.classList.contains('closed'));
    });
    const saved = read('localStorage', key);
    set(saved ? saved === 'open' : !!open);
  }

  const clock = t => new Date(t * 1000).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
  const runUrl = r => `/ui/?dev=v2-${encodeURIComponent(r.key)}#exp=${encodeURIComponent(r.experiment_id)}`;

  function outcome(r) {
    if (r.solved) return ['ok', 'Solved'];
    if (r.all_failed) return ['bad', 'Failed'];
    if (r.system) return ['bad', 'System problem'];
    return TAGS[r.state] || ['', r.state];
  }

  // Attempts the robot ran out of the allowed number, written like the main UI's badge.
  function attemptsText(r) {
    const used = Number(r.attempts) || 0, limit = r.attempts_limit;
    if (!(limit > 0)) return `${used} attempt${used === 1 ? '' : 's'}`;
    return r.state === 'running' && used < limit ? `Attempt ${used + 1}/${limit}` : `${used}/${limit} attempt${limit === 1 ? '' : 's'}`;
  }

  // Recent runs from /api/home/status, the same rows on the cover and in Memory.
  function taskRail({list, meta, status, href = runUrl, current}) {
    meta.textContent = `${status.today.runs} runs today · ${status.running.length} running`;
    list.innerHTML = status.recent.map(r => {
      const [kind, word] = outcome(r);
      const attempts = attemptsText(r);
      return `<a class="ab-rec" href="${escape(href(r))}" title="${escape(r.why)}"${r.experiment_id === current ? ' aria-current="true"' : ''}><span>${escape(clock(r.at))} · ${escape(r.task)}<small>${escape(POLICIES[r.policy] || r.policy)} · ${attempts}</small>${r.execution_profile==='r3-20261025'?'<small>Execution settings r3 · not comparable with earlier runs</small>':''}</span><span class="ab-tag ${kind}">${escape(word)}</span></a>`;
    }).join('');
  }

  function mount() {
    for (const nav of doc.querySelectorAll('[data-shell-pages]')) pageSwitcher(nav.dataset.shellPages, nav);
    for (const group of doc.querySelectorAll('[data-shell-themes]')) themeSwitch(group);
    for (const logo of doc.querySelectorAll('.ab-shell-logo[data-shell-page]')) turnLogo(logo, logo.dataset.shellPage);
  }

  const api = {theme: () => theme, setTheme, onTheme: fn => listeners.push(fn), themeSwitch, pageSwitcher, turnLogo, setMemory, setRunHref, drawer, taskRail, outcome, attemptsText, runUrl, TAGS, POLICIES};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.AttentionShell = api;
  if (doc) { if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', mount); else mount(); }
})(typeof window !== 'undefined' ? window : globalThis);
