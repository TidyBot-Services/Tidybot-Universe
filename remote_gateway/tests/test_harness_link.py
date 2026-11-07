"""The gateway against a real UI server in gateway mode: signatures, per-person keys, and creating a run for real."""
import http.client
import json
import secrets
import threading
import time

import pytest

from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge_module, dev_agent_provider as provider, ui_server
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.tests.fakes.byok_http import loopback
from benchmarks.attention_harness.tests.test_daily_byok_keys import fake_probe_transport, fake_process, materialize
from remote_gateway import server as gateway_server
from remote_gateway.config import Config, Limits
from remote_gateway.tests.test_gateway import Browser, UI
from remote_gateway.upstream import signature

SECRET = b'k' * 48
PARCC = lambda model='parcc/GLM': dict(credential='lab', provider='parcc', model=model)


def serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def raw_call(port, method, path, *, headers=None, body=b''):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    connection.request(method, path, body=body or None, headers=headers or {})
    response = connection.getresponse();content = response.read();connection.close()
    return response.status, content


class Link:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.harness = ui_server.create_server(store=AttentionStore(tmp_path / 'ui.sqlite3'), port=0, dev_v2_root=tmp_path / 'daily', dev_v2_per_run_budget=True,
                                               vault=ProviderKeyVault(), gateway_secret=Secret(SECRET.decode()))
        self.harness_port = serve(self.harness)
        self.config = Config(state_dir=tmp_path / 'gateway', upstream=f'http://127.0.0.1:{self.harness_port}', ui_dir=UI, listen_port=0, cookie_secure=False,
                             limits=Limits(requests_per_second=1000, request_burst=1000))
        self.config.state_dir.mkdir()
        self.gateway = gateway_server.create_server(self.config, secret=SECRET)
        self.port = serve(self.gateway)

    def person(self):
        browser = Browser(self.port);_, code = self.gateway.gateway.store.create_invite()
        assert browser.login(code)[0] == 303
        return browser

    def choice(self, browser):
        value = browser.json('GET', '/api/run-options')[1]['dev_v2']
        profile = next(p for p in value['profiles'] if p['enabled'] and not p.get('rules_profile'))
        return dict(request_key='client-key-' + secrets.token_hex(4), tasks=[profile['task_id']], seeds=[102], policies=['autonomous'], mode='interactive', help_budget=2,
                    agent_provider=dict(roles=dict(dev=PARCC(), eval=PARCC(), advisor=PARCC('parcc/Qwen'))))

    def close(self):
        for server in (self.gateway, self.harness):server.shutdown();server.server_close()


@pytest.fixture
def link(tmp_path, loopback, materialize):
    value = Link(tmp_path)
    yield value
    value.close()


def signed(owner, method, path, body=b'', secret=SECRET, now=None):
    return {'X-Attention-Owner': owner, 'X-Attention-Gateway-Auth': signature(secret, owner, method, path, body, time.time() if now is None else now)}


@pytest.mark.parametrize('method,path', [('GET', '/ui/'), ('GET', '/api/runs'), ('GET', '/api/run-options'), ('GET', '/api/codex-quota'), ('GET', '/api/provider/credentials'), ('POST', '/api/dev-v2/experiments')])
def test_the_harness_answers_nobody_else(link, method, path):
    status, content = raw_call(link.harness_port, method, path)
    assert status == 401 and json.loads(content)['error']['code'] == 'gateway_auth_invalid'


def test_a_wrong_or_stale_signature_is_refused(link):
    assert raw_call(link.harness_port, 'GET', '/api/runs', headers=signed('u-1', 'GET', '/api/runs', secret=b'x' * 48))[0] == 401
    assert raw_call(link.harness_port, 'GET', '/api/runs', headers=signed('u-1', 'GET', '/api/runs', now=time.time() - 60))[0] == 401
    assert raw_call(link.harness_port, 'GET', '/api/run-options', headers=signed('u-1', 'GET', '/api/runs'))[0] == 401
    assert raw_call(link.harness_port, 'GET', '/api/runs', headers=signed('u-1', 'GET', '/api/runs'))[0] == 200


def test_a_signed_body_cannot_be_changed(link):
    body = json.dumps(dict(provider='deepseek', key='sk-a')).encode();path = '/api/provider/credentials/a'
    headers = {**signed('u-1', 'PUT', path, body), 'Content-Type': 'application/json'}
    assert raw_call(link.harness_port, 'PUT', path, headers=headers, body=body.replace(b'sk-a', b'sk-b'))[0] == 401


