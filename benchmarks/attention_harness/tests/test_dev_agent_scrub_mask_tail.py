"""Providers answer a rejected key with a masked echo of it ("****1226"); scrub must remove that fragment too.

The DeepSeek body below is the form observed from the real API with a throwaway key (only the tail differs).
"""
import io
import json
import secrets
import urllib.error
import pytest
from benchmarks.attention_harness import dev_agent_byok_client as client_module
from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge_module
from benchmarks.attention_harness import dev_agent_provider as providers
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_parcc_bridge import ChatBridge
from benchmarks.attention_harness.dev_agent_provider import list_models, presets, probe
from benchmarks.attention_harness.dev_agent_secret import Secret, scrub
from benchmarks.attention_harness.parcc_client import ParccError
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer, exchange

REQUEST_ID = 'c882cde8-aacf-412f-967d-0084eadb4c89'


def deepseek_body(tail):
    return ('{"error":{"message":"Authentication Fails, Your api key: ****%s is invalid (request_id: %s)",'
            '"type":"authentication_error","param":null,"code":"invalid_request_error"}}') % (tail, REQUEST_ID)


def key_and_tail():
    key = 'sk-' + secrets.token_hex(16)
    return Secret(key), key[-4:]


def http_401(tail):
    def raiser(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(deepseek_body(tail).encode()))
    return raiser


@pytest.mark.parametrize('text', [
    'Authentication Fails, Your api key: ****1226 is invalid',
    'Incorrect API key provided: sk-proj-****WXYZ. You can find your API key',
    'Invalid key AIza****Q9xK for project',
    'bad credential ••••1226',
    'key ●●●●●●ab12cd',
    'x-api-key: ***9f3a',
])
def test_masked_fragments_are_removed(text):
    cleaned, changed = scrub(text, [Secret('sk-' + secrets.token_hex(16))])
    assert changed and '[REDACTED:key]' in cleaned
    for fragment in ('1226', 'WXYZ', 'Q9xK', 'ab12cd', '9f3a'):
        assert fragment not in cleaned


def test_the_real_deepseek_error_keeps_everything_but_the_fragment():
    cleaned, changed = scrub(deepseek_body('1226'), [Secret('sk-' + secrets.token_hex(16))])
    assert changed and '1226' not in cleaned
    value = json.loads(cleaned)
    assert value['error']['message'] == 'Authentication Fails, Your api key: [REDACTED:key] is invalid (request_id: %s)' % REQUEST_ID
    assert value['error']['type'] == 'authentication_error'


@pytest.mark.parametrize('text', [
    'all good', '**bold** text', 'x ** 2 and 2**10', '5 * 3 = 15', '**', 'see *note* here',
    '{"error":"Model Not Exist"}', 'request_id: %s' % REQUEST_ID,
])
def test_ordinary_text_is_left_alone(text):
    assert scrub(text, [Secret('sk-' + secrets.token_hex(16))]) == (text, False)


def test_probe_and_model_listing_errors_carry_no_fragment():
    secret, tail = key_and_tail()
    result = probe(presets()['deepseek'], secret, 'deepseek-chat', role='dev', transport=http_401(tail))
    assert not result.ok and result.errors and all(tail not in e['message'] and e['redacted'] for e in result.errors)
    with pytest.raises(Exception) as caught:
        list_models(presets()['deepseek'], secret, transport=http_401(tail))
    assert tail not in str(caught.value) and '[REDACTED:key]' in str(caught.value)


def test_direct_chat_client_error_carries_no_fragment(monkeypatch):
    secret, tail = key_and_tail()
    monkeypatch.setattr(providers, 'open_request', http_401(tail))
    with pytest.raises(ParccError) as caught:
        ByokChatClient(type('P', (), dict(spec=presets()['deepseek'], secret=secret))()).chat(
            model='deepseek-chat', messages=[dict(role='user', content='hi')], max_tokens=8)
    assert tail not in str(caught.value) and '[REDACTED:key]' in str(caught.value)


def test_bridge_reply_and_log_carry_no_fragment(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_module.http.server, 'ThreadingHTTPServer', MemoryServer)
    secret, tail = key_and_tail()

    def upstream(chat, key, **kwargs):
        raise urllib.error.HTTPError('x', 401, 'Unauthorized', {}, io.BytesIO(deepseek_body(tail).encode()))
        yield  # pragma: no cover

    bridge = ChatBridge(tmp_path, presets()['deepseek'], secret, token='t' * 24, upstream=upstream)
    try:
        status, body, _ = exchange(bridge.server, 'POST', '/v1/responses', dict(model='deepseek-chat', input=[]),
                                   {'Authorization': 'Bearer ' + bridge.token})
    finally:
        bridge.close()
    log = (tmp_path / 'calls.jsonl').read_text()
    assert status == 400 and tail.encode() not in body and tail not in log
    assert b'[REDACTED:key]' in body and json.loads(log.splitlines()[-1]).get('redacted') is True
