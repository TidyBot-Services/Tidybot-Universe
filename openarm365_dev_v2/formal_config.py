"""Formal configuration for the OpenArm365 dual-arm V2 runner.

Same robot, task, scene, controller and identity lock as the Dev v2 configuration. Only the
declared boundary and the three budget fields differ: the experiment freeze chooses them,
inside the envelope the Dev v2 runner has been verified for. Dev v2 configurations are unchanged.
"""
import copy

from benchmarks.attention_harness.dev_tasks import SDK
from .dev_config import SCHEMA as DEV_SCHEMA, make_dev_config, validate_dev_config

SCHEMA = 'attentionbench.openarm365-formal-config.v2'
# Dev v2 values for the fields a formal config chooses; used to reuse the Dev validator.
DEV_WHOLE_CASE_SECONDS = 1800


def _check_budget(attempt_wall_seconds, whole_case_wall_seconds, sdk_call_limit):
    if any(type(v) is not int for v in (attempt_wall_seconds, whole_case_wall_seconds, sdk_call_limit)):
        raise ValueError('formal budgets must be integers')
    if not 1 <= attempt_wall_seconds <= SDK['attempt_seconds']:
        raise ValueError('formal attempt wall outside the verified Dev v2 envelope')
    if not attempt_wall_seconds <= whole_case_wall_seconds <= DEV_WHOLE_CASE_SECONDS:
        raise ValueError('formal whole-case wall must cover one attempt and stay within the Dev v2 envelope')
    if not 1 <= sdk_call_limit <= SDK['sdk_calls']:
        raise ValueError('formal SDK call limit outside the verified Dev v2 envelope')


def make_formal_config(task_id, seed, lock, *, attempt_wall_seconds, whole_case_wall_seconds,
                       sdk_call_limit, layout_id=11, style_id=34, expected_source_root=None,
                       scene_reset_seed=None, scene_contract=None):
    _check_budget(attempt_wall_seconds, whole_case_wall_seconds, sdk_call_limit)
    c = copy.deepcopy(make_dev_config(task_id, seed, lock, layout_id, style_id,
                                      expected_source_root=expected_source_root))
    c.update(schema_version=SCHEMA, runner_boundary='formal', attempt_wall_seconds=attempt_wall_seconds,
             whole_case_wall_seconds=whole_case_wall_seconds, sdk_call_limit=sdk_call_limit)
    if scene_reset_seed is not None:
        c['scene_reset_seed'] = scene_reset_seed
    if scene_contract is not None:
        c['scene_contract'] = scene_contract
    return c


def validate_formal_config(c, *, expected_source_root=None):
    if not isinstance(c, dict) or c.get('schema_version') != SCHEMA:
        raise ValueError('OpenArm365 formal configuration required')
    if c.get('runner_boundary') != 'formal':
        raise ValueError('formal configuration must declare the formal boundary')
    if 'eval_agent' in c:
        raise ValueError('recording review is a Dev option, not part of a formal configuration')
    _check_budget(c.get('attempt_wall_seconds'), c.get('whole_case_wall_seconds'), c.get('sdk_call_limit'))
    # Everything except the boundary and the three budget fields is validated by the Dev
    # validator on the equivalent Dev view, so identity, lock, scene and robot checks stay single-sourced.
    dev_view = dict(c, schema_version=DEV_SCHEMA, runner_boundary='dev_loop',
                    whole_case_wall_seconds=DEV_WHOLE_CASE_SECONDS, sdk_call_limit=SDK['sdk_calls'])
    validate_dev_config(dev_view, expected_source_root=expected_source_root)
    return c
