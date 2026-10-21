"""Offline tests for the operator's seed-102 reseed command."""

import copy
import io
import json
from pathlib import Path

import pytest

from scripts import reseed_test_notes as command


NOTES = json.loads((command.HERE / 'test_notes_seed102.json').read_text())


def context(digest='a' * 64):
    return {'sdk_sha256': digest, 'namespace': 'attentionbench-dev-v2-c',
            'task_id': 'pick_place_counter_to_sink', 'seed': 102}


class FakeMemory:
    """Injected transport enforces the same role split as the HTTP routes."""

    def __init__(self):
        self.rows = {}
        self.writes = []

    def request(self, method, path, body=None, *, role='online'):
        if (method, path) == ('GET', '/dev-v2/memories'):
            assert role == 'online'
            return {'memories': list(self.rows.values())}
        if path.endswith('/disable'):
            assert method == 'POST' and role == 'operator'
            assert body == {'actor': command.ACTOR, 'reason': command.OFF_REASON}
            note_id = path.split('/')[2]
            assert self.rows[note_id]['seeded_for_tests'] is True
            self.rows[note_id]['status'] = 'disabled'
            self.writes.append(('disable', note_id))
            return {}
        assert (method, path, role) == ('POST', '/dev-v2/test-seeded-notes', 'importer')
        assert body['actor'] == command.ACTOR and body['reason'] == command.SEED_REASON
        note_id = body['memory_id']
        assert note_id not in self.rows
        self.rows[note_id] = {'memory_id': note_id, 'status': 'trusted',
                              'seeded_for_tests': True,
                              'title': body['title'],
                              'applicability_summary': copy.deepcopy(body['context'])}
        self.writes.append(('seed', note_id))
        return {}


def test_first_seed_and_idempotent_second_run():
    gateway = FakeMemory()
    lines = []
    assert command.reseed(gateway, context(), NOTES, out=lines.append) == 0
    assert len(gateway.writes) == 5
    assert {row['memory_id'] for row in gateway.rows.values()} == {
        't-aaaaaaaa-' + note['key'] for note in NOTES}
    assert 'Seeded 5 notes' in lines
    lines.clear()
    assert command.reseed(gateway, context(), NOTES, out=lines.append) == 0
    assert len(gateway.writes) == 5
    assert lines[-1] == 'Nothing to do'


def test_hand_seeded_current_title_is_kept_without_duplicate():
    gateway = FakeMemory()
    title = NOTES[0]['title']
    gateway.rows['hand-seeded'] = {'memory_id': 'hand-seeded', 'status': 'active',
                                   'seeded_for_tests': True, 'title': f'  {title.upper()}  ',
                                   'applicability': context()}
    lines = []
    assert command.reseed(gateway, context(), NOTES, out=lines.append) == 0
    assert ('seed', 't-aaaaaaaa-small-steps') not in gateway.writes
    assert len([kind for kind, _ in gateway.writes if kind == 'seed']) == 4
    assert f'kept existing: {title}' in lines
    before = list(gateway.writes)
    assert command.reseed(gateway, context(), NOTES, check=True, out=lines.append) == 0
    assert command.reseed(gateway, context(), NOTES, out=lines.append) == 0
    assert gateway.writes == before
    assert lines[-1] == 'Nothing to do'


def test_hand_seeded_different_title_does_not_satisfy_definition():
    gateway = FakeMemory()
    gateway.rows['hand-seeded'] = {'memory_id': 'hand-seeded', 'status': 'active',
                                   'seeded_for_tests': True, 'title': 'A different note',
                                   'applicability': context()}
    gateway.rows['real-note'] = {'memory_id': 'real-note', 'status': 'active',
                                 'seeded_for_tests': False, 'title': NOTES[0]['title'],
                                 'applicability': context()}
    assert command.reseed(gateway, context(), NOTES, out=lambda _: None) == 0
    assert ('seed', 't-aaaaaaaa-small-steps') in gateway.writes
    assert gateway.rows['hand-seeded']['status'] == 'active'
    assert gateway.rows['real-note']['status'] == 'active'


