"""Public profile: a visitor reaches the replay page, the recorded runs and /api/replay/* only."""
from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness import replay_http
from benchmarks.attention_harness.tests.test_attention_store import populated_store
from benchmarks.attention_harness.tests.test_replay_http import Collector, RUN
from benchmarks.attention_harness.ui_server import create_server

CONFIG = {'runs': {RUN: 'run-a'}}


def raw(base, method, path, body=None):
    request = Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'} if body is not None else {})
    try:
        with urlopen(request) as response:
            return response.status, response.read(), response.headers.get('Content-Type')
    except HTTPError as error:
        return error.code, error.read(), error.headers.get('Content-Type')


@pytest.fixture
def public(tmp_path):
    collector = Collector()
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    config = replay_http.load_config({**CONFIG, 'collector_url': collector.url})
    server = create_server(store=store, port=0, replay=config, public=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', collector
    finally:
        server.shutdown(); server.server_close(); collector.close()


HIDDEN_GET = ['/', '/ui', '/ui/', '/ui/home.html', '/ui/home.js', '/ui/memory.html', '/ui/memory.js', '/ui/index.html', '/api/run-options', '/api/notices',
              '/api/codex-quota', '/api/orchestrator/snapshot', '/api/home/status', '/api/home/scene', '/api/dev-v2/memory-monitor?exp=x',
              '/api/dev-v2/experiments', '/api/dev-v2/experiments/devexp-' + 'b' * 24, '/api/dev-v2/experiments/' + RUN + '/report',
              '/api/dev-v2/experiments/' + RUN + '/cells/' + 'a' * 64 + '/agent/files/../../../etc/passwd', '/api/provider/presets',
              '/api/provider/credentials', '/api/dev/sessions/x', '/api/runs/x', '/api/requests/x', '/ui/../ui_server.py', '/nope']
HIDDEN_WRITE = [('POST', '/api/dev-v2/experiments', {}), ('POST', '/api/dev-v2/experiments/' + RUN + '/run', {}), ('POST', '/api/runs/start', {}),
                ('POST', '/api/dev/start', {}), ('POST', '/api/requests/x/respond', {}), ('POST', '/api/provider/credentials/x/test', {}),
                ('PUT', '/api/provider/credentials/x', {'provider': 'openai', 'key': 'k'}), ('DELETE', '/api/provider/credentials/x', None),
                ('POST', '/api/replay/other', {}), ('PUT', '/api/replay/reply', {}), ('DELETE', '/api/replay/reply', None), ('PATCH', '/ui/replay', {}), ('OPTIONS', '/ui/replay', None)]


def test_every_other_path_is_the_same_plain_404(public):
    base, _ = public
    answers = {raw(base, 'GET', path)[:3] for path in HIDDEN_GET} | {raw(base, m, path, body)[:3] for m, path, body in HIDDEN_WRITE}
    assert answers == {(404, b'Not found\n', 'text/plain; charset=utf-8')}
    assert raw(base, 'HEAD', '/ui/replay')[0::2] == (404, 'text/plain; charset=utf-8') and raw(base, 'HEAD', '/ui/')[0] == 404


def test_visitors_get_the_replay_page_without_a_token_and_with_only_the_replay_tab(public):
    base, _ = public
    status, page, _ = raw(base, 'GET', '/ui/replay')
    assert status == 200 and b'data-replay="1"' in page and b'data-control-token' not in page
    assert b'data-replay-tab' not in page
    for path in ('/ui/live.js', '/ui/shell.js', '/ui/shell.css', '/ui/human_reply.js', '/ui/replay_mode.js', '/api/replay/config'):
        assert raw(base, 'GET', path)[0] == 200
    shell = raw(base, 'GET', '/ui/shell.js')[1].decode()
    assert "[['replay', 'Replay', '/ui/replay']]" in shell


def test_the_run_list_holds_only_recorded_sessions_without_machine_details(public):
    base, _ = public
    listing = json.loads(raw(base, 'GET', '/api/runs')[1])
    assert listing == {'runs': [], 'launches': [], 'dev_sessions': []}


def test_scrub_removes_machine_details():
    value = {'artifact_dir': '/home/me/x', 'owner': 'o', 'request_key': 'k', 'note': 'see /home/me/runs/a.json and /tmp/x', 'rows': [{'path': '/Users/a/b', 'n': 3}]}
    assert replay_http.scrub(value) == {'note': 'see [path] and [path]', 'rows': [{'path': '[path]', 'n': 3}]}


def test_replies_still_reach_the_collector_and_nothing_else(public):
    base, collector = public
    assert raw(base, 'POST', '/api/replay/reply', {'moment_id': 'run-a-m1', 'outcome': 'skipped'})[0] == 200
    assert [(m, p) for m, p, *_ in collector.seen] == [('POST', '/api/replay-reply')]


def test_allowlist_function_directly():
    config = replay_http.load_config({**CONFIG, 'collector_url': 'http://127.0.0.1:1'})
    cell = '/api/dev-v2/experiments/' + RUN + '/cells/' + 'c' * 64
    allowed = [('GET', '/ui/replay'), ('GET', '/api/runs'), ('GET', '/api/dev-v2/experiments/' + RUN), ('GET', cell + '/view'),
               ('GET', cell + '/agent/files/compat/v0001/bundle.json'), ('GET', cell + '/files/advisor_replay.mp4'), ('POST', '/api/replay/reply')]
    refused = [('GET', '/api/dev-v2/experiments/devexp-' + 'e' * 24), ('GET', cell + '/live'), ('GET', cell + '/agent/files/../x'), ('GET', '/ui//replay'),
               ('POST', '/api/dev-v2/experiments/' + RUN + '/run'), ('PUT', '/api/replay/reply'), ('HEAD', '/ui/replay')]
    assert all(replay_http.public_allows(m, p, config) for m, p in allowed)
    assert not any(replay_http.public_allows(m, p, config) for m, p in refused)


def test_public_profile_refuses_run_authorization_or_a_missing_replay_configuration(tmp_path):
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    with pytest.raises(ValueError):
        create_server(store=store, port=0, public=True)
    config = replay_http.load_config({**CONFIG, 'collector_url': 'http://127.0.0.1:1'})
    with pytest.raises(ValueError):
        create_server(store=store, port=0, replay=config, public=True, dev_v2_per_run_budget=True)
