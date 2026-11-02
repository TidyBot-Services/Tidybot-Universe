"""Recorded Dev views driven by the lobby's persisted simulation clock. No workers or providers."""
import copy
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

from .lobby import Lobby, LobbyError, TERMINAL, safe_text

EXPERIMENT = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})(/.*)?')
INDEXED = {'versions', 'jobs', 'attempts', 'executions', 'timeline', 'history', 'help_intervals',
           'ask_required', 'failure_errors', 'details', 'errors', 'activity_intervals'}
TEXT_FIELDS = {'source_text', 'diff_text', 'model_input', 'prompt', 'plan', 'reason', 'try_next',
               'decision_content', 'request_view', 'after_help', 'help_content', 'content', 'question',
               'failure', 'advice', 'reference_suggestion', 'summary', 'note', 'message', 'error',
               'fatal_cause', 'pending_ask', 'active_help', 'forced_fallback', 'evaluator_review', 'eval',
               'public_feedback', 'citations', 'links', 'history', 'sources', 'instrument', 'quota',
               'description'}
RESOURCE_FIELDS = {'used', 'counts', 'held', 'remaining', 'reserved', 'budget', 'limits', 'resources',
                   'agent_resources', 'quota_usage', 'wall_seconds', 'wall_used', 'wall_remaining',
                   'wall_limit', 'provider_tokens', 'actual', 'reservation', 'size_bytes'}
LABELS = {'solved', 'not solved', 'awaiting evaluation', 'out of reach', 'path blocked', 'object slipped',
          'grasp not confirmed', 'Eval did not run', 'Eval passed, task check failed', 'Task incomplete',
          'Out of reach', 'Planner refused', 'Collision risk', 'Grasp lost', 'Eval disagreed'}


def _replace(value, replacements):
    if isinstance(value, dict):return {_replace(k, replacements):_replace(v, replacements) for k, v in value.items()}
    if isinstance(value, list):return [_replace(v, replacements) for v in value]
    if isinstance(value, str):
        for before, after in replacements:
            if before:value = value.replace(before, after)
        return value
    return value


def _index(row):
    if not isinstance(row, dict):return None
    if type(row.get('after_version')) is int:return row['after_version']
    for key in ('version', 'dev_version', 'attempt'):
        if type(row.get(key)) is int:return row[key]
    if type(row.get('index')) is int:return row['index']+1
    match = re.fullmatch(r'v(\d+)', str(row.get('version_id') or ''))
    return int(match[1]) if match else None


def _total(view):
    if view.get('cells'):return _total(view['cells'][0])
    return len((view.get('dev_v2_compat') or view).get('versions') or view.get('versions') or [])


