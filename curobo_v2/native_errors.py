"""Distinguish solver refusal from infrastructure failure without a retry."""
from urllib.error import HTTPError,URLError
from .identity import IdentityError
from .client import PlanningRejected

def classify_failure(error,*,planning):
    if isinstance(error,IdentityError):return 503,'v2_identity_failed'
    if isinstance(error,InterruptedError):return 503,'v2_request_canceled'
    if isinstance(error,TimeoutError):return 503,'v2_upstream_timeout'
    if isinstance(error,(OSError,HTTPError,URLError)):return 503,'v2_upstream_unavailable'
    if isinstance(error,PlanningRejected):return 422,'v2_plan_rejected_no_motion'if planning else'v2_rejected'
    return 503,'v2_internal_error'
