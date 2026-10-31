"""Idle-only automatic checks. All external work is injected; missing settings means Off.

Adapters must make begin/start/finish idempotent by the supplied frozen IDs and
use the existing goal ledger. A claimed or unknown job is never redispatched.
"""
import argparse,fcntl,hashlib,importlib,json,math,os,sqlite3,time
from datetime import datetime
from pathlib import Path
from .codex_quota import current_window_readings,latest_quota_readings,valid_reset,WEEKLY_READING_MAX_AGE

QUOTA_MAX_AGE = WEEKLY_READING_MAX_AGE
UNEXPLAINED_RUN_MAX_AGE = 6 * 60 * 60
QUOTA_UNREADABLE = 'Automatic checks paused: Codex weekly use could not be read.'


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,sort_keys=True,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
        fd=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(name):os.unlink(name)


def settings(root):
    p=Path(root)/'autovalidate.json'
    if not p.exists():
        runtime=Path(root)/'runtime.json'
        enabled=json.loads(runtime.read_bytes()).get('enabled',False) if runtime.exists() else False
        if type(enabled) is not bool:raise ValueError('invalid automatic checks switch')
        return {'enabled':enabled}
    value=json.loads(p.read_bytes())
    if not isinstance(value,dict) or set(value)-{'enabled','cross_seed','family_checks','daily_run_limit','weekly_quota_stop_percent','daily_quota_rise_stop_points'} or type(value.get('enabled')) is not bool:raise ValueError('invalid automatic checks settings')
    validate_daily_run_limit(value.get('daily_run_limit',20))
    validate_quota_setting('weekly_quota_stop_percent',value.get('weekly_quota_stop_percent',70))
    validate_quota_setting('daily_quota_rise_stop_points',value.get('daily_quota_rise_stop_points',5))
    if 'family_checks' in value:
        family=value['family_checks']
        if not isinstance(family,dict) or set(family)!={'enabled'} or type(family['enabled']) is not bool:raise ValueError('invalid family checks settings')
    if 'cross_seed' in value:
        cross=value['cross_seed']
        if not isinstance(cross,dict) or set(cross)-{'enabled','candidate_seeds','contracts'} or type(cross.get('enabled',False)) is not bool:raise ValueError('invalid cross-seed settings')
        seeds=cross.get('candidate_seeds',[]);contracts=cross.get('contracts',{})
        if not isinstance(seeds,list) or any(type(s) is not int for s in seeds) or len(set(seeds))!=len(seeds):raise ValueError('unique integer candidate seeds required')
        if not isinstance(contracts,dict) or any(not isinstance(k,str) or not k.isdecimal() or not isinstance(p,str) or not Path(p).is_absolute() for k,p in contracts.items()):raise ValueError('absolute per-seed contract paths required')
    return value


def set_enabled(root,enabled):
    if type(enabled) is not bool:raise ValueError('enabled must be boolean')
    value=settings(root);value['enabled']=enabled
    atomic(Path(root)/'autovalidate.json',value)


def validate_daily_run_limit(limit):
    if limit is not None and (type(limit) is not int or limit<=0):
        raise ValueError('daily_run_limit must be a positive integer or null')


def set_daily_run_limit(root,limit):
    validate_daily_run_limit(limit)
    value=settings(root);value['daily_run_limit']=limit
    atomic(Path(root)/'autovalidate.json',value)


def daily_run_stop(state,root,needed=1):
    """One admission rule for origins, individual arms and complete batches."""
    limit=settings(root).get('daily_run_limit',20)
    if limit is None or state.get('runs_today',0)+needed<=limit:return None
    if needed==1:return f'Automatic checks done for today ({limit} runs).'
    return f'Automatic checks waiting until tomorrow: fewer than {needed} runs remain for the whole batch.'


def parse_daily_run_limit(value):
    if value=='none':return None
    try:
        limit=int(value);validate_daily_run_limit(limit)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected none or a positive integer') from exc
    return limit


def validate_quota_setting(key,limit):
    weekly=key=='weekly_quota_stop_percent'
    if key not in {'weekly_quota_stop_percent','daily_quota_rise_stop_points'}:raise ValueError('unknown quota setting')
    if not weekly and limit is None:return
    if (type(limit) not in (int,float) or limit<=0 or
        (type(limit) is float and not math.isfinite(limit)) or (weekly and limit>100)):
        rule='a number in (0, 100]' if weekly else 'a positive number or null'
        raise ValueError(f'{key} must be {rule}')


def set_quota_setting(root,key,limit):
    validate_quota_setting(key,limit)
    value=settings(root);value[key]=limit
    atomic(Path(root)/'autovalidate.json',value)


def parse_quota_setting(value,*,key):
    try:
        limit=None if value=='none' else float(value)
        validate_quota_setting(key,limit)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return limit


def weekly_quota_snapshot(home=None,*,now=None):
    """Use the newest weekly observations, allowing ten minutes of session lag.

    File mtime only selects recent sessions; it cannot prove a reading is fresh.
    Ephemeral calls and unrelated events must not advance the observation time.
    """
    root=Path(home).expanduser() if home is not None else Path.home()
    now=time.time() if now is None else now
    cutoff=now-86400;readings=[]
    for path in root.joinpath('.codex','sessions').rglob('*.jsonl'):
        try:
            if path.stat().st_mtime<cutoff:continue
            latest=None;stamp=None
            with path.open() as stream:
                for line in stream:
                    try:
                        row=json.loads(line);limits=row.get('payload',{}).get('rate_limits') or row.get('rate_limits')
                        if isinstance(limits,dict):latest=limits;stamp=row.get('timestamp')
                    except (ValueError,TypeError,AttributeError):continue
            for key in ('primary','secondary'):
                window=(latest or {}).get(key)
                if not isinstance(window,dict) or window.get('window_minutes')!=10080:continue
                used=window.get('used_percent')
                if valid_percent(used):
                    reading=dict(used_percent=used,observed_at=None,resets_at=window.get('resets_at'))
                    try:
                        observed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                        if observed.tzinfo is not None and observed.timestamp()<=now:reading['observed_at']=observed.timestamp()
                    except (ValueError,TypeError,AttributeError,OverflowError):pass
                    readings.append(reading)
        except (OSError,UnicodeError):continue
    if not readings:raise ValueError('Codex weekly quota is unreadable')
    # Without a fresh reading, retain a stale snapshot so the existing refresh
    # gate can explain the pause. Never let a stale window freshen another one.
    readings=latest_quota_readings(current_window_readings(readings,now=now))
    stamps=[r['observed_at'] for r in readings if r['observed_at'] is not None]
    return dict(used_percent=max(r['used_percent'] for r in readings),
                observed_at=max(stamps) if stamps else None,
                resets_at=readings[0]['resets_at'] if valid_reset(readings[0]['resets_at']) else None)


def weekly_quota(home=None,*,now=None):
    """Compatibility percentage projection; worker gates require the snapshot."""
    return weekly_quota_snapshot(home,now=now)['used_percent']