def slice_view(source_view, k, phase):
    """Copy a recording at k completed attempts; phase may include target identity and wall times.

    Running views expose k settled attempts. Unknown non-indexed metadata is retained.
    """
    options = phase if isinstance(phase, dict) else dict(state=phase)
    state = options.get('state', 'running')
    total = _total(source_view)
    k = max(0, min(int(k), total))
    old_eid = source_view.get('experiment_id','')
    old_key = source_view.get('key') or next((c.get('key') for c in source_view.get('cells',[]) if c.get('key')), '')
    eid = options.get('experiment_id', 'devexp-'+hashlib.sha256((old_eid+':demo').encode()).hexdigest()[:24])
    key = options.get('key', hashlib.sha256((eid+':cell').encode()).hexdigest())
    value = _replace(copy.deepcopy(source_view), [(old_eid, eid), (old_key, key)])
    ratio = k/total if total else 0
    terminal = state in TERMINAL
    ui_state = 'completed' if state == 'finished' else 'planned' if state == 'queued' else state
    complete = state == 'finished' and k == total
    compat = source_view.get('dev_v2_compat') or source_view
    times = [j.get('settled_at') for j in compat.get('jobs',[]) if j.get('kind') in ('execute','eval')
             and (_index(j) or total+1)<=k and type(j.get('settled_at')) in (int,float)]
    cutoff = max(times) if times else None

    def walk(node):
        if isinstance(node, list):return [walk(v) for v in node]
        if not isinstance(node, dict):return node
        result = {}
        for name, item in node.items():
            if isinstance(item,list) and (name in INDEXED or any(_index(row) is not None for row in item)) and not complete:
                indexed = []
                last = 0
                for index, row in enumerate(item):
                    number = _index(row)
                    if number is not None:last = number
                    # An unindexed timeline record belongs to the surrounding attempt,
                    # not to an arbitrary position among probes/model turns.
                    number = number if number is not None else (last if name == 'timeline' else index+1)
                    if name == 'timeline' and (k == 0 or (cutoff is not None and type(row.get('at')) in (int,float) and row['at']>cutoff)):
                        visible = False
                    elif name == 'help_intervals':
                        visible = number <= k
                    else:visible = number <= k and (k > 0 or number == 0)
                    if visible:indexed.append(walk(row))
                result[name] = indexed
            elif name in ('used', 'counts', 'actual') and isinstance(item, dict) and state != 'finished':
                result[name] = {n:(v*ratio if type(v) in (int,float) else v) for n, v in item.items()}
            else:result[name] = walk(item)
        if 'experiment_id' in result or ('versions' in result and 'state' in result):
            result.update(state=ui_state, reason=('simulated_'+state if state != 'finished' else result.get('reason')),
                          cancelled=state=='cancelled')
            for name in ('created_at','updated_at'):
                if name in options:result[name] = options[name]
        if state != 'finished':
            for name in ('wall_seconds','wall_used'):
                if type(result.get(name)) in (int,float):result[name] *= ratio
            if 'wall_remaining' in result and type(result.get('wall_limit')) in (int,float):
                result['wall_remaining'] = max(0,result['wall_limit']-result.get('wall_used',0))
            if isinstance(result.get('budget'),dict) and isinstance(result.get('used'),dict):
                result['remaining'] = {n:max(0,v-(result['used'].get(n) or 0)) if type(v) in (int,float) else None
                                       for n,v in result['budget'].items()}
            if isinstance(result.get('limits'),dict) and isinstance(result.get('counts'),dict):
                result['remaining'] = {n:max(0,v-(result['counts'].get(n) or 0)-(result.get('held',{}).get(n) or 0))
                                       if type(v) in (int,float) else None for n,v in result['limits'].items()}
        return result

    value = walk(value)
    for obj in (value, value.get('dev_v2_compat', {})):
        obj.update(state=ui_state, cancelled=state=='cancelled')
        if state != 'finished':obj['reason'] = 'simulated_'+state
        obj.update({name:options[name] for name in ('created_at','updated_at') if name in options})
        if 'lifecycle' in obj:obj['lifecycle'] = None if terminal else dict(phase='advisor' if state=='waiting_human' else 'prepared', can_stop=True)
        if 'run_controls' in obj:
            obj['run_controls'].update(state='ended' if terminal else 'running', can_pause=False, can_resume=False, can_keep_running=False)
        # A pending simulated boundary must not expose the source's future answer.
        if state != 'finished':
            for interval in obj.get('help_intervals', []):
                if interval.get('after_version') == k:
                    interval.update(help_content=dict(delivered=False, status='pending' if state=='waiting_human' else 'skipped'),
                                    decision_content=None, request_view=None)
            for version in obj.get('versions', []):
                if version.get('version') == k:
                    version.update(after_help=None, help_content=None, decision_content=None)
    if isinstance(value.get('agent'), dict):
        value['agent']['phase'] = 'stopped' if terminal else 'help' if state=='waiting_human' else 'turn running'
        if state != 'finished':
            value['agent']['reason'] = 'simulated_'+state
            value['agent']['pending_ask'] = None
            value['agent']['active_help'] = None
    return value


