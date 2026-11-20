/* Run page (trial version): turns the raw dev-v2 "view" of one run into the small model the page draws.
 * Pure functions, no DOM, no network: lite.js fetches, this file only reads. Tested by tests/test_run_lite_ui.cjs.
 * Everything here is derived from fields the existing 8769 API already returns; nothing is invented.
 */
(function (global) {
  'use strict';
  const TERMINAL = ['completed', 'failed', 'cancelled', 'condition_invalid', 'needs_review', 'stopped', 'budget_exhausted', 'not_started'];
  const VIEW_ORDER = [['advisor_replay.mp4', 'external', 'Main'], ['advisor_replay_side.mp4', 'side', 'Side'],
    ['advisor_replay_left_wrist.mp4', 'left_wrist', 'Left wrist'], ['advisor_replay_right_wrist.mp4', 'right_wrist', 'Right wrist'],
    ['advisor_replay_top.mp4', 'top', 'Top']];
  const TRIM_KEY = {external: 'overview'};
  const POLICY_TEXT = {
    reactive_help: 'Asks you after a failed attempt.',
    agent_initiated_help: 'Decides for itself when to ask you.',
    autonomous: 'Works alone and never asks you.',
    full_trace_aware_attention_planner: 'A planner decides when to ask you.'
  };
  const REASONS = {
    native_success: ['Solved', 'The simulator\'s own success check passed.'],
    operator_cancel: ['Stopped by you', 'You stopped this run. Everything so far is saved.'],
    operator_paused: ['Paused', 'The run is paused and can be resumed.'],
    quota_stop: ['Stopped: model usage limit', 'The robot model reached the usage limit set for this run. Everything so far is saved; continue after the limit resets.'],
    quota_unavailable: ['Stopped: usage limit unreadable', 'The run stopped as a safety measure because the model usage limit could not be read. This is a service problem, not a problem with the task.'],
    fatal_error: ['Stopped on an error', 'The run hit an error it could not recover from.']
  };

  const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
  const clean = s => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  // Failure reasons in words a person understands; raw class names never reach the page.
  const WHY_TEXT = [[/^RecoverablePlanRejection\b/, 'The motion planner refused the move'], [/^(The try failed|failed)\.?$/i, 'Failed without a recorded reason'],
    [/^[A-Z][A-Za-z]+(Error|Exception|Rejection|Timeout)\b/, 'A software error stopped the try']];
  const plainWhy = s => { const t = clean(s); const hit = WHY_TEXT.find(([re]) => re.test(t)); return hit ? hit[1] : t; };

  function taskTitle(id) {
    const m = /^pick_place_(.+)_to_(.+)$/.exec(id || '');
    const words = s => s.split('_').map(w => w.toUpperCase()).join(' ');
    if (m) return {a: words(m[1]), b: words(m[2]), sep: '→'};
    return {a: words(id || 'unknown task'), b: '', sep: ''};
  }

  // The file URL of anything under agent-view/ for this run (same rule as live.js devFile).
  function fileUrl(base, uri) {
    if (typeof uri !== 'string') return null;
    const name = uri.startsWith('agent-view/') ? uri.slice(11) : uri;
    if (!name || name.split('/').some(p => !p || p.startsWith('.') || p.includes('\\'))) return null;
    return base + name.split('/').map(encodeURIComponent).join('/');
  }

  function plainProblem(entry) {
    const src = clean(entry.source), err = entry.error;
    const dig = e => typeof e === 'string' ? e : (e && typeof e === 'object') ? (dig(e.message) || dig(e.error) || dig(e.reason) || (e.status ? 'status: ' + e.status : '')) : '';
    const text = dig(err) || JSON.stringify(err);
    const raw = clean(text);
    if (/^Memory staging/.test(src)) return {scope: 'run', text: 'A lesson could not be saved for later runs.', raw: src + ': ' + raw};
    if (/frame cleanup/i.test(src)) return {scope: 'minor', text: 'Disk cleanup of saved frames did not run yet.', raw: src + ': ' + raw};
    if (/^quota/.test(src) || /quota_unreadable/.test(raw)) return {scope: 'run', text: 'The model usage limit could not be read.', raw: src + ': ' + raw};
    if (src === 'controller') return {scope: 'run', text: raw.replace(/^broker_invariant:\s*/, 'Internal check failed: '), raw: src + ': ' + raw};
    if (/^e\d{6}\b/.test(src)) return {scope: 'try', text: 'Practice run ' + Number(src.slice(1, 7)) + ': ' + raw.slice(0, 160), raw: src + ': ' + raw};
    return {scope: 'minor', text: raw.slice(0, 200) || 'An unnamed error was recorded.', raw: src + ': ' + raw};
  }

  // One rule for the run view and the run list: the reason code decides what kind of ending it was.
  const ERROR_REASONS = ['quota_stop', 'quota_unavailable', 'fatal_error', 'condition_invalid', 'budget_exhausted'];
  function kindOf(state, reason) {
    if (state === 'paused' || (state === 'planned' && reason === 'operator_paused')) return 'paused';
    if (reason === 'native_success') return 'solved';
    if (reason === 'operator_cancel' || state === 'cancelled') return 'stopped';
    if (ERROR_REASONS.includes(reason) || ['failed', 'needs_review', 'condition_invalid', 'budget_exhausted', 'stopped', 'not_started'].includes(state)) return 'error';
    if (state === 'completed') return 'unsolved';
    return 'running';
  }
  const KIND_LABEL = {paused: 'Paused', solved: 'Solved', stopped: 'Stopped by you', unsolved: 'Not solved', running: 'Trying'};
  function statusOf(view, exp, agent, wait) {
    const state = exp && exp.state || view.state, reason = exp && exp.reason || view.reason;
    const kind = kindOf(state, reason), ended = ['solved', 'stopped', 'error', 'unsolved'].includes(kind);
    if (wait && !ended) return {kind: 'needs_you', label: 'Needs you', ended: false};
    return {kind, label: kind === 'error' ? (REASONS[reason] || ['Stopped on error'])[0] : KIND_LABEL[kind], ended};
  }

  function build(input) {
    const {session = {}, view, exp = {}, options = {}, now = Date.now() / 1000} = input;
    const a = view.agent || {}, compat = view.dev_v2_compat || {}, sel = exp.selection || {};
    const base = `/api/dev-v2/experiments/${encodeURIComponent(view.experiment_id)}/cells/${encodeURIComponent(view.key)}/agent/files/`;
    const timeline = a.timeline || [], tries = a.tries || [], versions = compat.versions || [];
    const wait = a.human_wait || null;
    const status = statusOf(view, exp, a, wait);
    const reason = exp.reason || view.reason;
    const limit = num(a.limits && a.limits.formal_versions) || num(sel.max_versions) || 10;
    const used = num(a.counts && a.counts.formal_versions) != null ? a.counts.formal_versions : versions.length;

    // Attempts = submitted versions. Practice runs before one belong to it.
    const eventsByExec = {};
    timeline.forEach(t => { if (t.execution_id) (eventsByExec[t.execution_id] = eventsByExec[t.execution_id] || []).push(t); });
    let submitted = tries.filter(t => t.submitted).sort((x, y) => x.number - y.number);
    // A run whose tries list lacks its submits still has its versions: show those, without practice runs.
    if (!submitted.length && versions.length) submitted = versions.map((v, i) => ({execution_id: v.execution_id, number: Number(String(v.execution_id || 'e0').slice(1)) || i + 1, why: 'The attempt was submitted'}));
    const attempts = [];
    let prev = 0, prevEnd = timeline.length ? Math.min(...timeline.map(t => t.at)) : null;
    submitted.forEach((sub, i) => {
      const practice = tries.filter(t => !t.submitted && t.number > prev && t.number < sub.number);
      const ver = versions.find(v => v.execution_id === sub.execution_id) || versions[i] || {};
      const submitEv = (eventsByExec[sub.execution_id] || []).find(e => e.kind === 'formal_version');
      const end = submitEv ? submitEv.at : Math.max(...(eventsByExec[sub.execution_id] || [{at: prevEnd}]).map(e => e.at));
      const rec = ver.recording || {}, trim = rec.trim && rec.trim.videos ? rec.trim : null;
      const views = VIEW_ORDER.map(([name, key, label]) => {
        const r = (ver.replays || {})[name];
        if (!r) return null;
        const tUri = trim && trim.videos && trim.videos[TRIM_KEY[key] || key] && trim.videos[TRIM_KEY[key] || key].uri;
        return {key, label, url: fileUrl(base, tUri || r.uri), trimmed: Boolean(tUri)};
      }).filter(v => v && v.url);
      const solved = ver.native_success === true;
      attempts.push({
        n: i + 1, version: ver.version || i + 1, executionId: sub.execution_id, start: prevEnd, end,
        solved, headline: solved ? 'solved' : clean((ver.story && ver.story.short) || 'not solved'),
        sentence: clean(solved ? (ver.story && ver.story.sentence) || '' : sub.wrong || (ver.story && ver.story.sentence) || sub.why),
        wrong: clean(sub.wrong), next: clean(sub.next), checkerAgrees: ver.agree !== false,
        practice: practice.map(t => ({number: t.number, ok: t.ok, why: plainWhy(t.why), kind: t.kind, video: t.video ? fileUrl(base, t.video) : null})),
        replay: views.length ? {views, duration: num(trim ? rec.trim.trimmed_duration_seconds : rec.duration_seconds), real: trim ? num(rec.trim.original_duration_seconds) : null, trimmed: views.some(v => v.trimmed)} : null
      });
      prev = sub.number; prevEnd = end;
    });
    // Tries after the last submitted attempt belong to the attempt in progress.
    // 11-18 (user): the tries of the attempt in progress showed raw class names ("RecoverablePlanRejection: start_state_…"); same words as finished attempts
    let current = tries.filter(t => !t.submitted && t.number > prev).map(t => ({...t, why: plainWhy(t.why)}));
    let currentStart = prevEnd;
    // 11-20 (user: the chart said Attempt 3, the conversation Attempt 2): number attempts the way the chart does — one per
    // version, including a version Eval never ran (it has no submitted try) — and hand the tries out in order using
    // each version's own try count. Only when the counts agree; otherwise keep the submission-based list above.
    const sortedVersions = versions.slice().sort((x, y) => (x.version || 0) - (y.version || 0));
    const ordered = tries.filter(t => !t.submitted).sort((x, y) => x.number - y.number);
    const counted = sortedVersions.reduce((n, v) => n + ((v.before_attempt && v.before_attempt.trials) || []).length, 0);
    if (sortedVersions.length && counted <= ordered.length && sortedVersions.length >= attempts.length) {
      const formalAt = vid => { const e = timeline.find(t => t.kind === 'formal_version' && t.version_id === vid); return e ? e.at : null; };
      let cursor = 0, startAt = timeline.length ? Math.min(...timeline.map(t => t.at)) : null;
      const byVersion = sortedVersions.map((ver, i) => {
        const k = ((ver.before_attempt && ver.before_attempt.trials) || []).length, practice = ordered.slice(cursor, cursor + k);cursor += k;
        const sub = (ver.execution_id && tries.find(t => t.submitted && t.execution_id === ver.execution_id)) || {};
        const old = attempts.find(x => ver.execution_id && x.executionId === ver.execution_id);
        const end = formalAt(ver.version_id) ?? (old ? old.end : startAt);
        const solved = ver.native_success === true, story = ver.story || {};
        const row = {
          n: ver.version || i + 1, version: ver.version || i + 1, executionId: ver.execution_id || null, start: startAt, end, solved,
          headline: solved ? 'solved' : clean(story.short || 'not solved'),
          sentence: clean(solved ? story.sentence || '' : sub.wrong || story.sentence || sub.why || ''),
          wrong: clean(sub.wrong), next: clean(sub.next), checkerAgrees: ver.agree !== false,
          practice: practice.map(t => ({number: t.number, ok: t.ok, why: plainWhy(t.why), kind: t.kind, video: t.video ? fileUrl(base, t.video) : null})),
          replay: old ? old.replay : null, notRun: !ver.execution_id
        };
        startAt = end;return row;
      });
      attempts.splice(0, attempts.length, ...byVersion);
      current = ordered.slice(cursor).map(t => ({...t, why: plainWhy(t.why)}));
      currentStart = startAt;
    }

    // Help: one entry per ask_help event. A person's reply is "hint", anything else is the AI advisor.
    const questionOf = eid => {
      const iv = (compat.help_intervals || []).find(x => x.execution_id === eid), it = iv && iv.request_view && (iv.request_view.items || []).find(i => i.label === 'Question');
      return it ? clean(it.value) : '';
    };
    let hintNo = 0;
    const helps = timeline.filter(t => t.kind === 'help' && t.result).map(t => {
      const r = t.result, human = r.provider === 'human' || r.provider === 'human_assisted';
      return {at: t.at, executionId: t.execution_id, human, hintNo: human ? ++hintNo : null, content: clean(r.content),
        provider: r.provider, trigger: r.fallback_trigger || (r.flags && r.flags.fallback_trigger) || null, forced: Boolean(r.forced_fallback),
        reasonText: clean((r.decision && r.decision.reason) || r.reason || ''), question: questionOf(t.execution_id),
        failed: r.status === 'help_error', failText: r.status === 'help_error' ? (/timeout/i.test(String(r.error_class || '') + String(r.message || '')) ? 'nobody answered in time and the request timed out' : 'the request could not be completed') : ''};
    }).sort((x, y) => x.at - y.at);

    // Problems.
    const problems = [], seen = new Set();
    if (view.error) problems.push(Object.assign(plainProblem({source: 'controller', error: String(view.error)}), {scope: 'run'}));
    problems.forEach(p => seen.add(p.text));
    (a.errors || []).map(plainProblem).forEach(p => { if (!seen.has(p.text)) { seen.add(p.text); problems.push(p); } });
    const runProblems = problems.filter(p => p.scope === 'run'), tryProblems = problems.filter(p => p.scope === 'try'), minor = problems.filter(p => p.scope === 'minor');

    // Stuck summary (stats page).
    const stuckMap = new Map();
    tries.filter(t => !t.ok).forEach(t => { const w = plainWhy(t.why); stuckMap.set(w, (stuckMap.get(w) || 0) + 1); });
    const stuck = [...stuckMap].map(([why, count]) => ({why, count})).sort((x, y) => y.count - x.count);

    const att = a.attention || {};
    const humanHints = helps.filter(h => h.human);
    const ownKey = Boolean(sel.agent_provider && Object.values(sel.agent_provider.roles || {}).some(r => r && r.credential && r.credential !== 'lab'));
    const ctl = compat.run_controls || {};
    const start = num(exp.created_at) || (timeline.length ? Math.min(...timeline.map(t => t.at)) : null);
    const lastAt = timeline.length ? Math.max(...timeline.map(t => t.at)) : num(view.updated_at);
    const endAt = status.ended ? (lastAt || null) : null;
    const task = [...(options.tasks || []), ...(options.fixture_tasks || [])].find(t => t.task_id === view.task_id) || {};

    let error = null;
    if (status.kind === 'error') {
      const rr = REASONS[reason] || ['Stopped on an error', 'The run stopped and the reason is not one the page knows.'];
      error = {title: rr[0], body: rr[1] + (REASONS[reason] ? '' : ' Reason code: ' + clean(reason) + '.'),
        saved: `${attempts.length} attempt${attempts.length === 1 ? '' : 's'} and ${tries.length} practice run${tries.length === 1 ? '' : 's'} are saved.`,
        technical: [view.error, a.fatal_cause, ...(a.errors || []).filter(e => e.source === 'controller').map(e => e.error)].filter(Boolean).map(x => typeof x === 'string' ? x : JSON.stringify(x)).join('\n')};
    }

    const setup = [
      ['Mode', clean(view.mode || sel.mode || 'unknown').replace(/^./, c => c.toUpperCase())],
      ['Asks for help', (POLICY_TEXT[view.policy_id] || clean(view.policy_id)).replace(/\.$/, '').replace(/^Asks you /, '').replace(/^./, c => c.toLowerCase())],
      ['Limits', `${limit} attempts · ${num(sel.help_budget) || num(a.limits && a.limits.help) || '?'} asks · ${Math.round((num(a.wall_limit) || num(sel.wall_limit) || 0) / 3600 * 10) / 10} h`],
      ['Who answers help', sel.help_mode === 'human_only' ? `only you · up to ${num(sel.human_reply_seconds) || 600} s, no AI fallback` : sel.help_mode === 'ai' ? 'the AI advisor, right away' : num(sel.human_reply_seconds) ? `you first · ${sel.human_reply_seconds} s, then the AI advisor` : 'the AI advisor'],
      ['Robot model', clean(a.dev_model || sel.dev_model || 'unknown')],
      ['Checker / advisor', [a.eval_model, a.advisor_model].filter(Boolean).filter((m, i, l) => l.indexOf(m) === i).join(' / ') || 'unknown'],
      ['Scene', 'seed ' + view.seed],
      ['Lessons', (a.memory_use || []).length || sel.run_notes ? `on · ${(a.memory_use || []).reduce((n, m) => n + (m.notes || []).length, 0)} read` : 'off']
    ];

    return {
      id: view.experiment_id, key: view.key, files: base, task: {id: view.task_id, title: taskTitle(view.task_id), prompt: task.task_prompt || '', seed: view.seed},
      strategy: POLICY_TEXT[view.policy_id] || clean(view.policy_id), status, reason, reasonText: (REASONS[reason] || [reason || ''])[0],
      limit, used, cells: Math.min(10, Math.max(0, Math.round(used / limit * 10))), wallSeconds: num(a.wall_seconds),
      attempts, current: {tries: current, start: currentStart, running: Boolean(a.executing)}, executing: a.executing || null,
      waitSeconds: num(sel.human_reply_seconds), helpBudget: num(sel.help_budget) || num(a.limits && a.limits.help) || null,
      helps, humanHints, advisorHelps: helps.filter(h => !h.human), wait,
      problems, runProblems, tryProblems, minor, stuck, setup, error,
      humanSeconds: num(att.actual_human_seconds) || 0, estimatedHumanSeconds: num(att.hint_human_seconds) || 0,
      quota: ownKey && a.quota ? {percent: a.quota.used_percent, runPoints: a.quota.run_points} : null,
      controls: {state: ctl.state || null, canPause: Boolean(ctl.can_pause), canResume: Boolean(ctl.can_resume), blocked: ctl.resume_blocked_reason || ''},
      start, endAt, lastAt, solvedAttempt: attempts.find(x => x.solved) || null, now,
      live: !status.ended && status.kind !== 'paused' && Boolean(a.executing)
    };
  }

  // The rail groups runs by what they need from the person, using only fields /api/runs already lists.
  function railKind(s) { return kindOf(s.state === 'planned' ? (s.experiment_state || s.state) : s.state, s.reason); }
  function railGroup(s) { const k = railKind(s); return k === 'paused' ? 'paused' : k === 'running' ? 'running' : 'ended'; }
  function railDate(s) { const t = Number(s.created_at); if (!t) return ''; const d = new Date(t * 1000); return ' · ' + d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}) + ' ' + clock(t); }
  function railLabel(s) { return railLabelOnly(s) + railDate(s); }
  function railLabelOnly(s) { const k = railKind(s); return k === 'error' ? 'stopped on error' : k === 'stopped' ? 'stopped by you' : k === 'unsolved' ? 'not solved' : k === 'running' ? 'running' : k; }

  function clock(sec) {
    if (sec == null) return '--:--';
    const d = new Date(sec * 1000);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  function mmss(sec) {
    if (sec == null || !isFinite(sec)) return '--';
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0');
  }
  function span(sec) {
    if (sec == null || !isFinite(sec)) return '--';
    const m = Math.round(sec / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
  }
  function ago(sec, now) {
    if (sec == null) return 'unknown';
    const d = Math.max(0, Math.round(now - sec));
    return d < 90 ? d + ' s ago' : d < 5400 ? Math.round(d / 60) + ' min ago' : d < 172800 ? Math.round(d / 3600) + ' h ago' : Math.round(d / 86400) + ' days ago';
  }

  const api = {build, kindOf, railKind, railGroup, railLabel, taskTitle, fileUrl, plainProblem, clock, mmss, span, ago, POLICY_TEXT, REASONS};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.LiteModel = api;
})(typeof window !== 'undefined' ? window : globalThis);