def test_hand_seeded_old_identity_is_disabled_and_replaced():
    gateway = FakeMemory()
    gateway.rows['hand-seeded'] = {'memory_id': 'hand-seeded', 'status': 'trusted',
                                   'seeded_for_tests': True, 'title': NOTES[0]['title'],
                                   'applicability': context('b' * 64)}
    lines = []
    assert command.reseed(gateway, context(), NOTES, out=lines.append) == 0
    assert ('disable', 'hand-seeded') in gateway.writes
    assert ('seed', 't-aaaaaaaa-small-steps') in gateway.writes
    assert gateway.rows['hand-seeded']['status'] == 'disabled'
    assert command.reseed(gateway, context(), NOTES, check=True, out=lambda _: None) == 0


def test_identity_change_disables_only_old_test_notes():
    gateway = FakeMemory()
    command.reseed(gateway, context(), NOTES, out=lambda _: None)
    gateway.rows['real-1'] = {'memory_id': 'real-1', 'status': 'trusted',
                              'seeded_for_tests': False,
                              'applicability_summary': {'sdk_sha256': 'a' * 64,
                                                        'namespace': context()['namespace']}}
    gateway.rows['other-namespace'] = {'memory_id': 'other-namespace', 'status': 'trusted',
                                       'seeded_for_tests': True,
                                       'applicability_summary': {'sdk_sha256': 'a' * 64,
                                                                 'namespace': 'other'}}
    lines = []
    assert command.reseed(gateway, context('b' * 64), NOTES, out=lines.append) == 0
    assert [kind for kind, _ in gateway.writes].count('disable') == 5
    assert [kind for kind, _ in gateway.writes].count('seed') == 10
    assert gateway.rows['real-1']['status'] == 'trusted'
    assert gateway.rows['other-namespace']['status'] == 'trusted'
    assert all(gateway.rows['t-bbbbbbbb-' + note['key']]['status'] == 'trusted' for note in NOTES)
    assert 'Turned off 5 old test notes' in lines
    assert 'Seeded 5 notes' in lines


def test_check_exit_codes_and_dry_run_are_read_only():
    gateway = FakeMemory()
    assert command.reseed(gateway, context(), NOTES, check=True, out=lambda _: None) == 1
    assert command.reseed(gateway, context(), NOTES, dry_run=True, out=lambda _: None) == 0
    assert gateway.writes == []
    command.reseed(gateway, context(), NOTES, out=lambda _: None)
    before = copy.deepcopy(gateway.rows)
    assert command.reseed(gateway, context(), NOTES, check=True, out=lambda _: None) == 0
    assert command.reseed(gateway, context('b' * 64), NOTES, check=True, out=lambda _: None) == 1
    assert command.reseed(gateway, context('b' * 64), NOTES, dry_run=True, out=lambda _: None) == 0
    assert gateway.rows == before


def test_occupied_current_id_fails_closed():
    gateway = FakeMemory()
    gateway.rows['t-aaaaaaaa-small-steps'] = {'memory_id': 't-aaaaaaaa-small-steps',
                                               'status': 'disabled', 'seeded_for_tests': True,
                                               'applicability_summary': {'sdk_sha256': 'a' * 64,
                                                                         'namespace': context()['namespace']}}
    with pytest.raises(ValueError, match='occupied or inactive'):
        command.reseed(gateway, context(), NOTES, out=lambda _: None)
    assert gateway.writes == []


def test_current_id_with_wrong_applicability_fails_closed():
    gateway = FakeMemory()
    command.reseed(gateway, context(), NOTES, out=lambda _: None)
    gateway.rows['t-aaaaaaaa-small-steps']['applicability_summary']['seed'] = 103
    before = list(gateway.writes)
    with pytest.raises(ValueError, match='occupied or inactive'):
        command.reseed(gateway, context(), NOTES, out=lambda _: None)
    assert gateway.writes == before


