"""Idle-only automatic checks. All external work is injected; missing settings means Off.

Adapters must make begin/start/finish idempotent by the supplied frozen IDs and
use the existing goal ledger. A claimed or unknown job is never redispatched.
"""
import argparse,fcntl,hashlib,importlib,json,math,os,sqlite3,time
from datetime import datetime
from pathlib import Path


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


def weekly_quota(home=None,*,now=None):
    """Maximum weekly use from each recent session's latest rate-limit receipt."""
    root=Path(home).expanduser() if home is not None else Path.home()
    cutoff=(time.time() if now is None else now)-86400;values=[]
    for path in root.joinpath('.codex','sessions').rglob('*.jsonl'):
        try:
            if path.stat().st_mtime<cutoff:continue
            latest=None
            with path.open() as stream:
                for line in stream:
                    try:
                        row=json.loads(line);limits=row.get('payload',{}).get('rate_limits') or row.get('rate_limits')
                        if isinstance(limits,dict):latest=limits
                    except (ValueError,TypeError,AttributeError):continue
            for key in ('primary','secondary'):
                window=(latest or {}).get(key)
                if not isinstance(window,dict) or window.get('window_minutes')!=10080:continue
                used=window.get('used_percent')
                if type(used) in (int,float) and math.isfinite(used) and 0<=used<=100:values.append(used)
        except (OSError,UnicodeError):continue
    if not values:raise ValueError('Codex weekly quota is unreadable')
    return max(values)


def run_activity(ledgers,*,own_experiment=None,own_pids=()):
    """Read-only process/ledger probe. An unreadable ledger fails closed."""
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in own_pids:continue
        try:args=(p/'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError,ProcessLookupError):continue
        if any(b'daily_dev_v2' in a for a in args) and b'--worker' in args:return 'Another run is active'
    for path in ledgers:
        with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
            for raw, in db.execute('SELECT payload FROM v2_experiments'):
                e=json.loads(raw)
                if e.get('experiment_id')==own_experiment:continue
                tier=e.get('selection',{}).get('budget_tier','legacy')
                if tier!='dev' and e['state'] in {'running','planned','claimed'}:return 'The frozen main matrix is planned or running'
                if e['state'] in {'running','claimed'}:return 'Another run is active'
            for raw, in db.execute("SELECT payload FROM v2_jobs WHERE state IN ('claimed','needs_review')"):
                job=json.loads(raw)
                row=db.execute('SELECT payload FROM v2_cells WHERE id=?',(job['cell'],)).fetchone()
                if not row or json.loads(row[0]).get('experiment_id')!=own_experiment:return 'Another run has active or unresolved work'
    return None


def contract_catalog(context):
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    ref=registered_contract_ref();p=Path(ref['uri'])
    return dict(role='contract',root=str(p.parent),context=context,files={p.name:ref['sha256']})


def freeze_plan(memory_id,origin,config,context,signature,pairs,*,policy_id,budget,dev_model):
    from attention_memory_service.dev_v2 import SCOPE
    if len(pairs)!=5 or dev_model['provider']!='codex':raise ValueError('five Codex pairs required')
    return dict(schema_version='attentionbench.memory-validation-plan.v2',profile='single_scene_v1',memory_id=memory_id,
        **{k:context[k] for k in SCOPE if k not in ('scene_id','object_set_id','camera_config_id','task_variant_id')},
        policy_id=policy_id,seeds=[config['seed']],cases=[dict(seed=config['seed'],**{k:context[k] for k in ('scene_id','object_set_id','camera_config_id','task_variant_id','camera_names','task_prompt')})],
        pairs=pairs,failure_signature=signature,assistance_credits=budget['assistance_credits'],arm_generation='dev_from_origin',
        origin_source_sha256=origin['sha256'],validation_config_sha256=config['sha256'],dev_model=dev_model)


def tick(state,deps):
    """One durable transition. deps: root, save, client, runner, clock, quota, activity.

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
        reason=deps.activity(current)
        try:
            quota=deps.quota()
            if type(quota) not in (int,float) or not math.isfinite(quota) or not 0<=quota<=100:raise ValueError()
            if state.get('day_start_quota') is None:state['day_start_quota']=quota
            state['weekly_quota']=quota
            if quota>=70:reason=reason or f'Automatic checks paused: Codex weekly use is at {quota:g}%.'
            elif quota-state['day_start_quota']>5:reason=reason or 'Automatic checks paused: Codex weekly use rose by more than 5 points today.'
        except Exception:reason=reason or 'Automatic checks paused: Codex weekly quota is unreadable.'
        if state.get('runs_today',0)>=20 and not (current and current.get('active')):reason=reason or 'Automatic checks done for today (20 runs).'
        if current and current.get('kind')=='cross_seed':
            from .memory_cross_seed_worker import advance
            return advance(state,deps,reason,save,status)
        if current and current.get('active'):
            if current.get('dispatch_pending'):
                if reason:return status(reason)
                deps.runner.start(current,current['arm_id']);current['dispatch_pending']=False;save()
                return status(f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
            phase=deps.runner.poll(current)
            if phase=='pending':
                if reason:return status(reason)
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
                if phase=='paused':deps.runner.resume(current);current['pause_requested']=False
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
            state['queue']=deps.client.validation_queue();save()
            if not state.get('pending') and not state['queue']:
                from .memory_cross_seed_worker import choose
                return choose(state,deps,save,status)
            if state['runs_today']>10:return status('Automatic checks waiting until tomorrow: fewer than 10 runs remain.')
            note=state['pending']['note'] if state.get('pending') else state['queue'][0]
            operation=hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
            # Persist the chosen head before candidate creation removes it from the queue.
            state['pending']=state.get('pending') or dict(note=note,operation=operation);save()
            pending=state['pending'];current=deps.runner.begin(pending['note'],pending['operation'])
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
        deps.runner.start(current,arm_id)
        current['dispatch_pending']=False;save()
        return status(f"Checking pair {current['pair_index']+1} of 5 · {current['arm']}")
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
                    deps=SimpleNamespace(client=Missing(),runner=Missing(),activity=lambda current:None,quota=weekly_quota)
                deps.root=root;deps.clock=time.time;deps.save=lambda value:atomic(path,value)
            tick(state,deps)
            print(state['status'],flush=True)
            if a.once:return
            time.sleep(min(60,max(.1,a.interval)))


if __name__=='__main__':main()