def refresh_quota(*,home=None,command=None,timeout=60):
    """One bounded, non-ephemeral call. Inject argv (or deps.refresh_quota) in tests."""
    import subprocess,tempfile
    command=command if command is not None else [
        'codex','exec','-m','gpt-6-astra','-s','read-only','--skip-git-repo-check',
        'Reply with the single word OK.']
    env=os.environ.copy()
    # Match the session directory read by weekly_quota_snapshot, even when the
    # worker inherited a different CODEX_HOME from its launcher.
    root=Path(home).expanduser() if home is not None else Path.home()
    env['CODEX_HOME']=str(root/'.codex')
    with tempfile.TemporaryDirectory(prefix='codex-quota-') as scratch:
        subprocess.run(command,cwd=scratch,env=env,stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout,check=True)


def valid_percent(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=100


def quota_fresh(stamp,now):
    return type(stamp) in (int,float) and math.isfinite(stamp) and 0<=now-stamp<=QUOTA_MAX_AGE


def quota_stop(state,deps,save,*,allow_refresh=True):
    """Refresh stale observations once, then apply the configured quota stops."""
    config=settings(deps.root)
    def read():
        try:
            value=deps.quota()
            if not isinstance(value,dict) or not valid_percent(value.get('used_percent')):return None
            quota=value['used_percent']
            reset=value.get('resets_at')
            if state.get('day_start_quota') is None or (valid_reset(reset) and reset!=state.get('day_start_quota_resets_at')):
                state.update(day_start_quota=quota,day_start_quota_resets_at=reset)
            stamp=value.get('observed_at')
            state.update(weekly_quota=quota,weekly_quota_at=stamp if type(stamp) in (int,float) and math.isfinite(stamp) else None,
                         weekly_quota_resets_at=reset)
            return value
        except Exception:return None
    value=read()
    if value is None or not quota_fresh(value.get('observed_at'),deps.clock()):
        if not allow_refresh:return QUOTA_UNREADABLE
        attempt=dict(at=deps.clock(),before_percent=value['used_percent'] if value else None,after_percent=None,status='refreshing')
        state.setdefault('quota_refreshes',[]).append(attempt);save()
        failed=False
        try:deps.refresh_quota()
        except Exception:failed=True
        value=read()
        attempt['after_percent']=value['used_percent'] if value else None
        if failed or value is None or not quota_fresh(value.get('observed_at'),deps.clock()):
            attempt.update(status='failed',finished_at=deps.clock());save();return QUOTA_UNREADABLE
        attempt.update(status='refreshed',finished_at=deps.clock());save()
    quota=value['used_percent']
    weekly=config.get('weekly_quota_stop_percent',70)
    daily=config.get('daily_quota_rise_stop_points',5)
    if quota>=weekly:return f'Automatic checks paused: Codex weekly use is at {quota:g}% (stop at {weekly:g}%).'
    if daily is not None and quota-state['day_start_quota']>daily:return f'Automatic checks paused: Codex weekly use rose by more than {daily:g} points today.'
    return None


class QuotaStop(Exception):
    pass


def check_dispatch_quota(state,deps,save):
    current=state.get('current') or {}
    if current.get('dev_mode')=='agent':
        from .memory_validation_disk import gate
        reason=gate(state,deps,current)
        if reason:raise QuotaStop(reason)
    # Provisioning or importing can take time after tick's initial gate.
    if (state.get('current') or {}).get('dev_mode')=='agent' or not quota_fresh(state.get('weekly_quota_at'),deps.clock()):
        reason=quota_stop(state,deps,save)
        if reason:raise QuotaStop(reason)


def scheduler_status(experiment,ledger):
    """True/False for verified live/dead receipts, None for unexplained state."""
    from .dev_loop import owner_alive
    # Some older ledgers use a neutral directory name for their sole experiment.
    directories={ledger.parent,ledger.parent/experiment['experiment_id']}
    if experiment.get('artifact_dir'):directories.add(Path(experiment['artifact_dir']))
    found=False;unreadable=False
    for directory in sorted(directories):
        try:
            receipts=[p for p in directory.iterdir() if p.match('scheduler-process-*.json')]
        except FileNotFoundError:continue
        except OSError:unreadable=True;continue
        for receipt in receipts:
            try:
                identity=json.loads(receipt.read_bytes())
                if (not isinstance(identity,dict) or set(identity)!={'pid','start_ticks','boot_id'}
                    or type(identity['pid']) is not int or identity['pid']<=0
                    or not all(isinstance(identity[k],str) and identity[k] for k in ('start_ticks','boot_id'))):
                    raise ValueError('invalid scheduler identity')
                found=True
                if owner_alive(identity):return True
            except (OSError,ValueError,TypeError):unreadable=True
    return None if unreadable or not found else False


def run_activity(ledgers,*,own_experiment=None,owned_experiments=None,own_pids=(),ignored_stale_runs=None,now=None):
    """Read-only probe: dead schedulers do not make abandoned jobs active."""
    now=time.time() if now is None else now
    reason=None
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in own_pids:continue
        try:args=(p/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError):continue
        if any(b'daily_dev_v2' in a for a in args) and b'--worker' in args:
            index=args.index(b'--worker')+1
            name=args[index].decode(errors='replace') if index<len(args) and args[index] else f'worker PID {p.name}'
            reason=reason or 'Another run is active: '+name
    for path in ledgers:
        path=Path(path)
        try:
            # WAL writes need not update the database file's mtime.
            modified=path.stat().st_mtime
            try:modified=max(modified,path.with_name(path.name+'-wal').stat().st_mtime)
            except FileNotFoundError:pass
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
                experiments={}
                for raw, in db.execute('SELECT payload FROM v2_experiments'):
                    e=json.loads(raw);experiments[e['experiment_id']]=e
                unresolved=set()
                for raw, in db.execute("SELECT payload FROM v2_jobs WHERE state IN ('claimed','needs_review')"):
                    job=json.loads(raw)
                    row=db.execute('SELECT payload FROM v2_cells WHERE id=?',(job['cell'],)).fetchone()
                    eid=json.loads(row[0]).get('experiment_id') if row else None
                    if eid not in experiments:raise ValueError('unresolved job has no experiment: '+str(eid or job['cell']))
                    unresolved.add(eid)
                for eid,e in experiments.items():
                    if eid==own_experiment:continue
                    selection=e.get('selection',{})
                    # Family templates have no validation marker. Only the
                    # durable ownership record can exempt their exact Dev key.
                    request_key=selection.get('request_key')
                    if (isinstance(request_key,str) and request_key.startswith('family-template-')
                        and (owned_experiments or {}).get(request_key)==eid
                        and selection.get('budget_tier')=='dev'
                        and selection.get('mode')=='diagnostic'):continue
                    if e.get('validation_experiment') is True and selection.get('budget_tier')=='dev' and any(
                        eid==owned and selection.get('request_key') in ('validation-'+operation,'cross-'+operation)
                        for operation,owned in (owned_experiments or {}).items()):continue
                    tier=selection.get('budget_tier','legacy')
                    # Use the same comparison mode as require_scene_gate; a
                    # legacy tier alone does not identify the frozen matrix.
                    if selection.get('mode')=='experiment' and tier!='dev' and e['state'] in {'running','planned','claimed'}:
                        reason=reason or 'The frozen main matrix is planned or running: '+eid
                        continue
                    alive=scheduler_status(e,path)
                    unexplained=alive is None and now-modified<UNEXPLAINED_RUN_MAX_AGE
                    pending=eid in unresolved or e['state']=='needs_review'
                    if alive or unexplained and (pending or e['state'] in {'running','claimed','planned'}):
                        message=('Another run has unresolved work: ' if pending else 'Another run is active: ')+eid
                        started=e.get('started_at') or e.get('created_at')
                        if started:message+=f' (started {started})'
                        reason=reason or message
                    elif pending and ignored_stale_runs is not None:
                        entry=dict(id=eid,reason='scheduler not running')
                        if entry not in ignored_stale_runs:ignored_stale_runs.append(entry)
                    elif e['state']=='planned' and not e.get('started_at') and alive is None and ignored_stale_runs is not None:
                        entry=dict(id=eid,reason='planned, never started')
                        if entry not in ignored_stale_runs:ignored_stale_runs.append(entry)
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError) as exc:
            raise ValueError(f'Run activity ledger unreadable: {path.parent.name}/{path.name}: {exc}') from exc
    return reason


