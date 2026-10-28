"""Trusted reset-scene probes. No trajectory issue/claim or physics stepping."""
import hashlib
import json
import math


def action_arguments(action):
    """Normalize one SDK action without dispatching it; duplicate args fail."""
    group=action['group'];args=action['args'];kwargs=action['kwargs']
    names=(['target_qpos'] if group=='bimanual' else ['dx','dy','dyaw'] if group=='base'
           else ['dx','dy','dz','rotation_delta'])
    if len(args)>len(names) or set(kwargs)-set(names) or set(names[:len(args)]) & set(kwargs):
        raise ValueError('invalid_action_args')
    values=dict(zip(names,args));values.update(kwargs)
    if group=='bimanual':
        if 'target_qpos' not in values:raise ValueError('target_required')
        return [values['target_qpos']]
    if group=='base' and set(values)!=set(names):raise ValueError('base_args_required')
    return [values.get(name,[0.,0.,0.] if name=='rotation_delta' else 0.) for name in names]


def validate(op, payload):
    def vector(value, n):
        if not isinstance(value, list) or len(value) != n or any(type(v) not in (int,float) or not math.isfinite(v) for v in value):
            raise ValueError('finite_vector_required')
    if op not in ('observe', 'find_objects', 'ik_check', 'plan_dry_run'): raise ValueError('unsupported_probe')
    if 'base_offset' in payload:
        offset=payload['base_offset']
        if op not in ('ik_check','plan_dry_run') or not isinstance(offset,dict) or set(offset)!={'dx','dy','dyaw'}:
            raise ValueError('invalid_base_offset')
        vector([offset[k] for k in ('dx','dy','dyaw')],3)
        from openarm365_dev.base_motion import validate_delta
        validate_delta(**offset)
    if 'start_joints' in payload:
        joints = payload['start_joints']
        if not isinstance(joints, dict) or not joints: raise ValueError('invalid_start_joints')
        names = {f'openarm_{side}_joint{i}' for side in ('left','right') for i in range(1,8)}
        if set(joints) - names: raise ValueError('unknown_joint')
        vector(list(joints.values()), len(joints))
    if op == 'find_objects' and 'names' in payload:
        if not isinstance(payload['names'],list) or any(not isinstance(n,str) or len(n)>128 for n in payload['names']): raise ValueError('invalid_names')
    if op == 'ik_check':
        target = payload.get('target', {})
        if not isinstance(target,dict) or set(target) != {'side','position','quaternion_xyzw'} or target['side'] not in ('left','right'): raise ValueError('invalid_target')
        vector(target['position'],3);vector(target['quaternion_xyzw'],4)
        if abs(sum(x*x for x in target['quaternion_xyzw'])-1)>1e-5: raise ValueError('unit_quaternion_required')
    if op == 'plan_dry_run':
        action = payload.get('action', {})
        if not isinstance(action,dict) or set(action) != {'group','operation','args','kwargs'}: raise ValueError('invalid_action')
        if (action['group'],action['operation']) not in {('left_arm','move_delta'),('right_arm','move_delta'),('bimanual','move_joints'),('base','move_delta')}:
            raise ValueError('unsupported_action')
        if not isinstance(action['args'],list) or not isinstance(action['kwargs'],dict): raise ValueError('invalid_action_args')
        args=action_arguments(action)
        if action['group']=='bimanual':
            if len(args)!=1:raise ValueError('one_target_required')
            vector(args[0],14)
        elif action['group']=='base': vector(args,3)
        else:
            if len(args) not in (3,4):raise ValueError('invalid_delta')
            vector(args[:3],3)
            if len(args)==4:vector(args[3],3)
            if any(abs(v)>.05 for v in args[:3]):raise ValueError('translation_limit')
            if len(args)==4 and any(abs(v)>.5 for v in args[3]):raise ValueError('rotation_limit')


def state_fingerprint(env, executor):
    data = env.sim.data
    h = hashlib.sha256()
    for name in ('qpos','qvel','ctrl','act','mocap_pos','mocap_quat','qacc_warmstart'):
        value = getattr(data,name,None)
        if value is not None:h.update(value.tobytes())
    h.update(repr((data.time,executor.sequence,executor.physics_steps,getattr(executor,'held',None))).encode())
    return h.hexdigest()


