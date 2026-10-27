"""Dense carried-sphere/support diagnostics; exact geometry decides departure."""
import numpy as np
from scipy.spatial.transform import Rotation


def support_departure(spheres, world, supports, *, measured_start=True):
    """Report every carried sphere against each declared departure support.

    Use the same fixed start-overlap floor as exact geometry/sweep, including
    their historical -0.1 mm floor for unmeasured goal queries. These inflated
    cell-cover spheres are not contact geometry: even this floor can reject a
    motion that complete shapes certify. This report must not veto that motion.
    Robot/support and carried/fixture checks remain in the ordinary checker.
    """
    values = np.asarray(spheres, float)
    if values.ndim != 3 or values.shape[-1] != 4 or not np.isfinite(values).all():
        raise ValueError('Invalid carried proxy samples')
    from curobo_v2.execution_settings import departure_floor
    evidence = dict(mode='monotone', representation='carried_sphere_proxy',
                    authoritative=False, measured_start=measured_start,
                    acceptance_basis='exact_geometry_and_continuous_sweep',
                    collision_free=True, supports={})
    for cuboid in world:
        name = cuboid['name']
        if name not in supports:
            continue
        rotation = Rotation.from_quat(np.asarray(cuboid.get('quat_wxyz', [1, 0, 0, 0]))[[1, 2, 3, 0]]).as_matrix()
        local = (values[..., :3] - cuboid['center']) @ rotation
        outside = np.abs(local) - np.asarray(cuboid['half_size'])
        gaps = (np.linalg.norm(np.maximum(outside, 0), axis=-1)
                + np.minimum(np.max(outside, axis=-1), 0) - values[..., 3])
        floors = np.array([departure_floor(gap if measured_start else None) for gap in gaps[0]])
        row = dict(start_separation_m=gaps[0].tolist(),
                   minimum_separation_m=np.min(gaps, axis=0).tolist(),
                   floor_m=floors.tolist(),
                   departing_spheres=int(((gaps[0] < .005) & measured_start).sum()))
        evidence['supports'][name] = row
        for index, gap in enumerate(gaps):
            bad = np.flatnonzero(gap < floors)
            if len(bad) and evidence['collision_free']:
                evidence.update(collision_free=False, support=name, sample=index,
                                sphere_indices=bad.tolist(), separation_m=gap[bad].tolist(),
                                floor_m=floors[bad].tolist(),
                                reason='carried_proxy_below_start_floor')
    return evidence
