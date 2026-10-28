"""r5 (10-29): no per-version step may end the run.

drive() is driven by a scripted Dev that submits v1, then idles. A fault is
injected at one per-version step at a time; the run must reach the next turn and
end agent_idle. Only FATAL_CAUSES may end a run early.
"""
import hashlib
import json
import pytest
from benchmarks.attention_harness import dev_agent_broker, dev_agent_loop
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_broker import FakeEvaluator
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_loop import drive, FATAL_CAUSES
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.tests.test_dev_agent_d2 import Executor, fake_session
from benchmarks.attention_harness.tests.test_dev_agent_d2 import ws  # noqa: F401

SCRIPT=["tool('submit','--entry','work/main.py')","pass"]


class Boom(Exception):pass


def boom(*a,**kw):raise Boom('synthetic step fault')


class R5Replay(Executor):
    """Exact r5 v1 shape: a native RuntimeError on move_delta, then evaluated=false."""
    def execute(self,source,directory,*,deadline):
        self.calls+=1
        data=json.dumps(dict(evaluated=False,status='failed',native_success=False,reason='not_started')).encode()
        path=directory/'native_result.json';path.write_bytes(data)
        self.native_result_ref=dict(uri=str(path),sha256=hashlib.sha256(data).hexdigest())
        event=dict(source='left_arm',operation='move_delta',status='failed',
                   arguments=dict(args=[0,0,-.04],kwargs={}),
                   error={'type':'RuntimeError','message':'Native joint tracking exceeds 4mrad endpoint bound'})
        return dict(status='failed',error='policy_error',stdout='descending\n',
                    stderr='RuntimeError: Native joint tracking exceeds 4mrad endpoint bound\n',
                    trace=[dict(source='sensors',operation='get_observation',status='completed',result={}),event],
                    state_log=[],diagnostics=[])


class RaisingExecutor(Executor):
    def execute(self,*a,**kw):self.calls+=1;raise RuntimeError('synthetic execution crash')


class Unreadable(Executor):
    def execute(self,*a,**kw):
        result=super().execute(*a,**kw);self.native_result_ref=dict(uri='/nonexistent',sha256='0'*64);return result


class BadEval(FakeEvaluator):
    def evaluate(self,*a,**kw):return 'not a verdict'


class RaisingEval(FakeEvaluator):
    def evaluate(self,*a,**kw):raise RuntimeError('synthetic eval crash')


class Helper:
    def __init__(self,mode):self.mode=mode
    def boundary(self,value,*,deadline):
        if self.mode=='raise':raise RuntimeError('synthetic help crash')
        return 'not a help record'
    def ask(self,payload,*,deadline):return dict(status='policy_denied')
    def cancel(self):pass


def run(ws,monkeypatch,*,executor=None,evaluator=None,helper=None,patch=None):  # noqa: F811
    if patch:patch(monkeypatch)
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,SCRIPT)
    result=drive(ws,session,ledger,executor=executor or Executor(),prober=FakeProber(),
                 evaluator=evaluator or FakeEvaluator(),helper=helper)
    return result,session,ledger


STEPS={
    'execution_raises':dict(executor=RaisingExecutor()),
    'r5_replay_native_unevaluated':dict(executor=R5Replay()),
    'completion_unreadable':dict(executor=Unreadable()),
    'eval_raises':dict(evaluator=RaisingEval()),
    'eval_bad_value':dict(evaluator=BadEval()),
    'help_boundary_raises':dict(helper=Helper('raise')),
    'help_boundary_bad_value':dict(helper=Helper('bad')),
    'export_cut_raises':dict(patch=lambda m:m.setattr(dev_agent_loop,'freeze_origin',boom)),
    'execution_artifacts_raise':dict(patch=lambda m:m.setattr(dev_agent_broker.Broker,'_execution',boom)),
    'history_write_raises':dict(patch=lambda m:m.setattr(dev_agent_broker.Broker,'_history',
        lambda self,kind,row:boom() if kind=='versions' else dev_agent_broker.Broker.__dict__['_history_real'](self,kind,row))),
    'version_files_raise':dict(patch=lambda m:m.setattr(Workspace,'publish',
        lambda self,name,value:boom() if name.startswith('versions/') else Workspace.__dict__['_publish_real'](self,name,value))),
}
dev_agent_broker.Broker._history_real=dev_agent_broker.Broker._history
Workspace._publish_real=Workspace.publish


@pytest.mark.parametrize('name',sorted(STEPS))
def test_per_version_fault_does_not_end_the_run(ws,monkeypatch,name):  # noqa: F811
    result,session,ledger=run(ws,monkeypatch,**STEPS[name])
    assert result['reason']=='agent_idle',(name,result)
    assert session.turn>=2 and 'fatal_cause' not in result
    assert ledger.snapshot()['used']['formal_versions']==1
    row=json.loads((ws.private/'versions/v0001.json').read_bytes())
    assert row['native_success'] is not True


def test_r5_replay_is_visible_execution_failure_and_native_unknown(ws,monkeypatch):  # noqa: F811
    result,session,ledger=run(ws,monkeypatch,executor=R5Replay())
    assert result['reason']=='agent_idle'
    stderr=(ws.ws/'executions/e000001/stderr.txt').read_text()
    assert '4mrad endpoint bound' in stderr
    trace=json.loads((ws.ws/'executions/e000001/trace.json').read_text())
    assert trace[-1]['error']['type']=='RuntimeError'
    outcome=json.loads((ws.ws/'executions/e000001/result.json').read_text())
    assert outcome['completed'] is False
    row=json.loads((ws.private/'versions/v0001.json').read_bytes())
    assert row['native']=='unknown' and row['native_success'] is None
    public=''.join(p.read_text() for p in ws.ws.rglob('*.json') if 'rpc' not in p.parts or True)
    assert 'completion_evidence' not in public and 'native_success' not in public


def test_ledger_version_record_failure_is_the_ledger_invariant(ws):  # noqa: F811
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,SCRIPT)
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber(),version_record=boom)
    assert result['reason']=='fatal_error' and result['fatal_cause']=='ledger_invariant'
    assert result['fatal_cause'] in FATAL_CAUSES


def test_allowed_fatal_list_is_closed():
    assert FATAL_CAUSES=={'ledger_invariant','broker_invariant','quota_stop','quota_unavailable',
                          'wall_stop','operator_cancel','session_failure'}


def test_memory_service_failure_after_a_version_is_not_fatal(ws):  # noqa: F811
    class FlakyMemory:
        def start(self):return 'Memory for v1: none'
        message=''
        def recheck(self):pass
        def open(self,version,previous):raise ConnectionError('memory service unreachable')
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,["tool('submit','--entry','work/main.py');tool('submit','--entry','work/main.py')","pass"])
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber(),memory=FlakyMemory())
    assert result['reason']=='agent_idle' and 'fatal_cause' not in result
    assert ledger.snapshot()['used']['formal_versions']==2
    assert result['memory_errors'][0]['version']==2 and 'unreachable' in result['memory_errors'][0]['error']
