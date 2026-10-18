import pytest
import hashlib

from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2, DEFAULT_CELL
from benchmarks.attention_harness.dev_experiment import validate_help_budget
from benchmarks.attention_harness.dev_ledger import DevV2Store, task_goal_limits, task_goal_wall_limit
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from openarm365_dev_v2.diag_budget import task_limits


def test_run_configuration_options_read_scene_contract_and_dev_limits(tmp_path):
    options = DailyDevV2(tmp_path / 'daily', [], per_run_budget=True).options()
    assert options['registered_scene_seeds'] == [101, 102, 103]
    assert options['tasks'][0]['seeds'] == list(range(101, 126))
    assert options['default_max_iterations'] == 5
    assert DEFAULT_CELL['dev_calls'] == DEFAULT_CELL['executions'] == 5
    assert DEFAULT_CELL['advisor_calls'] == DEFAULT_CELL['help_count'] == DEFAULT_CELL['help_credits'] == 5
    assert DEFAULT_CELL['sdk_calls'] == 5 * task_limits()['sdk_calls']
    assert DEFAULT_CELL['control_steps'] == 5 * task_limits()['control_steps']
    assert DEFAULT_CELL['model_seconds'] == 1800
    profile = options['profiles'][0]
    assert profile['sdk_call_limit'] == task_limits()['sdk_calls']
    assert profile['control_step_limit'] == task_limits()['control_steps']
    assert profile['attempt_wall_seconds'] == task_limits()['attempt_seconds']
    assert profile['wall_seconds'] == 21600
    assert profile['attempt_wall_seconds'] == 1920


def test_daily_comparison_rejects_unregistered_seed_before_ledger_creation(tmp_path):
    bridge = DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    with pytest.raises(ValueError, match='scene contract has no reference for open_cabinet seed 114'):
        bridge.create({'start': False, 'selection': {
            'mode': 'experiment', 'tasks': ['open_cabinet'], 'seeds': [114],
            'request_key': 'seed-gate-check',
        }})
    assert not list((tmp_path / 'daily').glob('devexp-*'))


def test_existing_goal_bridge_offers_same_scene_and_version_defaults(tmp_path):
    key = 'run-configuration-test'
    goal_id = hashlib.sha256(('goal|' + key).encode()).hexdigest()[:32]
    ledger = tmp_path / 'goal.sqlite3'
    DevV2Store(ledger, configuration={'goal_id': goal_id, 'limits': task_goal_limits(), 'wall_seconds': task_goal_wall_limit()})
    options = DevMainBridge(ledger, tmp_path / 'artifacts', key).options()
    assert options['registered_scene_seeds'] == [101, 102, 103]
    assert options['tasks'][0]['seeds'] == list(range(101, 126))
    assert options['default_max_iterations'] == 5
    assert options['default_cell_budget']['dev_calls'] == 5
    assert options['default_cell_budget']['advisor_calls'] == 5
    assert options['default_cell_budget']['eval_calls'] == 5
    assert options['default_no_eval_cell_budget']['eval_calls'] == 0
    assert options['default_cell_budget']['provider_tokens'] - options['default_no_eval_cell_budget']['provider_tokens'] == 5 * 75056
    assert options['default_cell_budget']['model_seconds'] - options['default_no_eval_cell_budget']['model_seconds'] == 5 * 120
    assert options['default_cell_budget']['help_wait_seconds'] == 5 * 120 + 5 * 300
    assert options['default_cell_budget']['model_seconds'] == 1800
    assert options['profiles'][0]['wall_seconds'] == 21600


@pytest.mark.parametrize('count', [1, 5, 10])
def test_help_budget_accepts_visible_range(count):
    assert validate_help_budget(count) == count


@pytest.mark.parametrize('count', [-1, 11, 1.5, True])
def test_help_budget_preserves_bounded_integer_validation(count):
    with pytest.raises(ValueError, match='help budget must be 0–10'):
        validate_help_budget(count)
