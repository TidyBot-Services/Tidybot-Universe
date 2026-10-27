"""C18 fake executor on real provisioning, replay, grants and worker transitions."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .autovalidate_fakes import FakeRunner, synthetic_creation, API, IMPORTER, sync_request, atomic
from .cross_seed_fakes import AutomaticCrossFakeRunner
from ..memory_positive_control import ControlClient, queue
from ..memory_validation_runtime import LedgerRunner
from ..memory_validation_worker import tick


class PositiveFake(AutomaticCrossFakeRunner):
    def __init__(self,root,state_root,*,treatments=(False,)*5,**kwargs):
        super().__init__(root,state_root,**kwargs)
        self.treatments=treatments;self.authority=self.service
        self.failed['context']['seed']=101
        self.failed['context']['scene_contract_ref']=self.f.contract
        self.f.write(self.f.root/'failed/execution.json',self.failed['descriptor'])
        spec=dict(control_id='pc-run8',title='Run8 controlled fake',guidance='Rotate the grasp heading +0.3 rad; first lift 45 mm, then 15 mm.',
            origin=dict(experiment_id='failed',cell='failed',version=1),context=self.failed['context'],
            label='test · not counted in results',cross_seed_targets=[dict(seed=int(k),contract=v) for k,v in self.paths.items()])
        state_root.mkdir(parents=True,exist_ok=True);self.spec=spec
        path=state_root/'positive-controls/run8.json';atomic(path,spec);queue(state_root,path)
        def read(mid,action):
            code,value=sync_request(self.app,f'/dev-v2/positive-controls/{mid}/{action}',key=API,importer_key=IMPORTER)
            if code!=200:raise ValueError(value)
            return value
        def write(action,**payload):
            code,value=sync_request(self.app,'/dev-v2/positive-controls/'+action,method='POST',payload=payload,key=API,importer_key=IMPORTER)
            if code!=200:raise ValueError(value)
            return value
        remote=SimpleNamespace(positive_control_read=read,positive_control_write=write,
            positive_control_list=lambda:self.authority.positive_controls.public(),
            register_dev_v2_catalog=lambda **kw:self.register(kw['catalog_id'],kw['catalog']),
            import_dev_v2_execution=self.authority.import_dev_v2_execution)
        self.proxy=ControlClient(remote,spec,self.provision);self.provision.client=self.proxy
        pc=self.authority.positive_controls
        self.service=SimpleNamespace(store=SimpleNamespace(path=self.authority.store.path,get_memory=pc.get_memory),
            authorize_dev_v2_validation=self.proxy.authorize_dev_v2_validation,
            import_dev_v2_execution=self.authority.import_dev_v2_execution,import_dev_v2_trial=self.proxy.import_dev_v2_trial)
        self.origin_runner.client=self.proxy
        self.remote=remote

    def deps(self,root):
        result=FakeRunner.deps(self,root)
        result.client=self.remote;result.positive_adapters={self.spec['control_id']:(self.proxy,self)}
        return result

    def begin(self,note,operation):
        runner=LedgerRunner(self.proxy,self.provision,experiments_root=self.f.root,validator_root=self.f.validator,state_root=self.provision.state_root)
        try:
            with synthetic_creation(self.f),patch('benchmarks.attention_harness.dev_planner_memory.context',lambda *a,**kw:self.failed['context']):
                batch=runner.begin(note,operation)
        finally:runner.pool.shutdown()
        if self.failed_check and self.failed_check[0]=='first-treatment':self.failed_check=(batch['pairs'][0]['treatment_cell'],self.failed_check[1])
        self.batch=batch;self.candidate_id=batch['memory_id'];self.f.budget_override=batch['budget']
        for i,pair in enumerate(batch['pairs']):
            for arm in ('control','treatment'):
                name=pair[arm+'_cell']
                if name in self.f.records:continue
                record=self.f.make(name,failure=arm=='control' or self.treatments[i],completed_calls=1,
                    exposure='none' if arm=='control' else 'candidate_validation',memory_id=None if arm=='control' else self.candidate_id,
                    code=f'def policy(sdk):\n    # {name}\n    pass\n')
                record['context'].update(batch['context'])
                d=record['descriptor'];clean=json.loads(Path(d['validation_input']['uri']).read_bytes());clean['context']=record['context']
                d['validation_input']=self.f.write(Path(d['validation_input']['uri']),clean)
                self.f.write(self.f.root/name/'execution.json',d)
        return batch


def run(root,*,treatments=(False,)*5,cross=True,failed_check=None):
    fake=PositiveFake(root/'fake',root/'worker',treatments=treatments)
    if failed_check:fake.failed_check=failed_check
    atomic(root/'worker/autovalidate.json',dict(enabled=True,daily_run_limit=None,cross_seed=dict(enabled=cross)))
    deps=fake.deps(root/'worker');state={}
    for _ in range(90):
        with synthetic_creation(fake.f),patch('benchmarks.attention_harness.dev_planner_memory.context',lambda *a,**kw:fake.failed['context']):
            tick(state,deps)
        fake.phase='completed'
        record=state.get('positive_controls',{}).get(fake.spec['control_id'],{})
        if record.get('same_done') and (not cross or record.get('cross_done')):break
        assert 'paused:' not in state.get('status',''),state.get('status')
    assert record.get('verdict'),state
    return fake,state,record
