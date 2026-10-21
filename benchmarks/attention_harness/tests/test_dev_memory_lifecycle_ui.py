"""Dev v2 lifecycle UI wiring with an in-process fake gateway; no Memory Service process."""
import io
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_planner_memory as planner_memory
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_view import MemoryView
from benchmarks.attention_harness import ui_server


MEMORY_ID = 'c1-seeded-retrieve-disable-001'


class FakeGateway:
    def __init__(self):
        self.status = 'trusted'
        self.expires_at = None
        self.calls = []
        self.events = []
        self.grants = {}
        self.context = {'namespace': 'daily-test'}

    def store_id(self):
        return 'fixture-authority'

    def list_memories(self, *, status=None):
        return []  # The general catalogue deliberately excludes this provenance channel.

    def list_dev_v2_memories(self, *, status=None):
        note = self.get_dev_v2_memory_public(MEMORY_ID)
        return [note] if status is None or note['status'] == status else []

    def get_dev_v2_memory_public(self, memory_id):
        if memory_id != MEMORY_ID:
            raise FileNotFoundError('unknown Dev v2 memory')
        return dict(memory_id=MEMORY_ID, version=1, status=self.status,
                    source_label='seeded for tests', seeded_for_tests=True,
                    excluded_from_results=True, applicability_summary={'seed': 102, 'scene_id': 'scene-102'})

    def history(self, memory_id):
        self.get_dev_v2_memory_public(memory_id)
        return self.events

    def _change(self, action, memory_id, actor, reason, expires_at=None):
        self.get_dev_v2_memory_public(memory_id)
        self.calls.append((action, memory_id, actor, reason, expires_at))
        self.status = {'disable': 'disabled', 'enable': 'trusted',
                       'rollback': 'rolled_back', 'expiry': 'trusted'}[action]
        if action == 'expiry':
            self.expires_at = expires_at
        self.events.append(dict(status=self.status if action != 'expiry' else 'expiry_set',
                                actor=actor, reason=reason))
        return SimpleNamespace(artifact=lambda: dict(memory_id=memory_id, status=self.status,
                                                      expires_at=self.expires_at))

    def disable(self, memory_id, *, actor, reason):
        return self._change('disable', memory_id, actor, reason)

    def enable(self, memory_id, *, actor, reason):
        return self._change('enable', memory_id, actor, reason)

    def rollback(self, memory_id, *, actor, reason):
        return self._change('rollback', memory_id, actor, reason)

    def set_expiry(self, memory_id, *, expires_at, actor, reason):
        return self._change('expiry', memory_id, actor, reason, expires_at)

    def retrieve(self, context, *, now):
        if self.status != 'trusted':
            return []
        return [SimpleNamespace(memory_id=MEMORY_ID, version=1, status='trusted',
                                guidance='Use a bounded grasp', applicability=context)]

    def authorize_dev_use(self, **kw):
        grant = dict(grant_id='grant-1', memory_id=kw['memory_id'], memory_version=kw['memory_version'],
                     context=kw['context'], development_id=kw['development_id'],
                     source_policy_sha256=kw['source_policy_sha256'], guidance='Use a bounded grasp',
                     source_trace_id='seeded-for-tests:fixture', evidence_refs=[],
                     seeded_for_tests=True, excluded_from_results=True)
        self.grants['grant-1'] = grant
        return grant

    def get_dev_use(self, grant_id):
        return self.grants[grant_id]