def test_a_local_server_refuses_owner_headers(tmp_path, loopback):
    server = ui_server.create_server(store=AttentionStore(tmp_path / 'ui.sqlite3'), port=0)
    port = serve(server)
    try:
        assert raw_call(port, 'GET', '/api/runs', headers={'X-Attention-Owner': 'u-1'})[0] == 400
        assert raw_call(port, 'GET', '/api/runs')[0] == 200
    finally:server.shutdown();server.server_close()


def test_a_preset_cannot_be_redirected_or_replaced_in_gateway_mode(tmp_path, loopback, monkeypatch):
    monkeypatch.setenv('PARCC_URL', 'https://elsewhere.example/v1/chat/completions')
    link = Link(tmp_path)
    try:
        import os
        assert 'PARCC_URL' not in os.environ
        browser = link.person()
        status, value = browser.json('PUT', '/api/provider/credentials/mine', dict(provider='openai-compatible', key='sk-x', base_url='https://example.org/v1'))
        assert status == 400 and value['error']['code'] == 'custom_base_url_disabled'
    finally:link.close()


@pytest.mark.parametrize('url,code', [('https://127.0.0.1/v1', 'base_url_private_host'), ('https://10.0.0.5/v1', 'base_url_private_host'),
                                      ('https://169.254.169.254/v1', 'base_url_private_host'), ('http://8.8.8.8/v1', 'base_url_not_https')])
def test_a_custom_endpoint_never_reaches_this_network(tmp_path, loopback, monkeypatch, url, code):
    monkeypatch.setenv('ATTENTION_ALLOW_CUSTOM_PROVIDER_URL', '1')
    link = Link(tmp_path)
    try:
        status, value = link.person().json('PUT', '/api/provider/credentials/mine', dict(provider='openai-compatible', key='sk-x', base_url=url))
        assert status == 400 and value['error']['code'] == code
    finally:link.close()


