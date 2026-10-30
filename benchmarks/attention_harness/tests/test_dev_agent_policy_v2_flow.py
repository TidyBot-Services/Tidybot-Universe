"""P2 integration: real broker/ledger/controller with entirely offline collaborators."""
from contextlib import nullcontext
import base64
import hashlib
import io
import json
import random
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from benchmarks.attention_harness import dev_agent_attention_v2 as attention
from benchmarks.attention_harness import dev_agent_policies as policies
from benchmarks.attention_harness.dev_agent_attention import Helper
from benchmarks.attention_harness.dev_agent_broker import Broker, FakeEvaluator
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger, LedgerBudget
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.dev_agent_workspace import Workspace, atomic_write, encode
from benchmarks.attention_harness.dev_agent_replay import freeze_origin, provision_replay
from benchmarks.attention_harness.dev_advisor_proxy import default_config
from benchmarks.attention_harness.tests.test_dev_agent_d1 import request

V2='agent-policy-v2'
SOURCE=b'from robot_sdk import sensors\nprint(sensors.get_observation())\n'
CONFIG=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,
            attempt_wall_seconds=1920,dev_mode='agent',advisor_proxy=default_config())


class Clock:
    def __init__(self):self.now=1000.
    def __call__(self):return self.now
    def advance(self,seconds):self.now+=seconds


class Executor:
    def __init__(self):self.calls=0;self.error=False
    def execute(self, source, directory, *, deadline):
        self.calls+=1
        if self.error:raise RuntimeError('motion failed 42')
        self.execution_usage=dict(sdk_calls=1,control_steps=1,wall_seconds=.2)
        raw=io.BytesIO();Image.new('RGB',(12,8),'red').save(raw,format='PNG')
        return dict(status='failed',stdout='ValueError: grasp missed 42\n',stderr='',
            trace=[dict(event_id='motion',operation='left_arm.move_delta',status='failed',
                error=dict(type='ValueError',code='grasp_miss',message='grasp missed'),
                timestamp=.2,timestamp_basis='relative')],
            frames=[dict(filename='frames/overview-000000.png',view='overview',sim_seconds=.2,
                         trace_seconds=.2,timestamp_basis='relative',data=raw.getvalue())])
    def cancel(self):pass


class Prober:
    def validate(self,op,payload):pass
    def probe(self,op,payload,*,deadline):return dict(planning_accepted=False,rejected=True,reason='no_path',diagnostics={'error_code':'no_path'})
    def close(self):pass
    def cancel(self):pass


class Advisor:
    def __init__(self):self.calls=[];self.error=False;self.usage={};self.cache=False
    def __call__(self,view,*args,**kwargs):
        self.calls.append(view)
        if self.error:raise RuntimeError('provider unavailable')
        return dict(feedback='Try a smaller motion.',usage=self.usage,cache_hit=self.cache)


class Memory:
    def __init__(self,matched=False):
        self.calls=[];self.matched=matched;self.message='';self.packet={}
    def start(self):self.calls.append(('start',));return self.open(1,'initial')
    def open(self,number,source,**kwargs):
        self.calls.append(('open',number,source));self.message='Memory notes'
        self.packet=dict(explicit_registry_authorization=True,entries=[dict(content='Use the grasp pose.')] if self.matched else [])
        return self.message
    def recheck(self):self.calls.append(('recheck',))


class Rig:
    def __init__(self,path,policy='reactive_help',**kwargs):
        self.ws=Workspace(path/'agent').prepare(dict(task_id='fake',task_prompt='Move safely.'),CONFIG)
        atomic_write(self.ws.ws,'work/main.py',SOURCE)
        self.clock=Clock();self.ledger=AgentLedger(path/'ledger.sqlite','cell',help_limit=10,clock=self.clock)
        self.advisor=Advisor();self.executor=Executor();self.prober=Prober()
        self.options=dict(policy=policy,config=CONFIG,ask_provider=self.advisor,agent_policy_version=V2,**kwargs)
        self.helper=Helper(self.ws,self.ledger,**self.options)
        self.broker=Broker(self.ws,executor=self.executor,prober=self.prober,evaluator=FakeEvaluator(),
            helper=self.helper,budget=LedgerBudget(self.ledger),agent_mode=True,agent_policy_version=V2)
        self.counter=0
    def wait(self):
        until=time.monotonic()+120
        while self.broker.active is not None and time.monotonic()<until:time.sleep(.002)
        assert self.broker.active is None,'broker did not settle'
    def call(self,op,payload=None,rid=None):
        self.counter+=1;rid=rid or 'r'+str(self.counter)
        value=self.broker.accept(request(rid,op,payload));self.wait()
        return self.broker.records[rid]['response']
    def submit(self):
        self.clock.advance(1)
        atomic_write(self.ws.ws,'work/main.py',SOURCE+f'# change {self.counter}\n'.encode())
        return self.call('submit')
    def reopen(self,delete=False):
        self.broker.close()
        if delete:self.helper.path.unlink()
        self.helper=Helper(self.ws,self.ledger,**self.options)
        self.broker=Broker(self.ws,executor=self.executor,prober=self.prober,evaluator=FakeEvaluator(),
            helper=self.helper,budget=LedgerBudget(self.ledger),agent_mode=True,agent_policy_version=V2)
    def close(self):self.broker.close()


