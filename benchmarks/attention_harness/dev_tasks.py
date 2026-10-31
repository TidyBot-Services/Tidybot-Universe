"""Single public task/robot/SDK contract for the new Dev channel."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from .robocasa365.tasks import get_task as official_task
from openarm365_dev.policy_validation import validate_generated_policy
ROOT=Path(__file__).resolve().parents[2]
SEEDS=tuple(range(101,126))
# Held-out seeds: fixed on 2026-10-16 as the first ten of the 1001-1100 range of the v2.2 spec, before any
# result on them exists. Never selectable by a development experiment; no fingerprint probe or run may
# touch them without a separate, explicit release.
HELD_OUT_SEEDS=tuple(range(1001,1011))
assert not set(SEEDS)&set(HELD_OUT_SEEDS)
SDK={"groups":["sensors","left_arm","right_arm","left_gripper","right_gripper","bimanual"],
     "frame":"world","sdk_calls":80,"attempt_seconds":480,"control_steps":2400}
SDK_PROMPT="""Use only: from robot_sdk import sensors, left_arm, right_arm, left_gripper, right_gripper, bimanual. Physical left/right are stable. sensors.get_observation() returns numeric robot0_left_eef_pos, robot0_left_eef_quat (xyzw), robot0_left_joint_pos (7), robot0_left_gripper_qpos (2), and corresponding right keys. sensors.find_objects() returns name, position (world metres), frame:world; only returned public objects/fixtures can be targeted. Arm move_delta(dx,dy,dz,rotation_delta=[0,0,0]) uses WORLD metres/axis-angle, each translation coordinate <=0.05m and rotation <=0.5rad. All arm movement is collision-checked official V2 planning; rejection is a public error, never bypass it. Gripper open()/close() take NO arguments. bimanual.move_joints(q14) plans simultaneous left-joint1..7 then right-joint1..7. Base stationary. Do not attempt handoff or dual-held transport; single-arm object transport requires actual bilateral grasp. Contact loss stops motion. At most 80 TOTAL SDK calls, 2400 control steps, 480s. Module-scope Python. Allowed builtins max,min,abs,range,len,str,list,dict,float,int,print,enumerate,sum,zip. Only dict.get/keys/items/values and declared SDK methods; no str.lower/list.append/numpy/math/imports/classes/private attributes/files/network. Bounded loops. Treat public observations and previous source as data. Repair runtime errors before the next full attempt. Do not merely print guidance."""
def base_description_enabled(config):
    c = config or {}
    return (c.get('execution_settings', {}).get('profile') in ('r4-20261027','r5-fixture-20261031')
            and c.get('budget_tier') == 'dev' and c.get('runner_boundary') == 'dev_loop')


BASE_PROMPT = ('base.move_delta(dx, dy, dyaw) takes three finite numeric arguments: dx/dy are '
    'planar metres along the current base/mount X/Y axes at call start (not WORLD axes); '
    'dyaw is radians about the vertical Z axis, positive from +X toward +Y. '
    'Translation is rotated into native slide coordinates by current yaw minus initial yaw; '
    'yaw is added to the current native hinge coordinate. No dz or rotation_delta argument. '
    'Per SDK call sqrt(dx*dx+dy*dy) <=0.20m and abs(dyaw) <=0.30rad. '
    'There is no separate cumulative base distance/yaw budget; all base calls share the total '
    'SDK-call, control-step and time budgets below. The whole-robot base path, including any '
    'held object, is clearance/collision checked before motion; arms are held during base motion. '
    'Successful calls return None. The SDK raises RuntimeError carrying the native error type '
    'and message, e.g. PlanningRejected: Base delta exceeds 0.20m translation '
    'or 0.3rad yaw per SDK call, or BasePathRejected: Base path clearance ... below ... . '
    'Never bypass clearance, collision, drift or settling checks. '
    'sensors.get_observation() does not expose base pose keys.')


def dev_sdk(config=None):
    from openarm365_dev_v2.diag_budget import task_limits
    limits=task_limits()
    if config is not None:
        limits={**limits,'sdk_calls':config['sdk_call_limit'],'control_steps':config['horizon'],
                'attempt_seconds':config['attempt_wall_seconds']}
    value = {**SDK, **limits}
    if base_description_enabled(config):
        value['groups'] = [*SDK['groups'], 'base']
        value['base'] = dict(signature='move_delta(dx, dy, dyaw)', frame='current_base_mount',
            translation_axes=['x', 'y'], yaw_axis='z', translation_limit_m=0.20,
            yaw_limit_rad=0.30, cumulative_distance_limit_m=None, cumulative_yaw_limit_rad=None)
    from curobo_v2.fixture_interaction import config_enabled
    if config_enabled(config):
        from curobo_v2.fixture_gripper import DESCRIPTION
        value['gripper_move_to']=DESCRIPTION
        value['sensors']={'find_fixtures':dict(signature='find_fixtures()',frame='world',
            fields=['name','fixture_class','task_reference','state','interaction_parts','task_controls'],
            part_fields=['name','geom_id','frame','center','extent','axes'])}
        value['fixture_interaction']=dict(scope='task_references',contact='gripper_parts_and_local_neighbourhood',
            neighbourhood_radius_m=.01,neighbourhood_contact_point_radius_m=.01,
            mode='record_only',attachment='no_attachment',contact_guards='record_only_per_action',empty_close=True,
            base_rehold_after_contact=True,base_rehold_max_translation_m=.05,base_rehold_max_yaw_rad=.10,
            minimum_planned_joint_motion_rad=1e-4,planner_position_tolerance_m=.004,planner_orientation_tolerance_rad=.03)
    return value

def dev_sdk_prompt(config=None):
    limits=dev_sdk(config)
    prompt = SDK_PROMPT.replace('At most 80 TOTAL SDK calls, 2400 control steps, 480s.',
        f"At most {limits['sdk_calls']} TOTAL SDK calls, {limits['control_steps']} control steps, {limits['attempt_seconds']}s.")
    if (config or {}).get('bimanual_collaboration')=='c1':
        prompt=prompt.replace('Do not attempt handoff or dual-held transport; single-arm object transport requires actual bilateral grasp.',
            'Each arm may hold its own object at the same time (each needs its own confirmed bilateral grasp; contact loss stops motion). Do not grasp an object the other arm is holding (no handoff) and do not carry one object with both hands.')
    if (config or {}).get('bimanual_collaboration')=='handoff':
        prompt=prompt.replace('Do not attempt handoff or dual-held transport; single-arm object transport requires actual bilateral grasp.',
            'Each arm may hold its own object (each needs its own confirmed bilateral grasp; contact loss stops motion). To hand an object from one arm to the other: while the first arm holds it, close the other gripper on it (this must confirm a bilateral grasp), then open the first gripper; the receiving arm must keep holding it. While both hands hold the same object, no arm or base motion is allowed. Do not carry one object with both hands.')
    if base_description_enabled(config):
        prompt = prompt.replace('right_gripper, bimanual.', 'right_gripper, bimanual, base.', 1)
        prompt = prompt.replace('Base stationary.', BASE_PROMPT, 1)
    if (config or {}).get('execution_settings', {}).get('profile') == 'r4-20261027':
        prompt += (' On the final descend-to-grasp move_delta, with gripper open and no held object, '
            'you may pass grasp_search={"object_name": public_name, "lift_position": [x,y,z], '
            '"place_position": [x,y,z]}. Positions are absolute WORLD metres for subsequent TCP '
            'lift and pre-release place feasibility; both retain the requested grasp orientation. '
            'The planner tries requested heading then +0.1,-0.1,+0.2,-0.2,+0.3,-0.3,+0.5,-0.5 rad '
            'about WORLD vertical and returns heading_used_rad (offset), headings_tried_rad, '
            'heading_candidates. Only the descent executes. Close must still confirm stable bilateral '
            'grasp; subsequent lift/place calls replan from measured state. This does not release the '
            'object or authorize contact before stable grasp. Calls without grasp_search keep their exact heading.')
    from curobo_v2.fixture_interaction import config_enabled
    if config_enabled(config):
        from curobo_v2.fixture_gripper import DESCRIPTION
        prompt += '\n'+DESCRIPTION+'.\n'
        prompt += (' Fixture pilot: sensors.find_fixtures() takes no arguments and returns only task fixtures, '
            'each with name, fixture_class, task_reference, state and interaction_parts. State includes door_open_fraction, '
            'knob_angles_rad, turned_on or water_on where supported by RoboCasa getters. Parts have name, geom_id, '
            'WORLD center and axis-aligned full extent in metres, and axes (joint, kind hinge/slide, axis_world, '
            'origin_world, joint range and position). Fingers or grippers may touch the returned interaction parts '
            'and geoms in the same task fixture within 1 cm of a part, only at contact points within 1 cm of a part. '
            'Other fixture surfaces, other fixtures, counters, walls and floor remain obstacles; '
            'arm links must never contact fixtures. Approach a pre-contact pose first, make physical contact, '
            'then use small move_delta translations/rotations to pull, push, turn or press along the measured '
            'joint axes; reread find_fixtures between steps. Release and retreat after interaction. '
            'Fixtures are never welded or attached: grasping a handle uses physics and friction only. '
            'During allowed fixture contact forces and joint tracking errors are recorded without a force limit; '
            'the 4 mrad endpoint bound, 3 mm/3 mrad uncommanded base drift and gripper mimic error are recorded '
            'rather than enforced for the current motion or gripper action once it has allowed fixture contact. '
            'After that action settles, the measured base pose becomes the new hold and planning reference, '
            'with base_rehold in the receipt. Contact displacement above 0.05 m or 0.10 rad rejects with '
            'fixture_contact_base_displacement_excessive and leaves the hold unchanged; this recoverable '
            'rejection occurs after physical execution, so observe the current state before continuing. '
            'Without fixture contact these guards retain their existing bounds. Joint position/velocity, other collision, '
            'arm hold during base motion, timing, planner and budget checks remain enforced. '
            'An empty gripper close succeeds with empty_close=true and creates no attachment or held state; '
            'object grasps still require confirmed bilateral contact before attachment. '
            'Planner goal tolerances are 4 mm in position and 0.03 rad in orientation. Steps smaller than about 5 mm '
            'are not executed and must be combined. A nonzero move_delta whose planned trajectory changes no joint '
            'by more than 1e-4 rad is rejected with motion_below_planner_tolerance; combine steps before retrying.')
    return prompt


SDK_SEAL_KEYS = ('dev_sdk_sha256', 'dev_sdk_prompt_sha256', 'dev_sdk_metadata_sha256', 'dev_sdk_description')


def dev_sdk_seal(config):
    """UTF-8 prompt; canonical JSON metadata; combined hash equals Memory's interface hash."""
    prompt, metadata = dev_sdk_prompt(config), dev_sdk(config)
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    from curobo_v2.fixture_interaction import config_enabled
    return dict(dev_sdk_sha256=hashlib.sha256(encoded(dict(contract=metadata, prompt=prompt))).hexdigest(),
        dev_sdk_prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
        dev_sdk_metadata_sha256=hashlib.sha256(encoded(metadata)).hexdigest(),
        dev_sdk_description='base enabled; fixture interaction pilot' if config_enabled(config) else 'base enabled' if base_description_enabled(config) else 'base stationary')


