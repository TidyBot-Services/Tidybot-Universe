"""Operator-only, virtual Dev v2 evidence. Reads never export into an agent workspace."""
import hashlib
import json
import math
from pathlib import Path
import re

from .dev_agent_workspace import read_beneath

ROOT = 'agent-view'
VIDEO = r'advisor_replay(?:_(?:side|top|left_wrist|right_wrist))?\.mp4'
TRIM_VIDEO = r'trim/(?:overview|side|top|left_wrist|right_wrist)\.mp4'


def wire(value):
    # Both operator HTTP servers serialize this sorted object to these exact bytes.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def ref(name, value):
    raw = value if isinstance(value, bytes) else wire(value)
    return dict(uri=ROOT+'/'+name, sha256=hashlib.sha256(raw).hexdigest())


def public(cell, name, default=None):
    from .dev_agent_view import artifact
    try:
        return artifact(cell, name)[0]
    except FileNotFoundError as error:
        if 'digest' in str(error):
            raise
        return default


def episode_file(cell, eid, name):
    """Enumerated display media only; never follow an archive-supplied host path."""
    if not re.fullmatch(r'e\d+', eid) or not re.fullmatch(VIDEO+r'|camera_recording\.json|camera_views\.json', name):
        raise FileNotFoundError('Not agent recording evidence')
    root = Path(cell['artifact_dir'])/'agent/private/executions'/eid
    episodes = [p for p in root.glob('openarm365-dev-*') if p.is_dir() and not p.is_symlink()]
    if len(episodes) != 1:
        raise FileNotFoundError('Agent recording unavailable')
    try:
        return read_beneath(root, episodes[0].name+'/'+name, 512*1024*1024)
    except (OSError, ValueError):
        raise FileNotFoundError('Agent recording unavailable') from None


_DIGESTS = {}


def episode_path(cell, eid, name):
    """Enumerated display media as a verified local Path (range-capable transport)."""
    if not re.fullmatch(r'e\d+', eid) or not re.fullmatch(VIDEO+'|'+TRIM_VIDEO, name):
        raise FileNotFoundError('Not agent recording evidence')
    root = Path(cell['artifact_dir'])/'agent/private/executions'/eid
    if any(p.is_symlink() for p in (root,*root.parents)):
        raise FileNotFoundError('Agent recording unavailable')
    root = root.resolve()
    episodes = [p for p in root.glob('openarm365-dev-*') if p.is_dir() and not p.is_symlink()]
    if len(episodes) != 1:
        raise FileNotFoundError('Agent recording unavailable')
    if name.startswith('trim/'):
        from .replay_trim import load_verified
        value = load_verified(episodes[0])
        view = Path(name).stem
        if value is None or view not in value['videos']:
            raise FileNotFoundError('Agent trim unavailable')
        path = episodes[0]/value['videos'][view]['file']
    else:
        path = episodes[0]/name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise FileNotFoundError('Agent recording unavailable')
    return path


def trim_for(cell, eid):
    from .replay_trim import public_trim
    try:
        path = episode_path(cell, eid, 'advisor_replay.mp4')
        return public_trim(path.parent)
    except (OSError,ValueError):
        return None


def file_digest(path):
    """SHA-256 of a sealed recording, cached by (path, size, mtime)."""
    stat = path.stat(); key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _DIGESTS:
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):h.update(chunk)
        _DIGESTS[key] = h.hexdigest()
    return _DIGESTS[key]


def recording(cell, eid):
    from .dev_agent_view import clean
    try:
        value = clean(json.loads(episode_file(cell, eid, 'camera_recording.json')))
    except FileNotFoundError:
        value = {}
    try:
        views = clean(json.loads(episode_file(cell, eid, 'camera_views.json')))
    except FileNotFoundError:
        views = {'views': {}}
    videos = {}
    for name in ['advisor_replay.mp4']+[f'advisor_replay_{v}.mp4' for v in ('side','top','left_wrist','right_wrist')]:
        try:
            raw = episode_file(cell, eid, name)
        except FileNotFoundError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        videos[name] = dict(uri=f'{ROOT}/replays/{eid}/{digest}/{name}', sha256=digest)
    # The virtual directory also serves the original named camera files.
    return value, views, videos


