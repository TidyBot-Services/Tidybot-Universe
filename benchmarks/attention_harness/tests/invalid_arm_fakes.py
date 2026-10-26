"""C17 synthetic provider/execution; real runtime, ledger, exporter and service."""
import json
from concurrent.futures import Future
from contextlib import contextmanager,ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from .autovalidate_fakes import ProvisionedFakeRunner,synthetic_creation,STATE,OBS,SIGNATURE
from ..dev_v2_loop import immutable,checked,job_for
from ..memory_validation_runtime import LedgerRunner
from ..memory_validation_worker import tick,set_enabled

SOURCE='from robot_sdk import sensors\nsensors.get_observation()\n'


class ImmediatePool:
    def submit(self,fn,*args):
        f=Future()
        try:f.set_result(fn(*args))
        except BaseException as exc:f.set_exception(exc)
        return f
    def shutdown(self,*args,**kwargs):pass


@contextmanager
def scenario(root,invalid=(('treatment',1),),*,infra=False):
    fake=ProvisionedFakeRunner(root/'evidence',root/'worker')
    class Client:
        def __getattr__(self,name):return getattr(fake.service,name)
        def register_dev_v2_catalog(self,**kw):fake.register(kw['catalog_id'],kw['catalog'])
    runner=LedgerRunner(Client(),fake.provision,experiments_root=fake.f.root,
        validator_root=fake.f.validator,state_root=root/'worker',replay=lambda *a:(OBS,STATE),gpu_meter=lambda *a:0)
    runner.pool.shutdown();runner.pool=ImmediatePool()
    generated=[];executed=[];invalid=set(invalid)

    def provider(store,key,version,kind,messages,owner,directory,client=None):
        from ..core.dev_store import DevConflict
        cell=store.cell(key)
        existing=job_for(cell,version,'model')
        if existing and existing['state']=='settled':
            receipt=existing['receipt']
            return json.loads(checked(receipt['response']).read_bytes())['content'],receipt['request'],receipt['response'],receipt['usage']
        if infra:raise DevConflict('provider transport unavailable')
        arm=next(r['validation_arm'] for r in json.loads(checked(store_experiment(store,cell)['manifest']).read_bytes())['cells'] if r['key']==key)
        # Apply faults to the first logical pair only; all later pairs are ordinary.
        pair=next(r['validation_pair'] for r in json.loads(checked(store_experiment(store,cell)['manifest']).read_bytes())['cells'] if r['key']==key)
        bad=(pair==0 and (arm,version) in invalid) or (pair,arm,version) in invalid
        source=SOURCE+('#'+('x'*18000) if bad else f'# fresh {version}\n')
        content=json.dumps(dict(source=source,reason='controlled_test'))
        usage=dict(total_tokens=7,prompt_tokens=4,completion_tokens=3,codex_usage=dict(input_tokens=4,output_tokens=3))
        request=immutable(directory/'model-input.json',dict(model_source='codex',model='gpt-6-astra',messages=messages))
        response=immutable(directory/'model-output.json',dict(content=content,usage=usage,model_source='codex',model='gpt-6-astra'))
        wire=immutable(directory/'model-wire.json',dict(http_status=200,request_id='fake',body=dict(model='gpt-6-astra',object='chat.completion',usage=usage,choices=[dict(finish_reason='stop',message=dict(role='assistant',content=content))])))
        job=store.reserve(key,version,'model',dict(dev_calls=1,provider_tokens=7,input_tokens=4,output_tokens=3,model_seconds=120),model_name='gpt-6-astra')
        store.claim(job['job_id'],owner)
        store.settle(job['job_id'],dict(dev_calls=1,provider_tokens=7,input_tokens=4,output_tokens=3,model_seconds=1),dict(request=request,response=response,provider_response=wire,usage=usage))
        generated.append((key,version,arm,bad))
        return content,request,response,usage

    def execute(store,key,version,record,config,owner,directory,*args,memory_validation=None):
        from openarm365_dev_v2.scene_contract import validate
        from attention_memory_service.dev_v2 import public_trace_projection
        c=store.cell(key);grant=json.loads(checked(memory_validation['grant']).read_bytes());b=grant['binding']
        control=b['exposure']=='none'
        binding=dict(run_id='run:'+key,attempt_id=f'attempt:{key}:{version-1}',suite='robocasa365',task_id=config['task_id'],seed=config['seed'],policy_sha256=record['source']['sha256'],config_sha256=record['config_sha256'])
        trace=dict(binding,status='completed',sdk_events=[dict(source='sensors',operation='get_observation',status='completed')])
        if control:
            trace['error']=SIGNATURE['public_error_class'];trace['sdk_events'].append(dict(source='left_arm',operation='move_delta',status='failed'))
        trace['memory_validation']=dict(validation_grant_id=grant['grant_id'],input_sha256=memory_validation['input']['sha256'],memory_ids=[] if control else [b['memory_id']],exposure=b['exposure'],memory_version=None if control else b['memory_version'],guidance_sha256=None if control else b['guidance_sha256'],consumed_before_policy=True)
        native=dict(run_id=binding['run_id'],attempt_id=binding['attempt_id'],status='completed',evaluated=True,native_success=False)
        short={k:binding[k] for k in ('run_id','attempt_id')}
        artifacts=dict(trace=immutable(directory/'trace.json',trace),native_result=immutable(directory/'native.json',native),
            safety=immutable(directory/'safety.json',dict(short,source='native_per_physics_step_monitor',monitor_available=True,unsafe_attempts=0,events=[],violations=[])),
            sandbox_receipt=immutable(directory/'sandbox.json',dict(short,sdk_call_budget=dict(dispatched=1),control_steps=1,elapsed_seconds=1)))
        condition=validate(config['scene_contract'],config['task_id'],STATE,config['seed'])
        immutable(directory/'scene-condition.json',dict(state=STATE,validation=condition))
        bundle=immutable(directory/'bundle.json',dict(binding,schema_version='attentionbench.formal-runner-result.v1',channel='dev_v2',formal_eligible=False,dev_version=version,policy_id=c['policy_id'],config_lock=dict(uri=str(Path(c['artifact_dir'])/'config.json'),sha256=record['config_sha256']),status='completed',native_success=False,native_evaluator=native,infrastructure_failure=False,outcome_unknown=False,sandbox=dict(process_isolated=True,sdk_rpc_only=True,deadline_enforced=True,action_cancellation_verified=True),scene_condition=condition,artifacts=artifacts,artifact_dir=str(directory)))
        feedback=immutable(directory/'public-feedback.json',public_trace_projection(trace))
        job=store.reserve(key,version,'execute',dict(executions=1,execution_seconds=505,sdk_calls=80,control_steps=2400));store.claim(job['job_id'],owner)
        store.settle(job['job_id'],dict(executions=1,execution_seconds=1,sdk_calls=1,control_steps=1),dict(bundle=bundle,trace=artifacts['trace'],native_result=artifacts['native_result'],public_feedback=feedback,execution_status='completed'))
        executed.append((key,version))

    from .. import memory_validation_generation as generation,dev_v2_loop,dev_planner_memory as exporter
    # Only synthetic SDK/context identity and execution/provider boundaries are replaced.
    with synthetic_creation(fake.f),ExitStack() as stack:
        stack.enter_context(patch.object(generation,'provider_call',provider))
        stack.enter_context(patch.object(generation,'model_messages',lambda config,previous,feedback,attention,remaining,**kw:[dict(role='system',content='Controlled test Dev'),dict(role='user',content=json.dumps(dict(previous_complete_source=previous,public_feedback=feedback,attention_control=attention,remaining_budget=remaining,source_size_limit_bytes=16384)))]))
        stack.enter_context(patch.object(dev_v2_loop,'execute',execute))
        stack.enter_context(patch.object(exporter,'sdk_identity',lambda config:fake.f.sdk))
        stack.enter_context(patch.object(exporter,'context',lambda *a,**kw:fake.failed['context']))
        deps=fake.deps(root/'worker');deps.runner=runner;set_enabled(deps.root,True)
        yield SimpleNamespace(fake=fake,runner=runner,deps=deps,state={},generated=generated,executed=executed)
    runner.pool.shutdown()


def store_experiment(store,cell):
    with store.tx() as db:return store.get(db,'experiments',cell['experiment_id'])


def complete(case):
    for _ in range(40):
        tick(case.state,case.deps)
        if case.state.get('results'):return case.state['results'][0]
        if 'paused:' in case.state['status']:
            for f in case.runner.futures.values():f.result()
            raise AssertionError(case.state['status'])
    raise AssertionError(case.state['status'])