def test_keys_belong_to_the_person_who_saved_them(link):
    alice, bob = link.person(), link.person()
    assert alice.send('PUT', '/api/provider/credentials/mine', dict(provider='deepseek', key='sk-' + secrets.token_urlsafe(32)))[0] == 200
    assert [row['credential_id'] for row in alice.json('GET', '/api/provider/credentials')[1]] == ['mine']
    assert bob.json('GET', '/api/provider/credentials')[1] == []
    selection = link.choice(bob);selection['agent_provider']['roles']['dev'] = dict(credential='mine', provider='deepseek', model='m')
    status, value = bob.json('POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
    assert status == 400 and value['code'] == 'key_not_found'


def test_a_key_that_reads_text_only_reaches_the_run_as_declared(link):
    browser = link.person()
    assert browser.send('PUT', '/api/provider/credentials/mine', dict(provider='deepseek', key='sk-' + secrets.token_urlsafe(32)))[0] == 200
    selection = link.choice(browser);selection['agent_provider']['roles']['eval'] = dict(credential='mine', provider='deepseek', model='m', vision=False)
    status, made = browser.json('POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
    assert status == 200, made
    view = browser.json('GET', f'/api/dev-v2/experiments/{made["experiment_id"]}/cells/{made["cells"][0]["key"]}/view')[1]
    assert 'eval' in view['agent']['text_only_roles'] and 'advisor' not in view['agent']['text_only_roles']


def test_a_run_is_public_to_read_and_only_its_owner_may_act(link):
    alice, bob = link.person(), link.person()
    selection = link.choice(alice)
    status, made = alice.json('POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
    assert status == 200, made
    eid, key = made['experiment_id'], made['cells'][0]['key']
    status, view = alice.json('GET', f'/api/dev-v2/experiments/{eid}/cells/{key}/view')
    assert status == 200, view
    assert view['agent']['model_source'] == 'parcc' and view['agent']['reduced_budget']['versions'] == 3 and view['agent']['reduced_budget']['wall_seconds'] == 21600
    assert [row['experiment_id'] for row in alice.json('GET', '/api/runs')[1]['dev_sessions']] == [eid]
    assert [row['lobby']['mine'] for row in bob.json('GET', '/api/runs')[1]['dev_sessions']] == [False]
    assert bob.send('GET', f'/api/dev-v2/experiments/{eid}')[0] == 200
    assert bob.send('POST', f'/api/dev-v2/experiments/{eid}/cancel', {})[0] == 404
    for answer in (made, view, alice.json('GET', f'/api/dev-v2/experiments/{eid}')[1], alice.json('GET', '/api/runs')[1]):
        assert str(link.root) not in json.dumps(answer), 'a host path reached the browser'


def test_a_reply_reaches_the_owners_waiting_run_and_nobody_elses(link):
    from benchmarks.attention_harness import dev_agent_human_reply as human
    from benchmarks.attention_harness.dev_experiment import DevV2Store
    alice, bob = link.person(), link.person()
    made = alice.json('POST', '/api/dev-v2/experiments', dict(selection=link.choice(alice), start=False))[1]
    eid, key = made['experiment_id'], made['cells'][0]['key']
    store = DevV2Store(link.root/'daily'/eid/'ledger.sqlite3')
    private = link.root/'cell'/'agent/private'
    store.update_cell(key, artifact_dir=str(link.root/'cell'))
    folder = private/'help/e000010';folder.mkdir(parents=True)
    (folder/human.WAIT).write_text(json.dumps(dict(execution_id='e000010', question='What next?', deadline_at=time.time()+60)))
    path, reply = f'/api/dev-v2/experiments/{eid}/help-reply', dict(cell=key, execution_id='e000010', content='Lower it.')
    assert bob.json('POST', path, reply)[0] == 404
    status, value = alice.json('POST', path, reply)
    assert status == 200 and value == dict(status='received', kind='reply'), value
    assert json.loads((folder/human.OUTCOME).read_bytes())['content'] == 'Lower it.'
    assert alice.json('POST', path, reply)[0] == 409


def test_start_goes_through_the_owners_keys_and_the_key_stays_out_of_every_record(link, monkeypatch):
    key = Secret('sk-canary-' + secrets.token_hex(24));lab = 'sk-lab-' + secrets.token_hex(24)
    monkeypatch.setattr(bridge_module, 'parcc_key', lambda: lab)
    monkeypatch.setattr(provider, 'open_request', fake_probe_transport());launched = fake_process(monkeypatch)
    browser = link.person()
    assert browser.send('PUT', '/api/provider/credentials/mine', dict(provider='deepseek', key=key.reveal()))[0] == 200
    selection = link.choice(browser);selection['agent_provider']['roles']['eval'] = dict(credential='mine', provider='deepseek', model='parcc/GLM')
    selection['agent_provider']['roles']['advisor'] = PARCC()
    status, made = browser.json('POST', '/api/dev-v2/experiments', dict(selection=selection, start=True))
    assert status == 200, made
    assert len(launched) == 1 and '--key-fd' in launched[0][0]
    assert not scan([key, Secret(lab)], roots=[link.config.state_dir, link.root / 'daily'], sqlite_paths=[link.config.database], include_processes=False)


def test_a_host_that_cannot_reach_the_provider_refuses_the_start_and_the_run_is_kept(link, monkeypatch):
    import urllib.error
    monkeypatch.setattr(bridge_module, 'parcc_key', lambda: 'sk-lab-' + 'x' * 30)
    def down(request, **kwargs):raise urllib.error.URLError('timed out')
    monkeypatch.setattr(provider, 'open_request', down);launched = fake_process(monkeypatch)
    browser = link.person()
    status, value = browser.json('POST', '/api/dev-v2/experiments', dict(selection=link.choice(browser), start=True))
    assert status == 409 and 'timed out' in value['error'] and not launched
    runs = browser.json('GET', '/api/runs')[1]['dev_sessions']
    assert len(runs) == 1
    monkeypatch.setattr(provider, 'open_request', fake_probe_transport())
    assert browser.send('POST', f'/api/dev-v2/experiments/{runs[0]["experiment_id"]}/run', {})[0] == 200
    assert len(launched) == 1


def test_the_run_list_says_when_a_run_waits_for_a_person(tmp_path):
    from benchmarks.attention_harness import dev_agent_human_reply as human
    from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(dict(dev_mode='agent', model=dict(model='m'), configs={'t:1': {}})))
    cell = dict(key='c' * 64, task_id='t', seed=1, state='running', artifact_dir=str(tmp_path / 'cell'))
    experiment = dict(manifest=dict(uri=str(manifest)), created_at=1, state='running', selection={}, cells=[cell])
    private = tmp_path / 'cell/agent/private'
    bridge = object.__new__(DailyDevV2)
    assert 'human_wait' not in bridge.sessions(experiment)[0]
    folder = private / 'help/e000001';folder.mkdir(parents=True)
    (folder / human.WAIT).write_text(json.dumps(dict(execution_id='e000001', question='What next?', deadline_at=time.time() + 60, seconds=120)))
    assert bridge.sessions(experiment)[0]['human_wait']['question'] == 'What next?'
    (folder / human.OUTCOME).write_text('{}')
    assert 'human_wait' not in bridge.sessions(experiment)[0]
