"""Ordered heading search with certified, non-executing carry lookahead."""
from copy import deepcopy
import numpy as np
from scipy.spatial.transform import Rotation

from .execution_settings import goal_margin


OUTPUT_FIELDS = ('heading_used_rad', 'headings_tried_rad', 'heading_candidates')


def feedback(response):
    return {key: deepcopy(response[key]) for key in OUTPUT_FIELDS if key in response}


def check_sdk_request(request, objects):
    if not isinstance(request, dict) or set(request) != {'object_name', 'lift_position', 'place_position'}:
        raise ValueError('grasp_search requires object_name, lift_position, place_position')
    if not isinstance(request['object_name'], str) or request['object_name'] not in objects:
        raise ValueError('grasp_search requires a public object name')
    for key in ('lift_position', 'place_position'):
        value = np.asarray(request[key], dtype=object)
        if (value.shape != (3,) or any(type(v) not in (int, float) for v in value)
                or not np.isfinite(value.astype(float)).all()):
            raise ValueError('grasp_search positions must be finite world xyz in metres')


def rotated(targets, offset, axis):
    result = deepcopy(targets)
    yaw = Rotation.from_rotvec(np.asarray(axis) * offset)
    for target in result.values():
        quat = np.asarray(target['quaternion_wxyz'])
        target['quaternion_wxyz'] = (yaw * Rotation.from_quat(quat[[1, 2, 3, 0]])).as_quat()[[3, 0, 1, 2]].tolist()
    return result


def pose(target):
    value = np.eye(4)
    value[:3, 3] = target['position']
    value[:3, :3] = Rotation.from_quat(np.asarray(target['quaternion_wxyz'])[[1, 2, 3, 0]]).as_matrix()
    return value


def stage_receipt(core, path):
    validation = core.last_validation or {}
    margin = validation.get('goal_joint_margin') or {}
    metrics = getattr(core, 'last_ik_path_metrics', {}) or {}
    if not margin and metrics.get('candidate_joint_margins'):
        selected = metrics.get('selected_candidate_index')
        if selected is not None:
            margin = metrics['candidate_joint_margins'][selected] or {}
    return dict(feasible=path is not None, reason=core.last_failure,
                minimum_joint_margin_rad=margin.get('minimum_joint_margin_rad'))


