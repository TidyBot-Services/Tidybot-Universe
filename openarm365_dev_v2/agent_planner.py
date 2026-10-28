"""Agent planner: only a typed, authenticated rejection avoids the old latch."""
from openarm365_dev.planner import MujocoV2Planner
from .agent_rejection import RecoverablePlanRejection


class _ProbeResult(BaseException):
    """Carries a dry-run response out of the planning closure before any plan is issued."""
    def __init__(self, response):
        super().__init__('probe_result');self.response=response


class _ProbeLedger:
    """While probing, issuing an executable plan is replaced by returning the response."""
    def __init__(self, real):self.real=real
    def issue(self, response, **kwargs):raise _ProbeResult(response)
    def __getattr__(self, name):return getattr(self.real, name)


class AgentPlanner(MujocoV2Planner):
    agent_probe_only = False

    @property
    def ledger(self):
        return _ProbeLedger(self._ledger) if self.agent_probe_only else self._ledger

    @ledger.setter
    def ledger(self, value):
        self._ledger = value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.execution_settings is None:
            self.close(); raise ValueError('agent_requires_frozen_settings')
        self.client.agent_mode = True

    def guard(self, operation):
        if self.failed: return super().guard(operation)
        try: return operation()
        except _ProbeResult as result:
            return result.response
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
            import numpy as np
            import xml.etree.ElementTree as ET
            from curobo_v2.execution_settings import goal_margin
            joints={j.get('name'):j for j in ET.parse(self.spec['robot_cfg']['kinematics']['urdf_path']).getroot().findall('joint')}
            limits=np.array([[float(joints[n].find('limit').get(b)) for b in ('lower','upper')] for n in self.names])
            margin=goal_margin(target,self.names,limits,active,execution_settings=self.execution_settings)
            # This method performs only the existing numeric margin check.
            raise RecoverablePlanRejection(dict(reason='goal_joint_margin_below_execution_limit',
                validation={'joint_limits':False},**margin))
