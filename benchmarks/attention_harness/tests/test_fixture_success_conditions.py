"""Offline native-source contracts and actual public publication boundaries."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from benchmarks.attention_harness import dev_agent_success as container,dev_fixture_success as fixture
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_diagnostics import FORBIDDEN
from benchmarks.attention_harness.tests.test_dev_agent_d4 import ws,seed_record
from openarm365_dev_v2.dev_config import ROBocasa
from curobo_v2.fixture_interaction import PROFILE,PILOT_TASKS

NATIVE=ROBocasa.parent

def node(file,symbol):
    library,relative=file.split('/',1)
    path=NATIVE/library/library/relative
    result=ast.parse(path.read_text())
    for name in symbol.split('.'):
        result=next(n for n in result.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name==name)
    return result

def native(file,symbol):
    n=copy.deepcopy(node(file,symbol));n.decorator_list=[]
    ns={'np':np}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[n],type_ignores=[])),file,'exec'),ns)
    return ns[n.name]

def default(file,symbol,arg):
    n=node(file,symbol)
    return ast.literal_eval(dict(zip([a.arg for a in n.args.args][-len(n.args.defaults):],n.args.defaults))[arg])

def constants(file,symbol):
    return [n.value for n in ast.walk(node(file,symbol)) if isinstance(n,ast.Constant)]

U='robocasa/utils/object_utils.py'
F='robocasa/models/fixtures/'
A='robocasa/environments/kitchen/atomic/'
C='robocasa/environments/kitchen/composite/'

@pytest.mark.parametrize('task',PILOT_TASKS)
def test_each_task_rule_matches_installed_native_sources(task):
    condition=fixture.load(task,101);preds=condition['predicates']
    assert condition['sha256']==container.digest({k:v for k,v in condition.items() if k!='sha256'})
    assert set(json.loads(fixture.RESOURCE.read_bytes())['tasks'])==set(PILOT_TASKS)
    for ref in condition['derivation']:
        if 'source_sha256' in ref:
            library,relative=ref['file'].split('/',1)
            raw=(NATIVE/library/library/relative).read_text()
            assert hashlib.sha256(ast.get_source_segment(raw,node(ref['file'],ref['symbol'])).encode()).hexdigest()==ref['source_sha256'],ref
        else:
            cls=node(ref['file'],'Blender')
            assignment=next(n for n in cls.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==ref['symbol'].split('.')[-1] for t in n.targets))
            assert ast.literal_eval(assignment.value)==ref['value']
    for p in preds:
        if p['kind']=='gripper_distance':
            if p['target']['kind']=='object':expected=default(U,'gripper_obj_far','th')
            elif task=='close_blender_lid':
                expected=.15;assert expected in constants(A+'kitchen_blender.py','CloseBlenderLid._check_success')
            else:expected=default(F+('microwave.py' if task=='turn_on_microwave' else 'coffee_machine.py'),('Microwave' if task=='turn_on_microwave' else 'CoffeeMachine')+'.gripper_button_far','th')
            assert p['threshold_m']==expected and p['op']=='>' and p['grippers']==['left']
        elif p['kind']=='object_in_region':
            assert p['projection_padding']==default(U,'obj_inside_of','th')
            assert p['partial_check']==default(U,'obj_inside_of','partial_check') is False
            assert p['points']=='all_8_bbox_corners' and not p['observable']
        elif p['kind']=='pour_alignment':
            assert p['xy_distance_m']=={'op':'<','value':default(F+'coffee_machine.py','CoffeeMachine.check_receptacle_placement_for_pouring','xy_thresh')}
            assert p['abs_z_distance_m']=={'op':'<','value':.10}
            assert .10 in constants(F+'coffee_machine.py','CoffeeMachine.check_receptacle_placement_for_pouring')
        elif p['kind']=='receptacle_xy_distance':
            assert p['threshold']['factor']==.7 and .7 in constants(U,'check_obj_in_receptacle')
            assert p['threshold']['value_m'] is None and p['op']=='<' and not p['observable']
        elif p['kind']=='upright':
            assert p['abs_roll_pitch_deg']=={'op':'<','value':default(U,'check_obj_upright','th')}
        elif p['kind']=='xy_distance':
            assert p['threshold_m']==.15 and p['op']=='<='
            assert .15 in constants(C+'arranging_condiments/categorize_condiments.py','CategorizeCondiments._check_success')
        elif p['kind']=='burner_alignment':
            assert p['xy_distance_m']=={'op':'<','value':.15}
            assert .15 in constants(C+'brewing/kettle_boiling.py','KettleBoiling._check_success')
            assert p['burner_on_interval_rad'][0]==default(F+'stove.py','Stove.is_burner_on','th')==.35
        elif p['kind']=='knob_off':
            assert p['on_interval_rad']==[default(F+'stove.py','Stove.is_burner_on','th'),'2*pi - 0.35']
        elif p['kind']=='not_all_joints' or p.get('field')=='joint_open_fraction':
            closed=task in ('weigh_ingredients','arrange_tea')
            assert p['value']==default(F+'fixture.py','Fixture.is_closed' if closed else 'Fixture.is_open','th')
            assert p['op']==('<=' if closed else '>=')
        elif p.get('field')=='door_open_fraction':
            assert p['value']==.95 and p['op']=='>='
            assert .95 in constants(A+'kitchen_drawer.py','ManipulateDrawer._check_success')
        elif p.get('field')=='head':
            assert p['value']==.99 and p['op']=='>'
            assert .99 in constants(A+'kitchen_stand_mixer.py','OpenStandMixerHead._check_success')
        elif p.get('field')=='time':
            assert p['value']==.1 and p['op']=='>='
            assert .1 in constants(C+'preparing_sandwiches/heat_kebab_sandwich.py','HeatKebabSandwich._check_success')
    view=container.eval_view(condition)
    assert not FORBIDDEN.search(json.dumps(view)) and 'derivation' not in view
    assert all(p.get('reason') for p in preds if not p['observable'])
    assert not container.check(condition,{'robot':{},'perception':{'values':{}}})['passed']


def observation(distance):
    return dict(robot={'robot0_left_eef_pos':[distance,0.,0.],'robot0_right_eef_pos':[0.,0.,0.]},perception={'values':{}},
        fixtures=[dict(name='mw',task_reference='microwave',state={'turned_on':True},interaction_parts=[dict(name='mw_start_button',position=[0.,0.,0.],center=[10.,0.,0.])])])

@pytest.mark.parametrize('distance,passed',[(.04,False),(.15,False),(.16,True)])
def test_microwave_native_and_public_clearance(distance,passed):
    helper=native(F+'microwave.py','Microwave.gripper_button_far')
    check=native(A+'kitchen_microwave.py','MicrowavePressButton._check_success')
    env=NS(behavior='turn_on',robots=[NS(eef_site_id={'right':0,'left':1})],sim=NS(model=NS(geom_name2id=lambda n:0),
        data=NS(geom_xpos=np.zeros((1,3)),site_xpos=np.array([[distance,0,0],[0,0,0]]))))
    microwave=NS(naming_prefix='mw_',get_state=lambda:{'turned_on':True})
    microwave.gripper_button_far=lambda e,button:helper(microwave,e,button)
    env.microwave=microwave
    assert bool(check(env)) is passed
    result=container.check(fixture.load('turn_on_microwave',101),observation(distance))
    assert result['passed'] is passed and result['complete']
    assert result['predicates'][1]['value']['left']==[distance]

@pytest.mark.parametrize('bad',['missing_gripper','nan','missing_part','missing_state','wrong_type'])
def test_microwave_missing_evidence_fails_closed(bad):
    obs=observation(.16)
    if bad=='missing_gripper':obs['robot'].pop('robot0_left_eef_pos')
    if bad=='nan':obs['robot']['robot0_left_eef_pos'][0]=float('nan')
    if bad=='missing_part':obs['fixtures'][0]['interaction_parts']=[]
    if bad=='missing_state':obs['fixtures'][0]['state']={}
    if bad=='wrong_type':obs['fixtures'][0]['state']['turned_on']=1
    result=fixture.check(fixture.load('turn_on_microwave',101),obs)
    assert not result['passed'] and not result['complete']


def test_mani_skill_microwave_helper_agrees():
    path=Path('/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/lib/python3.11/site-packages/mani_skill/utils/scene_builder/robocasa/fixtures/microwave.py')
    tree=ast.parse(path.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Microwave')
    helper=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='gripper_button_far')
    assert ast.literal_eval(helper.args.defaults[-1])==.15
    assert any(isinstance(n,ast.Compare) and isinstance(n.ops[0],ast.Gt) for n in ast.walk(helper))

@pytest.mark.parametrize('task',PILOT_TASKS)
def test_dev_workspace_publishes_fixture_rule(tmp_path,task):
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
    from benchmarks.attention_harness.dev_tasks import dev_sdk_seal
    config=dict(CONFIG,task_id=task,execution_settings={'profile':PROFILE})
    config.update(dev_sdk_seal(config))
    condition=fixture.load(task,101)
    workspace=Workspace(tmp_path/'agent').prepare(dict(task_id=task,task_prompt='Operate the fixture.'),config,success_condition=condition)
    assert json.loads((workspace.ws/'docs/success_condition.json').read_bytes())==fixture.eval_view(condition)
    assert fixture.DEV_RULE in (workspace.ws/'docs/tools.md').read_text()
    assert fixture.DEV_RULE in (workspace.ws/'docs/sdk.md').read_text()
    assert 'both grippers released' not in (workspace.ws/'docs/tools.md').read_text()


def test_eval_workspace_gets_frozen_fixture_rule_and_final_state(ws):
    from benchmarks.attention_harness.dev_agent_eval import build_workspace
    execution=seed_record(ws);obs=observation(.16)
    ws.publish('executions/e000001/final_observation.json',obs)
    condition=fixture.load('turn_on_microwave',101)
    path=build_workspace(ws,execution,condition)
    assert json.loads((path/container.FILE).read_bytes())==fixture.eval_view(condition)
    assert json.loads((path/'final_observation.json').read_bytes())==obs


def test_non_r5_conditions_are_byte_identical():
    import subprocess
    for path in (container.ROOT).glob('*.json'):
        if path.name=='fixture-public-v1.json':continue
        relative=str(path.relative_to(Path.cwd()))
        assert path.read_bytes()==subprocess.check_output(['git','show','HEAD:'+relative])
    assert container.load('pick_place_counter_to_cabinet',102) is None
    assert container.for_configs({'old':dict(task_id='open_cabinet',seed=102)})=={}

@pytest.mark.parametrize('mode',['one_call','agent'])
@pytest.mark.parametrize('task',['open_cabinet','pick_place_counter_to_sink'])
def test_non_r5_manifest_bytes_match_starting_head(tmp_path,monkeypatch,offline_dev_identity,mode,task):
    import subprocess,types
    from benchmarks.attention_harness import dev_experiment as current
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    path='benchmarks/attention_harness/dev_experiment.py'
    old=types.ModuleType('benchmarks.attention_harness._fixture_success_old_experiment')
    old.__package__='benchmarks.attention_harness'
    exec(compile(subprocess.check_output(['git','show','HEAD:'+path]),path,'exec'),old.__dict__)
    selection=dict(request_key='success-golden',tasks=[task],seeds=[102],policies=['autonomous'],mode='interactive',dev_mode=mode)
    if mode=='one_call':selection.update(target_depth=1,target_valid_executions=1,max_versions=1)
    store=DevV2Store(tmp_path/'db');exp=old.create(store,tmp_path/'runs',selection)
    before=Path(exp['manifest']['uri']).read_bytes()
    (tmp_path/'db').unlink()
    result=current.create(DevV2Store(tmp_path/'db'),tmp_path/'runs',selection)
    assert Path(result['manifest']['uri']).read_bytes()==before

from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity


def test_one_call_dev_packet_and_eval_prompt_use_frozen_rule(tmp_path):
    from benchmarks.attention_harness.dev_v2_loop import model_messages
    from benchmarks.attention_harness import eval_agent
    from benchmarks.attention_harness.tests.test_eval_advisor_split import evidence,FakeClient
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
    c=dict(CONFIG,task_id='turn_on_microwave',official_env='TurnOnMicrowave',task_prompt='Turn on the microwave.',
           seed=101,robot='OpenArm365',execution_arm='both',controller='BASIC',layout_id=11,style_id=34,scene_id='test',perception_mode='sim_gt',execution_settings={'profile':PROFILE})
    condition=fixture.load(c['task_id'],101)
    messages=model_messages(c,'',{}, {},{},success_condition=condition)
    packet=json.loads(messages[1]['content'])
    assert packet['success_condition']==fixture.eval_view(condition)
    assert fixture.DEV_RULE in messages[0]['content']
    e=dict(evidence(),success_condition=fixture.eval_view(condition))
    client=FakeClient('{"passed":false,"reason":"frame #1 and final pose: gripper too close","reference_suggestion":"Retreat."}')
    eval_agent.ask(e,eval_agent.default_config(),client)
    assert fixture.EVAL_RULE in client.calls[0]['messages'][0]['content']
    text=client.calls[0]['messages'][1]['content'][0]['text']
    assert json.loads(text)['success_condition']==fixture.eval_view(condition)


def test_one_call_evidence_retains_final_fixture_states(tmp_path):
    from benchmarks.attention_harness.v2_evaluator import build_eval_evidence
    obs=observation(.16)
    (tmp_path/'final_public_observation.json').write_text(json.dumps(obs))
    config=dict(task_id='turn_on_microwave',task_prompt='Turn it on.',execution_settings={'profile':PROFILE})
    e=build_eval_evidence(tmp_path,config)
    assert e['final_observation']['fixtures']==obs['fixtures']
    assert e['final_observation']['robot']==obs['robot']
    old=build_eval_evidence(tmp_path,dict(config,execution_settings={'profile':'r4-20261027'}))
    assert 'fixtures' not in old['final_observation']


def test_resource_tampering_refused_and_loaded_condition_stays_frozen(tmp_path,monkeypatch):
    condition=fixture.load('turn_on_microwave',101);before=copy.deepcopy(condition)
    resource=tmp_path/'rule.json';resource.write_bytes(fixture.RESOURCE.read_bytes()+b' ')
    monkeypatch.setattr(fixture,'RESOURCE',resource)
    with pytest.raises(ValueError,match='fixture_success_resource_digest'):fixture.load('turn_on_microwave',101)
    assert condition==before and fixture.check(condition,observation(.16))['passed']

GOLDEN=json.loads((Path.cwd()/'openarm365_dev_v2/tests/fixtures/fixture_legacy_golden.json').read_bytes())['cases']
@pytest.mark.parametrize('case',GOLDEN)
@pytest.mark.parametrize('collaboration',['none','c1','handoff'])
def test_non_r5_sdk_prompt_and_hash_golden(case,collaboration):
    import subprocess,types
    from benchmarks.attention_harness import dev_tasks
    path='benchmarks/attention_harness/dev_tasks.py'
    old=types.ModuleType('benchmarks.attention_harness._fixture_success_old_tasks')
    old.__package__='benchmarks.attention_harness';old.__file__=str(Path.cwd()/path)
    exec(compile(subprocess.check_output(['git','show','HEAD:'+path]),path,'exec'),old.__dict__)
    config=dict(GOLDEN[case]['config'])
    if collaboration!='none':config['bimanual_collaboration']=collaboration
    assert dev_tasks.dev_sdk(config)==old.dev_sdk(config)
    assert dev_tasks.dev_sdk_prompt(config).encode()==old.dev_sdk_prompt(config).encode()
    assert dev_tasks.dev_sdk_seal(config)==old.dev_sdk_seal(config)


def test_non_r5_workspace_bytes_golden(tmp_path):
    import subprocess,types
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
    path='benchmarks/attention_harness/dev_agent_workspace.py'
    old=types.ModuleType('benchmarks.attention_harness._fixture_success_old_workspace')
    old.__package__='benchmarks.attention_harness';old.__file__=str(Path.cwd()/path)
    exec(compile(subprocess.check_output(['git','show','HEAD:'+path]),path,'exec'),old.__dict__)
    condition=container.load('pick_place_counter_to_sink',102)
    task=dict(task_id='pick_place_counter_to_sink',task_prompt='Put the object in the sink.')
    a=old.Workspace(tmp_path/'old').prepare(task,CONFIG,condition)
    b=Workspace(tmp_path/'new').prepare(task,CONFIG,condition)
    files=lambda w:{str(p.relative_to(w.ws)):p.read_bytes() for p in w.ws.rglob('*') if p.is_file()}
    assert files(a)==files(b)

@pytest.mark.parametrize('task,reference,field,value,passed',[
    ('open_cabinet','fxtr','joint_open_fraction',.899,False),
    ('open_cabinet','fxtr','joint_open_fraction',.90,True),
    ('open_drawer','drawer','door_open_fraction',.949,False),
    ('open_drawer','drawer','door_open_fraction',.95,True),
    ('open_stand_mixer_head','stand_mixer','head',.99,False),
    ('open_stand_mixer_head','stand_mixer','head',.991,True)])
def test_fixture_fraction_boundaries(task,reference,field,value,passed):
    state={field:({'door':value} if 'fraction' in field else value)}
    assert fixture.check(fixture.load(task,101),{'fixtures':[dict(task_reference=reference,state=state)]})['passed'] is passed

@pytest.mark.parametrize('angle,passed',[(0.,True),(.349,True),(.35,False),(2*np.pi-.35,False),(2*np.pi-.349,True),(2*np.pi,True)])
def test_selected_stove_knob_boundaries(angle,passed):
    obs={'fixtures':[dict(task_reference='stove',state={'knob_angles_rad':{'front_left':angle,'front_right':1.}},task_controls={'selected_knob':'front_left'})]}
    assert fixture.check(fixture.load('turn_off_stove',101),obs)['passed'] is passed


def test_cabinet_publishes_native_joint_fraction_separately():
    from curobo_v2.fixture_interaction import fixture_state
    cabinet=type('HingeCabinet',(),{})()
    cabinet.door_joint_names=['hinge']
    cabinet.get_door_state=lambda e:{'door':.89}
    cabinet.get_joint_state=lambda e,names:{'hinge':.9}
    state=fixture_state(None,cabinet)
    assert state==dict(door_open_fraction={'door':.89},joint_open_fraction={'hinge':.9})


def test_worker_final_observation_contains_only_public_fixture_getters():
    from scipy.spatial.transform import Rotation
    from openarm365_dev_v2.environment import PHYSICAL_TO_NATIVE
    path=Path.cwd()/'openarm365_dev_v2/worker.py';tree=ast.parse(path.read_text())
    observe=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='observe')
    rows=observation(.16)['fixtures']
    names=[f'openarm_{side}_joint{i}' for side in ('left','right') for i in range(1,8)]
    env=NS(robots=[NS(_ref_gripper_joint_pos_indexes={'left':[0,1],'right':[2,3]})],obj_body_id={},sim=NS(data=NS(qpos=np.zeros(4))))
    ns=dict(np=np,Rotation=Rotation,PHYSICAL_TO_NATIVE=PHYSICAL_TO_NATIVE,tcp_pose=lambda e,s:np.eye(4),
        executor=NS(sequence=3,planner=NS(state=lambda:[0.]*14,names=names)),env=env,episode='offline',
        cabinet_public_values=lambda e:{},fixture_enabled=lambda s:True,execution_settings=NS(),find_fixtures=lambda e,s:rows)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[observe],type_ignores=[])),str(path),'exec'),ns)
    obs=ns['observe']()
    assert obs['fixtures']==rows and not FORBIDDEN.search(json.dumps(obs))
