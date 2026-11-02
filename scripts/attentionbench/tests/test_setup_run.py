"""Offline contracts for the collaborator entry points."""
import argparse
import fcntl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace

import pytest

from scripts.attentionbench import run, setup


BASE_SELECTION = {
    'dev_mode': 'agent', 'mode': 'experiment', 'budget_tier': 'dev',
    'tasks': ['pick_place_counter_to_sink'], 'seeds': [101],
    'policies': ['full_trace_aware_attention_planner'],
    'dev_model': 'gpt-6.1-sol', 'eval_model': 'gpt-6.1-sol', 'advisor_model': 'gpt-6.1-sol',
    'advisor_proxy': {'enabled': True}, 'help_budget': 1, 'max_versions': 2,
    'memory_family': True, 'run_notes': True, 'quota_stop_percent': 80,
    'wall_limit': 2400, 'goal_key': 'l2-acceptance-seed101-20261101-e',
    'request_key': 'l2-acceptance-seed101-20261101-e', 'name_suffix': 'L2 clean container',
}
# The L2 body with only the run's display name changed, so collaborator runs are not labelled L2.
TEMPLATE_SELECTION = {**BASE_SELECTION, 'name_suffix': 'collaborator run.sh'}


def test_request_matches_l2_and_keys_are_unique():
    assert json.loads(run.TEMPLATE.read_text()) == {'selection': TEMPLATE_SELECTION, 'start': True}
    keys = set()
    for _ in range(100):
        body = run.request_body('pick_place_counter_to_sink', 101, 30)
        selection = body['selection']
        assert {k: v for k, v in selection.items() if k not in
                {'tasks', 'seeds', 'wall_limit', 'goal_key', 'request_key'}} == {
                    k: v for k, v in TEMPLATE_SELECTION.items() if k not in
                    {'tasks', 'seeds', 'wall_limit', 'goal_key', 'request_key'}}
        assert selection['wall_limit'] == 2040
        assert selection['goal_key'] == selection['request_key']
        assert re.fullmatch(r'collab-pick_place_counter_to_sink-seed101-\d{14}-[0-9a-f]{6}', selection['request_key'])
        keys.add(selection['request_key'])
        assert all(name in selection for name in ('dev_model', 'eval_model', 'advisor_model'))
        assert not any(k in {'key', 'api_key'} for k in selection)
    assert len(keys) == 100
    with pytest.raises(ValueError, match='override'):
        run.request_body('pick_place_counter_to_sink', 101, 30, {'quota_stop_percent': 100})


def test_cli_has_no_relaxing_options(capsys):
    for entry in (setup.main, run.main):
        with pytest.raises(SystemExit) as exc:
            entry(['--help'])
        assert exc.value.code == 0
        help_text = capsys.readouterr().out
        assert not re.search(r'--[\w-]*(?:exempt|relax|skip-verif|physical|identity|provenance|movable)', help_text)


@pytest.fixture
def fake_prereqs(tmp_path, monkeypatch):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name in ('nvidia-smi', 'git', 'python3-config', 'cc', 'uv', 'conda', 'bwrap', 'codex'):
        file = bindir / name
        file.write_text('#!/bin/sh\nexit 0\n')
        file.chmod(0o755)
    monkeypatch.setenv('PATH', str(bindir))
    monkeypatch.setattr(setup, '_isolation_from_doctor', lambda ws: (True, 'probe passed'))
    monkeypatch.setattr(setup, '_free_port', lambda port: True)
    monkeypatch.setattr(setup, '_run', lambda argv, timeout=10: SimpleNamespace(
        returncode=0, stdout='580.95\n' if Path(argv[0]).name == 'nvidia-smi' else
        'codex-cli 0.165.0\n' if '--version' in argv else '', stderr=''))
    monkeypatch.setattr(setup.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=100, f_frsize=1024**3))
    return bindir, tmp_path / 'workspace'


