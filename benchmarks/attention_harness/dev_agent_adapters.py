"""Agent-only collaborators. Constructors do not start any simulator."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import time
from dataclasses import dataclass
from .formal_runner_boundary import FormalRunRequest
from .dev_agent_workspace import atomic_write, encode
from .dev_agent_diagnostics import project_planner_result, public_observation


PRESPAWN_MARKER='.prespawn'


@dataclass(frozen=True)
class AgentValidationRunRequest(FormalRunRequest):
    worker_arm: dict | None = None
    agent_configuration: dict | None = None


class Executor:
    def __init__(self, config, *, sim_python, runner_factory=None, validation_arm=None, agent_configuration=None):
        from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
        if config.get('budget_tier')!='dev' or config.get('runner_boundary')!='dev_loop':raise ValueError('dev_tier_required')
        self.config=config;self.python=sim_python;self.runner_factory=runner_factory or OpenArm365DevRunnerV2
        self.runner=None;self.running=False
        self.validation_arm=validation_arm;self.agent_configuration=agent_configuration
        self.execution_identity = dict(run_id='run:agent', attempt_id='attempt:agent:0')
        self.pending=None;self.spawner=None;self.prespawn_log=None;self.claiming=None
        self.cancel_requested=threading.Event()

    # Try speed-up 2 (user OK 11-14), local runs only: start a FRESH worker for the next execution
    # while Dev thinks. It is used only for exactly that execution directory and config; anything
    # else (wrong directory, dead worker, Memory validation, close) discards it. Never reused.
    PRESPAWN_SECONDS=3*3600.
    def prespawn(self,directory,*,log=None):
        from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
        if self.pending is not None or self.running:return False
        if getattr(self,'memory_validation',None) is not None or self.runner_factory is not OpenArm365DevRunnerV2:return False
        from openarm365_dev_v2.owner import NativeOwnerV2
        import uuid
        directory=Path(directory);self.prespawn_log=log
        self.clear_stale_prespawn(directory)
        if directory.exists():return False
        directory.mkdir(parents=True);(directory/PRESPAWN_MARKER).write_text('pre-started worker; not yet an execution\n')
        episode=directory/('openarm365-dev-'+uuid.uuid4().hex[:12]);episode.mkdir()
        config=encode(self.config);(episode/'config.json').write_bytes(config)
        cancel=threading.Event()
        owner=NativeOwnerV2(python=self.python,config=episode/'config.json',evidence=episode,
            deadline=time.monotonic()+self.PRESPAWN_SECONDS,cancel_check=cancel.is_set,agent_mode=True,prespawn=True)
        pending=dict(directory=directory.resolve(),episode=episode,owner=owner,config_sha256=hashlib.sha256(config).hexdigest(),
                     spawn_started=time.monotonic(),prespawned_at=None,cancel=cancel)
        if self.spawner is None:
            # The worker binds PR_SET_PDEATHSIG to the spawning thread: spawn from one long-lived thread.
            self.spawner=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='exec-prespawn')
        def start():
            owner.start();pending['prespawned_at']=time.monotonic()
        pending['future']=self.spawner.submit(start);self.pending=pending
        self._log('started',directory=str(directory))
        return True

    def _log(self,event,**fields):
        if self.prespawn_log is None:return
        try:
            with open(self.prespawn_log,'a') as f:f.write(json.dumps(dict(event=event,at=time.time(),**fields),sort_keys=True)+'\n')
        except OSError:pass

    def discard_prespawn(self,reason):
        pending,self.pending=self.pending,None
        if pending is None:return None
        owner=pending['owner']
        # Cancel a start still in progress first (its read loop checks this flag), so that stop()
        # always runs after the process exists and can never be overtaken by a late Popen.
        pending['cancel'].set()
        try:pending['future'].result(timeout=60)
        except BaseException:pass
        try:cleanup=owner.stop('prespawn_discarded')
        except Exception as exc:cleanup=dict(error=repr(exc))
        # Keep its evidence out of the execution folder (that folder must hold only real executions).
        directory=pending['directory'];moved=None
        if directory.is_dir():
            target=directory.parent.parent/'prespawn-discarded'/(directory.name+'-'+str(time.time_ns()))
            target.parent.mkdir(parents=True,exist_ok=True)
            try:directory.rename(target);moved=str(target)
            except OSError:moved=None
        self._log('discarded',reason=reason,directory=str(directory),moved_to=moved,
                  cleanup=cleanup if isinstance(cleanup,dict) else None)
        return reason

    def clear_stale_prespawn(self,directory):
        """A marked folder left by a pre-started worker of an earlier (crashed or restarted) process."""
        directory=Path(directory)
        if not (directory/PRESPAWN_MARKER).is_file():return None
        if self.pending is not None and directory.resolve()==self.pending['directory']:return None
        target=directory.parent.parent/'prespawn-discarded'/(directory.name+'-stale-'+str(time.time_ns()))
        target.parent.mkdir(parents=True,exist_ok=True);directory.rename(target)
        self._log('discarded',reason='stale_from_earlier_process',directory=str(directory),moved_to=str(target))
        return target

    def claim_prespawn(self,directory):
        """The pre-started worker for exactly this directory, or None (and any other one discarded)."""
        pending=self.pending
        if pending is None:return None
        if Path(directory).resolve()!=pending['directory']:
            self.discard_prespawn('directory_mismatch');return None
        return pending

    def bind_execution(self, cell, version, execution_id):
        # Formal versions share the one-call identity convention. No-eval work
        # has a disjoint attempt ID and cannot collide with a formal version.
        self.execution_identity = dict(run_id='run:' + cell,
            attempt_id=f'attempt:{cell}:{version-1}' if version is not None else f'probe:{cell}:{execution_id}')

    def execute(self, source, directory, *, deadline):
        self.running=True;self.cancel_requested.clear()
        try:return self._execute(source, directory, deadline=deadline)
        finally:self.running=False

    def _execute(self, source, directory, *, deadline):
        from .formal_runner_boundary import FormalRunRequest
        directory=Path(directory)
        self.result=None;self.episode=None;self.execution_usage=None;self.native_result_ref=None
        prespawned=self.pending if self.pending is not None and Path(directory).resolve()==self.pending['directory'] else None
        if self.pending is not None and prespawned is None:self.discard_prespawn('directory_mismatch')
        atomic_write(directory,'policy.py',source);atomic_write(directory,'config.json',encode(self.config))
        if prespawned is not None:
            self.pending=None;reason=None;self.claiming=prespawned
            try:prespawned['future'].result(timeout=max(1.,min(600.,deadline-time.monotonic())))
            except BaseException as exc:reason='start_failed: '+type(exc).__name__
            finally:self.claiming=None
            if self.cancel_requested.is_set():
                self.pending=prespawned;self.discard_prespawn('cancelled');raise InterruptedError('execution cancelled before start')
            if reason is None and (getattr(self,'memory_validation',None) is not None):reason='memory_validation'
            if reason is None and hashlib.sha256((directory/'config.json').read_bytes()).hexdigest()!=prespawned['config_sha256']:reason='config_changed'
            if reason is None and (prespawned['owner'].process is None or prespawned['owner'].process.poll() is not None):reason='worker_exited'
            if reason is not None:
                self.pending=prespawned;self.discard_prespawn(reason)
                # The discarded worker's folder was moved away; recreate this execution's folder.
                Path(directory).mkdir(parents=True,exist_ok=True)
                atomic_write(directory,'policy.py',source);atomic_write(directory,'config.json',encode(self.config))
                prespawned=None
            else:
                (Path(directory)/PRESPAWN_MARKER).unlink(missing_ok=True);self._log('used',directory=str(directory))
        episodes=[]
        validation=getattr(self,'memory_validation',None) is not None
        self.runner=self.runner_factory(service_source_root=None,sim_python=self.python,
            sim_source_root=None,task_source_root=None,asset_manifest=None,agent_mode=True,
            episode_observer=episodes.append,**({'memory_validation':self.memory_validation} if validation else {}))
        seconds=min(1920.,deadline-time.monotonic())
        request_type=AgentValidationRunRequest if validation else FormalRunRequest
        request=request_type(suite='robocasa365',task_id=self.config['task_id'],seed=self.config['seed'],
            policy_code_path=directory/'policy.py',policy_sha256=hashlib.sha256(source).hexdigest(),
            config_sha256=hashlib.sha256((directory/'config.json').read_bytes()).hexdigest(),
            config_path=directory/'config.json',artifact_root=directory,overall_deadline_seconds=seconds,
            attempt_deadline_seconds=seconds,sdk_call_limit=320,**self.execution_identity,
            **(dict(worker_arm=self.validation_arm,agent_configuration=self.agent_configuration) if validation else {}))
        if validation:
            from .gpu_meter import GpuMeter
            meter=GpuMeter();meter.start()
            try:result=self.runner.execute(request)
            finally:self.gpu_measurement=meter.stop()
        elif prespawned is not None:
            try:result=self.runner.execute(request,prespawned=prespawned)
            finally:
                try:prespawned['owner'].stop('normal_cleanup')
                except Exception:pass
        else:result=self.runner.execute(request)
        self.result=result
        if result.get('error'):
            atomic_write(directory,'controller-error.json',encode(dict(error=result['error'])))
        episode=Path(episodes[-1]) if episodes else None
        self.episode=episode
        self.recording=result.get('recording',{})
        # Only the trusted controller consumes this private reference.
        self.native_result_ref=(result.get('artifacts') or {}).get('native_result')
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
        self.execution_usage=dict(sdk_calls=(receipt.get('sdk_call_budget') or {}).get('dispatched'),
            control_steps=receipt.get('control_steps'),wall_seconds=receipt.get('elapsed_seconds'))
        public['state_log']=[]
        physics=episode/'native-physics.jsonl' if episode else None
        if physics and physics.is_file():public['state_log']=[public_observation(json.loads(line)) for line in physics.read_text().splitlines()]
        from .dev_agent_recording import recording_frames
        public['frames']=recording_frames(episode,self.config['control_freq']) if episode and ((episode/'recording_frames').is_dir() or (episode/'advisor_replay.mp4').is_file()) else []
        return public

    def cancel(self):
        self.cancel_requested.set()
        claiming=getattr(self,'claiming',None)
        if claiming is not None:claiming['cancel'].set()
        if self.runner:self.runner.cancel_event.set()
    def stopped(self, wait=60.):
        # The broker reuses no simulator after a timeout until this returns True.
        until=time.monotonic()+wait
        while self.running and time.monotonic()<until:time.sleep(.1)
        return not self.running


class Prober:
    def __init__(self, config, *, sim_python, private_dir, owner_factory=None, run_seconds=21600):
        from openarm365_dev_v2.owner import NativeOwnerV2
        if config.get('budget_tier')!='dev' or config.get('runner_boundary')!='dev_loop':raise ValueError('dev_tier_required')
        self.config=config;self.python=sim_python;self.private=Path(private_dir)
        self.owner_factory=owner_factory or NativeOwnerV2;self.owner=None;self.cancelled=threading.Event();self.index=0
        # Local runs: the owner lives at most 6 hours. Remote time-share (run_seconds=None) has no time limit; each probe still has its own deadline.
        from .dev_agent_ledger import UNLIMITED_REMAINING
        self.run_deadline=time.monotonic()+(UNLIMITED_REMAINING if run_seconds is None else run_seconds)
        # The worker binds PR_SET_PDEATHSIG to the thread that spawned it, and the
        # broker runs each probe on a fresh thread. Spawn from one long-lived thread
        # so the warm owner survives between probes (10-28 real run).
        self.spawner=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='probe-owner-spawn')

    def validate(self,op,payload):
        from openarm365_dev_v2.agent_probe import validate
        validate(op,payload,config=getattr(self,'config',None))

    def probe(self,op,payload,*,deadline):
        self.validate(op,payload);self.start_failed=False
        if self.owner is None:
            self.index+=1;directory=self.private/('probe-owner-%04d'%self.index);directory.mkdir(parents=True)
            atomic_write(directory,'config.json',encode(self.config))
            atomic_write(directory,'recording_context.json',encode(dict(run_id='run:agent-probe',attempt_id='attempt:probe:0',
                camera_name='robot0_agentview_left',started_monotonic=time.monotonic(),origin='agent_probe',dev_version='0')))
            self.cancelled.clear()
            self.owner=self.owner_factory(python=self.python,config=directory/'config.json',evidence=directory,
                deadline=self.run_deadline,cancel_check=self.cancelled.is_set,agent_mode=True)
            try:
                ready=self.spawner.submit(self.owner.start).result()
                if ready.get('scene_condition') and not ready['scene_condition']['matched']:raise ValueError('scene_contract_mismatch')
            except BaseException:
                self.start_failed=True;self.close();raise
        # Owner's deadline is per probe. Recreate if prior deadline is already exhausted.
        self.owner.deadline=deadline
        return self.owner.rpc('owner','agent_probe',kwargs=dict(op=op,payload=payload))

    @property
    def cold(self):return self.owner is None
    def cancel(self):self.cancelled.set();self.close()
    def close(self):
        if self.owner:
            self.owner.stop();self.owner=None


class FakeProber:
    @staticmethod
    def validate(op,payload):
        from openarm365_dev_v2.agent_probe import validate
        validate(op,payload)
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