def contract_catalog(context):
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    ref=registered_contract_ref();p=Path(ref['uri'])
    return dict(role='contract',root=str(p.parent),context=context,files={p.name:ref['sha256']})


def freeze_plan(memory_id,origin,config,context,signature,pairs,*,policy_id,budget,dev_model):
    from attention_memory_service.dev_v2 import SCOPE
    if len(pairs)!=5 or dev_model['provider']!='codex':raise ValueError('five Codex pairs required')
    return dict(schema_version='attentionbench.memory-validation-plan.v2',profile='single_scene_v1',memory_id=memory_id,
        **{k:context[k] for k in SCOPE if k not in ('scene_id','object_set_id','camera_config_id','task_variant_id')},
        **({k:context[k] for k in ('execution_profile','planner_dev_local_sha256',) if k in context}),
        policy_id=policy_id,seeds=[config['seed']],cases=[dict(seed=config['seed'],**{k:context[k] for k in ('scene_id','object_set_id','camera_config_id','task_variant_id','camera_names','task_prompt')})],
        pairs=pairs,failure_signature=signature,assistance_credits=budget['assistance_credits'],arm_generation='dev_from_origin',
        origin_source_sha256=origin['sha256'],validation_config_sha256=config['sha256'],dev_model=dev_model)


def prepare_origin(state,deps,note,save,status,*,cross_seed=False):
    """Reserve one replay run before dispatch, through the ordinary worker gates."""
    try:needed=getattr(deps.runner,'origin_needed',lambda note:None)(note)
    except Exception as exc:
        if note.get('channel')!='dev_agent':raise
        from .notices import safe_detail
        message='Agent origin preparation failed: '+safe_detail(str(exc) or type(exc).__name__)
        state.setdefault('note_errors',{})[note['memory_id']]=dict(reason=message,title=note.get('title'),memory_id=note['memory_id'])
        state.pop('pending',None);save()
        return status(message)
    if needed is None:return None
    if note.get('channel')=='dev_agent':needed['channel']='dev_agent'
    key=needed['origin_key']
    if key in state.get('origin_errors',{}):return status(state['origin_errors'][key]['reason'])
    if key in state.get('origin_failures',{}):return status(state['origin_failures'][key]['reason'])
    if needed.get('receipt_ready'):
        needed['cross_seed']=cross_seed;state['current']=needed;save()
        return advance_origin(state,deps,None,save,status)
    stop=daily_run_stop(state,deps.root)
    if stop:return status(stop)
    counted=state.setdefault('counted',{})
    if key not in counted:counted[key]=state['day'];state['runs_today']+=1
    needed.update(dispatch_pending=True,cross_seed=cross_seed);state['current']=needed;save()
    check_dispatch_quota(state,deps,save)
    deps.runner.start(needed,key);needed['dispatch_pending']=False;save()
    return status('Preparing origin evidence: '+needed['title'])


def advance_origin(state,deps,reason,save,status):
    if any(e.get('status')=='archiving' for e in state.get('origin_interruptions',[])):
        return status('Interrupted origin abandonment is incomplete; rerun --abandon-interrupted-origin with --reason to finish recovery.')
    current=state['current']
    if current.get('cross_seed') and not settings(deps.root).get('cross_seed',{}).get('enabled',False):reason=reason or 'Cross-seed checks are Off'
    if current.get('dispatch_pending'):
        if reason:return status(reason)
        check_dispatch_quota(state,deps,save)
        deps.runner.start(current,current['origin_key']);current['dispatch_pending']=False;save()
        return status('Preparing origin evidence: '+current['title'])
    phase=deps.runner.poll(current)
    if phase=='completed':
        result=deps.runner.finish(current)
        if result['status']=='error':
            state.setdefault('origin_errors',{})[current['origin_key']]=dict(result,memory_id=current['memory_id'],title=current['title'])
            if current.get('channel')=='dev_agent':
                state.setdefault('note_errors',{})[current['memory_id']]=dict(reason=result['reason'],memory_id=current['memory_id'],title=current['title'])
                state.pop('pending',None)
        elif result['status']!='passed':
            state.setdefault('origin_failures',{})[current['origin_key']]=result
            state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status='inconclusive',note_remains='trusted' if current.get('cross_seed') else 'provisional',decision=dict(decision='inconclusive',reason=result['reason']),origin_failure=result))
            state.pop('pending',None);state.pop('cross_pending',None)
        if result['status']!='passed' and current.get('channel')=='dev_agent':check_error(state,current,result['reason'])
        state['current']=None;save();return status(result['reason'])
    if reason:
        if not current.get('pause_requested'):deps.runner.pause(current);current['pause_requested']=True;save()
        return status(reason)
    if current.get('pause_requested'):
        check_dispatch_quota(state,deps,save)
        deps.runner.resume(current);current['pause_requested']=False;save()
    if phase=='pending':
        check_dispatch_quota(state,deps,save)
        deps.runner.start(current,current['origin_key'])
    return status('Preparing origin evidence: '+current['title'])


def begin_item(pending):
    return dict(kind='begin',note=pending['note'],operation=pending['operation'],
                memory_id=pending['note']['memory_id'],title=pending['note'].get('title') or 'Untitled note')


