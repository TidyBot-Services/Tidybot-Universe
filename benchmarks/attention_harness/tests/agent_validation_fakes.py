"""Scripted Dev, real controller/adapters, synthetic native owner and runner."""
import json,threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from benchmarks.attention_harness.tests.agent_origin_fakes import build,replay
from benchmarks.attention_harness.tests.test_dev_agent_memory_phase2 import run_origin
from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
from benchmarks.attention_harness.tests.autovalidate_fakes import synthetic_creation,STATE,OBS
from benchmarks.attention_harness.dev_agent_broker import FakeEvaluator
from benchmarks.attention_harness import memory_independent_validator,dev_agent_loop,dev_agent_adapters,dev_agent_isolation,dev_agent_eval,gpu_meter
from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
from benchmarks.attention_harness.dev_memory_validation import policy_input


def fixture(tmp_path,monkeypatch,*,outcome='passed',script=None,prepared=True,models=None,policy_version=None,policy='reactive_help'):
    f=build(tmp_path,monkeypatch,validation=True,models=models,policy_version=policy_version,policy=policy)
    f.native.records['failed']=f.native.records['cell']
    meter=lambda:SimpleNamespace(start=lambda:None,stop=lambda:dict(gpu_seconds=0.,wall_seconds=.1,samples=1))
    monkeypatch.setattr(memory_independent_validator,'GpuMeter',meter)
    monkeypatch.setattr(gpu_meter,'GpuMeter',meter)
    from benchmarks.attention_harness import dev_agent_attention
    def forbidden_helper(*args,**kwargs):raise AssertionError('validation arm constructed an Advisor helper')
    monkeypatch.setattr(dev_agent_attention,'Helper',forbidden_helper)
    monkeypatch.setattr(dev_agent_isolation,'verify_runtime',lambda *a,**kw:None)
    monkeypatch.setattr(dev_agent_isolation,'codex_identity',lambda:dict(offline_fake=True))
    monkeypatch.setattr(dev_agent_loop,'QuotaMonitor',lambda *a,**kw:SimpleNamespace(check=lambda *a:None,event=lambda *a:None))
    monkeypatch.setattr(dev_agent_eval,'Evaluator',lambda *a,**kw:FakeEvaluator())
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(dev_agent_loop.socket,'socket',Listener)
    script=script or ("pass" if outcome=='inconclusive' else "tool('observe'); packet=json.loads(Path('memory/v1.json').read_text()); assert Path('work/main.py').read_bytes()==Path('origin/policy.py').read_bytes(); assert Path('origin/public-feedback.json').exists(); Path('work/main.py').write_text(Path('work/main.py').read_text()+'\\n# fixed\\n' if packet['memory'] else Path('work/main.py').read_text()+'\\n# control\\n'); tool('run','--no-eval'); tool('submit')")
    f.model_choices=[];f.eval_choices=[]
    def session(ws,private,**kw):
        f.model_choices.append(kw)
        value=fake_session(SimpleNamespace(ws=ws,private=private),[script])
        value.model=kw['model'];value.reasoning_effort=kw['reasoning_effort']
        return value
    def evaluator(*a,**kw):
        f.eval_choices.append(kw['model'])
        return FakeEvaluator()
    monkeypatch.setattr(dev_agent_loop,'CodexAgentSession',session)
    monkeypatch.setattr(dev_agent_eval,'Evaluator',evaluator)
    class Owner:
        def __init__(self,**kw):self.deadline=kw['deadline'];self.stopped=False
        def start(self):return dict(scene_condition=dict(matched=True))
        def rpc(self,*a,**kw):return dict(observation=dict(robot0_left_joint_pos=[0.]*7),state_basis='reset_scene',physics_steps=0)
        def stop(self):self.stopped=True
    original_prober=dev_agent_adapters.Prober
    monkeypatch.setattr(dev_agent_adapters,'Prober',lambda *a,**kw:original_prober(*a,owner_factory=Owner,**kw))
    f.executions=[]
    template=json.loads((f.root/'v1/bundle.json').read_bytes())
    class Runner:
        def __init__(self,**kw):self.observer=kw['episode_observer'];self.envelope=kw.get('memory_validation');self.cancel_event=threading.Event()
        def execute(self,request):
            directory=Path(request.artifact_root)/'episode';directory.mkdir()
            config=json.loads(Path(request.config_path).read_bytes())
            public,consumed=policy_input(self.envelope,request) if self.envelope else (None,None)
            fixed=outcome=='passed' and b'# fixed' in Path(request.policy_code_path).read_bytes()
            bundle=deepcopy(template)
            bundle.update(run_id=request.run_id,attempt_id=request.attempt_id,policy_sha256=request.policy_sha256,
                config_sha256=request.config_sha256,status='completed' if fixed else 'failed',native_success=fixed,
                task_id=config['task_id'],seed=config['seed'])
            for label,ref in list(bundle['artifacts'].items()):
                value=json.loads(Path(ref['uri']).read_bytes())
                value.update(run_id=request.run_id,attempt_id=request.attempt_id)
                if 'policy_sha256' in value:value['policy_sha256']=request.policy_sha256
                if 'config_sha256' in value:value['config_sha256']=request.config_sha256
                if label=='trace':
                    value['status']=bundle['status']
                    if fixed:value.pop('error',None)
                    if fixed:value['sdk_events']=[dict(source='sensors',operation='get_observation',status='completed')]*3
                    if consumed:value['memory_validation']=consumed
                if label=='native_result':
                    value.update(evaluated=True,native_success=fixed,status=bundle['status'])
                    bundle['native_evaluator']=value
                name='sandbox_receipt.json' if label=='sandbox_receipt' else label+'.json'
                bundle['artifacts'][label]=f.native.write(directory/name,value)
            for label in ('initial','final'):f.native.write(directory/(label+'_public_observation.json'),OBS)
            f.native.write(directory/'scene-condition.json',dict(state=STATE,validation=bundle['scene_condition']))
            self.observer(directory);f.executions.append(request.attempt_id)
            return bundle
    f.runner=LedgerRunner(f.service,f.provision,experiments_root=f.native.root,validator_root=f.native.validator,
        state_root=f.provision.state_root,replay=lambda *a,**kw:replay(f,*a,**kw),runner_factory=Runner,gpu_meter=f.provision.gpu_seconds)
    if prepared:f.origin=run_origin(f)
    return f
