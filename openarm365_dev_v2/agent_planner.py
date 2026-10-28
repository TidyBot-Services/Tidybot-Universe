"""Agent planner: only a typed, authenticated rejection avoids the old latch."""
from openarm365_dev.planner import MujocoV2Planner
from .agent_rejection import RecoverablePlanRejection

class AgentPlanner(MujocoV2Planner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.execution_settings is None:
            self.close(); raise ValueError('agent_requires_frozen_settings')
        self.client.agent_mode = True

    def guard(self, operation):
        if self.failed: return super().guard(operation)
        try: return operation()
        except RecoverablePlanRejection:
            self.ledger.invalidate('pre_dispatch_rejection')
            raise
        except BaseException:
            self.failed = True
            self.ledger.invalidate('planner_failure'); self.owner.close('planner_failure')
            raise

    def check_goal_margin(self, target, active):
        from curobo_v2.client import PlanningRejected
        self.client.attest()
        try: return super().check_goal_margin(target, active)
        except PlanningRejected:
            # This method performs only the existing numeric margin check.
            raise RecoverablePlanRejection(dict(reason='goal_joint_margin_below_execution_limit',
                validation={'joint_limits':False},joint_margin_limit_rad=.02))
