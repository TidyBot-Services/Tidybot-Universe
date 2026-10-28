"""Operator CLI and isolated family-check adapter; no live work on import."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from attention_memory_service.family import REWRITE_PROMPT, validate_spec, validate_rewrite
from attention_memory_service.positive_controls import LABEL
from attention_memory_service.dev_v2 import encoded, digest
from attention_memory_service.core.store import StateConflictError
from .core.dev_store import DevConflict
from .dev_v2_loop import checked, immutable


def read(client,key,action):
    if hasattr(client,'family_read'):return client.family_read(key,action)
    return client.family.read(key,action)


def write(client,action,**payload):
    if hasattr(client,'family_write'):return client.family_write(action,**payload)
    return client.family.write(action,payload)


def generalize(client,source,*,provider=None,output_dir,clock=time.time,before_call=None):
    """Use Dev's CodexClient, retaining raw provider wire and immutable attempts."""
    kind,mid=source.split(':',1);r=read(client,source,'source')
    prompt=dict(instruction=REWRITE_PROMPT,title=r['title'],guidance=r['guidance'],task_id=r['context']['task_id'])
    directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    for attempt in range(2):
        path=directory/f'rewrite-{attempt}.json'
        if path.exists():rewrite=json.loads(path.read_bytes())
        else:
            if before_call:before_call()
            from .codex_client import CodexClient, MODEL
            wire=[]
            def observe(status,headers,raw):wire.append(dict(http_status=status,body=json.loads(raw)))
            p=provider or CodexClient(response_observer=observe)
            if hasattr(p,'response_observer'):p.response_observer=observe
            started=clock()
            response=p.chat(model=MODEL,messages=[dict(role='system',content=REWRITE_PROMPT),dict(role='user',content=encoded(prompt))],max_tokens=2048,temperature=0,reasoning_effort='high')
            raw=response.artifact()['content'];finished=clock()
            receipt=dict(provider='codex',model=MODEL,prompt=prompt,prompt_sha256=digest(encoded(prompt).encode()),raw_response=raw,response_sha256=digest(raw.encode()),started_at=started,finished_at=finished,provider_response=wire,model_source='codex')
            import re
            content=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip())
            try:parsed=json.loads(content)
            except ValueError:parsed={}
            if not isinstance(parsed,dict):parsed={}
            rewrite=dict(title=parsed.get('title'),guidance=parsed.get('guidance'),receipt=receipt)
            immutable(path,rewrite)
        try:validate_rewrite(r,rewrite)
        except (StateConflictError,ValueError,KeyError,TypeError) as exc:
            if attempt==1:raise DevConflict('Model rewrite rejected after two attempts: '+str(exc)) from exc
            continue
        return write(client,'generalize',source=dict(kind=kind,id=mid),rewrite=rewrite)


def fixture_create(client,spec,*,control_id=None):
    validate_spec(spec)
    if not control_id:raise DevConflict('An already imported positive-control origin ID is required')
    prior=client.positive_control_read(control_id,'record') if hasattr(client,'positive_control_read') else client.positive_controls.read(control_id,'record')
    if not prior or any(prior['spec'][k]!=spec[k] for k in ('origin','context','guidance','title')):raise DevConflict('Fixture must exactly reuse the selected imported control origin and text')
    return write(client,'fixture-create',spec=spec,spec_sha256=validate_spec(spec),**{k:prior[k] for k in ('execution_import_id','catalog_id','descriptor')})