@pytest.fixture
def rig(tmp_path):
    value=Rig(tmp_path)
    yield value
    value.close()


# 1: every policy uses three real submit windows, retry cycles and random slots.
@pytest.mark.parametrize('policy',policies.AGENT_POLICY_IDS)
def test_eight_policies_three_versions(tmp_path,policy):
    memory=Memory();r=Rig(tmp_path,policy,memory=memory,demo=dict(content='Bounded demo.'))
    try:
        r.helper.initial();r.helper.initial()
        for _ in range(3):
            response=r.submit();assert response['status']=='completed',response
            assert response['result']['help'].get('status')!='help_error',response
            if r.helper.pending_ask:
                assert r.call('ask_help',{'question':'What stopped this motion?'})['result']['requested']
            elif policy=='agent_initiated_help':
                assert r.call('ask_help',{'question':'What stopped this motion?'})['result']['requested']
        assert len(r.helper.state['versions'])==3
        assert r.helper.state['completed_execution_count']==3
        if policy=='demo_first':assert r.ledger.snapshot()['used']['help']==1
        if policy=='autonomous':assert r.ledger.snapshot()['used']['help']==0
        if policy=='trace_aware_hint_only':assert not memory.calls
        r.reopen(delete=True)
        assert len(r.helper.state['versions'])==3
        assert len(r.advisor.calls)==r.ledger.snapshot()['used']['help']-(policy=='demo_first')
    finally:r.close()


def test_retry_six_failures(tmp_path):
    r=Rig(tmp_path,'retry_k_then_ask');asked=[]
    try:
        for number in range(1,7):
            r.submit()
            if r.helper.pending_ask:
                asked.append(number);r.call('ask_help',{'question':'How can I fix this?'})
        assert asked==[2,4,6]
        assert r.helper.state['retry_failures']==0
    finally:r.close()


def test_random_ten_failure_positions(tmp_path):
    r=Rig(tmp_path,'budget_matched_random_escalation');asked=[]
    try:
        for index in range(10):
            before=len(r.advisor.calls);r.submit()
            if len(r.advisor.calls)>before:asked.append(index)
            if index==4:r.reopen(delete=True)
        assert asked==list(policies.selected_random_slots())==[7]
        assert r.helper.state['cumulative_failed_versions']==10
    finally:r.close()


def test_pending_runs_probes_replay_and_next_submit(rig):
    r=rig;r.submit();original=r.helper.pending_ask.copy()
    r.call('run');r.call('observe');assert r.helper.pending_ask==original
    before=r.ledger.snapshot()['used']
    # The rejected request is frozen before fallback starts and never becomes a submit.
    receipt=r.call('submit',rid='blocked');frozen=encode(receipt)
    assert receipt['error']=='ask_required' and receipt['charged']=={}
    after=r.ledger.snapshot()['used']
    assert after['formal_versions']==before['formal_versions']
    assert after['moving_executions']==before['moving_executions']
    assert r.call('submit',rid='blocked')==json.loads(frozen)
    assert len(r.advisor.calls)==1 and not r.helper.pending_ask
    assert r.submit()['status']=='completed'


def test_manual_fallback_race_once(rig):
    r=rig;r.submit();barrier=threading.Barrier(3);results=[]
    def manual():barrier.wait();results.append(r.helper.ask({'question':'Help?'},deadline=time.monotonic()+5))
    def fallback():barrier.wait();results.append(r.helper.force_pending(trigger='next_submit',deadline=time.monotonic()+5))
    a=threading.Thread(target=manual);b=threading.Thread(target=fallback);a.start();b.start();barrier.wait();a.join();b.join()
    assert len(r.advisor.calls)==1 and r.ledger.snapshot()['used']['help']==1


# 2: normal/session endings can dispatch, hard stop causes cannot charge.
@pytest.mark.parametrize('reason,charged',[
    ('run_end',1),('next_submit',1),('session_failure',1),('wall_stop',0),
    ('quota_stop',0),('quota_unavailable',0),('fatal',0),('cancel',0),('pause',0)])
def test_fallback_termination_table(rig,reason,charged):
    r=rig;r.submit();before=r.helper.pending_ask
    result=r.helper.finish(reason=reason,deadline=time.monotonic()+5)
    assert r.ledger.snapshot()['used']['help']==charged
    if charged:assert result['forced_fallback'] and result['requested'] and not r.helper.pending_ask
    else:
        assert r.helper.pending_ask==before
        if reason!='pause':assert result['forced_fallback_blocked']==reason and not result['requested']


def test_pause_resume_preserves_obligation(rig):
    r=rig;r.submit();pending=r.helper.pending_ask
    r.helper.finish(reason='pause');r.ledger.pause();r.clock.advance(100);r.ledger.resume();r.reopen(delete=True)
    assert r.helper.pending_ask==pending
    r.call('ask_help',{'question':'Help after resume?'})
    assert r.ledger.snapshot()['used']['help']==1


