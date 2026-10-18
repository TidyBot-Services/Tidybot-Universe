"""Supervisor entry for this existing goal. No approval or budget reset command."""
import argparse
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from .dev_capabilities import execution_environment
from .dev_ledger import DevV2Store
from .dev_loop import sha, process_identity, owner_alive
from .dev_main_bridge import DevMainBridge
from .dev_configuration import normalize_configuration, derive_cell_budget, configured_budget
from .core.dev_store import DevConflict, DevBudgetExhausted
from .legacy_paths import resolve
from .dev_ledger import RESOURCES,fill_optional
from .dev_tasks import validate_selection
from .dev_v2_loop import immutable
from . import dev_experiment as experiments

CLI_MODULE='benchmarks.attention_harness.dev_integration'
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/full-planner-threefeatures-20261015'
GOAL_KEY='full-planner-threefeatures-20261015'
GOAL_ID=hashlib.sha256(('goal|'+GOAL_KEY).encode()).hexdigest()[:32]
LEDGER=OUT/'real-integration-ledger.sqlite'
STATE=OUT/'main-integration/host'
SERVICE=resolve(Path('/home/truares/桌面/attention_memory_service'))


def load_private(path):
    if not path:return
    p=Path(path)
    if not stat.S_ISREG(p.stat().st_mode) or p.stat().st_mode&0o077:raise PermissionError('private environment file must be owner-only')
    value=json.loads(p.read_bytes())
    permitted={'PARCC_API_KEY','LITELLM_KEY','ATTENTION_MEMORY_API_KEY','ATTENTION_HUMAN_APPROVAL_TOKEN'}
    if not isinstance(value,dict) or set(value)-permitted or any(not isinstance(v,str) for v in value.values()):raise ValueError('only approved online/provider/human variables are accepted')
    os.environ.update(value)


def bridge(port):
    settings=json.loads((STATE/'memory.json').read_bytes())
    return DevMainBridge(LEDGER,STATE/'artifacts',GOAL_KEY,settings,f'http://127.0.0.1:{port}/ui/')


def epoch():
    # Independent repositories retain their own identities; actual bytes, not HEAD
    # alone, identify this dirty source. No environment or credentials are copied.
    files=list((ROOT/'benchmarks/attention_harness').glob('dev*.py'))
    files += [ROOT/p for p in ('benchmarks/attention_harness/ui_server.py','benchmarks/attention_harness/ui/live.js','benchmarks/attention_harness/ui/index.html','openarm365_dev_v2/dev_config.py','openarm365_dev_v2/runner.py')]
    manifest=json.loads((SERVICE/'outputs/full-planner-threefeatures-20261015/changes/manifest.json').read_bytes())
    service_files={}
    for r in manifest:
        name=r.get('relative_path') or r.get('path')
        p=Path(name) if Path(name).is_absolute() else SERVICE/name
        actual=sha(resolve(p))
        if actual!=r['after_sha256']:raise ValueError('Service final contract bytes drifted: '+str(p))
        service_files[str(p)]=actual
    return dict(schema='attentionbench.main-integration-epoch.v1',goal_id=GOAL_ID,ledger=str(LEDGER),
        main_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        main_files={str(p):sha(p) for p in sorted(files)},service_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SERVICE,text=True).strip(),service_files=service_files,
        permissions=dict(online='exact trusted retrieve/grant/recheck/use only',motion='specific external human decision required',
            offline_import='separate importer process',native='no keys/tokens; unchanged safety',formal_eligible=False))


def checked_json(ref):
    path=Path(ref.get('uri') or ref['path'])
    if sha(path)!=ref['sha256']:raise DevConflict('immutable preparation reference drift: '+str(path))
    return json.loads(path.read_bytes())


