"""Cross-tree synthetic evidence runner; service tests supply sealed fake bytes."""
import json,time
from pathlib import Path
from types import SimpleNamespace
from attention_memory_service.dev_v2 import digest,encoded
from attention_memory_service.family import LABEL
from benchmarks.attention_harness.memory_family import generalize,FamilyClient
from benchmarks.attention_harness.memory_validation_worker import atomic,tick
# The required service checkout is on PYTHONPATH for the M-11 test command.
from tests.test_family import setup,candidate,proxy,target,prepare,import_trial,MODEL,TARGET,real_source


class Provider:
    def __init__(self):self.calls=[]
    def chat(self,**kw):
        self.calls.append(kw)
        content=encoded(dict(title='Grasp heading',guidance='Adjust the grasp heading and lift in small increments.'))
        return SimpleNamespace(artifact=lambda:dict(content=content))


class Runner:
    def __init__(self,f,service):self.f,self.service=f,service;self.starts=[];self.pauses=[];self.batches={}
    def origin_needed(self,note):return None
    def admit_seed(self,note,seed,path):return dict(seed=seed,contract=dict(uri=path,sha256='0'*64))
    def begin_cross(self,note,operation,target,*,pairs=None):
        if operation in self.batches:return self.batches[operation]
        seed=target['seed'];f=self.f;p=proxy(f,self.service);f.task=TARGET
        if pairs is None:
            args,scene=globals()['target'](f,p,seed,failure=False)
            self.batches['scene-'+str(seed)]=scene
            current=dict(probe_args=args,probe_cell=args['execution_import_id'],pairs=[])
        else:
            rows=[dict(pair_id=str(i),control_cell=f'c{seed}-{i}',treatment_cell=f't{seed}-{i}') for i in range(pairs)]
            plan=p.cross_seed.plan(memory_id='m1',target_seed=seed,pairs=rows,dev_model=MODEL)
            for pair in rows:
                for arm in ('control_cell','treatment_cell'):
                    name=pair[arm];treatment=arm=='treatment_cell'
                    f.make(name,seed=seed,completed_calls=1,scene=self.batches['scene-'+str(seed)],exposure='candidate_validation' if treatment else 'none',memory_id=f.mid if treatment else None,code=f'def policy(sdk):\n    # {name}\n    pass\n')
            current=dict(plan=plan,pairs=rows)
        current.update(kind='cross_seed',stage='probe' if pairs is None else 'pairs',target_seed=seed,target=target,note=note,operation=operation,memory_id=f.mid,memory_version=1,experiment_id=operation)
        self.batches[operation]=current;return current
    def start(self,current,arm):self.starts.append(arm)
    def poll(self,current):return 'completed'
    def pause(self,current):self.pauses.append(current['operation'])
    def resume(self,current):pass
    def finish(self,current):
        p=proxy(self.f,self.service)
        if current['stage']=='probe':return dict(status='probe',probe=p.cross_seed.probe(**current['probe_args']))
        name=current['arm_id'];prepare(self.f,p,current['plan'],name);import_trial(p,name)
        return dict(status='passed',trial_import_id='trial-'+name)


def run(root,*,real=False,monkeypatch=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if real:f,s=real_source(root,monkeypatch);source='memory:m1'
    else:f,s,*_=setup(root);source='fixture:fixture-one'
    provider=Provider();c=generalize(s,source,provider=provider,output_dir=root/'rewrite')
    f.mid=c['candidate_id'];f.treatment_label=c['treatment_label']
    spec=dict(family_check_id='fake-family',candidate_id=f.mid,target_task_id=TARGET,label='Family check' if real else LABEL,
        targets=[dict(seed=seed,contract=str(root/f'contract-{seed}'/'contract.json'),baseline_source=dict(path=str(f.root/'failed/policy.py'),sha256=f.records['failed']['record']['source']['sha256'])) for seed in (114,124)])
    s.family.queue_check(spec=spec)
    state_root=root/'worker';atomic(state_root/'family-checks/fake.json',spec)
    atomic(state_root/'autovalidate.json',dict(enabled=True,family_checks=dict(enabled=True)))
    client=FamilyClient(s,spec);runner=Runner(f,s);state={}
    deps=SimpleNamespace(root=state_root,client=s,runner=runner,family_adapters={f.mid:(client,runner)},
        clock=time.time,save=lambda state:atomic(state_root/'state.json',state),activity=lambda current:None,
        quota=lambda:dict(used_percent=10,observed_at=time.time(),resets_at=time.time()+86400),refresh_quota=lambda:None)
    for _ in range(40):
        tick(state,deps)
        if state.get('family_checks',{}).get(f.mid,{}).get('done'):break
        if 'paused:' in state.get('status',''):raise AssertionError(state['status'])
    assert state['family_checks'][f.mid]['done'],state
    result=s.family.status(f.mid)
    assert state['runs_today']==14 and len(runner.starts)==14 and len(provider.calls)==1
    assert result['conditions']['neutral_only'] and len(result['scenes'])==2
    if real:
        assert result['status']=='family_trusted'
        for i,failed in enumerate([True,True,False,False,True]):s.family.record_use(candidate_id=f.mid,use_id='later-'+str(i),signature_present=failed)
        assert s.family.get(f.mid)['status']=='candidate'
    else:
        assert result['decision']['decision']=='would be trusted for the family'
        s.family.fixture_revoke(fixture_id='fixture-one',reason='fake E2E finished')
        assert s.family.get(f.mid)['status']=='revoked'
    return s,state,runner
