"""Contract with the service package actually constructed by MemoryView."""
import ast
import inspect
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from attention_memory_service import MemoryServiceClient, memory_service_client
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.memory_view import MemoryView


def test_service_client_implements_every_automatic_checks_gateway_call():
    tree = ast.parse(inspect.getsource(automatic_checks))
    methods = {node.func.attr for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == 'gateway'}
    assert methods, 'No gateway calls found; update the contract extractor'
    assert not {name for name in methods if not callable(getattr(MemoryServiceClient, name, None))}


def gateway():
    return SimpleNamespace(family_list=lambda: dict(fixtures=[], candidates=[], events=[]),
                           cross_seed_queue=lambda: [], cross_seed_status=lambda **kw: {},
                           validation_queue=lambda: [], memory_events=lambda **kw: [])


@pytest.mark.parametrize('method', ['family_list', 'cross_seed_queue', 'cross_seed_status'])
@pytest.mark.parametrize('noncallable', [False, True])
def test_missing_gateway_method_is_visible_even_with_empty_queue(tmp_path, method, noncallable):
    client = gateway()
    if noncallable:
        setattr(client, method, None)
    else:
        delattr(client, method)
    label = 'Family' if method == 'family_list' else 'Cross-seed'
    assert automatic_checks(tmp_path, client)['errors'] == [
        f'{label} feed unavailable: memory gateway has no {method}']


def test_all_missing_feeds_report_errors(tmp_path):
    client = SimpleNamespace(validation_queue=lambda: [], memory_events=lambda **kw: [])
    assert automatic_checks(tmp_path, client)['errors'] == [
        'Family feed unavailable: memory gateway has no family_list',
        'Cross-seed feed unavailable: memory gateway has no cross_seed_queue',
        'Cross-seed feed unavailable: memory gateway has no cross_seed_status',
    ]


@pytest.mark.parametrize('method', ['family_list', 'cross_seed_queue', 'cross_seed_status'])
def test_feed_exceptions_remain_visible(tmp_path, method):
    client = gateway()
    client.cross_seed_queue = lambda: [{'memory_id': 'm1'}]

    def unavailable(**kwargs):
        raise RuntimeError('feed offline')

    setattr(client, method, unavailable)
    prefix = 'Family feed' if method == 'family_list' else 'Automatic checks service feed'
    assert automatic_checks(tmp_path, client)['errors'] == [prefix + ' unavailable: feed offline']


def test_absent_gateway_and_successful_empty_feeds_are_not_errors(tmp_path):
    assert automatic_checks(tmp_path)['errors'] == []
    value = automatic_checks(tmp_path, gateway())
    assert value['errors'] == []
    assert value['family'] == dict(fixtures=[], candidates=[], events=[])
    assert value['cross_seed_notes'] == []


def test_memory_view_service_client_projects_real_response_shapes(tmp_path, monkeypatch):
    # Shared with the service transport regression; no live 8878 or credentials.
    fixture = Path(memory_service_client.__file__).resolve().parent.parent / 'tests/fixtures/monitor_feeds.json'
    feeds = json.loads(fixture.read_text())
    calls = []

    def transport(request, *, timeout):
        assert request.method == 'GET' and request.data is None
        assert dict(request.header_items()) == {
            'X-memory-service-key': 'synthetic-read-key', 'Content-type': 'application/json',
        }
        url = urlsplit(request.full_url)
        calls.append(url.path)
        if url.path == '/dev-v2/cross-seed/status':
            assert parse_qs(url.query) == {'memory_id': [feeds['cross_seed_status']['memory_id']]}
            value = feeds['cross_seed_status']
        else:
            value = {'/dev-v2/family': feeds['family'],
                     '/dev-v2/cross-seed/queue': feeds['cross_seed_queue'],
                     '/dev-v2/validation-queue': {'items': []},
                     '/dev-v2/memory-events': {'events': []}}[url.path]
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(memory_service_client, 'urlopen', transport)
    view = MemoryView.service('http://localhost:8878', api_key='synthetic-read-key')
    assert type(view.gateway) is MemoryServiceClient
    value = automatic_checks(tmp_path, view.gateway)
    assert value['errors'] == []
    assert value['family'] == feeds['family']
    assert all(value['family'][key] for key in ('fixtures', 'candidates', 'events'))
    assert value['cross_seed_notes'] == [dict(feeds['cross_seed_status'], title='Exact cup')]
    assert calls == ['/dev-v2/family', '/dev-v2/validation-queue', '/dev-v2/memory-events',
                     '/dev-v2/cross-seed/queue', '/dev-v2/cross-seed/status']
