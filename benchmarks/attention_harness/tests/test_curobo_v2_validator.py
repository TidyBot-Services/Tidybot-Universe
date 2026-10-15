from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from curobo_v2.sapien_validator import KitchenCollisionValidator
from curobo_v2.client import PlanningRejected


def validator():
    value=object.__new__(KitchenCollisionValidator);value.rig=SimpleNamespace(robot=SimpleNamespace(dof=2))
    value.collision_scene=lambda:None
    return value


def test_native_veto_checks_curve_instead_of_obstructed_endpoint_chord():
    value=validator();segments=[]
    def check(target,*,start,**kwargs):
        segments.append((start.copy(),target.copy()))
        middle=(start+target)/2
        obstructed=any(.012<p[0]<.018 and p[1]<.001 for p in [start,middle,target])
        return dict(collision_free=not obstructed,samples=3)
    value.check_path=check
    result=value.validate_trajectory([[0,0],[0,.03],[.03,.03],[.03,0]],cancel_check=lambda:False)
    assert result['collision_free']
    assert max(np.max(abs(b-a))for a,b in segments)<=.005+1e-9
    assert not value.validate_trajectory([[0,0],[.03,0]],cancel_check=lambda:False)['collision_free']
    value.check_path=lambda *a,**kw:dict(collision_free=False,samples=2,contacts=['native_mesh'])
    assert not value.validate_trajectory([[0,0],[.03,0]],cancel_check=lambda:False)['collision_free']


def test_cancel_during_native_validation_cannot_issue_action():
    value=validator();checks=[]
    def check(*args,**kwargs):checks.append(1);return dict(collision_free=True,samples=2)
    value.check_path=check
    with pytest.raises(InterruptedError):
        value.validate_trajectory([[0,0],[.03,.03]],cancel_check=lambda:len(checks)==1)
    assert len(checks)==1


def test_collision_validator_cannot_generate_any_plan():
    assert not hasattr(KitchenCollisionValidator,'plan')
    assert not hasattr(KitchenCollisionValidator,'inverse')
    source=Path(__import__('curobo_v2.sapien_validator',fromlist=['']).__file__).read_text()
    assert 'least_squares'not in source and 'bounded_rrt'not in source


@pytest.mark.parametrize('trajectory',[[[0,0]],[[0,0],[float('nan'),0]],[[0],[1]]])
def test_invalid_native_path_is_never_dispatched(trajectory):
    with pytest.raises(PlanningRejected):validator().validate_trajectory(trajectory,cancel_check=lambda:False)
