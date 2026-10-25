"""Offline-only validation grant and actual native policy input consumption."""
import hashlib
import json
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_v2_loop import immutable, checked, job_for


def prepare_validation(store,cell,version,config,context,client,memory_id,exposure,directory,*,origin_source_sha256=None,target_seed=None):
    """Operator invokes only after independent scene prerequisites and plan pass.

    Does not run an evaluator, mint a budget or approve a motion. Online planner
    code has no route to this function or the importer client.
    """
    store.start_cell_clock(cell)
    c=store.cell(cell);record=next(v for v in c['versions'] if v['version']==version)
    if job_for(c,version,'execute'):raise DevConflict('validation input must precede execution reservation')
    config_ref={'uri':str(Path(c['artifact_dir'])/'config.json'),'sha256':record['config_sha256']}
    if json.loads(checked(config_ref).read_bytes())!=config:raise DevConflict('validation config drift')
    grant=client.authorize_dev_v2_validation(memory_id=memory_id,cell=cell,version=version,seed=config['seed'],
        context=context,policy_sha256=record['source']['sha256'],config_sha256=record['config_sha256'],exposure=exposure,
        **({'origin_source_sha256':origin_source_sha256} if origin_source_sha256 is not None else {}),
        **({'target_seed':target_seed} if target_seed is not None else {}))
    value=dict(memory_ids=[memory_id] if exposure=='candidate_validation' else [],exposure=exposure,
        context=context,policy_sha256=record['source']['sha256'],config_sha256=record['config_sha256'],validation_grant_id=grant['grant_id'])
    guidance=client.get_memory(memory_id).guidance if value['memory_ids'] else None
    ref=immutable(Path(directory)/'memory-validation-input.json',value)
    frozen=immutable(Path(directory)/'memory-validation-grant.json',grant)
    envelope=dict(input=ref,grant=frozen,guidance=guidance)
    # Immutable evidence plus exactly one pre-dispatch event across restarts.
    payload=dict(cell=cell,version=version,validation_input=ref,validation_grant_id=grant['grant_id'],source=record['source'],config=config_ref)
    with store.tx() as db:
        rows=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='memory_validation_dispatch'")]
        prior=[p for p in rows if p['cell']==cell and p['version']==version]
        if prior and prior!=[payload]:raise DevConflict('validation dispatch binding conflict')
        if not prior:store.event(db,'memory_validation_dispatch',payload)
    return envelope


def policy_input(envelope,request):
    """Trusted runner checks the original grant/input, then supplies public text.

    No native result, validator score, importer or operator capability is passed
    into the sandbox. This pathway is explicitly offline candidate validation.
    """
    value=json.loads(checked(envelope['input']).read_bytes());grant=json.loads(checked(envelope['grant']).read_bytes());b=grant['binding']
    expected=dict(policy_sha256=request.policy_sha256,config_sha256=request.config_sha256,seed=request.seed)
    if any(b[k]!=v for k,v in expected.items()) or request.run_id!='run:'+b['cell'] or request.attempt_id!=f"attempt:{b['cell']}:{b['version']-1}":raise DevConflict('offline validation belongs to another native execution')
    ids=[b['memory_id']] if b['exposure']=='candidate_validation' else []
    exact=dict(memory_ids=ids,exposure=b['exposure'],context=b['context'],policy_sha256=request.policy_sha256,
        config_sha256=request.config_sha256,validation_grant_id=grant['grant_id'])
    if value!=exact:raise DevConflict('offline validation exposure changed')
    guidance=envelope['guidance']
    if (ids and (not isinstance(guidance,str) or not guidance.strip())) or (not ids and guidance is not None):raise DevConflict('actual validation guidance required')
    public=dict(memory_ids=ids,exposure=b['exposure'],guidance=guidance)
    receipt=dict(validation_grant_id=grant['grant_id'],input_sha256=envelope['input']['sha256'],memory_ids=ids,
        exposure=b['exposure'],memory_version=b['memory_version'] if ids else None,
        guidance_sha256=hashlib.sha256(guidance.encode()).hexdigest() if ids else None,consumed_before_policy=True)
    return public,receipt


