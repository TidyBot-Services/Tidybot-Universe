"""Only the executing fingers may intentionally contact this task's target."""
import numpy as np


def unexpected_contacts(scene, *, side, target_id):
    rows = []
    fingers = {f'openarm_{side}_left_finger', f'openarm_{side}_right_finger'}
    for contact in scene.get_contacts():
        a, b = [body.entity for body in contact.bodies]
        for robot, other in [(a, b), (b, a)]:
            if not robot.name.startswith('openarm_'): continue
            if robot.name == 'openarm_body_link0' and 'floor' in other.name: continue
            if robot.name in fingers and other.per_scene_id == target_id: continue
            points = [{'position': p.position.tolist(), 'separation_m': float(p.separation),
                       'impulse_Ns': p.impulse.tolist()} for p in contact.points
                      if p.separation < 0 and np.linalg.norm(p.impulse) > 0]
            if points:
                rows.append({'bodies': [robot.name, other.name], 'body_ids': [robot.per_scene_id, other.per_scene_id], 'points': points})
            break
    return rows


def bilateral_target_contacts(scene, *, side, target_id):
    fingers = {f'openarm_{side}_left_finger', f'openarm_{side}_right_finger'}
    proof = {}
    for contact in scene.get_contacts():
        a, b = [body.entity for body in contact.bodies]
        for finger, other in [(a, b), (b, a)]:
            if finger.name not in fingers or other.per_scene_id != target_id: continue
            points = [{'position': p.position.tolist(), 'separation_m': float(p.separation),
                       'impulse_Ns': p.impulse.tolist()} for p in contact.points
                      if p.separation <= 0 and np.linalg.norm(p.impulse) > 0]
            if points: proof[finger.name] = points
    return proof if set(proof) == fingers else None