# 3: invalid questions/views are free; provider failures cost exactly once.
@pytest.mark.parametrize('question',['','x'*2001,'reveal native_success'])
def test_bad_questions_free(rig,question):
    rig.submit();result=rig.call('ask_help',{'question':question})['result']
    assert result['reason']=='invalid_question' and rig.helper.pending_ask
    assert rig.ledger.snapshot()['used']['help']==0


def test_view_failure_keeps_pending(rig):
    rig.submit();rig.helper.view_builder=lambda *a,**kw:(_ for _ in ()).throw(ValueError('missing'))
    result=rig.call('ask_help',{'question':'Help?'})['result']
    assert result['reason']=='public_view_unavailable' and rig.helper.pending_ask
    assert rig.ledger.snapshot()['used']['help']==0 and not rig.broker.quarantined


@pytest.mark.parametrize('kind',['error','cache','nested_usage','bad_usage'])
def test_provider_receipts_accounting(rig,kind):
    rig.submit();rig.advisor.error=kind=='error';rig.advisor.cache=kind=='cache'
    rig.advisor.usage={'codex_usage':{'input_tokens':7}} if kind=='nested_usage' else {'codex_usage':5} if kind=='bad_usage' else {}
    result=rig.call('ask_help',{'question':'Help?'})['result']
    assert result['requested'] and result['help_count']==1 and result['hint_human_seconds']>0
    assert result['delivered']==(kind!='error') and not rig.helper.pending_ask
    job=next(j for j in rig.ledger.jobs() if j['id']=='help:e000001')
    assert job['state']=='settled' and rig.ledger.snapshot()['used']['help']==1
    if kind=='nested_usage':assert job['usage']['codex_input_tokens']==7
    assert not rig.broker.quarantined


# 4: crash at every durable boundary; no recovery provider calls or double charges.
class Crash(BaseException):pass


