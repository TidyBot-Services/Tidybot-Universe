"""Read-only operator projection. Never used by Dev/Eval or as a file browser."""
import copy
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote
from .dev_agent_workspace import read_beneath, stat_beneath
from .dev_agent_ledger import LIMITS

# Preserve error wording except host paths/credentials. Relative evidence names stay readable.
HOST_PATH = re.compile(r'(?<![\w:])/(?:[^\s\"\'<>:,;\]\[()]+)|[A-Za-z]:\\[^\s\"\']+')
SECRET = re.compile(r'(?i)(Bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+')
DROP = {'uri','artifact_dir','manifest','ledger_path','owner','runtime_root','planner_deployment',
        'identity_lock','codex_identity','source_root','authorization'}


def clean(value):
    if isinstance(value,str):return SECRET.sub(r'\1[redacted]',HOST_PATH.sub('[host path]',value))
    if isinstance(value,list):return [clean(v) for v in value]
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in DROP}
    return value


def is_agent(value):
    return value.get('dev_mode')=='agent' or value.get('selection',{}).get('dev_mode')=='agent'


def display(value):
    return clean(value) if is_agent(value) else value


VIEW_LIMIT=32*1024*1024       # inlined into the view
DOWNLOAD_LIMIT=256*1024*1024  # one public file download; clean() needs the whole text (10-30: sol3 state logs 42 MB)


def _read(root,name,default=None):
    try:return json.loads(read_beneath(root,name,VIEW_LIMIT))
    except FileNotFoundError:return default


def _size(value):
    return f'{value/1e6:.1f} MB'


def _link(base,label,root,name):
    """Availability without reading: an unsafe or oversize file marks only its own link."""
    try:size=stat_beneath(root,name,shared=name.endswith('.png'))
    except FileNotFoundError:return dict(label=label+' (not yet available)',available=False)
    except (OSError,ValueError) as exc:
        return dict(label=label+' (unavailable: unsafe file)',available=False,reason=f'unsafe file ({exc})')
    if size>DOWNLOAD_LIMIT:
        return dict(label=f'{label} (too large to open: {_size(size)})',available=False,size_bytes=size,
                    reason=f'{_size(size)} exceeds the {_size(DOWNLOAD_LIMIT)} download limit')
    entry=dict(label=label,available=True,url=base+quote(name,safe='/'),size_bytes=size)
    if size>VIEW_LIMIT:entry.update(label=f'{label} ({_size(size)}, large)',large=True)
    return entry


def _public_name(name):
    if re.fullmatch(r'(?:events/forced-fallback\.json|history/help\.jsonl|executions/e\d+/(?:help|execution|evidence)\.json|versions/v\d+/(?:evidence|decision)\.json)',name):return True
    if re.fullmatch(r'executions/e\d+/(?:frames\.json|frames/(?:overview|side|top|left_wrist|right_wrist)-\d+-sim-\d+\.\d+s\.png)',name):return True
    return bool(re.fullmatch(r'(?:events/demo\.json|probes/p\d+\.json|rpc/responses/[A-Za-z0-9_-]{1,64}\.json|history/(?:versions|executions|probes)\.jsonl|history/version_history\.json|versions/v\d+/(?:policy\.py|bundle\.py|bundle-map\.json|submission\.json|eval\.json|help\.json|continue\.json|sources/[A-Za-z0-9_-]+\.py)|executions/e\d+/(?:policy\.py|stdout\.txt|stderr\.txt|trace\.json|state_log\.json|result\.json|initial_observation\.json|final_observation\.json|diagnostics/planner\.json))',name))


def artifact(cell,name):
    """Verified public copies and enumerated operator display evidence only."""
    if not is_agent(cell):raise FileNotFoundError('Not an agent public artifact')
    if name.startswith(('compat/','replays/')):
        from .dev_agent_ui_projection import evidence
        return evidence(cell,name)
    if not _public_name(name):raise FileNotFoundError('Not an agent public artifact')
    root=Path(cell['artifact_dir'])/'agent'
    try:
        linked=name.endswith('.png')
        raw=read_beneath(root/'ws',name,DOWNLOAD_LIMIT,shared=linked)
        canonical=read_beneath(root/'private/public',name,DOWNLOAD_LIMIT,shared=linked)
    except FileNotFoundError:raise FileNotFoundError('Agent public evidence unavailable') from None
    except (OSError,ValueError) as exc:raise FileNotFoundError(f'Agent public evidence unavailable: {exc}') from None
    if raw!=canonical:raise FileNotFoundError('Agent public evidence digest mismatch')
    if name.endswith('.png'):
        import hashlib
        folder,filename=name.split('/frames/',1)
        manifest=_read(root/'private/public',folder+'/frames.json',[])
        entry=next((r for r in manifest if r.get('filename')=='frames/'+filename),None)
        if not entry or hashlib.sha256(raw).hexdigest()!=entry.get('sha256'):
            raise FileNotFoundError('Agent frame digest mismatch')
        return raw,'image/png'
    text=raw.decode('utf-8')
    # Scrub any paths from text too. This endpoint cannot download private/raw logs.
    return (clean(json.loads(text)),'application/json') if name.endswith('.json') else (clean(text),'text/plain; charset=utf-8')


