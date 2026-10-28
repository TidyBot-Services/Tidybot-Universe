"""Agent-only, recursive allowlist for planner evidence (REVIEW R7).

Unknown keys and unsafe text never cross the boundary. Missing evidence is
explicit; this module does not change or manufacture planner diagnostics.
"""
import math
import re

FORBIDDEN = re.compile(r'native_success|object_in_sink|native_result|check_success|reward|nonce|token|secret|password|Bearer|traceback|(?:/home/|/tmp/|/etc/|file:|https?://)|[A-Za-z]:\\', re.I)
FIELDS = frozenset('''status reason stage validation solver_diagnostics success feasible
joint_limits collision_free joint_names qpos solution_qpos candidate_qpos trajectory ik_candidates
ik_candidate_distances ik_candidate_reasons candidate_distances candidate_reasons
candidate_joint_margins selected_candidate selected_candidate_index selected_distance
selected_max_joint_change selection_seconds ik_seconds joint_margin_limit_rad
minimum_joint_margin_rad joint_margins_rad closest_joint joint_name lower upper margin
position_error rotation_error cspace_error position_error_m rotation_error_rad
position_tolerance rotation_tolerance cspace_tolerance solve_time total_time
world_revision interpolation_dt duration timing planning_seconds collision contacts
body1 body2 geom1 geom2 distance contact_distance max_joint_jump goal_margin goal_joint_margin tool_errors orientation_error_rad
exact_geometry proxy_collision_free attachment bodies
time_from_start_seconds trajectory_derivatives velocity acceleration jerk
ik_selected_joint_distance_rad ik_selected_max_joint_change_rad ik_selection_seconds
orientation_tolerance returned metrics interpolated_metrics constraints hybrid_costs_constraints
index candidate_index evaluated heading_used_rad headings_tried_rad heading_candidates
heading_rad accepted diagnostics_complete missing error_code elapsed_seconds
samples max_joint_step_rad interpolated_samples ik_path_max_joint_change
continuous_geometry continuous_motion official_derivative_limits
max_velocity max_acceleration max_jerk velocity_limit acceleration_limit jerk_limit
minimum_distance clearance_m path_clearance_m start_clearance_m sample_count
contact_count colliding_bodies colliding_links contact_points normals depths
max_position_error max_rotation_error max_cspace_error position_error_mean
rotation_error_mean cspace_error_mean check name value limit units checks
'''.split())
REQUIRED = ('candidate_qpos', 'ik_candidates', 'candidate_joint_margins', 'selected_candidate',
            'solver_diagnostics', 'validation', 'contacts', 'world_revision', 'timing')


def safe_text(value):
    return isinstance(value, str) and not FORBIDDEN.search(value) and not re.search(r'/(?:[\w.-]+/)+[\w.-]*|(?<!\w)/[\w.-]+|\.\./|\\\\', value)


def project(value):
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if safe_text(value) else '[withheld]'
    if isinstance(value, (list, tuple)):
        return [project(v) for v in value]
    if isinstance(value, dict):
        return {k: ({name: project(number) for name, number in v.items()
                    if re.fullmatch(r'openarm_(?:left|right)_joint[1-7]', name)}
                    if k == 'joint_margins_rad' and isinstance(v, dict) else
                    {name: project(item) for name,item in v.items() if re.fullmatch(r'openarm_(?:left|right)_hand_tcp',name)}
                    if k == 'tool_errors' and isinstance(v,dict) else project(v))
                for k, v in value.items() if k in FIELDS}
    if hasattr(value, 'tolist'):
        return project(value.tolist())
    return None


def project_planner_result(result):
    result = result if isinstance(result, dict) else {}
    public = project(result)
    def present(key):
        if key in public and public[key] not in (None, [], {}): return True
        if key == 'contacts':
            validation=public.get('validation',{})
            return bool(validation.get('contacts') or validation.get('exact_geometry',{}).get('contacts'))
        if key == 'timing':
            return any(public.get(k) is not None for k in ('time_from_start_seconds','ik_seconds','ik_selection_seconds'))
        return False
    missing = [key for key in REQUIRED if not present(key)]
    public.update(diagnostics_complete=not missing, missing=missing)
    return public


def screen_text(text):
    """Withhold the entire authored artifact rather than silently truncate it."""
    return text if safe_text(text) else '[artifact withheld by public screening]\n'


def public_observation(value):
    """Numeric observation tree; public names cannot smuggle paths or text."""
    if isinstance(value, dict):
        return {k: public_observation(v) for k, v in value.items()
                if isinstance(k, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_ .-]{0,127}', k)
                and safe_text(k)}
    if isinstance(value, str):
        return value if safe_text(value) and re.fullmatch(r'[A-Za-z0-9_ .-]{0,128}', value) else '[withheld]'
    if isinstance(value, (list, tuple)):
        return [public_observation(v) for v in value]
    return project(value)
