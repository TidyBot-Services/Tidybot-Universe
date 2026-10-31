"""Opt-in fixture part boundary shared by perception, planning and execution."""
import hashlib
import json
import re
from types import MappingProxyType
import numpy as np

PROFILE = 'r5-fixture-20261031'
WHITELIST_ID = 'robocasa-interaction-parts-v1'
PART_PATTERNS = MappingProxyType({
    'HingeCabinet': (r'(?:left|right)_door_handle_g\d+',),
    'SingleCabinet': (r'door_handle_g\d+',),
    'Drawer': (r'door_handle_g\d+',),
    'Microwave': (r'door_handle_main', r'(?:start|stop)_button'),
    'Stove': (r'door_handle_main', r'knob_(?:front_left|front_right|rear_left|rear_right|temp|time)_main'),
    'Sink': (r'(?:handle|spout)_main',),
    'ToasterOven': (r'door_handle_main', r'knob_(?:doneness|function|temp|time)_main'),
    'Toaster': (r'side[LR]_(?:button_cancel_main|knob_doneness_main|lever_handle)',),
    'CoffeeMachine': (r'start_button_\d+',),
    'ElectricKettle': (r'button_lid_main', r'handle_(?:\d+|main)', r'switch_main'),
    'Blender': (r'power_button_main', r'pitcher_handle_\d+', r'knob_speed_main', r'auxiliary_main_group_handle_main'),
    'StandMixer': (r'button_head_lock_main', r'knob_speed_main'),
    'Dishwasher': (r'door_handle_(?:\d+|main)',),
    'Oven': (r'door_handle_(?:\d+|main)', r'knob_(?:temp|time)_main'),
    'Fridge': (r'(?:left_|right_|freezer_|fridge_)?door_handle_(?:g\d+|\d+|main)',),
})
WHITELIST_SHA256 = hashlib.sha256(json.dumps(dict(PART_PATTERNS),sort_keys=True,
    separators=(',',':')).encode()).hexdigest()
DEFAULT_PILOT_TASKS = ('turn_on_microwave','turn_off_stove','open_drawer','open_cabinet')
PILOT_SEEDS = MappingProxyType({'open_cabinet': (101, 102, 103), 'close_blender_lid': (101, 102, 103), 'open_drawer': (101, 102, 103), 'turn_on_microwave': (101, 102, 103), 'turn_off_stove': (101, 102, 103), 'open_stand_mixer_head': (101, 102, 103), 'coffee_setup_mug': (101, 103, 104), 'prepare_coffee': (102, 103, 104), 'kettle_boiling': (101, 102, 103), 'heat_kebab_sandwich': (103, 105, 106), 'pick_place_counter_to_cabinet': (101, 102, 107), 'categorize_condiments': (102, 103, 105), 'weigh_ingredients': (101, 102, 103), 'arrange_tea': (104, 105, 106)})
PILOT_TASKS = tuple(PILOT_SEEDS)
NEIGHBOURHOOD_M = .01
PRESS_PATTERNS = frozenset(p for patterns in PART_PATTERNS.values() for p in patterns
    if 'button' in p or 'switch' in p)


def is_press_part(part):
    suffix=part.get('part_name',part['name'])[len(part['fixture'])+1:]
    return part['pattern'] in PRESS_PATTERNS and any(t in suffix for t in ('button','switch'))


def contact_radius(settings, part):
    return settings.fixture_press_contact_radius_m if is_press_part(part) else settings.fixture_contact_radius_m


def enabled(settings):
    return getattr(settings,'profile',None)==PROFILE


def config_enabled(config):
    return (config or {}).get('execution_settings',{}).get('profile')==PROFILE


def fixture_class(fixture):
    return next((cls.__name__ for cls in type(fixture).__mro__ if cls.__name__ in PART_PATTERNS),None)


def part_pattern(cls, suffix):
    return next((p for p in PART_PATTERNS.get(cls,()) if re.fullmatch(p,suffix)),None)


def task_fixtures(env):
    from robocasa.models.fixtures import Fixture
    candidates={k:v for k,v in vars(env).items() if isinstance(v,Fixture) and k!='init_robot_base_ref'}
    for key,value in (getattr(env,'fixture_refs',None) or {}).items():
        value=value[0] if isinstance(value,(tuple,list)) and value else value
        if key!='init_robot_base_ref' and isinstance(value,Fixture):candidates.setdefault(key,value)
    result={}
    for key,f in sorted(candidates.items()):
        if fixture_class(f) is not None:result.setdefault(f.name,(key,f))
    return result