def search(core, current, targets, request, *, active_joints, locked_joints, mode, options):
    settings = getattr(core, 'execution_settings', None)
    offsets = getattr(settings, 'grasp_heading_search', ())
    if not offsets:
        raise ValueError('grasp_search requires r4-20261027')
    if core.attachment is not None or len(targets) != 1:
        raise ValueError('grasp_search requires one unattached descend-to-grasp target')
    if set(request) != {'lift_targets', 'place_targets', 'attachment', 'world_vertical'}:
        raise ValueError('Complete grasp_search lift/place/attachment/vertical required')
    tool = next(iter(targets))
    for stage in ('lift_targets', 'place_targets'):
        if set(request[stage]) != {tool}:
            raise ValueError('grasp_search must use the same tool in all stages')
        core.check_targets(request[stage])
    axis = np.asarray(request['world_vertical'], float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or abs(np.linalg.norm(axis)-1) > 1e-6:
        raise ValueError('grasp_search requires a unit world vertical in planner coordinates')
    if np.dot(np.asarray(request['lift_targets'][tool]['position']) - np.asarray(targets[tool]['position']), axis) <= 0:
        raise ValueError('grasp_search lift_position must be above the grasp along world vertical')
    core.last_validation = None
    core.last_failure = None
    core.last_timing = None
    core.last_derivatives = None
    core.last_solver_diagnostics = []
    core.last_ik_path_metrics = {}
    original_world = deepcopy(core._world_cuboids)
    original_context = deepcopy(core._attachment_context)
    original_failure = None
    diagnostics = []
    saved_names = ('last_validation', 'last_failure', 'last_timing', 'last_derivatives',
                   'last_solver_diagnostics', 'last_ik_path_metrics')

    def motion(start, goal, ik_result=None):
        if mode == 'ik_path':
            return core.plan_ik_path(start, targets=goal, active_joints=active_joints,
                                    locked_joints=locked_joints, _ik_result=ik_result, **options)
        return core.plan_pose(start, targets=goal, active_joints=active_joints,
                              locked_joints=locked_joints)

    # Try the exact request first, including its entire carry lookahead.
    batches = ([0.], list(offsets))
    try:
        for headings in batches:
            goals = [rotated(targets, angle, axis) for angle in headings]
            core.set_attachment(None)
            core.set_collision_world(original_world)
            results = core.heading_ik_batch(current, goals, active_joints, locked_joints)
            for angle, goal, ik in zip(headings, goals, results):
                core.set_attachment(None)
                core.set_collision_world(original_world)
                successes = np.asarray(ik.success.detach().cpu()).reshape(-1)
                values = np.asarray(ik.solution.detach().cpu()).reshape(len(successes), -1)
                margins = []
                for q, ok in zip(values, successes):
                    if not ok or not np.isfinite(q).all():
                        continue
                    full = current.copy()
                    for name, value in zip(active_joints, q):
                        full[core.joint_names.index(name)] = value
                    margins.append(goal_margin(full, core.joint_names, core.limits, active_joints,
                                               execution_settings=settings)['minimum_joint_margin_rad'])
                row = dict(heading_rad=angle, ik_success=bool(successes.any()),
                           minimum_joint_margin_rad=max(margins) if margins else None,
                           joint_margin_limit_rad=settings.goal_joint_margin_rad, reason=None, stages={})
                diagnostics.append(row)
                if angle != 0. and not successes.any():
                    row['reason'] = 'official_ik_non_success'
                elif angle != 0. and (not margins or max(margins) < settings.goal_joint_margin_rad):
                    row['reason'] = 'ik_joint_margin_below_execution_limit'
                else:
                    # Preserve the original requested-heading call and its error
                    # precedence; deviations consume the batched IK candidates.
                    grasp = motion(current, goal, None if angle == 0. else ik)
                    row['stages']['grasp'] = stage_receipt(core, grasp)
                    if grasp is not None:
                        row['ik_success'] = True
                        actual_margin = row['stages']['grasp']['minimum_joint_margin_rad']
                        if actual_margin is not None:
                            row['minimum_joint_margin_rad'] = actual_margin
                    if grasp is None:
                        row['reason'] = core.last_failure
                    else:
                        saved = {name: deepcopy(getattr(core, name, None)) for name in saved_names}
                        context = deepcopy(request['attachment'])
                        context['tcp_object_transform'] = (np.linalg.inv(pose(goal[tool])) @ np.asarray(context['object_pose'])).tolist()
                        core.set_attachment(context)
                        state = grasp[-1]
                        for stage in ('lift', 'place'):
                            stage_goal = rotated(request[stage+'_targets'], angle, axis)
                            path = motion(state, stage_goal)
                            row['stages'][stage] = stage_receipt(core, path)
                            if path is None:
                                row['reason'] = core.last_failure
                                break
                            state = path[-1]
                            if stage == 'lift':
                                # Rebase the hypothetical payload world for the next leg.
                                moved = pose(stage_goal[tool]) @ np.asarray(context['tcp_object_transform'])
                                change = moved @ np.linalg.inv(np.asarray(context['object_pose']))
                                world = deepcopy(original_world)
                                for shape in world:
                                    if shape['name'] in context['shape_names']:
                                        transform = change @ pose(dict(position=shape['center'], quaternion_wxyz=shape['quat_wxyz']))
                                        shape['center'] = transform[:3, 3].tolist()
                                        shape['quat_wxyz'] = Rotation.from_matrix(transform[:3, :3]).as_quat()[[3, 0, 1, 2]].tolist()
                                context['object_pose'] = moved.tolist()
                                context['support_names'] = []  # Departure ended; later contact is ordinary collision.
                                core.set_attachment(None)
                                core.set_collision_world(world)
                                core.set_attachment(context)
                        if row['reason'] is None:
                            for name, value in saved.items():
                                setattr(core, name, value)
                            row['reason'] = 'selected'
                            core.last_heading_search = dict(heading_used_rad=angle,
                                headings_tried_rad=[r['heading_rad'] for r in diagnostics], heading_candidates=diagnostics)
                            return grasp
                if angle == 0.:
                    original_failure = row['reason']
        core.last_failure = original_failure
        core.last_heading_search = dict(headings_tried_rad=[r['heading_rad'] for r in diagnostics],
                                        heading_candidates=diagnostics)
        return None
    finally:
        core.set_attachment(None)
        core.set_collision_world(original_world)
        core.set_attachment(original_context)
