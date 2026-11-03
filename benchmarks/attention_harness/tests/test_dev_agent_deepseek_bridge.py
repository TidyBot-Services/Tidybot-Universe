"""DeepSeek through the bridge: thinking is switched off and disclosed, truncation is an error, and the request
shapes real Codex sends (tests/fixtures/codex_0_165_dev_requests.json) pass a strict DeepSeek stand-in."""
import json
import secrets
import urllib.error
from pathlib import Path
import pytest
from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge_module
from benchmarks.attention_harness import dev_agent_provider as providers
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_parcc_bridge import ChatBridge, to_chat
from benchmarks.attention_harness.dev_agent_provider import presets, probe
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer, exchange
from benchmarks.attention_harness.tests.fakes.deepseek_strict import StrictDeepSeek, validate

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'codex_0_165_dev_requests.json').read_text())
REQUESTS = FIXTURE['requests']


@pytest.fixture
def memory_bridge(monkeypatch):
    monkeypatch.setattr(bridge_module.http.server, 'ThreadingHTTPServer', MemoryServer)


def post(bridge, body):
    return exchange(bridge.server, 'POST', '/v1/responses', body, {'Authorization': 'Bearer ' + bridge.token})


def new_bridge(tmp_path, upstream, spec=None):
    return ChatBridge(tmp_path, spec or presets()['deepseek'], Secret('sk-' + secrets.token_hex(16)), token=secrets.token_urlsafe(24), upstream=upstream)


def test_what_real_codex_sends_is_plain_enough():
    """Pins the facts the bridge relies on; a Codex upgrade that changes them should fail here first."""
    first = REQUESTS[0]
    assert {t['type'] for t in first['tools']} == {'function'}
    assert first['tool_choice'] == 'auto' and first['stream'] is True and first['store'] is False
    kinds = {item.get('type', 'message') for request in REQUESTS for item in request['input']}
    assert kinds == {'message', 'reasoning', 'function_call', 'function_call_output'}
    reasoning = [i for r in REQUESTS for i in r['input'] if i['type'] == 'reasoning']
    assert reasoning and all(i['summary'] and i['encrypted_content'] is None for i in reasoning)  # replayed, as text only


def test_every_captured_request_converts_to_a_chat_body_a_strict_deepseek_accepts():
    spec = presets()['deepseek']
    for request in REQUESTS:
        chat = to_chat(request, spec)
        assert validate(chat) is False  # thinking is off, so nothing else is demanded


def test_the_whole_codex_sequence_runs_through_the_bridge_against_a_strict_deepseek(tmp_path, memory_bridge):
    upstream = StrictDeepSeek(vision_models=())
    bridge = new_bridge(tmp_path, upstream)
    try:
        for number, request in enumerate(REQUESTS, 1):
            status, body, _ = post(bridge, request)
            assert status == 200 and b'response.failed' not in body and b'response.completed' in body, (number, body[:300])
    finally:
        bridge.close()
    assert len(upstream.bodies) == len(REQUESTS)


def test_without_the_switch_a_thinking_deepseek_refuses_the_second_turn():
    """The failure this change prevents: reasoning items are dropped by the bridge, thinking is on by default."""
    from dataclasses import replace
    thinking_on = replace(presets()['deepseek'], extra_body={})
    with pytest.raises(urllib.error.HTTPError) as caught:
        validate(to_chat(REQUESTS[1], thinking_on))
    assert b'reasoning_content' in caught.value.read()


@pytest.mark.parametrize('provider,expected', [('deepseek', {'type': 'disabled'}), ('openai', None), ('parcc', None)])
def test_the_thinking_switch_belongs_to_the_deepseek_preset_only(provider, expected):
    spec = presets()[provider]
    assert spec.extra_body.get('thinking') == expected
    assert to_chat(REQUESTS[0], spec).get('thinking') == expected
    assert (spec.thinking == 'off') is (expected is not None)


class Reply:
    headers = {}

    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def read(self, *args): return self.data
    def __iter__(self): return iter(self.data.splitlines(True))


