"""D2+D3 fake CLI/controller tests. No installed Codex, quota files or simulator."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import pytest
from benchmarks.attention_harness.dev_agent_workspace import Workspace,atomic_write
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession,SessionError
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger,LedgerBudget
from benchmarks.attention_harness.dev_agent_quota import QuotaMonitor,read_weekly_quota,QuotaUnavailable
from benchmarks.attention_harness.dev_agent_bundle import bundle
from benchmarks.attention_harness.dev_agent_replay import freeze_origin,provision_replay
from benchmarks.attention_harness.dev_agent_loop import drive,CONTINUE_MESSAGE
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_broker import FakeEvaluator,Broker,SCHEMA

CONFIG=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920,dev_mode='agent')
SOURCE=b'from robot_sdk import sensors\nprint(sensors.get_observation())\n'

@pytest.fixture
def ws(tmp_path):
    value=Workspace(tmp_path/'agent').prepare({'task_prompt':'Move safely.'},CONFIG)
    atomic_write(value.ws,'work/main.py',SOURCE)
    return value

class Executor:
    def __init__(self, successes=()):self.calls=0;self.successes=successes
    def execute(self,source,directory,*,deadline):
        self.calls+=1
        data=json.dumps(dict(evaluated=True,native_success=self.calls in self.successes)).encode()
        path=directory/'native_result.json';path.write_bytes(data)
        self.native_result_ref=dict(uri=str(path),sha256=hashlib.sha256(data).hexdigest())
        return dict(status='completed',stdout='trace native_success SECRET',trace=[],state_log=[],diagnostics=[])
    def cancel(self):pass

class PassEval(FakeEvaluator):
    def evaluate(self,*a,**kw):return dict(passed=True,reason='Looks done.',advice='Inspect logs.')


def fake_session(ws, scripts, *, raw=None):
    path=ws.private/'codex-fake'
    program='''#!/usr/bin/python3
import json,sys,subprocess,time
from pathlib import Path
args=sys.argv[1:]
prompt=sys.stdin.read()
note=Path('work/turn-counter')
turn=int(note.read_text())+1 if note.exists() else 1
note.write_text(str(turn))
def emit(value):print(json.dumps(value),flush=True)
def tool(op,*args):
 if op=='submit' and '--summary' not in args:args=(*args,'--summary','Try the planned motion.')
 p=subprocess.run(['/usr/bin/python3','docs/devtool.py',op,*args],capture_output=True,text=True)
 emit({'type':'item.completed','item':{'type':'agent_message','text':p.stdout}})
 return p
'''
    program+='scripts='+repr(scripts)+'\n'
    if raw is not None:program+=raw+'\n'
    else:
        program+="emit({'type':'thread.started','thread_id':'fake-thread-exact'})\n"
        program+="emit({'type':'test.argv','argv':args,'prompt':prompt})\n"
        program+="exec(scripts[min(turn-1,len(scripts)-1)])\n"
        program+="emit({'type':'turn.completed','usage':{'input_tokens':17,'output_tokens':9}})\n"
    path.write_text(program);path.chmod(0o700)
    def command(root,prompt):
        return [str(path),'-a','never','exec','-C',str(root),'--json','-m','gpt-6-astra','-c','model_reasoning_effort="low"','-c','default_permissions="ws"',prompt]
    return CodexAgentSession(ws.ws,ws.private,command_factory=command)


def run_fake(ws,scripts,*,successes=(),evaluator=None,quota=None,control=lambda:None):
    ledger=AgentLedger(ws.private/'experiment.sqlite','cell')
    session=fake_session(ws,scripts)
    result=drive(ws,session,ledger,executor=Executor(successes),prober=FakeProber(),evaluator=evaluator,quota=quota,control=control)
    return result,session,ledger


def test_multi_turn_resume_and_native_end(ws):
    result,session,ledger=run_fake(ws,["Path('work/notes.md').write_text('remember me'); tool('observe'); tool('run','--no-eval'); tool('run','--no-eval')", "assert Path('work/notes.md').read_text()=='remember me'; tool('submit'); time.sleep(20)"],successes=(3,))
    assert result['reason']=='native_success' and result['state']=='completed'
    assert json.loads((ws.private/'versions/v0001.json').read_text())['summary'] == 'Try the planned motion.'
    assert session.turn==2 and len(session.messages)==2
    assert ledger.snapshot()['used']==dict(formal_versions=1,moving_executions=3,read_only_probes=1,eval_runs=1,help=0)
    second=json.loads((ws.private/'turns/t000002/input.json').read_bytes())['command']
    assert second[second.index('resume')+1]=='fake-thread-exact'
    assert '-C' not in second and '--last' not in second and '--ephemeral' not in second
    assert 'default_permissions="ws"' in second and 'model_reasoning_effort="high"' in second
    assert json.loads((ws.private/'turns/t000002/usage.json').read_bytes())['usage']=='unknown'
    for name in ('policy.py','bundle.py','bundle-map.json','submission.json','eval.json','help.json','sources/main.py'):
        assert (ws.ws/'versions/v0001'/name).exists()
    assert len((ws.ws/'history/executions.jsonl').read_text().splitlines())==3
    assert json.loads((ws.ws/'history/version_history.json').read_bytes())['entries'][0]['version_id']=='v0001'
    prohibited=('native_success','object_in_sink','native_result','check_success','reward')
    for path in ws.ws.rglob('*'):
        if path.is_file():assert not any(word in path.read_text() for word in prohibited),path
    assert not any(word in '\n'.join(session.messages) for word in prohibited)
    assert len(list((ws.private/'snapshots').glob('*-v1*.json')))==3


def test_two_submits_across_turns_eval_pass_continues(ws):
    result,session,ledger=run_fake(ws,["tool('submit')","tool('submit'); time.sleep(20)"],successes=(2,),evaluator=PassEval())
    assert result['reason']=='native_success' and session.turn==2
    assert CONTINUE_MESSAGE in session.messages[1]
    assert ledger.snapshot()['used']['formal_versions']==2
    assert 'native_success' not in (ws.ws/'history/versions.jsonl').read_text()


def test_three_empty_turns_idle_missing_usage(ws):
    result,session,ledger=run_fake(ws,['pass'])
    assert result['reason']=='agent_idle' and session.turn==3
    assert 'No formal submission yet;' in session.messages[1]
    assert ledger.snapshot()['model_turns']==3
    assert all(j['usage']==dict(input_tokens=17,output_tokens=9) for j in ledger.jobs())

@pytest.mark.parametrize('raw,error',[
 ("emit({'type':'turn.completed'})",'missing_thread_id'),
 ("print('NOT JSON',flush=True)",'corrupt_jsonl'),
 ("emit({'type':'thread.started','thread_id':'saved-before-crash'});sys.exit(7)",'turn_process_failed')])
def test_session_corruption_and_crash(ws,raw,error):
    session=fake_session(ws,['pass'],raw=raw)
    with pytest.raises(SessionError,match=error):list(session.start('task'))
    if 'saved-before-crash' in raw:
        assert json.loads((ws.private/'session.json').read_bytes())['thread_id']=='saved-before-crash'
    assert json.loads((ws.private/'turns/t000001/usage.json').read_bytes())['usage']=='unknown'


def test_cancel_kills_process_group(ws):
    session=fake_session(ws,["p=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(60)']);Path('work/child').write_text(str(p.pid));time.sleep(60)"])
    stream=session.start('task')
    for event in stream:
        if (ws.ws/'work/child').exists():session.cancel('cancel');break
    stream.close()
    pid=int((ws.ws/'work/child').read_text())
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        try:state=Path('/proc')/str(pid)/'stat';text=state.read_text()
        except FileNotFoundError:break
        if text.split()[2]=='Z':break
        time.sleep(.01)
    else:pytest.fail('child survived cancellation')

@pytest.mark.parametrize('resource,cap',[('formal_versions',10),('moving_executions',40),('read_only_probes',300)])
def test_durable_budget_boundaries(tmp_path,resource,cap):
    path=tmp_path/'db';ledger=AgentLedger(path,'cell')
    for i in range(cap):
        ledger.reserve(str(i),'test',{resource:1});ledger.claim(str(i));ledger.settle(str(i),{})
    with pytest.raises(ValueError,match='budget_exhausted'):ledger.reserve('overflow','test',{resource:1})
    assert AgentLedger(path,'cell').snapshot()['used'][resource]==cap
    assert sqlite3.connect(path).execute("SELECT count(*) FROM sqlite_master WHERE name='v2_jobs'").fetchone()[0]==0


def test_atomic_reservation_recovery_pause_deadline(tmp_path):
    now=[1000.];ledger=AgentLedger(tmp_path/'db','c',clock=lambda:now[0])
    ledger.reserve('forty','test',{'moving_executions':40});ledger.claim('forty')
    with pytest.raises(ValueError,match='budget_exhausted'):
        ledger.reserve('submit','request',{'moving_executions':1,'formal_versions':1})
    assert ledger.snapshot()['used']['formal_versions']==0
    now[0]+=10;ledger.pause();now[0]+=1000
    assert ledger.snapshot()['wall_remaining']==21590
    ledger.resume();assert ledger.snapshot()['deadline']==23600
    assert ledger.recover() and ledger.jobs()[0]['state']=='unknown'
    now[0]+=21591
    with pytest.raises(ValueError,match='wall_stop'):ledger.reserve('turn','model',{'model_turns':1})


def test_quota_same_window_stale_and_crossing(ws):
    now=[1000.];ledger=AgentLedger(ws.private/'db','c',clock=lambda:now[0])
    reading=dict(used_percent=65,window_minutes=10080,resets_at=2000,observed_at=1000)
    quota=QuotaMonitor(ws.private,ledger,reader=lambda:reading,clock=lambda:now[0])
    quota.event(dict(rate_limits=dict(secondary=dict(used_percent=95,window_minutes=10080,resets_at=1500))))
    assert quota.sample('newer-window')['used_percent']==65
    quota.event(dict(rate_limits=dict(secondary=dict(used_percent=90,window_minutes=10080,resets_at=2000))))
    with pytest.raises(ValueError,match='quota_stop'):quota.check('crossed')
    assert len((ws.private/'quota.jsonl').read_text().splitlines())==2
    assert sqlite3.connect(ledger.path).execute('SELECT count(*) FROM v2_agent_quota').fetchone()[0]==2
    now[0]=1701
    with pytest.raises(ValueError,match='quota_stop'):quota.check('stale')
    assert json.loads((ws.private/'quota.jsonl').read_text().splitlines()[-1])['stale'] is True


def test_quota_reset_jitter_same_window(ws, tmp_path):
    now=time.time();ledger=AgentLedger(ws.private/'jitter-db','c')
    day=tmp_path/'.codex/sessions/2026/10/28';day.mkdir(parents=True)
    def row(ts,used,reset):
        return json.dumps(dict(timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(ts)),
            payload=dict(rate_limits=dict(primary=dict(used_percent=used,window_minutes=10080,resets_at=reset)))))
    (day/'rollout-a.jsonl').write_text(row(now-7200,73,now+5002)+'\n')
    (day/'rollout-b.jsonl').write_text(row(now-3600,82,now+5000)+'\n'+row(now-3600,10,now-10)+'\n')
    reading=read_weekly_quota(tmp_path,now=now)
    assert reading['used_percent']==82 and reading['resets_at']==now+5002
    quota=QuotaMonitor(ws.private,ledger,reader=lambda:read_weekly_quota(tmp_path),stop_percent=90)
    sample=quota.sample('start')
    assert sample['used_percent']==82 and sample['stale'] is True and not sample['stopped']


def test_quota_mid_run_refuses_request(ws):
    ledger=AgentLedger(ws.private/'quota-db','c');calls=[]
    def reader():
        calls.append(1);return dict(used_percent=90 if len(calls)>1 else 60,window_minutes=10080,resets_at=time.time()+1000,observed_at=time.time())
    quota=QuotaMonitor(ws.private,ledger,reader=reader)
    result,session,used=run_fake(ws,["tool('observe');time.sleep(20)"],quota=quota)
    assert result['reason']=='quota_stop'
    assert used.snapshot()['used']['read_only_probes']==0
    response=next((ws.ws/'rpc/responses').glob('*.json'))
    assert json.loads(response.read_bytes())['error']=='quota_stop'


def test_snapshot_replay_no_future_files(ws,tmp_path):
    atomic_write(ws.ws,'work/notes.md','earlier'.encode())
    cut=freeze_origin(ws,'post-feedback-v1-pre-help')
    atomic_write(ws.ws,'work/notes.md',b'future solution');ws.publish('versions/v0002/future.txt',b'future')
    control=provision_replay(cut,tmp_path/'control');treatment=provision_replay(cut,tmp_path/'treatment')
    def contents(w):return {str(p.relative_to(w.ws)):p.read_bytes() for p in w.ws.rglob('*') if p.is_file()}
    assert contents(control)==contents(treatment)
    assert (control.ws/'work/notes.md').read_text()=='earlier'
    assert not (control.ws/'versions/v0002').exists()
    treatment.publish('events/hint.json',{'hint':'only treatment'})
    assert not (control.ws/'events/hint.json').exists()


def test_bundle_helpers_source_map_and_multibyte(ws):
    atomic_write(ws.ws,'work/submission.json',json.dumps(dict(entry='main.py',modules=['helpers.py'])).encode())
    atomic_write(ws.ws,'work/helpers.py',b'from robot_sdk import sensors\nX=1\ndef see():\n    return sensors.get_observation()\n')
    atomic_write(ws.ws,'work/main.py',b'from helpers import see\nprint(see())\n')
    first=bundle(ws.ws);assert first==bundle(ws.ws)
    assert b'from helpers' not in first.script and first.source_map['order']==['helpers.py','main.py']
    assert any(row==dict(file='main.py',line=2) for row in first.source_map['lines'])
    (ws.ws/'work/submission.json').unlink()
    prefix='from robot_sdk import sensors\n#'.encode();data=prefix+'中'.encode()*((131072-len(prefix))//3)
    data+=b' '*(131072-len(data));atomic_write(ws.ws,'work/main.py',data)
    assert len(bundle(ws.ws).script)==131072
    atomic_write(ws.ws,'work/main.py',data+b'x')
    with pytest.raises(ValueError):bundle(ws.ws)

@pytest.mark.parametrize('helper,main,error',[
 ('X=1\n','X=2\n','conflicting_name'),
 ('from b import Y\nX=1\n','from a import X\nprint(X)\n','module_cycle'),
 ('X=1\n','from a import X as other\n','unsupported_import_name')])
def test_bundle_rejects_ambiguous_flattening(ws,helper,main,error):
    modules=['a.py'];atomic_write(ws.ws,'work/a.py',helper.encode())
    if error=='module_cycle':modules+=['b.py'];atomic_write(ws.ws,'work/b.py',b'from a import X\nY=2\n')
    atomic_write(ws.ws,'work/main.py',main.encode())
    atomic_write(ws.ws,'work/submission.json',json.dumps(dict(modules=modules)).encode())
    with pytest.raises(ValueError,match=error):bundle(ws.ws)


def test_two_formal_submits_same_turn(ws):
    result,session,ledger=run_fake(ws,["tool('submit');tool('submit');time.sleep(20)"],successes=(2,))
    assert result['reason']=='native_success' and session.turn==1
    assert ledger.snapshot()['used']['formal_versions']==2


@pytest.mark.parametrize('resource,cap,operation',[('formal_versions',10,'submit'),('moving_executions',40,'run'),('read_only_probes',300,'observe')])
def test_broker_last_durable_slot(ws,resource,cap,operation):
    ledger=AgentLedger(ws.private/'db','cell')
    ledger.reserve('previous','test',{resource:cap-1});ledger.claim('previous');ledger.settle('previous',{})
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,budget=LedgerBudget(ledger))
    def request(rid):return dict(schema=SCHEMA,request_id=rid,op=operation,payload={'summary':'Try the planned motion.'} if operation=='submit' else {})
    try:
        b.accept(request('last'))
        end=time.monotonic()+5
        while b.active and time.monotonic()<end:time.sleep(.01)
        assert b.active is None
        assert b.records['last']['response']['status']=='completed'
        assert b.accept(request('overflow'))['error']=='budget_exhausted'
        assert ledger.snapshot()['used'][resource]==cap
    finally:b.close()


def test_loop_stops_at_tenth_version(ws):
    result,session,ledger=run_fake(ws,["tool('submit')"])
    assert result['reason']=='budget_exhausted' and session.turn==10
    assert ledger.snapshot()['used']['formal_versions']==10
    assert len(list((ws.ws/'versions').iterdir()))==10


def test_wall_deadline_during_turn(ws):
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,['time.sleep(20)'])
    ledger.reserve('prior','test',{});ledger.claim('prior');ledger.settle('prior',{})
    with ledger.tx() as db:
        run=ledger._run(db);run['deadline']=time.time()+.2;ledger._put_run(db,run)
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber())
    assert result['reason']=='wall_stop'


def test_pause_resume_keeps_session_and_notes(ws):
    ledger=AgentLedger(ws.private/'db','cell')
    session=fake_session(ws,["Path('work/notes.md').write_text('saved');time.sleep(20)","assert Path('work/notes.md').read_text()=='saved';tool('submit');time.sleep(20)"])
    def control():return 'pause' if (ws.ws/'work/notes.md').read_text()=='saved' else None
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber(),control=control)
    assert result['reason']=='operator_paused' and ledger.snapshot()['paused_at'] is not None
    previous=ledger.snapshot()['wall_remaining'];time.sleep(.1)
    resumed=CodexAgentSession(ws.ws,ws.private,command_factory=session.command_factory)
    result=drive(ws,resumed,ledger,executor=Executor((1,)),prober=FakeProber())
    assert result['reason']=='native_success' and resumed.turn==2
    assert ledger.snapshot()['paused_seconds']>=.1
    assert ledger.snapshot()['wall_remaining']<=previous


def test_session_missing_usage_never_zero(ws):
    session=fake_session(ws,['pass'],raw="emit({'type':'thread.started','thread_id':'exact'});emit({'type':'turn.completed'})")
    list(session.start('task'))
    assert session.usage=='unknown'


def test_execution_limit_holds_are_per_request(tmp_path):
    ledger=AgentLedger(tmp_path/'db','c')
    ledger.reserve('r','execute',{'moving_executions':1},execution=True);ledger.claim('r')
    assert ledger.jobs()[0]['execution_limits']==dict(sdk_calls=320,control_steps=9600,wall_seconds=1920)


def test_final_slot_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    ledger=AgentLedger(tmp_path/'db','c')
    ledger.reserve('prior','test',{'formal_versions':9});ledger.claim('prior');ledger.settle('prior',{})
    def reserve(i):
        try:AgentLedger(tmp_path/'db','c').reserve(str(i),'test',{'formal_versions':1});return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(reserve,[1,2]))==1


@pytest.mark.parametrize('name,cap',[('sdk_calls',320),('control_steps',9600),('wall_seconds',1920)])
def test_execution_usage_boundaries(tmp_path,name,cap):
    ledger=AgentLedger(tmp_path/'db','cell')
    ledger.reserve('r','execute',{'moving_executions':1},execution=True);ledger.claim('r')
    ledger.record_execution('r',{name:cap})
    assert ledger.jobs()[0]['execution_usage'][name]==cap
    with pytest.raises(ValueError,match='execution_budget_exceeded'):ledger.record_execution('r',{name:cap+1})
    other=next(k for k in ('sdk_calls','control_steps','wall_seconds') if k!=name)
    assert ledger.jobs()[0]['execution_usage'][other]=='unknown'
    assert ledger.jobs()[0]['execution_held'][other]>0


def test_broker_republishes_settled_receipt_without_dispatch(ws):
    ledger=AgentLedger(ws.private/'db','c')
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,budget=LedgerBudget(ledger))
    req=dict(schema=SCHEMA,request_id='probe',op='observe',payload={})
    b.accept(req)
    end=time.monotonic()+5
    while b.active and time.monotonic()<end:time.sleep(.01)
    assert b.active is None;b.close()
    journal=ws.private/'journal/probe.json';record=json.loads(journal.read_bytes())
    record['response']={'status':'running','request_id':'probe'};journal.write_text(json.dumps(record))
    (ws.ws/'rpc/responses/probe.json').unlink()
    restored=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,budget=LedgerBudget(ledger))
    try:
        assert not restored.quarantined
        assert restored.accept(req)['status']=='completed'
        assert restored.prober.calls==0 and ledger.snapshot()['used']['read_only_probes']==1
    finally:restored.close()


def test_original_and_bundle_caps_both_enforced(ws):
    atomic_write(ws.ws,'work/submission.json',b'{"modules":["helper.py"]}')
    helper=b'X=1\n#';main=b'from robot_sdk import sensors\nfrom helper import X\n#'
    # The original sources fit exactly, but source-map comments exceed the cap.
    helper+=b'a'*(131072-len(main)-len(helper))
    atomic_write(ws.ws,'work/helper.py',helper);atomic_write(ws.ws,'work/main.py',main)
    with pytest.raises(ValueError,match='bundle_oversize'):bundle(ws.ws)
    atomic_write(ws.ws,'work/main.py',main+b'x')
    with pytest.raises(ValueError,match='source_oversize'):bundle(ws.ws)


def test_bundle_cross_module_global_scope_rejected(ws):
    atomic_write(ws.ws,'work/submission.json',b'{"modules":["a.py","b.py"]}')
    atomic_write(ws.ws,'work/a.py',b'def f():\n    return X\ndef g():\n    X=3\n    return X\n')
    atomic_write(ws.ws,'work/b.py',b'X=1\n')
    with pytest.raises(ValueError,match='unimported_shared_name'):bundle(ws.ws)


def test_periodic_quota_stop_without_any_tool_request(ws):
    ledger=AgentLedger(ws.private/'db','c');calls=[];reset=time.time()+1000
    def reading():
        calls.append(1)
        return dict(used_percent=90 if len(calls)>1 else 60,window_minutes=10080,resets_at=reset,observed_at=time.time())
    quota=QuotaMonitor(ws.private,ledger,reader=reading)
    session=fake_session(ws,['time.sleep(20)'])
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber(),quota=quota,quota_interval=.1)
    assert result['reason']=='quota_stop' and session.turn==1
    samples=[json.loads(line) for line in (ws.private/'quota.jsonl').read_text().splitlines()]
    assert any(s['reason']=='during_turn' for s in samples)


def test_unreadable_quota_prevents_new_turn(ws):
    ledger=AgentLedger(ws.private/'db','c')
    def unreadable():raise OSError('synthetic quota unreadable')
    quota=QuotaMonitor(ws.private,ledger,reader=unreadable)
    session=fake_session(ws,['pass'])
    with pytest.raises(QuotaUnavailable):drive(ws,session,ledger,executor=Executor(),prober=FakeProber(),quota=quota)
    assert session.turn==0 and ledger.snapshot()['model_turns']==0


def test_snapshot_failure_is_recorded_and_does_not_hang_or_end_the_run(ws):
    # r6 policy: replay snapshots are export steps; a failure is recorded privately.
    def fail(*a):raise ValueError('synthetic unsafe snapshot')
    ledger=AgentLedger(ws.private/'db','c')
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,
             budget=LedgerBudget(ledger),on_cut=fail)
    try:
        b.accept(dict(schema=SCHEMA,request_id='formal',op='submit',payload={'summary':'Try the planned motion.'}))
        until=time.monotonic()+10
        while b.records['formal']['response']['status'] not in ('completed','needs_review') and time.monotonic()<until:time.sleep(.02)
        assert b.records['formal']['response']['status']=='completed' and not b.quarantined
        assert b.executor.calls==1 and {e['step'] for e in b.step_errors}>={'cut_pre_submit','cut_post_exec','cut_post_feedback'}
        assert (ws.private/'step-errors').is_dir()
    finally:b.close()


def test_invalid_bundle_records_version_without_eval(ws):
    atomic_write(ws.ws,'work/submission.json',b'{"modules":["missing.py"]}')
    ledger=AgentLedger(ws.private/'db','c')
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=PassEval(),agent_mode=True,budget=LedgerBudget(ledger))
    try:
        b.accept(dict(schema=SCHEMA,request_id='bad',op='submit',payload={'summary':'Try the planned motion.'}))
        end=time.monotonic()+5
        while b.active and time.monotonic()<end:time.sleep(.01)
        assert not b.active
        assert b.records['bad']['response']['result']['eval']['status']=='not_run_invalid_submission'
        assert ledger.snapshot()['used']['formal_versions']==1 and ledger.snapshot()['used']['eval_runs']==0
        assert b.executor.calls==0
    finally:b.close()


def test_disallowed_sdk_ast_is_invalid_formal_not_fatal(ws):
    atomic_write(ws.ws,'work/main.py',b'import os\nos.system("anything")\n')
    ledger=AgentLedger(ws.private/'db','c')
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,budget=LedgerBudget(ledger))
    try:
        b.accept(dict(schema=SCHEMA,request_id='bad',op='submit',payload={'summary':'Try the planned motion.'}))
        end=time.monotonic()+5
        while b.active and time.monotonic()<end:time.sleep(.01)
        assert not b.active and not b.quarantined
        response=b.records['bad']['response']
        assert response['status']=='completed' and 'invalid_policy' in response['validation_error']
        assert ledger.snapshot()['used']['formal_versions']==1
    finally:b.close()


def test_crash_after_execution_keeps_receipt_and_unknown_usage(ws):
    ledger=AgentLedger(ws.private/'db','c')
    session=fake_session(ws,["tool('run','--no-eval');sys.exit(7)"])
    # r6 policy: a failed Dev turn is retried; 3 in a row is the session_failure cause.
    result=drive(ws,session,ledger,executor=Executor(),prober=FakeProber())
    assert result['reason']=='fatal_error' and result['fatal_cause']=='session_failure'
    assert 'turn_process_failed' in result['error']
    assert ledger.snapshot()['used']['moving_executions']==3
    turn=next(r for r in ledger.jobs() if r['kind']=='model_turn')
    assert turn['usage']=='unknown'
    request=next(r for r in ledger.jobs() if r['kind']=='request')
    assert request['state']=='settled'


def test_native_success_still_records_unavailable_eval_and_no_private_eval_input(ws):
    seen=[]
    class Unavailable(FakeEvaluator):
        def evaluate(self,execution,*,deadline):
            seen.append(json.dumps(execution));raise ValueError('synthetic invalid evaluation')
    result,session,ledger=run_fake(ws,["tool('submit');time.sleep(20)"],successes=(1,),evaluator=Unavailable())
    assert result['reason']=='native_success' and session.turn==1
    assert json.loads((ws.ws/'versions/v0001/eval.json').read_bytes())['status']=='unavailable'
    assert not any(word in ''.join(seen) for word in ('native_success','object_in_sink','native_result','check_success','reward'))


def test_missing_completion_evidence_is_not_completed_and_run_continues(ws):
    # r6 policy: unreadable native = "not completed", native=unknown privately.
    class Missing(Executor):
        def execute(self,*a,**kw):
            result=super().execute(*a,**kw);self.native_result_ref=None;return result
    ledger=AgentLedger(ws.private/'db','c');session=fake_session(ws,["tool('submit')","pass"])
    result=drive(ws,session,ledger,executor=Missing(),prober=FakeProber())
    assert result['reason']=='agent_idle' and session.turn==4
    assert (ws.ws/'versions/v0001/eval.json').exists()
    row=json.loads((ws.private/'versions/v0001.json').read_bytes())
    assert row['native_success'] is None and row['native']=='unknown' and 'completion_evidence_missing' in row['native_error']
    assert 'native' not in (ws.ws/'versions/v0001/eval.json').read_text()


def test_quota_sample_on_idempotent_retry_does_not_redispatch(ws):
    ledger=AgentLedger(ws.private/'db','c');calls=[];reset=time.time()+1000
    def reading():
        calls.append(1)
        return dict(used_percent=60 if len(calls)==1 else 90,window_minutes=10080,resets_at=reset,observed_at=time.time())
    monitor=QuotaMonitor(ws.private,ledger,reader=reading)
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,budget=LedgerBudget(ledger),quota=monitor)
    request=dict(schema=SCHEMA,request_id='p',op='observe',payload={})
    try:
        b.accept(request)
        end=time.monotonic()+5
        while b.active and time.monotonic()<end:time.sleep(.01)
        assert b.accept(request)['status']=='completed'
        assert b.stop_reason=='quota_stop' and b.prober.calls==1 and len(calls)==2
    finally:b.close()