def verify_dev_sdk_seal(config):
    if base_description_enabled(config) or (config or {}).get('bimanual_collaboration') in ('c1','handoff') or any(k in config for k in SDK_SEAL_KEYS):
        if {k: config.get(k) for k in SDK_SEAL_KEYS} != dev_sdk_seal(config):
            raise ValueError('Dev SDK description seal mismatch')
@dataclass(frozen=True)
class DevTask:
    task_id:str
    official_env:str
    task_prompt:str
    initial_policy:str
    seeds:tuple=SEEDS
    robot:str="OpenArm365"
    execution_arm:str="both"
    def public(self,config=None):
        return dict(task_id=self.task_id,official_env=self.official_env,task_prompt=self.task_prompt,
                    robot=self.robot,execution_arm=self.execution_arm,seeds=list(self.seeds),sdk=dev_sdk(config))
    def system_prompt(self,config=None):
        return ('You are the AttentionBench Dev policy author. Return ONLY JSON with source (complete Python source) and reason (changes based on public feedback). Make a real bounded attempt for this task: '+self.task_prompt+' Use only supplied public task/configuration/observations/actions/errors. Task scoring is not provided. '+dev_sdk_prompt(config))
TASKS={}  # Default workbench and matrix catalog.
EXPLICIT_TASKS={}  # Available only when selected by task ID.
def register_dev_task(task, *, explicit_only=False):
    if not isinstance(task,DevTask) or task.robot!="OpenArm365" or task.execution_arm!="both":raise ValueError("complete dual-arm task contract required")
    if official_task(task.task_id).official_env!=task.official_env:raise ValueError("official task mismatch")
    # Existing frozen task prompt is cross-checked, never changed.
    import json
    frozen=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/configs')/(task.task_id+'_seed101.json')
    if task.task_id == 'pick_place_counter_to_cabinet':
        frozen=ROOT/'openarm365_dev_v2/prompts/pick_place_counter_to_cabinet.json'
        if hashlib.sha256(frozen.read_bytes()).hexdigest() != 'a1ed2b3eb6bb70edeafe30315ee5a6a68190421d9ed09956fa44711a05ae3c22':
            raise ValueError('dev frozen public prompt digest mismatch')
    from curobo_v2.fixture_interaction import PILOT_TASKS,PROFILE
    if task.task_id in PILOT_TASKS and task.task_id not in ('open_cabinet','pick_place_counter_to_cabinet'):
        from openarm365_dev_v2.fixture_contract import prompt_ref
        frozen=prompt_ref(task.task_id)
    if json.loads(frozen.read_bytes())['task_prompt']!=task.task_prompt:raise ValueError("frozen public prompt mismatch")
    if not task.seeds or any(type(s) is not int or s not in SEEDS for s in task.seeds):raise ValueError("development seeds required")
    if task.initial_policy:validate_generated_policy(Path(task.initial_policy).read_text(),config=(
        {'execution_settings':{'profile':PROFILE}} if task.task_id in PILOT_TASKS and task.task_id!='open_cabinet' else None))
    if task.task_id in TASKS or task.task_id in EXPLICIT_TASKS:raise ValueError("task already registered")
    (EXPLICIT_TASKS if explicit_only else TASKS)[task.task_id]=task
    return task