def interaction_geoms(env, settings):
    if not enabled(settings):return {}
    from openarm365_dev.world import is_descendant
    m=env.sim.model;result={}
    for name,(ref,f) in sorted(task_fixtures(env).items()):
        cls=fixture_class(f);prefix=name+'_';root=m.body_name2id(f.root_body)
        for gid in range(m.ngeom):
            n=m.geom_id2name(gid) or ''
            if not n.startswith(prefix) or not (m.geom_contype[gid] or m.geom_conaffinity[gid]):continue
            pattern=part_pattern(cls,n[len(prefix):])
            if pattern and is_descendant(m,int(m.geom_bodyid[gid]),root):
                result[gid]=dict(geom_id=gid,name=n,fixture=name,fixture_class=cls,task_reference=ref,pattern=pattern)
    return result


def gripper_side(name):
    match=re.fullmatch(r'(?:gripper\d+_(?:(?:left|right)_)?|robot\d+_)?openarm_(left|right)_(?:(?:left_|right_)?finger|hand|palm)_collision\d+',name)
    return match.group(1) if match else None


def neighbourhood_geoms(env, settings, parts=None, candidates=None):
    if not enabled(settings):return {}
    import mujoco
    from openarm365_dev.world import is_descendant
    m,d=env.sim.model,env.sim.data;parts=interaction_geoms(env,settings) if parts is None else parts
    roots={name:m.body_name2id(f.root_body) for name,(_,f) in task_fixtures(env).items()}
    result={}
    for gid in range(m.ngeom) if candidates is None else sorted(candidates):
        if gid in parts or not (m.geom_contype[gid] or m.geom_conaffinity[gid]):continue
        nearby=[]
        for pid,part in parts.items():
            if not is_descendant(m,int(m.geom_bodyid[gid]),roots[part['fixture']]):continue
            distance=float(mujoco.mj_geomDistance(m._model,d._data,gid,pid,1e6,None))
            if not np.isfinite(distance):raise RuntimeError('Nonfinite fixture neighbourhood distance')
            if distance<=NEIGHBOURHOOD_M:
                nearby.append(dict(part_geom_id=pid,part_name=part['name'],distance_m=distance))
        if nearby:
            nearest=min(nearby,key=lambda row:row['distance_m']);part=parts[nearest['part_geom_id']]
            result[gid]=dict(part,geom_id=gid,name=m.geom_id2name(gid) or 'unnamed',
                contact_kind='neighbourhood',nearby_parts=nearby,part_distance_m=nearest['distance_m'],**nearest)
    return result


def fixture_contacts(env, settings, robot_root, parts=None):
    if not enabled(settings):return {}
    import mujoco
    from openarm365_dev.world import is_descendant
    from .fixture_distance import point_geom_distance
    m,d=env.sim.model,env.sim.data;parts=interaction_geoms(env,settings) if parts is None else parts
    candidates={int(gid) for c in d.contact if c.dist<=0 for gid in (c.geom1,c.geom2)
        if gripper_side(m.geom_id2name(int(c.geom1)) or '') or gripper_side(m.geom_id2name(int(c.geom2)) or '')}
    neighbours=neighbourhood_geoms(env,settings,parts,candidates-parts.keys())
    result={}
    for index,c in enumerate(d.contact):
        if c.dist>0:continue
        for rg,fg in ((int(c.geom1),int(c.geom2)),(int(c.geom2),int(c.geom1))):
            name=m.geom_id2name(rg) or '';side=gripper_side(name)
            if side is None or not is_descendant(m,int(m.geom_bodyid[rg]),robot_root):continue
            if fg in parts:
                pid=fg;distance=point_geom_distance(m,d,pid,c.pos)
                part=dict(parts[pid],contact_kind='part')
            elif fg in neighbours:
                matches=[(point_geom_distance(m,d,pid,c.pos),pid) for pid,p in parts.items()
                    if p['fixture']==neighbours[fg]['fixture']]
                matches=[(distance,pid) for distance,pid in matches if distance<=contact_radius(settings,parts[pid])]
                if not matches:continue
                distance,pid=min(matches)
                part=dict(neighbours[fg],pattern=parts[pid]['pattern'])
            else:continue
            part.update(part_geom_id=pid,part_name=parts[pid]['name'],part_kind='press' if is_press_part(parts[pid]) else 'other',
                contact_radius_m=contact_radius(settings,parts[pid]),contact_point_distance_m=distance,
                contact_point_world=np.asarray(c.pos,float).tolist())
            force=np.zeros(6);mujoco.mj_contactForce(m._model,d._data,index,force)
            if not np.isfinite(force).all():raise RuntimeError('Nonfinite fixture contact force')
            result[(rg,fg,index)]=dict(part,contact_index=index,robot_geom_id=rg,robot_geom_name=name,physical_side=side,
                distance_m=float(c.dist),contact_force_n=float(force[0]),contact_wrench=force.tolist(),
                force_norm_n=float(np.linalg.norm(force[:3])))
            break
    return result