def probe(env, executor, planner, observe, request):
    import numpy as np
    from scipy.spatial.transform import Rotation
    from openarm365_dev.environment import mount_pose,tcp_pose
    from benchmarks.attention_harness.dev_agent_diagnostics import project_planner_result
    from .agent_rejection import RecoverablePlanRejection
    from openarm365_dev.base_motion import BasePathRejected
    op, payload = request['op'],request['payload'];validate(op,payload)
    if executor.sequence != 0 or executor.physics_steps != 0:raise ValueError('probe_owner_must_be_reset')
    base = dict(state_basis='reset_scene', physics_steps=0)
    if executor.held or payload.get('held_object') or payload.get('attachment'):
        return dict(base,unsupported_scope='held_object')
    before = state_fingerprint(env,executor)
    if op=='observe':return dict(base,observation=observe())
    if op=='find_objects':
        values=observe()['perception']['values'];names=payload.get('names')
        return dict(base,objects=[dict(name=k[:-4],position=v,frame='world') for k,v in values.items()
                                  if k.endswith('_pos') and (names is None or k[:-4] in names)])
    data=env.sim.data
    saved={n:getattr(data,n).copy() for n in ('qpos','qvel','ctrl','act','mocap_pos','mocap_quat','qacc_warmstart') if hasattr(data,n)}
    try:
        for name,value in payload.get('start_joints',{}).items():
            i=planner.names.index(name)
            if not executor.limits[i][0] <= value <= executor.limits[i][1]:raise ValueError('start_joint_limit')
            data.qpos[planner.addresses[i]]=value
        if payload.get('start_joints'):env.sim.forward()
        if 'base_offset' in payload:
            from openarm365_dev.base_motion import check_exact_base_path,validate_delta,base_joint_delta
            from openarm365_dev.world import snapshot
            delta=validate_delta(**payload['base_offset'])
            try:
                audit=check_exact_base_path(env,snapshot(env,chassis_binding=planner.chassis_binding),
                    planner.chassis_binding,delta,executor.base_initial,
                    execution_settings=executor.execution_settings)
                accepted=True
            except BasePathRejected as exc:
                audit=exc.collision_check;accepted=False
            base['base_clearance']=dict(accepted=accepted,**project_planner_result(audit))
            base['base_offset']=payload['base_offset']
            indexes=env.robots[0]._ref_base_joint_pos_indexes
            current=data.qpos[indexes].copy()
            data.qpos[indexes]=current+base_joint_delta(delta,current,executor.base_initial)
            env.sim.forward()
        planner.agent_probe_only=True
        if op=='ik_check':
            target=payload['target'];side=target['side'];active=[f'openarm_{side}_joint{i}' for i in range(1,8)]
            if tuple(active) not in planner.prepared:planner.prepare(active)
            planner.upload();body=planner.body(active);local=np.linalg.inv(mount_pose(env))
            rot=local[:3,:3]@Rotation.from_quat(target['quaternion_xyzw']).as_matrix()
            body['targets']={f'openarm_{side}_hand_tcp':dict(position=(local@np.r_[target['position'],1])[:3].tolist(),
                quaternion_wxyz=Rotation.from_matrix(rot).as_quat()[[3,0,1,2]].tolist())}
            planner.client.call('/warmup',{k:v for k,v in body.items() if k not in ('env_id','targets')})
            body=dict(body)
            body['seed_q']=body.pop('current_q')
            from openarm365_dev.planning_rejection import call_with_rejection_facts
            settings=getattr(executor,'execution_settings',None)
            result=(call_with_rejection_facts(planner.client,'/plan/ik',body,settings)
                    if settings is not None else planner.client.call('/plan/ik',body))
            if result.get('qpos') is not None:
                from curobo_v2.execution_settings import minimum_arm_margin
                margin=dict(minimum_arm_margin(result['qpos'],planner.names,executor.limits,active),
                    joint_margin_limit_rad=settings.goal_joint_margin_rad if settings is not None else .02)
                result['goal_margin']=margin
                if margin['minimum_joint_margin_rad']<margin['joint_margin_limit_rad']:
                    return dict(base,planning_accepted=False,rejected=True,
                        reason='goal_joint_margin_below_execution_limit',diagnostics=project_planner_result(result))
        else:
            action=payload['action'];group=action['group'];args=action_arguments(action)
            if group=='base':
                from openarm365_dev.base_motion import validate_delta
                from openarm365_dev.base_motion import check_exact_base_path
                from openarm365_dev.world import snapshot
                result=check_exact_base_path(env,snapshot(env,chassis_binding=planner.chassis_binding),
                    planner.chassis_binding,validate_delta(*args),executor.base_initial,
                    execution_settings=executor.execution_settings)
            elif group=='bimanual':result=planner.plan_joints(args[0],side='both',epoch=executor.epoch)
            else:
                side=group.split('_')[0];current=tcp_pose(env,side)
                rotation=args[3] if len(args)==4 else [0,0,0]
                if executor.any_object_contact():return dict(base,unsupported_scope='unconfirmed_contact')
                result=planner.plan_pose(current[:3,3]+np.asarray(args[:3]),
                    Rotation.from_rotvec(rotation).as_matrix()@current[:3,:3],side=side,epoch=executor.epoch)
            if group!='base':
                from openarm365_dev.executor import diagnostic_settle_steps,official_timing_exceeds_ceiling,sample_for_original_drive
                steps=diagnostic_settle_steps();dt=float(env.control_timestep)
                count=math.ceil(result['time_from_start_seconds'][-1]/dt)+steps
                if official_timing_exceeds_ceiling(count,steps):
                    return dict(base,planning_accepted=False,reason='action_timing_limit',diagnostics=project_planner_result(result))
                kin=planner.spec['robot_cfg']['kinematics']['cspace']
                sample_for_original_drive(result,joint_names=planner.names,maximum_speed=executor.max_speed,
                    position_limits=executor.limits,max_acceleration=kin['max_acceleration'],max_jerk=kin['max_jerk'],dt=dt,step_budget=count,settle_steps=steps)
        return dict(base,planning_accepted=True,diagnostics=project_planner_result(result))
    except BasePathRejected as exc:
        return dict(base,planning_accepted=False,rejected=True,reason='base_path_rejected',
                    diagnostics=project_planner_result(getattr(exc,'collision_check',{})))
    except RecoverablePlanRejection as exc:
        return dict(base,planning_accepted=False,**exc.public())
    finally:
        planner.agent_probe_only=False
        for name,value in saved.items():getattr(data,name)[:]=value
        if payload.get('start_joints') or 'base_offset' in payload:env.sim.forward()
        # forward may update warmstart; restore all primary state exactly.
        for name,value in saved.items():getattr(data,name)[:]=value
        if state_fingerprint(env,executor)!=before:raise RuntimeError('probe_mutated_scene')
