"""Idle-only automatic checks. All external work is injected; missing settings means Off.

Adapters must make begin/start/finish idempotent by the supplied frozen IDs and
use the existing goal ledger. A claimed or unknown job is never redispatched.
"""
import argparse,fcntl,hashlib,importlib,json,math,os,sqlite3,time
from datetime import datetime
from pathlib import Path
from .codex_quota import current_window_readings,valid_reset,WEEKLY_READING_MAX_AGE

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
    if not isinstance(value,dict) or set(value)-{'enabled','cross_seed'} or type(value.get('enabled')) is not bool:raise ValueError('invalid automatic checks settings')
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


def weekly_quota_snapshot(home=None,*,now=None):
    """Keep the conservative session maximum within the current weekly window.

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
    readings=current_window_readings(readings,now=now)
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
    """Refresh stale observations once, then apply the unchanged quota stops."""
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
    if quota>=70:return f'Automatic checks paused: Codex weekly use is at {quota:g}%.'
    if quota-state['day_start_quota']>5:return 'Automatic checks paused: Codex weekly use rose by more than 5 points today.'
    return None


class QuotaStop(Exception):
    pass


def check_dispatch_quota(state,deps,save):
    # Provisioning or importing can take time after tick's initial gate.
    if not quota_fresh(state.get('weekly_quota_at'),deps.clock()):
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


def run_activity(ledgers,*,own_experiment=None,own_pids=(),ignored_stale_runs=None,now=None):
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
        **({k:context[k] for k in ('execution_profile',) if k in context}),
        policy_id=policy_id,seeds=[config['seed']],cases=[dict(seed=config['seed'],**{k:context[k] for k in ('scene_id','object_set_id','camera_config_id','task_variant_id','camera_names','task_prompt')})],
        pairs=pairs,failure_signature=signature,assistance_credits=budget['assistance_credits'],arm_generation='dev_from_origin',
        origin_source_sha256=origin['sha256'],validation_config_sha256=config['sha256'],dev_model=dev_model)


def prepare_origin(state,deps,note,save,status,*,cross_seed=False):
    """Reserve one replay run before dispatch, through the ordinary worker gates."""
    needed=getattr(deps.runner,'origin_needed',lambda note:None)(note)
    if needed is None:return None
    key=needed['origin_key']
    if key in state.get('origin_failures',{}):return status(state['origin_failures'][key]['reason'])
    if needed.get('receipt_ready'):
        needed['cross_seed']=cross_seed;state['current']=needed;save()
        return advance_origin(state,deps,None,save,status)
    if state['runs_today']>=20:return status('Automatic checks done for today (20 runs).')
    counted=state.setdefault('counted',{})
    if key not in counted:counted[key]=state['day'];state['runs_today']+=1
    needed.update(dispatch_pending=True,cross_seed=cross_seed);state['current']=needed;save()
    check_dispatch_quota(state,deps,save)
    deps.runner.start(needed,key);needed['dispatch_pending']=False;save()
    return status('Preparing origin evidence: '+needed['title'])


def advance_origin(state,deps,reason,save,status):
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
        if result['status']!='passed':
            state.setdefault('origin_failures',{})[current['origin_key']]=result
            state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status='inconclusive',note_remains='trusted' if current.get('cross_seed') else 'provisional',decision=dict(decision='inconclusive',reason=result['reason']),origin_failure=result))
            state.pop('pending',None);state.pop('cross_pending',None)
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


def tick(state,deps):
    """One durable transition. deps includes quota snapshots and refresh_quota.

    runner.begin(note, id) returns a frozen current record with candidate/plan.
    runner.start(current, arm_id) launches/reconciles exactly one ledger arm;
    poll returns running/paused/completed, finish seals/imports that arm. Neither
    callback may grant fresh budgets. Unknown outcomes raise and remain visible.
    """
    def save():deps.save(state)
    def status(text):
        if state.get('status')!=text:
            state.setdefault('notices',[]).append(dict(title=text,at=deps.clock()))
            state['notices']=state['notices'][-100:]
        state['status']=text;save();return state
    current=state.get('current')
    try:
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
        if state.get('runs_today',0)>=20 and not (current and current.get('active')):reason=reason or 'Automatic checks done for today (20 runs).'
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
                return status(f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
            phase=deps.runner.poll(current)
            if phase=='pending':
                if reason:return status(reason)
                check_dispatch_quota(state,deps,save)
                deps.runner.start(current,current['arm_id'])
                return status('Automatic checks resuming')
            if phase=='completed':
                # Finish/import before moving the cursor. Retrying uses immutable IDs.
                result=deps.runner.finish(current)
                if result.get('status')=='refused':raise ValueError('execution evidence refused; review required')
                if result.get('status')=='failed':
                    # Close using the original sealed verdict; failed arms are never trials.
                    try:decision=deps.client.record_dev_v2_failed_check(**result['failed_check'])
                    except Exception as exc:
                        from .notices import safe_detail
                        gap=dict(memory_id=current['memory_id'],title=current['title'],validation=result,reason=safe_detail(str(exc)),at=deps.clock())
                        state.setdefault('service_gaps',[]).append(gap);state['current']=None;save()
                        return status('Automatic check failed: '+current['title']+'; service decision unavailable: '+gap['reason'])
                    outcome=decision['decision']
                    state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status=outcome,decision=decision,validation_failure=result))
                    message=('Note rejected after check: '+current['title'] if outcome=='rejected' else decision['reason']+' — '+current['title'])
                    state['current']=None;save();return status(message)
                current.setdefault('trials',{})[current['arm_id']]=result
                current['active']=False;current['pause_requested']=False
                if current['arm']=='treatment':
                    pair=current['plan']['pairs'][current['pair_index']]
                    deps.client.record_dev_v2_pair(memory_id=current['memory_id'],control_import_id=current['trials'][pair['control_cell']]['trial_import_id'],treatment_import_id=result['trial_import_id'])
                    current['pair_index']+=1;current['arm']='control'
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
            else:return status(f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
        if reason:return status(reason)
        if current and current['pair_index']==5:
            result=deps.client.evaluate(current['memory_id']);decision=deps.client.decision(current['memory_id'])
            outcome=getattr(result.status,'value',result.status) if hasattr(result,'status') else result['status']
            state.setdefault('results',[]).append(dict(memory_id=current['memory_id'],title=current['title'],status=outcome,decision=decision))
            state['current']=None;save()
            return status(('Note checked and trusted: ' if outcome=='trusted' else 'Note rejected after check: ')+current['title'])
        if current is None:
            queued=deps.client.validation_queue()
            closed={r['memory_id'] for r in state.get('results',[]) if r.get('origin_failure')}
            state['queue']=[n for n in queued if n['memory_id'] not in closed];save()
            if queued and not state['queue'] and not settings(deps.root).get('cross_seed',{}).get('enabled',False):
                return status(next(r['origin_failure']['reason'] for r in reversed(state['results']) if r.get('origin_failure')))
            if not state.get('pending') and not state['queue']:
                from .memory_cross_seed_worker import choose
                return choose(state,deps,save,status)
            if state['runs_today']>10:return status('Automatic checks waiting until tomorrow: fewer than 10 runs remain.')
            note=state['pending']['note'] if state.get('pending') else state['queue'][0]
            operation=hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
            # Persist the chosen head before candidate creation removes it from the queue.
            state['pending']=state.get('pending') or dict(note=note,operation=operation);save()
            pending=state['pending']
            preparing=prepare_origin(state,deps,pending['note'],save,status)
            if preparing is not None:return preparing
            current=deps.runner.begin(pending['note'],pending['operation'])
            current.update(pair_index=0,arm='control',active=False,title=pending['note'].get('title') or 'Untitled note')
            state['current']=current;state.pop('pending');save()
        pair=current['plan']['pairs'][current['pair_index']];arm_id=pair[current['arm']+'_cell']
        # Reserve the daily slot durably before dispatch. A crash may conservatively
        # consume a slot, but cannot run the same arm twice or exceed the daily cap.
        counted=state.setdefault('counted',{})
        if arm_id not in counted:
            if state['runs_today']>=20:return status('Automatic checks done for today (20 runs).')
            counted[arm_id]=day;state['runs_today']+=1
        current.update(arm_id=arm_id,active=True,dispatch_pending=True);save()
        check_dispatch_quota(state,deps,save)
        deps.runner.start(current,arm_id)
        current['dispatch_pending']=False;save()
        return status(f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
    except QuotaStop as exc:
        return status(str(exc))
    except Exception as exc:
        from .notices import safe_detail
        return status('Automatic checks paused: '+safe_detail(str(exc) or type(exc).__name__))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state-dir',required=True);p.add_argument('--adapter',help='module:factory; injected authorized ledger runner and service client');p.add_argument('--once',action='store_true');p.add_argument('--interval',type=float,default=5)
    p.add_argument('--set-enabled',choices=['true','false'])
    a=p.parse_args(argv);root=Path(a.state_dir);root.mkdir(parents=True,exist_ok=True)
    if a.set_enabled is not None:set_enabled(root,a.set_enabled=='true');return
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


if __name__=='__main__':main()
