"""Offline reset rules; measured pose fingerprints require the phase-2 simulator."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PINS={'scene_contracts/fixture-pilots-v1.json': '39024aa9025da521d437d8db31370361f7fbc84f538ce0c3e4ff5591cec46a48', 'success_conditions/fixture-pilots-v1.json': '0a550fc59ed1727ecbfb62a96a72f47a7aca06733aad7ad328dd30e9c5678715', 'prompts/turn_on_microwave.json': '19534d181a7037a44930ec06c754075f18fdcbda5656ce0ee124621bf2bc7349', 'prompts/turn_off_stove.json': '87844d12a814c97df2256074c86b7ce3ee2e475a6d11fe149e2b20d1eb572ed2', 'prompts/open_drawer.json': '3b6960755b7616985981fd924f843151b9fb183c1e7385150d233af9c2f610df', 'prompts/weigh_ingredients.json': '2d7ebd3f3359f2b1aec255edd73b3766eacb144f0bc8cfbad514235388eb97ff', 'prompts/coffee_setup_mug.json': 'c839c6b5df2afedab6ab894b0cd825be2e239188e08a0116a4b370b49190b8e9', 'prompts/close_blender_lid.json': 'c90671f93283cfa64653178684f8af3f3f69775134ed9736aed24363e32dd61a', 'prompts/open_stand_mixer_head.json': '110a05c7526e71ddfbcd426f3bde26cc4b530f61373e3035e0fcb7cae940697b', 'prompts/prepare_coffee.json': '624431b69bb24007c6c82ac37b96b49269d0ca8bc978b66339cd9f36a9893edc', 'prompts/heat_kebab_sandwich.json': 'e66e3b04225160c0ae4df87d15a7a2fb558fce21d87475d561f6390277186fd8', 'prompts/arrange_tea.json': 'ca6723a946e733091efe161b475a2d73fd7cb13f4e75c782144ef68f7e53f3ca', 'prompts/categorize_condiments.json': 'c99e4577fbb5a7cf585a5e50daa6c49c5fb0af33d28238976ed3fe577598a1fb', 'prompts/kettle_boiling.json': 'a714e663d48dd39968d7ae872bd0f37b8f8c937c1467a040af5f1375f1262cb7', 'scene_contracts/fixture-screened-v1.json': '1f4aa0f03c7a18aa9d2a839c2244ce835078302551108a733d707094f5e3c79c', 'success_conditions/fixture-screened-v1.json': 'b6ed30d53e7ef81b8834ae7511a6e1a406f79e1717399c1952129c4a79a99136'}


def checked(name):
    path=ROOT/name
    if hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('fixture resource digest drift: '+name)
    return path


def resource_paths():return [checked(name) for name in PINS]
def prompt_ref(task):return checked('prompts/'+task+'.json')
def resource_name(folder,task=None):
    from curobo_v2.fixture_interaction import DEFAULT_PILOT_TASKS
    return folder+('/fixture-pilots-v1.json' if task is None or task in DEFAULT_PILOT_TASKS else '/fixture-screened-v1.json')

def contract_ref(task=None):
    name=resource_name('scene_contracts',task);return dict(uri=str(checked(name)),sha256=PINS[name])

def success_ref(task=None):
    name=resource_name('success_conditions',task);return dict(uri=str(checked(name)),sha256=PINS[name])


def camera_target(env,config):
    import numpy as np
    spec=json.loads(checked(resource_name('scene_contracts',config['task_id'])).read_bytes())['tasks'][config['task_id']]
    return np.asarray(getattr(env,spec['reference']).pos,float).copy()


def configure_camera(env,config):
    import numpy as np
    from scipy.spatial.transform import Rotation
    target=camera_target(env,config);pos=target+np.array([1.7,-1.2,1.25])
    z=(pos-target)/np.linalg.norm(pos-target);x=np.cross([0.,0.,1.],z);x/=np.linalg.norm(x)
    y=np.cross(z,x);cam=env.sim.model.camera_name2id('robot0_agentview_left')
    env.sim.model.cam_pos[cam]=pos
    env.sim.model.cam_quat[cam]=Rotation.from_matrix(np.column_stack([x,y,z])).as_quat()[[3,0,1,2]]
    env.sim.forward()


def validate_reset(env,config,settings):
    from curobo_v2.fixture_interaction import find_fixtures
    spec=json.loads(checked(resource_name('scene_contracts',config['task_id'])).read_bytes())['tasks'][config['task_id']]
    rows=find_fixtures(env,settings)
    row=next((r for r in rows if r['name']==getattr(env,spec['reference']).name),None)
    if row is None or row['fixture_class'] not in spec['classes']:raise ValueError('fixture reset reference/class mismatch')
    if spec['reset_rule']=='native_reset':
        if config['seed'] not in spec['seeds']:raise ValueError('unregistered fixture screening seed')
        return dict(matched=True,comparison='native_reset_reference_class',measured_scene_fingerprints=False,
            task_id=config['task_id'],seed=config['seed'],contract=contract_ref(config['task_id']),fixtures=rows)
    required=spec['required_part'].format(selected_knob=getattr(env,'knob',''))
    if not any(required in p['name'] for p in row['interaction_parts']):raise ValueError('fixture reset interaction part missing')
    state=row['state'];rule=spec['reset_rule']
    if rule=='turned_on_false':matched=state.get('turned_on') is False
    elif rule=='selected_burner_on':
        matched=bool(getattr(env,spec['reference']).is_burner_on(env, burner_loc=env.knob))
    else:
        doors=state.get('door_open_fraction',{})
        matched=bool(doors) and all(float(v)<=.05 for v in doors.values())
    if not matched:raise ValueError('fixture reset state mismatch')
    return dict(matched=True,comparison='fixture_reset_rules',measured_scene_fingerprints=False,
        task_id=config['task_id'],seed=config['seed'],contract=contract_ref(config['task_id']),fixtures=rows)