def test_the_direct_clients_send_the_switch_too(monkeypatch):
    seen = []

    def transport(request, **kwargs):
        seen.append(json.loads(request.data) if request.data else None)
        if request.data is None: return Reply(b'{"data":[]}')
        if seen[-1].get('stream'):
            chunk = dict(choices=[dict(delta=dict(tool_calls=[dict(function=dict(name='ping', arguments='{}'))]))])
            return Reply(b'data: ' + json.dumps(chunk).encode() + b'\n\ndata: [DONE]\n\n')
        return Reply(json.dumps(dict(model='m', choices=[dict(message=dict(content='hi'), finish_reason='stop')])).encode())
    key = Secret('sk-' + secrets.token_hex(16))
    probe(presets()['deepseek'], key, 'deepseek-flash', role='dev', transport=transport)
    monkeypatch.setattr(providers, 'open_request', transport)
    provider = type('P', (), dict(spec=presets()['deepseek'], secret=key))()
    ByokChatClient(provider).chat(model='deepseek-flash', messages=[dict(role='user', content='hi')], max_tokens=8)
    assert [b['thinking'] for b in seen if b] == [{'type': 'disabled'}, {'type': 'disabled'}]
    seen.clear()
    probe(presets()['openai'], key, 'm', role='dev', transport=transport)
    assert all('thinking' not in b for b in seen if b)


def test_a_reply_cut_off_at_the_token_limit_is_an_error_not_a_completion(tmp_path, memory_bridge):
    upstream = StrictDeepSeek(finish='length', partial_tool=('exec_command', '{"cmd": "echo hel'))
    bridge = new_bridge(tmp_path, upstream)
    try:
        status, body, _ = post(bridge, REQUESTS[0])
    finally:
        bridge.close()
    assert b'response.failed' in body and b'response.completed' not in body
    assert b'output token limit' in body and b'truncated' in body
    row = json.loads((tmp_path / 'calls.jsonl').read_text().splitlines()[-1])
    assert row['truncated'] is True and row['finish_reason'] == 'length' and row['status'] == 'truncated'


def test_a_normal_stop_is_still_a_completion(tmp_path, memory_bridge):
    bridge = new_bridge(tmp_path, StrictDeepSeek())
    try:
        status, body, _ = post(bridge, REQUESTS[0])
    finally:
        bridge.close()
    assert b'response.completed' in body and b'response.failed' not in body
    row = json.loads((tmp_path / 'calls.jsonl').read_text().splitlines()[-1])
    assert row['status'] == 200 and not row.get('truncated')


def test_thinking_off_is_disclosed_in_the_manifest_block_never_silent():
    from benchmarks.attention_harness.dev_agent_config import provider_roles
    role = lambda provider, **extra: dict(roles=dict(dev=dict(credential='mine', provider=provider, model='m', **extra)))
    assert provider_roles(role('deepseek'))['roles']['dev']['thinking'] == 'off'
    assert provider_roles(role('deepseek', thinking='off'))['roles']['dev']['thinking'] == 'off'  # a stored block round-trips
    assert 'thinking' not in provider_roles(role('openai'))['roles']['dev']
    with pytest.raises(ValueError, match='invalid_agent_provider_thinking'):
        provider_roles(role('deepseek', thinking='on'))
    with pytest.raises(ValueError, match='invalid_agent_provider_thinking'):
        provider_roles(role('openai', thinking='off'))


def test_run_keys_public_block_and_preset_listing_say_thinking_off():
    from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault
    vault = ProviderKeyVault()
    vault.put('o', 'mine', 'deepseek', Secret('sk-' + secrets.token_hex(16)))
    vault.put('o', 'other', 'openai', Secret('sk-' + secrets.token_hex(16)))
    keys = vault.take_for_run('o', dict(dev=dict(credential='mine', provider='deepseek', model='m'),
                                        eval=dict(credential='other', provider='openai', model='m')))
    assert keys.roles['dev']['thinking'] == 'off' and 'thinking' not in keys.roles['eval']