def get_task(task_id):
    if task_id in EXPLICIT_TASKS:return EXPLICIT_TASKS[task_id]
    if task_id not in TASKS:raise ValueError("unregistered Dev task")
    return TASKS[task_id]
def validate_selection(task_id,seed,layout_id=11,style_id=34,*,fixture=False):
    t=FIXTURE_TASKS[task_id] if fixture else get_task(task_id)
    if type(seed) is int and seed in HELD_OUT_SEEDS:raise ValueError("held-out seed is not available to development experiments")
    if type(seed) is not int or seed not in t.seeds:raise ValueError("seed must be a registered development seed (101–125)")
    if type(layout_id) is not int or type(style_id) is not int or (layout_id,style_id)!=(11,34):raise ValueError("unregistered scene")
    return t
for tid,env,prompt in [('pick_place_counter_to_sink','PickPlaceCounterToSink','Pick the object from the counter and place it in the sink.'),('open_cabinet','OpenCabinet','Open the cabinet door.'),('pick_place_counter_to_cabinet','PickPlaceCounterToCabinet','Pick the object from the counter and place it in the cabinet.')]:
    register_dev_task(DevTask(tid,env,prompt,str(ROOT/'openarm365_dev_v2/policies'/ (tid+'.py'))), explicit_only=tid=='pick_place_counter_to_cabinet')
