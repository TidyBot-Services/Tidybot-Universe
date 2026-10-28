"""r4 (10-29): any exception inside one request becomes that request's response.

Stages: validation, acceptance, dispatch, owner start, projection, history write,
execution, Eval, help boundary, manual help. Only BrokerInvariant ends the run.
"""
import json
import time
import pytest
from benchmarks.attention_harness import dev_agent_broker as module
from benchmarks.attention_harness.dev_agent_adapters import FakeExecutor, FakeProber
from benchmarks.attention_harness.dev_agent_broker import FakeEvaluator
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.tests.test_dev_agent_d1 import broker, request, settled
from benchmarks.attention_harness.tests.test_dev_agent_d1 import workspace  # noqa: F401
from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
from benchmarks.attention_harness.tests.test_dev_agent_d2 import ws  # noqa: F401

TARGET=dict(side='left',position=[.5,-2.4,.95],quaternion_xyzw=[0,0,0,1])
PAYLOADS={'observe':{},'find_objects':{},'ik_check':dict(target=TARGET),
          'plan_dry_run':dict(action=dict(group='left_arm',operation='move_delta',args=[0,0,.01],kwargs={}))}
R4=dict(target=dict(position=[0.497,-2.377,0.96],quaternion_xyzw=[0.07736454723838088,0.7601851488556195,
        -0.38165449620884756,-0.5200702950693594],side='left'),base_offset=dict(dx=0,dy=0.25,dyaw=0))


class Boom(FakeProber):
    def __init__(self,stage):super().__init__();self.stage=stage
    @staticmethod
    def validate(op,payload):FakeProber.validate(op,payload)
    def probe(self,op,payload,*,deadline):
        if self.stage=='dispatch':raise KeyError('synthetic dispatch')
        if self.stage=='owner':self.start_failed=True;raise RuntimeError('Native startup failed: synthetic')
        return super().probe(op,payload,deadline=deadline)


@pytest.mark.parametrize('op',sorted(PAYLOADS))
@pytest.mark.parametrize('stage',['validation','dispatch','owner','projection','history'])
def test_every_probe_stage_answers_the_request(workspace,monkeypatch,op,stage):  # noqa: F811
    prober=Boom(stage)
    if stage=='validation':
        monkeypatch.setattr(prober,'validate',lambda *a:(_ for _ in ()).throw(LookupError('synthetic validation')))
    if stage=='projection':
        monkeypatch.setattr(module,'public_observation',lambda v:(_ for _ in ()).throw(TypeError('synthetic projection')))
    b=broker(workspace,prober=prober)
    if stage=='history':
        real=b._history
        monkeypatch.setattr(b,'_history',lambda kind,row:(_ for _ in ()).throw(OSError('synthetic history')) if kind=='probes' else real(kind,row))
    try:
        value=b.accept(request('a',op=op,payload=PAYLOADS[op]))
        if stage=='validation':
            assert value['status']=='rejected' and 'synthetic validation' in value['validation_error']
            assert b.budget.counts['probes']==0
        else:
            value=settled(b,'a')
            assert value['status']=='completed' and value['result']['probe_error']['error_class'] in ('KeyError','RuntimeError','TypeError','OSError')
            assert b.budget.counts['probes']==1
        until=time.monotonic()+2
        while b.active is not None and time.monotonic()<until:time.sleep(.01)
        assert not b.quarantined and b.active is None
        if stage!='owner':
            # Next request is served (owner stage: second failed start is the invariant).
            prober.stage=None;prober.validate=FakeProber.validate;b.accept(request('b',op='observe'));assert settled(b,'b')['status']=='completed'
    finally:b.close()


def test_acceptance_stage_failure_rejects_only_that_request(workspace,monkeypatch):  # noqa: F811
    b=broker(workspace)
    real=b.budget.charge
    monkeypatch.setattr(b.budget,'charge',lambda **kw:(_ for _ in ()).throw(ZeroDivisionError('synthetic charge')))
    try:
        value=b.accept(request('a'))
        assert value['status']=='rejected' and 'synthetic charge' in value['validation_error']
        monkeypatch.setattr(b.budget,'charge',real)
        b.accept(request('b'));assert settled(b,'b')['status']=='completed' and not b.quarantined
    finally:b.close()


def test_r4_out_of_limit_base_offset_is_clear_rejection(workspace):  # noqa: F811
    b=broker(workspace)
    try:
        # User decision 10-29: the r4 hypothetical dy=0.25 probe is now within the 0.6 m probe limit.
        b.accept(request('r4',op='ik_check',payload=R4));assert settled(b,'r4')['status']=='completed'
        until=time.monotonic()+2
        while b.active is not None and time.monotonic()<until:time.sleep(.01)
        action=dict(action=dict(group='base',operation='move_delta',args=[0,.25,0],kwargs={}))
        value=b.accept(request('r4b',op='plan_dry_run',payload=action))
        assert value['status']=='rejected' and 'per-call base limit' in value['validation_error']
        assert b.budget.counts['probes']==1 and not b.quarantined
    finally:b.close()


class Raising(FakeExecutor):
    def execute(self,*a,**kw):self.calls+=1;raise RuntimeError('synthetic execution')