def redact_for_other(view):
    """Keep the JSON skeleton, outcomes, fixed failure labels and read-only replay references."""
    def blank(value):
        if isinstance(value, list):return []
        if isinstance(value, dict):return {k:blank(v) for k,v in value.items()}
        return None

    def walk(value, parent=''):
        if isinstance(value, list):return [walk(v, parent) for v in value]
        if not isinstance(value, dict):
            if not isinstance(value, str):return value
            if parent in ('short','failure_class','cause'):return value if value in LABELS else 'Task incomplete'
            # Arbitrary strings from Dev, future fields and structured errors fail closed.
            if parent in ('key','experiment_id','run_id','attempt_id','session_id','execution_id','version_id','probe_id','help_id'):
                return value if re.fullmatch(r'[A-Za-z0-9:_-]{1,160}',value) else None
            # The task's identity and the lab-side setup are public; they come from the registry and the recording, not from the run's owner.
            if parent in ('model','dev_model','eval_model','advisor_model'):return value if re.fullmatch(r'[A-Za-z0-9.\-/]{1,40}',value) else None
            if parent in ('suite','task_id','task_name','robot','execution_arm','scene_id','official_env','base_mode','model_source','controller','execution_profile','camera_names','planner_mode','perception_mode'):
                return value if re.fullmatch(r'[A-Za-z0-9_.:/ -]{1,80}',value) else None
            if parent == 'task_prompt':return value if len(value)<=300 and not re.search(r'[\r\n]',value) else None
            if parent in ('sha256','config_sha256','policy_sha256'):return value if re.fullmatch('[a-f0-9]{64}',value) else None
            if parent in ('state','status','phase','kind','op','policy_id','mode','dev_mode','channel','agent_policy_version',
                          'execution_state','execution_status','mime_type','source','operation','event_type','view','filename','name','label','uri','url','video','artifact_dir'):
                if parent in ('uri','url','video','artifact_dir'):
                    return value if value.startswith(('/api/dev-v2/experiments/','agent-view/')) and '..' not in value else None
                if parent == 'filename':return value if re.fullmatch(r'(?:frames/)?[A-Za-z0-9_.-]+',value) else None
                return value if re.fullmatch(r'[A-Za-z0-9_./ :·-]{1,80}',value) else None
            return None
        result = {}
        for name, item in value.items():
            if name in TEXT_FIELDS or name in RESOURCE_FIELDS:
                # Retain the attempt ceiling; the UI uses it outside region 05.
                result[name] = ({k:(v if k=='formal_versions' else None) for k,v in item.items()}
                                if name=='limits' and isinstance(item,dict) else blank(item))
            else:result[name] = walk(item, name)
        return result
    return walk(copy.deepcopy(view))


def _clean(value):
    if isinstance(value,dict):
        return {k:None if re.search(r'(?i)(?:api.?key|token|credential|authorization|owner|request_key|ip_address)',k) else _clean(v)
                for k,v in value.items()}
    if isinstance(value,list):return [_clean(v) for v in value]
    return safe_text(value)


def source_timing(view, experiment):
    compat = view.get('dev_v2_compat') or view
    versions, jobs = compat.get('versions', []), compat.get('jobs', [])
    start = experiment.get('created_at') or view.get('created_at') or 0
    timestamps = [j.get('settled_at') for j in jobs if type(j.get('settled_at')) in (int,float)]
    end = max(timestamps+[experiment.get('updated_at') or start])
    duration = max(1, end-start, (view.get('agent') or {}).get('wall_seconds') or 0)
    done, helps = [], {}
    for index, version in enumerate(versions,1):
        times = [j['settled_at'] for j in jobs if j.get('version')==index and j.get('kind') in ('execute','eval')
                 and type(j.get('settled_at')) in (int,float)]
        at = max(times)-start if times else duration*index/max(1,len(versions))
        done.append(max(done[-1] if done else 0, min(duration, max(0,at))))
    for row in compat.get('help_intervals', []):
        help_value = row.get('help_content') or {}
        if not help_value.get('delivered'):continue
        number = row.get('after_version',0)
        if not 1 <= number <= len(versions):continue
        version = versions[number-1]
        failure = (version.get('story') or {}).get('short') or 'The attempt did not succeed.'
        delay = max(0,(row.get('settled_at') or 0)-(row.get('reserved_at') or 0))
        helps[str(number)] = dict(question='What should the robot do differently on its next attempt?',
                                   failure=safe_text(failure), delay=delay)
    last = versions[-1] if versions else {}
    native = last.get('native_success')
    solved = native is True or (native is None and (last.get('evaluator_review') or {}).get('passed') is True)
    limit = (view.get('agent') or {}).get('limits',{}).get('formal_versions') or compat.get('budget',{}).get('executions') or len(versions)
    limited = experiment.get('state')=='budget_exhausted' or view.get('state')=='budget_exhausted' or len(versions)>=limit
    result = 'solved' if solved else 'limit' if limited else 'failed'
    return dict(attempt_done_at=done, helps=helps, duration=max(duration, done[-1] if done else 1), limit=limit, result=result)


