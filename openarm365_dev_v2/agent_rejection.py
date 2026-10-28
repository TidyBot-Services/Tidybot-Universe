"""Typed pre-dispatch refusal, created only after authenticated client checks."""
from curobo_v2.client import PlanningRejected
from benchmarks.attention_harness.dev_agent_diagnostics import project_planner_result

# Only known solver/check failures, never timeout/transport/certificate failures.
REASONS = frozenset(('ik_joint_margin_below_execution_limit',
    'start_state_collision_or_constraint_violation', 'ik_failed', 'ik_no_solution',
    'motion_gen_failed', 'no_ik_solution', 'ik_path_joint_jump_exceeded',
    'official_motion_planner_non_success', 'official_ik_non_success',
    'goal_joint_margin_below_execution_limit', 'goal_state_collision_or_constraint_violation',
    'ik_geometry_validation_failed', 'ik_path_no_feasible_candidate',
    'trajectory_validation_failed', 'service_continuous_command_validation_rejected',
    'ik_joint_limits', 'ik_path_joint_jump',
    'trajectory_collision_or_constraint_violation'))

class RecoverablePlanRejection(PlanningRejected):
    def __init__(self, response):
        self.reason = response['reason']
        self.diagnostics = project_planner_result(response)
        super().__init__(self.reason)

    def public(self):
        return dict(ok=False, rejected=True, reason=self.reason, diagnostics=self.diagnostics)


def classify(result, body, world):
    from curobo_v2.identity import IdentityError
    if (result.get('world_revision'), result.get('world_sha256')) != world:
        raise IdentityError('Agent rejection has stale collision world')
    if not isinstance(body, dict) or body.get('execution_settings') is None:
        raise IdentityError('Agent requires frozen execution settings')
    if (result.get('status') == 'failed' and result.get('reason') in REASONS
            and result.get('trajectory') is None and result.get('qpos') is None
            and (isinstance(result.get('validation'), dict) or
                 result.get('reason') in {'official_ik_non_success','official_motion_planner_non_success','ik_path_no_feasible_candidate'})):
        raise RecoverablePlanRejection(result)


def is_recoverable(exc):
    return isinstance(exc, RecoverablePlanRejection)
