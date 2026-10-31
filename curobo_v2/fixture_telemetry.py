"""Record-only pilot measurements. No force or tracking error threshold."""
import numpy as np
from .fixture_interaction import enabled
from .client import PlanningRejected


class FixtureContactBaseDisplacementRejected(PlanningRejected):
    reason='fixture_contact_base_displacement_excessive'

    def __init__(self, diagnostics):
        self.diagnostics=diagnostics
        super().__init__(self.reason)

    def public(self):
        return dict(ok=False,rejected=True,reason=self.reason,diagnostics=self.diagnostics)


def rehold(executor):
    settings=getattr(executor,'execution_settings',None)
    if not enabled(settings):return
    stats=getattr(executor,'_fixture_motion',{})
    if not stats.get('guard_record_only_steps') or 'base_rehold' in stats:return
    before=np.asarray(executor.base_hold,float).copy()
    after=np.asarray(executor.env.sim.data.qpos[executor.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
    if before.shape!=(3,) or after.shape!=(3,) or not np.isfinite(np.r_[before,after]).all():
        raise RuntimeError('Nonfinite or invalid fixture base re-hold pose')
    translation=float(np.linalg.norm(after[:2]-before[:2]));yaw=float(abs(after[2]-before[2]))
    peak_translation=max(translation,getattr(executor,'_fixture_rehold_translation_max',0.))
    peak_yaw=max(yaw,stats['maximum_base_drift_yaw_rad'])
    executor.planner.prepared.clear()
    if (peak_translation>settings.fixture_contact_base_rehold_max_translation_m or
            peak_yaw>settings.fixture_contact_base_rehold_max_yaw_rad):
        diagnostics=dict(before=before.tolist(),after=after.tolist(),translation_m=translation,yaw_rad=yaw,
            maximum_translation_m=peak_translation,maximum_yaw_rad=peak_yaw,
            translation_limit_m=settings.fixture_contact_base_rehold_max_translation_m,
            yaw_limit_rad=settings.fixture_contact_base_rehold_max_yaw_rad)
        executor.planner.ledger.invalidate(FixtureContactBaseDisplacementRejected.reason)
        exc=FixtureContactBaseDisplacementRejected(diagnostics)
        executor.log(executor.journal,dict(event='receipt',status='rejected',request_id=executor.active_request,
            sequence=executor.sequence,physics_steps=executor.physics_steps,**exc.public(),**receipt(executor)))
        raise exc
    executor.base_hold=after.copy()
    if hasattr(executor,'_drift_reference'):
        executor.record_drift_sample()
        executor._drift_reference=after.copy()
    executor.planner.ledger.invalidate('fixture_contact_rehold')
    stats['base_rehold']=dict(before=before.tolist(),after=after.tolist(),translation_m=translation,yaw_rad=yaw)
    executor.log(executor.journal,dict(event='fixture_base_rehold',request_id=executor.active_request,
        sequence=executor.sequence,physics_steps=executor.physics_steps,base_rehold=stats['base_rehold']))


def begin(executor):
    if enabled(getattr(executor,'execution_settings',None)):
        executor._fixture_rehold_translation_max=0.
        executor._fixture_motion=dict(contact_steps=0,contact_count=0,maximum_force_n=0.,
            maximum_joint_tracking_error_rad=0.,first_physics_step=None,last_physics_step=None,
            part_contact_count=0,neighbourhood_contact_count=0,
            maximum_base_drift_translation_m=0.,maximum_base_drift_yaw_rad=0.,
            maximum_gripper_mimic_error_m={},guard_record_only_steps=0,
            parts={},neighbourhood={},sides=set())


def sample(executor, contacts, q):
    if not contacts:return None
    errors=np.asarray(q)-executor.goals
    if not np.isfinite(errors).all():raise RuntimeError('Nonfinite fixture joint tracking error')
    row=dict(event='fixture_contact_sample',request_id=executor.active_request,physics_step=executor.physics_steps,
        mode='record_only',contacts=list(contacts.values()),
        part_contacts=[c for c in contacts.values() if c['contact_kind']=='part'],
        neighbourhood_contacts=[c for c in contacts.values() if c['contact_kind']=='neighbourhood'],
        joint_tracking_error_rad=dict(zip(executor.planner.names,errors.tolist())))
    stats=getattr(executor,'_fixture_motion',None)
    if stats is not None:
        stats['contact_steps']+=1;stats['contact_count']+=len(contacts)
        stats['maximum_force_n']=max(stats['maximum_force_n'],max(c['force_norm_n'] for c in contacts.values()))
        stats['maximum_joint_tracking_error_rad']=max(stats['maximum_joint_tracking_error_rad'],float(max(abs(errors))))
        if stats['first_physics_step'] is None:stats['first_physics_step']=executor.physics_steps
        stats['last_physics_step']=executor.physics_steps
        for c in contacts.values():
            kind=c['contact_kind'];stats[kind+'_contact_count']+=1
            stats['parts' if kind=='part' else 'neighbourhood'][c['geom_id']]={k:v for k,v in c.items()
                if k in ('geom_id','name','fixture','pattern','part_geom_id','part_name','part_distance_m',
                    'part_kind','contact_radius_m','contact_point_distance_m')}
            stats['sides'].add(c['physical_side'])
    executor.log(executor.journal,row)
    return row


def guards(executor, base, held_base, grippers):
    if not enabled(getattr(executor,'execution_settings',None)):return False
    from openarm365_dev.base_motion import base_drift
    translation,yaw=base_drift(base,held_base)
    mimic={side:float(abs(q[0]-q[1])) for side,q in grippers.items()}
    stats=getattr(executor,'_fixture_motion',None)
    if stats is None:return False
    stats['maximum_base_drift_translation_m']=max(stats['maximum_base_drift_translation_m'],translation)
    stats['maximum_base_drift_yaw_rad']=max(stats['maximum_base_drift_yaw_rad'],yaw)
    for side,value in mimic.items():
        stats['maximum_gripper_mimic_error_m'][side]=max(stats['maximum_gripper_mimic_error_m'].get(side,0.),value)
    contact=stats['contact_steps']>0
    if contact:
        executor._fixture_rehold_translation_max=max(getattr(executor,'_fixture_rehold_translation_max',0.),
            float(np.linalg.norm(np.asarray(base)[:2]-np.asarray(held_base)[:2])))
        stats['guard_record_only_steps']+=1
        executor.log(executor.journal,dict(event='fixture_guard_sample',request_id=executor.active_request,
            physics_step=executor.physics_steps,mode='record_only',base_drift_translation_m=translation,
            base_drift_yaw_rad=yaw,gripper_mimic_error_m=mimic))
    return contact


def receipt(executor, *, after_physics_step=None):
    if not enabled(getattr(executor,'execution_settings',None)):return {}
    if after_physics_step is not None and executor.physics_steps<=after_physics_step:return {}
    stats=getattr(executor,'_fixture_motion',{})
    return dict(fixture_pilot=dict(mode='record_only',force_limit_n=None,attachment='no_attachment',
        telemetry_journal='native-actions.jsonl',
        **{k:v for k,v in stats.items() if k not in ('parts','neighbourhood','sides','base_rehold')},
        parts=list(stats.get('parts',{}).values()),neighbourhood=list(stats.get('neighbourhood',{}).values()),
        physical_sides=sorted(stats.get('sides',()))),
        **({'base_rehold':stats['base_rehold']} if 'base_rehold' in stats else {}))
