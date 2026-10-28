"""10-28 real run: a cold probe owner (~55 s scene build) hit the 10 s observe limit."""
import json
import threading
import time
from benchmarks.attention_harness.dev_agent_adapters import FakeProber, Prober
from benchmarks.attention_harness.dev_agent_broker import TIMEOUTS
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.tests.test_dev_agent_d1 import broker, request, settled
from benchmarks.attention_harness.tests.test_dev_agent_d1 import workspace  # noqa: F401
from benchmarks.attention_harness.tests.test_dev_agent_d2 import Executor, fake_session
from benchmarks.attention_harness.tests.test_dev_agent_d2 import ws  # noqa: F401


class ColdStart(FakeProber):
    """Starting the owner takes longer than the per-op limit; warm probes are fast."""
    def __init__(self, startup, warm=0.):
        super().__init__();self.cold=True;self.startup=startup;self.warm=warm
    def probe(self,*a,**kw):
        time.sleep(self.startup if self.cold else self.warm);self.cold=False
        return super().probe(*a,**kw)
    def close(self):self.cold=True


def test_real_prober_reports_cold_until_owner_started():
    p=Prober(dict(budget_tier='dev',runner_boundary='dev_loop'),sim_python='python',private_dir='/nonexistent')
    assert p.cold
    p.owner=object();assert not p.cold
    p.owner=None;assert p.cold


def test_startup_allowance_is_generous_and_only_for_cold_owner():
    assert TIMEOUTS['observe']==10. and TIMEOUTS['find_objects']==10.
    assert TIMEOUTS['probe_startup']>=120.


def test_cold_probe_gets_startup_allowance(workspace):
    b=broker(workspace,prober=ColdStart(.3),timeouts={'observe':.05,'probe_startup':2.})
    try:
        b.accept(request());value=settled(b)
        assert value['status']=='completed' and not b.quarantined
        b.accept(request('r2'));assert settled(b,'r2')['status']=='completed'
    finally:b.close()


def test_warm_probe_keeps_per_op_limit(workspace):
    gate=threading.Event()
    class Hang(ColdStart):
        def probe(self,*a,**kw):
            if not self.cold:gate.wait(2)
            return super().probe(*a,**kw)
    b=broker(workspace,prober=Hang(0.),timeouts={'observe':.05,'probe_startup':2.})
    try:
        b.accept(request());assert settled(b)['status']=='completed'
        started=time.monotonic();b.accept(request('r2'));value=settled(b,'r2')
        assert value['result']['probe_error']['error_class']=='TimeoutError' and time.monotonic()-started<1.
        assert 'after 0.1 s' in value['result']['probe_error']['message'] and not b.quarantined
        error=json.loads((workspace.private/'errors'/'r2.json').read_text())
        assert error['error']=='probe_error' and error['op']=='observe' and 'TimeoutError' in error['detail']
    finally:gate.set();b.close()


def test_unrecoverable_owner_quarantines_with_cause(ws):  # noqa: F811
    class NoStart(FakeProber):
        def probe(self,*a,**kw):self.start_failed=True;raise RuntimeError('Native startup failed: synthetic')
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,["tool('observe');tool('observe');time.sleep(20)"])
    result=drive(ws,session,ledger,executor=Executor(),prober=NoStart())
    assert result['state']=='needs_review' and result['reason']=='fatal_error'
    assert result['error'].startswith('broker_invariant: observe ') and result['fatal_cause']=='broker_invariant'
    assert 'probe_owner_unrecoverable' in result['error'] and 'synthetic' in result['error']
    assert ledger.snapshot()['used']['read_only_probes']==2


def test_probe_exception_returns_to_dev_and_owner_restarts(workspace):  # noqa: F811
    class Flaky(FakeProber):
        cancels=0
        def probe(self,op,payload,**kw):
            if self.calls==1:self.calls+=1;raise AttributeError("'NoneType' object has no attribute 'get'")
            return super().probe(op,payload,**kw)
        def cancel(self):self.cancels+=1
    p=Flaky();b=broker(workspace,prober=p)
    try:
        b.accept(request('a'));assert settled(b,'a')['status']=='completed'
        b.accept(request('b',op='ik_check',payload=dict(target=dict(side='left',position=[.5,-2.4,.95],quaternion_xyzw=[0,0,0,1]))))
        value=settled(b,'b')
        assert value['status']=='completed' and value['result']['probe_error']['error_class']=='AttributeError'
        assert p.cancels==1 and not b.quarantined
        b.accept(request('c',op='find_objects'));assert settled(b,'c')['status']=='completed'
        assert b.budget.counts['probes']==3
    finally:b.close()


