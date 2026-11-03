#!/usr/bin/env python3
"""Check a collaborator host, then run the locked installer and doctor."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import sysconfig
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.attentionbench import installer


INSTALL_FREE_GB = 70


def workspace_path(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or path != path.resolve(strict=False):
        raise installer.StepError('workspace', 'workspace must be an absolute real path without symlinks',
                                  'Choose an absolute directory path with no symlink components.')
    return path


def _run(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SimpleNamespace(returncode=1, stdout='', stderr=str(exc))


def _free_port(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def _our_port(ws: Path, port: int, ui_port: int, memory_port: int) -> bool:
    name = 'ui' if port == ui_port else 'memory'
    pidfile = ws / 'data/run' / f'{name}.pid'
    try:
        pid = pidfile.read_text().strip()
        if not pid.isdigit():
            return False
        cmdline = (Path('/proc') / pid / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        return False
    expected = 'benchmarks.attention_harness.ui_server' if name == 'ui' else 'attention_memory_service'
    return expected in cmdline and str(ws) in cmdline


def _isolation_from_doctor(ws: Path) -> tuple[bool, str]:
    # Doctor owns the namespace probe and its policy. Read only its isolation row.
    parent = ws
    while not parent.exists():
        parent = parent.parent
    output = io.StringIO()
    with redirect_stdout(output):
        installer.doctor(SimpleNamespace(workspace=str(parent), json=True))
    row = next(row for row in json.loads(output.getvalue()) if row['check'] == 'isolation')
    return row['status'] == 'PASS', row['reason']


def uv_python_status(lock: dict) -> tuple[bool, str]:
    versions = sorted(set(lock['python'].values()))
    uv = shutil.which('uv')
    missing = [version for version in versions if not uv or _run([uv, 'python', 'find', version]).returncode]
    downloads = os.environ.get('UV_PYTHON_DOWNLOADS', '').lower()
    offline = os.environ.get('UV_OFFLINE', '').lower() not in ('', '0', 'false', 'no', 'off')
    allowed = downloads not in ('never', 'manual') and not offline
    return not missing or allowed, ('available or downloads allowed' if not missing or allowed else 'missing ' + ', '.join(missing) + '; downloads disabled')


def fix_userns() -> None:
    isolated, reason, _ = installer.namespace_status()
    if isolated or reason != installer.USERNS_REASON:
        print('AppArmor bwrap user namespace restriction not detected; continuing.')
        return
    if not sys.stdin.isatty():
        raise installer.StepError('fix-userns', 'stdin is not a TTY', 'Run setup interactively with --fix-userns.')
    bwrap = shutil.which('bwrap')
    if not bwrap or Path(bwrap).resolve() != Path('/usr/bin/bwrap'):
        raise installer.StepError('fix-userns', f'bwrap resolves to {bwrap}', 'Install /usr/bin/bwrap and rerun setup.')
    commands = (['sudo', 'install', '-m', '0644', str(installer.USERNS_PROFILE), installer.USERNS_TARGET],
                ['sudo', 'apparmor_parser', '-r', installer.USERNS_TARGET])
    print(installer.USERNS_PROFILE.read_text(), end='')
    for command in commands:
        print(' '.join(command))
    if input('Apply? [y/N] ').strip().lower() != 'y':
        print('No changes made; continuing.')
        return
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise installer.StepError('fix-userns', f'command exited {result.returncode}: {" ".join(command)}',
                                      'Inspect AppArmor and rerun setup with --fix-userns.')
    isolated, reason, _ = installer.namespace_status()
    print(f'bwrap user namespaces: {"available" if isolated else reason}')


def prerequisites(ws: Path, *, dry_run: bool, ui_port: int, memory_port: int, rebuild_planner: bool = False) -> bool:
    lock = installer.data()
    failed = False

    def check(name: str, ok: bool, detail: str, fix: str, *, warning: bool = False) -> None:
        nonlocal failed
        status = 'PASS' if ok else 'WARN' if warning else 'FAIL'
        print(f'{status} {name}: {detail}' + (f'; install: {fix}' if not ok else ''), flush=True)
        failed |= not ok and not warning

    check('Linux x86_64', platform.system() == 'Linux' and platform.machine() == 'x86_64',
          f'{platform.system()} {platform.machine()}', 'Install Ubuntu 22.04 x86_64.')
    nvidia = shutil.which('nvidia-smi')
    driver = _run([nvidia, '--query-gpu=driver_version', '--format=csv,noheader']) if nvidia else None
    check('NVIDIA driver', bool(driver and driver.returncode == 0 and driver.stdout.strip()),
          driver.stdout.strip() if driver and driver.returncode == 0 else 'nvidia-smi or driver unavailable',
          'Install a compatible NVIDIA driver: https://www.nvidia.com/Download/index.aspx', warning=dry_run)
    check('git', bool(shutil.which('git')), 'git available' if shutil.which('git') else 'git missing',
          'sudo apt install git')
    py_ok = sys.version_info >= (3, 10)
    headers = bool(shutil.which('python3-config')) or (Path(sysconfig.get_path('include') or '') / 'Python.h').is_file()
    check('Python 3.10+ headers', py_ok and headers, f'{sys.version.split()[0]}; headers {"found" if headers else "missing"}',
          'sudo apt install python3 python3-dev')
    check('C compiler', bool(shutil.which('cc')), 'cc available' if shutil.which('cc') else 'cc missing',
          'sudo apt install build-essential')
    check('uv', bool(shutil.which('uv')), 'uv available' if shutil.which('uv') else 'uv missing',
          'Install uv: https://docs.astral.sh/uv/getting-started/installation/')
    uv_ok, uv_reason = uv_python_status(lock)
    check('uv Python 3.10/3.11', uv_ok, uv_reason,
          'Unset UV_PYTHON_DOWNLOADS/UV_OFFLINE or install the locked Python versions with uv python install.')
    check('Conda', bool(shutil.which('conda')), 'conda available' if shutil.which('conda') else 'conda missing',
          'Install Miniconda: https://docs.conda.io/en/latest/miniconda.html')
    bwrap = shutil.which('bwrap')
    if bwrap:
        try:
            isolated, reason = _isolation_from_doctor(ws)
        except Exception as exc:
            isolated, reason = False, str(exc)
    else:
        isolated, reason = False, 'bwrap missing'
    in_container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
    check('bwrap user namespaces', isolated, reason,
          installer.USERNS_FIX if reason == installer.USERNS_REASON else
          'sudo apt install bubblewrap; enable unprivileged user namespaces (inspect kernel/container policy).'
          + (' Inside Docker, start the container as in the Docker appendix of'
             ' docs/attentionbench_collaborator_setup.md.' if in_container else ''))
    codex = shutil.which('codex')
    version = _run([codex, '--version']) if codex else None
    version_text = version.stdout.strip() if version and version.returncode == 0 else 'codex missing'
    check('Codex CLI version', version_text == lock['codex']['version'], version_text,
          f'Install {lock["codex"]["version"]}: https://developers.openai.com/codex/cli/', warning=dry_run)
    login = _run([codex, 'login', 'status']) if codex else None
    check('Codex login', bool(login and login.returncode == 0),
          'logged in' if login and login.returncode == 0 else 'not logged in',
          'codex login', warning=dry_run)
    parent = ws
    while not parent.exists():
        parent = parent.parent
    stat = os.statvfs(parent)
    free_gb = stat.f_bavail * stat.f_frsize / 1024**3
    # A fresh install peaked at 65 GB (V2, 2026-11-02): ~51 GB workspace plus ~15 GB uv/pip/conda
    # download caches. A workspace that is already set up only needs room for runs.
    need = 10 if (ws / 'paths.json').is_file() else INSTALL_FREE_GB
    if rebuild_planner and (ws / 'deployments').is_dir():
        deployments_gb = sum(file.stat().st_size for file in (ws / 'deployments').rglob('*') if file.is_file()) / 1024**3
        need = max(1, deployments_gb)
    check('disk', free_gb >= need, f'{free_gb:.1f} GB free at {parent}; setup needs {need} GB',
          f'Free at least {need} GB on the filesystem containing {parent}. A first install uses about '
          f'51 GB in the workspace plus about 15 GB of download caches; after setup you can reclaim the caches '
          f'with: uv cache clean; pip cache purge; conda clean -a')
    for name, port in (('UI port', ui_port), ('Memory port', memory_port)):
        free = _free_port(port)
        ours = not free and not rebuild_planner and (ws / 'paths.json').is_file() and _our_port(ws, port, ui_port, memory_port)
        check(name, free or ours, f'127.0.0.1:{port} {"free" if free else "our process" if ours else "busy"}',
              f'Stop the process using port {port} or rerun with --{name.lower().replace(" ", "-")} {port + 1}.')
    return not failed


def quota_readable() -> bool:
    """True only if a run started now can read the weekly quota: agent runs accept a reading
    at most WEEKLY_READING_MAX_AGE (10 min) old, so keep two minutes of margin."""
    try:
        from benchmarks.attention_harness.codex_quota import WEEKLY_READING_MAX_AGE
        from benchmarks.attention_harness.dev_agent_quota import read_weekly_quota
    except ImportError:
        return False
    try:
        reading = read_weekly_quota()
    except (OSError, ValueError):
        return False
    return time.time() - reading['observed_at'] <= WEEKLY_READING_MAX_AGE - 120


def warmup(no_codex_warmup: bool) -> None:
    if no_codex_warmup:
        print('Codex warm-up skipped: agent runs will stop with quota_unreadable until one Codex call is made.')
        return
    if quota_readable():
        print('Codex weekly quota reading found; warm-up skipped.')
        return
    print("Codex warm-up uses the collaborator's own Codex quota.", flush=True)
    result = _run(['codex', 'exec', '--skip-git-repo-check', 'Reply with ok.'], timeout=120)
    if result.returncode:
        raise installer.StepError('codex warm-up', (result.stderr or result.stdout).strip(),
                                  'Install the Codex CLI, run codex login and one short codex exec call, then rerun this command.')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', required=True)
    p.add_argument('--assets-from')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-codex-warmup', action='store_true')
    p.add_argument('--update', action='store_true')
    p.add_argument('--skip-planner', action='store_true')
    p.add_argument('--fix-userns', action='store_true')
    p.add_argument('--rebuild-planner', action='store_true')
    p.add_argument('--planner-project-from')
    p.add_argument('--planner-native-assets-from')
    p.add_argument('--ui-port', type=int, default=8769)
    p.add_argument('--memory-port', type=int, default=8878)
    args = p.parse_args(argv)
    try:
        ws = workspace_path(args.workspace)
        if args.rebuild_planner:
            installer.validate_rebuild(ws, args)
        if args.fix_userns and not args.dry_run:
            fix_userns()
        if not prerequisites(ws, dry_run=args.dry_run, ui_port=args.ui_port, memory_port=args.memory_port,
                             rebuild_planner=args.rebuild_planner):
            raise installer.StepError('prerequisites', 'one or more required checks failed',
                                      'Run the install commands above and rerun setup.')
        if not args.dry_run:
            warmup(args.no_codex_warmup)
        bootstrap_args = SimpleNamespace(**vars(args))
        installer.bootstrap(bootstrap_args)
        if args.dry_run:
            return 0
        if installer.doctor(SimpleNamespace(workspace=str(ws), json=False)):
            raise installer.StepError('doctor', 'doctor reported failed checks',
                                      'Repair each FAIL item printed above and rerun setup.')
        return 0
    except installer.StepError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(installer.StepError('setup', str(exc), 'Inspect the error and rerun setup.'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
