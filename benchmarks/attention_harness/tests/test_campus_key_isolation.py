"""Campus key selection and sandbox canaries, without sockets or model processes."""
import ast
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_isolation as isolation
from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge
from benchmarks.attention_harness import dev_ui_server as ui


@pytest.fixture
def home_key(tmp_path, monkeypatch):
    home = tmp_path / 'home';home.mkdir()
    (home / '.litellm-key').write_text('decoy-home-key')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('PARCC_API_KEY', raising=False)
    monkeypatch.delenv('LITELLM_KEY', raising=False)
    monkeypatch.delenv('ATTENTION_NO_HOME_LAB_KEY', raising=False)
    return home


@pytest.mark.parametrize('name', [None, 'PARCC_API_KEY', 'LITELLM_KEY'])
def test_no_home_key_flag_uses_only_environment(home_key, monkeypatch, name):
    monkeypatch.setenv('ATTENTION_NO_HOME_LAB_KEY', '1')
    if name:monkeypatch.setenv(name, 'environment-key')
    is_file, read_text = Path.is_file, Path.read_text
    def stat(path):
        assert path != home_key / '.litellm-key', 'must not inspect the home key'
        return is_file(path)
    def read(path, *args, **kwargs):
        assert path != home_key / '.litellm-key', 'must not read the home key'
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'is_file', stat)
    monkeypatch.setattr(Path, 'read_text', read)
    defaults = ui.provider_defaults(public_gateway_mode=True)
    parcc = defaults['providers']['parcc']
    assert parcc['available'] is bool(name)
    assert defaults['roles']['dev'] == parcc
    if name:
        assert bridge.parcc_key() == 'environment-key'
        assert parcc['reason'] == ''
    else:
        assert parcc['reason']
        # Key lookup refuses a PARCC call, but defaults/startup do not call it or exit.
        with pytest.raises(bridge.BridgeError, match='parcc_key_missing'):bridge.parcc_key()


def test_home_key_fallback_remains_available_without_flag(home_key):
    assert bridge.parcc_key() == 'decoy-home-key'
    assert ui.provider_defaults(home=home_key)['providers']['parcc']['available'] is True


def test_explicit_launcher_modes_report_no_parcc_without_key(home_key, tmp_path, monkeypatch):
    from remote_gateway.deploy import run_host_harness as launcher
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'scripts/attentionbench'))
    credentials = tmp_path / 'credentials';credentials.mkdir()
    (credentials / 'parcc-key').write_text('')
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(credentials))
    for source in ('stdin', 'credential', 'none'):
        monkeypatch.setattr('sys.stdin', io.StringIO(''))
        env = launcher.build_env(tmp_path, key_source=source)
        defaults = ui.provider_defaults(public_gateway_mode=True, home=home_key, getenv=env.get)
        assert defaults['providers']['parcc']['available'] is False
        assert defaults['providers']['parcc']['reason']


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    ws, private = tmp_path / 'workspace', tmp_path / 'private'
    ws.mkdir();private.mkdir()
    monkeypatch.setattr(isolation.shutil, 'which', lambda _: '/fake/bwrap')
    monkeypatch.setattr(isolation, 'real_binary', lambda:Path('/fake/codex'))
    monkeypatch.setattr(isolation, 'codex_home', lambda:tmp_path / 'codex-home')
    return ws, private


@pytest.mark.parametrize('visible', [False, True])
def test_extra_hidden_paths_are_stat_canaries_and_fail_closed(sandbox, tmp_path, monkeypatch, visible):
    ws, private = sandbox
    hidden = [str(tmp_path / 'gateway-state'), str(tmp_path / 'gateway-state/gateway.secret'),
              str(tmp_path / "credentials/quote's-key")]
    monkeypatch.setenv('ATTENTION_PREFLIGHT_HIDDEN_PATHS', os.pathsep + os.pathsep.join(hidden) + os.pathsep * 2)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[:2] == ['codex', 'sandbox']
        assert kwargs['stdin'] == subprocess.DEVNULL
        script = command[-1]
        assignment = next(node for node in ast.parse(script).body if isinstance(node, ast.Assign) and
                          isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'paths')
        paths = ast.literal_eval(assignment.value)
        assert paths[-len(hidden):] == hidden and '' not in paths
        # This expression checks visibility via stat, without reading hidden files.
        assert 'all(not os.path.exists(p) for p in paths)' in script
        checks = dict(outside_hidden=not visible, home_listing_restricted=True, workspace_rw=True,
                      shared_readonly=True, network_denied=True)
        return SimpleNamespace(returncode=0, stdout=json.dumps(checks), stderr='')
    if visible:
        with pytest.raises(isolation.IsolationUnavailable, match='boundary_check_failed'):
            isolation.verify_runtime(ws, private, planner_port=18999, run=run)
    else:
        isolation.verify_runtime(ws, private, planner_port=18999, run=run)
    assert len(calls) == 1


@pytest.mark.parametrize('path', ['relative/gateway-state', '~/gateway.secret', '/absolute:relative'])
def test_relative_hidden_path_refuses_before_running_sandbox(sandbox, monkeypatch, path):
    monkeypatch.setenv('ATTENTION_PREFLIGHT_HIDDEN_PATHS', path)
    def forbidden(*args, **kwargs):raise AssertionError('must not execute a process')
    with pytest.raises(isolation.IsolationUnavailable, match='must_be_absolute'):
        isolation.verify_runtime(*sandbox, planner_port=18999, run=forbidden)