@pytest.mark.parametrize('missing', ['nvidia-smi', 'git', 'cc', 'uv', 'conda', 'bwrap', 'codex'])
def test_prerequisite_missing_executable_has_install_instruction(fake_prereqs, capsys, missing):
    bindir, ws = fake_prereqs
    (bindir / missing).unlink()
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    failures = [line for line in capsys.readouterr().out.splitlines() if line.startswith('FAIL')]
    assert failures and all('; install: ' in line for line in failures)


def test_prerequisites_disk_and_port_and_dry_warning(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=10, f_frsize=1024**3))
    monkeypatch.setattr(setup, '_free_port', lambda port: port != 8769)
    assert not setup.prerequisites(ws, dry_run=True, ui_port=8769, memory_port=8878)
    output = capsys.readouterr().out
    assert 'FAIL disk:' in output and 'FAIL UI port:' in output
    assert all('; install:' in line for line in output.splitlines() if line.startswith('FAIL'))


def test_prerequisites_platform_and_python(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(setup.sys, 'version_info', (3, 9))
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    output = capsys.readouterr().out
    assert 'FAIL Linux x86_64:' in output and 'FAIL Python 3.10+ headers:' in output
    assert all('; install:' in line for line in output.splitlines() if line.startswith('FAIL'))


def test_dry_run_creates_nothing(tmp_path, monkeypatch):
    ws = tmp_path / 'absent'
    monkeypatch.setattr(setup, 'prerequisites', lambda *a, **k: True)
    monkeypatch.setattr(setup.installer, 'bootstrap', lambda args: None)
    monkeypatch.setattr(setup.installer, 'doctor', lambda args: pytest.fail('doctor should not run in dry-run'))
    assert setup.main(['--workspace', str(ws), '--dry-run']) == 0
    assert not ws.exists()


def test_warmup_paths(tmp_path, monkeypatch, capsys):
    calls = []
    original_quota = setup.quota_readable
    monkeypatch.setattr(setup, '_run', lambda argv, timeout=10: calls.append((argv, timeout)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(setup, 'quota_readable', lambda: True)
    setup.warmup(False)
    assert not calls
    monkeypatch.setattr(setup, 'quota_readable', lambda: False)
    setup.warmup(False)
    assert calls == [(['codex', 'exec', '--skip-git-repo-check', 'Reply with ok.'], 120)]
    setup.warmup(True)
    assert len(calls) == 1
    assert 'quota_unreadable' in capsys.readouterr().out
    sessions = tmp_path / '.codex/sessions/2026/11'
    sessions.mkdir(parents=True)
    rollout = sessions / 'rollout-test.jsonl'
    monkeypatch.setattr(setup.Path, 'home', lambda: tmp_path)

    def reading(age):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(time.time() - age))
        event = {'timestamp': stamp, 'payload': {'rate_limits': {'secondary': {
            'window_minutes': 10080, 'used_percent': 42.0, 'resets_at': int(time.time()) + 86400}}}}
        rollout.write_text(json.dumps(event) + '\n')

    assert not original_quota()  # no reading at all
    reading(60)
    assert original_quota()
    reading(9 * 60)  # an agent run accepts 10 minutes; setup keeps 2 minutes of margin
    assert not original_quota()
    reading(60)
    monkeypatch.setitem(sys.modules, 'benchmarks.attention_harness.dev_agent_quota', None)
    assert not original_quota()  # without the reader, warm up rather than guess


class Handler(BaseHTTPRequestHandler):
    state = 'completed'
    posts = []
    eid = 'devexp-' + 'a' * 24

    def log_message(self, *args):
        pass

    def _send(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/ui/':
            data = b'<div data-control-token="token123"></div>'
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
        else:
            self._send({'experiment_id': self.eid, 'state': self.state,
                        'cells': [{'versions': [{'version': 1, 'eval_verdict': {'passed': True}}]}]})

    def do_POST(self):
        data = self.rfile.read(int(self.headers['Content-Length']))
        self.posts.append((self.path, self.headers['X-Attention-Token'], self.headers['Content-Type'], json.loads(data)))
        if self.path.endswith('/cancel'):
            self.__class__.state = 'cancelled'
            self._send({'state': 'cancelled'})
        else:
            self._send({'experiment_id': self.eid, 'state': 'running'})


@pytest.fixture
def server():
    Handler.state = 'completed'
    Handler.posts = []
    try:
        http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    except PermissionError:
        pytest.skip('loopback sockets are blocked by this sandbox')
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield http
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def args(ws, port, **changes):
    (ws / 'paths.json').write_text('{}')  # a set-up workspace
    values = dict(workspace=str(ws), task='pick_place_counter_to_sink', seed=101,
                  minutes=30, lock=None, no_stop=False, ui_port=port, memory_port=8878)
    values.update(changes)
    return argparse.Namespace(**values)


def test_run_terminal_and_summary(server, tmp_path, monkeypatch):
    actions = []
    monkeypatch.setattr(run, 'command', lambda action, *a: actions.append(action))
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: actions.append('warmup'))
    assert run.execute(args(tmp_path, server.server_port)) == 0
    assert actions == ['warmup', 'start', 'stop']
    assert len(Handler.posts) == 1
    assert Handler.posts[0][1:3] == ('token123', 'application/json')
    summary = json.loads((tmp_path / 'data/run' / (Handler.eid + '-summary.json')).read_text())
    assert summary['final_state'] == 'completed' and summary['version_count'] == 1
    assert summary['eval_verdict'] == {'passed': True}


def test_run_timeout_cancels_with_object_then_stops(server, tmp_path, monkeypatch):
    Handler.state = 'running'
    actions = []
    monkeypatch.setattr(run, 'command', lambda action, *a: actions.append(action))
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: actions.append('warmup'))
    assert run.execute(args(tmp_path, server.server_port, minutes=.00001)) == 0
    assert actions == ['warmup', 'start', 'stop']
    assert Handler.posts[-1][0].endswith('/cancel')
    assert Handler.posts[-1][1:] == ('token123', 'application/json', {})


def test_busy_lock_fails_before_start(tmp_path, monkeypatch):
    lock_file = tmp_path / 'run.lock'
    with lock_file.open('w') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setattr(run, 'command', lambda *a: pytest.fail('must not start'))
        with pytest.raises(setup.installer.StepError, match='busy'):
            run.execute(args(tmp_path, 8769, lock=str(lock_file)))


def test_run_flow_with_in_memory_http(tmp_path, monkeypatch):
    posts = []
    actions = []
    state = {'value': 'running'}
    monkeypatch.setattr(run, 'command', lambda action, *a: actions.append(action))
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: actions.append('warmup'))
    monkeypatch.setattr(run.control, 'control_token', lambda url: 'token123')
    def post(url, token, body):
        posts.append((url, token, body))
        if url.endswith('/cancel'):
            state['value'] = 'cancelled'
            return {'state': 'cancelled'}
        return {'experiment_id': Handler.eid, 'state': 'running'}
    monkeypatch.setattr(run, 'post_json', post)
    monkeypatch.setattr(run, 'get_json', lambda url: {
        'experiment_id': Handler.eid, 'state': state['value'],
        'cells': [{'versions': [{'version': 1, 'eval_verdict': {'passed': False}}]}],
    })
    assert run.execute(args(tmp_path, 8769, minutes=.00001)) == 0
    assert actions == ['warmup', 'start', 'stop']
    assert posts[-1][0].endswith('/cancel') and posts[-1][1:] == ('token123', {})
    result = json.loads((tmp_path / 'data/run' / (Handler.eid + '-summary.json')).read_text())
    assert result['final_state'] == 'cancelled' and result['version_count'] == 1


def test_run_requires_set_up_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: pytest.fail('must not warm up'))
    monkeypatch.setattr(run, 'command', lambda *a: pytest.fail('must not start'))
    assert run.main(['--workspace', str(tmp_path / 'WS')]) == 1
    assert 'setup.sh --workspace' in capsys.readouterr().err