def check_error(state,current,reason):
    from .notices import safe_detail
    note=current.get('note') or {}
    entry=dict(memory_id=current.get('note_id') or current.get('memory_id') or note.get('memory_id'),reason=safe_detail(reason))
    errors=state.setdefault('check_errors',[])
    if not errors or errors[-1]!=entry:errors.append(entry)
    state['check_errors']=errors[-100:]


def begin_current(state,deps,save):
    current=state['current']
    # Persist the attempt before external effects. A process restart or an
    # explicit request grants one new attempt; ordinary polling grants none.
    current['begin_cycle']=deps.begin_cycle;save()
    batch=deps.runner.begin(current['note'],current['operation'])
    batch.update(pair_index=0,arm='control',active=False,title=current['title'],operation=current['operation'])
    if current['note'].get('channel')=='dev_agent':batch['note_id']=current['note']['memory_id']
    state['current']=batch;state.pop('pending',None);save()
    return batch


def discard_pair(state,current,result):
    """Persist both arms' exclusion before any replacement can be dispatched."""
    from .memory_validation_replacements import attempt_version,MAX_REPLACEMENTS
    pair=current['plan']['pairs'][current['pair_index']]
    entry=dict(pair_id=pair['pair_id'],pair=current['pair_index']+1,version=attempt_version(current),
               arm=current['arm'],arm_id=current['arm_id'],status='invalid_generation',reason=result['reason'],
               cells={a:pair[a+'_cell'] for a in ('control','treatment')},
               arms={a:current.get('trials',{}).get(pair[a+'_cell']) for a in ('control','treatment')})
    entry['arms'][current['arm']]=result
    current.setdefault('discarded_pairs',[]).append(entry)
    for arm in ('control','treatment'):current.setdefault('trials',{}).pop(pair[arm+'_cell'],None)
    current.update(active=False,pause_requested=False,dispatch_pending=False)
    count=len(current['discarded_pairs'])
    message=f"Pair {entry['pair']} discarded: {entry['arm']} produced invalid source ({entry['reason']})"
    if count>MAX_REPLACEMENTS:
        reason='Could not finish: 5 valid pairs could not be obtained after 2 replacement pairs.'
        state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],
            status='inconclusive',note_remains='provisional',decision=dict(decision='inconclusive',reason=reason),
            discarded_pairs=current['discarded_pairs']))
        state['current']=None
        return message+'; '+reason
    current.update(pair_version=entry['version']+1,arm='control',replacement_notice=message+f'; running replacement {count} of 2')
    return current['replacement_notice']


def evaluation_ready(current):
    return bool(current and current.get('kind') not in {'begin','origin','cross_seed'}
                and not current.get('active') and current.get('pair_index')==5)


def evaluation_key(current):
    # Bind retries to the frozen batch, including legacy records without operation.
    return hashlib.sha256(json.dumps([current['memory_id'],current['plan']],sort_keys=True).encode()).hexdigest()


def request_current_retry(root):
    """Mailbox only: works with a running/Off worker, never enables dispatch."""
    import uuid
    root=Path(root);state=json.loads((root/'state.json').read_bytes())
    if state.get('family_active'):
        state=state.get('family_checks',{}).get(state['family_active'],{}).get('worker',{})
    active=state.get('positive_control_active')
    if active:state=state.get('positive_controls',{}).get(active,{}).get('worker',{})
    current=state.get('current')
    cross=state.get('cross_pending') or state.get('cross_waiting')
    if cross and cross.get('begin_cycle'):
        atomic(root/'retry-current.json',dict(operation=cross['operation'],request_id=uuid.uuid4().hex,cross=True))
        return 'Current family batch retry requested; switches remain unchanged.'
    from .memory_validation_replacements import legacy_invalid_available,attempt_version
    if legacy_invalid_available(current):
        atomic(root/'retry-current.json',dict(arm_id=current['arm_id'],version=attempt_version(current),evaluation_key=evaluation_key(current),request_id=uuid.uuid4().hex))
        return 'Current invalid-generation recovery requested; automatic checks must be On to resume.'
    if evaluation_ready(current):
        atomic(root/'retry-current.json',dict(evaluation_key=evaluation_key(current),request_id=uuid.uuid4().hex))
        return 'Current batch retry requested; automatic checks must be On to resume.'
    item=current if current and current.get('kind')=='begin' else state.get('pending') if not current else None
    if not item:raise ValueError('No interrupted begin or completed batch evaluation is available to retry')
    atomic(root/'retry-current.json',dict(operation=item['operation'],request_id=uuid.uuid4().hex))
    return 'Current batch retry requested; automatic checks must be On to resume.'


def tick(state,deps):
    from .memory_family import tick_channel as family_tick
    try:
        if family_tick(state,deps,_tick):return state
    except Exception as exc:
        from .notices import safe_detail
        state['status']='Family check paused: '+safe_detail(str(exc) or type(exc).__name__)
        deps.save(state);return state
    from .memory_positive_control import tick_channel
    try:
        if tick_channel(state,deps,_tick):return state
    except Exception as exc:
        from .notices import safe_detail
        state['status']='Positive-control check paused: '+safe_detail(str(exc) or type(exc).__name__)
        deps.save(state);return state
    return _tick(state,deps)