def test_malformed_probe_payload_returns_validation_reason(workspace):  # noqa: F811
    b=broker(workspace)
    bad=[('ik_check',dict(target=dict(side='left',position=[1,2],quaternion_xyzw=[0,0,0,1])),'finite_vector_required'),
         ('ik_check',dict(target=dict(side='up',position=[1,2,3],quaternion_xyzw=[0,0,0,1])),'invalid_target'),
         ('plan_dry_run',dict(action=dict(group='left_arm',operation='fly',args=[],kwargs={})),'unsupported_action'),
         ('find_objects',dict(names='cup'),'invalid_names'),
         ('ik_check',dict(target=dict(side='left',position=[1,2,3],quaternion_xyzw=[0,0,0,1]),base_offset=dict(dx=0)),'invalid_base_offset')]
    try:
        for i,(op,payload,reason) in enumerate(bad):
            value=b.accept(request('m%d'%i,op=op,payload=payload))
            assert value['status']=='rejected' and value['error']=='invalid_request' and reason in value['validation_error'],value
        assert b.budget.counts['probes']==0 and not b.quarantined
    finally:b.close()


def test_owner_spawned_from_a_thread_that_outlives_each_probe(tmp_path):
    # Worker binds PR_SET_PDEATHSIG to its spawning thread; broker probes run on fresh threads.
    spawned=[]
    class Owner:
        def __init__(self,**kw):self.deadline=None
        def start(self):spawned.append(threading.current_thread());return {}
        def rpc(self,*a,**kw):return dict(state_basis='reset_scene',physics_steps=0)
        def stop(self):pass
    p=Prober(dict(budget_tier='dev',runner_boundary='dev_loop'),sim_python='python',private_dir=tmp_path,owner_factory=Owner)
    def call():p.probe('observe',{},deadline=time.monotonic()+5)
    for _ in range(2):
        t=threading.Thread(target=call);t.start();t.join()
        assert spawned[-1] is not t and spawned[-1].is_alive()
    p.close();t=threading.Thread(target=call);t.start();t.join()
    assert len(spawned)==2 and spawned[0] is spawned[1] and spawned[1].is_alive()


def test_projection_tolerates_null_validation_fields():
    # 10-28 r3: a real /plan/ik rejection carried validation/exact_geometry null.
    from benchmarks.attention_harness.dev_agent_diagnostics import project_planner_result
    for result in ({'validation':None},{'validation':{'exact_geometry':None}},{'validation':{'contacts':None}}):
        value=project_planner_result(result)
        assert 'contacts' in value['missing'] and value['diagnostics_complete'] is False
    assert 'contacts' not in project_planner_result({'validation':{'exact_geometry':{'contacts':[{'a':1}]}}})['missing']


def test_out_of_limit_base_offset_rejects_only_that_request(workspace):  # noqa: F811
    # r4: validate_delta raises PlanningRejected (not ValueError); it escaped accept and killed the run.
    b=broker(workspace)
    try:
        target=dict(side='left',position=[.5,-2.4,.95],quaternion_xyzw=[0,0,0,1])
        value=b.accept(request('o1',op='ik_check',payload=dict(target=target,base_offset=dict(dx=0,dy=.61,dyaw=0))))
        assert value['status']=='rejected' and 'exceeds the probe limit 0.6 m / 0.9 rad' in value['validation_error']
        b.accept(request('o2',op='ik_check',payload=dict(target=target,base_offset=dict(dx=0,dy=.2,dyaw=0))))
        assert settled(b,'o2')['status']=='completed' and not b.quarantined
    finally:b.close()


def test_sdk_doc_is_delivered_not_withheld(workspace):  # noqa: F811
    # sdk.md verbatim delivery is covered with a hash-bound config in test_dev_agent_request_boundary.
    for name in ('tools.md','guide.md'):
        assert 'withheld' not in (workspace.ws/'docs'/name).read_text()
