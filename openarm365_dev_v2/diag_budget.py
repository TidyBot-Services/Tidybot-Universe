"""Task-budget scaling available only to the scripted grasp diagnostics.

This module changes resource counts, never physical or sandbox safety rules.
"""
import math
import os

SOURCE = 'ATTN_DIAG_BUDGET_SCALE'
MARKER = 'ATTN_DIAG_GRASP_PROBE'
BASE = {'sdk_calls': 80, 'control_steps': 2400, 'attempt_seconds': 480}


def _scaled_values():
    """The sole multiplier for every diagnostic task and ledger ceiling."""
    raw = os.environ.get(SOURCE)
    if raw is None:
        return None
    try:
        scale = float(raw)
    except (TypeError, ValueError):
        raise ValueError('ATTN_DIAG_BUDGET_SCALE must be a finite number in [1, 8]') from None
    if not math.isfinite(scale) or not 1 <= scale <= 8:
        raise ValueError('ATTN_DIAG_BUDGET_SCALE must be a finite number in [1, 8]')
    if os.environ.get(MARKER) != '1':
        raise ValueError('ATTN_DIAG_BUDGET_SCALE requires the grasp diagnostic probe')
    return {'scale': scale, **{k: math.ceil(v * scale) for k, v in BASE.items()},
            'startup_cleanup_seconds': math.ceil(60 * scale),
            'wall_limit': math.ceil(4260 * scale),
            'whole_case_seconds': math.ceil(1800 * scale)}


def diagnostic_budget():
    values = _scaled_values()
    if values is None:
        return None
    return {'scale': values['scale'], **{k: values[k] for k in BASE}, 'source': SOURCE}


def task_limits():
    """Default bytes and values are unchanged when the override is absent."""
    budget = diagnostic_budget()
    return {k: budget[k] for k in BASE} if budget else BASE.copy()


def probe_reserve(attempts=1):
    """Resource ceiling and wall reserve for isolated diagnostic attempts."""
    values = _scaled_values() or {**BASE, 'startup_cleanup_seconds': 60, 'wall_limit': 4260}
    cleanup = values['startup_cleanup_seconds']
    return {'sdk_calls': attempts * values['sdk_calls'],
            'control_steps': attempts * values['control_steps'],
            'execution_seconds': attempts * (values['attempt_seconds'] + cleanup),
            'startup_cleanup_seconds': cleanup, 'wall_limit': values['wall_limit']}


def policy_limit(conservative=False):
    """Generated pre-carry guards retain five SDK calls of headroom."""
    limit = task_limits()['sdk_calls']
    return limit - 5 if conservative else limit


def case_wall_seconds():
    values = _scaled_values()
    return values['whole_case_seconds'] if values else 1800