def queue(client,root,spec):
    from .dev_tasks import get_task
    from openarm365_dev.policy_validation import validate_generated_policy
    from openarm365_dev_v2.source_policy import DEV_SOURCE_BYTES
    get_task(spec['target_task_id'])
    for target in spec['targets']:
        baseline=target.get('baseline_source')
        if baseline is None:
            p=Path(get_task(spec['target_task_id']).initial_policy)
            target['baseline_source']=baseline=dict(path=str(p),sha256=digest(p.read_bytes()))
        source=checked(dict(uri=baseline['path'],sha256=baseline['sha256'])).read_text()
        validate_generated_policy(source,config=dict(budget_tier='dev',runner_boundary='dev_loop'))
        if len(source.encode())>DEV_SOURCE_BYTES:raise DevConflict('source_size_limit_exceeded')
    from .memory_cross_seed import register_contract
    runtime=json.loads((Path(root)/'runtime.json').read_bytes())
    candidate=read(client,spec['candidate_id'],'record')
    task=get_task(spec['target_task_id'])
    ctx=dict(candidate['context'],task_id=task.task_id,task_prompt=task.task_prompt,
             task_variant_id=task.official_env+':'+candidate['context']['task_variant_id'].split(':',1)[1])
    for target in spec['targets']:
        register_contract(client,runtime.get('contract_registration_roots',[]),target['contract'],target['seed'],ctx)
    frozen=write(client,'queue-check',spec=spec)
    path=Path(root)/'family-checks'/(spec['family_check_id']+'.json');path.parent.mkdir(parents=True,exist_ok=True)
    immutable(path,frozen)
    return frozen


class FamilyClient:
    family_channel=True
    def __init__(self,client,spec):self.client,self.family_spec=client,spec
    def read(self,action):return read(self.client,self.family_spec['candidate_id'],action)
    def register_dev_v2_catalog(self,**kw):return self.client.register_dev_v2_catalog(**kw)
    def import_dev_v2_execution(self,**kw):return self.client.import_dev_v2_execution(**kw)
    def import_dev_v2_trial(self,**kw):return write(self.client,'trials',**kw)
    def authorize_dev_v2_validation(self,**kw):return write(self.client,'grants',**kw)
    def cross_seed_write(self,route,**kw):return write(self.client,route,**kw)
    def get_memory(self,mid):
        if mid!=self.family_spec['candidate_id']:raise DevConflict('Family adapter candidate mismatch')
        return SimpleNamespace(**self.read('input'))
    def get_plan(self,mid):return dict(policy_id='full_trace_aware_attention_planner',assistance_credits=0)
    def provenance(self,mid):return self.read('provenance')
    def validation_queue(self):return []
    def memory_events(self,**kw):return []
    def cross_seed_status(self,*,memory_id):
        s=self.read('status');return dict(memory_id=memory_id,seeds=s['scenes'],task_trust=s['conditions'])
    def cross_seed_queue(self):
        status=self.read('status')
        if status['status'] in {'revoked','disabled'}:return []
        self.read('input')
        scenes=[r for r in status['scenes'] if r['role']!='pending']
        tested={r['target_seed'] for r in scenes}
        if all(t['seed'] in tested for t in self.family_spec['targets']):return []
        return [dict(memory_id=status['candidate_id'],version=1,title=status['title'],record_kind='family_candidate',
                     source_seed=None,cross_seed_targets=self.family_spec['targets'],seeds=scenes,task_trust=status['conditions'])]