def test_missing_keys_file_and_no_key_in_output(tmp_path, capsys, monkeypatch):
    with pytest.raises(FileNotFoundError, match='Keys file not found'):
        command.keys(tmp_path / 'missing.env')
    key_values = ('online-secret-123', 'operator-secret-456', 'importer-secret-789')
    key_file = tmp_path / 'keys.env'
    key_file.write_text('\n'.join(f'{name}={value}' for name, value in zip(
        ('ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_OPERATOR_KEY',
         'ATTENTION_MEMORY_IMPORTER_KEY'), key_values)))
    credentials = command.keys(key_file)
    gateway = FakeMemory()
    command.reseed(gateway, context(), NOTES)
    captured = capsys.readouterr()
    assert all(key not in captured.out + captured.err for key in key_values)
    monkeypatch.setattr(command, 'service_settings', lambda: (tmp_path, context()['namespace']))
    monkeypatch.setattr(command, 'current_context', lambda root, namespace: context())
    monkeypatch.setattr(command, 'MemoryHTTP', lambda url, keys: gateway)
    assert command.main(['--check', '--keys-file', str(tmp_path / 'missing.env')]) == 2
    assert 'Keys file not found' in capsys.readouterr().err
    assert command.main(['--check', '--keys-file', str(key_file)]) == 0
    captured = capsys.readouterr()
    assert all(key not in captured.out + captured.err for key in key_values)
    assert set(credentials.values()) == set(key_values)


def test_service_error_does_not_expose_response_or_credentials(monkeypatch):
    from urllib.error import HTTPError
    credentials = {'ATTENTION_MEMORY_API_KEY': 'online-secret',
                   'ATTENTION_MEMORY_OPERATOR_KEY': 'operator-secret',
                   'ATTENTION_MEMORY_IMPORTER_KEY': 'importer-secret'}

    def reject(request, timeout):
        raise HTTPError(request.full_url, 403, 'operator-secret', {},
                        io.BytesIO(b'online-secret importer-secret'))

    monkeypatch.setattr(command, 'urlopen', reject)
    client = command.MemoryHTTP('http://127.0.0.1:8878', credentials)
    with pytest.raises(RuntimeError) as error:
        client.request('GET', '/dev-v2/memories')
    assert all(secret not in str(error.value) for secret in credentials.values())


def test_http_headers_are_role_specific(monkeypatch):
    requests = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.close()

    def fake_urlopen(request, timeout):
        requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        return Response(b'{}')

    monkeypatch.setattr(command, 'urlopen', fake_urlopen)
    client = command.MemoryHTTP('http://127.0.0.1:8878', {
        'ATTENTION_MEMORY_API_KEY': 'online-secret',
        'ATTENTION_MEMORY_OPERATOR_KEY': 'operator-secret',
        'ATTENTION_MEMORY_IMPORTER_KEY': 'importer-secret'})
    client.request('GET', '/dev-v2/memories')
    client.request('POST', '/memories/old/disable', {}, role='operator')
    client.request('POST', '/dev-v2/test-seeded-notes', {}, role='importer')
    headers = [item[2] for item in requests]
    assert all(row['X-memory-service-key'] == 'online-secret' for row in headers)
    assert 'X-memory-importer-key' not in headers[0] and 'X-memory-operator-key' not in headers[0]
    assert headers[1]['X-memory-operator-key'] == 'operator-secret'
    assert 'X-memory-importer-key' not in headers[1]
    assert headers[2]['X-memory-importer-key'] == 'importer-secret'
    assert 'X-memory-operator-key' not in headers[2]


def test_current_identity_is_deterministic_against_configured_checkout():
    root, namespace = command.service_settings()
    if not root.is_dir():
        pytest.skip('configured experiments root unavailable')
    first = command.current_context(root, namespace)
    second = command.current_context(root, namespace)
    assert first == second
    assert len(first['sdk_sha256']) == 64