def active_epoch(store=None, db=None):
    prepared=json.loads((STATE/'prepared.json').read_bytes())
    if db is None:
        store=store or DevV2Store(LEDGER)
        with store.tx() as db:return active_epoch(store,db)
    e=store.get(db,'experiments',prepared['experiment_id'])
    if e.get('goal_id')!=GOAL_ID:raise DevConflict('preparation outside original goal')
    return checked_json(e['source_epoch']) if e.get('source_epoch') else json.loads((STATE/'epoch.json').read_bytes())


def verify_epoch():
    if epoch()!=active_epoch():raise ValueError('final source epoch changed; preserve old receipts; use rebind-prepared only before any dispatch')


@contextmanager
def lifecycle_lock():
    # All host CLI mutations serialize, including the spawn-to-receipt window.
    STATE.parent.mkdir(parents=True,exist_ok=True)
    with (STATE.parent/('.'+STATE.name+'-lifecycle.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def zero_execution_guard(store,db,eid):
    raw=store.get(db,'goals',GOAL_ID);g=store._goal(db,GOAL_ID)
    if 'real_started_at' not in raw or set(raw['used'])!=set(RESOURCES):raise DevConflict('unknown goal clock/usage schema')
    if raw.get('real_started_at') is not None or raw.get('real_finished_at') is not None or raw.get('opening') or any(raw['used'].values()) or any(g['reserved'].values()):
        raise DevConflict('zero-execution required: goal clock/actual/opening/reservation exists')
    if g['wall_remaining']<=0:raise DevBudgetExhausted('original goal deadline expired')
    # Orphans cannot be proved to belong to another goal; refuse unknown holds.
    for table in ('v2_jobs','v2_versions','v2_planner_requests','v2_planner_work'):
        if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone() and db.execute('SELECT 1 FROM '+table+' r LEFT JOIN v2_cells c ON r.cell=c.id WHERE c.id IS NULL').fetchone():
            raise DevConflict('zero-execution required: orphan/unknown ledger evidence in '+table)
    keys=set();ids=set()
    for ident,payload in db.execute('SELECT id,payload FROM v2_experiments'):
        e=json.loads(payload)
        if e.get('goal_id')!=GOAL_ID:continue
        ids.add(ident)
        if e.get('state')!='planned' or e.get('cancelled') is not False or e.get('owner') is not None:
            raise DevConflict('zero-execution required: experiment dispatched/owned/unknown')
        for key,p in db.execute('SELECT id,payload FROM v2_cells WHERE exp=?',(ident,)):
            c=json.loads(p);keys.add(key)
            if c.get('experiment_id')!=ident or set(c['used'])!=set(RESOURCES) or c.get('state')!='planned' or c.get('owner') is not None or c.get('created_at') is not None or any(c['used'].values()) or c.get('mixed_policy'):
                raise DevConflict('zero-execution required: cell started/changed/unknown')
            if db.execute('SELECT 1 FROM v2_jobs WHERE cell=?',(key,)).fetchone() or db.execute('SELECT 1 FROM v2_versions WHERE cell=?',(key,)).fetchone():
                raise DevConflict('zero-execution required: jobs or versions exist (including released/unknown)')
            for table in ('v2_planner_requests','v2_planner_work'):
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone() and db.execute('SELECT 1 FROM '+table+' WHERE cell=?',(key,)).fetchone():
                    raise DevConflict('zero-execution required: planner request/work exists; no guidance may be reused')
    if eid not in ids or not keys:raise DevConflict('original prepared experiment/cells missing')
    for kind,p in db.execute('SELECT kind,payload FROM v2_events'):
        ev=json.loads(p)
        if ev.get('goal_id',GOAL_ID)!=GOAL_ID or ev.get('experiment_id',eid) not in ids or ev.get('cell',next(iter(keys))) not in keys:continue
        if kind not in {'experiment_created','preparation_source_rebound','preparation_source_rebind_refused'}:
            raise DevConflict('zero-execution required: prior work/unknown event '+kind)
    # No UI may race a fresh HTTP dispatch. Dead failed UI receipts are retained.
    receipt=STATE/'ui-process.json'
    if receipt.exists():
        identity=json.loads(receipt.read_bytes())
        if not isinstance(identity,dict) or set(identity)!={'pid','start_ticks','boot_id'}:raise DevConflict('unknown UI ownership receipt')
        if owner_alive(identity):raise DevConflict('owned UI active; zero-dispatch source rebind refused')
    for ident in ids:
        e=store.get(db,'experiments',ident);directory=Path(e['artifact_dir'])
        if list(directory.glob('scheduler-process-*.json')):raise DevConflict('scheduler receipt exists: dispatched/unknown preparation cannot rebind')
        # Only creation artifacts and prior immutable rebinds can exist.
        for path in directory.iterdir():
            if path.name not in {'manifest.json','identity-lock.json','ui-selection.json','source-revisions'}:
                raise DevConflict('unknown execution artifact: '+str(path))
    return dict(goal=raw,remaining=g['remaining'],
        experiments=sorted(ids),cells=sorted(keys),jobs=0,versions=0,requests=0,work=0)


def validate_source_revision(old_epoch,new_epoch,old_lock,new_lock):
    # Explicit reviewed continuation, not a general source migration. Both the
    # old bytes and final bytes must match the review artifact. Whole-goal zero
    # execution is checked before and after this function in the same tx.
    names={'benchmarks/attention_harness/dev_integration.py',
           'benchmarks/attention_harness/dev_main_bridge.py',
           'benchmarks/attention_harness/ui/live.js',
           'openarm365_dev_v2/tests/test_integration_epoch.py',
           'openarm365_dev_v2/tests/test_prepared_lifecycle.py'}
    readiness=json.loads((OUT/'main-integration/source-readiness.json').read_bytes())
    review=readiness.get('prepared_lifecycle_revision',{})
    approved=review.get('files',{})
    if review.get('schema')!='attentionbench.prepared-lifecycle-review.v1' or set(approved)!=names:
        raise DevConflict('exact reviewed lifecycle source-readiness required')
    for field in ('main_head','main_files','service_head','service_files','permissions'):
        if readiness.get(field)!=new_epoch.get(field):raise DevConflict('final source-readiness differs: '+field)
    allowed={str(ROOT/name) for name in names}
    for name,row in approved.items():
        path=str(ROOT/name)
        if row['after_sha256']!=sha(ROOT/name):raise DevConflict('reviewed final source drift: '+name)
        if path in new_epoch['main_files'] and new_epoch['main_files'][path]!=row['after_sha256']:
            raise DevConflict('reviewed final epoch differs: '+name)
        if path in new_lock['files'] and new_lock['files'][path]!=row['after_sha256']:
            raise DevConflict('reviewed final lock differs: '+name)
    for old,new,field in ((old_epoch,new_epoch,'main_files'),(old_lock,new_lock,'files')):
        if {k:v for k,v in old.items() if k not in {field,'source_sets'}}!={k:v for k,v in new.items() if k not in {field,'source_sets'}}:
            raise DevConflict('unrelated source identity/Service/model change refused')
        changed={k for k in set(old[field])|set(new[field]) if old[field].get(k)!=new[field].get(k)}
        if not changed<=allowed or set(old[field])-set(new[field]):raise DevConflict('unrelated source bytes changed: '+str(sorted(changed-allowed)))
        for path in changed:
            row=approved[str(Path(path).relative_to(ROOT))]
            if old[field].get(path)!=row['before_sha256'] or new[field].get(path)!=row['after_sha256']:
                raise DevConflict('source difference outside reviewed before/after pair: '+path)
    old_sets=old_lock['source_sets'];new_sets=new_lock['source_sets']
    if set(old_sets)!=set(new_sets):raise DevConflict('source set roots changed')
    for folder in old_sets:
        if set(old_sets[folder])-set(new_sets[folder]) or not (set(old_sets[folder])^set(new_sets[folder]))<=allowed:
            raise DevConflict('unrelated source file set changed')


def rebind_prepared():
    """Audit and atomically rebind immutable sources of a never-dispatched plan.

    No goal/cell/job rows, allocation, deadline, approval or usage are changed.
    Files made before a crash are inert until the single experiment-row commit.
    """
    if not LEDGER.is_file():raise FileNotFoundError('original goal ledger required')
    store=DevV2Store(LEDGER)
    prepared=json.loads((STATE/'prepared.json').read_bytes());eid=prepared['experiment_id']
    try:
        with store.tx() as db:
            evidence=zero_execution_guard(store,db,eid)
            e=store.get(db,'experiments',eid);old_manifest=checked_json(e['manifest'])
            old_epoch=active_epoch(store,db);new_epoch=epoch()
            if old_manifest['goal_id']!=GOAL_ID or old_manifest['experiment_id']!=eid or old_manifest['selection']!=e['selection'] or Path(old_manifest['ledger_path']).resolve()!=LEDGER.resolve():
                raise DevConflict('original manifest/selection/ledger binding differs')
            # The original preparation stays the authority for all conditions;
            # only lock references may differ in subsequent source revisions.
            initial=checked_json(prepared['manifest']);expected=copy.deepcopy(initial)
            expected['identity_lock']=old_manifest['identity_lock']
            for config in expected['configs'].values():config['dev_lock']=old_manifest['identity_lock']
            ui_selection=json.loads((Path(e['artifact_dir'])/'ui-selection.json').read_bytes())
            if expected!=old_manifest or prepared['goal_id']!=GOAL_ID or prepared['seed']!=initial['selection']['seeds'][0] or prepared['wait_seconds']!=initial['planner_features']['human_wait_seconds'] or prepared['ui_url']!=f"http://127.0.0.1:{prepared['port']}/ui/":
                raise DevConflict('original prepared conditions changed')
            if ui_selection['tasks']!=initial['selection']['tasks'] or ui_selection['seeds']!=initial['selection']['seeds'] or ui_selection['human_wait_seconds']!=prepared['wait_seconds'] or json.loads((STATE/'memory.json').read_bytes())!=initial['planner_features']['memory']:
                raise DevConflict('original UI selection/Memory binding changed')
            experiments.validate_fairness(old_manifest)
            if fill_optional(old_manifest['goal_limits'])!=fill_optional(evidence['goal']['limits']) or old_manifest['wall_limit']!=evidence['goal']['wall_limit']:
                raise DevConflict('original goal allocation binding differs')
            required_wall=len(old_manifest['cells'])*(old_manifest['cell_budget']['model_seconds']+old_manifest['cell_budget']['execution_seconds']+old_manifest['max_versions']*old_manifest['planner_features']['human_wait_seconds'])
            if store._goal(db,GOAL_ID)['wall_remaining']<required_wall:raise DevBudgetExhausted('original remaining wall insufficient for prepared plan')
            if new_epoch==old_epoch:
                experiments.verify_manifest(e)
                return dict(experiment_id=eid,manifest=e['manifest'],changed=False,dispatch=False)
            revision=Path(e['artifact_dir'])/'source-revisions'/hashlib.sha256(json.dumps([e['manifest'],new_epoch],sort_keys=True).encode()).hexdigest()
            if revision.parent.is_symlink() or revision.is_symlink():raise DevConflict('linked source revision directory refused')
            revision.mkdir(parents=True,exist_ok=True)
            from openarm365_dev_v2.dev_config import make_lock,verify_lock,validate_dev_config
            lock_path=revision/'identity-lock.json'
            new_ref={'path':str(lock_path),'sha256':sha(lock_path)} if lock_path.exists() else make_lock(lock_path)
            new_lock=verify_lock(new_ref);old_lock=checked_json(old_manifest['identity_lock'])
            validate_source_revision(old_epoch,new_epoch,old_lock,new_lock)
            new_manifest=copy.deepcopy(old_manifest);new_manifest['identity_lock']=new_ref
            for config in new_manifest['configs'].values():
                if config['dev_lock']!=old_manifest['identity_lock']:raise DevConflict('original config lock differs')
                config['dev_lock']=new_ref;validate_dev_config(config)
            # Preserve bytes and URIs, including the original failed startup.
            originals={STATE/'epoch.json',STATE/'prepared.json',STATE/'memory.json',
                Path(e['manifest']['uri']),Path(old_manifest['identity_lock']['path']),Path(e['artifact_dir'])/'ui-selection.json'}
            old_epoch_ref=e.get('source_epoch') or {'uri':str(STATE/'epoch.json'),'sha256':sha(STATE/'epoch.json')}
            originals.add(Path(old_epoch_ref['uri']))
            if e.get('preparation_rebind'):originals.add(Path(e['preparation_rebind']['uri']))
            originals.update(p for p in (STATE/'ui.log',STATE/'ui-process.json') if p.exists())
            preserved=[]
            for index,path in enumerate(sorted(originals)):
                if path.is_symlink() or not path.is_file():raise DevConflict('missing/linked original preparation evidence: '+str(path))
                data=path.read_bytes();backup=revision/'previous'/f'{index}-{path.name}'
                backup.parent.mkdir(parents=True,exist_ok=True)
                if not backup.exists():
                    with backup.open('xb') as f:f.write(data)
                    backup.chmod(0o444)
                if backup.read_bytes()!=data:raise DevConflict('previous evidence changed during retry')
                preserved.append(dict(uri=str(path),sha256=sha(path),preserved=dict(uri=str(backup),sha256=sha(backup))))
            source_ref=immutable(revision/'epoch.json',new_epoch)
            manifest_ref=immutable(revision/'manifest.json',new_manifest)
            experiments.verify_manifest({**e,'manifest':manifest_ref})
            audit=immutable(revision/'rebind.json',dict(schema='attentionbench.prepared-source-rebind.v1',
                goal_id=GOAL_ID,experiment_id=eid,old_manifest=e['manifest'],new_manifest=manifest_ref,old_source_epoch=old_epoch_ref,
                source_epoch=source_ref,preserved=preserved,zero_execution=evidence,
                authorization='source preparation only; concrete motion still requires external human approval',dispatch=False))
            # A second source observation and process guard precede the commit.
            if epoch()!=new_epoch:raise DevConflict('source changed during rebind')
            zero_execution_guard(store,db,eid)
            previous_ref=e['manifest']
            e.update(manifest=manifest_ref,source_epoch=source_ref,preparation_rebind=audit)
            store.put(db,'experiments',eid,e)
            store.event(db,'preparation_source_rebound',dict(goal_id=GOAL_ID,experiment_id=eid,audit=audit,old_manifest=previous_ref,new_manifest=manifest_ref))
            return dict(experiment_id=eid,manifest=manifest_ref,source_epoch=source_ref,audit=audit,changed=True,dispatch=False)
    except Exception as exc:
        # Refusals leave the binding untouched and retain a durable reason.
        with store.tx() as db:
            store.event(db,'preparation_source_rebind_refused',dict(goal_id=GOAL_ID,experiment_id=eid,reason=str(exc)))
        raise


def prepare(args):
    if not LEDGER.is_file():raise FileNotFoundError('original goal ledger missing; cannot create another')
    store=DevV2Store(LEDGER);store.goal(GOAL_ID)
    settings=dict(base_url=args.memory_url,store_id=args.memory_store_id,namespace=args.namespace)
    b=DevMainBridge(LEDGER,STATE/'artifacts',GOAL_KEY,settings,f'http://127.0.0.1:{args.port}/ui/')
    o=b.options()
    # Two tasks, one legal seed each, at most 2 Dev/2 native + 1 Advisor per
    # task. No matrix and no paired Memory run behind a failed D3 prerequisite.
    selection=dict(request_key='main-integration-final-source-eval-v1',tasks=['pick_place_counter_to_sink','open_cabinet'],seeds=[args.seed],
        policies=o['policies'],mode='experiment',help_budget=1,human_wait_seconds=args.wait_seconds,budget_tier='legacy',
        configuration=dict(goal_id=GOAL_KEY,experiment_budget=o['goal_limits'],max_iterations=2,wall_seconds=store.goal(GOAL_ID)['wall_limit']))
    from .eval_agent import default_config as eval_default
    selection['eval_agent']={'enabled':True,'model':eval_default(True)['model']}
    p=normalize_configuration(dict(max_versions=selection['configuration']['max_iterations'],
        target_depth=selection['configuration']['max_iterations'],target_valid_executions=selection['configuration']['max_iterations'],
        help_budget=selection['help_budget'],policies=selection['policies'],coverage='budget_limited',
        planner_features=dict(human_wait_seconds=args.wait_seconds,memory=settings),budget_tier='legacy',
        eval_agent=eval_default(True)))
    budget=derive_cell_budget(p)
    configured_budget({**p,'cell_budget':budget})
    selection['configuration']['cell_budget']=budget
    for task in selection['tasks']:validate_selection(task,args.seed)
    if type(args.port) is not int or not 1<=args.port<=65535 or args.port==8769:raise ValueError('isolated UI port required')
    eid='devexp-'+hashlib.sha256(selection['request_key'].encode()).hexdigest()[:24]
    n=len(selection['tasks'])*len(selection['seeds'])*len(selection['policies'])
    # Inspect the existing goal only. Account for prior allocations and paid or
    # unknown holds; retries must not allocate this same experiment a second time.
    with store.tx() as db:
        g=store._goal(db,GOAL_ID)
        allocated={k:0 for k in RESOURCES};own_used={k:0 for k in RESOURCES};own_held=dict(own_used)
        for ep, in db.execute('SELECT payload FROM v2_experiments WHERE id!=?',(eid,)):
            ep=json.loads(ep)
            if ep.get('goal_id')!=GOAL_ID:continue
            for cp, in db.execute('SELECT payload FROM v2_cells WHERE exp=?',(ep['experiment_id'],)):
                for k,v in json.loads(cp)['budget'].items():allocated[k]+=v
        for cp, in db.execute('SELECT payload FROM v2_cells WHERE exp=?',(eid,)):
            for k,v in json.loads(cp)['used'].items():own_used[k]+=v
        from .dev_ledger import charged_amount
        from .free_models import limited_resources
        from .dev_experiment import MODEL
        limited=limited_resources(eval_config=p['eval_agent'],model_names=(MODEL['model'],p['advisor_proxy']['model']))
        for state,j in store.goal_jobs(db,GOAL_ID):
            if state in {'reserved','claimed','needs_review'} and db.execute('SELECT exp FROM v2_cells WHERE id=?',(j['cell'],)).fetchone()[0]==eid:
                for k,v in charged_amount(j,j['reservation']).items():own_held[k]+=v
        shortage={k:allocated[k]+n*budget[k]-o['goal_limits'].get(k,0) for k in limited if allocated[k]+n*budget[k]>o['goal_limits'].get(k,0)}
        if shortage:raise DevBudgetExhausted('shared goal allocation exceeds original limits: '+', '.join(f'{k} +{v}' for k,v in shortage.items()))
        if any(max(0,n*budget[k]-own_used[k]-own_held[k])>g['remaining'][k] for k in limited):raise DevBudgetExhausted('original goal remaining resources insufficient for prepared plan')
        required_wall=n*(budget['model_seconds']+budget['execution_seconds']+p['max_versions']*p['planner_features']['human_wait_seconds'])
        if g.get('real_finished_at') or g['wall_remaining']<required_wall:raise DevBudgetExhausted('original goal remaining wall insufficient for prepared plan')
    # No immutable host configuration is written until all input, resource and
    # source checks pass. Existing bindings are checked before creating artifacts.
    source_epoch=epoch()
    bindings={'memory.json':settings,'epoch.json':source_epoch}
    if (STATE/'prepared.json').exists():
        try:verify_epoch()
        except ValueError as exc:raise DevConflict(str(exc)) from exc
        bindings.pop('epoch.json')  # Original epoch stays immutable after a rebind.
    for name,value in bindings.items():
        path=STATE/name
        if path.exists() and json.loads(path.read_bytes())!=value:raise DevConflict('immutable recovery artifact conflict: '+str(path))
    if (STATE/'prepared.json').exists():
        old=json.loads((STATE/'prepared.json').read_bytes())
        if any(old.get(k)!=v for k,v in dict(experiment_id=eid,goal_id=GOAL_ID,port=args.port,seed=args.seed,wait_seconds=args.wait_seconds).items()):raise DevConflict('prepared configuration differs; preserve the frozen request and deadline')
    result=b.create(dict(start=False,selection=selection))
    STATE.mkdir(parents=True,exist_ok=True)
    for name,value in bindings.items():immutable(STATE/name,value)
    if not (STATE/'prepared.json').exists():immutable(STATE/'prepared.json',dict(experiment_id=result['experiment_id'],manifest=result['manifest'],goal_id=GOAL_ID,
        ui_url=f'http://127.0.0.1:{args.port}/ui/',port=args.port,seed=args.seed,wait_seconds=args.wait_seconds,
        real_dispatch=False,scene_gate='D3 unresolved; offline paired Memory validation NOT STARTED'))
    return dict(experiment_id=result['experiment_id'],ui_url=f'http://127.0.0.1:{args.port}/ui/',dispatch=False)


def serve(args):
    verify_epoch()
    from .ui_server import create_server
    from .core.store import AttentionStore
    # Ordinary AttentionStore is isolated too; old histories stay in place.
    store=AttentionStore(STATE/'ui.sqlite')
    server=create_server(store=store,artifact_root=STATE,port=args.port,dev_v2_bridge=bridge(args.port))
    try:server.serve_forever()
    finally:server.server_close()


def status():
    store=DevV2Store(LEDGER);ledger=store.export_ledger(GOAL_ID)
    b=bridge(json.loads((STATE/'prepared.json').read_bytes())['port']) if (STATE/'prepared.json').exists() else None
    requests=[]
    if b:
        for e in b.experiments():
            for c in experiments.snapshot(store,e['experiment_id'])['cells']:
                requests.extend(c.get('planner',{}).get('requests',[]))
    waiting=[r for r in requests if r['request_type']=='approval' and r['state']=='waiting']
    return dict(goal_id=GOAL_ID,resource_ledger=ledger,requests=requests,waiting_for_external_human=waiting,
        real_acceptance='pending; controlled checks do not substitute for provider/native/human evidence')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['prepare','rebind-prepared','start','serve','dispatch','status','stop','reconcile','export'])
    p.add_argument('--memory-url');p.add_argument('--memory-store-id');p.add_argument('--namespace');p.add_argument('--port',type=int,default=8876)
    p.add_argument('--seed',type=int,default=101);p.add_argument('--wait-seconds',type=int,default=60);p.add_argument('--private-env');p.add_argument('--output');a=p.parse_args()
    load_private(a.private_env)
    if a.command in {'prepare','rebind-prepared','start','dispatch','stop','reconcile'}:
        with lifecycle_lock():return execute(a)
    return execute(a)


def execute(a):
    if a.command=='prepare':value=prepare(a)
    elif a.command=='rebind-prepared':value=rebind_prepared()
    elif a.command=='serve':serve(a);return
    elif a.command=='start':
        verify_epoch()
        if a.port==8769:raise ValueError('daily port prohibited')
        if not os.environ.get('ATTENTION_MEMORY_API_KEY') or len(os.environ.get('ATTENTION_HUMAN_APPROVAL_TOKEN',''))<24:raise PermissionError('online Memory and separate external human capability required in environment/private file')
        # Check before spawning; never stop an unrelated listener.
        import socket
        prepared=json.loads((STATE/'prepared.json').read_bytes())
        if a.port!=prepared['port']:raise ValueError('port differs from prepared UI/approval binding')
        with socket.socket() as probe:probe.bind(('127.0.0.1',a.port))
        receipt=STATE/'ui-process.json'
        if receipt.exists() and owner_alive(json.loads(receipt.read_bytes())):raise ValueError('owned UI already active')
        env=execution_environment(ROOT);env['ATTENTION_HUMAN_APPROVAL_TOKEN']=os.environ['ATTENTION_HUMAN_APPROVAL_TOKEN']
        with (STATE/'ui.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,'-B','-m',CLI_MODULE,'serve','--port',str(a.port)],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
        receipt.write_text(json.dumps(process_identity(child.pid),indent=2)+'\n')
        from urllib.request import urlopen
        until=time.monotonic()+15
        while time.monotonic()<until:
            if child.poll() is not None:raise RuntimeError('UI startup failed; see host/ui.log')
            try:
                with urlopen(f'http://127.0.0.1:{a.port}/api/dev-v2/options',timeout=1) as response:
                    if json.load(response).get('bridge_schema')=='main-dev-v3':break
            except OSError:time.sleep(.1)
        else:
            child.terminate();child.wait(timeout=5);raise RuntimeError('owned UI startup timed out and was reaped')
        value=dict(ui_url=f'http://127.0.0.1:{a.port}/ui/',process=process_identity(child.pid),model_dispatch=False)
    elif a.command=='dispatch':
        prepared=json.loads((STATE/'prepared.json').read_bytes())
        from .dev_wait_recovery import reconcile_waits
        b=bridge(prepared['port']);reconcile_waits(b.store,prepared['experiment_id'])
        verify_epoch()
        # Only scheduler gets online/provider credentials; human token is stripped
        # by the bridge. A real action will emit human-review-needed.json.
        value=bridge(prepared['port']).start(prepared['experiment_id'])
    elif a.command=='stop':
        prepared=json.loads((STATE/'prepared.json').read_bytes());b=bridge(prepared['port'])
        for e in b.experiments():experiments.cancel(b.store,e['experiment_id'])
        # Owned schedulers observe cancel and clean their provider/native groups.
        receipts=list((STATE/'artifacts').glob('devexp-*/scheduler-process-*.json'))
        deadline=time.monotonic()+55
        while any(owner_alive(json.loads(p.read_bytes())) for p in receipts) and time.monotonic()<deadline:time.sleep(.2)
        owned=[json.loads(p.read_bytes()) for p in receipts if owner_alive(json.loads(p.read_bytes()))]
        if owned:raise RuntimeError('owned scheduler still cleaning; retained claim/process receipts; do not kill an unrelated process')
        from .dev_wait_recovery import reconcile_waits
        for e in b.experiments():reconcile_waits(b.store,e['experiment_id'])
        receipt=STATE/'ui-process.json'
        if receipt.exists():
            identity=json.loads(receipt.read_bytes())
            if owner_alive(identity):os.kill(identity['pid'],signal.SIGTERM)
        value=dict(stopped=True,remaining_owned_schedulers=owned)
    elif a.command=='reconcile':
        from .dev_wait_recovery import reconcile_waits
        prepared=json.loads((STATE/'prepared.json').read_bytes());b=bridge(prepared['port'])
        value=dict(dispatch=False,waits=reconcile_waits(b.store,prepared['experiment_id']))
    else:value=status()
    if a.output:Path(a.output).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    else:print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
