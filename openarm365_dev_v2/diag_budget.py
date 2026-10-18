"""Task-budget scaling for Dev v2 and isolated scripted diagnostics.

This module changes resource counts, never physical or sandbox safety rules.
"""
import math
import os

SOURCE = 'ATTN_DIAG_BUDGET_SCALE'
MARKER = 'ATTN_DIAG_GRASP_PROBE'
BASE = {'sdk_calls': 80, 'control_steps': 2400, 'attempt_seconds': 480}
DEV_TIER_BUDGET_SCALE = 4
TIERS = ('legacy', 'dev')


def check_tier(tier):
    if tier not in TIERS or type(tier) is not str:
        raise ValueError('budget_tier must be legacy or dev')
    return tier


def formal_task_limits():
    """Frozen formal envelope, independent of any development environment."""
    return BASE.copy()


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


def dev_tier_budget():
    """Explicit evidence for ordinary Dev runs; probes retain their own marker."""
    if diagnostic_budget() is not None:
        return None
    return {'scale': DEV_TIER_BUDGET_SCALE,
            **{k: v * DEV_TIER_BUDGET_SCALE for k, v in BASE.items()},
            'source': 'dev_tier_default'}


def task_limits(budget_tier='dev'):
    """A diagnostic override replaces, rather than compounds, the Dev default."""
    check_tier(budget_tier)
    override=diagnostic_budget()
    if budget_tier=='legacy':
        if override is not None:raise ValueError('legacy budget tier cannot use diagnostic scale')
        return formal_task_limits()
    budget = override or dev_tier_budget()
    return {k: budget[k] for k in BASE}


def probe_reserve(attempts=1):
    """Resource ceiling and wall reserve for isolated diagnostic attempts."""
    values = _scaled_values() or {**task_limits(), 'startup_cleanup_seconds': 60 * DEV_TIER_BUDGET_SCALE,
                                  'wall_limit': 4260 * DEV_TIER_BUDGET_SCALE}
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
    return values['whole_case_seconds'] if values else 1800 * DEV_TIER_BUDGET_SCALE
