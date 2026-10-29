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
