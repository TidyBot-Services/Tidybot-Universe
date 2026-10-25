"""Cross-seed transitions called only through the M-8 gating/lock/budget loop."""
import hashlib
from .memory_validation_worker import settings,prepare_origin,check_dispatch_quota


def progress(current):
    seed=current['target_seed']
    if current['stage']=='probe':return f'Probing seed {seed} without notes'
    return f"Seed {seed}: checking pair {current['pair_index']+1} of {len(current['plan']['pairs'])} · {current['arm']}"


def choose(state,deps,save,status):
    config=settings(deps.root).get('cross_seed',{})
    state['cross_seed_enabled']=config.get('enabled',False)
    if not state['cross_seed_enabled']:return status('Automatic checks waiting for used notes')
    waiting=state.get('cross_waiting')
    if waiting:
        needed=10 if waiting['probe']['role']=='transfer' else 6
        if 20-state['runs_today']<needed:return status(f'Cross-seed checks waiting until tomorrow: {needed} runs needed for the whole batch.')
        batch=deps.runner.begin_cross(waiting['note'],waiting['operation']+'-pairs',waiting['target'],pairs=needed//2)
        batch.update(pair_index=0,arm='control',active=False,title=waiting['title'])
        state['current']=batch;state.pop('cross_waiting');save()
        return advance(state,deps,None,save,status)
    queued=deps.client.cross_seed_queue()
    closed={r['memory_id'] for r in state.get('results',[]) if r.get('origin_failure')}
    state['cross_queue']=[n for n in queued if n['memory_id'] not in closed];state['skipped_seeds']=[];save()
    if queued and not state['cross_queue']:
        return status(next(r['origin_failure']['reason'] for r in reversed(state['results']) if r.get('origin_failure')))
    pending=state.get('cross_pending')
    if not pending:
        if not state['cross_queue']:return status('Cross-seed checks waiting for scene-trusted notes')
        note=state['cross_queue'][0]
        preparing=prepare_origin(state,deps,note,save,status,cross_seed=True)
        if preparing is not None:return preparing
        tested={r['target_seed'] for r in note['seeds']}
        for seed in config.get('candidate_seeds',[]):
            if seed in tested or seed==note['source_seed']:continue
            try:target=deps.runner.admit_seed(note,seed,config.get('contracts',{}).get(str(seed)))
            except Exception as exc:
                from .notices import safe_detail
                state['skipped_seeds'].append(dict(memory_id=note['memory_id'],seed=seed,reason=safe_detail(str(exc))));save();continue
            operation=hashlib.sha256(f"cross:{note['memory_id']}:{note['version']}:{seed}".encode()).hexdigest()[:24]
            pending=dict(note=note,operation=operation,target=target)
            state['cross_pending']=pending;save();break
    if not pending:return status('Cross-seed checks waiting for usable registered candidate seeds')
    if state['runs_today']>=20:return status('Automatic checks done for today (20 runs).')
    current=deps.runner.begin_cross(pending['note'],pending['operation'],pending['target'])
    current.update(pair_index=0,arm='probe',active=False,title=pending['note'].get('title') or 'Untitled note')
    state['current']=current;state.pop('cross_pending');save()
    return advance(state,deps,None,save,status)


def advance(state,deps,reason,save,status):
    current=state['current']
    if not settings(deps.root).get('cross_seed',{}).get('enabled',False):reason=reason or 'Cross-seed checks are Off'
    if current.get('active'):
        if current.get('dispatch_pending'):
            if reason:return status(reason)
            check_dispatch_quota(state,deps,save)
            deps.runner.start(current,current['arm_id']);current['dispatch_pending']=False;save();return status(progress(current))
        phase=deps.runner.poll(current)
        if phase=='pending':
            if reason:return status(reason)
            check_dispatch_quota(state,deps,save)
            deps.runner.start(current,current['arm_id']);return status(progress(current))
        if phase=='completed':
            result=deps.runner.finish(current)
            if result.get('status')=='refused':raise ValueError('cross-seed evidence refused; review required')
            if current['stage']=='probe':
                probe=result['probe']
                if probe['role']!='could_not_finish':state['cross_waiting']=dict(current,probe=probe,active=False)
                state.setdefault('cross_notes',{})[current['memory_id']]=dict(title=current['title'],**deps.client.cross_seed_status(memory_id=current['memory_id']))
                state['current']=None;save();return status(f"Seed {current['target_seed']}: "+probe['role'].replace('_',' '))
            if result.get('status')=='failed':
                decision=deps.client.cross_seed_write('failed-checks',**result['failed_check'],target_seed=current['target_seed'])
                state.setdefault('cross_notes',{})[current['memory_id']]=dict(title=current['title'],**deps.client.cross_seed_status(memory_id=current['memory_id']))
                state['current']=None;save();return status(decision['reason'])
            current.setdefault('trials',{})[current['arm_id']]=result
            if current['arm']=='treatment':
                pair=current['plan']['pairs'][current['pair_index']]
                deps.client.cross_seed_write('pairs',memory_id=current['memory_id'],target_seed=current['target_seed'],control_import_id=current['trials'][pair['control_cell']]['trial_import_id'],treatment_import_id=result['trial_import_id'])
                current['pair_index']+=1;current['arm']='control'
                state.setdefault('cross_notes',{})[current['memory_id']]=dict(title=current['title'],**deps.client.cross_seed_status(memory_id=current['memory_id']))
            else:current['arm']='treatment'
            current['active']=False;current['pause_requested']=False;save()
        elif reason or current.get('pause_requested'):
            if reason:
                if not current.get('pause_requested'):deps.runner.pause(current);current['pause_requested']=True
                return status(reason)
            if phase=='paused':
                check_dispatch_quota(state,deps,save)
                deps.runner.resume(current);current['pause_requested']=False
            return status('Automatic checks resuming' if phase=='paused' else 'Automatic checks waiting for pause')
        else:return status(progress(current))
    if current['stage']=='pairs' and current['pair_index']==len(current['plan']['pairs']):
        state['current']=None;save();return status(f"Seed {current['target_seed']}: paired checks finished")
    if reason:return status(reason)
    arm_id=current['probe_cell'] if current['stage']=='probe' else current['plan']['pairs'][current['pair_index']][current['arm']+'_cell']
    counted=state.setdefault('counted',{})
    if arm_id not in counted:
        if state['runs_today']>=20:return status('Automatic checks done for today (20 runs).')
        counted[arm_id]=state['day'];state['runs_today']+=1
    current.update(arm_id=arm_id,active=True,dispatch_pending=True);save()
    check_dispatch_quota(state,deps,save)
    deps.runner.start(current,arm_id);current['dispatch_pending']=False;save()
    return status(progress(current))
