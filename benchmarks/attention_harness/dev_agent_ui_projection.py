"""Operator-only, virtual Dev v2 evidence. Reads never export into an agent workspace."""
import hashlib
import json
import math
from pathlib import Path
import re

from .dev_agent_workspace import read_beneath

ROOT = 'agent-view'
VIDEO = r'advisor_replay(?:_(?:side|top|left_wrist|right_wrist))?\.mp4'


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
    if not re.fullmatch(r'e\d+', eid) or not re.fullmatch(VIDEO, name):
        raise FileNotFoundError('Not agent recording evidence')
    root = (Path(cell['artifact_dir'])/'agent/private/executions'/eid).resolve()
    episodes = [p for p in root.glob('openarm365-dev-*') if p.is_dir() and not p.is_symlink()]
    if len(episodes) != 1:
        raise FileNotFoundError('Agent recording unavailable')
    path = episodes[0]/name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise FileNotFoundError('Agent recording unavailable')
    return path


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
        fatal = not rejected and (source == 'controller' or 'controller-error.json' in source
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


def version_evidence(cell, version, *, media=True, instruments=True):
    """Bind public evidence to the same identities checked by devWorkspaceSnapshot."""
    from .dev_agent_view import clean
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
    row = dict(clean(version), source=source, sources=sources, source_text=code,
               config_sha256=identity['config_sha256'], diff_text='', decision_content=(help_value or {}).get('decision'),
               help_content=help_value, help=ref(prefix+'help.json', help_value) if help_value else None,
               request_view=request_view, evaluator_review=review, eval_review=ref(prefix+'eval.json', review),
               model_input=None, reason=review.get('reason', ''), execution_state='not_dispatched')
    if not eid:
        return row, assets
    citations = []
    for citation in review.get('evidence', []):
        entry = dict(citation)
        # Preserve the full citation; link only a recognized public filename.
        match = re.match(r'^(stdout\.txt|stderr\.txt|trace\.json|state_log\.json|diagnostics/planner\.json|frames/[A-Za-z0-9_.-]+\.png)(?=$|[\s:#])', citation.get('source', ''))
        if match:
            entry['uri'] = f'{ROOT}/executions/{eid}/'+match[1]
        citations.append(entry)
    row['citations'] = citations
    outcome = public(cell, f'executions/{eid}/result.json', {})
    events = public(cell, f'executions/{eid}/trace.json', [])
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


def public_config(cell):
    from .dev_agent_view import clean
    root = Path(cell['artifact_dir'])/'agent/private/public'
    try:
        return clean(json.loads(read_beneath(root, 'config.json', 32*1024*1024)))
    except FileNotFoundError:
        return dict(task_id=cell.get('task_id'), seed=cell.get('seed'))


def evidence(cell, name):
    """Resolve only a formal version's virtual files or digest-addressed replay."""
    match = re.fullmatch(r'replays/(e\d+)/([a-f0-9]{64})/('+VIDEO+r')', name)
    if match:
        eid, digest, filename = match.groups()
        path = episode_path(cell, eid, filename)
        if file_digest(path) != digest:
            raise FileNotFoundError('Agent recording digest mismatch')
        # A Path lets the server answer Range requests (206) like one-call files/.
        return path, 'video/mp4'
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
    intervals = []
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
        jid = job['id']; record = by_request.get(jid.split(':', 1)[-1], {})
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
    goal = dict(wall_used=agent['wall_seconds'], wall_limit=agent['wall_limit'],
                wall_remaining=max(0, agent['wall_limit']-agent['wall_seconds']), limits=budget, used=used, reserved=held)
    phase = 'terminal' if agent['phase']=='stopped' else {'waiting on execution':'sdk','Eval':'eval','help':'advisor','turn running':'model'}.get(agent['phase'], 'prepared')
    return dict(clean({k:cell.get(k) for k in ('key','experiment_id','state','reason','task_id','seed','policy_id','mode')}),
                dev_mode='agent', artifact_dir=ROOT, versions=versions, help_intervals=intervals, jobs=jobs, budget=budget, used=used,
                ledger={'goal':goal}, lifecycle={'phase':phase, 'can_stop':phase!='terminal'})
