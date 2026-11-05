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
    for entry in (setup.main, run.main, setup.verify.main):
        with pytest.raises(SystemExit) as exc:
            entry(['--help'])
        assert exc.value.code == 0
        help_text = capsys.readouterr().out
        assert not re.search(r'--[\w-]*(?:exempt|relax|skip-verif|physical|identity|provenance|movable)', help_text)


@pytest.fixture
def fake_prereqs(tmp_path, monkeypatch):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name in ('nvidia-smi', 'git', 'cc', 'g++', 'ffmpeg', 'ffprobe', 'bwrap'):
        file = bindir / name
        file.write_text('#!/bin/sh\nexit 0\n')
        file.chmod(0o755)
    monkeypatch.setenv('PATH', str(bindir))
    monkeypatch.setattr(setup, '_isolation', lambda: (True, 'probe passed'))
    monkeypatch.setattr(setup.installer, 'missing_libraries', lambda: [])
    monkeypatch.setattr(setup, '_free_port', lambda port: True)
    monkeypatch.setattr(setup, '_run', lambda argv, timeout=10: SimpleNamespace(
        returncode=0, stdout='580.95\n' if Path(argv[0]).name == 'nvidia-smi' else
        'codex-cli 0.165.0\n' if '--version' in argv else '', stderr=''))
    monkeypatch.setattr(setup.installer, 'host_fingerprint', lambda: {
        'driver': '580.95', 'glibc': 'glibc 2.39', 'os': {'VERSION_ID': '24.04'}})
    monkeypatch.setattr(setup.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=100, f_frsize=1024**3))
    return bindir, tmp_path / 'workspace'


def test_uv_conda_and_codex_are_not_prerequisites(fake_prereqs, capsys):
    _, ws = fake_prereqs
    assert setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    output = capsys.readouterr().out
    assert 'FAIL' not in output and 'Codex' not in output and 'Conda' not in output


@pytest.mark.parametrize('missing', ['ffmpeg', 'bwrap'])
def test_a_tool_with_a_workspace_fallback_only_warns(fake_prereqs, capsys, missing):
    bindir, ws = fake_prereqs
    (bindir / missing).unlink()
    assert setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'WARN' in capsys.readouterr().out


def test_missing_system_library_names_its_package(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.installer, 'missing_libraries', lambda: ['libEGL.so.1'])
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'sudo apt-get install libegl1' in capsys.readouterr().out


@pytest.mark.parametrize('missing', ['nvidia-smi', 'git', 'cc', 'g++'])
def test_prerequisite_missing_executable_has_install_instruction(fake_prereqs, capsys, missing):
    bindir, ws = fake_prereqs
    (bindir / missing).unlink()
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    failures = [line for line in capsys.readouterr().out.splitlines() if line.startswith('FAIL')]
    assert failures and all('; install: ' in line for line in failures)


def test_a_declined_apparmor_change_does_not_stop_the_install(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup, '_isolation', lambda: (False, setup.installer.USERNS_REASON))
    assert setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'WARN bwrap user namespaces' in capsys.readouterr().out
    monkeypatch.setattr(setup, '_isolation', lambda: (False, 'bwrap namespace probe failed: denied'))
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'FAIL bwrap user namespaces' in capsys.readouterr().out


def test_prerequisites_disk_and_port_and_dry_warning(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=10, f_frsize=1024**3))
    monkeypatch.setattr(setup, '_free_port', lambda port: port != 8769)
    assert not setup.prerequisites(ws, dry_run=True, ui_port=8769, memory_port=8878)
    output = capsys.readouterr().out
    assert 'FAIL disk:' in output and 'WARN UI port:' in output and 'next free port' in output
    assert all('; install:' in line for line in output.splitlines() if line.startswith('FAIL'))
    assert f'setup needs {setup.installer.INSTALL_FREE_GB} GB' in output


def test_disk_need_is_lower_for_an_installed_workspace(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.os, 'statvfs', lambda path: SimpleNamespace(f_bavail=40, f_frsize=1024**3))
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'FAIL disk:' in capsys.readouterr().out
    ws.mkdir()
    (ws / 'paths.json').write_text('{}')
    assert setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    assert 'PASS disk:' in capsys.readouterr().out


