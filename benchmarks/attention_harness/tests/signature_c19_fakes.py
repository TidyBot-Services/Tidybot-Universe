"""C19 synthetic planner errors on the real worker/importer/validator pipeline."""
from contextlib import contextmanager
from unittest.mock import patch

from . import autovalidate_fakes as base
from . import cross_seed_fakes as cross
from .positive_control_fakes import run

TARGET='ik_joint_margin_below_execution_limit'
OTHER='official_ik_non_success'
SIGNATURE=dict(public_error_class='RuntimeError: PlanningRejected',operation='left_arm.move_delta',reason_code=TARGET)


@contextmanager
def planner_errors(signature=SIGNATURE):
    original=base.SceneFixture.make
    controls={}
    def make(f,name,*args,**kwargs):
        reason=TARGET
        if name!='failed':
            if kwargs.get('exposure')=='candidate_validation':reason=OTHER
            else:
                index=controls.get(id(f),0);controls[id(f)]=index+1
                reason=TARGET if index%5<3 else OTHER
        write=f.write
        def emit(path,value):
            if path.name=='trace.json' and isinstance(value,dict) and value.get('error'):
                value['error']='RuntimeError: PlanningRejected: cuRoboV2 planning rejected: '+reason+(
                    '; best evaluated IK candidate has closest joint openarm_left_joint7 at 0.01 rad from its limit' if reason==TARGET else '')
            return write(path,value)
        with patch.object(f,'write',emit):return original(f,name,*args,**kwargs)
    with patch.object(base.SceneFixture,'make',make),patch.object(base,'SIGNATURE',signature),patch.object(cross,'SIGNATURE',signature):
        yield


def lifecycle(root):
    with planner_errors():
        fake,state,record=run(root,treatments=(True,)*5,cross=True)
    pc=fake.authority.positive_controls
    assert pc.get(fake.spec['control_id'])['failure_signature']==SIGNATURE
    assert pc.get_plan(fake.spec['control_id'])['failure_signature']==SIGNATURE
    verdict=record['verdict'];report=verdict['report']
    assert verdict['decision']=='would_pass',verdict
    assert report['control_signature_count']==3 and report['treatment_signature_count']==0
    assert len([p for p in record['pairs'] if p['seed']==101])==5 and fake.replays==2
    assert record['cross_seed']['task_trust']['verdict']=='would be trusted for this task'
    assert fake.cross_batches and all(b['failure_signature']==SIGNATURE for b in fake.cross_batches)
    return fake,state,record