def help_evidence(cell, vid):
    """An agent help boundary belongs after vid, regardless of provider status."""
    from .dev_agent_view import _read, clean
    value = public(cell, f'versions/{vid}/help.json')
    if value and value.get('agent_policy_version')=='agent-policy-v2':return None
    if not value or value.get('status') in ('not_requested', 'not_eligible'):
        return None
    private = Path(cell['artifact_dir'])/'agent/private'
    failure = _read(private, f'help/{vid}/error.json', {})
    delivered = value.get('delivered') is True or value.get('status') == 'delivered'
    result = dict(value, provider='advisor', delivered=delivered, rejected=not delivered,
                  after_version=int(vid[1:]),
                  reason=clean(value.get('reason') or failure.get('error') or value.get('error') or value.get('status')))
    # The invalid_usage failure happened after delivery. Only this known accounting
    # defect permits substituting the persisted Advisor cost; never recalculate it.
    if delivered and value.get('status') == 'unavailable' and failure.get('error') == 'invalid_usage':
        advisor = _read(private, f'help/{vid}/advisor.json', {})
        seconds = advisor.get('hint_human_seconds')
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0:
            result.update(hint_human_seconds=seconds,
                          recorded_hint_human_seconds=value.get('hint_human_seconds'), cost_corrected=True)
    return result


def error_groups(errors, timeline):
    """Keep outcome failures once per execution, with routine diagnostics collapsed."""
    requests = {r['id']: r for r in timeline}
    failures, details = {}, []
    seen = set()
    def execution(source):
        request = requests.get(source.removeprefix('request:'), {})
        match = re.search(r'\be\d+\b', source)
        return request.get('execution_id') or (match[0] if match else None)
    fatal_executions = {execution(e['source']) for e in errors
        if 'controller-error.json' in e['source'] or re.search(r'fatal_|execution_failed', json.dumps(e['error']))}
    fatal_executions.discard(None)
    for entry in errors:
        source, value = entry['source'], entry['error']
        text = json.dumps(value, sort_keys=True)
        request = requests.get(source.removeprefix('request:'), {})
        eid = execution(source)
        item = dict(entry, execution_id=eid, version_id=request.get('version_id'))
        rejected = isinstance(value, dict) and (value.get('status') == 'rejected' or value.get('rejected') is True)
        fatal = not rejected and (source in ('controller','Memory') or source.startswith('errors/memory-') or 'controller-error.json' in source
            or 'fatal_' in text or 'execution_failed' in text
            or (eid in fatal_executions and source.endswith(' action')))
        identity = (source, text)
        if identity in seen:
            continue
        seen.add(identity)
        if fatal:
            key = eid or source
            if key not in failures:
                failures[key] = dict(item, reports=[])
            group = failures[key]
            group['reports'].append(item)
            # The SDK action explains the failure better than its controller code.
            if source.endswith(' action'):
                group.update(item)
        else:
            details.append(item)
    return list(failures.values()), details


def attempt_story(review):
    """Small operator captions derived from Eval, without evidence citations or IDs."""
    reason = str(review.get('reason') or '')
    text = (str(review.get('failure_class') or '')+' '+reason).lower()
    if review.get('passed') is True:
        short, failure = 'solved', None
    elif re.search(r'\bik\b|inverse kinematics|out.of.reach', text):
        short, failure = 'out of reach', 'Out of reach'
    elif re.search(r'no bilateral|not establish bilateral|not confirm.*grasp|grasp.*not confirmed', text):
        short, failure = 'grasp not confirmed', 'Planner refused'
    elif 'collision' in text:
        short, failure = 'path blocked', 'Collision risk'
    elif 'slip' in text:
        short, failure = 'object slipped', 'Grasp lost'
    elif review.get('passed') is False:
        short, failure = 'not solved', 'Task incomplete'
    else:
        short, failure = 'awaiting evaluation', None
    # Remove source citations before choosing the first sentence. Full Eval text
    # stays in the downloadable agent report (region 03 no longer shows it, 10-30).
    plain = re.sub(r'\([^)]*(?:\.txt|\.json|\.png|\.jpg|e\d{6}|v\d{4})[^)]*\)', '', reason)
    plain = re.sub(r'\b(?:stdout|stderr|trace|state_log|frames)\b.*', '', plain, flags=re.I)
    plain = re.sub(r'\b(?:e\d{6,}|v\d{4,}|[a-f0-9]{32,})\b', '', plain)
    plain = re.sub(r'\s+', ' ', plain).strip()
    plain = re.sub(r'\s+([.,;!?])', r'\1', plain)
    sentences = re.split(r'(?<=[.!?])\s+', plain)
    sentence = (next((s for s in sentences if 'bilateral' in s.lower()), sentences[0])
                if short == 'grasp not confirmed' else sentences[0]) if plain else 'No evaluation reason recorded.'
    sentence = sentence.replace('bilateral contact', 'contact on both sides').replace('pregrasp', 'pre-grasp')
    sentence = sentence.replace('failed IK planning', 'had no reachable path')
    if len(sentence) > 260:
        sentence = sentence[:257].rsplit(' ', 1)[0]+'…'
    return dict(short=short, failure_class=failure, sentence=sentence)


