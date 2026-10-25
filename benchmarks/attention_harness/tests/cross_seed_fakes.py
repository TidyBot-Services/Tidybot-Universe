"""Fake execution only; real provisioning, importer routes, validator and service."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from .autovalidate_fakes import (ProvisionedFakeRunner,FakeRunner,synthetic_creation,STATE,OBS,SIGNATURE,
                                fingerprint,validate_arm,API,IMPORTER,sync_request,outcome,CHANGED)
from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled,atomic
from benchmarks.attention_harness.memory_cross_seed import admit


class CrossFakeRunner(ProvisionedFakeRunner):
    def __init__(self,root,state_root,*,roles=None,execution_settings=None):
        super().__init__(root,state_root,execution_settings=execution_settings)
        self.roles=roles or {114:'transfer',104:'neutral'}
        self.paths={}
        for seed in self.roles:
            ref=self.f.write(root/'contracts'/str(seed)/'scene.json',dict(schema='attentionbench.public-scene-contract.v2',tolerance=1e-6,tasks={'pick':{'seeds':{str(seed):{'normalized_sha256':fingerprint(STATE,True)}}}}))
            self.paths[str(seed)]=ref['uri']
        self.provision.config['contract_registration_roots']=[str(root/'contracts')]
        def write(route,**payload):
            code,value=sync_request(self.app,'/dev-v2/cross-seed/'+route,method='POST',payload=payload,key=API,importer_key=IMPORTER)
            if code!=200:raise ValueError(value)
            return value
        self.proxy=SimpleNamespace(**{name:getattr(self.service,name) for name in (
            'provenance','get_memory','get_plan','validation_queue','evaluate','decision','record_dev_v2_pair','record_dev_v2_failed_check','authorize_dev_v2_validation','import_dev_v2_execution','import_dev_v2_trial','memory_events')},
            register_dev_v2_catalog=lambda **kw:self.register(kw['catalog_id'],kw['catalog']),
            cross_seed_write=write,cross_seed_queue=self.service.cross_seed.queue,cross_seed_status=self.service.cross_seed.status)
        self.provision.client=self.proxy
        self.cross_batches=[]

    def deps(self,root):
        result=super().deps(root);result.client=self.proxy;return result

    def admit_seed(self,note,seed,path):return admit(self.provision,note,seed,path)

    def begin_cross(self,note,operation,target,*,pairs=None):
        runner=LedgerRunner(self.proxy,self.provision,experiments_root=self.f.root,validator_root=self.f.validator,state_root=self.provision.state_root)
        try:
            with synthetic_creation(self.f):batch=runner.begin_cross(note,operation,target,pairs=pairs)
        finally:runner.pool.shutdown()
        self.cross_batches.append(batch)
        raw=Path(batch['config']['uri']).read_text();config=json.loads(raw)
        old_contract=self.f.contract;old_config=self.f.config_overrides;old_write=self.f.write
        def write(path,value):return old_write(path,raw if path.name=='config.json' else value)
        try:
            self.f.contract=target['contract'];self.f.config_overrides=config;self.f.budget_override=batch['budget']
            with patch.object(self.f,'write',write):
                for key in batch['cells']:
                    if key in self.f.records:continue
                    arm=next((a for p in batch['pairs'] for a in ('control','treatment') if p[a+'_cell']==key),'probe')
                    failure=self.roles[target['seed']]=='transfer' and arm!='treatment'
                    record=self.f.make(key,seed=target['seed'],failure=failure,completed_calls=1,exposure='candidate_validation' if arm=='treatment' else 'none',memory_id=note['memory_id'] if arm=='treatment' else None,
                        code=Path(batch['origin']['uri']).read_text() if arm=='probe' else f'def policy(sdk):\n    # {key}\n    pass\n')
                    record['context'].update(batch['context']);record['descriptor']['context']=record['context']
                    clean=json.loads(Path(record['descriptor']['validation_input']['uri']).read_bytes());clean['context']=record['context']
                    record['descriptor']['validation_input']=old_write(Path(record['descriptor']['validation_input']['uri']),clean)
                    old_write(self.f.root/key/'execution.json',record['descriptor'])
        finally:self.f.contract=old_contract;self.f.config_overrides=old_config
        return batch

    def finish(self,current):
        if current.get('kind')!='cross_seed':return super().finish(current)
        key=current['arm_id']
        if key in self.finished:return self.finished[key]
        target=current['target'];old=self.f.contract;self.f.contract=target['contract']
        try:
            if current['stage']!='probe':return FakeRunner.finish(self,current)
            r=self.f.records[key];d=r['descriptor'];catalog=self.f.seal()[key]
            result=validate_arm(experiment_dir=self.f.root,experiments_root=self.f.root,cell=key,version=1,execution_import_id=key,
                bundle_ref=r['verdict']['bundle'],config_ref=d['config'],source_ref=r['record']['source'],grant=None,permission_scope=d['permission_scope'],executor_catalog=catalog,
                contract=target['contract'],validator_root=self.f.validator,replay=lambda *a:(OBS,STATE if self.roles[target['seed']]!='could_not_finish' else {}),
                failure_signature=SIGNATURE,budget=current['budget'],developer_model='gpt-6-astra',gpu_seconds=0,ledger_ref=d['ledger'],probe_input=d['validation_input'],origin_source_sha256=current['origin']['sha256'])
            assert result['status'] in {'passed','failed'},result
            self.register(key,catalog);self.register('independent-'+key,result['catalog'])
            self.service.import_dev_v2_execution(import_id=key,catalog_id=key,descriptor=key+'/execution.json')
            probe=self.proxy.cross_seed_write('probes',memory_id=current['memory_id'],target_seed=target['seed'],execution_import_id=key,catalog_id='independent-'+key,descriptor='verdict.json',scene_contract_ref=target['contract'])
            self.finished[key]=dict(status='probe',probe=probe);return self.finished[key]
        finally:self.f.contract=old

    def promote_origin(self):
        deps=self.deps(self.provision.state_root);set_enabled(deps.root,True);state={}
        for _ in range(30):
            tick(state,deps);self.phase='completed'
            if state.get('results'):break
        assert state['results'][0]['status']=='trusted',state['status']
        self.origin_state=state
        return self.service.cross_seed.queue()[0]

    def enable_cross(self):
        atomic(self.provision.state_root/'autovalidate.json',dict(enabled=True,cross_seed=dict(enabled=True,candidate_seeds=list(self.roles),contracts=self.paths)))

    def demote_task(self,mid):
        ctx=dict(self.failed['context'],seed=114,scene_contract_ref=self.cross_batches[0]['target']['contract'])
        failure=json.dumps(dict(schema_version='attentionbench.dev-public-feedback.v1',execution_status='failed',public_error=SIGNATURE['public_error_class'],action_trace=[dict(source='left_arm',operation='move_delta',status='failed')]))
        for i in range(5):outcome(self.service,mid,i,failure if i>=2 else CHANGED,ctx=ctx)
        assert self.service.decision(mid)['trust_scope']=='scene_scoped'
        assert self.service.get_memory(mid).status.value=='trusted'


def lifecycle(root):
    fake=CrossFakeRunner(root/'fake',root/'worker');note=fake.promote_origin();fake.enable_cross()
    # Start a new validation day; original scene checks used ten runs yesterday.
    state={};deps=fake.deps(root/'worker');atomic(root/'worker'/'state.json',state)
    for _ in range(60):
        tick(state,deps);fake.phase='completed'
        if fake.service.decision(note['memory_id'])['trust_scope']=='task_scoped':break
    assert fake.service.decision(note['memory_id'])['trust_scope']=='task_scoped',state.get('status')
    assert state['runs_today']==18
    # Exercise the main-side reader against service-authored trust markers.
    from benchmarks.attention_harness import dev_planner_memory as memory
    from benchmarks.attention_harness.memory_layers import label
    ctx=fake.cross_batches[0]['context']
    def retrieve(context,*,now,match_levels):
        code,rows=sync_request(fake.app,'/retrieve',method='POST',key=API,payload=dict(context=context,now=now,match_levels=match_levels))
        assert code==200
        return rows
    gateway=SimpleNamespace(store_id=lambda:'controlled-test',retrieve_entries=retrieve,list_dev_v2_memories=fake.service.list_dev_v2_memories,
                            authorize_dev_use=fake.service.authorize_dev_use)
    with patch.object(memory,'context',lambda *a:ctx),patch.object(memory,'memory_scope',lambda *a:{'level':'scene'}):
        reader=memory.DevMemory(gateway,dict(store_id='controlled-test',namespace=ctx['namespace']),{},layered=True)
        packet=reader.packet(development_id='cross-seed-read:v1',source=Path(fake.cross_batches[0]['origin']['uri']).read_text())
    assert len(packet['entries'])==1 and packet['entries'][0]['trusted_here'] is True
    assert packet['excluded_from_results'] is False
    assert label(packet['entries'][0])=='Trusted for this task: '
    fake.demote_task(note['memory_id'])
    return fake,state


from .autovalidate_fakes import AutomaticOriginFakeRunner


class AutomaticCrossFakeRunner(CrossFakeRunner,AutomaticOriginFakeRunner):
    """Both queues share the automatically sealed provisional origin receipt."""