class DemoEngine:
    def __init__(self, config, bridge, *, clock=time.time):
        allowed = {'mode','state_dir','speed','sources','invitees','seed','per_person_running','per_person_queued','help_wait_seconds'}
        if (not isinstance(config,dict) or set(config)-allowed or config.get('mode') != 'demo'
                or not isinstance(config.get('state_dir'),str) or not Path(config['state_dir']).is_absolute()
                or not isinstance(config.get('sources'),dict) or not config['sources'] or bridge is None
                or type(config.get('seed',1)) is not int):
            raise ValueError('invalid lobby configuration')
        self.bridge, self.cache, self.file_cache = bridge, {}, {}
        self.config = config
        sources = {}
        for eid, entry in config['sources'].items():
            if (not re.fullmatch(r'devexp-[a-f0-9]{24}',eid) or not isinstance(entry,dict) or set(entry)!={'task_key','title'}
                    or not isinstance(entry['task_key'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',entry['task_key'])
                    or not isinstance(entry['title'],str) or not 1<=len(entry['title'])<=200 or safe_text(entry['title'])!=entry['title']):
                raise ValueError('invalid lobby source')
            experiment = bridge.get('/api/dev-v2/experiments/'+eid)[0]
            cells = experiment.get('cells') or []
            if len(cells) != 1:raise ValueError('demo source needs one recorded cell')
            key = cells[0]['key']
            if not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('invalid lobby source')
            view = bridge.get(f'/api/dev-v2/experiments/{eid}/cells/{key}/view')[0]
            self.cache[eid] = dict(experiment=experiment, view=view, key=key)
            sources[eid] = dict(source_id=eid, **entry, **source_timing(view, experiment))
            if not sources[eid]['attempt_done_at']:raise ValueError('demo source needs recorded attempts')
        if len({s['task_key'] for s in sources.values()}) != len(sources):raise ValueError('duplicate demo task')
        invitees = config.get('invitees', [])
        if (not isinstance(invitees,list) or any(not isinstance(r,dict) or set(r)!={'count'}
                or type(r['count']) is not int or not 0<=r['count']<=20 for r in invitees)):
            raise ValueError('invalid demo invitees')
        self.lobby = Lobby(config['state_dir'], sources, clock=clock, speed=config.get('speed',20), seed=config.get('seed',1),
            invitees=sum(r['count'] for r in invitees),
            **{k:config[k] for k in ('per_person_running','per_person_queued','help_wait_seconds') if k in config})

    def tick(self, now=None):
        self.lobby.tick(now)

    @staticmethod
    def key(run):
        return hashlib.sha256((run['experiment_id']+':cell').encode()).hexdigest()

    def replacements(self, run):
        source = self.cache[run['source_id']]
        return [(run['source_id'],run['experiment_id']), (source['key'],self.key(run))]

    def _bind_refs(self, value, run, viewer):
        if isinstance(value,list):return [self._bind_refs(v,run,viewer) for v in value]
        if not isinstance(value,dict):return value
        value = {k:self._bind_refs(v,run,viewer) for k,v in value.items()}
        uri = value.get('uri')
        if isinstance(uri,str) and re.fullmatch(r'agent-view/(?:compat/v\d+/(?:bundle|trace|frames)\.json|executions/e\d+/frames\.json)',uri):
            result = self._asset(run,'agent/files/'+uri.removeprefix('agent-view/'),viewer)
            value['sha256'] = hashlib.sha256(json.dumps(result[0],ensure_ascii=False).encode()).hexdigest()
        return value

    def _asset(self, run, name, viewer):
        mine = bool(viewer and run['owner']==viewer['id'])
        cache_key = (run['run_id'],mine,name)
        if cache_key in self.file_cache:return copy.deepcopy(self.file_cache[cache_key])
        source = self.cache[run['source_id']]
        result = self.bridge.get(f'/api/dev-v2/experiments/{run["source_id"]}/cells/{source["key"]}/{name}')
        value, content = result[:2]
        if content.startswith('application/json'):
            value = _clean(_replace(value,self.replacements(run)))
            if not mine:value = redact_for_other(value)
            value = self._bind_refs(value,run,viewer)
        elif isinstance(value,str):value = safe_text(_replace(value,self.replacements(run)))
        result = (value,content,*result[2:])
        # Media Paths stay references; media bytes are never duplicated in a cache.
        if content.startswith('application/json'):
            if len(self.file_cache)>512:self.file_cache.clear()
            self.file_cache[cache_key] = result
        return copy.deepcopy(result)

    def view(self, run, viewer):
        value = slice_view(self.cache[run['source_id']]['view'], run['current'],
            dict(state=run['state'], experiment_id=run['experiment_id'], key=self.key(run),
                 created_at=run['created_at'], updated_at=run['ended_at'] or self.lobby.clock()))
        value = _clean(value)
        if not viewer or run['owner'] != viewer['id']:value = redact_for_other(value)
        return self._bind_refs(value,run,viewer)

    def sessions(self, viewer):
        self.tick()
        rows = []
        # sessions() uses the same source projection as the existing /api/runs bridge.
        for run in self.lobby.records():
            source = self.cache[run['source_id']]
            original = source.get('session')
            if original is None:
                original = next((s for s in self.bridge.list() if s.get('experiment_id')==run['source_id']), None)
                if original is None:raise LobbyError('source_unavailable',503)
                source['session'] = original
            row = slice_view(original,run['current'],dict(state=run['state'],
                experiment_id=run['experiment_id'],key=self.key(run),created_at=run['created_at'],
                updated_at=run['ended_at'] or self.lobby.clock()))
            row.update(session_id='v2-'+self.key(run), experiment_id=run['experiment_id'], key=self.key(run),
                state='completed' if run['state']=='finished' else 'planned' if run['state']=='queued' else run['state'],
                experiment_state='completed' if run['state']=='finished' else run['state'],
                created_at=run['created_at'], updated_at=run['ended_at'] or self.lobby.clock())
            row = _clean(row)
            if not viewer or run['owner'] != viewer['id']:row = redact_for_other(row)
            row['lobby'] = self.lobby.card(run['run_id'],viewer)
            rows.append(row)
        return rows

    def get(self, run, suffix, viewer):
        source = self.cache[run['source_id']]
        if not suffix:
            value = slice_view(source['experiment'],run['current'],dict(state=run['state'],
                experiment_id=run['experiment_id'],key=self.key(run),created_at=run['created_at'],
                updated_at=run['ended_at'] or self.lobby.clock()))
            # A sparse experiment index need not carry the source cell key at its root.
            value = _replace(value,[(source['key'],self.key(run))])
            state = 'completed' if run['state']=='finished' else 'planned' if run['state']=='queued' else run['state']
            value.update(state=state, reason='simulated_'+run['state'], cancelled=run['state']=='cancelled',
                         created_at=run['created_at'], updated_at=run['ended_at'] or self.lobby.clock())
            for cell in value.get('cells',[]):
                cell.update(state=state, reason=value['reason'], cancelled=value['cancelled'],
                            versions=self.view(run,viewer).get('versions',[]), jobs=[])
            value = _clean(value)
            if not viewer or run['owner'] != viewer['id']:value = redact_for_other(value)
            return value,'application/json'
        match = re.fullmatch(r'/cells/([a-f0-9]{64})(?:/(.*))?',suffix)
        if not match or match[1] != self.key(run):raise LobbyError('not_found',404)
        name = match[2]
        if name in (None,'view','agent/view'):return self.view(run,viewer),'application/json'
        if not name.startswith(('agent/files/','files/')):raise LobbyError('not_found',404)
        if unquote(name) != name or any(p in ('','.','..') for p in name.split('/')):raise LobbyError('not_found',404)
        # Future-attempt URLs cannot bypass the clock. Only recorded replay/compat
        # assets are available to observers; text/code/help download paths are private.
        number = re.search(r'/(?:compat/)?v(\d+)(?:/|\.)',name)
        if number and int(number[1]) > run['current']:raise LobbyError('not_found',404)
        if name.endswith(('help.json','request-view.json')) and run['state'] not in TERMINAL:
            if not number or int(number[1]) >= run['current']:raise LobbyError('not_found',404)
        mine = bool(viewer and run['owner']==viewer['id'])
        if not mine and not (re.fullmatch(r'agent/files/(?:compat/v\d+/(?:bundle|trace|frames|camera_views)\.json|replays/e\d+/[a-f0-9]{64}/advisor_replay(?:_[a-z_]+)?\.mp4|executions/e\d+/frames(?:\.json|/[A-Za-z0-9_.-]+\.png))',name)
                             or re.fullmatch(r'files/[A-Za-z0-9_.-]+\.(?:mp4|png|jpg)',name)):
            raise LobbyError('private_artifact',403)
        # Non-version assets must already have a reference in the current cut.
        if not number:
            path_name = name.removeprefix('agent/files/').removeprefix('files/')
            visible = json.dumps(self.view(run,viewer))
            if path_name not in visible:raise LobbyError('not_found',404)
        return self._asset(run,name,viewer)
