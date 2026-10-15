from copy import deepcopy
import json
from pathlib import Path
import numpy as np
import pytest
from curobo_v2.trajectory import TrajectoryLedger,world_matches
from curobo_v2.command_curve import sample_for_original_drive
from curobo_v2.client import PlanningRejected


def fixture():
    response=json.loads((Path(__file__).parent/'fixtures/recorded_v2_micro_joint_plan.json').read_text())
    args=dict(epoch=4,state=deepcopy(response['trajectory'][0]),world=[dict(name='door',center=[0.,0.,0.],half_size=[1.,1.,1.])],
              identity={'runtime':{'generation':2},'nonce':'owner'},attachment={'holder':'left','receipt':'4:grasp'})
    return args,response


def test_one_use_and_consumed_capability():
    args,response=fixture();ledger=TrajectoryLedger();pid=ledger.issue(response,**args)
    assert ledger.claim(pid,**args)==response
    with pytest.raises(PlanningRejected):ledger.claim(pid,**args)


@pytest.mark.parametrize('change',['epoch','qpos','door','shape','removed','owner','grasp','expired','stop'])
def test_stale_trajectory_never_dispatches(change):
    args,response=fixture();now=[1.];ledger=TrajectoryLedger(clock=lambda:now[0]);pid=ledger.issue(response,**args)
    if change=='epoch':args['epoch']+=1
    elif change=='qpos':args['state'][0]+=.006
    elif change=='door':args['world'][0]['center'][0]=.001
    elif change=='shape':args['world'][0]['half_size'][0]=1.001
    elif change=='removed':args['world']=[]
    elif change=='owner':args['identity']['nonce']='other'
    elif change=='grasp':args['attachment']=None
    elif change=='expired':now[0]=10.
    else:ledger.invalidate('stop')
    with pytest.raises(PlanningRejected):ledger.claim(pid,**args)
    with pytest.raises(PlanningRejected):ledger.claim(pid,**fixture()[0])


def test_quaternion_sign_and_tiny_static_drift():
    args,_=fixture();a=args['world'];b=deepcopy(a);b[0]['quat_wxyz']=[-1.,0,0,0];b[0]['center'][0]=1e-6
    assert world_matches(a,b)
    b[0]['quat_wxyz']=[.9999995,0,0,.001]
    assert not world_matches(a,b)


def test_float32_world_quaternion_is_not_stale_without_motion():
    args,_=fixture();a=args['world'];a[0]['quat_wxyz']=[.70710677,0,0,.70710677]
    assert world_matches(a,deepcopy(a))


def test_original_times_preserved_and_budget_overrun_rejected():
    _,response=fixture();names=response['joint_names']
    kwargs=dict(joint_names=names,maximum_speed={n:.5 for n in names},position_limits=response['robot']['contract']['limits'],max_acceleration=5.,max_jerk=100.,dt=1/240,step_budget=720,settle_steps=120)
    samples,receipt=sample_for_original_drive(response,**kwargs)
    assert receipt['time_scale']==1. and receipt['official_time_from_start_seconds']==response['time_from_start_seconds']
    assert np.max(abs(np.diff(samples[:,0])))<=.5/240+1e-10
    assert np.allclose(samples[-1],response['trajectory'][-1],rtol=0,atol=1e-12)
    response['time_from_start_seconds']=[t*10 for t in response['time_from_start_seconds']]
    response['trajectory_derivatives']['velocity']=(np.asarray(response['trajectory_derivatives']['velocity'])/10).tolist()
    response['trajectory_derivatives']['acceleration']=(np.asarray(response['trajectory_derivatives']['acceleration'])/100).tolist()
    with pytest.raises(PlanningRejected,match='no retiming'):sample_for_original_drive(response,**kwargs)


@pytest.mark.parametrize('field,value',[('status','failed'),('trajectory',[[.2,.0],[.3,0.]]),('joint_names',['left','left'])])
def test_unusable_plan_has_no_execution_capability(field,value):
    args,response=fixture();response[field]=value
    with pytest.raises(PlanningRejected):TrajectoryLedger().issue(response,**args)