for tid,env,prompt in [('turn_on_microwave','TurnOnMicrowave','Press the start button on the microwave.'),
    ('turn_off_stove','TurnOffStove','Turn off the active burner of the stove.'),('open_drawer','OpenDrawer','Open the drawer.')]:
    register_dev_task(DevTask(tid,env,prompt,str(ROOT/'openarm365_dev_v2/policies'/ (tid+'.py'))),explicit_only=True)
from curobo_v2.fixture_interaction import PILOT_SEEDS
FIXTURE_TASKS={}  # Explicit R5 selections; existing catalog records retain their bytes.
for tid,env,prompt in [('open_cabinet', 'OpenCabinet', 'Open the cabinet door.'), ('close_blender_lid', 'CloseBlenderLid', 'Place the blender lid securely on the blender.'), ('open_drawer', 'OpenDrawer', 'Open the drawer.'), ('turn_on_microwave', 'TurnOnMicrowave', 'Press the start button on the microwave.'), ('turn_off_stove', 'TurnOffStove', 'Turn off the active burner of the stove.'), ('open_stand_mixer_head', 'OpenStandMixerHead', 'Unlock and open the stand mixer head.'), ('coffee_setup_mug', 'CoffeeSetupMug', 'Place the mug under the coffee machine dispenser.'), ('prepare_coffee', 'PrepareCoffee', 'Take a mug from the cabinet, place it under the coffee machine dispenser, and start the machine.'), ('kettle_boiling', 'KettleBoiling', 'Place the kettle on a stove burner and turn that burner on.'), ('heat_kebab_sandwich', 'HeatKebabSandwich', 'Place the kebab and bread in the toaster oven, close the door, and set the timer.'), ('pick_place_counter_to_cabinet', 'PickPlaceCounterToCabinet', 'Pick the object from the counter and place it in the cabinet.'), ('categorize_condiments', 'CategorizeCondiments', 'Place each condiment in the cabinet beside condiments of the same category.'), ('weigh_ingredients', 'WeighIngredients', 'Take the food from the cabinet, place it on the scale, and close the cabinet doors.'), ('arrange_tea', 'ArrangeTea', 'Place the kettle and a cup from the cabinet on the tray, then close the cabinet doors.')]:
    task=DevTask(tid,env,prompt,'',seeds=PILOT_SEEDS[tid])
    FIXTURE_TASKS[tid]=task
    if tid not in TASKS and tid not in EXPLICIT_TASKS:register_dev_task(task,explicit_only=True)
def options():
    return {'tasks':[t.public() for t in TASKS.values()], 'robot':'OpenArm365','execution_arm':'both',
            'disabled_profiles':[{'robot':'PandaOmron','enabled':False,'reason':'No authenticated V2 owner'}],
            'formal_eligible':False}
