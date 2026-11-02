"""Replay mode of the Run page: no control token, fixed collector paths, loopback collector only."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness import replay_http
from benchmarks.attention_harness.tests.test_attention_store import populated_store
from benchmarks.attention_harness.ui_server import create_server

RUN = 'devexp-' + 'a' * 24


class Collector:
    """Stands in for the reply collector and remembers what reached it."""

    def __init__(self):
        seen = self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def reply(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

            def do_GET(self):
                seen.append(('GET', self.path, None, self.headers.get('X-Forwarded-For')))
                self.reply({'count': 3})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append(('POST', self.path, body, self.headers.get('X-Forwarded-For')))
                self.reply({'error': 'invalid_code'}, 401) if body.get('code') == 'bad' else self.reply({'receipt': 'AAAA-BBBB-CCCC-DDDD'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self): return f'http://127.0.0.1:{self.server.server_port}'

    def close(self): self.server.shutdown(); self.server.server_close()


@pytest.fixture
def served(tmp_path):
    collector = Collector()
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    config = replay_http.load_config({'runs': {RUN: 'run-a'}, 'collector_url': collector.url, 'dataset_version': 'ds-1', 'requires_code': True})
    server = create_server(store=store, port=0, replay=config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', collector
    finally:
        server.shutdown(); server.server_close(); collector.close()


def get(base, path):
    with urlopen(base + path) as response:
        return response.status, response.read().decode()


def post(base, path, body, content_type='application/json'):
    try:
        with urlopen(Request(base + path, method='POST', data=json.dumps(body).encode(), headers={'Content-Type': content_type})) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read() or b'{}')


def test_configuration_is_validated():
    ok = {'runs': {RUN: 'run-a'}, 'collector_url': 'http://127.0.0.1:8794'}
    assert replay_http.load_config(ok)['collector'] == 'http://127.0.0.1:8794'
    for bad in ({}, {**ok, 'extra': 1}, {**ok, 'runs': {}}, {**ok, 'runs': {'x': 'run-a'}}, {**ok, 'runs': {RUN: 'Run A!'}},
                {**ok, 'collector_url': 'http://example.com:8794'}, {**ok, 'collector_url': 'https://127.0.0.1:8794'},
                {**ok, 'collector_url': 'http://127.0.0.1:8794/x'}):
        with pytest.raises(ValueError):
            replay_http.load_config(bad)


def test_replay_page_has_no_control_token_and_the_normal_page_still_does(served):
    base, _ = served
    status, page = get(base, '/ui/replay')
    root = lambda html: re.search(r'<div id="attentionbench-clear"[^>]*>', html)[0]
    assert status == 200 and 'data-replay="1"' in root(page) and 'data-control-token' not in page
    normal = get(base, '/ui/')[1]
    assert 'data-control-token' in root(normal) and 'data-replay' not in root(normal)
    assert json.loads(get(base, '/api/replay/config')[1]) == {'mode': 'replay', 'runs': {RUN: 'run-a'}, 'dataset_version': 'ds-1', 'requires_code': True}
    assert 'ReplayMode' in get(base, '/ui/replay_mode.js')[1]


def test_replies_reach_the_collector_only_through_the_fixed_paths(served):
    base, collector = served
    assert post(base, '/api/replay/reply', {'moment_id': 'run-a-m1', 'outcome': 'answered', 'content': 'slower', 'code': 'ok'}) == (200, {'receipt': 'AAAA-BBBB-CCCC-DDDD'})
    assert post(base, '/api/replay/reply', {'moment_id': 'run-a-m1', 'outcome': 'answered', 'content': 'x', 'code': 'bad'}) == (401, {'error': 'invalid_code'})
    assert post(base, '/api/replay/delete', {'receipt': 'AAAA-BBBB-CCCC-DDDD'})[0] == 200
    assert get(base, '/api/replay/count?moment=run-a-m1') == (200, '{"count": 3}')
    assert [(m, p) for m, p, *_ in collector.seen] == [('POST', '/api/replay-reply'), ('POST', '/api/replay-reply'), ('POST', '/api/replay-reply/delete'),
                                                      ('GET', '/api/replay-count?moment=run-a-m1')]
    assert all(entry[3] == '127.0.0.1' for entry in collector.seen)


def test_other_paths_and_bad_requests_are_not_forwarded(served):
    base, collector = served
    assert post(base, '/api/replay/reply', {'x': 1}, 'text/plain')[0] == 400
    assert post(base, '/api/replay/unknown', {'x': 1})[0] in (403, 404, 405)
    try:
        urlopen(base + '/api/replay/reply')
    except HTTPError as error:
        assert error.code == 405
    assert collector.seen == []


def test_collector_down_gives_a_plain_error(tmp_path):
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    config = replay_http.load_config({'runs': {RUN: 'run-a'}, 'collector_url': 'http://127.0.0.1:9'})
    server = create_server(store=store, port=0, replay=config)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert post(f'http://127.0.0.1:{server.server_port}', '/api/replay/reply', {'moment_id': 'run-a-m1', 'outcome': 'skipped'}) == (503, {'error': 'collector_unavailable'})
    finally:
        server.shutdown(); server.server_close()


def test_without_a_replay_configuration_the_replay_paths_do_not_exist(tmp_path):
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    server = create_server(store=store, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for path in ('/ui/replay', '/api/replay/config'):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 404
    finally:
        server.shutdown(); server.server_close()
