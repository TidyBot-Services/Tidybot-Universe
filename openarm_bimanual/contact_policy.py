"""Both task grippers may touch only the native target; all other contacts stop."""
from openarm_robocasa.contact_policy import bilateral_target_contacts
import numpy as np


def grasp_command(side, holders, holds, free_limit):
    """An explicitly commanded re-close settles the certified existing target."""
    if side not in {'left','right'}:raise ValueError('Unknown arm')
    maintain=side in holders and side in holds
    goal=holds[side] if maintain else free_limit
    if not np.isfinite(goal) or goal < free_limit:raise ValueError('Invalid grasp target')
    return float(goal), maintain


def permitted_target_sides(allowed_sides,handoff,active):
    sides=list(allowed_sides)
    if handoff and handoff.get('phase')=='completed':
        sides=[handoff['receiver']]
        acquiring=(active or {}).get('grasp_arm')
        if acquiring in allowed_sides and acquiring not in sides:sides.append(acquiring)
    return sorted(sides)


def unexpected_contacts(scene, *, sides, target_id, target_contact_sides=None):
    fingers = {f'openarm_{s}_{f}_finger' for s in (sides if target_contact_sides is None else target_contact_sides) for f in ['left','right']}
    rows = []
    for contact in scene.get_contacts():
        a,b = [body.entity for body in contact.bodies]
        for robot,other in [(a,b),(b,a)]:
            if not (robot.name.startswith('openarm_') or robot.name == 'base_link'): continue
            if robot.name in {'openarm_body_link0','base_link'} and 'floor' in other.name: continue
            if robot.name in fingers and other.per_scene_id == target_id: continue
            points = [{'position':p.position.tolist(),'separation_m':float(p.separation),'impulse_Ns':p.impulse.tolist()}
                      for p in contact.points if p.separation < 0 and np.linalg.norm(p.impulse) > 0]
            if points: rows.append({'bodies':[robot.name,other.name],'body_ids':[robot.per_scene_id,other.per_scene_id],'points':points})
            break
    return rows


def release_allowed(handoff, donor, *, minimum_steps=12):
    receiver = handoff.get('receiver')
    proof = handoff.get('receiver_contacts')
    return (handoff.get('receiver_grasp_status') == 'completed' and donor in {'left','right'} and receiver in {'left','right'} and donor != receiver
            and isinstance(proof,dict)
            and set(proof) == {f'openarm_{receiver}_{f}_finger' for f in ['left','right']}
            and all(proof.values()) and handoff.get('contact_steps',0) >= minimum_steps)


def contact_hold_goal(driver, follower, *, inset, free_limit, reference='driver'):
    """Preserve the accepted contact controller closure from the driver."""
    if not all(np.isfinite(v) for v in [driver,follower,inset,free_limit]) or inset < 0:
        raise ValueError('Invalid contact-hold measurement')
    if reference not in {'driver','pair_mean'}:raise ValueError('Unknown contact-hold reference')
    center=driver if reference=='driver' else (driver+follower)/2
    return max(free_limit, float(center-inset))


def target_finger_contacts(scene,*,side,target_id):
    names={f'openarm_{side}_{f}_finger' for f in ['left','right']};result={}
    for contact in scene.get_contacts():
        a,b=[body.entity for body in contact.bodies]
        for finger,target in [(a,b),(b,a)]:
            if finger.name not in names or target.per_scene_id!=target_id:continue
            points=[{'position':p.position.tolist(),'normal':p.normal.tolist(),'separation_m':float(p.separation),'impulse_Ns':p.impulse.tolist()}
                    for p in contact.points if p.separation<=0 and np.linalg.norm(p.impulse)>0]
            if points:result.setdefault(finger.name,[]).extend(points)
    return result


def release_hold_metrics(reference, current, *, contact_age, unilateral, fingers, goal):
    """Contact jitter cannot substitute for object/receiver tracking and grip checks.

    Acquisition still requires 12 consecutive positive bilateral physical contacts.
    During donor opening, a missing bilateral impulse is accepted only with a
    current positive target contact, a recent bilateral contact and the original
    4 mm / 0.03 rad / 1 mm / 1 mm mimic constraints. This is observation only.
    """
    from scipy.spatial.transform import Rotation
    reference, current, fingers = map(np.asarray, (reference, current, fingers))
    if (reference.shape != (4,4) or current.shape != (4,4) or fingers.shape != (2,)
            or not all(np.isfinite(x).all() for x in [reference,current,fingers]) or not np.isfinite(goal)):
        return {'lost':True,'reason':'invalid_grasp_measurement'}
    translation=float(np.linalg.norm(current[:3,3]-reference[:3,3]))
    rotation=float(np.linalg.norm(Rotation.from_matrix(current[:3,:3]@reference[:3,:3].T).as_rotvec()))
    finger_error=float(np.max(abs(fingers-goal)));mimic_error=float(abs(fingers[0]-fingers[1]))
    return {'lost':not unilateral or contact_age>=12 or translation>.004 or rotation>.03
                    or finger_error>.001 or mimic_error>.001,
            'translation_error_m':translation,'rotation_error_rad':rotation,
            'finger_error_m':finger_error,'mimic_error_m':mimic_error,'bilateral_contact_age_steps':contact_age}


def certified_hold_metrics(certificate, current, *, step, bilateral, unilateral, fingers, goal):
    """Current measured bilateral contact refreshes age, never the grasp origin."""
    age = 0 if bilateral else step-certificate['step']
    return release_hold_metrics(certificate['relative'], current, contact_age=age,
        unilateral=unilateral, fingers=fingers, goal=goal)
