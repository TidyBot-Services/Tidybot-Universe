"""Native-process substitutes for exercising the production runner and policy worker."""
import json
from pathlib import Path
from openarm365_dev_v2 import runner, agent_sandbox, extra_views
from benchmarks.attention_harness.tests.autovalidate_fakes import OBS, STATE


def install(f, monkeypatch, *, synthetic_registry=True):
    # The phase-3 fixture has a synthetic task/SDK registry. Its existing
    # synthetic_creation context replaces native config/lock validation; route
    # the runner's import alias to that same fixture validator. execute(), the
    # envelope verifier, SDK, agent sandbox and evidence export remain real.
    from openarm365_dev_v2 import dev_config
    def validate_config(c):
        dev_config.validate_dev_config(c)
        return c
    if synthetic_registry:monkeypatch.setattr(runner, 'validate_config', validate_config)
    f.real_requests=[]
    f.policy_contexts=[]

    class Owner:
        def __init__(self, **kw):
            self.ledger=[];self.sent={};self.unresolved=set()
            self.config=json.loads(Path(kw['config']).read_bytes())
            self.fixed=b'# fixed' in (Path(kw['evidence'])/'policy.py').read_bytes()
            f.executions.append(json.loads((Path(kw['evidence'])/'recording_context.json').read_bytes())['attempt_id'])
        def start(self):
            from openarm365_dev_v2.scene_contract import validate
            condition=(validate(self.config['scene_contract'],self.config['task_id'],STATE,self.config['seed'])
                if self.config.get('scene_contract') else None)
            return dict(initial_observation=OBS,scene_state=STATE,
                scene_condition=condition,capabilities={})
        def rpc(self, group, operation, args=(), kwargs=None, **kw):
            if group=='owner':
                if operation=='observe':return dict(OBS,sequence=len(self.ledger))
                if operation=='evaluate':return dict(evaluated=True,native_success=self.fixed)
                raise AssertionError(operation)
            row=dict(group=group,operation=operation,request_id=str(len(self.ledger)),
                ok=self.fixed or group=='sensors',sequence=len(self.ledger)+1)
            self.ledger.append(row)
            if not row['ok']:
                row['error']='RuntimeError: PlanningRejected'
                raise RuntimeError('PlanningRejected')
            return OBS if group=='sensors' else dict(ok=True,rejected=False)
        def stop(self, *a):
            return dict(leader_reaped=True,process_group_gone=True,planner_process_groups_gone=True)

    monkeypatch.setattr(runner,'NativeOwner',Owner)
    monkeypatch.setattr(runner,'probe_sandbox',lambda:dict(controlled_test=True))
    monkeypatch.setattr(runner,'finalize_recording',lambda *a:{})
    monkeypatch.setattr(extra_views,'finalize_extra_views',lambda *a:None)
    execute=agent_sandbox.execute_agent_policy
    def policy(**kw):
        f.policy_contexts.append(kw['context'])
        # Real subprocess/worker/RPC and source validation, without unavailable
        # bubblewrap namespaces. This is the sandbox's explicit test transport.
        return execute(**kw,unsafe_test_transport=True)
    monkeypatch.setattr(agent_sandbox,'execute_agent_policy',policy)
    # Observe construction without substituting the runner or its execute method.
    def factory(**kw):
        value=runner.OpenArm365DevRunnerV2(**kw)
        f.real_requests.append(value)
        return value
    f.runner.runner_factory=factory
    return f


SCRIPT=("packet=json.loads(Path('memory/v1.json').read_text()); "
    "Path('work/main.py').write_text('from robot_sdk import sensors, left_arm\\n'"
    "+'print(context.get(\"memory_validation\"))\\n'"
    "+'sensors.get_observation()\\nleft_arm.move_delta(0,0,.01)\\n'"
    "+('# fixed\\n' if packet['memory'] else '# control\\n')); tool('submit')")
