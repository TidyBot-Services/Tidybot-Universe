"""Switch for pre-starting the next execution's worker (try speed-up 2, user decision 11-14).

The user accepted the extra GPU memory (~1.8 GB per idle pre-started worker) for the LOCAL
setup only, and does not want it on Remote. So the feature is off unless the local service
opts in with ATTENTIONBENCH_PRESPAWN=local, and it is always off, whatever the environment
says, for anything that looks like Remote (host simulator time-share lock, public gateway
mode) and for Memory validation runs.
"""
import os

ENV = 'ATTENTIONBENCH_PRESPAWN'
LOCAL = 'local'


def prespawn_allowed(*, sim_lock, public_gateway_mode, validation, environ=None):
    environ = os.environ if environ is None else environ
    if sim_lock is not None or public_gateway_mode or validation is not None:
        return False
    return environ.get(ENV) == LOCAL
