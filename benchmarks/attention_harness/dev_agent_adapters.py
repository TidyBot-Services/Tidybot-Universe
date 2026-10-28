"""Agent-only collaborators. Constructors do not start any simulator."""
import hashlib
import json
from pathlib import Path
import threading
import time
from .dev_agent_workspace import atomic_write, encode
from .dev_agent_diagnostics import project_planner_result, public_observation


class Executor:
    def __init__(self, config, *, sim_python, runner_factory=None):
        from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
        if config.get('budget_tier')!='dev' or config.get('runner_boundary')!='dev_loop':raise ValueError('dev_tier_required')
        self.config=config;self.python=sim_python;self.runner_factory=runner_factory or OpenArm365DevRunnerV2
        self.runner=None

    def execute(self, source, directory, *, deadline):
        from .formal_runner_boundary import FormalRunRequest
        directory=Path(directory)
        atomic_write(directory,'policy.py',source);atomic_write(directory,'config.json',encode(self.config))
        episodes=[]
        self.runner=self.runner_factory(service_source_root=None,sim_python=self.python,
            sim_source_root=None,task_source_root=None,asset_manifest=None,agent_mode=True,
            episode_observer=episodes.append)
        seconds=min(1920.,deadline-time.monotonic())
        request=FormalRunRequest(suite='robocasa365',task_id=self.config['task_id'],seed=self.config['seed'],
            policy_code_path=directory/'policy.py',policy_sha256=hashlib.sha256(source).hexdigest(),
            config_sha256=hashlib.sha256((directory/'config.json').read_bytes()).hexdigest(),
            config_path=directory/'config.json',artifact_root=directory,overall_deadline_seconds=seconds,
            attempt_deadline_seconds=seconds,sdk_call_limit=320,run_id='run:agent',attempt_id='attempt:agent:0')
        result=self.runner.execute(request)
        if result.get('error'):
            atomic_write(directory,'controller-error.json',encode(dict(error=result['error'])))
        episode=Path(episodes[-1]) if episodes else None
        self.episode=episode
        self.recording=result.get('recording',{})
        # Only the trusted controller consumes this private reference.
        self.native_result_ref=result.get('artifacts',{}).get('native_result')
        # Never load the private evaluator file. Only enumerated public inputs.
        def read(name, default):
            path=episode/name if episode else None
            return path.read_text() if path and path.is_file() else default
        trace=json.loads(read('trace.json','{}'))
        diagnostics=[]
        path=episode/'planner/v2_client.jsonl' if episode else None
        if path and path.is_file():
            for line in path.read_text().splitlines():
                row=json.loads(line)
                if row.get('path','').startswith('/plan'):
                    diagnostics.append(project_planner_result(row.get('response',{})))
        public=dict(status=result['status'],error=bool(result.get('error')),
            trace=trace.get('sdk_events',[]),stdout=read('policy-stdout.txt',''),stderr=read('policy-stderr.txt',''),
            diagnostics=diagnostics,initial_observation=json.loads(read('initial_public_observation.json','{}')),
            final_observation=json.loads(read('final_public_observation.json','{}')))
        # Preserve full per-physics-state public evidence independently of action events.
        receipt=json.loads(read('sandbox_receipt.json','{}'))
        self.execution_usage=dict(sdk_calls=receipt.get('sdk_call_budget',{}).get('dispatched'),
            control_steps=receipt.get('control_steps'),wall_seconds=receipt.get('elapsed_seconds'))
        public['state_log']=[]
        physics=episode/'native-physics.jsonl' if episode else None
        if physics and physics.is_file():public['state_log']=[public_observation(json.loads(line)) for line in physics.read_text().splitlines()]
        from .dev_agent_recording import recording_frames
        public['frames']=recording_frames(episode,self.config['control_freq']) if episode and ((episode/'recording_frames').is_dir() or (episode/'advisor_replay.mp4').is_file()) else []
        return public

    def cancel(self):
        if self.runner:self.runner.cancel_event.set()


