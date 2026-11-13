"""Pin the Dev Memory gateway timeout without contacting a service."""
from unittest.mock import Mock

from benchmarks.attention_harness import dev_planner_memory, memory_service_client


def test_client_for_uses_180_second_timeout(monkeypatch):
    constructor = Mock()
    monkeypatch.setenv('ATTENTION_MEMORY_API_KEY', 'test-reader-key')
    monkeypatch.setattr(memory_service_client, 'MemoryServiceClient', constructor)

    client = dev_planner_memory.client_for({'base_url': 'http://memory.invalid'})

    constructor.assert_called_once_with(
        'http://memory.invalid', api_key='test-reader-key', timeout=180)
    assert client is constructor.return_value


def test_operator_memory_view_uses_180_second_timeout(monkeypatch):
    import attention_memory_service
    from benchmarks.attention_harness.memory_view import MemoryView
    constructor=Mock()
    monkeypatch.setattr(attention_memory_service,'MemoryServiceClient',constructor)
    MemoryView.service('http://memory.invalid',api_key='read',operator_key='operator')
    constructor.assert_called_once_with('http://memory.invalid',api_key='read',
                                       operator_key='operator',timeout=180)