def activity_intervals(cell, timeline):
    """Partition request sequence at formal submits; rejected trials can keep going."""
    intervals = []
    current = dict(checks=0, previews=0, trials=[])
    for record in timeline:
        if record.get('kind') == 'formal_version' and record.get('version_id'):
            intervals.append(dict(current, version_id=record['version_id']))
            current = dict(checks=0, previews=0, trials=[])
        elif record.get('op') == 'ik_check':
            current['checks'] += 1
        elif record.get('op') == 'plan_dry_run':
            current['previews'] += 1
        elif record.get('op') in ('observe', 'find_objects'):
            label = 'observed scene' if record['op'] == 'observe' else 'found objects'
            if label not in current.setdefault('exploration', []):
                current['exploration'].append(label)
        elif record.get('kind') == 'no_eval_execution' and record.get('execution_id'):
            eid = record['execution_id']
            outcome = public(cell, f'executions/{eid}/result.json', {})
            events = public(cell, f'executions/{eid}/trace.json', []) or []
            completed = outcome.get('completed') is True or outcome.get('status') in ('completed', 'completed_with_rejections')
            errors = ' '.join(json.dumps(e.get('error', '')) for e in events).lower()
            stopped = not completed and (outcome.get('status') == 'failed' or bool(outcome.get('error')))
            if completed:
                cause = 'move refused, continued' if outcome.get('rejected_calls') else 'trial completed'
            elif 'gripper' in errors and ('velocity' in errors or 'speed' in errors):
                cause = 'gripper speed limit'
            elif 'object/hand transform' in errors or 'slip' in errors:
                cause = 'object slipped'
            elif 'limit' in errors or 'bound' in errors:
                cause = 'safety limit reached'
            elif stopped:
                cause = 'fatal SDK fault'
            else:
                cause = 'trial in progress'
            current['trials'].append(dict(execution_id=eid, color='red' if stopped else 'grey', cause=cause))
    # Preserve activity after the latest submit for an in-progress full story.
    if current['checks'] or current['previews'] or current['trials'] or current.get('exploration'):
        intervals.append(dict(current, version_id=None))
    return intervals


def plain_sentence(text):
    """One operator sentence, without file citations or diagnostic identifiers."""
    text = re.sub(r'\([^)]*\.(?:txt|json|jsonl|png|jpg)[^)]*\)', '', str(text or ''))
    text = re.sub(r'\b[\w/-]+\.(?:txt|json|jsonl|png|jpg)(?::[\d–—,: -]+)?', '', text)
    text = re.sub(r'\s+([,;!?]|\.(?!\d))', r'\1', re.sub(r'\s+', ' ', text)).strip()
    return re.split(r'(?<=[.!?])\s+', text)[0]


