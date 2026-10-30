"""D5 offline probes and operator routes; never starts a server or simulator."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
import numpy as np
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity
from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG,TARGET,ACTION,SOURCE,request,settled
from benchmarks.attention_harness.dev_agent_workspace import Workspace,atomic_write,encode
from benchmarks.attention_harness.dev_agent_broker import Broker,Budget,FakeEvaluator
from benchmarks.attention_harness.dev_agent_adapters import FakeProber,Prober
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger,LedgerBudget
from benchmarks.attention_harness.dev_agent_view import projection,artifact,clean
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness import dev_experiment,daily_dev_v2
from openarm365_dev_v2.agent_probe import probe,state_fingerprint,validate
from openarm365_dev.base_motion import BasePathRejected,required_pair_distance
from curobo_v2.execution_settings import R4_20261027

OFFSET=dict(dx=0,dy=.09,dyaw=.1)

class Executor:
    def execute(self,*a,**kw):
        return dict(status='completed',stdout='Trying the safe offset\n',stderr='Rejected approach; retrying\n',
                    trace=[],diagnostics=[dict(joint_margin_limit_rad=.02)],final_observation={})
    def cancel(self):pass


def owner(monkeypatch,clearance=.010):
    data=SimpleNamespace(qpos=np.zeros(17),qvel=np.zeros(17),ctrl=np.zeros(17),act=np.zeros(1),
        mocap_pos=np.zeros(3),mocap_quat=np.zeros(4),qacc_warmstart=np.zeros(17),time=0.)
    def forbidden(*a,**kw):pytest.fail('stepping/reset/trajectory forbidden')
    forwards=[]
    env=SimpleNamespace(sim=SimpleNamespace(data=data,forward=lambda:forwards.append(data.qpos.copy())),
        robots=[SimpleNamespace(_ref_base_joint_pos_indexes=np.array([14,15,16]))],step=forbidden,reset=forbidden)
    names=[f'openarm_{s}_joint{i}' for s in ('left','right') for i in range(1,8)]
    executor=SimpleNamespace(sequence=0,physics_steps=0,held=None,limits=np.array([[-1,1]]*14),
        execution_settings=R4_20261027,base_initial=np.zeros(3),epoch=0,any_object_contact=lambda:False)
    seen=[]
    planner=SimpleNamespace(names=names,addresses=list(range(14)),chassis_binding={},prepared=set(),
        ledger=SimpleNamespace(issue=forbidden,claim=forbidden),agent_probe_only=False)
    planner.prepare=lambda active:planner.prepared.add(tuple(active))
    planner.upload=lambda:seen.append(data.qpos.copy())
    planner.body=lambda active:dict(current_q=data.qpos[:14].tolist(),active_joints=active)
    planner.client=SimpleNamespace(call=lambda path,body:dict(status='success',qpos=[0.]*14))
    monkeypatch.setattr('openarm365_dev.environment.mount_pose',lambda env:np.eye(4))
    monkeypatch.setattr('openarm365_dev.world.snapshot',lambda *a,**kw:{'reset':True})
    checks=[]
    def exact(env,world,binding,delta,initial,*,execution_settings):
        checks.append((delta.copy(),execution_settings,data.qpos.copy()))
        required=required_pair_distance(.04,execution_settings=execution_settings)
        audit=dict(status='clear' if clearance>=required else 'rejected',minimum_clearance_m=clearance,
            minimum_clearance_pair=dict(obstacle='counter',robot_geom='chassis',clearance_m=clearance),
            start_minimum_clearance_m=.04,start_minimum_clearance_pair=dict(obstacle='counter'),
            required_clearance_m=execution_settings.base_path_clearance_m,
            absolute_min_distance_m=execution_settings.base_start_clearance_m)
        if clearance<required:raise BasePathRejected('too close',audit)
        return audit
    monkeypatch.setattr('openarm365_dev.base_motion.check_exact_base_path',exact)
    return env,executor,planner,seen,checks,forwards

@pytest.mark.parametrize('clearance,accepted',[(.0093,False),(.010,True),(.0101,True)])
def test_offset_exact_threshold_restored_and_arm_still_probed(monkeypatch,clearance,accepted):
    env,ex,planner,seen,checks,forwards=owner(monkeypatch,clearance)
    before=state_fingerprint(env,ex)
    result=probe(env,ex,planner,lambda:{},request(op='ik_check',payload=dict(target=TARGET,base_offset=OFFSET,
        start_joints={'openarm_left_joint1':.3})))
    assert state_fingerprint(env,ex)==before and ex.sequence==ex.physics_steps==0
    assert planner.agent_probe_only is False and len(forwards)==3
    assert seen[0][14:]==pytest.approx([0,.09,.1]) and seen[0][0]==.3
    assert checks[0][1] is R4_20261027
    assert checks[0][2][14:]==pytest.approx([0,0,0])
    assert result['base_clearance']['accepted'] is accepted
    assert result['base_clearance']['minimum_clearance_pair']['obstacle']=='counter'
    assert result['base_clearance']['minimum_clearance_m']==clearance
    assert result['base_clearance']['required_clearance_m']==.01
    assert result['base_clearance']['absolute_min_distance_m']==R4_20261027.base_start_clearance_m
    assert result['planning_accepted'] is True
    assert len(result['diagnostics']['goal_margin']['joint_margins_rad'])==7
    assert result['diagnostics']['goal_margin']['joint_margin_limit_rad']==.02


def test_offset_restores_on_planner_failure_and_margin_rejection(monkeypatch):
    env,ex,planner,*_=owner(monkeypatch)
    before=state_fingerprint(env,ex)
    planner.client.call=lambda *a:dict(qpos=[.99]*14)
    result=probe(env,ex,planner,lambda:{},request(op='ik_check',payload=dict(target=TARGET,base_offset=OFFSET)))
    assert not result['planning_accepted'] and result['diagnostics']['goal_margin']['minimum_joint_margin_rad']==pytest.approx(.01)
    assert state_fingerprint(env,ex)==before
    def fail(*args):raise RuntimeError('solver_error')
    planner.client.call=fail
    with pytest.raises(RuntimeError,match='solver_error'):
        probe(env,ex,planner,lambda:{},request(op='ik_check',payload=dict(target=TARGET,base_offset=OFFSET)))
    assert state_fingerprint(env,ex)==before and not planner.agent_probe_only


def test_plan_offset_rejection_diagnostics_restores(monkeypatch):
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    env,ex,planner,*_=owner(monkeypatch)
    def plan(*args,**kw):
        assert env.sim.data.qpos[15]==.09 and planner.agent_probe_only
        raise RecoverablePlanRejection(dict(reason='goal_joint_margin_below_execution_limit',joint_margin_limit_rad=.02,
            joint_margins_rad={n:.01 for n in planner.names}))
    planner.plan_joints=plan
    before=state_fingerprint(env,ex)
    result=probe(env,ex,planner,lambda:{},request(op='plan_dry_run',payload=dict(base_offset=OFFSET,
        action=dict(group='bimanual',operation='move_joints',args=[[.99]*14],kwargs={}))))
    assert result['base_clearance']['accepted'] and not result['planning_accepted']
    assert len(result['diagnostics']['joint_margins_rad'])==14
    assert state_fingerprint(env,ex)==before

@pytest.mark.parametrize('offset',[{},dict(dx=0,dy=0),dict(dx=0,dy=float('nan'),dyaw=0),dict(dx=True,dy=0,dyaw=0),dict(dx=0,dy=.61,dyaw=0),dict(dx=0,dy=0,dyaw=.91)])
def test_bad_offsets(offset):
    with pytest.raises(Exception):validate('ik_check',dict(target=TARGET,base_offset=offset))


def test_fake_owner_offset_rpc_and_single_budget(tmp_path):
    calls=[]
    class Owner:
        def __init__(self,**kw):pass
        def start(self):return {}
        def rpc(self,group,op,kwargs):
            calls.append(kwargs)
            return dict(state_basis='reset_scene',physics_steps=0,planning_accepted=False,
                base_clearance=dict(accepted=False,minimum_clearance_m=.0093,minimum_clearance_pair=dict(obstacle='counter')))
        def stop(self):pass
    ws=Workspace(tmp_path/'agent').prepare({'task_id':'fake'},CONFIG)
    p=Prober(CONFIG,sim_python='unused',private_dir=ws.private,owner_factory=Owner)
    b=Broker(ws,executor=Executor(),prober=p,evaluator=FakeEvaluator(),agent_mode=True,budget=Budget())
    try:
        req=request('offset','ik_check',dict(target=TARGET,base_offset=OFFSET))
        b.accept(req);result=settled(b,'offset');b.accept(req)
        assert b.budget.counts==dict(versions=0,executions=0,probes=1)
        assert len(calls)==1 and calls[0]['payload']['base_offset']==OFFSET
        assert result['result']['base_clearance']['minimum_clearance_m']==.0093
    finally:b.close()


def fixture_run(root,offline_dev_identity,label='native_success'):
    request_key='d5-fixture-'+label
    import hashlib
    eid='devexp-'+hashlib.sha256(request_key.encode()).hexdigest()[:24]
    directory=Path(root)/eid;directory.mkdir(parents=True,exist_ok=True)
    store=DevV2Store(directory/'ledger.sqlite3')
    selection=dict(request_key=request_key,tasks=['open_cabinet'],seeds=[102],policies=['reactive_help'],
        mode='interactive',dev_mode='agent',dev_model='gpt-6-sol',eval_model='gpt-6-astra',goal_key=request_key,
        name_suffix='D5 fixture · '+label)
    exp=dev_experiment.create(store,directory/'artifacts',selection,agent_policy_version='agent-policy-v1')
    cell=exp['cells'][0];key=cell['key'];cellroot=Path(exp['artifact_dir'])/'cells'/key
    store.update_cell(key,artifact_dir=str(cellroot),dev_mode='agent')
    ws=Workspace(cellroot/'agent').prepare(dict(task_id='open_cabinet',task_prompt='Synthetic fixture; no robot runs.'),CONFIG)
    atomic_write(ws.ws,'work/main.py',SOURCE)
    ledger=AgentLedger(store.path,key,clock=lambda:time.time()-3600)
    class Eval:
        n=0
        def evaluate(self,*a,**kw):
            self.n+=1
            return dict(status='completed' if self.n<3 else 'unavailable',passed=False if self.n==1 else True if self.n==2 else None,
                        reason='Blocked approach' if self.n==1 else 'Eval unavailable: timeout' if self.n==3 else 'Visible success',
                        advice='Scan a smaller base offset.',evidence=[dict(source='stdout.txt',claim='Approach was rejected')])
        def cancel(self):pass
    def version(response,value):
        n=int(response['version_id'][1:]);native=n==3
        store.version(key,dict(version=n,version_id=response['version_id'],execution_id=response.get('execution_id'),valid=True,formal_eligible=False,
            eval=value['eval'],eval_verdict='fail' if n==1 else 'pass' if n==2 else 'unavailable',native_success=native,
            agree=True if n==1 else False if n==2 else None))
    b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=Eval(),agent_mode=True,
             budget=LedgerBudget(ledger),on_version=version)
    try:
        for rid,op,payload in [('probe','ik_check',dict(target=TARGET,base_offset=OFFSET)),('dry','plan_dry_run',dict(action=ACTION,base_offset=OFFSET)),
            ('noeval','run',{}),('v1','submit',{}),('help','ask_help',{'question':'How far should the base move?'}),('v2','submit',{}),('v3','submit',{})]:
            if op=='submit':  # each formal version changes the code (identical resubmits are rejected)
                atomic_write(ws.ws,'work/main.py',(ws.ws/'work/main.py').read_bytes()+('\n# %s\n'%rid).encode())
            b.accept(request(rid,op,payload));settled(b,rid)
    finally:b.close()
    ledger.quota(dict(used_percent=90 if label=='quota_stop' else 72.5,stop_percent=90,at=time.time()-20))
    phases={'turn_running':('model_turn',{'model_turns':1}),'execution':('request',{'moving_executions':1}),
            'eval':('eval',{'eval_runs':1,'formal_versions':1}),'help':('help',{'help':1})}
    if label in phases:
        kind,resources=phases[label];ledger.reserve('active',kind,resources);ledger.claim('active')
        state='running';reason=None
    else:state='completed' if label=='native_success' else 'needs_review' if label=='fatal_error' else 'failed';reason=label
    store.update_cell(key,state=state,reason=reason,agent_resources=ledger.snapshot(),
                      **({'error':'broker_invariant: Solver exploded: fixture error verbatim','fatal_cause':'broker_invariant'} if label=='fatal_error' else {}))
    if label=='fatal_error':
        atomic_write(ws.private,'errors/failure.json',encode(dict(error='Failure opening /home/example/private/data',error_class='RuntimeError')))
        atomic_write(ws.private,'eval/v0003/error.json',encode(dict(error_class='timeout',error_text='Eval exceeded its hard deadline')))
        atomic_write(ws.private,'turns/t000001/stderr.txt',b'Codex failed: synthetic failure\n')
    with store.tx() as db:
        e=store.get(db,'experiments',eid);e.update(state='running' if label in phases else 'completed',reason=reason)
        store.put(db,'experiments',eid,e)
    atomic_write(directory,'authorization.json',encode(dict(authorization_mode='per_run_budget',deadline_epoch=None)))
    atomic_write(directory,'selection.json',encode(selection))
    return store,store.cell(key),ws

@pytest.fixture
def agent_fixture(tmp_path,offline_dev_identity):return fixture_run(tmp_path,offline_dev_identity)


def test_operator_endpoint_and_public_artifacts(agent_fixture):
    store,cell,ws=agent_fixture
    bridge=object.__new__(daily_dev_v2.DailyDevV2);bridge.store=lambda eid:store
    base=f'/api/dev-v2/experiments/{cell["experiment_id"]}/cells/{cell["key"]}'
    view,mime=bridge.get(base+'/view')
    assert view['agent']['counts']['formal_versions']==3 and view['agent']['counts']['moving_executions']==4
    assert view['agent']['counts']['read_only_probes']==2
    assert len(view['agent']['timeline'])==7 and view['agent']['reason']=='native_success'
    assert view['versions'][1]['native_success'] is False and view['versions'][1]['agree'] is False
    text=json.dumps(view)
    assert str(ws.root) not in text and '/home/' not in text and 'private/' not in text
    for row in view['agent']['timeline']:
        for link in row['links']:
            value,mime=bridge.get(link['url']);assert value is not None
    for path in ('files/agent/private/versions/v0001.json','agent/files/../private/versions/v0001.json',
                 'agent/files/versions/v0001/native_result.json','agent/files/work/main.py'):
        with pytest.raises(FileNotFoundError):bridge.get(base+'/'+path)
    # Dev-visible tree still passes R3; only the operator projection has outcomes.
    import re
    forbidden=re.compile(r'native_success|object_in_sink|native_result|check_success|reward')
    for path in ws.ws.rglob('*'):
        if path.is_file():assert not forbidden.search(path.read_text()),path
    # Tampering with the public mirror cannot spoof trusted evidence.
    (ws.ws/'executions/e000001/stdout.txt').write_text('forged')
    with pytest.raises(FileNotFoundError,match='digest'):artifact(cell,'executions/e000001/stdout.txt')

@pytest.mark.parametrize('label,phase',[(x,'stopped') for x in ('native_success','quota_stop','agent_idle','budget_exhausted','wall_stop','fatal_error')]+
                         [('turn_running','turn running'),('execution','waiting on execution'),('eval','Eval'),('help','help')])
def test_all_states_and_build_browser_fixture(tmp_path,offline_dev_identity,label,phase):
    root=Path(os.environ.get('D5_FIXTURE_DIR',tmp_path))
    store,cell,ws=fixture_run(root,offline_dev_identity,label)
    result=projection(store,cell)
    assert result['agent']['phase']==phase
    if label=='fatal_error':
        text=json.dumps(result)
        assert 'fixture error verbatim' in text and 'Eval exceeded its hard deadline' in text and 'Codex failed' in text
        assert result['agent']['fatal_cause']=='broker_invariant'
        from benchmarks.attention_harness.dev_agent_loop import end_line
        line=end_line('k',dict(state='needs_review',reason='fatal_error',fatal_cause='broker_invariant',error=cell['error']))
        assert 'fatal_cause=broker_invariant' in line and 'fixture error verbatim' in line
        assert '/home/example' not in text and '[host path]' in text
    if os.environ.get('D5_FIXTURE_DIR'):
        (root/(label+'.view.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))

@pytest.mark.parametrize('change,reason',[(dict(budget_tier='formal'),'modern_dev'),(dict(budget_tier='legacy'),'modern_dev'),
    (dict(configuration={}), 'modern_dev'),(dict(seeds=[101,102]),'single_cell'),(dict(policies=['autonomous','reactive_help']),'single_cell')])
def test_daily_negative_gates_before_translation_or_writes(tmp_path,change,reason):
    bridge=daily_dev_v2.DailyDevV2(tmp_path,[],per_run_budget=True)
    selection=dict(request_key='d5-negative-test',tasks=['open_cabinet'],seeds=[102],policies=['autonomous'],mode='interactive',
        dev_mode='agent',model_source='codex',budget_tier='dev',**{})
    selection.update(change)
    with pytest.raises(ValueError,match=reason):bridge.create(dict(selection=selection,start=False))
    assert not list(tmp_path.glob('devexp-*'))


def test_path_symlink_and_missing_evidence_fail_closed(agent_fixture,tmp_path):
    store,cell,ws=agent_fixture
    path=ws.ws/'executions/e000001/stdout.txt';path.unlink();path.symlink_to(tmp_path/'outside')
    with pytest.raises(FileNotFoundError):artifact(cell,'executions/e000001/stdout.txt')
    with pytest.raises(FileNotFoundError):artifact(cell,'probes/p999999.json')
    with pytest.raises(FileNotFoundError):artifact(cell,'/etc/passwd')
    with pytest.raises(FileNotFoundError):artifact(dict(cell,dev_mode='one_call'),'probes/p000001.json')


def test_accounting_disagreement_visible_and_reads_do_not_mutate(agent_fixture):
    store,cell,ws=agent_fixture
    cell['agent_resources']['used']['formal_versions']=9
    before=store.path.read_bytes();value=projection(store,cell)
    assert value['agent']['counts']['formal_versions']==3
    assert 'persisted_count_disagreement' in json.dumps(value['agent']['errors'])
    assert store.path.read_bytes()==before


def test_daily_creation_reaches_real_create_and_binds_automatic_memory(tmp_path,offline_dev_identity,monkeypatch):
    import subprocess
    original=subprocess.run
    calls=[]
    def execute(argv,*args,**kwargs):
        if 'benchmarks.attention_harness.dev_experiment' not in argv:return original(argv,*args,**kwargs)
        def arg(name):return argv[argv.index(name)+1]
        selection=json.loads(Path(arg('--selection')).read_bytes());calls.append(selection)
        dev_experiment.create(DevV2Store(arg('--store')),Path(arg('--artifacts')),selection)
        return SimpleNamespace(returncode=0)
    bridge=daily_dev_v2.DailyDevV2(tmp_path,[],per_run_budget=True)
    # D7: agent runs bind the server's Memory authority exactly like one-call runs.
    memory={'base_url':'http://127.0.0.1:8878','namespace':'attentionbench-dev-v2-c','store_id':'00000000-0000-4000-8000-000000000000','url_sha256':__import__('hashlib').sha256(b'http://127.0.0.1:8878').hexdigest()}
    bridge.memory=memory
    monkeypatch.setattr(subprocess,'run',execute)
    body=dict(start=False,selection=dict(request_key='d5-ui-creation',tasks=['open_cabinet'],seeds=[102],
        policies=['reactive_help'],mode='experiment',dev_mode='agent',model_source='codex',budget_tier='dev',
        dev_model='gpt-6-sol',eval_model='gpt-6-astra',quota_stop_percent=90,goal_key='d5-ui-goal'))
    result=bridge.create(body)
    assert calls and calls[0]['model_source']=='codex' and 'configuration' not in calls[0]
    assert calls[0]['memory_authority']==memory and calls[0]['dev_mode']=='agent'
    assert 'uri' not in json.dumps(result) and '/home/' not in json.dumps(result)
    e=dev_experiment.snapshot(bridge.store(result['experiment_id']),result['experiment_id'])
    manifest=dev_experiment.verify_manifest(e)
    assert manifest['dev_model']=='gpt-6-sol' and manifest['eval_model']=='gpt-6-astra'
    assert manifest['quota_stop_percent']==90 and manifest['selection']['mode']=='experiment'
    assert bridge.list()[0]['locked']['config']['dev_mode']=='agent'
    repeated=bridge.create(body)
    assert repeated==result
    for field,value in [('dev_model','gpt-6-astra'),('eval_model','gpt-6-sol'),('quota_stop_percent',91)]:
        with pytest.raises(Exception,match='different conditions'):
            bridge.create(dict(body,selection={**body['selection'],field:value}))


def test_arm_dry_run_success_hypothetical_state_and_no_issue(monkeypatch):
    env,ex,planner,seen,checks,_=owner(monkeypatch)
    from openarm365_dev import executor as motion
    from openarm365_dev.environment import tcp_pose
    ex.max_speed=1
    env.control_timestep=.1
    planner.spec={'robot_cfg':{'kinematics':{'cspace':{'max_acceleration':1,'max_jerk':1}}}}
    def plan(*args,**kwargs):
        assert env.sim.data.qpos[15]==.09 and planner.agent_probe_only
        return dict(trajectory=[[0.]*14,[.1]*14],time_from_start_seconds=[0,.1],
            goal_margin=dict(joint_margin_limit_rad=.02,joint_margins_rad={n:.9 for n in planner.names}))
    planner.plan_joints=plan
    monkeypatch.setattr(motion,'diagnostic_settle_steps',lambda:0)
    monkeypatch.setattr(motion,'official_timing_exceeds_ceiling',lambda *a:False)
    monkeypatch.setattr(motion,'sample_for_original_drive',lambda *a,**kw:None)
    before=state_fingerprint(env,ex)
    result=probe(env,ex,planner,lambda:{},request(op='plan_dry_run',payload=dict(base_offset=OFFSET,
        action=dict(group='bimanual',operation='move_joints',args=[[.1]*14],kwargs={}))))
    assert result['planning_accepted'] and result['base_clearance']['accepted']
    assert len(result['diagnostics']['goal_margin']['joint_margins_rad'])==14
    assert state_fingerprint(env,ex)==before


def test_rejected_goal_margin_retains_every_joint(tmp_path):
    from openarm365_dev_v2.agent_planner import AgentPlanner
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    names=[f'openarm_{s}_joint{i}' for s in ('left','right') for i in range(1,8)]
    urdf=tmp_path/'robot.xml'
    urdf.write_text('<robot>'+''.join(f'<joint name="{n}"><limit lower="-1" upper="1"/></joint>' for n in names)+'</robot>')
    planner=object.__new__(AgentPlanner);planner.names=names;planner.execution_settings=R4_20261027
    planner.client=SimpleNamespace(attest=lambda:None)
    planner.spec={'robot_cfg':{'kinematics':{'urdf_path':str(urdf)}}}
    with pytest.raises(RecoverablePlanRejection) as caught:planner.check_goal_margin([.99]*14,names)
    value=caught.value.public()['diagnostics']
    assert len(value['joint_margins_rad'])==14 and value['joint_margin_limit_rad']==.02
    assert value['minimum_joint_margin_rad']==pytest.approx(.01)


def test_missing_artifacts_are_explicit(agent_fixture):
    store,cell,ws=agent_fixture
    (ws.canonical/'executions/e000001/stdout.txt').unlink()
    value=projection(store,cell)
    links=next(r for r in value['agent']['timeline'] if r['id']=='noeval')['links']
    assert any(link['available'] is False and 'stdout' in link['label'] for link in links)


def test_isolated_http_routes_without_binding_a_socket(agent_fixture,monkeypatch):
    import io
    from benchmarks.attention_harness import dev_ui_server
    store,cell,ws=agent_fixture
    monkeypatch.setattr(dev_ui_server,'ThreadingHTTPServer',lambda address,handler:handler)
    monkeypatch.setattr(dev_ui_server.secrets,'token_urlsafe',lambda size:'fixture-token')
    handler=dev_ui_server.serve(store,ws.root,8790)  # Fake class above: no server/socket starts.
    def get(path):
        instance=object.__new__(handler);instance.path=path;instance.headers={'X-Attention-Token':'fixture-token'}
        instance.wfile=io.BytesIO();status=[]
        instance.send_response=status.append;instance.send_header=lambda *a:None;instance.end_headers=lambda:None
        instance.do_GET()
        raw=instance.wfile.getvalue()
        assert b'/home/' not in raw and str(ws.root).encode() not in raw
        return status[0],raw
    prefix=f'/api/dev/experiments/{cell["experiment_id"]}/cells/{cell["key"]}'
    status,raw=get(prefix+'/agent/view');assert status==200
    view=json.loads(raw);assert view['agent']['counts']['read_only_probes']==2
    for row in view['agent']['timeline']:
        for link in row['links']:
            if link.get('url'):assert get(link['url'])[0]==200
    assert get(prefix+'/files/agent/private/versions/v0001.json')[0]==404
    assert get(prefix+'/agent/files/versions/v0001/native_result.json')[0]==404
    assert get(f'/api/dev/experiments/{cell["experiment_id"]}')[0]==200
    assert get('/api/dev/experiments')[0]==200
    assert get('/api/dev/resources')[0]==200


def test_operator_execution_error_is_visible_without_public_leak(agent_fixture):
    store,cell,ws=agent_fixture
    atomic_write(ws.private,'executions/e000001/controller-error.json',encode(dict(error='Exact runner error at /tmp/host/secret.py')))
    value=projection(store,cell)
    assert 'Exact runner error at [host path]' in json.dumps(value['agent']['errors'])
    assert '/tmp/host/' not in json.dumps(value)
    assert not (ws.ws/'executions/e000001/controller-error.json').exists()
    with pytest.raises(FileNotFoundError):artifact(cell,'executions/e000001/controller-error.json')


def test_ik_transport_uses_service_seed_contract(monkeypatch):
    import ast
    # Inspect the actual installed repository schema without importing GPU/owner config.
    tree=ast.parse(Path('curobo_v2/service/curobo_service/server.py').read_text())
    schemas={n.name:n for n in tree.body if isinstance(n,ast.ClassDef)}
    def fields(name):return {n.target.id for n in schemas[name].body if isinstance(n,ast.AnnAssign)}
    assert 'seed_q' in fields('IKRequest') and 'current_q' not in fields('IKRequest')
    assert 'current_q' in fields('WarmupRequest')
    env,ex,planner,*_=owner(monkeypatch)
    seen=[]
    def call(path,body):
        seen.append((path,body))
        if path=='/plan/ik':
            assert 'current_q' not in body and body['seed_q'][0]==.4
        elif path=='/warmup':assert body['current_q'][0]==.4 and 'seed_q' not in body
        return dict(status='success',qpos=[0.]*14)
    planner.client.call=call
    result=probe(env,ex,planner,lambda:{},request(op='ik_check',payload=dict(target=TARGET,base_offset=OFFSET,
        start_joints={'openarm_left_joint1':.4})))
    assert result['planning_accepted'] and [p for p,b in seen]==['/warmup','/plan/ik']


def test_creation_errors_reach_operator_with_paths_redacted(tmp_path,monkeypatch):
    def fail(argv,**kw):
        kw['stderr'].write(b'Exact creation failure at /home/host/module.py\n')
        kw['stderr'].flush()
        return SimpleNamespace(returncode=1)
    bridge=daily_dev_v2.DailyDevV2(tmp_path,[],per_run_budget=True)
    monkeypatch.setattr(daily_dev_v2.subprocess,'run',fail)
    with pytest.raises(Exception) as caught:
        bridge.create(dict(start=False,selection=dict(request_key='d5-create-error',tasks=['open_cabinet'],seeds=[102],
            policies=['autonomous'],mode='interactive',dev_mode='agent',goal_key='d5-create-error-goal')))
    assert 'Exact creation failure' in str(caught.value) and '/home/' not in str(caught.value)


def test_demo_and_help_errors_are_operator_visible(agent_fixture):
    store,cell,ws=agent_fixture
    ws.publish('events/demo.json',dict(status='delivered',content='Synthetic fixed demo'))
    atomic_write(ws.private,'help/v0001/error.json',encode(dict(error='Exact Advisor failure',error_class='RuntimeError')))
    value=projection(store,cell)
    assert any(r['op']=='demo' and r['result']['content']=='Synthetic fixed demo' for r in value['agent']['timeline'])
    assert 'Exact Advisor failure' in json.dumps(value['agent']['errors'])
    assert artifact(cell,'events/demo.json')[0]['content']=='Synthetic fixed demo'