def main():
    import argparse
    import os
    from .dev_integration import LEDGER, GOAL_ID
    from .dev_ledger import DevV2Store
    from .dev_loop import process_identity
    from .dev_planner_memory import context
    from .memory_service_client import MemoryServiceClient
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','dispatch'])
    p.add_argument('--cell',required=True);p.add_argument('--version',type=int,required=True)
    p.add_argument('--memory-id',required=True);p.add_argument('--base-url',required=True);p.add_argument('--namespace',required=True)
    p.add_argument('--exposure',choices=['none','candidate_validation'],required=True)
    p.add_argument('--scene-prerequisites',required=True,help='independent accepted scene evidence JSON, not a bypass flag')
    a=p.parse_args()
    # No trial starts while the known D3 dependency remains unresolved. Require
    # original independent replay states and the frozen contract, and compare
    # actual bytes locally as well as later in the Service import.
    pre=json.loads(Path(a.scene_prerequisites).read_bytes())
    from openarm365_dev_v2.scene_contract import validate
    for ref in pre['scene_replay_states']:
        state=json.loads(checked(ref).read_bytes())
        if not validate(pre['scene_contract'],pre['task_id'],state,pre.get('seed'))['matched']:raise DevConflict('D3 scene prerequisites failed; no validation dispatch')
    if len(pre['scene_replay_states'])!=2 or pre['scene_replay_states'][0]['uri']==pre['scene_replay_states'][1]['uri']:raise DevConflict('two independent original scene replays required')
    s=DevV2Store(LEDGER);c=s.cell(a.cell)
    with s.tx() as db:
        if s.cell_goal(db,c)!=GOAL_ID:raise DevConflict('validation must use the existing authorized goal ledger')
    config=json.loads((Path(c['artifact_dir'])/'config.json').read_bytes())
    if config.get('scene_contract')!=pre['scene_contract'] or config['task_id']!=pre['task_id']:raise DevConflict('trial config differs from accepted scene prerequisites')
    online=os.environ.get('ATTENTION_MEMORY_API_KEY');importer=os.environ.get('ATTENTION_MEMORY_IMPORTER_KEY')
    if not online or not importer or online==importer:raise PermissionError('distinct offline importer capability required')
    experiment=__import__('benchmarks.attention_harness.dev_experiment',fromlist=['acquire'])
    owner=process_identity();old_state=c['state']
    if c.get('owner') and __import__('benchmarks.attention_harness.dev_loop',fromlist=['owner_alive']).owner_alive(c['owner']):raise DevConflict('cell already has a live executor')
    e=experiment.acquire(s,c['experiment_id'],owner)
    if e['state']=='needs_review':raise DevConflict('unresolved work; do not redispatch')
    s.update_cell(a.cell,state='running',owner=owner)
    directory=Path(c['artifact_dir'])/f'v{a.version}'
    try:
        s.start_cell_clock(a.cell)
        client=MemoryServiceClient(a.base_url,api_key=online,importer_key=importer)
        if a.command=='prepare':
            envelope=prepare_validation(s,a.cell,a.version,config,context(config,a.namespace),client,a.memory_id,a.exposure,directory)
            immutable(directory/'memory-validation-envelope.json',envelope)
        else:
            envelope=json.loads((directory/'memory-validation-envelope.json').read_bytes())
            grant=json.loads(checked(envelope['grant']).read_bytes());b=grant['binding']
            if a.memory_id!=b['memory_id'] or a.exposure!=b['exposure'] or context(config,a.namespace)!=b['context']:raise DevConflict('validation invocation differs from original grant')
            candidate=client.get_memory(a.memory_id)
            if getattr(candidate.status,'value',candidate.status)!='candidate' or candidate.version!=b['memory_version'] or (envelope['guidance'] is not None and envelope['guidance']!=candidate.guidance):raise DevConflict('validation candidate revoked or changed')
            plan=client.get_plan(a.memory_id)
            if hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()!=b['plan_sha256']:raise DevConflict('validation plan changed')
            record=next(v for v in c['versions'] if v['version']==a.version)
            from .dev_v2_loop import execute
            execute(s,a.cell,a.version,record,config,owner,directory,None,memory_validation=envelope)
    finally:
        jobs=s.cell(a.cell)['jobs'];unresolved=any(j['state'] in {'claimed','needs_review'} for j in jobs)
        s.update_cell(a.cell,owner=None,state='needs_review' if unresolved else old_state)
        with s.tx() as db:
            e=s.get(db,'experiments',c['experiment_id']);e.update(owner=None,state='needs_review' if unresolved else 'planned');s.put(db,'experiments',c['experiment_id'],e)


if __name__=='__main__':main()
