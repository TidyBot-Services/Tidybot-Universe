"""Convert measured idle joints to valid drive targets without changing physics."""
import numpy as np

def bounded_idle_pose(current,limits,*,slack):
    current=np.asarray(current,float);limits=np.asarray(limits,float);slack=np.asarray(slack,float)
    if (limits.shape!=(current.size,2) or current.ndim!=1 or slack.shape!=current.shape
            or not np.isfinite(current).all() or not np.isfinite(limits).all()
            or not np.isfinite(slack).all() or np.any(slack<0)):
        raise ValueError('Invalid idle pose measurement')
    if np.any(current<limits[:,0]-slack) or np.any(current>limits[:,1]+slack):
        raise ValueError('Measured idle pose exceeds frozen joint-limit slack')
    return np.clip(current,limits[:,0],limits[:,1])


def idle_hold_metrics(current,reference,joint_order,requested):
    current=np.asarray(current,float);reference=np.asarray(reference,float)
    result={'arm':{},'gripper':{},'violation':None}
    for side in ['left','right']:
        if side not in requested:
            indices=[joint_order.index(f'openarm_{side}_joint{i}') for i in range(1,8)]
            result['arm'][side]=float(np.max(abs(current[indices]-reference[indices])))
        if side+'_gripper' not in requested:
            indices=[joint_order.index(f'openarm_{side}_finger_joint{i}') for i in [1,2]]
            result['gripper'][side]=float(np.max(abs(current[indices]-reference[indices])))
    if any(v>.01 for v in result['arm'].values()):result['violation']='idle_arm_drift'
    elif any(v>.0005 for v in result['gripper'].values()):result['violation']='idle_gripper_drift'
    return result


def idle_arm_goal(goal,current,joint_order,requested,holds):
    goal=np.asarray(goal,float).copy();current=np.asarray(current,float)
    holds={s:list(values) for s,values in holds.items()}
    for side in ['left','right']:
        indices=[joint_order.index(f'openarm_{side}_joint{i}') for i in range(1,8)]
        if side in requested:
            holds.pop(side,None)
        else:
            if side not in holds:
                holds[side]=(goal[indices] if side+'_gripper' in requested else current[indices]).tolist()
            goal[indices]=holds[side]
    return goal,holds
