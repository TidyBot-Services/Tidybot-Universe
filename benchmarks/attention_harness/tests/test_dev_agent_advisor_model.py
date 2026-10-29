"""AdvisorProxy model on agent runs (10-29): configurable there only."""
import pytest

from benchmarks.attention_harness import codex_client
from benchmarks.attention_harness.codex_client import CodexClient, ParccError
from benchmarks.attention_harness.dev_agent_config import configuration, normalize
from benchmarks.attention_harness.dev_agent_loop import advisor_client
from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION


def test_agent_advisor_model_is_frozen_into_the_config():
    selection = dict(SELECTION, advisor_proxy={'enabled': True}, advisor_model='gpt-6.1-sol')
    assert normalize(selection)['advisor_model'] == 'gpt-6.1-sol'
    proxy = configuration(selection)['advisor_proxy']
    assert proxy['model'] == 'gpt-6.1-sol' and proxy['reasoning_effort'] == 'high' and proxy['prompt_version'] == 'advisor-vision-v3'


def test_selection_without_advisor_model_keeps_the_fixed_advisor():
    selection = dict(SELECTION, advisor_proxy={'enabled': True})
    assert 'advisor_model' not in normalize(selection)
    assert configuration(selection)['advisor_proxy']['model'] == 'gpt-6-astra'


def test_unknown_agent_advisor_model_refused():
    with pytest.raises(ValueError):normalize(dict(SELECTION, advisor_model='gpt-6-sol'))


def test_one_call_client_still_refuses_other_models():
    with pytest.raises(ParccError, match='model'):
        CodexClient().chat(model='gpt-6.1-sol', messages=[], max_tokens=1)
    client = advisor_client(None, {'model_source': 'codex'})
    assert client.models == {'gpt-6-astra', 'gpt-6.1-sol'} and client.timeout_seconds == 90
    assert codex_client.MODEL == 'gpt-6-astra'


def test_advisor_normalize_accepts_agent_model_only_for_agent_configs():
    from benchmarks.attention_harness.dev_advisor_proxy import default_config, normalize as normalize_advisor
    sol = dict(default_config(True, 'codex'), model='gpt-6.1-sol')
    assert normalize_advisor(sol, 'codex', agent=True)['model'] == 'gpt-6.1-sol'
    with pytest.raises(ValueError):normalize_advisor(sol, 'codex')
    with pytest.raises(ValueError):normalize_advisor(dict(sol, model='gpt-6-sol'), 'codex', agent=True)
    with pytest.raises(ValueError):normalize_advisor(dict(sol, prompt_version='other'), 'codex', agent=True)


def test_created_agent_config_with_sol_advisor_passes_run_time_validation(tmp_path):
    """The 10-29 sol2 launch died in validate_dev_config; exercise create() and verify_manifest() together."""
    import json
    from benchmarks.attention_harness import dev_experiment
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from pathlib import Path
    main = Path(__file__).resolve().parents[3]
    contract = main/'openarm365_dev_v2/scene_contracts/dev-seeds-101-103-v2-20261019-kp1500.json'
    import hashlib
    selection = dict(dev_mode='agent', dev_model='gpt-6.1-sol', eval_model='gpt-6.1-sol', advisor_model='gpt-6.1-sol',
                     quota_stop_percent=70, tasks=['pick_place_counter_to_sink'], seeds=[102], policies=['reactive_help'],
                     mode='experiment', budget_tier='dev', model_source='codex', help_budget=10, max_versions=10,
                     wall_limit=21600, advisor_proxy={'enabled': True}, goal_key='advisor-sol-test',
                     request_key='advisor-sol-test', reproducible_reset=True,
                     scene_contract=dict(sha256=hashlib.sha256(contract.read_bytes()).hexdigest(), uri=str(contract)))
    store = DevV2Store(tmp_path/'ledger.sqlite3')
    exp = dev_experiment.create(store, tmp_path/'artifacts', selection)
    manifest = dev_experiment.verify_manifest(dev_experiment.snapshot(store, exp['experiment_id']))
    proxy = next(iter(manifest['configs'].values()))['advisor_proxy']
    assert proxy['model'] == 'gpt-6.1-sol' and proxy['prompt_version'] == 'advisor-vision-v3'
