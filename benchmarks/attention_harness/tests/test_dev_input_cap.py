import pytest

from benchmarks.attention_harness.dev_configuration import MAX_INPUT_BYTES, derive_cell_budget, normalize_configuration


def test_default_dev_input_cap_is_150000_and_larger_is_rejected():
    assert MAX_INPUT_BYTES == 150000
    assert normalize_configuration({})['max_input_bytes_per_request'] == 150000
    assert normalize_configuration({'budget_tier': 'dev', 'model_source': 'codex'})['max_input_bytes_per_request'] == 150000
    assert normalize_configuration({'max_input_bytes_per_request': 30000})['max_input_bytes_per_request'] == 30000
    with pytest.raises(ValueError, match='public input contract exceeded'):
        normalize_configuration({'max_input_bytes_per_request': 150001})


def test_cell_token_budget_scales_with_the_cap():
    small = derive_cell_budget(normalize_configuration({'max_input_bytes_per_request': 30000}))
    large = derive_cell_budget(normalize_configuration({}))
    dev_calls = large['dev_calls']
    assert large['input_tokens'] - small['input_tokens'] >= dev_calls * (150000 - 30000)
    assert large['provider_tokens'] - small['provider_tokens'] >= dev_calls * (150000 - 30000)