def test_execution_exception_is_that_executions_result(workspace):  # noqa: F811
    b=broker(workspace,executor=Raising())
    try:
        b.accept(request('x',op='run',payload=dict(entry='work/main.py')))
        value=settled(b,'x')
        assert value['status']=='completed' and value['result']['execution_error']['error_class']=='RuntimeError'
        assert value['result']['outcome']['completed'] is False and not b.quarantined
        assert 'synthetic execution' in (workspace.ws/'executions'/value['execution_id']/'stderr.txt').read_text()
        b.accept(request('y',op='submit',payload=dict(entry='work/main.py')))
        until=time.monotonic()+5
        while b.records['y']['response']['status'] not in ('completed','needs_review') and time.monotonic()<until:time.sleep(.02)
        value=b.records['y']['response']
        assert value['status']=='completed' and value['result']['eval'] is not None and not b.quarantined
    finally:b.close()


class Helper:
    def __init__(self):self.asks=0
    def ask(self,payload,*,deadline):self.asks+=1;raise RuntimeError('synthetic advisor')
    def boundary(self,value,*,deadline):raise RuntimeError('synthetic boundary')
    def cancel(self):pass


def test_help_failures_answer_the_request(workspace):  # noqa: F811
    b=broker(workspace,helper=Helper(),executor=FakeExecutor(unsafe_test_transport=True))
    try:
        b.accept(request('s',op='submit',payload=dict(entry='work/main.py')))
        until=time.monotonic()+5
        while b.records['s']['response']['status'] not in ('completed','needs_review') and time.monotonic()<until:time.sleep(.02)
        value=b.records['s']['response']
        assert value['status']=='completed' and value['result']['help']['status']=='help_error' and not b.quarantined
        b.accept(request('h',op='ask_help',payload=dict(question='?')))
        value=settled(b,'h')
        assert value['status']=='completed' and value['result']['status']=='help_error' and not b.quarantined
    finally:b.close()


def test_drive_survives_every_failure_kind(ws,monkeypatch):  # noqa: F811
    class Mixed(FakeProber):
        def probe(self,op,payload,*,deadline):
            self.calls+=1
            if self.calls==2:raise AttributeError("'NoneType' object has no attribute 'get'")
            return super().probe(op,payload,deadline=deadline)
    r4=json.dumps(R4['target']);offset=json.dumps(R4['base_offset'])
    script=("tool('observe');tool('ik_check','--target',%r);tool('ik_check','--target',%r,'--base-offset',%r);"
            "tool('find_objects');tool('run','--no-eval','--entry','work/main.py');time.sleep(20)")%(r4,r4,offset)
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,[script,'pass'])
    result=drive(ws,session,ledger,executor=Raising(),prober=Mixed(),evaluator=FakeEvaluator())
    assert result['reason']=='agent_idle',result
    used=ledger.snapshot()['used']
    assert used['read_only_probes']==4 and used['moving_executions']==1


def test_only_hash_bound_sdk_doc_is_verbatim(tmp_path):
    import hashlib
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt
    from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG
    good=dict(CONFIG,dev_sdk_prompt_sha256=hashlib.sha256(dev_sdk_prompt(CONFIG).encode()).hexdigest())
    text=(Workspace(tmp_path/'a').prepare({'task_prompt':'x'},good).ws/'docs/sdk.md').read_text()
    assert 'open()/close()' in text and 'Shape notes' in text and 'withheld' not in text
    bad=dict(CONFIG,dev_sdk_prompt_sha256='0'*64)
    assert (Workspace(tmp_path/'b').prepare({'task_prompt':'x'},bad).ws/'docs/sdk.md').read_text().startswith('[artifact withheld')


def test_run_derived_files_keep_their_screening(workspace):  # noqa: F811
    from benchmarks.attention_harness.dev_agent_diagnostics import screen_text
    from benchmarks.attention_harness.dev_agent_eval import feedback
    phrase='Gripper open()/close(); dict.get/keys'
    assert screen_text(phrase).startswith('[artifact withheld')          # validation/probe errors
    assert feedback('native_success printed by '+phrase)=='[withheld]'  # stdout/Eval/help: unchanged rule
    class Leaky(FakeExecutor):
        def execute(self,source,directory,*,deadline):
            return dict(status='completed',stdout='object_in_sink True\n',stderr='',trace=[])
    b=broker(workspace,executor=Leaky())
    try:
        b.accept(request('x',op='run',payload=dict(entry='work/main.py')));value=settled(b,'x')
        assert (workspace.ws/'executions'/value['execution_id']/'stdout.txt').read_text()=='[withheld]'
    finally:b.close()


def test_fatal_sdk_event_has_runner_shape():
    # r5: "Native joint tracking exceeds 4mrad endpoint bound" -> agent event error was the
    # string 'fatal_sdk_error'; runner.py's e.get('error',{}).get('type') then raised and the
    # native evaluator never ran (completion_evidence_unavailable -> fatal_error).
    from openarm365_dev_v2.agent_sdk import build_sdk
    events=[]
    class Owner:
        def rpc(self,*a,**k):raise RuntimeError('Native joint tracking exceeds 4mrad endpoint bound')
    with pytest.raises(RuntimeError):build_sdk(Owner(),events.append).left_arm.move_delta(0,0,-.04)
    error=events[-1]['error']
    assert events[-1]['status']=='failed' and error['type']=='RuntimeError' and '4mrad' in error['message']
    assert not any(e.get('error',{}).get('type')=='OutcomeUnknown' for e in events)
    from openarm365_dev.owner import OutcomeUnknown
    class Lost:
        def rpc(self,*a,**k):raise OutcomeUnknown('Native response channel closed')
    with pytest.raises(OutcomeUnknown):build_sdk(Lost(),events.append).left_arm.move_delta(0,0,-.04)
    assert any(e.get('error',{}).get('type')=='OutcomeUnknown' for e in events)
