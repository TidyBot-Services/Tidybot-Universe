"""Formal configuration for the OpenArm365 dual-arm V2 runner.

Same robot, task, scene, controller and identity lock as the Dev v2 configuration. Only the
declared boundary and the three budget fields differ: the experiment freeze chooses them,
inside the original 80/2400/480 envelope. The Dev tier has a separate budget.
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
    # Reproducible reset is not optional in a formal configuration: without it the same task and
    # seed lands on different physical scenes, and policies are no longer compared on the same scene.
    if scene_reset_seed is None:
        scene_reset_seed = seed
    if scene_reset_seed != seed:
        raise ValueError('scene reset seed must equal the configuration seed')
    _check_budget(attempt_wall_seconds, whole_case_wall_seconds, sdk_call_limit)
    c = copy.deepcopy(make_dev_config(task_id, seed, lock, layout_id, style_id,
                                      expected_source_root=expected_source_root, budget_boundary='formal', budget_tier='legacy'))
    c.update(schema_version=SCHEMA, runner_boundary='formal', attempt_wall_seconds=attempt_wall_seconds,
             whole_case_wall_seconds=whole_case_wall_seconds, sdk_call_limit=sdk_call_limit)
    c['scene_reset_seed'] = scene_reset_seed
    if scene_contract is not None:
        c['scene_contract'] = scene_contract
    return c


def validate_formal_config(c, *, expected_source_root=None):
    if not isinstance(c, dict) or c.get('schema_version') != SCHEMA:
        raise ValueError('OpenArm365 formal configuration required')
    if c.get('runner_boundary') != 'formal':
        raise ValueError('formal configuration must declare the formal boundary')
    if 'eval_agent' in c or 'human_proxy' in c:
        raise ValueError('recording review is a Dev option, not part of a formal configuration')
    if c.get('scene_reset_seed') != c.get('seed') or type(c.get('scene_reset_seed')) is not int:
        raise ValueError('a formal configuration requires reproducible reset (scene_reset_seed equal to the seed)')
    _check_budget(c.get('attempt_wall_seconds'), c.get('whole_case_wall_seconds'), c.get('sdk_call_limit'))
    # Everything except the boundary and the three budget fields is validated by the Dev
    # validator on the equivalent Dev view, so identity, lock, scene and robot checks stay single-sourced.
    dev_view = dict(c, schema_version=DEV_SCHEMA, runner_boundary='dev_loop',
                    whole_case_wall_seconds=DEV_WHOLE_CASE_SECONDS, sdk_call_limit=SDK['sdk_calls'])
    validate_dev_config(dev_view, expected_source_root=expected_source_root, budget_boundary='formal')
    return c


def validate_native_config(c, *, expected_source_root=None):
    """The check the native worker runs on its own config: a Dev or a formal configuration."""
    if isinstance(c, dict) and c.get('schema_version') == SCHEMA:
        return validate_formal_config(c, expected_source_root=expected_source_root)
    return validate_dev_config(c, expected_source_root=expected_source_root)


RUN_BUDGET_SCHEMA = 'attentionbench.openarm365-formal-run-budget.v2'
_RUN_BUDGET_KEYS = {'schema_version', 'suite', 'formal_eligible', 'max_attempts', 'assistance_credits',
                    'token_limit', 'human_deadline_seconds', 'attempt_wall_seconds',
                    'whole_case_wall_seconds', 'approved_config_sha256s', 'approved_policy_sha256'}


def validate_formal_run_budget(value, config_sha256, policy_sha256):
    """A run budget for the formal OpenArm365 configuration, bound to its configs and policy.

    Wall limits are ceilings the freeze chooses for a run; they stay inside the envelope the
    configuration itself allows (one attempt includes the cold native start).
    """
    if not isinstance(value, dict) or set(value) != _RUN_BUDGET_KEYS:
        raise ValueError('invalid OpenArm365 formal run budget')
    if value['schema_version'] != RUN_BUDGET_SCHEMA or value['suite'] != 'robocasa365' or value['formal_eligible'] is not False:
        raise ValueError('OpenArm365 formal run budget identity mismatch')
    numbers = {k: value[k] for k in ('max_attempts', 'assistance_credits', 'token_limit', 'human_deadline_seconds',
                                     'attempt_wall_seconds', 'whole_case_wall_seconds')}
    if any(type(v) is not int for v in numbers.values()):
        raise ValueError('OpenArm365 formal run budget values must be integers')
    if (not 1 <= numbers['max_attempts'] <= 10 or not 0 <= numbers['assistance_credits'] <= 10
            or not 0 <= numbers['token_limit'] <= 8000 or numbers['human_deadline_seconds'] != 30
            or not 1 <= numbers['attempt_wall_seconds'] <= SDK['attempt_seconds']
            or not numbers['attempt_wall_seconds'] <= numbers['whole_case_wall_seconds'] <= 600):
        raise ValueError('OpenArm365 formal run budget outside the verified envelope')
    configs = value['approved_config_sha256s']
    if (not isinstance(configs, list) or not configs or len(set(configs)) != len(configs)
            or config_sha256 not in configs or value['approved_policy_sha256'] != policy_sha256):
        raise ValueError('OpenArm365 formal run budget is not bound to this config/policy')
    return value
