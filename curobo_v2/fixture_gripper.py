"""R5 partial-opening contract and recoverable execution refusal."""
import math
from .client import PlanningRejected

MIN_WIDTH_M=.005257
OPEN_WIDTH_M=.022
WIDTH_TOLERANCE_M=.001
DESCRIPTION='move_to(width_m): partial close, per-finger opening 0.005257–0.022 m, no grasp, for pressing buttons with fingertips together'


def wire_width(value):
    return repr(value) if type(value) is float and not math.isfinite(value) else value


class GripperWidthRejected(PlanningRejected):
    def __init__(self, reason, diagnostics):
        self.reason=reason;self.diagnostics=diagnostics
        super().__init__(reason)

    def public(self):
        return dict(ok=False,rejected=True,reason=self.reason,diagnostics=self.diagnostics)
