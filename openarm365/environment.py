"""Construct unchanged upstream tasks with a separately registered OpenArm model."""
from pathlib import Path
import json,hashlib
import numpy as np
from .model import ROOT

def model_identity():
    return json.loads((ROOT/'model/identity.json').read_text())

def create_env(env_name,**kw):
    import robocasa,robosuite
    from .robot import controller_config,initial_pose
    kw.pop('split',None)
    kw['camera_names']=['robot0_agentview_left','gripper0_right_eye_in_hand']
    with initial_pose(env_name):
        env=robosuite.make(env_name=env_name,controller_configs=controller_config(),has_renderer=False,has_offscreen_renderer=True,ignore_done=True,use_object_obs=True,use_camera_obs=True,initialization_noise=None,**kw)

    reset=env.reset
    def reset_with_approved_pose(*args,**kwargs):
        with initial_pose(env_name): return reset(*args,**kwargs)
    env.reset=reset_with_approved_pose
    return env

def validate_physical_model(env):
    r=env.robots[0]
    assert type(r.robot_model).__name__=='OpenArm365'
    assert len(r.robot_model.arm_joints)==14 and len(r.robot_model.base_joints)==3
    assert len(r.gripper['left'].joints)==2
    # Full equality and limits are in model identity; this checks compiled torque ranges.
    assert np.allclose(env.sim.model.actuator_ctrlrange[r._ref_actuators_indexes_dict['right']][:,1],[40,40,27,27,7,7,7])

def task_geometry(env,c):
    out={}
    if hasattr(env,'sink'):
        out['sink_pos']=np.array(env.sink.pos).tolist()
        out['sink_size']=np.array(env.sink.size).tolist()
    # Explicit read-only simulator regions support rim grasps for wide mugs.
    for name in getattr(env,'obj_body_id',{}):
        region=env.sim.model.geom_name2id(name+'_reg_int') if name+'_reg_int' in env.sim.model.geom_names else -1
        if region>=0:
            out[name+'_aperture_pos']=env.sim.data.geom_xpos[region].tolist()
            out[name+'_aperture_size']=env.sim.model.geom_size[region].tolist()
    for item in env.get_ep_meta().get('object_cfgs',[]):
        name=item.get('name');asset=item.get('info',{}).get('mjcf_path','')
        # Reviewed annotation: mug_3 collision_mesh_8 is its outer handle bar.
        if asset.endswith('/objaverse/mug/mug_3/model.xml'):
            geom=env.sim.model.geom_name2id(name+'_g10')
            out[name+'_handle_pos']=env.sim.data.geom_xpos[geom].tolist()
    if hasattr(env,'fxtr'):
        f=env.fxtr;out['cabinet_pos']=np.array(f.pos).tolist()
        for side in ['left','right']:
            geom=f.name+'_'+side+'_door_handle_g1'
            if geom in env.sim.model.geom_names:
                out['cabinet_'+side+'_handle_pos']=env.sim.data.geom_xpos[env.sim.model.geom_name2id(geom)].tolist()
            joint=f.name+'_'+side+'doorhinge'
            if joint in env.sim.model.joint_names:
                index=env.sim.model.joint_name2id(joint);out['cabinet_'+side+'_hinge_pos']=env.sim.data.xanchor[index].tolist()
    return out

def physics_sample(env):
    r=env.robots[0]; sim=env.sim
    return {'robot_qpos':sim.data.qpos[r._ref_arm_joint_pos_indexes].tolist(),'base_qpos':sim.data.qpos[r._ref_base_joint_pos_indexes].tolist(),'eef_quat':{a:__import__('scipy.spatial.transform',fromlist=['Rotation']).Rotation.from_matrix(sim.data.site_xmat[r.eef_site_id[native]].reshape(3,3)).as_quat().tolist() for a,native in [('left','right'),('right','left')]},'fixture_joints':{n:float(sim.data.qpos[sim.model.get_joint_qpos_addr(n)]) for n in getattr(getattr(env,'fxtr',None),'door_joint_names',[])},'robot_qvel':sim.data.qvel[r._ref_arm_joint_vel_indexes].tolist(),'gripper_qpos':{a:sim.data.qpos[r._ref_gripper_joint_pos_indexes[native]].tolist() for a,native in [('left','right'),('right','left')]},'eef':{a:sim.data.site_xpos[r.eef_site_id[native]].tolist() for a,native in [('left','right'),('right','left')]},'objects':{n:sim.data.body_xpos[i].tolist() for n,i in getattr(env,'obj_body_id',{}).items()},'contacts':[[sim.model.geom_id2name(c.geom1),sim.model.geom_id2name(c.geom2),float(c.dist)] for c in sim.data.contact]}

def configure_display_camera(env):
    # A world camera keeps the actual arm and target in view while the base moves.
    from scipy.spatial.transform import Rotation
    if not hasattr(env,'sink') and not hasattr(env,'fxtr') and hasattr(env,'cab'):
        # Pick/place cabinets can be upper or lower: retain the fixture height.
        target=np.array(env.cab.pos,dtype=float)
    else:
        target=np.array(env.sink.pos if hasattr(env,'sink') else env.fxtr.pos,dtype=float)
        target[2]=1.05 if hasattr(env,'sink') else 1.55
    pos=target+np.array([1.7,-1.2,1.25]);z=(pos-target)/np.linalg.norm(pos-target)
    x=np.cross([0,0,1],z);x/=np.linalg.norm(x);y=np.cross(z,x)
    cam=env.sim.model.camera_name2id('robot0_agentview_left')
    env.sim.model.cam_pos[cam]=pos
    env.sim.model.cam_quat[cam]=Rotation.from_matrix(np.column_stack([x,y,z])).as_quat()[[3,0,1,2]]
    env.sim.forward()