def snapshot_notes(cell, number, cut='pre-submit'):
    root = Path(cell['artifact_dir'])/'agent/private/snapshots'
    try:
        manifest = json.loads(read_beneath(root, f'{cut}-v{number}.json', 32*1024*1024))
        entry = manifest['files'].get('work/notes.md')
        if not entry:
            return ''
        digest = entry['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            return None
        raw = read_beneath(root, 'objects/'+digest, 32*1024*1024)
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != digest:
            return None
        return raw.decode('utf-8')
    except (OSError, ValueError, KeyError, TypeError):
        return None


def plan_paragraphs(note):
    # Dev's append-only journal uses one newline per entry. Also accept Markdown
    # paragraphs with wrapped lines when blank-line separators are present.
    return [p.strip() for p in re.split(r'\n\s*\n' if re.search(r'\n\s*\n', note) else r'\n', note) if p.strip()]


def plan_sentence(paragraph, number):
    sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', paragraph).strip())
    named = re.compile(rf'\bv0*{number}\b', re.I)
    return next((s for s in sentences if named.search(s)), sentences[-1])


def attempt_plan(cell, version, submission):
    """Authored plan notes only. Never use the compatibility Eval reason."""
    from difflib import SequenceMatcher
    from .dev_agent_view import clean
    from .dev_agent_diagnostics import screen_text
    number = version['version']
    current = snapshot_notes(cell, number)
    previous = snapshot_notes(cell, number-1) if number > 1 else ''
    note = ''
    if current is not None and previous is not None:
        if current.startswith(previous):
            note = current[len(previous):].strip()
        else:
            old, new = previous.splitlines(keepends=True), current.splitlines(keepends=True)
            note = ''.join(''.join(new[j:k]) for tag, _, _, j, k in
                           SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
                           if tag in ('insert', 'replace')).strip()
    summary = version.get('summary') or version.get('agent', {}).get('summary') or submission.get('summary')
    summary = clean(screen_text(summary)) if isinstance(summary, str) else ''
    # Legacy r6 authored its submission paragraphs after the pre-submit cut.
    # Recover only the explicit current-version submission paragraph from the
    # verified post-exec snapshot, never its later execution/Eval recap.
    if not summary and current is not None and previous is not None:
        after = snapshot_notes(cell, number, 'post-exec')
        if after is not None and after.startswith(current):
            submitted = re.compile(rf'\bv0*{number}\s+(?:now running|submitted)\b', re.I)
            paragraph = next((p for p in plan_paragraphs(after[len(current):]) if submitted.search(p)), '')
            if paragraph:
                note = paragraph
    note = clean(note)
    paragraphs = plan_paragraphs(note)
    return dict(summary=summary or (plan_sentence(paragraphs[-1], number) if paragraphs else ''), note=note or summary,
                source='summary' if summary else 'notes' if note else 'unavailable')


def replay_stages(events, review, solved=False):
    """Interpret ordered SDK actions, then group adjacent stages to a short strip."""
    names = ['Find objects', 'Pre-grasp', 'Descent', 'Close', 'Lift', 'Carry', 'Release']
    states = ['unreached']*7
    errors = {}; closed = False; reached = False; lifted = False
    for event in events or []:
        op = str(event.get('operation', '')).split('.')[-1]
        source = str(event.get('source', ''))
        result = event.get('result') or {}
        rejected = event.get('status') in ('failed', 'blocked', 'rejected') or isinstance(result, dict) and result.get('rejected') is True
        if event.get('status') in ('started', 'running'):
            continue
        args = (event.get('arguments') or {}).get('args', [])
        stage = None
        if op == 'find_objects':stage = 0
        elif op == 'close':stage = 3
        elif op == 'open' and closed:stage = 6
        elif op.startswith('move'):
            if source == 'base':
                if closed:stage = 5
            elif closed:
                stage = 5 if lifted else 4
            elif op == 'move_delta' and len(args) >= 3 and isinstance(args[2], (int, float)) and args[2] < 0 and reached:
                stage = 2
            else:
                stage = 2 if reached else 1
        if stage is None:
            continue
        states[stage] = 'failed' if rejected else 'completed'
        if rejected:errors[stage] = json.dumps(event.get('error') or result).lower()
        else:
            errors.pop(stage, None)
            if stage == 1:reached = True
            if stage == 3:closed = True
            if stage == 4:lifted = True
    passed = solved or review.get('passed') is True
    if passed:
        states = ['completed']*7
    elif review.get('passed') is False and not errors:
        reason = str(review.get('reason', '')).lower()
        stage = (2 if re.search(r'descen\w*.*(?:fail|reject)|grasp.*\bik\b', reason) else
                 3 if re.search(r'close|bilateral|grasp.*not confirmed', reason) else
                 4 if re.search(r'lift|slip', reason) else
                 5 if re.search(r'carry|transport', reason) else
                 6 if re.search(r'release|containment|outside.*sink', reason) else None)
        if stage is not None:
            states[stage] = 'failed'; errors[stage] = reason
    reason = attempt_story(review)['short']
    rows = [dict(stages=[i], label=name, state=states[i], cause=reason if states[i]=='failed' else '')
            for i, name in enumerate(names)]
    # Keep the failing stage distinct; combine the unreached suffix first, then
    # later completed phases. This retains 3–5 chips for every verdict.
    while len(rows) > 5:
        pair = next((i for i in range(len(rows)-2, -1, -1)
                     if rows[i]['state'] == rows[i+1]['state'] and rows[i]['state'] != 'failed'), None)
        if pair is None:break
        left, right = rows[pair:pair+2]
        rows[pair:pair+2] = [dict(stages=left['stages']+right['stages'], label=left['label']+' · '+right['label'], state=left['state'], cause='')]
    # For failed runs group the rest of an unreached suffix even at five chips.
    while len(rows) > 3 and rows[-1]['state'] == rows[-2]['state'] == 'unreached':
        left, right = rows[-2:]
        rows[-2:] = [dict(stages=left['stages']+right['stages'], label=left['label']+' · '+right['label'], state='unreached', cause='')]
    return rows


TERMINAL_STATES = ('completed','failed','cancelled','needs_review','stopped','condition_invalid','budget_exhausted','not_started')


def native_of(version):
    """Operator-only native verdict. Agent runs from 10-29 store it under version['agent'] (sol3)."""
    for value in (version.get('native_success'), (version.get('agent') or {}).get('native_success')):
        if type(value) is bool:
            return value
    return None


def eval_unavailable_reason(cell, vid, review):
    """Why a formal Eval produced no verdict, from the recorded error (never hidden)."""
    try:
        recorded = json.loads(read_beneath(Path(cell['artifact_dir'])/'agent/private', f'eval/{vid}/error.json', 1024*1024))
    except (OSError, ValueError):
        recorded = {}
    text = str(recorded.get('error_text') or '')
    kind = str(recorded.get('error_class') or review.get('error') or '')
    if 'Errno 28' in text or 'No space left' in text:
        return 'disk full (ENOSPC)'
    if kind == 'timeout' or 'timeout' in text.lower():
        return 'timed out'
    if kind or text:
        return ': '.join(x for x in (kind, text) if x)
    return 'no verdict was recorded'


def version_evidence(cell, version, *, media=True, instruments=True):
    """Bind public evidence to the same identities checked by devWorkspaceSnapshot."""
    from .dev_agent_view import clean
    native = native_of(version)
    if native is not None:
        version = dict(version, native_success=native)
    n = version['version']; vid = version.get('version_id', f'v{n:04d}')
    prefix = f'compat/{vid}/'
    submission = public(cell, f'versions/{vid}/submission.json', {})
    eid = version.get('execution_id') or submission.get('execution_id')
    code = public(cell, f'versions/{vid}/bundle.py', '')
    assets = {'policy.py': code, 'eval.json': public(cell, f'versions/{vid}/eval.json', {})}
    sources = []
    source_root = Path(cell['artifact_dir'])/'agent/private/public/versions'/vid/'sources'
    for path in sorted(source_root.glob('*.py')):
        name = 'sources/'+path.name
        value = public(cell, f'versions/{vid}/{name}')
        if value is not None:
            assets[name] = value
            sources.append(dict(name=path.name, **ref(prefix+name, value.encode())))
    source = ref(prefix+'policy.py', code.encode())
    # The digest identifies the exact sanitized config shown to the operator.
    config = public_config(cell)
    identity = dict(run_id='run:'+cell['key'], attempt_id=f'attempt:{cell["key"]}:{n-1}',
                    config_sha256=hashlib.sha256(wire(config)).hexdigest(), policy_sha256=source['sha256'])
    help_value = help_evidence(cell, vid)
    if help_value is not None:
        assets['help.json'] = help_value
    request_view = None
    try:
        raw = read_beneath(Path(cell['artifact_dir'])/'agent/private', f'help/{vid}/request-view.json', 32*1024*1024)
        from .dev_request_view import display_request_view
        request_view = clean(display_request_view(json.loads(raw)))
        assets['request-view.json'] = request_view
    except FileNotFoundError:
        pass
    review = assets['eval.json'] or clean(version.get('eval', {}))
    review = dict(review, reference_suggestion=review.get('advice', ''))
    assets['eval.json'] = review
    plan = attempt_plan(cell, version, submission)
    row = dict(clean(version), source=source, sources=sources, source_text=code,
               config_sha256=identity['config_sha256'], diff_text='', decision_content=(help_value or {}).get('decision'),
               help_content=help_value, help=ref(prefix+'help.json', help_value) if help_value else None,
               request_view=request_view, evaluator_review=review, eval_review=ref(prefix+'eval.json', review),
               model_input=None, reason=plan['summary'], plan=plan, story=attempt_story(review),
               try_next='' if version.get('native_success') is True or review.get('passed') is True else plain_sentence(review.get('reference_suggestion')),
               eval_stages=replay_stages([], review, version.get('native_success') is True), execution_state='not_dispatched')
    row['version_id'] = vid
    # The banner and captions follow the native verdict; an Eval that did not run says so with its reason.
    story = row['story']
    if review.get('status') == 'unavailable' or (review.get('passed') is None and cell.get('state') in TERMINAL_STATES):
        row['eval_unavailable'] = eval_unavailable_reason(cell, vid, review)
        story = dict(story, short='Eval did not run', failure_class=None,
                     sentence=f'Eval did not run: {row["eval_unavailable"]}.')
    if native is True:
        story = dict(story, short='solved', failure_class=None)
    elif native is False and story.get('short') == 'solved':
        story = dict(story, short='Eval passed, task check failed', failure_class='Eval disagreed')
    row['story'] = story
    if not eid:
        return row, assets
    citations = []
    for citation in review.get('evidence', []):
        entry = dict(citation)
        # Preserve the full citation; link only a recognized public filename.
        match = re.match(r'^(stdout\.txt|stderr\.txt|trace\.json|state_log\.json|diagnostics/planner\.json|frames/[A-Za-z0-9_.-]+\.(?:png|jpg))(?=$|[\s:#])', citation.get('source', ''))
        if match:
            from .frame_cleanup import was_cleaned, CLEANED
            frame = Path(cell['artifact_dir'])/'agent/private/public/executions'/eid/match[1]
            if was_cleaned(frame):entry.update(label=CLEANED, available=False)
            else:entry['uri'] = f'{ROOT}/executions/{eid}/'+match[1]
        citations.append(entry)
    row['citations'] = citations
    outcome = public(cell, f'executions/{eid}/result.json', {})
    events = public(cell, f'executions/{eid}/trace.json', [])
    row['eval_stages'] = replay_stages(events, review, version.get('native_success') is True)
    assets['trace.json'] = dict(identity, sdk_events=events, error=outcome.get('error'))
    frames = public(cell, f'executions/{eid}/frames.json', [])
    assets['frames.json'] = frames
    if instruments:
        assets['state-log.json'] = public(cell, f'executions/{eid}/state_log.json', [])
    rec, views, videos = recording(cell, eid) if media else ({}, {}, {})
    assets['camera_views.json'] = views
    # Named files retain the original renderer's camera-view discovery contract.
    assets.update({name: value for name, value in videos.items()})
    rec = dict(rec, run_id=identity['run_id'], attempt_id=identity['attempt_id'],
               video_saved='advisor_replay.mp4' in videos,
               video=videos.get('advisor_replay.mp4', {}).get('uri'))
    trim = trim_for(cell, eid) if media else None
    if trim is not None:rec['trim'] = trim
    bundle = dict(identity, dev_version=n, formal_eligible=False, artifact_dir=ROOT+'/'+prefix.rstrip('/'),
                  recording=rec, policy_id=cell['policy_id'],
                  artifacts={'trace': ref(prefix+'trace.json', assets['trace.json'])})
    if type(version.get('native_success')) is bool:
        bundle.update(native_success=version['native_success'], native_evaluator=dict(
            run_id=identity['run_id'], attempt_id=identity['attempt_id'], evaluated=True))
    assets['bundle.json'] = bundle
    row.update(execution_id=eid, execution_state='settled', recording=rec,
               execution_status='completed' if outcome.get('completed') else outcome.get('status', 'unknown'),
               frames=ref(prefix+'frames.json', frames),
               instrument=ref(prefix+'state-log.json', assets['state-log.json']) if instruments else None,
               replays=videos,
               public_feedback=outcome, bundle=ref(prefix+'bundle.json', bundle), trace=ref(prefix+'trace.json', assets['trace.json']))
    return row, assets


def try_replay(cell, eid):
    """What the replay panel reads, for a finished try whether or not it was submitted."""
    outcome = public(cell, f'executions/{eid}/result.json')
    if outcome is None:
        raise FileNotFoundError('Try not finished')
    rec, views, videos = recording(cell, eid)
    trim = trim_for(cell, eid)
    if trim is not None:rec = dict(rec, trim=trim)
    return dict(recording=dict(rec, video_saved='advisor_replay.mp4' in videos), views=views.get('views', {}), replays=videos,
                sdk_events=public(cell, f'executions/{eid}/trace.json', []), error=outcome.get('error'))


def public_config(cell):
    from .dev_agent_view import clean
    root = Path(cell['artifact_dir'])/'agent/private/public'
    try:
        return clean(json.loads(read_beneath(root, 'config.json', 32*1024*1024)))
    except FileNotFoundError:
        return dict(task_id=cell.get('task_id'), seed=cell.get('seed'))


def evidence(cell, name):
    """Resolve only a formal version's virtual files or digest-addressed replay."""
    match = re.fullmatch(r'replays/(e\d+)/([a-f0-9]{64})/('+VIDEO+'|'+TRIM_VIDEO+r')', name)
    if match:
        eid, digest, filename = match.groups()
        path = episode_path(cell, eid, filename)
        if filename.startswith('trim/'):
            from .replay_trim import load_verified
            value = load_verified(path.parent.parent)
            if value is None or value['videos'][path.stem]['sha256'] != digest:
                raise FileNotFoundError('Agent trim digest mismatch')
        if file_digest(path) != digest:
            raise FileNotFoundError('Agent recording digest mismatch')
        # A Path lets the server answer Range requests (206) like one-call files/.
        return path, 'video/mp4'
    match = re.fullmatch(r'compat/(e\d+)/replay\.json', name)
    if match:
        return json.loads(wire(try_replay(cell, match[1]))), 'application/json'
    match = re.fullmatch(r'compat/(v\d+)/(policy\.py|sources/[A-Za-z0-9_-]+\.py|(?:bundle|trace|frames|state-log|eval|help|request-view|camera_views)\.json|'+VIDEO+r')', name)
    if not match:
        raise FileNotFoundError('Not agent display evidence')
    vid, filename = match.groups()
    version = next((v for v in cell.get('versions', []) if v.get('version_id', f'v{v["version"]:04d}') == vid), None)
    if version is None:
        raise FileNotFoundError('Unknown formal version')
    _, assets = version_evidence(cell, version,
        media=filename in ('bundle.json','camera_views.json') or filename.endswith('.mp4'),
        instruments=filename=='state-log.json')
    if filename not in assets:
        raise FileNotFoundError('Agent display evidence unavailable')
    value = assets[filename]
    if filename.endswith('.mp4'):
        return evidence(cell, value['uri'].removeprefix(ROOT+'/'))
    if filename.endswith('.py'):
        return value, 'text/plain; charset=utf-8'
    return json.loads(wire(value)), 'application/json'


def compat(cell, agent, ledger_jobs):
    from .dev_agent_view import clean
    versions = [version_evidence(cell, v)[0] for v in cell.get('versions', [])]
    activity = activity_intervals(cell, agent['timeline'])
    for row in versions:
        row['before_attempt'] = next((i for i in activity if i['version_id'] == row['version_id']),
                                     dict(checks=0, previews=0, trials=[]))
    v2=agent.get('agent_policy_version')=='agent-policy-v2'
    intervals=execution_help_intervals(cell,agent,ledger_jobs) if v2 else []
    if not v2:
        incoming = {}
        for row in versions:
            boundary = {key: row.get(key) for key in ('help_content', 'help', 'request_view', 'decision_content')}
            row['after_help'] = boundary['help_content']
            if boundary['help_content']:
                intervals.append(dict(boundary, after_version=row['version'], version=row['version']+1))
                incoming[row['version']+1] = boundary
        for row in versions:
            row.update(incoming.get(row['version'], dict(help_content=None, help=None, request_view=None, decision_content=None)))
    by_request = {r['id']: r for r in agent['timeline']}
    by_vid = {v.get('version_id', f'v{v["version"]:04d}'): v for v in versions}
    current_vid = None
    help_versions = {}
    for record in agent['timeline']:
        if record.get('kind') == 'formal_version':
            current_vid = record.get('version_id')
        if record.get('op') == 'ask_help':
            help_versions[record['id']] = current_vid
    jobs = []
    for job in ledger_jobs:
        jid = job['id']
        if v2 and jid.startswith('help:e'):
            interval=next((h for h in intervals if h['help_content']['help_id']==jid),None)
            if interval:
                jobs.append(dict(id=jid,kind='advisor',version=interval['version'],state=job['state'],
                    reserved_at=job.get('reserved_at'),settled_at=job.get('settled_at'),
                    reservation={},actual={},receipt={'help':interval['help']}))
            continue
        record = by_request.get(jid.split(':', 1)[-1], {})
        vid = record.get('version_id') or record.get('result', {}).get('version_id') or (job.get('receipt') or {}).get('version_id')
        vid = vid or help_versions.get(record.get('id'))
        if jid.startswith('help:v'):
            vid = jid.split(':', 1)[1]
        row = by_vid.get(vid)
        if row is None:
            continue
        kind = 'execute' if job['kind']=='request' and record.get('op')=='submit' else 'eval' if job['kind']=='eval' else 'advisor' if job['kind']=='help' or record.get('op')=='ask_help' else None
        if kind is None:
            continue
        if kind == 'advisor':
            interval = next((h for h in intervals if h['after_version'] == row['version']), None)
            if interval is None or any(j['kind']=='advisor' and j['version']==interval['version'] for j in jobs):
                continue
            interval.update(reserved_at=job.get('reserved_at'), settled_at=job.get('settled_at'))
        receipt = {'execution_status': row.get('execution_status'), 'bundle': row.get('bundle'), 'trace': row.get('trace')} if kind=='execute' else {'eval_review': row['eval_review']} if kind=='eval' else {'help': interval['help']}
        jobs.append(dict(id=jid, kind=kind, version=row['version']+(kind=='advisor'), state=job['state'],
                         reserved_at=job.get('reserved_at'), settled_at=job.get('settled_at'),
                         reservation={}, actual={}, receipt=receipt))
    counts = agent['counts']; limits = agent['limits']
    mapping = {'executions':'moving_executions', 'dev_calls':'model_turns', 'eval_calls':'eval_runs', 'help_count':'help'}
    budget = {key: limits.get(value) for key,value in mapping.items()}
    used = {key: counts.get(value, 0) for key,value in mapping.items()}
    budget['provider_tokens'] = None; used['provider_tokens'] = None
    held = {key: agent['held'].get(value, 0) for key,value in mapping.items()}
    unlimited = agent.get('wall_limit') is None  # Remote time-share: no time limit
    goal = dict(wall_used=agent['wall_seconds'], wall_limit=agent['wall_limit'],
                wall_remaining=None if unlimited else max(0, agent['wall_limit']-agent['wall_seconds']), limits=budget, used=used, reserved=held)
    phase = 'terminal' if agent['phase']=='stopped' else {'waiting on execution':'sdk','waiting for robot':'robot_wait','Eval':'eval','help':'advisor','turn running':'model'}.get(agent['phase'], 'prepared')
    # Same shapes as a one-call cell: a finished run has no lifecycle, only ended
    # run controls, plus the Codex quota points spent (first and last sample).
    terminal = phase == 'terminal'
    controls = dict(state='ended' if terminal else 'running', can_pause=False, can_resume=False, can_keep_running=False,
                    resume_blocked_reason='This run has finished.' if terminal else None, current_step=None,
                    paused_since=None, paused_seconds_total=0, interrupted_by_stop=None)
    return dict(clean({k:cell.get(k) for k in ('key','experiment_id','state','reason','task_id','seed','policy_id','mode')}),
                **(dict(memory_visibility='validation_override',memory_visibility_label='Memory visibility override · validation') if cell.get('memory_visibility')=='validation_override' else {}),
                **({'reduced_budget': agent['reduced_budget']} if agent.get('reduced_budget') else {}),
                dev_mode='agent', agent_policy_version=agent.get('agent_policy_version','agent-policy-v1'), artifact_dir=ROOT, versions=versions, help_intervals=intervals,
                trailing_activity=next((i for i in activity if i['version_id'] is None), None), jobs=jobs, budget=budget, used=used,
                ledger={'goal':goal}, lifecycle=None if terminal else {'phase':phase, 'can_stop':True, **({'robot_ahead':(agent.get('robot') or {}).get('ahead')} if phase=='robot_wait' else {})},
                run_controls=controls, quota_usage=quota_usage(cell))


def quota_usage(cell):
    """First and last recorded weekly Codex usage for this cell, if both are readable."""
    path = Path(cell.get('artifact_dir', '/missing'))/'agent/private/quota.jsonl'
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    values = [r.get('used_percent') for r in rows if isinstance(r.get('used_percent'), (int, float))]
    return dict(before=values[0], after=values[-1]) if values else None


def execution_help_intervals(cell, agent, ledger_jobs):
    """One row per execution help, including practice before any formal submit."""
    if not cell.get('artifact_dir'):return []
    from .dev_agent_view import _read, clean
    from .dev_request_view import display_request_view
    private = Path(cell['artifact_dir'])/'agent/private'
    jobs = {j['id']: j for j in ledger_jobs}
    after_execution = {}
    submitted = 0
    for record in agent['timeline']:
        if record.get('kind') == 'formal_version' and record.get('version_id'):
            submitted = int(record['version_id'][1:])
        if record.get('execution_id'):
            after_execution[record['execution_id']] = submitted
    intervals = []
    for path in sorted((private/'public/executions').glob('e*/help.json')):
        eid = path.parent.name
        if not re.fullmatch(r'e\d+', eid):continue
        value = public(cell, f'executions/{eid}/help.json')
        if not value:continue
        hid = 'help:'+eid
        if value.get('help_id') != hid:raise ValueError('agent_help_identity_mismatch')
        job = jobs.get(hid, {})
        receipt = job.get('receipt') or {}
        origin=value.get('origin_version_id')
        after = int(origin[1:]) if isinstance(origin,str) and re.fullmatch(r'v\d+',origin) else after_execution.get(eid, 0)
        delivered = value.get('delivered') is True
        help_value = dict(value, provider=value.get('provider','advisor'), delivered=delivered, rejected=not delivered,
                          after_version=after, reason=value.get('reason') or (value.get('decision') or {}).get('reason') or value.get('error') or value.get('status'))
        # Never infer free from not-delivered. The ledger is the accounting fact.
        help_value['charged'] = job.get('state') in ('claimed','unknown','settled') and job.get('resources',{}).get('help',0)>0
        help_value['hint_human_seconds'] = receipt.get('hint_human_seconds',0) if help_value['charged'] else 0
        help_value['actual_help_count'] = job.get('resources',{}).get('help',0) if help_value['charged'] else 0
        help_value['actual_cost_known'] = job.get('state') == 'settled'
        help_value['ask_kind'] = ('forced' if cell['policy_id'] in ('reactive_help','retry_k_then_ask') else
                                  'agent_initiated' if cell['policy_id']=='agent_initiated_help' else 'system_injected')
        raw = _read(private, f'help/{eid}/request-view.json')
        view = clean(display_request_view(raw)) if raw else None
        intervals.append(dict(help_content=clean(help_value), help={'uri':ROOT+f'/executions/{eid}/help.json'},
            after_version=after, version=after+1, execution_id=eid, help_id=hid,
            request_view=view, decision_content=clean(value.get('decision')),
            reserved_at=job.get('reserved_at'), settled_at=job.get('settled_at')))
    return sorted(intervals,key=lambda row:(row['reserved_at'] is None,row['reserved_at'] or 0,row['execution_id']))
