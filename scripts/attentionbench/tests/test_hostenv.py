"""A workspace's processes see a clean environment and its own ports."""
import socket
from pathlib import Path

import pytest

from scripts.attentionbench import hostenv


def test_lab_shell_settings_do_not_reach_the_workspace(tmp_path):
    env = hostenv.clean_env(tmp_path, {
        'HOME': '/home/x', 'PATH': '/opt/ros/humble/bin:/home/x/miniforge3/bin', 'PYTHONPATH': '/opt/ros/humble/lib',
        'LD_LIBRARY_PATH': '/opt/ros/lib', 'CONDA_PREFIX': '/home/x/miniforge3', 'VIRTUAL_ENV': '/v', 'PYTHONHOME': '/p',
        'CC': 'clang', 'CUDA_HOME': '/usr/local/cuda', 'PIP_USER': '1', 'UV_PROJECT_ENVIRONMENT': 'x', 'ROS_DISTRO': 'humble'})
    assert not {'PYTHONPATH', 'LD_LIBRARY_PATH', 'CONDA_PREFIX', 'VIRTUAL_ENV', 'PYTHONHOME', 'CC', 'CUDA_HOME',
                'PIP_USER', 'UV_PROJECT_ENVIRONMENT', 'ROS_DISTRO'} & set(env)
    assert 'miniforge3' not in env['PATH'] and 'ros' not in env['PATH']
    assert env['PYTHONNOUSERSITE'] == '1' and env['UV_PYTHON_PREFERENCE'] == 'only-managed'


def test_network_gpu_and_key_settings_pass_through():
    kept = {'https_proxy': 'http://p', 'NO_PROXY': 'x', 'SSL_CERT_FILE': '/c', 'PIP_INDEX_URL': 'http://m', 'HF_ENDPOINT': 'http://h',
            'CUDA_VISIBLE_DEVICES': '1', 'CODEX_HOME': '/c', 'DEEPSEEK_API_KEY': 'k', 'ATTENTION_DEV_V2_RUN_MIN_FREE_GB': '5', 'LC_ALL': 'C'}
    env = hostenv.clean_env(None, {'HOME': '/h', **kept})
    assert {key: env[key] for key in kept} == kept


def test_workspace_tools_come_first_and_the_bundled_bwrap_last(tmp_path):
    paths = hostenv.clean_env(tmp_path, {'HOME': '/h'})['PATH'].split(':')
    assert paths[:3] == [str(tmp_path / 'tools/bin'), str(tmp_path / 'tools/codex/bin'), str(tmp_path / 'tools/conda/condabin')]
    assert paths[-1] == str(tmp_path / 'tools/codex/codex-resources') and '/usr/bin' in paths


def test_caches_live_in_the_workspace(tmp_path):
    env = hostenv.clean_env(tmp_path, {'HOME': '/h'})
    assert all(env[name].startswith(str(tmp_path / 'cache')) for name in ('UV_CACHE_DIR', 'PIP_CACHE_DIR', 'CONDA_PKGS_DIRS'))


def test_default_workspace_only_for_the_expected_layout():
    assert hostenv.default_workspace(Path('/w/repos/attentionbench')) == '/w'
    assert hostenv.default_workspace(Path('/elsewhere/Tidybot-Universe')) is None


@pytest.fixture
def busy_port():
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', 0))
        except PermissionError:
            pytest.skip('loopback sockets are blocked by this sandbox')
        sock.listen()
        yield sock.getsockname()[1]


def test_a_busy_port_moves_to_the_next_free_one_and_is_remembered(tmp_path, busy_port):
    hostenv.save_ports(tmp_path, busy_port, busy_port + 100)
    ui, memory = hostenv.choose_ports(tmp_path)
    assert ui != busy_port and ui > busy_port and memory != ui
    hostenv.save_ports(tmp_path, ui, memory)
    assert hostenv.saved_ports(tmp_path) == (ui, memory)


def test_explicit_ports_are_kept_as_given(tmp_path, busy_port):
    assert hostenv.choose_ports(tmp_path, busy_port, busy_port + 1) == (busy_port, busy_port + 1)


def test_ui_and_memory_never_share_a_port(tmp_path, monkeypatch):
    monkeypatch.setattr(hostenv, 'port_free', lambda port: True)
    hostenv.save_ports(tmp_path, 9000, 9000)
    ui, memory = hostenv.choose_ports(tmp_path)
    assert ui != memory


def test_saved_ports_default_when_unreadable(tmp_path):
    (tmp_path / 'data/run').mkdir(parents=True)
    (tmp_path / 'data/run/ports.json').write_text('not json')
    assert hostenv.saved_ports(tmp_path) == (hostenv.DEFAULT_UI_PORT, hostenv.DEFAULT_MEMORY_PORT)