def tick_channel(state,deps,ordinary_tick):
    from .memory_positive_control import SHARED
    from .memory_validation_worker import settings
    active=state.get('family_active')
    if not active and any(state.get(k) for k in ('current','pending','cross_pending','cross_waiting','positive_control_active')):return False
    config=settings(deps.root)
    if not active and not config.get('family_checks',{}).get('enabled',False):return False
    specs=[json.loads(p.read_bytes()) for p in sorted((Path(deps.root)/'family-checks').glob('*.json'))]
    rows=state.setdefault('family_checks',{})
    spec=next((s for s in specs if s['candidate_id']==active),None)
    if spec is None:spec=next((s for s in specs if not rows.get(s['candidate_id'],{}).get('done')),None)
    if spec is None:return False
    mid=spec['candidate_id'];state['family_active']=mid
    row=rows.setdefault(mid,dict(title=spec['family_check_id']));sub=row.setdefault('worker',{})
    sub.update({k:state[k] for k in SHARED if k in state})
    cache=getattr(deps,'family_adapters',None)
    if cache is None:cache={};deps.family_adapters=cache
    if mid not in cache:
        from .memory_family_provision import FamilyProvisioner
        from .memory_validation_runtime import LedgerRunner
        client=FamilyClient(deps.client,spec);original=deps.runner
        provision=FamilyProvisioner(dict(original.provision.config),client=client,clock=original.provision.clock)
        runner=LedgerRunner(client,provision,experiments_root=original.experiments_root,validator_root=original.validator_root,state_root=original.state_root,replay=original.replay,provider=original.provider,runner_factory=original.runner_factory,gpu_meter=original.gpu_meter)
        cache[mid]=(client,runner)
    client,runner=cache[mid]
    if client.read('check')!=spec:raise DevConflict('Queued family spec changed')
    def save(value):
        row['worker']={k:v for k,v in value.items() if k not in SHARED}
        state.update({k:v for k,v in value.items() if k in SHARED});state['status']='Family: '+value.get('status','Queued');deps.save(state)
    scoped=SimpleNamespace(**vars(deps));scoped.client=client;scoped.runner=runner;scoped.save=save
    status=client.read('status')
    if status['status'] in {'revoked','disabled'}:
        if (sub.get('current') or {}).get('arm_id'):runner.pause(sub['current'])
        row.update(done=True,status=status);state.pop('family_active',None);save(sub);return True
    ordinary_tick(sub,scoped)
    if getattr(scoped,'begin_cycle',None):deps.begin_cycle=scoped.begin_cycle
    row['status']=client.read('status')
    if not sub.get('current') and not sub.get('cross_waiting') and not client.cross_seed_queue():
        row['done']=True;state.pop('family_active',None)
    save(sub);return True


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--service-url',default='http://127.0.0.1:8878');p.add_argument('--key-file',required=True)
    p.add_argument('--state-dir',required=True);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('fixture-create');c.add_argument('--spec',required=True);c.add_argument('--reuse-control',required=True)
    c=sub.add_parser('fixture-revoke');c.add_argument('fixture_id');c.add_argument('--reason',required=True)
    c=sub.add_parser('generalize');c.add_argument('--source',required=True)
    c=sub.add_parser('queue-check');c.add_argument('--spec',required=True)
    sub.add_parser('status')
    args=p.parse_args(argv)
    from .memory_service_client import MemoryServiceClient
    client=MemoryServiceClient.offline(args.service_url,key_file=args.key_file)
    if args.command=='fixture-create':result=fixture_create(client,json.loads(Path(args.spec).read_bytes()),control_id=args.reuse_control)
    elif args.command=='fixture-revoke':result=write(client,'fixture-revoke',fixture_id=args.fixture_id,reason=args.reason)
    elif args.command=='queue-check':result=queue(client,args.state_dir,json.loads(Path(args.spec).read_bytes()))
    elif args.command=='generalize':
        from .memory_validation_worker import quota_stop,atomic
        root=Path(args.state_dir);state_path=root/'state.json';state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
        # Operator rewrites use the same quota reader as the worker; no switch changes.
        from .memory_validation_worker import weekly_quota_snapshot,refresh_quota
        deps=SimpleNamespace(root=root,clock=time.time,quota=weekly_quota_snapshot,refresh_quota=refresh_quota)
        def before_call():
            reason=quota_stop(state,deps,lambda:atomic(state_path,state))
            if reason:raise DevConflict(reason)
        version=read(client,args.source,'source')['version']
        # Same worker lock prevents competing state/quota updates. No service or
        # scheduler is stopped by this CLI; an active worker refuses the call.
        import fcntl
        with (root/'worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
            result=generalize(client,args.source,output_dir=root/'family-rewrites'/digest(encoded([args.source,version]).encode()),before_call=before_call)
    else:result=client.family_list()
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