def test_dev_directory_and_all_four_actions_are_routed_without_general_catalogue(tmp_path, monkeypatch):
    gateway = FakeGateway()
    view = MemoryView(gateway, source='service', can_operate=True)
    # Exercise the real HTTP handler in process: the sandbox forbids sockets.
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    handler_class = ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite'),
                                            port=0, memory_view=view)

    def request(path, body=None, token=''):
        handler = object.__new__(handler_class)
        handler.path = path
        payload = b'' if body is None else json.dumps(body).encode()
        handler.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
                           'X-Attention-Token': token}
        handler.rfile = io.BytesIO(payload)
        handler.wfile = io.BytesIO()
        response = {}
        handler.send_response = lambda status: response.update(status=int(status))
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        (handler.do_GET if body is None else handler.do_POST)()
        raw = handler.wfile.getvalue()
        return response['status'], json.loads(raw) if raw.startswith(b'{') else raw.decode()

    import re
    token = re.search(r'data-control-token="([^"]+)"', request('/ui/')[1]).group(1)

    def get(path):
        status, value = request(path)
        assert status == 200
        return value

    def post(action, body, memory_id=MEMORY_ID):
        return request(f'/api/dev-memories/{memory_id}/{action}', body, token)

    assert get('/api/memories')['memories'] == []
    assert get('/api/dev-memories')['memories'][0]['seeded_for_tests'] is True
    assert 'guidance' not in json.dumps(get('/api/dev-memories'))
    assert request(f'/api/dev-memories/{MEMORY_ID}/disable',
                   {'actor': 'a', 'reason': 'x'})[0] == 403
    for action, body, expected in [
        ('disable', {'actor': 'alice', 'reason': 'test exclusion'}, 'disabled'),
        ('enable', {'actor': 'alice', 'reason': 'repeat test'}, 'trusted'),
        ('expiry', {'actor': 'bob', 'reason': 'scheduled end', 'expires_at': 4102444800}, 'trusted'),
        ('rollback', {'actor': 'carol', 'reason': 'close test'}, 'rolled_back'),
    ]:
        status, reply = post(action, body)
        assert status == 200 and reply['memory']['status'] == expected
        assert get('/api/dev-memories')['memories'][0]['status'] == expected
    assert [call[0] for call in gateway.calls] == ['disable', 'enable', 'expiry', 'rollback']
    detail = get(f'/api/dev-memories/{MEMORY_ID}')
    assert detail['memory']['status'] == 'rolled_back'
    assert [event['actor'] for event in detail['history']] == ['alice', 'alice', 'bob', 'carol']
    for body in ({'actor': '', 'reason': 'x'}, {'actor': 'a', 'reason': '  '}):
        assert post('disable', body)[0] == 400
    assert post('expiry', {'actor': 'a', 'reason': 'x', 'expires_at': 1})[0] == 400
    assert post('disable', {'actor': 'a', 'reason': 'x'}, memory_id='outside-allowlist')[0] == 404
    assert len(gateway.calls) == 4


def test_no_operator_authority_rejects_dev_action():
    view = MemoryView(FakeGateway(), source='service', can_operate=False)
    assert view.public()['can_operate'] is False
    with pytest.raises(PermissionError, match='operator authority'):
        view.operate(MEMORY_ID, 'disable', actor='a', reason='b', dev_v2=True)
    local = MemoryView(FakeGateway(), source='local', can_operate=True)
    assert local.dev_control()['can_operate'] is False
    with pytest.raises(PermissionError, match='operator authority'):
        local.operate(MEMORY_ID, 'disable', actor='a', reason='b', dev_v2=True)


def test_disabled_note_is_absent_from_next_retrieval_and_stops_frozen_version(tmp_path, monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(planner_memory, 'context', lambda config, namespace: gateway.context)
    monkeypatch.setattr(planner_memory, 'client_for', lambda settings: gateway)
    settings = {'store_id': gateway.store_id(), 'namespace': 'daily-test'}
    service = planner_memory.DevMemory(gateway, settings, {})
    packet = service.packet(development_id='cell:v1', source='source')
    assert [entry['memory_id'] for entry in packet['entries']] == [MEMORY_ID]
    service.recheck(packet)
    gateway.disable(MEMORY_ID, actor='alice', reason='test exclusion')
    assert service.packet(development_id='cell:v2', source='source')['entries'] == []
    with pytest.raises(PermissionError, match='disabled/expired/rolled back'):
        service.recheck(packet)

    class FakeStore:
        events = []
        def start_cell_clock(self, cell):
            pass
        def tx(self):
            return nullcontext(None)
        def event(self, db, kind, payload):
            self.events.append((kind, payload))

    store = FakeStore()
    (tmp_path/'memory-input.json').write_text(json.dumps(packet))
    with pytest.raises(PermissionError, match='disabled/expired/rolled back'):
        planner_memory.load_memory(store, 'cell', 1, 'source', {}, settings, tmp_path)
    assert store.events[-1][0] == 'planner_memory_query_finished'
    assert store.events[-1][1]['status'] == 'failed'
    assert store.events[-1][1]['error'] == 'PermissionError'
