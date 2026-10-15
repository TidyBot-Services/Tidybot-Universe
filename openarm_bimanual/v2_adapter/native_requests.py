"""Strict native HTTP values before NumPy conversion, world upload, or GPU calls."""
import math
import numpy as np

def finite_vector(value,length,label):
    if not isinstance(value,(list,tuple))or len(value)!=length or any(
            isinstance(v,bool)or not isinstance(v,(int,float))or not math.isfinite(v)for v in value):
        raise ValueError(f'{label} requires exactly {length} finite numeric values, excluding bool/string')
    return np.array(value,dtype=float)

def rotation_matrix(value):
    raw=np.asarray(value)
    if raw.shape!=(3,3)or raw.dtype.kind not in 'iuf' or not np.isfinite(raw).all():
        raise ValueError('Expected a finite numeric 3x3 rotation matrix')
    matrix=raw.astype(float)
    if not np.allclose(matrix.T@matrix,np.eye(3),rtol=0,atol=1e-6)or abs(np.linalg.det(matrix)-1)>1e-6:
        raise ValueError('Orientation must be a proper rotation, without scale or reflection')
    return matrix

def position_request(body):
    if not isinstance(body,dict)or not {'position'}<=set(body)<= {'position','frame','length_unit'}:
        raise ValueError('Expected position with optional declared world frame and m unit')
    if body.get('frame','world')!='world'or body.get('length_unit','m')!='m':
        raise ValueError('Native position requires world coordinates in metres')
    return finite_vector(body['position'],3,'position').tolist()

def joint_request(body,*,names,epoch,limits):
    if not isinstance(body,dict)or not {'target_qpos','epoch'}<=set(body)<={'target_qpos','epoch','joint_names','angular_unit'}:
        raise ValueError('Expected target_qpos, current integer epoch, optional joint_names and rad unit')
    if type(body['epoch'])is not int:raise ValueError('epoch must be an integer excluding bool')
    if body.get('angular_unit','rad')!='rad':raise ValueError('Native joint coordinates require radians')
    requested=body.get('joint_names',names)
    if(not isinstance(requested,list)or len(requested)!=len(names)or any(not isinstance(n,str)for n in requested)
            or len(set(requested))!=len(requested)or set(requested)!=set(names)):
        raise ValueError('joint_names must name every native planner joint exactly once')
    q=finite_vector(body['target_qpos'],len(names),'target_qpos')
    target=np.array([q[requested.index(n)]for n in names])
    if body['epoch']!=epoch:raise LookupError('Joint planning request belongs to an old epoch')
    if np.any(target<limits[:,0])or np.any(target>limits[:,1]):raise ValueError('Target exceeds actual native joint limits')
    return target.tolist()

def trajectory_request(body,epoch):
    if not isinstance(body,dict)or set(body)!={'plan_id','epoch'}or type(body['epoch'])is not int:
        raise ValueError('Expected one-use hexadecimal plan_id and integer epoch')
    if body['epoch']!=epoch:raise LookupError('Trajectory belongs to an old epoch')
    pid=body['plan_id']
    if not isinstance(pid,str)or len(pid)!=32 or any(c not in '0123456789abcdef'for c in pid):
        raise ValueError('plan_id must be a 32-character lowercase hexadecimal capability')
    return pid
