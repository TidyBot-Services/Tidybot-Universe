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
        assert value['error']=='timeout' and time.monotonic()-started<1.
        assert b.failure['op']=='observe' and 'after 0.1 s' in b.failure['detail']
        error=json.loads((workspace.private/'errors'/'r2.json').read_text())
        assert error['error']=='timeout' and error['error_class']=='TimeoutError'
    finally:gate.set();b.close()


def test_fatal_stop_carries_the_cause(ws):  # noqa: F811
    class Hang(FakeProber):
        def probe(self,*a,**kw):time.sleep(3);return super().probe(*a,**kw)
    import benchmarks.attention_harness.dev_agent_broker as module
    saved=dict(module.TIMEOUTS)
    module.TIMEOUTS.update(observe=.05,probe_startup=0.)
    try:
        ledger=AgentLedger(ws.private/'db','cell')
        session=fake_session(ws,["tool('observe');time.sleep(20)"])
        result=drive(ws,session,ledger,executor=Executor(),prober=Hang())
    finally:module.TIMEOUTS.clear();module.TIMEOUTS.update(saved)
    assert result['state']=='needs_review' and result['reason']=='fatal_error'
    assert result['error'].startswith('broker_quarantined: observe ')
    assert 'timeout' in result['error'] and 'collaborator_timeout after' in result['error']


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
