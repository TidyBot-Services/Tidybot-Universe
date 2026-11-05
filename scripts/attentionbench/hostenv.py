"""One clean process environment and port choice for a workspace. Standard library only.

Lab machines export PYTHONPATH (ROS), LD_LIBRARY_PATH, an active Conda, PIP_*, UV_*, CC or CUDA_HOME
from their shell profiles. None of that should reach an install or a service, so the environment is
built from an allow list: how to reach the network, which GPU to use, where the user's Codex home and
provider keys are, and this project's own settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket

BASE_PATH = ('/usr/local/sbin', '/usr/local/bin', '/usr/sbin', '/usr/bin', '/sbin', '/bin')
KEEP = frozenset({
    'HOME', 'USER', 'LOGNAME', 'LANG', 'LANGUAGE', 'TERM', 'TZ', 'TMPDIR',
    'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME',
    'http_proxy', 'https_proxy', 'ftp_proxy', 'no_proxy', 'all_proxy',
    'HTTP_PROXY', 'HTTPS_PROXY', 'FTP_PROXY', 'NO_PROXY', 'ALL_PROXY',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
    'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_TRUSTED_HOST',
    'UV_DEFAULT_INDEX', 'UV_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL', 'UV_NATIVE_TLS',
    'UV_OFFLINE', 'UV_PYTHON_DOWNLOADS', 'UV_PYTHON_INSTALL_MIRROR', 'HF_ENDPOINT',
    'CUDA_VISIBLE_DEVICES', 'MUJOCO_EGL_DEVICE_ID', 'CODEX_HOME', 'PARCC_URL',
    'PARCC_API_KEY', 'LITELLM_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY',
})
KEEP_PREFIXES = ('LC_', 'NVIDIA_', 'ATTENTION_', 'ATTENTIONBENCH_', 'TIDYBOT_')
DEFAULT_UI_PORT = 8769
DEFAULT_MEMORY_PORT = 8878


def clean_env(ws: Path | None = None, environ=None) -> dict:
    """The environment for this workspace's child processes; tools in the workspace come first."""
    source = os.environ if environ is None else environ
    env = {k: v for k, v in source.items() if k in KEEP or k.startswith(KEEP_PREFIXES)}
    env.setdefault('HOME', str(Path.home()))
    env.setdefault('LANG', 'C.UTF-8')
    env.update(PYTHONNOUSERSITE='1', PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               UV_NO_CONFIG='1', UV_PYTHON_PREFERENCE='only-managed')
    paths = list(BASE_PATH)
    if ws is not None:
        ws = Path(ws)
        tools, cache = ws / 'tools', ws / 'cache'
        paths = [str(tools / 'bin'), str(tools / 'codex/bin'), str(tools / 'conda/condabin'), *paths,
                 # Last: Codex's own bwrap serves only when the system has none.
                 str(tools / 'codex/codex-resources')]
        env.update(ATTENTIONBENCH_WORKSPACE=str(ws), UV_CACHE_DIR=str(cache / 'uv'), PIP_CACHE_DIR=str(cache / 'pip'),
                   CONDA_PKGS_DIRS=str(cache / 'conda'), UV_PYTHON_INSTALL_DIR=str(tools / 'python'))
    env['PATH'] = os.pathsep.join(paths)
    return env


def apply(ws: Path | None = None) -> None:
    """Replace this process's environment, so every child inherits the clean one."""
    env = clean_env(ws)
    os.environ.clear()
    os.environ.update(env)


def default_workspace(repo: Path) -> str | None:
    """The workspace a checkout belongs to, when it sits at WS/repos/attentionbench."""
    return str(repo.parents[1]) if repo.parent.name == 'repos' and repo.name == 'attentionbench' else None


def port_free(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def owned(ws: Path, name: str) -> bool:
    """True when this workspace's own UI or Memory process is alive."""
    try:
        pid = (ws / 'data/run' / f'{name}.pid').read_text().strip()
        if not pid.isdigit():
            return False
        cmdline = (Path('/proc') / pid / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        return False
    expected = 'benchmarks.attention_harness.ui_server' if name == 'ui' else 'attention_memory_service'
    return expected in cmdline and str(ws) in cmdline


def saved_ports(ws: Path) -> tuple[int, int]:
    try:
        value = json.loads((ws / 'data/run/ports.json').read_text())
        return int(value['ui']), int(value['memory'])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_UI_PORT, DEFAULT_MEMORY_PORT


def next_free(port: int, taken: tuple = ()) -> int:
    for candidate in range(port, port + 200):
        if candidate not in taken and port_free(candidate):
            return candidate
    raise RuntimeError(f'no free loopback port between {port} and {port + 199}')


def choose_ports(ws: Path, ui: int | None = None, memory: int | None = None) -> tuple[int, int]:
    """Explicit ports stay as given; otherwise keep the workspace's ports, moving off busy ones."""
    saved_ui, saved_memory = saved_ports(ws)
    if ui is None:
        ui = saved_ui if owned(ws, 'ui') else next_free(saved_ui, () if memory is None else (memory,))
    if memory is None:
        memory = saved_memory if owned(ws, 'memory') else next_free(saved_memory, (ui,))
    return ui, memory


def save_ports(ws: Path, ui: int, memory: int) -> None:
    file = ws / 'data/run/ports.json'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({'ui': ui, 'memory': memory}) + '\n')
