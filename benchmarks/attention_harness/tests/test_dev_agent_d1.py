"""Offline D1 contract tests. No simulator or model is started."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import pytest

from benchmarks.attention_harness.dev_agent_workspace import Workspace, read_beneath, atomic_write
from benchmarks.attention_harness.dev_agent_broker import Broker, Budget, FakeEvaluator, SCHEMA
from benchmarks.attention_harness.dev_agent_adapters import FakeExecutor, FakeProber
from benchmarks.attention_harness.dev_agent_diagnostics import project_planner_result, FORBIDDEN
from benchmarks.attention_harness import dev_agent_isolation as isolation

CONFIG=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920)
SOURCE=b'''from robot_sdk import left_arm
r = left_arm.move_delta(0.01,0,0)
print("first",r.get("rejected"))
left_arm.move_delta(0,0.01,0)
print("authored stdout")
print("authored stderr",file=stderr)
'''
TARGET=dict(side='left',position=[.1,.2,.3],quaternion_xyzw=[0,0,0,1])
ACTION=dict(group='left_arm',operation='move_delta',args=[.01,0,0],kwargs={})

@pytest.fixture
def workspace(tmp_path):
    ws=Workspace(tmp_path/'agent').prepare({'task_id':'fixture','task_prompt':'Move safely.'},CONFIG)
    atomic_write(ws.ws,'work/main.py',SOURCE)
    return ws


def request(rid='r1',op='observe',payload=None):
    payload = dict(payload or {})
    if op == 'submit':payload.setdefault('summary', 'Try the planned motion.')
    return dict(schema=SCHEMA,request_id=rid,op=op,payload=payload)


def broker(ws,**kw):
    return Broker(ws,executor=kw.pop('executor',FakeExecutor(unsafe_test_transport=True)),
        prober=kw.pop('prober',FakeProber()),evaluator=kw.pop('evaluator',FakeEvaluator()),agent_mode=True,**kw)


def settled(b,rid='r1'):
    until=time.monotonic()+5
    while time.monotonic()<until:
        value=b.records[rid]['response']
        if value['status'] in ('completed','rejected','needs_review'):return value
        time.sleep(.01)
    pytest.fail('broker did not settle')


def test_workspace_authority_and_atomic_publish(workspace,tmp_path):
    for name in ('docs','history','versions','executions','probes','events','rpc','work','scratch'):
        assert (workspace.ws/name).is_dir()
    (workspace.ws/'task.json').write_text('forged')
    workspace.regenerate()
    assert json.loads((workspace.ws/'task.json').read_text())['task_id']=='fixture'
    (workspace.ws/'task.json').unlink();(workspace.ws/'task.json').symlink_to(tmp_path/'outside')
    workspace.publish('task.json',{'task_id':'clean'})
    assert not (workspace.ws/'task.json').is_symlink()
    assert not (tmp_path/'outside').exists()
    assert not workspace.private.is_relative_to(workspace.ws)


@pytest.mark.parametrize('path',['../private/a','/etc/passwd','work/../a','work//a','work/./a','work\\a',''])
def test_path_escapes(workspace,path):
    with pytest.raises((ValueError,OSError)):read_beneath(workspace.ws,path)


def test_links_hardlinks_fifo_and_oversize(workspace,tmp_path):
    outside=tmp_path/'secret';outside.write_text('secret')
    (workspace.ws/'work/link.py').symlink_to(outside)
    os.link(outside,workspace.ws/'work/hard.py')
    os.mkfifo(workspace.ws/'work/fifo.py')
    (workspace.ws/'work/large.py').write_bytes(b'x'*131073)
    (workspace.ws/'escape').symlink_to(tmp_path,target_is_directory=True)
    for name in ('work/link.py','work/hard.py','work/fifo.py','work/large.py','escape/secret'):
        with pytest.raises((ValueError,OSError)):read_beneath(workspace.ws,name)
    (workspace.ws/'probes').rmdir();(workspace.ws/'probes').symlink_to(tmp_path)
    with pytest.raises(OSError):workspace.publish('probes/stolen',b'x')
    assert not (tmp_path/'stolen').exists()


def test_diagnostics_projection():
    value=project_planner_result(dict(ik_candidates=3,candidate_joint_margins=[dict(
        joint_margins_rad={'openarm_left_joint1':.01,'secret':99},minimum_joint_margin_rad=.01)],
        selected_candidate=1,solver_diagnostics={'position_error':[.1],'token':'SECRET'},
        validation={'exact_geometry':{'collision_free':False,'contacts':[{'bodies':['cup','left_hand'],'path':'/home/private'}]}},
        world_revision=1,nonce='SECRET',robot={'install':'/home/hidden'},native_success=True,reward=1,
        reason='/home/truares/secret',contacts=[],timing={'total_time':1}))
    text=json.dumps(value)
    assert not FORBIDDEN.search(text)
    assert 'cup' in text and 'openarm_left_joint1' in text and 'token' not in text
    assert value['diagnostics_complete'] is False and 'candidate_qpos' in value['missing']
    assert 'contacts' not in value['missing']  # Nested exact-geometry contacts are present.


def test_idempotency_busy_and_wait(workspace):
    gate=threading.Event()
    class Slow(FakeExecutor):
        def execute(self,*a,**kw):gate.wait(2);return super().execute(*a,**kw)
    b=broker(workspace,executor=Slow(unsafe_test_transport=True))
    try:
        req=request(op='submit',payload={'entry':'work/main.py'})
        b.accept(req)
        assert b.accept(request('r2','submit',{'entry':'work/main.py'}))['error']=='busy'
        assert b.accept(req)['request_id']=='r1'
        assert b.accept(request('r1','observe'))['error']=='request_id_conflict'
        assert b.budget.counts==dict(versions=1,executions=1,probes=0)
        gate.set();assert settled(b)['status']=='completed'
        b.accept(req);assert b.executor.calls==1
        (workspace.ws/'rpc/responses/r1.json').write_text('{}')
        b.accept(req);assert json.loads((workspace.ws/'rpc/responses/r1.json').read_text())['status']=='completed'
    finally:gate.set();b.close()


@pytest.mark.parametrize('kind,cap',[('versions',10),('executions',40),('probes',300)])
def test_budget_limit(kind,cap):
    b=Budget()
    for _ in range(cap):b.charge(**{kind:1})
    with pytest.raises(ValueError,match='budget_exhausted'):b.charge(**{kind:1})
    assert b.counts[kind]==cap
    with pytest.raises(ValueError):Budget(**{kind:cap+1})


def test_wall_and_atomic_budget():
    b=Budget(wall_seconds=0)
    with pytest.raises(ValueError,match='wall_stop'):b.charge(probes=1)
    b=Budget(executions=0)
    with pytest.raises(ValueError):b.charge(versions=1,executions=1)
    assert b.counts['versions']==0


def test_probe_timeout_is_a_charged_probe_error(workspace):
    # 10-28 policy: read-only probes never quarantine; the owner is restarted.
    gate=threading.Event()
    class Slow(FakeProber):
        def probe(self,*a,**kw):gate.wait(2);return super().probe(*a,**kw)
    b=broker(workspace,prober=Slow(),timeouts={'observe':.02})
    try:
        b.accept(request());value=settled(b)
        assert value['status']=='completed' and value['result']['probe_error']['error_class']=='TimeoutError'
        assert value['result']['owner_restarted'] is True and not b.quarantined
        assert b.budget.counts['probes']==1
        gate.set();b.accept(request('r2'));assert settled(b,'r2')['status']=='completed'
        assert b.budget.counts['probes']==2
    finally:gate.set();b.close()


def test_invalid_formal_consumes_version(workspace):
    atomic_write(workspace.ws,'work/bad.py',b'bad syntax !!!')
    b=broker(workspace)
    try:
        b.accept(request(op='submit',payload={'entry':'work/bad.py'}));r=settled(b)
        assert r['result']['outcome']['status']=='invalid_submission'
        assert b.budget.counts==dict(versions=1,executions=0,probes=0)
    finally:b.close()


def test_fake_agent_real_cli_file_protocol(workspace):
    b=broker(workspace);stop=threading.Event();thread=threading.Thread(target=b.serve,args=(stop,));thread.start()
    try:
        script='''import subprocess,json,sys
cli=sys.argv[1]
commands=[['observe'],['find_objects'],['ik_check','--target',sys.argv[2]],
['plan_dry_run','--action',sys.argv[3]],['run','--no-eval','--entry','work/main.py'],
['submit','--entry','work/main.py','--summary','Try the planned motion.']]
for command in commands:
 p=subprocess.run(['/usr/bin/python3',cli,*command,'--timeout','10'],capture_output=True,text=True)
 assert p.returncode==0,(p.stdout,p.stderr)
 value=json.loads(p.stdout)
 assert value['status']=='completed',value
 print(p.stdout,end='')
'''
        p=subprocess.run(['/usr/bin/python3','-c',script,str(workspace.ws/'docs/devtool.py'),json.dumps(TARGET),json.dumps(ACTION)],
            cwd=workspace.ws,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=25)
        assert p.returncode==0,p.stderr
        values=[json.loads(line) for line in p.stdout.splitlines()]
        assert values[2]['result']['rejected'] is True
        assert b.budget.counts==dict(probes=4,executions=2,versions=1)
        assert b.executor.calls==2 and b.prober.steps==0
        for eid in ('e000001','e000002'):
            root=workspace.ws/'executions'/eid
            assert (root/'stdout.txt').read_text()=='first True\nauthored stdout\n'
            assert (root/'stderr.txt').read_text()=='authored stderr\n'
            result=json.loads((root/'result.json').read_text())
            assert result['status']=='completed_with_rejections' and result['sdk_calls']==2
            assert (root/'final_observation.json').is_file()
        assert (workspace.ws/'versions/v0001/eval.json').is_file()
        assert len((workspace.ws/'history/probes.jsonl').read_text().splitlines())==4
        # REVIEW R3 exact whole-public-tree grep, including CLI, source and logs.
        for file in workspace.ws.rglob('*'):
            if file.is_file():
                assert not any(term in file.read_bytes() for term in
                    (b'native_success',b'object_in_sink',b'native_result',b'check_success',b'reward')),str(file)
    finally:stop.set();thread.join();b.close()


def test_isolation_profile_and_identity(tmp_path,monkeypatch):
    binary=tmp_path/'codex';binary.write_bytes(b'fixture binary')
    monkeypatch.setattr(isolation,'real_binary',lambda:binary)
    monkeypatch.setattr(isolation.subprocess,'check_output',lambda *a,**k:'codex-cli 0.155.1\n')
    identity=isolation.codex_identity();assert identity['sha256']==hashlib.sha256(b'fixture binary').hexdigest()
    profile=isolation.build_profile(tmp_path)
    assert '-s' not in profile and 'default_permissions="ws"' in profile
    assert '":minimal" = "read"' in profile[1] and '":project_roots"' in profile[1]
    flags=isolation.feature_args()
    assert 'features.skills=false' not in flags and 'features.skip_host_skill_discovery=true' in flags
    assert '--ignore-user-config' in flags


def test_preflight_pass_fail_and_missing_bwrap(workspace,monkeypatch):
    monkeypatch.setattr(isolation,'real_binary',lambda:Path('/usr/bin/codex'))
    monkeypatch.setattr(isolation.shutil,'which',lambda _: '/usr/bin/bwrap')
    checks=dict(outside_hidden=True,home_listing_restricted=True,workspace_rw=True,network_denied=True)
    def run(cmd,**kw):
        assert cmd[:2]==['codex','sandbox'] and kw['stdin']==subprocess.DEVNULL
        return SimpleNamespace(returncode=0,stdout=json.dumps(checks),stderr='')
    isolation.verify_runtime(workspace.ws,workspace.private,planner_port=18999,run=run)
    checks['outside_hidden']=False
    with pytest.raises(isolation.IsolationUnavailable):isolation.verify_runtime(workspace.ws,workspace.private,planner_port=18999,run=run)
    monkeypatch.setattr(isolation.shutil,'which',lambda _:None)
    with pytest.raises(isolation.IsolationUnavailable,match='bubblewrap_missing'):
        isolation.verify_runtime(workspace.ws,workspace.private,planner_port=18999,run=run)


def test_cli_timeout_wait_and_no_redispatch(workspace):
    b=broker(workspace);stop=threading.Event()
    cli=str(workspace.ws/'docs/devtool.py')
    try:
        first=subprocess.run(['/usr/bin/python3',cli,'observe','--request-id','resume1','--timeout','0'],capture_output=True,text=True)
        assert first.returncode==2 and json.loads(first.stdout)['status']=='pending'
        thread=threading.Thread(target=b.serve,args=(stop,));thread.start()
        second=subprocess.run(['/usr/bin/python3',cli,'wait','resume1','--timeout','5'],capture_output=True,text=True)
        assert second.returncode==0 and json.loads(second.stdout)['status']=='completed'
        assert b.budget.counts['probes']==1 and b.prober.calls==1
    finally:
        stop.set()
        if 'thread' in locals():thread.join()
        b.close()


def test_real_probe_adapter_laziness_and_zero_mutation(tmp_path):
    from benchmarks.attention_harness.dev_agent_adapters import Prober
    owners=[]
    class Owner:
        def __init__(self,**kw):self.calls=[];self.deadline=kw['deadline'];self.stopped=False;owners.append(self)
        def start(self):return {'scene_condition':{'matched':True}}
        def rpc(self,group,op,kwargs):
            assert (group,op)==('owner','agent_probe')
            self.calls.append(kwargs)
            return {'state_basis':'reset_scene','physics_steps':0}
        def stop(self):self.stopped=True
    p=Prober(CONFIG,sim_python='/fake',private_dir=tmp_path,owner_factory=Owner)
    assert owners==[]
    for op,payload in [('observe',{}),('find_objects',{}),('ik_check',{'target':TARGET}),('plan_dry_run',{'action':ACTION})]:
        assert p.probe(op,payload,deadline=time.monotonic()+5)['physics_steps']==0
    assert len(owners)==1 and len(owners[0].calls)==4
    p.close();assert owners[0].stopped


def test_real_probe_logic_held_start_limits_and_state(tmp_path,monkeypatch):
    import numpy as np
    from openarm365_dev_v2.agent_probe import probe,state_fingerprint
    data=SimpleNamespace(qpos=np.zeros(14),qvel=np.zeros(14),ctrl=np.zeros(14),act=np.zeros(1),
                         mocap_pos=np.zeros(3),mocap_quat=np.zeros(4),qacc_warmstart=np.zeros(14),time=0)
    fail=lambda:pytest.fail('physics/reset/trajectory dispatch forbidden')
    env=SimpleNamespace(sim=SimpleNamespace(data=data,forward=lambda:None),step=fail,reset=fail)
    executor=SimpleNamespace(sequence=0,physics_steps=0,held=None,limits=np.array([[-1,1]]*14))
    planner=SimpleNamespace(names=[f'openarm_{s}_joint{i}' for s in ('left','right') for i in range(1,8)],
        addresses=list(range(14)),ledger=SimpleNamespace(issue=fail,claim=fail),prepared=set())
    observe=lambda:{'perception':{'values':{'cup_pos':[1,2,3]}},'robot':{'joints':[0]*14}}
    before=state_fingerprint(env,executor)
    assert probe(env,executor,planner,observe,{'op':'observe','payload':{}})['physics_steps']==0
    assert probe(env,executor,planner,observe,{'op':'find_objects','payload':{}})['objects'][0]['name']=='cup'
    assert probe(env,executor,planner,observe,{'op':'ik_check','payload':{'target':TARGET,'held_object':True}})['unsupported_scope']=='held_object'
    with pytest.raises(ValueError,match='start_joint_limit'):
        probe(env,executor,planner,observe,{'op':'ik_check','payload':{'target':TARGET,'start_joints':{'openarm_left_joint1':2}}})
    assert state_fingerprint(env,executor)==before
    # Use actual IK-probe translation with fake planner transport; no executable plan issued.
    monkeypatch.setattr('openarm365_dev.environment.mount_pose',lambda _:np.eye(4))
    planner.prepare=lambda active:planner.prepared.add(tuple(active))
    planner.upload=lambda:None
    seen=[]
    planner.body=lambda active:{'current_q':data.qpos.tolist(),'active_joints':active}
    planner.client=SimpleNamespace(call=lambda path,body:(seen.append((path,body)) or {'status':'success','qpos':[0]*14}))
    result=probe(env,executor,planner,observe,{'op':'ik_check','payload':{'target':TARGET,'start_joints':{'openarm_left_joint1':.5}}})
    assert result['planning_accepted'] and seen[-1][1]['seed_q'][0]==.5
    assert state_fingerprint(env,executor)==before


def test_real_executor_adapter_uses_runner_and_only_public_inputs(tmp_path):
    from benchmarks.attention_harness.dev_agent_adapters import Executor
    seen=[]
    class Runner:
        def __init__(self,**kw):assert kw['agent_mode'];self.observer=kw['episode_observer'];self.cancel_event=threading.Event()
        def execute(self,request):
            seen.append(request)
            episode=request.artifact_root/'episode';episode.mkdir();self.observer(str(episode))
            (episode/'trace.json').write_text(json.dumps({'sdk_events':[{'status':'rejected'},{'status':'completed'}]}))
            (episode/'policy-stdout.txt').write_text('stdout')
            (episode/'policy-stderr.txt').write_text('stderr')
            (episode/'native-physics.jsonl').write_text('{"qpos":[0],"token":"secret","native_success":true}\n')
            # A private filename points at a forbidden read: adapter must not open it.
            (episode/'native_result.json').symlink_to('/nonexistent/forbidden')
            return {'status':'completed','native_success':True}
    e=Executor(dict(CONFIG,task_id='fixture',seed=101),sim_python='/fake',runner_factory=Runner)
    value=e.execute(SOURCE,tmp_path,deadline=time.monotonic()+3)
    assert value['stdout']=='stdout' and value['stderr']=='stderr' and len(value['trace'])==2
    assert not FORBIDDEN.search(json.dumps(value))
    assert seen[0].sdk_call_limit==320 and seen[0].effective_attempt_deadline_seconds<=3


def test_streams_not_protocol_and_fatal_stops(tmp_path):
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    from openarm365_dev_v2.agent_sdk import build_sdk
    events=[]
    class Owner:
        def rpc(self,*args,**kw):raise RuntimeError('fatal after motion')
    source='from robot_sdk import left_arm\nprint(\'{"kind":"done"}\')\nleft_arm.move_delta(0,0,0)\nprint("must not run")\n'
    outcome=execute_agent_policy(code=source,sdk=build_sdk(Owner(),events.append),context={},deadline=time.monotonic()+3,
        stderr_path=tmp_path/'policy-stderr.txt',unsafe_test_transport=True)
    assert outcome.status=='failed' and outcome.call_count==1
    assert 'must not run' not in (tmp_path/'policy-stdout.txt').read_text()


def test_sdk_budget_including_rejections(tmp_path):
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    from openarm365_dev_v2.agent_sdk import build_sdk
    owner=SimpleNamespace(rpc=lambda *a,**k:dict(ok=False,rejected=True,reason='ik_failed',diagnostics={}))
    events=[]
    outcome=execute_agent_policy(code='from robot_sdk import left_arm\nfor i in range(321):\n left_arm.move_delta(0,0,0)\n',
        sdk=build_sdk(owner,events.append),context={},deadline=time.monotonic()+5,stderr_path=tmp_path/'policy-stderr.txt',unsafe_test_transport=True)
    assert outcome.status=='failed' and outcome.call_count==320 and len(events)==320


def test_ask_help_denied_and_broker_lease(workspace):
    b=broker(workspace)
    try:
        with pytest.raises((BlockingIOError,ValueError)):broker(workspace)
        b.accept(request(op='ask_help',payload={'question':'help'}))
        assert settled(b)['result']['status']=='policy_denied'
        assert not any(b.budget.counts.values())
    finally:b.close()

from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment

@pytest.mark.parametrize('agent',[False,True])
def test_authenticated_rejection_client_recovery(tmp_path,monkeypatch,r3_deployment,agent):
    from openarm365_dev_v2.tests.test_r3_planning_rejection import client_for
    from curobo_v2.client import PlanningRejected
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    response=dict(status='failed',reason='ik_joint_margin_below_execution_limit',validation={'joint_limits':False},
        world_revision=1,world_sha256='world',execution_settings_sha256=r3_deployment.sha256,
        candidate_joint_margins=[{'minimum_joint_margin_rad':.001}],joint_margin_limit_rad=.02)
    client=client_for(tmp_path,monkeypatch,response);client.agent_mode=agent
    body=dict(env_id='robocasa365',execution_settings=r3_deployment.to_dict(),joint_names=['a','b'])
    with pytest.raises(RecoverablePlanRejection if agent else PlanningRejected):client.call('/plan/ik',body)
    assert client.failed is (not agent)
    if agent:
        client._request=lambda *a,**kw:dict(status='success',qpos=[0,0],validation={'joint_limits':True,'collision_free':True},
            world_revision=1,world_sha256='world',execution_settings_sha256=r3_deployment.sha256)
        assert client.call('/plan/ik',body)['status']=='success'


@pytest.mark.parametrize('fault',['world','settings','unknown','malformed'])
def test_rejection_auth_or_certificate_fault_latches(tmp_path,monkeypatch,r3_deployment,fault):
    from openarm365_dev_v2.tests.test_r3_planning_rejection import client_for
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    response=dict(status='failed',reason='ik_joint_margin_below_execution_limit',validation={},
        world_revision=1,world_sha256='world',execution_settings_sha256=r3_deployment.sha256)
    if fault=='world':response['world_revision']=2
    if fault=='settings':response['execution_settings_sha256']='wrong'
    if fault=='unknown':response['reason']='cancelled'
    if fault=='malformed':response.update(status='success',qpos=[float('inf')])
    client=client_for(tmp_path,monkeypatch,response);client.agent_mode=True
    with pytest.raises(Exception) as caught:
        client.call('/plan/ik',dict(env_id='robocasa365',execution_settings=r3_deployment.to_dict(),joint_names=['a']))
    assert not isinstance(caught.value,RecoverablePlanRejection)
    assert client.failed


def test_agent_planner_latch_only_fatal():
    from openarm365_dev_v2.agent_planner import AgentPlanner
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    planner=AgentPlanner.__new__(AgentPlanner);planner.failed=False;invalid=[];closed=[]
    planner.ledger=SimpleNamespace(invalidate=invalid.append);planner.owner=SimpleNamespace(close=closed.append)
    def reject():raise RecoverablePlanRejection({'reason':'official_ik_non_success'})
    with pytest.raises(RecoverablePlanRejection):planner.guard(reject)
    assert not planner.failed and not closed
    assert planner.guard(lambda:42)==42
    def fatal():raise RuntimeError('fault after motion')
    with pytest.raises(RuntimeError):planner.guard(fatal)
    assert planner.failed and closed==['planner_failure']


def test_old_paths_import_sentinel(tmp_path):
    # Fresh interpreter: cached modules cannot hide accidental eager imports.
    code='''import sys,importlib.abc,pytest
class Sentinel(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if '.dev_agent_' in fullname or fullname.startswith('openarm365_dev_v2.agent_'):
   raise AssertionError('old path imported agent module: '+fullname)
sys.meta_path.insert(0,Sentinel())
import benchmarks.attention_harness.dev_v2_loop
import benchmarks.attention_harness.codex_client
import openarm365_dev_v2.runner
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',
'openarm365_dev_v2/tests/test_r3_planning_rejection.py',
'openarm365_dev_v2/tests/test_r3_executor.py',
'benchmarks/attention_harness/tests/test_sandbox.py',
'benchmarks/attention_harness/tests/test_codex_client.py']))
'''
    p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=60)
    assert p.returncode==0,p.stdout+p.stderr


@pytest.mark.parametrize('op,collaborator',[('run','executor'),('submit','evaluator')])
def test_execution_timeout_holds_but_eval_timeout_is_unavailable(workspace,op,collaborator):
    gate=threading.Event()
    class SlowExecutor(FakeExecutor):
        def execute(self,*a,**kw):gate.wait(2);return super().execute(*a,**kw)
    class SlowEvaluator(FakeEvaluator):
        def evaluate(self,*a,**kw):gate.wait(2);return super().evaluate(*a,**kw)
    opts={collaborator:SlowExecutor(unsafe_test_transport=True) if collaborator=='executor' else SlowEvaluator()}
    b=broker(workspace,timeouts={op:.02 if op=='run' else 3,'evaluate':.02},**opts)
    try:
        b.accept(request(op=op,payload={'entry':'work/main.py'}))
        receipt=settled(b)
        assert b.budget.counts['executions']==1
        if collaborator=='evaluator':
            # D4/R4: unavailable Eval is neither a failed version nor a physical
            # execution of uncertain outcome. Public work may continue.
            assert receipt['status']=='completed'
            assert receipt['result']['eval']['status']=='unavailable'
            assert receipt['result']['eval']['passed'] is None
            assert b.accept(request('r2'))['status']=='accepted'
            assert settled(b,'r2')['status']=='completed'
        else:
            # r6 policy: a stopped, timed-out execution is that execution's result.
            assert receipt['status']=='completed' and not b.quarantined
            assert receipt['result']['execution_error']['error_class']=='TimeoutError'
            gate.set();time.sleep(.05)
            assert b.accept(request('r2'))['status']=='accepted'
    finally:gate.set();b.close()


def test_controller_fails_closed_before_launch(workspace,monkeypatch):
    monkeypatch.setattr(isolation,'codex_identity',lambda:dict(path='/fake',version='fixture',sha256='a'*64))
    def fail(*a,**kw):raise isolation.IsolationUnavailable('bwrap missing')
    monkeypatch.setattr(isolation,'verify_runtime',fail)
    monkeypatch.setattr(isolation.subprocess,'Popen',lambda *a,**kw:pytest.fail('controller started without preflight'))
    with pytest.raises(isolation.IsolationUnavailable):
        isolation.launch_controller(workspace.ws,workspace.private,'canary',planner_port=18999)


def test_authored_output_overflow_is_explicit(tmp_path):
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy,OUTPUT_LIMIT
    outcome=execute_agent_policy(code='from robot_sdk import sensors\nprint("x" * 17000000)\n',
        sdk=SimpleNamespace(),context={},deadline=time.monotonic()+5,
        stderr_path=tmp_path/'policy-stderr.txt',unsafe_test_transport=True)
    assert outcome.status=='failed' and outcome.error=='output_limit'
    assert (tmp_path/'policy-stdout.txt').stat().st_size==OUTPUT_LIMIT


def test_public_exception_uses_virtual_source_filename(tmp_path):
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    execute_agent_policy(code='from robot_sdk import sensors\nx = 1/0\n',sdk=SimpleNamespace(),context={},
        deadline=time.monotonic()+5,stderr_path=tmp_path/'policy-stderr.txt',unsafe_test_transport=True)
    text=(tmp_path/'policy-stderr.txt').read_text()
    assert 'work/main.py' in text and 'ZeroDivisionError' in text
    assert '/home/' not in text and 'agent_policy_worker' not in text


def test_dry_run_keyword_delta_normalization():
    from openarm365_dev_v2.agent_probe import validate,action_arguments
    action=dict(group='left_arm',operation='move_delta',args=[],kwargs={'dz':.01})
    validate('plan_dry_run',{'action':action})
    assert action_arguments(action)==[0.,0.,.01,[0.,0.,0.]]
    action['args']=[0]
    action['kwargs']={'dx':.01}
    with pytest.raises(ValueError):validate('plan_dry_run',{'action':action})


def test_agent_local_rejection_never_clears_after_motion(monkeypatch):
    from openarm365_dev_v2.agent_executor import AgentExecutor
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    from openarm365_dev.base_motion import BasePathRejected
    from openarm365_dev.executor import NativeExecutor
    executor=AgentExecutor.__new__(AgentExecutor);executor.failed=False
    executor.check_live=lambda:None
    attests=[]
    executor.planner=SimpleNamespace(client=SimpleNamespace(attest=lambda:attests.append(True)),
                                    ledger=SimpleNamespace(invalidate=lambda _:None))
    with pytest.raises(RecoverablePlanRejection,match='action_delta_limit'):
        executor.move_delta('left',.051,0,0,request_id='fake')
    assert attests==[True] and not executor.failed
    executor.env=object()
    monkeypatch.setattr('openarm365_dev_v2.agent_executor.state_fingerprint',lambda *a:'state')
    def after_motion(*a,**kw):
        executor.failed=True
        raise BasePathRejected('fatal_after_motion',{})
    monkeypatch.setattr(NativeExecutor,'base_move',after_motion)
    with pytest.raises(BasePathRejected):executor.base_move(0,0,0,request_id='fake')
    assert executor.failed and attests==[True]


def test_real_bwrap_transport_runs_policy(tmp_path):
    # 10-28 offline e2e: every real execution died on "bwrap: Unknown option --preserve-fds".
    import shutil
    if shutil.which('bwrap') is None:pytest.skip('bubblewrap required')
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    from openarm365_dev_v2.agent_sdk import build_sdk
    events=[]
    owner=SimpleNamespace(rpc=lambda *a,**k:{'robot0_left_joint_pos':[0.]*7})
    source='from robot_sdk import sensors\nprint("obs", len(sensors.get_observation()))\n'
    outcome=execute_agent_policy(code=source,sdk=build_sdk(owner,events.append),context={},deadline=time.monotonic()+20,
        stderr_path=tmp_path/'policy-stderr.txt')
    assert outcome.status=='completed',(tmp_path/'policy-stderr.txt').read_text()
    assert outcome.call_count==1 and 'obs 1' in (tmp_path/'policy-stdout.txt').read_text()