def _tick(state,deps):
    """One durable transition. deps includes quota snapshots and refresh_quota.

    runner.begin(note, id) returns a frozen current record with candidate/plan.
    runner.start(current, arm_id) launches/reconciles exactly one ledger arm;
    poll returns running/paused/completed, finish seals/imports that arm. Only a
    proven invalid pair permits the bounded C17 supplemental authorization.
    Unknown outcomes raise and remain visible.
    """
    def save():deps.save(state)
    def status(text):
        item=state.get('current') or state.get('pending') or current or {}
        note=item.get('note') or {}
        mid=item.get('note_id') or item.get('memory_id') or note.get('memory_id')
        agent=item.get('dev_mode')=='agent' or item.get('channel')=='dev_agent' or note.get('channel')=='dev_agent'
        gate_pause=text.startswith(('Automatic checks paused','Another run','The frozen main matrix',
            'Automatic checks done for today','Automatic checks waiting until tomorrow'))
        if agent and state.get('current'):
            if gate_pause:item['gate_pause_reason']=text
            else:item.pop('gate_pause_reason',None)
        if agent and gate_pause:check_error(state,item,text)
        if item.get('dev_mode')=='agent' and item.get('active') and not item.get('dispatch_pending'):
            from .memory_validation_disk import progress
            try:progress(state,deps,item)
            except Exception as exc:
                from .notices import safe_detail
                error=dict(memory_id=mid,reason='Disk measurement failed: '+safe_detail(str(exc)))
                if error not in state.setdefault('check_errors',[]):state['check_errors'].append(error)
        if state.get('status')!=text:
            state.setdefault('notices',[]).append(dict(title=text,at=deps.clock()))
            state['notices']=state['notices'][-100:]
        state['status']=text;save();return state
    current=state.get('current')
    try:
        if any(e.get('status')=='retrying' for e in state.get('origin_registration_retries',[])):
            return status('Origin registration retry is incomplete; rerun --retry-origin-registration with --reason to finish recovery.')
        if not getattr(deps,'begin_cycle',None):
            import uuid
            deps.begin_cycle=uuid.uuid4().hex
        request_path=Path(deps.root)/'retry-current.json'
        request=json.loads(request_path.read_bytes()) if request_path.exists() else None
        if request and request['request_id']!=state.get('retry_request_id'):
            pending=state.get('pending')
            if request.get('cross'):
                cross=state.get('cross_pending') or state.get('cross_waiting')
                if cross and cross['operation']==request['operation']:deps.begin_cycle=request['request_id']
            if request.get('arm_id') and current and current.get('arm_id')==request['arm_id'] and current.get('pair_version',1)==request.get('version') and evaluation_key(current)==request.get('evaluation_key'):
                current['recover_invalid_requested']=True
            elif request.get('evaluation_key'):
                if evaluation_ready(current) and evaluation_key(current)==request['evaluation_key']:
                    current['evaluate_cycle']=request['request_id']
            elif current is None and pending and pending['operation']==request['operation']:
                from .memory_validation_ownership import recover_pending
                if recover_pending(deps.root,pending):
                    current=state['current']=begin_item(pending)
            if current and current.get('kind')=='begin' and current['operation']==request.get('operation'):
                deps.begin_cycle=request['request_id']
            state['retry_request_id']=request['request_id'];save()
        enabled=settings(deps.root)['enabled'];state['enabled']=enabled
        if not enabled:
            if current and current.get('active') and not current.get('dispatch_pending') and not current.get('pause_requested'):
                deps.runner.pause(current);current['pause_requested']=True
            return status('Off')
        now=deps.clock();day=datetime.fromtimestamp(now).astimezone().date().isoformat()
        if state.get('day')!=day:state.update(day=day,runs_today=0,day_start_quota=None)
        try:reason=deps.activity(current)
        except Exception as exc:
            from .notices import safe_detail
            reason='Automatic checks paused: '+safe_detail(str(exc) or type(exc).__name__)
        for entry in getattr(deps.activity,'ignored_stale_runs',[]):
            ignored=state.setdefault('ignored_stale_runs',[])
            if entry not in ignored:ignored.append(entry)
        quota_reason=quota_stop(state,deps,save,allow_refresh=not reason)
        reason=reason or quota_reason
        if not (current and current.get('active')):reason=reason or daily_run_stop(state,deps.root)
        if current and current.get('kind')=='origin':
            return advance_origin(state,deps,reason,save,status)
        if current and current.get('kind')=='cross_seed':
            from .memory_cross_seed_worker import advance
            return advance(state,deps,reason,save,status)
        if current and current.get('active'):
            if current.get('dispatch_pending'):
                if reason:return status(reason)
                check_dispatch_quota(state,deps,save)
                deps.runner.start(current,current['arm_id']);current['dispatch_pending']=False;save()
                return status(current.get('replacement_notice') or f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
            if current.get('recover_invalid_requested'):
                if deps.runner.recover_invalid(current):
                    from .memory_validation_replacements import attempt_id
                    deps.runner.futures.pop(attempt_id(current),None)
                current.pop('recover_invalid_requested',None);save()
            phase=deps.runner.poll(current)
            if phase=='pending':
                if reason:return status(reason)
                check_dispatch_quota(state,deps,save)
                deps.runner.start(current,current['arm_id'])
                return status('Automatic checks resuming')
            if phase=='completed':
                # Finish/import before moving the cursor. Retrying uses immutable IDs.
                if current.get('dev_mode')=='agent':
                    from .notices import safe_detail
                    error=getattr(deps.runner,'arm_error',lambda c:None)(current)
                    if error:
                        check_error(state,current,error)
                        save()
                    from .memory_validation_disk import progress
                    progress(state,deps,current,finished=True)
                result=deps.runner.finish(current)
                if current.get('dev_mode')=='agent':current['last_verdict']=result.get('reason') or result.get('status')
                if result.get('status')=='invalid_generation':
                    message=discard_pair(state,current,result);save();return status(message)
                if result.get('status')=='refused':
                    detail=': '+str(result.get('reason') or 'reason unavailable') if current.get('dev_mode')=='agent' else ''
                    raise ValueError('execution evidence refused; review required'+detail)
                if result.get('status')=='failed':
                    # Close using the original sealed verdict; failed arms are never trials.
                    try:decision=deps.client.record_dev_v2_failed_check(**result['failed_check'])
                    except Exception as exc:
                        from .notices import safe_detail
                        gap=dict(memory_id=current['memory_id'],title=current['title'],validation=result,reason=safe_detail(str(exc)),at=deps.clock())
                        state.setdefault('service_gaps',[]).append(gap);state['current']=None;save()
                        return status('Automatic check failed: '+current['title']+'; service decision unavailable: '+gap['reason'])
                    outcome=decision['decision']
                    if current.get('dev_mode')=='agent':check_error(state,current,decision['reason'])
                    state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status=outcome,decision=decision,validation_failure=result,**({'discarded_pairs':current['discarded_pairs']} if current.get('discarded_pairs') else {})))
                    message=('Note rejected after check: '+current['title'] if outcome=='rejected' else decision['reason']+' — '+current['title'])
                    state['current']=None;save();return status(message)
                current.setdefault('trials',{})[current['arm_id']]=result
                current['active']=False;current['pause_requested']=False
                if current['arm']=='treatment':
                    pair=current['plan']['pairs'][current['pair_index']]
                    deps.client.record_dev_v2_pair(memory_id=current['memory_id'],control_import_id=current['trials'][pair['control_cell']]['trial_import_id'],treatment_import_id=result['trial_import_id'])
                    current['pair_index']+=1;current['arm']='control';current['pair_version']=1;current.pop('replacement_notice',None)
                else:current['arm']='treatment'
                save()
            elif reason or current.get('pause_requested'):
                if reason:
                    if not current.get('pause_requested'):deps.runner.pause(current);current['pause_requested']=True
                    return status(reason)
                if phase=='paused':
                    check_dispatch_quota(state,deps,save)
                    deps.runner.resume(current);current['pause_requested']=False
                return status('Automatic checks resuming' if phase=='paused' else 'Automatic checks waiting for pause')
            else:return status(current.get('replacement_notice') or f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
        if reason:return status(reason)
        if current and current.get('kind')=='begin':
            if current.get('begin_cycle')==deps.begin_cycle:
                return status(current.get('begin_error') or 'Automatic checks paused: interrupted begin; restart or use --retry-current.')
            stop=daily_run_stop(state,deps.root,needed=10)
            if stop:return status(stop)
            if current['note'].get('channel')=='dev_agent':
                from .memory_validation_disk import gate
                stop=gate(state,deps,current,batch=True)
                if stop:return status(stop)
            current=begin_current(state,deps,save)
        if current and current['pair_index']==5:
            if current.get('evaluate_cycle')==deps.begin_cycle:
                return status(current.get('evaluate_error') or 'Automatic checks paused: interrupted evaluation; restart or use --retry-current.')
            current['evaluate_cycle']=deps.begin_cycle
            current.pop('evaluate_error',None)
            status('Evaluating 5 pairs (this can take several minutes)')
            decision=deps.client.decision(current['memory_id'])
            result=None
            # Detail-only responses do not prove evaluation already committed.
            if decision is None or not decision.get('decision'):
                result=deps.client.evaluate(current['memory_id'])
                decision=deps.client.decision(current['memory_id'])
            if decision is None:raise ValueError('evaluation returned without a recorded decision; use --retry-current')
            if decision.get('decision'):
                outcome='trusted' if decision['decision']=='promoted' else decision['decision']
            else:
                # Preserve the evaluate result contract for legacy detail-only decisions.
                outcome=getattr(result.status,'value',result.status) if hasattr(result,'status') else result['status']
            state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status=outcome,decision=decision,**({'discarded_pairs':current['discarded_pairs']} if current.get('discarded_pairs') else {})))
            state['current']=None;save()
            return status((('Positive control '+outcome.replace('_',' ')+': ') if outcome in {'would_pass','would_fail'} else 'Note checked and trusted: ' if outcome=='trusted' else 'Note rejected after check: ')+current['title'])
        if current is None:
            queued=deps.client.validation_queue()
            closed={r['memory_id'] for r in state.get('results',[]) if r.get('origin_failure')}
            closed.update(state.get('note_errors',{}))
            state['queue']=[n for n in queued if n['memory_id'] not in closed];save()
            if queued and not state['queue'] and state.get('note_errors'):
                return status(next(reversed(state['note_errors'].values()))['reason'])
            if queued and not state['queue'] and not settings(deps.root).get('cross_seed',{}).get('enabled',False):
                return status(next(r['origin_failure']['reason'] for r in reversed(state['results']) if r.get('origin_failure')))
            if not state.get('pending') and not state['queue']:
                from .memory_cross_seed_worker import choose
                return choose(state,deps,save,status)
            stop=daily_run_stop(state,deps.root,needed=10)
            if stop:return status(stop)
            note=state['pending']['note'] if state.get('pending') else state['queue'][0]
            if note.get('channel') is None and getattr(deps.runner,'note_channel',lambda n:None)(note)=='dev_agent':
                note=dict(note,channel='dev_agent')
            if note.get('channel')=='dev_agent':
                from .memory_validation_disk import gate
                stop=gate(state,deps,note,batch=True)
                if stop:
                    state['pending']=state.get('pending') or dict(note=note,operation=hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24])
                    return status(stop)
            operation=hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
            # Persist the chosen head before candidate creation removes it from the queue.
            state['pending']=state.get('pending') or dict(note=note,operation=operation);save()
            pending=state['pending']
            preparing=prepare_origin(state,deps,pending['note'],save,status)
            if preparing is not None:return preparing
            if pending['note'].get('channel')=='dev_agent':
                from .memory_validation_disk import gate
                stop=gate(state,deps,pending,batch=True)
                if stop:return status(stop)
            state['current']=begin_item(pending);save()
            current=begin_current(state,deps,save)
        pair=current['plan']['pairs'][current['pair_index']];arm_id=pair[current['arm']+'_cell']
        # Reserve the daily slot durably before dispatch. A crash may conservatively
        # consume a slot, but cannot run the same arm twice or exceed the daily cap.
        from .memory_validation_replacements import attempt_version
        count_id=arm_id if attempt_version(current)==1 else f'{arm_id}-v{attempt_version(current)}'
        if current.get('dev_mode')=='agent':
            from .memory_validation_disk import gate
            stop=gate(state,deps,current)
            if stop:return status(stop)
        counted=state.setdefault('counted',{})
        if count_id not in counted:
            stop=daily_run_stop(state,deps.root)
            if stop:return status(stop)
            counted[count_id]=day;state['runs_today']+=1
        current.update(arm_id=arm_id,active=True,dispatch_pending=True)
        if current.get('dev_mode')=='agent':current.update(arm_started_at=deps.clock(),disk_sample_at=0,arm_disk_bytes=0)
        save()
        check_dispatch_quota(state,deps,save)
        deps.runner.start(current,arm_id)
        current['dispatch_pending']=False;save()
        return status(current.get('replacement_notice') or f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
    except QuotaStop as exc:
        return status(str(exc))
    except Exception as exc:
        from .notices import safe_detail
        message='Automatic checks paused: '+safe_detail(str(exc) or type(exc).__name__)
        if state.get('current') and state['current'].get('kind')=='begin':
            state['current']['begin_error']=message
        elif evaluation_ready(state.get('current')):
            state['current']['evaluate_error']=message
        return status(message)


def origin_command_config(root,client=None):
    """Read-only setup; never construct a runner, pool or provisioning factory."""
    from .memory_validation_provision import Provisioner
    from .memory_service_client import MemoryServiceClient
    config=json.loads((Path(root)/'runtime.json').read_bytes())
    config['state_root']=str(Path(root).resolve())
    if client is None:client=MemoryServiceClient.offline(config['base_url'],key_file=config['key_file'])
    return Provisioner(config,client),config


def reopen_origin(root,memory_id,reason,*,client=None,clock=time.time):
    """Archive a pre-verdict failure while holding the same exclusive worker lock."""
    import getpass
    from .core.dev_store import DevConflict
    from .memory_origin_evidence import identity
    root=Path(root).resolve()
    if not reason or not reason.strip():raise DevConflict('A reason is required to reopen an origin.')
    # The existing worker owns this lock even while Off. Fail closed if it runs.
    with (root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise DevConflict('The validation worker still holds its lock; exit that worker before reopening an origin.') from exc
        provision,config=origin_command_config(root,client);client=provision.client
        path=root/'state.json';state=json.loads(path.read_bytes()) if path.exists() else {}
        if state.get('current'):raise DevConflict('The worker still has current work; resolve it before reopening an origin.')
        # The public inventory includes immutable candidate links, even after a
        # candidate was rejected/demoted. Do not infer absence from the queue.
        memories=client.list_dev_v2_memories()
        if any((r['memory_id']==memory_id and r['status']=='candidate') or r.get('from_provisional')==memory_id for r in memories):
            raise DevConflict('This note already has a service candidate; its origin cannot be reopened.')
        queue=client.validation_queue();note=next((n for n in queue if n['memory_id']==memory_id),None)
        if note is None:raise DevConflict('This note is not eligible under the normal validation queue rules.')
        key,original,_=identity(provision,note);directory=root/'origins'/key
        intent=next((e for e in reversed(state.get('origin_reopens',[])) if e.get('origin_key')==key and e.get('memory_id')==memory_id and e.get('status')=='archiving'),None)
        archived=Path(intent['archive']) if intent else None
        if archived and (archived.parent!=root/'origins-archive' or not archived.name.startswith(key+'-')):
            raise DevConflict('The pending origin archive path is invalid.')
        evidence=archived if archived and archived.exists() and not directory.exists() else directory
        if evidence.resolve()!=evidence or not evidence.is_dir():raise DevConflict('The original origin directory is missing or not canonical.')
        receipt=json.loads((evidence/'receipt.json').read_bytes())
        if receipt.get('origin_key')!=key or receipt.get('note')!=original:raise DevConflict('The origin receipt does not match this note.')
        if receipt.get('status') not in {'refused','error'}:raise DevConflict('Only a refused or error origin can be reopened.')
        if any(k in receipt for k in ('validation','validator_result','validation_result','verdict','result','candidate','candidate_id','service_candidate')) or any((evidence/name).exists() for name in ('validation.json','verdict.json','trial.json')) or receipt.get('catalogs'):
            raise DevConflict('This origin has a validator result or service candidate and cannot be reopened.')
        now=clock();stamp=datetime.fromtimestamp(now).astimezone().strftime('%Y%m%dT%H%M%S%f%z')
        archive=archived or root/'origins-archive'/(key+'-'+stamp)
        if archive.exists() and evidence!=archive:raise DevConflict('The origin archive destination already exists.')
        event=intent or dict(event='origin_reopened',memory_id=memory_id,origin_key=key,actor=getpass.getuser(),uid=os.getuid(),at=now,reason=reason.strip(),archive=str(archive),run_reservation='Previous reservation remains counted; the next attempt reserves a new run.')
        # Journal before the move: a crash leaves an explicit, recoverable intent,
        # never an unrecorded retry or a deleted receipt.
        if intent is None:
            event['status']='archiving';state.setdefault('origin_reopens',[]).append(event);atomic(path,state)
        if evidence!=archive:
            archive.parent.mkdir(parents=True,exist_ok=True)
            if archive.parent.resolve()!=archive.parent:raise DevConflict('The origin archive directory is not canonical.')
            directory.rename(archive)
            for parent in (directory.parent,archive.parent):
                fd=os.open(parent,os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
        state['results']=[r for r in state.get('results',[]) if not (r['memory_id']==memory_id and r.get('status')=='inconclusive' and r.get('origin_failure'))]
        for field in ('origin_failures','origin_errors'):state.get(field,{}).pop(key,None)
        state.get('note_errors',{}).pop(memory_id,None)
        counted=state.setdefault('counted',{})
        if key in counted:counted[archive.name]=counted.pop(key)
        for field in ('pending','cross_pending'):
            if state.get(field,{}).get('note',{}).get('memory_id')==memory_id:state.pop(field)
        closed={r['memory_id'] for r in state['results'] if r.get('origin_failure')}
        state['queue']=[n for n in queue if n['memory_id'] not in closed]
        event['status']='reopened';state['status']='Origin reopened; note queued. Previous run reservation remains counted.'
        atomic(path,state)
        return state['status']


def abandon_interrupted_origin(root,memory_id,reason,*,client=None,clock=time.time):
    """Explicitly archive an unknown origin dispatch, preserving its reservation."""
    import getpass,re
    from .core.dev_store import DevConflict
    from .memory_origin_evidence import identity
    from .memory_origin_recovery import interrupted_artifacts
    root=Path(root).resolve()
    if not reason or not reason.strip():raise DevConflict('A reason is required to abandon an interrupted origin.')
    with (root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise DevConflict('The validation worker still holds its lock; exit that worker before abandoning an interrupted origin.') from exc
        path=root/'state.json';state=json.loads(path.read_bytes())
        current=state.get('current') or {}
        if current.get('kind')!='origin' or current.get('memory_id')!=memory_id:
            raise DevConflict('Current work must be an origin item for this memory ID.')
        provision,config=origin_command_config(root,client);client=provision.client
        memories=client.list_dev_v2_memories()
        if any((r['memory_id']==memory_id and r['status']=='candidate') or r.get('from_provisional')==memory_id for r in memories):
            raise DevConflict('This note already has a service candidate; its origin cannot be abandoned.')
        if any(r.get('memory_id')==memory_id and not r.get('origin_failure') for r in state.get('results',[])):
            raise DevConflict('This note already has a validation result; its origin cannot be abandoned.')
        queue=client.validation_queue();note=next((n for n in queue if n['memory_id']==memory_id),None)
        if note is None:raise DevConflict('This note is not eligible under the normal validation queue rules.')
        key,original,provenance=identity(provision,note)
        if not re.fullmatch(r'origin-[0-9a-f]{32}',key) or current.get('origin_key')!=key or current.get('note')!=original:
            raise DevConflict('The current origin does not match this note.')
        directory=root/'origins'/key
        intent=next((e for e in reversed(state.get('origin_interruptions',[])) if e.get('origin_key')==key and e.get('memory_id')==memory_id and e.get('status')=='archiving'),None)
        archived=Path(intent['archive']) if intent else None
        if archived and (archived.parent!=root/'origins-archive' or not archived.name.startswith(key+'-') or archived.resolve()!=archived):
            raise DevConflict('The pending origin archive path is invalid.')
        evidence=archived if archived and archived.exists() and not directory.exists() else directory
        if evidence.resolve()!=evidence or not evidence.is_dir():raise DevConflict('The interrupted origin directory is missing or not canonical.')
        if (evidence/'receipt.json').exists() or (evidence/'receipt.json').is_symlink():raise DevConflict('An origin receipt exists; interrupted-origin abandonment is not allowed.')
        if (evidence/'dispatch.json').resolve()!=evidence/'dispatch.json':raise DevConflict('The origin dispatch path is not canonical.')
        dispatch=json.loads((evidence/'dispatch.json').read_bytes())
        if dispatch.get('origin_key')!=key or dispatch.get('note')!=original:
            raise DevConflict('The origin dispatch does not match this note.')
        artifacts=interrupted_artifacts(provision,config,current,dispatch,evidence,provenance)
        now=clock();stamp=datetime.fromtimestamp(now).astimezone().strftime('%Y%m%dT%H%M%S%f%z')
        archive=archived or root/'origins-archive'/(key+'-'+stamp)
        if archive.exists() and evidence!=archive:raise DevConflict('The origin archive destination already exists.')
        if archive.parent.resolve()!=archive.parent:raise DevConflict('The origin archive directory is not canonical.')
        event=intent or dict(event='origin_interrupted_abandoned',memory_id=memory_id,origin_key=key,
            actor=getpass.getuser(),uid=os.getuid(),at=now,reason=reason.strip(),archive=str(archive),
            **artifacts,run_reservation='Previous reservation remains counted; the next attempt reserves a new run.')
        # Keep current intact until the archive and reservation transfer are saved.
        # A crash on either side of rename resumes this same frozen intent.
        if intent is None:
            event['status']='archiving';state.setdefault('origin_interruptions',[]).append(event);atomic(path,state)
        elif any(event.get(k)!=artifacts[k] for k in ('export_key','partial_export_dir')):
            raise DevConflict('The pending interrupted origin artifacts differ.')
        if evidence!=archive:
            archive.parent.mkdir(parents=True,exist_ok=True);directory.rename(archive)
        # Also fsync when resuming after rename but before its directory sync.
        for parent in (directory.parent,archive.parent):
            fd=os.open(parent,os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        state['current']=None
        for field in ('pending','cross_pending'):
            item=state.get(field) or {}
            if item.get('memory_id')==memory_id or item.get('note',{}).get('memory_id')==memory_id:state.pop(field)
        state['results']=[r for r in state.get('results',[]) if not (r.get('memory_id')==memory_id and r.get('origin_failure'))]
        for field in ('origin_failures','origin_errors'):state.get(field,{}).pop(key,None)
        state.get('note_errors',{}).pop(memory_id,None)
        state['check_errors']=[e for e in state.get('check_errors',[]) if e.get('memory_id')!=memory_id]
        counted=state.setdefault('counted',{})
        if key in counted:counted[archive.name]=counted.pop(key)
        closed={r['memory_id'] for r in state['results'] if r.get('origin_failure')}
        state['queue']=[n for n in queue if n['memory_id'] not in closed]
        event['status']='abandoned'
        state['status']='Interrupted origin abandoned; note queued. Previous run reservation remains counted.'
        atomic(path,state)
        return state['status']


def retry_origin_registration(root,memory_id,reason,*,client=None,clock=time.time):
    from .memory_origin_registration import retry_origin_registration as retry
    return retry(root,memory_id,reason,client=client,clock=clock)


def main(argv=None,*,client=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state-dir',required=True);p.add_argument('--adapter',help='module:factory; injected authorized ledger runner and service client');p.add_argument('--once',action='store_true');p.add_argument('--interval',type=float,default=5)
    actions=p.add_mutually_exclusive_group()
    actions.add_argument('--set-enabled',choices=['true','false'])
    actions.add_argument('--set-daily-run-limit',type=parse_daily_run_limit,metavar='{none|N}',default=argparse.SUPPRESS)
    actions.add_argument('--set-weekly-quota-stop',type=lambda value:parse_quota_setting(value,key='weekly_quota_stop_percent'),metavar='N',default=argparse.SUPPRESS)
    actions.add_argument('--set-daily-quota-rise-stop',type=lambda value:parse_quota_setting(value,key='daily_quota_rise_stop_points'),metavar='{none|N}',default=argparse.SUPPRESS)
    actions.add_argument('--positive-control-check',metavar='SPEC')
    actions.add_argument('--check-origin',metavar='MEMORY_ID')
    actions.add_argument('--reopen-origin',metavar='MEMORY_ID')
    actions.add_argument('--abandon-interrupted-origin',metavar='MEMORY_ID',help='archive a current origin dispatch without a receipt; requires --reason and an exited worker')
    actions.add_argument('--retry-origin-registration',metavar='MEMORY_ID',help='retry registration of a sealed passed origin without replay; requires --reason and an exited worker')
    actions.add_argument('--retry-current',action='store_true',help='request one retry of interrupted begin, invalid generation recovery or completed batch evaluation; does not turn checks On')
    p.add_argument('--reason')
    a=p.parse_args(argv);root=Path(a.state_dir)
    if a.positive_control_check:
        from .memory_positive_control import queue
        print(queue(root,a.positive_control_check));return 0
    recovery=a.reopen_origin or a.abandon_interrupted_origin or a.retry_origin_registration
    if recovery and not (a.reason and a.reason.strip()):p.error('origin recovery requires --reason')
    if a.reason and not recovery:p.error('--reason requires --reopen-origin, --abandon-interrupted-origin or --retry-origin-registration')
    if a.check_origin or recovery:
        try:
            if a.check_origin:
                provision,config=origin_command_config(root,client);client=provision.client
                from .memory_origin_evidence import check_origin
                message=check_origin(provision,a.check_origin,validator_root=config['validator_root'])
            elif a.abandon_interrupted_origin:message=abandon_interrupted_origin(root,a.abandon_interrupted_origin,a.reason,client=client)
            elif a.retry_origin_registration:message=retry_origin_registration(root,a.retry_origin_registration,a.reason,client=client)
            else:
                provision,config=origin_command_config(root,client);client=provision.client
                message=reopen_origin(root,a.reopen_origin,a.reason,client=client)
            print(message);return 0
        except Exception as exc:
            from .notices import safe_detail
            from .memory_auto_import import Redactor
            redact=Redactor([getattr(client,name,None) for name in ('api_key','importer_key','operator_key')])
            print('FAIL: '+safe_detail(redact(str(exc) or type(exc).__name__)));return 1
    root.mkdir(parents=True,exist_ok=True)
    if a.retry_current:
        try:print(request_current_retry(root));return 0
        except Exception as exc:
            from .notices import safe_detail
            print('FAIL: '+safe_detail(str(exc) or type(exc).__name__));return 1
    if a.set_enabled is not None:set_enabled(root,a.set_enabled=='true');return
    if hasattr(a,'set_daily_run_limit'):set_daily_run_limit(root,a.set_daily_run_limit);return
    if hasattr(a,'set_weekly_quota_stop'):set_quota_setting(root,'weekly_quota_stop_percent',a.set_weekly_quota_stop);return
    if hasattr(a,'set_daily_quota_rise_stop'):set_quota_setting(root,'daily_quota_rise_stop_points',a.set_daily_quota_rise_stop);return
    from types import SimpleNamespace
    with (root/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=root/'state.json';state=json.loads(path.read_bytes()) if path.exists() else {}
        deps=None;configured=False
        while True:
            try:enabled=settings(root)['enabled']
            except Exception:enabled=False
            if deps is None or enabled and a.adapter and not configured:
                if a.adapter and enabled:
                    try:
                        module,name=a.adapter.split(':');deps=getattr(importlib.import_module(module),name)(root)
                    except Exception as exc:
                        from .notices import safe_detail
                        message='Automatic checks paused: adapter unavailable: '+safe_detail(str(exc))
                        state.update(enabled=True,status=message)
                        state.setdefault('notices',[]).append(dict(title=message,at=time.time()))
                        state['notices']=state['notices'][-100:];atomic(path,state);print(message,flush=True)
                        if a.once:return
                        time.sleep(min(60,max(.1,a.interval)));continue
                    configured=True
                else:
                    class Missing:
                        def __getattr__(self,name):raise RuntimeError('authorized ledger adapter is not configured')
                    deps=SimpleNamespace(client=Missing(),runner=Missing(),activity=lambda current:None,quota=weekly_quota_snapshot,refresh_quota=refresh_quota)
                deps.root=root;deps.clock=time.time;deps.save=lambda value:atomic(path,value)
            tick(state,deps)
            print(state['status'],flush=True)
            if a.once:return
            time.sleep(min(60,max(.1,a.interval)))


if __name__=='__main__':raise SystemExit(main())