def _projection(store,cell,*,api='/api/dev-v2',now=None):
    if not is_agent(cell):raise ValueError('agent_view_requires_agent')
    now=time.time() if now is None else now
    key=cell['key'];root=Path(cell.get('artifact_dir','/missing'))/'agent';private=root/'private'
    public=private/'public';errors=[];jobs=[];run={};quota={}
    with sqlite3.connect('file:'+str(store.path.resolve())+'?mode=ro',uri=True) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'v2_agent_runs' in tables:
            row=db.execute('SELECT payload FROM v2_agent_runs WHERE cell=?',(key,)).fetchone()
            if row:run=json.loads(row[0])
            jobs=[dict(json.loads(p),id=i,state=s) for i,s,p in db.execute('SELECT id,state,payload FROM v2_agent_jobs WHERE cell=? ORDER BY rowid',(key,))]
            row=db.execute('SELECT payload FROM v2_agent_quota WHERE cell=? ORDER BY seq DESC LIMIT 1',(key,)).fetchone()
            if row:quota=json.loads(row[0])
        row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(cell['experiment_id'],)).fetchone()
        experiment=json.loads(row[0]);selection=experiment['selection']
    limits=run.get('limits',dict(LIMITS,help=selection.get('help_budget',1)))
    used={k:sum(j['resources'].get(k,0) for j in jobs if j['state'] not in ('reserved','released')) for k in limits}
    held={k:sum(j['resources'].get(k,0) for j in jobs if j['state']=='reserved') for k in limits}
    stopped=cell.get('state') not in ('running','planned') or (cell.get('state')=='planned' and bool(cell.get('reason')) and cell.get('reason')!='operator_paused')
    end=run.get('paused_at') or (max((j.get('settled_at',0) for j in jobs),default=0) if stopped else now)
    wall=max(0,min(21600,21600-(run['deadline']-(end or now)) if run.get('deadline') else 0))
    if stopped and cell.get('agent_resources',{}).get('wall_remaining') is not None:
        wall=max(0,min(21600,21600-cell['agent_resources']['wall_remaining']))
    def error(source,value):
        if value not in (None,False,'',{},[]):errors.append(dict(source=source,error=clean(value)))
    error('controller',cell.get('error'))
    error('controller result',(cell.get('result') or {}).get('error'))
    error('Memory staging',cell.get('agent_memory_staging_failure'))
    for failure in cell.get('memory_errors',[]):error('Memory',failure)
    if quota.get('error'):error('quota',quota['error'])
    base=f'{api}/experiments/{cell["experiment_id"]}/cells/{key}/agent/files/'
    def soft(root,name,default=None):
        # One unreadable evidence file is reported, never a failed view (10-30 sol3).
        try:return _read(root,name,default)
        except (OSError,ValueError) as exc:error(name,f'unreadable: {exc}');return default
    def links(names):
        result=[]
        for label,name in names:
            if not _public_name(name):continue
            entry=_link(base,label,public,name)
            if entry.get('reason') and not entry['available']:error(name,entry['reason'])
            result.append(entry)
        return result
    records=[]
    for path in sorted((private/'journal').glob('*.json')):
        record=_read(private,'journal/'+path.name)
        response=record['response'];op=response['op']
        if op in ('status','wait'):continue
        rid=response['request_id'];job=next((j for j in jobs if j['id']=='request:'+rid),{})
        result=response.get('result',{});vid=response.get('version_id');eid=response.get('execution_id');pid=response.get('probe_id')
        names=[('receipt','rpc/responses/'+rid+'.json')]
        if pid:names.append(('diagnostics','probes/'+pid+'.json'))
        if eid:
            names += [(label,'executions/'+eid+'/'+name) for label,name in
                      [('stdout','stdout.txt'),('stderr','stderr.txt'),('diagnostics','diagnostics/planner.json'),('code','policy.py'),('trace','trace.json'),('state log','state_log.json')]]
        if eid:
            try:
                stderr=read_beneath(public,'executions/'+eid+'/stderr.txt',VIEW_LIMIT).decode('utf-8')
                if stderr.strip():error(eid+' stderr',stderr)
            except FileNotFoundError:pass
            except (OSError,ValueError) as exc:error(eid+' stderr',f'unreadable: {exc}')
            for event in soft(public,'executions/'+eid+'/trace.json',[]) or []:
                if isinstance(event,dict) and (event.get('error') or event.get('rejected') or event.get('status') in ('failed','rejected','timeout')):
                    error(eid+' action',{k:event[k] for k in ('status','rejected','error','reason','diagnostics') if k in event})
        if vid:
            names += [(label,'versions/'+vid+'/'+name) for label,name in [('code bundle','bundle.py'),('Eval verdict + advice','eval.json'),('help','help.json')]]
            current_help=soft(public,'versions/'+vid+'/help.json')
            if current_help is not None:result={**result,'help':current_help}
        elif op=='ask_help' and re.fullmatch(r'v\d+',str(result.get('version_id',''))):
            names.append(('help','versions/'+result['version_id']+'/help.json'))
        error(rid,response.get('validation_error'));error(rid,response.get('error'));error(rid,record.get('validation_error'));error(rid,result.get('outcome',{}).get('error'))
        error(rid,result.get('eval',{}).get('error'))
        if op=='ask_help' and result.get('status') not in (None,'delivered'):error(rid,result.get('reason') or result.get('error'))
        records.append(dict(id=rid,kind='formal_version' if op=='submit' else 'no_eval_execution' if op=='run' else 'help' if op=='ask_help' else 'probe',
            op=op,status=response['status'],sequence=response.get('sequence'),at=job.get('reserved_at'),version_id=vid or result.get('version_id'),execution_id=eid or result.get('execution_id'),probe_id=pid,
            result=clean(result),error=clean(response.get('error')),links=links(names)))
    demo=soft(public,'events/demo.json')
    if demo is not None:
        job=next((j for j in jobs if j['id']=='help:demo'),{})
        records.append(dict(id='help:demo',kind='help',op='demo',status=demo.get('status'),at=job.get('reserved_at'),
            result=clean(demo),links=links([('help','events/demo.json')])))
    records.sort(key=lambda r:(r.get('sequence') is None,r.get('sequence') or 0,r['at'] is None,r['at'] or 0,r['id']))
    # Controller error fields only: never return command/prompt/event/session logs.
    error_files=['settlement-error.json']+[str(p.relative_to(private)) for p in (private/'errors').glob('*.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'eval').glob('v*/error.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'help').glob('[ve]*/error.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'executions').glob('e*/controller-error.json')]
    for name in error_files:
        value=soft(private,name)
        if value:error(name,{k:value[k] for k in ('error','error_class','error_text') if k in value})
    for folder in ('turns','eval'):
        for directory in sorted((private/folder).glob('*')):
            if not directory.is_dir() or directory.is_symlink():continue
            prefix=folder+'/'+directory.name+'/'
            try:
                stderr=read_beneath(private,prefix+'stderr.txt',VIEW_LIMIT).decode('utf-8',errors='replace')
                if stderr.strip():error(prefix+'stderr',stderr)
            except FileNotFoundError:pass
            except (OSError,ValueError) as exc:error(prefix+'stderr',f'unreadable: {exc}')
            try:
                raw=read_beneath(private,prefix+'events.jsonl',VIEW_LIMIT)
                for line in raw.splitlines():
                    try:event=json.loads(line)
                    except ValueError:continue
                    if event.get('type') in ('error','turn.failed'):
                        error(prefix+'error',event.get('error') or event.get('message') or event)
            except FileNotFoundError:pass
            except (OSError,ValueError) as exc:error(prefix+'events',f'unreadable: {exc}')
    saved=cell.get('agent_resources',{}).get('used')
    if stopped and saved is not None and saved!=used:
        error('accounting',dict(error='persisted_count_disagreement',ledger=used,persisted=saved))
    for job in jobs:
        receipt=job.get('receipt') or {}
        error(job['id'],receipt.get('error'))
    active=[j for j in jobs if j['state'] in ('claimed','reserved')]
    phase='stopped' if stopped else 'turn running' if any(j['kind']=='model_turn' for j in active) else 'planned'
    if not stopped:
        if any(j['resources'].get('moving_executions') for j in active):phase='waiting on execution'
        if any(j['kind']=='eval' for j in active):phase='Eval'
        if any(j['kind']=='help' for j in active):phase='help'
    # Advisor model: configured (10-29+), else the model recorded by the run's own Advisor calls,
    # else the advisor_proxy setting; runs before 10-29 used the fixed gpt-6-astra Advisor.
    recorded=[r.get('model') for r in (soft(private,str(p.relative_to(private))) for p in sorted((private/'help').glob('[ve]*/advisor.json')))
              if isinstance(r,dict) and isinstance(r.get('model'),str)]
    advisor_model=(selection.get('advisor_model') or (recorded[-1] if recorded else None)
                   or (selection.get('advisor_proxy') or {}).get('model')
                   or ('gpt-6-astra' if selection.get('model_source')=='codex' else None))
    version_rows=[dict(v,**v.get('agent',{})) for v in cell.get('versions',[])]
    versions=[{k:v[k] for k in ('version','version_id','execution_id','valid','eval','eval_verdict','native_success','agree','agreement') if k in v} for v in version_rows]
    result=clean({k:cell.get(k) for k in ('key','experiment_id','state','reason','error','task_id','seed','policy_id','mode','budget','used','versions','jobs')})
    result.update(dev_mode='agent',versions=clean(versions),jobs=[],agent=dict(
        phase=phase,reason=clean(cell.get('reason')),fatal_cause=clean(cell.get('fatal_cause')),errors=errors,counts=used,held=held,limits=limits,
        remaining={k:limits[k]-used[k]-held[k] for k in limits},wall_seconds=wall,wall_limit=21600,
        quota=clean(quota),dev_model=selection.get('dev_model','gpt-6-astra'),eval_model=selection.get('eval_model','gpt-6-astra'),
        advisor_model=advisor_model,
        quota_stop_percent=selection.get('quota_stop_percent',90),timeline=records,versions=clean(versions),
        history=links([('version history','history/version_history.json')]),state_basis='reset_scene'))
    if selection.get('agent_policy_version','agent-policy-v1')=='agent-policy-v2':
        attention=soft(private,'attention.json',{}) or {}
        result['agent'].update(agent_policy_version='agent-policy-v2',
            pending_ask=clean(attention.get('pending_ask')), active_help=clean(attention.get('active_help')),
            forced_fallback=clean(soft(public,'events/forced-fallback.json')),
            ask_required=[r for r in records if r.get('error')=='ask_required'])
    from .dev_agent_ui_projection import compat, error_groups
    result['agent']['failure_errors'], result['agent']['details'] = error_groups(errors, records)
    result['dev_v2_compat']=compat(cell,result['agent'],jobs)
    if experiment.get('pause_requested_at') or experiment.get('state')=='paused':
        from .daily_dev_v2 import DailyDevV2
        bridge=object.__new__(DailyDevV2);bridge.workers={};bridge.root=Path(experiment.get('disk_root') or experiment.get('artifact_dir','/missing'))
        result['dev_v2_compat']['run_controls']=bridge.run_controls(store,cell)
        result['dev_v2_compat']['lifecycle']=None
    # The UI needs only identity, status and links per timeline row; full probe
    # results (about 90% of the r6 payload) stay behind each row's links.
    result['agent']['timeline']=[r if r.get('kind')=='help' else {k:v for k,v in r.items() if k!='result'}|({'version_id':r['result']['version_id']}
        if isinstance(r.get('result'),dict) and r['result'].get('version_id') and not r.get('version_id') else {}) for r in records]
    for record in result['agent']['timeline']:
        record.pop('sequence', None)  # Ordering is projected; do not duplicate its bookkeeping.
    return result


_CACHE={}
TERMINAL=('completed','failed','cancelled','needs_review','stopped','condition_invalid','budget_exhausted','not_started')


def projection(store,cell,*,api='/api/dev-v2',now=None):
    """Terminal agent cells are immutable: reuse the projection while the ledger is unchanged."""
    if cell.get('state') not in TERMINAL or now is not None:return _projection(store,cell,api=api,now=now)
    stat=Path(store.path).stat()
    # Publishing is atomic rename, so any rewrite of display evidence changes a
    # directory mtime under these roots; include them all in the key.
    root=Path(cell.get('artifact_dir','/missing'))/'agent'
    dirs=[]
    for top in (root/'ws/versions',root/'ws/executions',root/'private/versions',root/'private/help',root/'private/public'):
        if top.is_dir():dirs+=[(str(d),d.stat().st_mtime_ns) for d in [top,*sorted(p for p in top.rglob('*') if p.is_dir())]]
    # Replay sidecars land in private/executions after settlement (backfill or the completion hook); their commit marker is part of the key.
    trims=tuple((str(p),p.stat().st_mtime_ns) for p in sorted((root/'private/executions').glob('e*/*/replay_trim/segments.json')))
    key=(cell['key'],api,stat.st_size,stat.st_mtime_ns,json.dumps(cell,sort_keys=True,default=str),tuple(dirs),trims)
    if key not in _CACHE:
        if len(_CACHE)>16:_CACHE.clear()
        _CACHE[key]=_projection(store,cell,api=api)
    return copy.deepcopy(_CACHE[key])