def part_axes(env, gid):
    m,d=env.sim.model,env.sim.data;bid=int(m.geom_bodyid[gid]);axes=[]
    while bid>0:
        for jid in range(int(m.body_jntadr[bid]),int(m.body_jntadr[bid]+m.body_jntnum[bid])):
            kind=int(m.jnt_type[jid])
            if kind not in (2,3):continue
            axis=np.asarray(d.xaxis[jid],float);origin=np.asarray(d.xanchor[jid],float)
            axes.append(dict(joint=m.joint_id2name(jid),kind='slide' if kind==2 else 'hinge',
                axis_world=axis.tolist(),origin_world=origin.tolist(),
                range=np.asarray(m.jnt_range[jid],float).tolist(),
                position=float(d.qpos[m.jnt_qposadr[jid]])))
        bid=int(m.body_parentid[bid])
    return axes


def fixture_state(env, f):
    cls=fixture_class(f);state={}
    if hasattr(f,'get_door_state'):state['door_open_fraction']=f.get_door_state(env)
    elif hasattr(f,'door_joint_names') and f.door_joint_names:
        state['door_open_fraction']=f.get_joint_state(env,f.door_joint_names)
    if hasattr(f,'get_joint_state') and getattr(f,'door_joint_names',None):
        state['joint_open_fraction']=f.get_joint_state(env,f.door_joint_names)
    if cls=='Stove':state['knob_angles_rad']=f.get_knobs_state(env)
    elif cls=='Sink':state.update(f.get_handle_state(env))
    elif cls in ('Microwave','CoffeeMachine','Blender','ToasterOven','Oven'):state.update(f.get_state())
    elif cls in ('ElectricKettle','StandMixer','Dishwasher','Toaster'):state.update(f.get_state(env))
    # Cabinet get_state takes sim, unlike the appliance getters.
    return state


def find_fixtures(env, settings):
    if not enabled(settings):raise ValueError('find_fixtures requires '+PROFILE)
    from openarm365_dev.world import shape_box
    m,d=env.sim.model,env.sim.data;parts=interaction_geoms(env,settings);result=[]
    def plain(value):
        if isinstance(value,dict):return {str(k):plain(v) for k,v in value.items()}
        if isinstance(value,(tuple,list)):return [plain(v) for v in value]
        if hasattr(value,'tolist'):return plain(value.tolist())
        return value
    for name,(ref,f) in sorted(task_fixtures(env).items()):
        cls=fixture_class(f);rows=[]
        for gid,part in sorted(parts.items()):
            if part['fixture']!=name:continue
            center,half=shape_box(m,gid);rotation=np.asarray(d.geom_xmat[gid]).reshape(3,3)
            rows.append(dict(name=part['name'],geom_id=gid,frame='world',
                position=np.asarray(d.geom_xpos[gid],float).tolist(),
                center=(rotation@center+d.geom_xpos[gid]).tolist(),
                extent=(2*np.abs(rotation)@half).tolist(),axes=part_axes(env,gid)))
        result.append(dict(name=name,fixture_class=fixture_class(f),task_reference=ref,frame='world',
            state=plain(fixture_state(env,f)),interaction_parts=rows,
            task_controls={'selected_knob':str(env.knob)} if cls=='Stove' and hasattr(env,'knob') else {}))
    json.dumps(result,allow_nan=False)
    return result
