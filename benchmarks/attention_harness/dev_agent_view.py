"""Read-only operator projection. Never used by Dev/Eval or as a file browser."""
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote
from .dev_agent_workspace import read_beneath
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


def _read(root,name,default=None):
    try:return json.loads(read_beneath(root,name,32*1024*1024))
    except FileNotFoundError:return default


def _public_name(name):
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
        raw=read_beneath(root/'ws',name,32*1024*1024)
        canonical=read_beneath(root/'private/public',name,32*1024*1024)
    except (OSError,ValueError):raise FileNotFoundError('Agent public evidence unavailable') from None
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


def projection(store,cell,*,api='/api/dev-v2',now=None):
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
    stopped=cell.get('state') not in ('running','planned') or (cell.get('state')=='planned' and bool(cell.get('reason')))
    end=run.get('paused_at') or (max((j.get('settled_at',0) for j in jobs),default=0) if stopped else now)
    wall=max(0,min(21600,21600-(run['deadline']-(end or now)) if run.get('deadline') else 0))
    if stopped and cell.get('agent_resources',{}).get('wall_remaining') is not None:
        wall=max(0,min(21600,21600-cell['agent_resources']['wall_remaining']))
    def error(source,value):
        if value not in (None,False,'',{},[]):errors.append(dict(source=source,error=clean(value)))
    error('controller',cell.get('error'))
    if quota.get('error'):error('quota',quota['error'])
    base=f'{api}/experiments/{cell["experiment_id"]}/cells/{key}/agent/files/'
    def links(names):
        result=[]
        for label,name in names:
            if not _public_name(name):continue
            try:read_beneath(public,name,32*1024*1024)
            except FileNotFoundError:
                result.append(dict(label=label+' (not yet available)',available=False));continue
            result.append(dict(label=label,available=True,url=base+quote(name,safe='/')))
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
                stderr=read_beneath(public,'executions/'+eid+'/stderr.txt',32*1024*1024).decode('utf-8')
                if stderr.strip():error(eid+' stderr',stderr)
            except FileNotFoundError:pass
            for event in _read(public,'executions/'+eid+'/trace.json',[]) or []:
                if isinstance(event,dict) and (event.get('error') or event.get('rejected') or event.get('status') in ('failed','rejected','timeout')):
                    error(eid+' action',{k:event[k] for k in ('status','rejected','error','reason','diagnostics') if k in event})
        if vid:
            names += [(label,'versions/'+vid+'/'+name) for label,name in [('code bundle','bundle.py'),('Eval verdict + advice','eval.json'),('help','help.json')]]
            current_help=_read(public,'versions/'+vid+'/help.json')
            if current_help is not None:result={**result,'help':current_help}
        elif op=='ask_help' and re.fullmatch(r'v\d+',str(result.get('version_id',''))):
            names.append(('help','versions/'+result['version_id']+'/help.json'))
        error(rid,response.get('validation_error'));error(rid,response.get('error'));error(rid,record.get('validation_error'));error(rid,result.get('outcome',{}).get('error'))
        error(rid,result.get('eval',{}).get('error'))
        if op=='ask_help' and result.get('status') not in (None,'delivered'):error(rid,result.get('reason') or result.get('error'))
        records.append(dict(id=rid,kind='formal_version' if op=='submit' else 'no_eval_execution' if op=='run' else 'help' if op=='ask_help' else 'probe',
            op=op,status=response['status'],at=job.get('reserved_at'),version_id=vid or (result.get('version_id') if op=='ask_help' else None),execution_id=eid,probe_id=pid,
            result=clean(result),error=clean(response.get('error')),links=links(names)))
    demo=_read(public,'events/demo.json')
    if demo is not None:
        job=next((j for j in jobs if j['id']=='help:demo'),{})
        records.append(dict(id='help:demo',kind='help',op='demo',status=demo.get('status'),at=job.get('reserved_at'),
            result=clean(demo),links=links([('help','events/demo.json')])))
    records.sort(key=lambda r:(r['at'] is None,r['at'] or 0,r['id']))
    # Controller error fields only: never return command/prompt/event/session logs.
    error_files=['settlement-error.json']+[str(p.relative_to(private)) for p in (private/'errors').glob('*.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'eval').glob('v*/error.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'help').glob('v*/error.json')]
    error_files += [str(p.relative_to(private)) for p in (private/'executions').glob('e*/controller-error.json')]
    for name in error_files:
        value=_read(private,name)
        if value:error(name,{k:value[k] for k in ('error','error_class','error_text') if k in value})
    for folder in ('turns','eval'):
        for directory in sorted((private/folder).glob('*')):
            if not directory.is_dir() or directory.is_symlink():continue
            prefix=folder+'/'+directory.name+'/'
            try:
                stderr=read_beneath(private,prefix+'stderr.txt',32*1024*1024).decode('utf-8',errors='replace')
                if stderr.strip():error(prefix+'stderr',stderr)
            except FileNotFoundError:pass
            try:
                raw=read_beneath(private,prefix+'events.jsonl',32*1024*1024)
                for line in raw.splitlines():
                    try:event=json.loads(line)
                    except ValueError:continue
                    if event.get('type') in ('error','turn.failed'):
                        error(prefix+'error',event.get('error') or event.get('message') or event)
            except FileNotFoundError:pass
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
    version_rows=[dict(v,**v.get('agent',{})) for v in cell.get('versions',[])]
    versions=[{k:v[k] for k in ('version','version_id','execution_id','valid','eval','eval_verdict','native_success','agree','agreement') if k in v} for v in version_rows]
    result=clean({k:cell.get(k) for k in ('key','experiment_id','state','reason','error','task_id','seed','policy_id','mode','budget','used','versions','jobs')})
    result.update(dev_mode='agent',versions=clean(versions),jobs=[],agent=dict(
        phase=phase,reason=clean(cell.get('reason')),fatal_cause=clean(cell.get('fatal_cause')),errors=errors,counts=used,held=held,limits=limits,
        remaining={k:limits[k]-used[k]-held[k] for k in limits},wall_seconds=wall,wall_limit=21600,
        quota=clean(quota),dev_model=selection.get('dev_model','gpt-6-astra'),eval_model=selection.get('eval_model','gpt-6-astra'),
        quota_stop_percent=selection.get('quota_stop_percent',90),timeline=records,versions=clean(versions),
        history=links([('version history','history/version_history.json')]),state_basis='reset_scene'))
    from .dev_agent_ui_projection import compat, error_groups
    result['agent']['failure_errors'], result['agent']['details'] = error_groups(errors, records)
    result['dev_v2_compat']=compat(cell,result['agent'],jobs)
    return result