@pytest.mark.parametrize('point',['pending','reserve','claim','answer','settle','publish'])
def test_crash_recovery_idempotence(rig,monkeypatch,point):
    r=rig
    if point=='pending':
        original=r.helper._commit
        def commit(kind):
            original(kind)
            if kind=='submit_decision':raise Crash()
        monkeypatch.setattr(r.helper,'_commit',commit)
        # Use the boundary directly after broker has durably published the execution.
        r.call('run')
        atomic_write(r.ws.ws,'work/main.py',SOURCE+b'# formal\n')
        r.submit()
        monkeypatch.setattr(r.helper,'_commit',original)
        assert r.helper.pending_ask
    else:
        r.submit()
        if point in ('reserve','claim','settle'):
            original=getattr(r.ledger,point)
            def operation(*a,**kw):
                value=original(*a,**kw)
                if a[0].startswith('help:'):raise Crash()
                return value
            monkeypatch.setattr(r.ledger,point,operation)
        elif point=='answer':
            original=r.helper.ask_provider
            def provider(*a,**kw):original(*a,**kw);raise Crash()
            monkeypatch.setattr(r.helper,'ask_provider',provider)
        else:
            original=r.helper._publish_requests
            monkeypatch.setattr(r.helper,'_publish_requests',lambda:(_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):r.helper.ask({'question':'Help?'},deadline=time.monotonic()+5)
        if point in ('reserve','claim','settle'):monkeypatch.setattr(r.ledger,point,original)
    before=len(r.advisor.calls);used=r.ledger.snapshot()['used']['help']
    r.reopen(delete=True)
    assert len(r.advisor.calls)==before and r.ledger.snapshot()['used']['help']==used
    if point in ('claim','answer'):
        assert r.helper.state['active_help']['phase']=='unknown'
        r.helper.finish(reason='run_end')
        assert len(r.advisor.calls)==before
    elif point in ('settle','publish'):
        assert r.helper.state['requests']['help:e000001']['delivered']
        assert not r.helper.pending_ask
    else:
        assert r.helper.pending_ask
        r.helper.finish(reason='run_end');assert len(r.advisor.calls)==before+1


# 5: per-execution identities, immutable code/frames/logs, withheld and errored executions.
def test_run_help_multiple_before_submit_and_interval(tmp_path):
    r=Rig(tmp_path,'agent_initiated_help')
    try:
        assert r.call('ask_help',{'question':'Help?'})['result']['reason']=='no_completed_execution'
        for i in range(3):
            r.call('run');assert r.call('ask_help',{'question':'Help?'})['result']['delivered']
            r.call('observe')
            assert r.call('ask_help',{'question':'Again?'})['result']['reason']=='new_execution_required'
        for _ in range(3):r.submit();r.call('ask_help',{'question':'Help?'})
        assert len(r.advisor.calls)==6
        assert {j['id'] for j in r.ledger.jobs() if j['kind']=='help'}=={'help:e%06d'%i for i in range(1,7)}
        assert (r.ws.canonical/'executions/e000001/help.json').exists()
        assert r.helper.state['versions']['v0001']['execution_ids']==['e000001','e000002','e000003','e000004']
        assert r.helper.state['versions']['v0001']['help_ids']==['help:e%06d'%i for i in range(1,5)]
    finally:r.close()


def test_request_public_identity_code_frames_and_logs(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop,event_timing
    monkeypatch.setattr(dev_v2_loop,'events_for_moment_selection',lambda *a,**kw:pytest.fail('private timing reader'))
    monkeypatch.setattr(event_timing,'derive_event_times',lambda *a,**kw:pytest.fail('private event reader'))
    r=Rig(tmp_path,'agent_initiated_help')
    try:
        r.call('run')
        source=(r.ws.canonical/'executions/e000001/policy.py').read_text()
        atomic_write(r.ws.ws,'work/main.py',b'print("different")\n')
        (r.ws.ws/'executions/e000001/stdout.txt').write_text('FORGED')
        result=r.call('ask_help',{'question':'How can I fix this?'})['result'];assert result['delivered']
        view=r.advisor.calls[0]
        assert 'e000001' in view['reasoning'] and source in view['reasoning'] and 'FORGED' not in view['reasoning']
        assert 'ValueError: grasp missed 42' in view['reasoning'] and view['frames']
        assert view['request_kind']=='targeted'
        frame=view['frames'][0]
        raw=(r.ws.canonical/'executions/e000001'/frame['filename']).read_bytes()
        assert frame['sha256']==hashlib.sha256(raw).hexdigest()
        assert frame['data_url'].startswith('data:image/jpeg;base64,')
    finally:r.close()


@pytest.fixture(scope='module')
def full_hd_png():
    # Incompressible, reproducible pixels exercise real 1080p payload sizes and RGB conversion.
    pixels=random.Random(102).randbytes(1920*1080*3)
    image=Image.frombytes('RGB',(1920,1080),pixels).convert('RGBA')
    raw=io.BytesIO();image.save(raw,format='PNG')
    return raw.getvalue()


@pytest.mark.parametrize('targeted',[False,True],ids=['ten-frame-fallback','targeted'])
def test_request_full_hd_jpeg_size_sha_and_selected_only(tmp_path,monkeypatch,full_hd_png,targeted):
    r=Rig(tmp_path,'agent_initiated_help');original=r.executor.execute
    def execute(*a,**kw):
        result=original(*a,**kw)
        result['trace'][0]['timestamp']=5.2
        result['frames']=[dict(filename=f'frames/overview-{i:06d}.png',view='overview',
            sim_seconds=i,trace_seconds=i,timestamp_basis='relative' if targeted else 'unknown',
            data=full_hd_png) for i in range(12)]
        return result
    monkeypatch.setattr(r.executor,'execute',execute)
    encoded=[];original_encode=base64.b64encode
    def capture(raw):
        encoded.append(raw)
        return original_encode(raw)
    try:
        assert r.call('run')['status']=='completed'
        with monkeypatch.context() as patch:
            patch.setattr(base64,'b64encode',capture)
            assert r.call('ask_help',{'question':'How can I fix this?'})['result']['delivered']
        view=r.advisor.calls[0]
        assert view['request_kind']==('targeted' if targeted else 'generic')
        assert len(view['frames'])==len(encoded)==(1 if targeted else 10)
        rows=json.loads((r.ws.canonical/'executions/e000001/frames.json').read_bytes())
        public={row['filename']:row for row in rows}
        assert len(public)==12
        assert [frame['filename'] for frame in view['frames']]==(
            [rows[5]['filename']] if targeted else [row['filename'] for row in rows[:10]])
        for frame,raw in zip(view['frames'],encoded):
            prefix,payload=frame['data_url'].split(',',1)
            assert prefix=='data:image/jpeg;base64'
            assert len(payload)<300_000
            assert base64.b64decode(payload)==raw
            with Image.open(io.BytesIO(raw)) as image:
                assert image.format=='JPEG' and image.mode=='RGB'
                assert image.size==(768,432)
            assert frame['sha256']==public[frame['filename']]['sha256']==hashlib.sha256(full_hd_png).hexdigest()
        assert len(encode(view))<3_000_000
        print(f"{view['request_kind']}: PNG={len(full_hd_png)} bytes; JPEG={len(encoded[0])} bytes; "
              f"base64={len(view['frames'][0]['data_url'].split(',',1)[1])} bytes; "
              f"view ({len(view['frames'])} frames)={len(encode(view))} bytes")
    finally:r.close()


@pytest.mark.parametrize('corrupt_index',[0,1],ids=['selected','unselected'])
def test_frame_digest_failure_is_free(tmp_path,monkeypatch,corrupt_index):
    r=Rig(tmp_path,'agent_initiated_help')
    original=r.executor.execute
    def execute(*a,**kw):
        result=original(*a,**kw)
        result['frames'].append(dict(result['frames'][0],filename='frames/overview-000001.png',
            sim_seconds=1,trace_seconds=1))
        return result
    monkeypatch.setattr(r.executor,'execute',execute)
    try:
        r.call('run');path=r.ws.canonical/f'executions/e000001/frames/overview-{corrupt_index:06d}.png'
        # Replace the inode instead of modifying the read-only shared public frame.
        path.unlink();path.write_bytes(b'wrong image')
        monkeypatch.setattr(base64,'b64encode',lambda *a:pytest.fail('encoded before SHA verification'))
        result=r.call('ask_help',{'question':'Help?'})['result']
        assert result['reason']=='public_view_unavailable' and not r.advisor.calls
    finally:r.close()


def test_errored_execution_registered(tmp_path):
    r=Rig(tmp_path,'agent_initiated_help');r.executor.error=True
    try:
        result=r.call('run');assert result['status']=='completed'
        assert r.helper.state['completed_execution_count']==1
        assert r.call('ask_help',{'question':'Why did it fail?'})['result']['requested']
    finally:r.close()


# 6: Memory gates/order/revocation are exercised with a service-free fake.
@pytest.mark.parametrize('matched',[True,False])
def test_full_memory_priority_and_miss(tmp_path,matched):
    memory=Memory(matched);r=Rig(tmp_path,'full_trace_aware_attention_planner',memory=memory)
    try:
        for _ in range(3):r.call('run')
        value=r.submit()['result']['help']
        assert value['decision']['action']==('retrieve_memory' if matched else 'request')
        assert len(r.advisor.calls)==(not matched)
        assert memory.calls[0][1]==2 and memory.calls[0][2]==(r.ws.canonical/'versions/v0001/bundle.py').read_text()
    finally:r.close()


def test_full_service_unavailable_continues(tmp_path):
    memory=Memory()
    memory.open=lambda *a,**kw:(_ for _ in ()).throw(OSError('service offline'))
    r=Rig(tmp_path,'full_trace_aware_attention_planner',memory=memory)
    try:
        for _ in range(3):assert r.submit()['status']=='completed'
        assert len(r.advisor.calls)==2 and not r.broker.quarantined
    finally:r.close()


# 7/8: sealed export cutoff, hostile workspace, replay and version-lock compatibility.
def test_late_help_does_not_mutate_frozen_cut(rig,tmp_path):
    r=rig;r.submit()
    ref=freeze_origin(r.ws,'post-feedback-v1-pre-help',metadata={'agent_policy_version':V2})
    before=(r.ws.private/'snapshots/post-feedback-v1-pre-help.json').read_bytes()
    r.call('ask_help',{'question':'Help?'})
    replay=provision_replay(ref,tmp_path/'replay')
    assert not list((replay.canonical/'executions').glob('*/help.json'))
    assert (r.ws.private/'snapshots/post-feedback-v1-pre-help.json').read_bytes()==before
    assert len(r.advisor.calls)==1
    with pytest.raises(ValueError,match='sealed policy evidence'):
        Helper(replay,r.ledger,**r.options)


def test_v2_state_version_mismatch_refused(rig):
    r=rig;r.submit();r.broker.close()
    state=json.loads(r.helper.path.read_bytes());state['agent_policy_version']='agent-policy-v1'
    atomic_write(r.ws.private,'attention.json',encode(state))
    with pytest.raises(ValueError,match='mismatch'):Helper(r.ws,r.ledger,**r.options)
    with pytest.raises(ValueError,match='mismatch'):
        state['agent_policy_version']=V2;atomic_write(r.ws.private,'attention.json',encode(state))
        Helper(r.ws,r.ledger,policy='reactive_help')


def test_corrupt_projection_rebuilt_but_journal_tamper_refused(rig):
    r=rig;r.submit();pending=r.helper.pending_ask.copy()
    atomic_write(r.ws.private,'attention.json',b'{invalid')
    r.reopen();assert r.helper.pending_ask==pending
    r.broker.close();p=sorted(r.helper.journal.glob('*.json'))[-1]
    value=json.loads(p.read_bytes());value['kind']='tampered';p.write_bytes(encode(value))
    with pytest.raises(ValueError,match='digest mismatch'):Helper(r.ws,r.ledger,**r.options)


class Session:
    """In-process fake session driving requests through the real controller poller."""
    def __init__(self,ws,scripts):
        self.ws=ws;self.scripts=scripts;self.turn=0;self.thread_id=None;self.usage={};self.prompts=[]
    def quiesce(self):return nullcontext()
    def checkpoint(self):return dict(turn=self.turn,thread_id=self.thread_id)
    def cancel(self,reason):pass
    def start(self,prompt):return self.resume(None,prompt)
    def resume(self,thread,prompt):
        self.turn+=1;self.thread_id='fake';self.prompts.append(prompt)
        yield {'type':'turn.started'}
        for index,op in enumerate(self.scripts[min(self.turn-1,len(self.scripts)-1)]):
            if op=='failure':raise RuntimeError('session failed')
            rid='s%02d-%02d-%s'%(self.turn,index,op)
            if op=='submit':atomic_write(self.ws.ws,'work/main.py',SOURCE+f'# turn {self.turn}\n'.encode())
            atomic_write(self.ws.ws,'rpc/requests/'+rid+'.json',encode(request(rid,op)))
            yield {'type':'tool.started'}
            until=time.monotonic()+120
            while time.monotonic()<until:
                path=self.ws.canonical/'rpc/responses'/(rid+'.json')
                if path.exists() and json.loads(path.read_bytes())['status'] in ('completed','rejected','needs_review'):break
                yield {'type':'tool.wait'}
                time.sleep(.002)
            else:raise AssertionError('session tool timeout')
        yield {'type':'turn.completed'}


def drive_rig(r,scripts,**kwargs):
    r.broker.close();session=Session(r.ws,scripts)
    result=drive(r.ws,session,r.ledger,executor=r.executor,prober=r.prober,helper=r.helper,
        agent_policy_version=V2,policy=r.helper.policy,completion_reader=lambda *a:False,**kwargs)
    return result,session


def test_hint_only_entire_loop_zero_memory(tmp_path):
    memory=Memory(True);r=Rig(tmp_path,'trace_aware_hint_only',memory=memory)
    result,session=drive_rig(r,[['submit'],['submit'],['submit'],[]],memory=memory)
    assert result['reason']=='agent_idle' and len(r.helper.state['versions'])==3
    assert memory.calls==[]
    assert not (r.ws.canonical/'memory').exists()
    assert not any('Memory notes' in p for p in session.prompts)
    for p in r.ws.canonical.rglob('*'):
        if p.is_file() and p.suffix not in ('.png',):assert b'Use the grasp pose.' not in p.read_bytes()


@pytest.mark.parametrize('mode',['idle','versions','executions','session_failure','cancel','pause','wall'])
def test_loop_final_snapshot_and_stop_reason(tmp_path,mode):
    r=Rig(tmp_path)
    scripts=[['submit'],[]]
    control=lambda:None
    if mode in ('versions','executions'):
        # Real ledger resource holds cause the last allowed request to end the run.
        resource='formal_versions' if mode=='versions' else 'moving_executions'
        r.ledger.reserve('prefill','fixture',{resource:r.ledger.limits[resource]-1});r.ledger.claim('prefill');r.ledger.settle('prefill',{})
    elif mode=='session_failure':scripts=[['submit'],['failure']]
    elif mode in ('cancel','pause','wall'):
        def control():
            if not r.helper.pending_ask:return None
            if mode=='wall':r.clock.advance(21601);return None
            return mode
    result,_=drive_rig(r,scripts,control=control)
    expected={'idle':'agent_idle','versions':'budget_exhausted','executions':'budget_exhausted',
        'session_failure':'fatal_error','cancel':'operator_cancel','pause':'operator_paused','wall':'wall_stop'}[mode]
    assert result['reason']==expected
    assert result['resources']['attention']==r.ledger.snapshot()['attention']
    assert result['resources']['used']['help']==(mode in ('idle','versions','executions','session_failure'))
    if mode=='session_failure':assert result['fatal_cause']=='session_failure'

@pytest.mark.parametrize('policy',policies.AGENT_POLICY_IDS[:-1])
def test_v1_public_bytes_and_legacy_decide_golden(tmp_path,policy):
    """Run the committed P1 classes as an independent pre-change oracle."""
    import shutil
    import subprocess
    import types
    def original(name):
        source=subprocess.check_output(['git','show','d1d50d1:benchmarks/attention_harness/'+name+'.py'],text=True)
        module=types.ModuleType('benchmarks.attention_harness._p1_'+name)
        module.__package__='benchmarks.attention_harness'
        exec(compile(source,name+'.py','exec'),module.__dict__)
        return module
    old_attention=original('dev_agent_attention');old_broker=original('dev_agent_broker')
    from benchmarks.attention_harness import dev_agent_attention as current
    old_decisions=[];new_decisions=[]
    old_decide=old_attention.decide;new_decide=current.decide
    def capture_old(*a,**kw):
        value=old_decide(*a,**kw);old_decisions.append(value);return value
    def capture_new(*a,**kw):
        value=new_decide(*a,**kw);new_decisions.append(value);return value
    old_attention.decide=capture_old;current.decide=capture_new
    def view(*a,**kw):
        from benchmarks.attention_harness.dev_request_view import build_request_view
        return build_request_view(task={'task_id':'fake','task_prompt':'Move safely.'},question=kw.get('question') or 'Help?',frames=[])
    root=tmp_path/'same-root'
    def run(helper_cls,broker_cls,explicit=False):
        root.mkdir();ws=Workspace(root/'agent').prepare(dict(task_id='fake',task_prompt='Move safely.'),CONFIG)
        ledger=AgentLedger(root/'ledger.sqlite','cell',help_limit=10,clock=Clock())
        h=helper_cls(ws,ledger,policy=policy,config=CONFIG,view_builder=view,ask_provider=Advisor(),demo={'content':'Demo.'},
            **({'agent_policy_version':'agent-policy-v1'} if explicit else {}))
        b=broker_cls(ws,executor=Executor(),prober=Prober(),evaluator=FakeEvaluator(),helper=h,agent_mode=True,budget=LedgerBudget(ledger))
        try:
            h.initial()
            for i in range(3):
                atomic_write(ws.ws,'work/main.py',SOURCE+f'# version {i}\n'.encode())
                b.accept(request(f's{i}','submit'))
                while b.active is not None:time.sleep(.002)
                b.accept(request(f'a{i}','ask_help',{'question':'Help?'}))
                while b.active is not None:time.sleep(.002)
            h.finish()
            result={str(p.relative_to(ws.canonical)):p.read_bytes() for p in ws.canonical.rglob('*') if p.is_file()}
            result['attention.json']=h.path.read_bytes()
            return result
        finally:b.close();shutil.rmtree(root)
    try:
        expected=run(old_attention.Helper,old_broker.Broker)
        assert run(Helper,Broker)==expected
        assert new_decisions==old_decisions
        new_decisions.clear()
        assert run(Helper,Broker,explicit=True)==expected
        assert new_decisions==old_decisions
    finally:current.decide=new_decide


def test_logs_withheld_no_private_recovery(tmp_path):
    r=Rig(tmp_path,'agent_initiated_help');original=r.executor.execute
    def execute(*a,**kw):
        result=original(*a,**kw);result['stdout']='native_success secret ValueError: secret reason'
        result['stderr']='safe detail\n'+'z'*3000
        return result
    r.executor.execute=execute
    try:
        r.call('run');r.call('ask_help',{'question':'Help?'})
        view=r.advisor.calls[0]
        assert 'secret reason' not in view['reasoning'] and '[withheld]' not in view['reasoning']
        evidence=json.loads((r.ws.canonical/'executions/e000001/evidence.json').read_bytes())
        assert len(evidence['log_tail'])<=2000
        assert next(x for x in evidence['artifacts'] if x['path'].endswith('stdout.txt'))['status']=='withheld'
    finally:r.close()


def test_missing_timing_generic_fallback(tmp_path):
    r=Rig(tmp_path,'agent_initiated_help');original=r.executor.execute
    def execute(*a,**kw):
        result=original(*a,**kw);result['frames'][0].pop('timestamp_basis');return result
    r.executor.execute=execute
    try:
        r.call('run');r.call('ask_help',{'question':'Help?'})
        assert r.advisor.calls[0]['request_kind']=='generic' and r.advisor.calls[0]['frames']
    finally:r.close()


def test_memory_revocation_does_not_move_or_charge(tmp_path):
    from benchmarks.attention_harness.dev_agent_memory import MemoryRevoked
    r=Rig(tmp_path,'full_trace_aware_attention_planner');r.broker.memory=Memory()
    r.broker.memory.recheck=lambda:(_ for _ in ()).throw(MemoryRevoked('memory_revoked'))
    try:
        response=r.submit()
        assert response['error']=='memory_revoked' and r.executor.calls==0
        assert r.ledger.snapshot()['used']['moving_executions']==0
        assert not r.helper.state['executions'] and not r.broker.quarantined
    finally:r.close()


def test_full_memory_budget_gate_before_query(tmp_path):
    memory=Memory(True);r=Rig(tmp_path,'full_trace_aware_attention_planner',memory=memory)
    try:
        r.ledger.reserve('all-help','fixture',{'help':10});r.ledger.claim('all-help');r.ledger.settle('all-help',{})
        assert r.submit()['result']['help']['decision']['reason']=='assistance_budget_exhausted'
        assert memory.calls==[]
    finally:r.close()


@pytest.mark.parametrize('cause',['quota_stop','quota_unavailable','fatal'])
def test_loop_quota_and_fatal_keep_stop_and_free_pending(tmp_path,cause):
    r=Rig(tmp_path)
    class Quota:
        def event(self,event):pass
        def check(self,reason):
            if r.helper.pending_ask and reason=='before_resume':raise ValueError(cause if cause!='fatal' else 'ledger_invariant')
    result,_=drive_rig(r,[['submit'],[]],quota=Quota())
    assert result['reason']==('fatal_error' if cause=='fatal' else cause)
    assert result['resources']['used']['help']==0 and r.helper.pending_ask
    assert r.helper.state['forced_fallback']['forced_fallback_blocked']==('fatal' if cause=='fatal' else cause)

from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture as memory_fixture


def test_v2_d7_export_freeze_late_help_and_practice_identity(memory_fixture,monkeypatch):
    from benchmarks.attention_harness.dev_agent_export import Exporter
    from benchmarks.attention_harness.tests.test_dev_agent_d7 import Executor as FormalExecutor
    f=memory_fixture;ex=FormalExecutor();exporter=Exporter(f.store,'cell',f.ws,f.config,f.ledger)
    advisor=Advisor()
    def view(*a,**kw):
        from benchmarks.attention_harness.dev_request_view import build_request_view
        return build_request_view(task={'task_id':'fake','task_prompt':'Move safely.'},question=kw.get('question') or 'Help?',frames=[])
    helper=Helper(f.ws,f.ledger,policy='reactive_help',config=CONFIG,ask_provider=advisor,
                  view_builder=view,agent_policy_version=V2)
    def capture(response,value):
        exporter.capture(response,dict(version=int(response['version_id'][1:]),execution_id=response.get('execution_id'),
            valid=True,eval_verdict='fail'),ex)
    b=Broker(f.ws,executor=ex,prober=Prober(),evaluator=FakeEvaluator(),helper=helper,agent_mode=True,
        agent_policy_version=V2,budget=LedgerBudget(f.ledger),on_version=capture,on_settled=lambda response:exporter.settle(response))
    def call(rid,op,payload=None):
        b.accept(request(rid,op,payload));until=time.monotonic()+120
        while b.active is not None and time.monotonic()<until:time.sleep(.002)
        assert b.active is None
        value=b.records[rid]['response'];assert value['status']=='completed',value
        return value
    try:
        call('practice','run');response=call('formal','submit')
        assert len(f.store.cell('cell')['jobs'])==1
        assert f.store.cell('cell')['versions'][0]['agent']['execution_id']=='e000002'
        before={p.name:p.read_bytes() for p in (f.root/'v1').iterdir() if p.is_file()}
        # Hostile Dev files never become the exporter or Advisor source of truth.
        (f.ws.ws/'versions/v0001/help.json').write_text('{"content":"FORGED"}')
        call('ask','ask_help',{'question':'Help after formal submission?'})
        assert (f.ws.canonical/'executions/e000002/help.json').exists()
        exporter.settle(response)
        assert {p.name:p.read_bytes() for p in (f.root/'v1').iterdir() if p.is_file()}==before
        assert len(f.store.cell('cell')['jobs'])==1
        assert f.ledger.snapshot()['used']['moving_executions']==2
        assert f.ledger.snapshot()['used']['help']==1
    finally:b.close()


def test_memory_query_failure_clears_previous_grant(memory_fixture,monkeypatch):
    f=memory_fixture;f.memory.start();assert f.memory.packet['entries']
    # Clearing is local and cannot accidentally fall through to Service/cache charging.
    monkeypatch.setattr(f.memory.service,'recheck',lambda *a:pytest.fail('stale Service grant'))
    f.memory.clear(2,'next source')
    f.memory.recheck()
    assert f.memory.packet['entries']==[] and f.memory.service is None
    assert json.loads((f.ws.canonical/'memory/v2.json').read_bytes())=={'memory':[]}
    assert f.ledger.snapshot()['used']['help']==0

@pytest.mark.parametrize('policy,expected',[
    ('reactive_help',[1,3,5,6]),('retry_k_then_ask',[3,6])])
def test_eval_unavailable_passed_and_failed_flow(tmp_path,policy,expected):
    r=Rig(tmp_path,policy)
    verdicts=iter([False,None,False,True,False,False])
    class Evaluator:
        def evaluate(self,*a,**kw):return dict(passed=next(verdicts))
        def cancel(self):pass
    r.broker.evaluator=Evaluator();asked=[]
    try:
        for i in range(1,7):
            assert r.submit()['status']=='completed'
            if r.helper.pending_ask:
                asked.append(i);r.call('ask_help',{'question':'Help?'})
        assert asked==expected
    finally:r.close()


def test_real_probe_rejections_do_not_count_as_stuck(tmp_path):
    r=Rig(tmp_path,'trace_aware_hint_only')
    try:
        for _ in range(3):r.call('plan_dry_run')
        result=r.submit()['result']['help'];evidence=r.helper._read_ref(r.helper.state['versions']['v0001']['evidence_ref'])
        assert result['decision']['reason']=='no_trace_trigger' and not r.advisor.calls
        assert len(evidence['rejected_probes'])==3 and evidence['stuck_in_version']==1
    finally:r.close()


def test_loop_memory_start_unavailable_continues(tmp_path):
    memory=Memory()
    memory.start=lambda:(_ for _ in ()).throw(ConnectionError('service unavailable'))
    r=Rig(tmp_path,'full_trace_aware_attention_planner',memory=memory)
    result,_=drive_rig(r,[['submit'],['submit'],['submit'],[]],memory=memory)
    assert result['reason']=='agent_idle' and len(r.helper.state['versions'])==3
    assert result['memory_errors'][0]['version']==1


def test_revoked_policy_query_keeps_recheck_armed(tmp_path):
    from benchmarks.attention_harness.dev_agent_memory import MemoryRevoked
    memory=Memory(True);cleared=[]
    memory.recheck=lambda:(_ for _ in ()).throw(MemoryRevoked('memory_revoked'))
    memory.clear=lambda *a:cleared.append(a)
    r=Rig(tmp_path,'full_trace_aware_attention_planner',memory=memory)
    try:
        assert r.submit()['status']=='completed'
        assert cleared==[]
        r.broker.memory=memory
        assert r.submit()['error']=='memory_revoked'
        assert r.executor.calls==1 and not r.broker.quarantined
    finally:r.close()