class Prober:
    def __init__(self, config, *, sim_python, private_dir, owner_factory=None):
        from openarm365_dev_v2.owner import NativeOwnerV2
        if config.get('budget_tier')!='dev' or config.get('runner_boundary')!='dev_loop':raise ValueError('dev_tier_required')
        self.config=config;self.python=sim_python;self.private=Path(private_dir)
        self.owner_factory=owner_factory or NativeOwnerV2;self.owner=None;self.cancelled=threading.Event();self.index=0;self.run_deadline=time.monotonic()+21600

    @staticmethod
    def validate(op,payload):
        from openarm365_dev_v2.agent_probe import validate
        validate(op,payload)

    def probe(self,op,payload,*,deadline):
        self.validate(op,payload)
        if self.owner is None:
            self.index+=1;directory=self.private/('probe-owner-%04d'%self.index);directory.mkdir(parents=True)
            atomic_write(directory,'config.json',encode(self.config))
            atomic_write(directory,'recording_context.json',encode(dict(run_id='run:agent-probe',attempt_id='attempt:probe:0',
                camera_name='robot0_agentview_left',started_monotonic=time.monotonic(),origin='agent_probe',dev_version='0')))
            self.cancelled.clear()
            self.owner=self.owner_factory(python=self.python,config=directory/'config.json',evidence=directory,
                deadline=self.run_deadline,cancel_check=self.cancelled.is_set,agent_mode=True)
            ready=self.owner.start()
            if ready.get('scene_condition') and not ready['scene_condition']['matched']:
                self.close();raise ValueError('scene_contract_mismatch')
        # Owner's deadline is per probe. Recreate if prior deadline is already exhausted.
        self.owner.deadline=deadline
        return self.owner.rpc('owner','agent_probe',kwargs=dict(op=op,payload=payload))

    def cancel(self):self.cancelled.set();self.close()
    def close(self):
        if self.owner:
            self.owner.stop();self.owner=None


class FakeProber:
    validate=staticmethod(Prober.validate)
    def __init__(self):self.calls=0;self.steps=0
    def probe(self,op,payload,*,deadline):
        self.calls+=1
        result=dict(state_basis='reset_scene',physics_steps=0)
        if payload.get('held_object') or payload.get('attachment'):return dict(result,unsupported_scope='held_object')
        if op=='observe':return dict(result,observation={'robot0_left_joint_pos':[0.]*7})
        if op=='find_objects':return dict(result,objects=[dict(name='cup',position=[.1,.2,.3],frame='world')])
        return dict(result,planning_accepted=op!='ik_check',rejected=op=='ik_check',
                    diagnostics=project_planner_result({'reason':'official_ik_non_success'} if op=='ik_check' else {}))
    def close(self):pass
    def cancel(self):pass


class FakeExecutor:
    def __init__(self, *, unsafe_test_transport=False):
        self.calls=0;self.unsafe_test_transport=unsafe_test_transport;self.cancelled=threading.Event()
    def execute(self,source,directory,*,deadline):
        from openarm365_dev_v2.agent_sandbox import execute_agent_policy
        from openarm365_dev_v2.agent_sdk import build_sdk
        self.calls+=1;events=[]
        class Owner:
            moves=0
            def rpc(self,group,operation,args,kwargs):
                if group=='sensors':return {'robot0_left_joint_pos':[0.]*7}
                self.moves+=1
                return (dict(ok=False,rejected=True,reason='official_ik_non_success',diagnostics={})
                        if self.moves==1 else dict(ok=True))
        outcome=execute_agent_policy(code=source.decode(),sdk=build_sdk(Owner(),events.append),context={},
            deadline=deadline,stderr_path=directory/'policy-stderr.txt',max_sdk_calls=320,
            cancel_check=self.cancelled.is_set,unsafe_test_transport=self.unsafe_test_transport)
        return dict(status=outcome.status,error=outcome.error,trace=events,diagnostics=[{}],
            stdout=(directory/'policy-stdout.txt').read_text(),stderr=(directory/'policy-stderr.txt').read_text(),
            initial_observation={'joint_pos':[0.]*14},final_observation={'joint_pos':[.1]*14})
    def cancel(self):self.cancelled.set()