def test_defaults_and_summary_paths(tmp_path):
    assert run.parser().parse_args(['--workspace', str(tmp_path)]).minutes == 45
    result = run.summary(tmp_path, 'devexp-' + 'a' * 24, 'k', {'state': 'cancelled', 'cells': []})
    assert all(Path(p).is_file() for p in result['l3_files'])


def test_prerequisites_platform_and_python(fake_prereqs, monkeypatch, capsys):
    _, ws = fake_prereqs
    monkeypatch.setattr(setup.platform, 'machine', lambda: 'aarch64')
    monkeypatch.setattr(setup.sys, 'version_info', (3, 9))
    assert not setup.prerequisites(ws, dry_run=False, ui_port=8769, memory_port=8878)
    output = capsys.readouterr().out
    assert 'FAIL Linux x86_64:' in output and 'FAIL Python 3.10+:' in output
    assert all('; install:' in line for line in output.splitlines() if line.startswith('FAIL'))


def test_dry_run_creates_nothing(tmp_path, monkeypatch):
    ws = tmp_path / 'absent'
    monkeypatch.setattr(setup, 'prepare_host', lambda dry_run: None)
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
    values = dict(workspace=str(ws), task='pick_place_counter_to_sink', seed=101, model='codex',
                  dev_model=None, eval_model=None, advisor_model=None, base_url=None, api_key_env=None,
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
    def post(url, token, body, timeout=15):
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


def test_create_waits_longer_than_the_ui_create_subprocess(monkeypatch):
    # V2 (2026-11-02): the UI's create step took longer than 15 s; it allows its subprocess 60 s.
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{}'

    def urlopen(request, timeout):
        seen[request.full_url] = timeout
        return Response()

    monkeypatch.setattr(run.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(run.json, 'load', lambda response: {})
    run.post_json('http://x/api/dev-v2/experiments', 't', {}, timeout=run.CREATE_TIMEOUT)
    run.post_json('http://x/api/dev-v2/experiments/e/cancel', 't', {})
    assert run.CREATE_TIMEOUT >= 120
    assert seen == {'http://x/api/dev-v2/experiments': run.CREATE_TIMEOUT,
                    'http://x/api/dev-v2/experiments/e/cancel': 15}


@pytest.mark.parametrize('name', ['with space', 'a:b', 'a#b'])
def test_workspace_paths_conda_cannot_use_fail_at_once(tmp_path, name):
    with pytest.raises(setup.installer.StepError, match='space, colon or #'):
        setup.workspace_path(str(tmp_path / name))


def test_workspace_path_too_long_fails_at_once():
    with pytest.raises(setup.installer.StepError, match='longer than'):
        setup.workspace_path('/tmp/' + 'x' * setup.MAX_WORKSPACE_BYTES)


def test_workspace_through_a_symlink_is_resolved(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    (tmp_path / 'link').symlink_to(real)
    assert setup.workspace_path(str(tmp_path / 'link/ws')) == real / 'ws'


def test_relative_workspace_is_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert setup.workspace_path('ws') == tmp_path.resolve() / 'ws'


def test_a_checkout_elsewhere_moves_into_the_workspace_and_restarts(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    commands, restarts = [], []
    monkeypatch.setattr(setup, '_run', lambda argv, timeout=60: SimpleNamespace(
        stdout='https://example.test/repo.git\n' if 'get-url' in argv else 'abc1234\n', returncode=0))
    monkeypatch.setattr(setup.installer, 'command', lambda step, argv, **kw: commands.append(argv))
    monkeypatch.setattr(setup.os, 'execv', lambda path, argv: restarts.append(argv))
    setup.adopt_checkout(ws, ['--workspace', str(ws)])
    assert commands[0][:2] == ['git', 'clone'] and commands[0][-1] == str(ws / 'repos/attentionbench')
    assert ['git', '-C', str(ws / 'repos/attentionbench'), 'checkout', '--detach', 'abc1234'] in commands
    assert commands[-1][-1] == 'https://example.test/repo.git'
    assert restarts[0][1:4] == ['-I', '-u', str(ws / 'repos/attentionbench/scripts/attentionbench/setup.py')]


def test_a_checkout_already_in_place_stays(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    monkeypatch.setattr(setup, 'ROOT', ws / 'repos/attentionbench')
    monkeypatch.setattr(setup.os, 'execv', lambda *a: pytest.fail('restarted'))
    setup.adopt_checkout(ws, [])


def successful_setup(monkeypatch, ws, *, verify_failures=0, logged_in=False):
    calls = []
    monkeypatch.setattr(setup, 'adopt_checkout', lambda ws, argv: None)
    monkeypatch.setattr(setup, 'prepare_host', lambda dry_run: None)
    monkeypatch.setattr(setup, 'prerequisites', lambda *a, **k: True)
    monkeypatch.setattr(setup.installer, 'bootstrap', lambda args: calls.append('bootstrap'))
    monkeypatch.setattr(setup.installer, 'doctor', lambda args: calls.append('doctor') or 0)
    monkeypatch.setattr(setup.installer, 'clean_caches', lambda ws: calls.append('clean'))
    monkeypatch.setattr(setup.verify, 'run', lambda ws: calls.append('verify') or verify_failures)
    monkeypatch.setattr(setup.routes, 'codex_logged_in', lambda: logged_in)
    monkeypatch.setattr(setup.routes, 'available', lambda: ['codex'] if logged_in else [])
    monkeypatch.setattr(setup, 'warmup', lambda no_warmup: calls.append('warmup'))
    return calls


def test_setup_needs_no_model_account(tmp_path, monkeypatch, capsys):
    calls = successful_setup(monkeypatch, tmp_path)
    assert setup.main(['--workspace', str(tmp_path)]) == 0
    assert calls == ['bootstrap', 'doctor', 'verify', 'clean']
    output = capsys.readouterr().out
    assert 'No model route is ready' in output and 'deepseek' in output


def test_a_failed_self_test_keeps_the_caches_and_fails(tmp_path, monkeypatch, capsys):
    calls = successful_setup(monkeypatch, tmp_path, verify_failures=1)
    assert setup.main(['--workspace', str(tmp_path)]) == 1
    assert 'clean' not in calls and 'self-test' in capsys.readouterr().err


def test_a_failed_warm_up_does_not_fail_an_installed_workspace(tmp_path, monkeypatch, capsys):
    successful_setup(monkeypatch, tmp_path, logged_in=True)
    def fail(no_warmup):
        raise setup.installer.StepError('codex warm-up', 'quota', 'later')
    monkeypatch.setattr(setup, 'warmup', fail)
    assert setup.main(['--workspace', str(tmp_path)]) == 0
    assert 'WARN' in capsys.readouterr().out


def route_args(**changes):
    values = dict(api_key_env=None, base_url=None, dev_model=None, eval_model=None, advisor_model=None)
    values.update(changes)
    return argparse.Namespace(**values)


def provider_server(monkeypatch, answers):
    """answers maps (role, model) to the /test reply; anything else passes."""
    calls = []
    def post(url, token, body, timeout=15, method='POST'):
        calls.append((method, url, body))
        if url.endswith('/test'):
            return answers.get((body['role'], body['model']), {'ok': True, 'checks': {}})
        return {'ok': True}
    monkeypatch.setattr(run, 'post_json', post)
    return calls


BLIND = {'ok': False, 'checks': {'auth': 'ok', 'chat': 'ok', 'vision': 'fail'}, 'errors': [{'message': 'no images'}]}
DOWN = {'ok': False, 'checks': {'auth': 'fail', 'chat': 'fail'}, 'errors': [{'message': 'unreachable'}]}


def test_key_goes_to_the_ui_and_never_into_the_request(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'secret-value')
    calls = provider_server(monkeypatch, {})
    provider, warnings = run.prepare_route('http://ui', 't', run.routes.ROUTES['deepseek'], route_args())
    assert calls[0][0] == 'PUT' and calls[0][2] == {'provider': 'deepseek', 'key': 'secret-value'}
    assert [body['role'] for method, url, body in calls[1:]] == ['dev', 'eval', 'advisor']
    assert 'secret-value' not in json.dumps(provider) and warnings == []
    assert provider['roles']['eval'] == {'credential': 'run-deepseek', 'provider': 'deepseek', 'model': 'deepseek-flash'}


def test_a_model_that_cannot_read_images_runs_with_a_warning(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'k')
    provider_server(monkeypatch, {('eval', 'blind'): BLIND, ('advisor', 'blind'): BLIND})
    provider, warnings = run.prepare_route('http://ui', 't', run.routes.ROUTES['deepseek'],
                                           route_args(dev_model='blind', eval_model='blind', advisor_model='blind'))
    assert provider['roles']['eval']['vision'] is False and provider['roles']['advisor']['vision'] is False
    assert 'vision' not in provider['roles']['dev']
    assert any('Eval did not see images' in line for line in warnings)
    assert any('Advisor did not see images' in line for line in warnings)


def test_dev_has_to_pass_every_check(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'k')
    provider_server(monkeypatch, {('dev', 'deepseek-flash'): BLIND})
    with pytest.raises(setup.installer.StepError, match='dev check'):
        run.prepare_route('http://ui', 't', run.routes.ROUTES['deepseek'], route_args())


def test_a_default_advisor_model_that_is_down_falls_back_to_dev_with_a_warning(monkeypatch, tmp_path):
    monkeypatch.setenv('PARCC_API_KEY', 'k')
    provider_server(monkeypatch, {('advisor', 'parcc/Qwen'): DOWN,
                                  ('eval', 'parcc/GLM'): BLIND, ('advisor', 'parcc/GLM'): BLIND})
    provider, warnings = run.prepare_route('http://ui', 't', run.routes.ROUTES['parcc'], route_args())
    assert provider['roles']['eval']['model'] == 'parcc/GLM' and provider['roles']['eval']['vision'] is False
    assert provider['roles']['advisor']['model'] == 'parcc/GLM' and provider['roles']['advisor']['vision'] is False
    assert any('unreachable' in line for line in warnings)


def test_the_parcc_route_gives_each_role_a_model_lab_runs_allow():
    from benchmarks.attention_harness.dev_agent_config import PARCC_MODELS
    assert all(model in PARCC_MODELS[role] for role, model in run.routes.ROUTES['parcc'].models.items())


def test_a_named_model_that_fails_is_an_error(monkeypatch):
    monkeypatch.setenv('PARCC_API_KEY', 'k')
    provider_server(monkeypatch, {('eval', 'parcc/Qwen'): DOWN})
    with pytest.raises(setup.installer.StepError, match='unreachable'):
        run.prepare_route('http://ui', 't', run.routes.ROUTES['parcc'], route_args(eval_model='parcc/Qwen'))


def test_a_route_without_a_key_says_how_to_add_one(monkeypatch, tmp_path):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    with pytest.raises(setup.installer.StepError, match='DEEPSEEK_API_KEY'):
        run.prepare_route('http://ui', 't', run.routes.ROUTES['deepseek'], route_args())


def test_auto_route_prefers_a_logged_in_codex_then_keys(monkeypatch):
    monkeypatch.setattr(run.routes, 'available', lambda: ['codex', 'deepseek'])
    assert run.routes.choose('auto').name == 'codex'
    monkeypatch.setattr(run.routes, 'available', lambda: ['deepseek'])
    assert run.routes.choose('auto').name == 'deepseek'
    monkeypatch.setattr(run.routes, 'available', lambda: [])
    with pytest.raises(LookupError, match='codex login'):
        run.routes.choose('auto')


def test_key_comes_from_a_named_variable_or_the_key_file(monkeypatch, tmp_path):
    (tmp_path / '.litellm-key').write_text('from-file\n')
    monkeypatch.delenv('PARCC_API_KEY', raising=False)
    monkeypatch.delenv('LITELLM_KEY', raising=False)
    assert run.routes.find_key(run.routes.ROUTES['parcc'], home=tmp_path) == 'from-file'
    monkeypatch.setenv('MY_KEY', 'from-env')
    assert run.routes.find_key(run.routes.ROUTES['custom'], 'MY_KEY') == 'from-env'


def test_openai_and_custom_need_a_model_name():
    with pytest.raises(ValueError, match='--dev-model'):
        run.routes.role_models(run.routes.ROUTES['openai'], None, None, None)
    assert run.routes.role_models(run.routes.ROUTES['custom'], 'm', None, 'a') == {'dev': 'm', 'eval': 'm', 'advisor': 'a'}


def test_a_named_key_variable_survives_the_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('MY_KEY', 'k')
    assert run.hostenv.clean_env(tmp_path, keep=('MY_KEY',))['MY_KEY'] == 'k'
    assert 'MY_KEY' not in run.hostenv.clean_env(tmp_path)


def test_long_own_key_runs_stay_inside_the_agent_budget():
    roles = {'dev': {}}
    assert run.request_body('pick_place_counter_to_sink', 101, 600, provider={'roles': roles})['selection']['agent_limits']['wall_seconds'] == 21600


def test_custom_route_needs_its_three_options(tmp_path):
    with pytest.raises(setup.installer.StepError, match='--base-url'):
        run.execute(args(tmp_path, 8769, model='custom'))


def test_run_on_a_key_route_skips_codex_and_records_the_warning(server, tmp_path, monkeypatch):
    actions = []
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'k')
    monkeypatch.setattr(run, 'command', lambda action, *a: actions.append(action))
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: pytest.fail('a key route must not call Codex'))
    roles = {role: {'credential': 'run-deepseek', 'provider': 'deepseek', 'model': 'm'} for role in run.routes.ROLES}
    roles['eval']['vision'] = False
    monkeypatch.setattr(run, 'prepare_route', lambda *a: ({'roles': roles}, ['Eval did not see images: m cannot read them.']))
    assert run.execute(args(tmp_path, server.server_port, model='deepseek')) == 0
    assert actions == ['start', 'stop']
    selection = Handler.posts[0][3]['selection']
    assert selection['agent_provider']['roles']['eval']['vision'] is False
    assert selection['agent_limits'] == {'versions': 2, 'wall_seconds': 3600}
    result = json.loads((tmp_path / 'data/run' / (Handler.eid + '-summary.json')).read_text())
    assert result['model_route'] == 'deepseek' and 'Eval did not see images' in result['warnings'][0]


def test_a_missed_poll_does_not_end_the_run(tmp_path, monkeypatch, capsys):
    (tmp_path / 'paths.json').write_text('{}')
    monkeypatch.setattr(run, 'command', lambda *a: None)
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: None)
    monkeypatch.setattr(run.control, 'control_token', lambda url: 't')
    monkeypatch.setattr(run, 'post_json', lambda *a, **k: {'experiment_id': Handler.eid, 'state': 'running'})
    monkeypatch.setattr(run.time, 'sleep', lambda seconds: None)
    replies = [TimeoutError('slow'), OSError('reset'), {'state': 'completed', 'cells': []}]
    def get(url):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(run, 'get_json', get)
    assert run.execute(args(tmp_path, 8769)) == 0
    assert capsys.readouterr().out.count('trying again') == 2


def test_a_ui_that_stays_down_ends_the_run_with_a_reason(tmp_path, monkeypatch):
    (tmp_path / 'paths.json').write_text('{}')
    stopped = []
    monkeypatch.setattr(run, 'command', lambda action, *a: stopped.append(action))
    monkeypatch.setattr(run.setup, 'warmup', lambda no_warmup: None)
    monkeypatch.setattr(run.control, 'control_token', lambda url: 't')
    monkeypatch.setattr(run, 'post_json', lambda *a, **k: {'experiment_id': Handler.eid, 'state': 'running'})
    monkeypatch.setattr(run.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(run, 'get_json', lambda url: (_ for _ in ()).throw(OSError('refused')))
    with pytest.raises(RuntimeError, match='stopped answering'):
        run.execute(args(tmp_path, 8769))
    assert stopped[-1] == 'stop'


@pytest.mark.parametrize('roles,source', [
    ({'dev': 'deepseek-flash', 'eval': 'deepseek-flash', 'advisor': 'deepseek-flash'}, 'byok'),
])
def test_a_key_route_request_passes_the_agent_configuration(roles, source):
    from benchmarks.attention_harness import dev_agent_config as config
    described = {role: {'credential': 'run-deepseek', 'provider': 'deepseek', 'model': model} for role, model in roles.items()}
    described['eval']['vision'] = False
    selection = run.request_body('pick_place_counter_to_sink', 101, 45, provider={'roles': described})['selection']
    frozen = config.normalize(selection)
    assert frozen['agent_provider']['roles']['eval']['vision'] is False
    assert frozen['agent_limits'] == {'versions': 2, 'wall_seconds': 3600}
    assert config.display_source(selection) == source
