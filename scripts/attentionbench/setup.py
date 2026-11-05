#!/usr/bin/env python3
"""Check a host, prepare it once, then install the locked workspace and prove it works."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.attentionbench import hostenv, installer, routes, verify


# Conda and pip fall back to another launcher form above a 127-byte shebang, and the official
# comparison accepts only the plain form: #! + WS/envs/curobo-env/bin/python3.11 must fit.
MAX_WORKSPACE_BYTES = 90
_free_port = hostenv.port_free


def workspace_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    path = (path if path.is_absolute() else Path.cwd() / path).resolve(strict=False)
    if any(c.isspace() or c in ':#' for c in str(path)):
        raise installer.StepError('workspace', f'{path} contains a space, colon or #',
                                  'Conda and PATH cannot use such a directory. Choose another workspace path.')
    if len(str(path).encode()) > MAX_WORKSPACE_BYTES:
        raise installer.StepError('workspace', f'{path} is longer than {MAX_WORKSPACE_BYTES} bytes',
                                  'Launcher scripts have a length limit. Choose a shorter workspace path.')
    return path


def _run(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SimpleNamespace(returncode=1, stdout='', stderr=str(exc))


def adopt_checkout(ws: Path, argv: list[str]) -> None:
    """Setup runs from WS/repos/attentionbench. A checkout elsewhere places itself there and restarts."""
    target = ws / 'repos/attentionbench'
    if ROOT == target.resolve():
        return
    if not target.exists():
        origin = _run(['git', '-C', str(ROOT), 'remote', 'get-url', 'origin']).stdout.strip()
        head = _run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip()
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f'Placing this checkout ({head[:7]}) at {target}', flush=True)
        installer.command('repos', ['git', 'clone', str(ROOT), str(target)])
        installer.command('repos', ['git', '-C', str(target), 'checkout', '--detach', head])
        if origin:
            installer.command('repos', ['git', '-C', str(target), 'remote', 'set-url', 'origin', origin])
    os.execv(sys.executable, [sys.executable, '-I', '-u', str(target / 'scripts/attentionbench/setup.py'), *argv])


def uv_python_status(lock: dict) -> tuple[bool, str]:
    """The environments use uv's own Python builds, so uv must be allowed to download them."""
    downloads = os.environ.get('UV_PYTHON_DOWNLOADS', '').lower()
    offline = os.environ.get('UV_OFFLINE', '').lower() not in ('', '0', 'false', 'no', 'off')
    allowed = downloads not in ('never', 'manual') and not offline
    versions = ', '.join(sorted(set(lock['python'].values())))
    return allowed, f'uv may download Python {versions}' if allowed else f'downloads disabled for Python {versions}'


def system_packages() -> list[str]:
    """Ubuntu packages this host still lacks. ffmpeg and bwrap also have workspace fallbacks."""
    packages = [] if shutil.which('git') else ['git']
    if not (shutil.which('cc') and shutil.which('g++')):
        packages.append('build-essential')
    if not Path('/usr/bin/bwrap').is_file():
        packages.append('bubblewrap')
    if not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
        packages.append('ffmpeg')
    return packages + [installer.SYSTEM_LIBRARIES[name] for name in installer.missing_libraries()]


def system_commands() -> tuple[list[list[str]], bool]:
    """Every change that needs an administrator, as one list, and whether AppArmor is among them."""
    packages = system_packages()
    commands = [['sudo', 'apt-get', 'install', '-y', *packages]] if packages else []
    restricted = installer.apparmor_policy_on() and not installer.namespace_status()[0]
    if restricted:
        commands += [['sudo', 'install', '-m', '0644', str(installer.USERNS_PROFILE), installer.USERNS_TARGET],
                     ['sudo', 'apparmor_parser', '-r', installer.USERNS_TARGET]]
    return commands, restricted


def prepare_host(dry_run: bool) -> None:
    """One question for everything that needs sudo. Nothing changes without a yes."""
    commands, restricted = system_commands()
    if not commands:
        return
    print('This host needs a one-time change that only an administrator can make:', flush=True)
    if restricted:
        print('Ubuntu 23.10+ blocks user namespaces, so the agent sandbox cannot start. This profile allows only '
              f'/usr/bin/bwrap; any local user can then use bwrap to create user namespaces:\n{installer.USERNS_PROFILE.read_text()}', end='')
    for command in commands:
        print(' '.join(command))
    if dry_run:
        return
    if not sys.stdin.isatty():
        print('stdin is not a terminal, so nothing was changed. Run the commands above, or rerun setup in a terminal.', flush=True)
        return
    if input('Apply? [y/N] ').strip().lower() != 'y':
        print('No changes made; continuing.', flush=True)
        return
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode:
            raise installer.StepError('host', f'command exited {result.returncode}: {" ".join(command)}',
                                      'Run the command by hand, or ask an administrator, then rerun setup.')


def _isolation() -> tuple[bool, str]:
    isolated, reason, _ = installer.namespace_status()
    return isolated, reason


def prerequisites(ws: Path, *, dry_run: bool, ui_port: int | None = None, memory_port: int | None = None,
                  rebuild_planner: bool = False) -> bool:
    lock = installer.data()
    failed = False

    def check(name: str, ok: bool, detail: str, fix: str, *, warning: bool = False) -> None:
        nonlocal failed
        status = 'PASS' if ok else 'WARN' if warning else 'FAIL'
        print(f'{status} {name}: {detail}' + (f'; install: {fix}' if not ok else ''), flush=True)
        failed |= not ok and not warning

    check('Linux x86_64', platform.system() == 'Linux' and platform.machine() == 'x86_64',
          f'{platform.system()} {platform.machine()}', 'Use Ubuntu 22.04 or 24.04 on x86_64.')
    host_failures, host_warnings = installer.gate(installer.host_fingerprint())
    check('host versions', not host_failures,
          '; '.join(host_failures) if host_failures else 'driver and glibc readable',
          'Install the NVIDIA driver or move to Ubuntu 22.04 or 24.04.', warning=dry_run)
    for warning in host_warnings:
        check('host', False, warning, 'See the message.', warning=True)
    nvidia = shutil.which('nvidia-smi')
    driver = _run([nvidia, '--query-gpu=driver_version', '--format=csv,noheader']) if nvidia else None
    check('NVIDIA driver', bool(driver and driver.returncode == 0 and driver.stdout.strip()),
          driver.stdout.strip() if driver and driver.returncode == 0 else 'nvidia-smi or driver unavailable',
          'Install a compatible NVIDIA driver: https://www.nvidia.com/Download/index.aspx', warning=dry_run)
    check('git', bool(shutil.which('git')), 'git available' if shutil.which('git') else 'git missing',
          'sudo apt-get install git')
    check('Python 3.10+', sys.version_info >= (3, 10), '.'.join(map(str, sys.version_info[:3])), 'Use Ubuntu 22.04 or 24.04.')
    compilers = bool(shutil.which('cc') and shutil.which('g++'))
    check('C and C++ compilers', compilers, 'cc and g++ available' if compilers else 'cc or g++ missing',
          'sudo apt-get install build-essential')
    gaps = installer.missing_libraries()
    check('system libraries', not gaps, 'found' if not gaps else 'missing ' + ', '.join(gaps),
          'sudo apt-get install ' + ' '.join(installer.SYSTEM_LIBRARIES[name] for name in gaps))
    media = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
    check('ffmpeg', media, 'ffmpeg and ffprobe available' if media else 'missing; setup will add a workspace copy',
          'sudo apt-get install ffmpeg', warning=True)
    uv_ok, uv_reason = uv_python_status(lock)
    check('uv Python downloads', uv_ok, uv_reason, 'Unset UV_PYTHON_DOWNLOADS and UV_OFFLINE.')
    bwrap = shutil.which('bwrap')
    isolated, reason = _isolation() if bwrap else (False, 'no bwrap yet; the Codex package brings one')
    in_container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
    # prepare_host has just offered the AppArmor change. It blocks only agent runs, so the long install goes on
    # and doctor stops at the end until the change is made.
    check('bwrap user namespaces', isolated, reason,
          installer.USERNS_FIX if reason == installer.USERNS_REASON else
          'sudo apt-get install bubblewrap; enable unprivileged user namespaces (inspect kernel/container policy).'
          + (' Inside Docker, start the container as in the Docker appendix of'
             ' docs/attentionbench_collaborator_setup.md.' if in_container else ''),
          warning=dry_run or not bwrap or reason == installer.USERNS_REASON)
    parent = ws
    while not parent.exists():
        parent = parent.parent
    stat = os.statvfs(parent)
    free_gb = stat.f_bavail * stat.f_frsize / 1024**3
    # A workspace that is already set up only needs room for runs.
    need = installer.RUN_FREE_GB if (ws / 'paths.json').is_file() else installer.INSTALL_FREE_GB
    if rebuild_planner and (ws / 'deployments').is_dir():
        deployments_gb = sum(file.stat().st_size for file in (ws / 'deployments').rglob('*') if file.is_file()) / 1024**3
        need = max(1, deployments_gb)
    check('disk', free_gb >= need, f'{free_gb:.1f} GB free at {parent}; setup needs {need} GB',
          f'Free at least {need} GB on the filesystem containing {parent}. A first install peaks near 75 GB and ends near 53 GB.')
    saved = hostenv.saved_ports(ws)
    for name, label, port in (('ui', 'UI port', ui_port or saved[0]), ('memory', 'Memory port', memory_port or saved[1])):
        free = _free_port(port)
        ours = not free and hostenv.owned(ws, name)
        check(label, free or ours, f'127.0.0.1:{port} {"free" if free else "our process" if ours else "busy"}',
              'start.sh and run.sh use the next free port.', warning=not rebuild_planner)
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
                                  'Run codex login and one short codex exec call, then rerun this command.')


def report_routes(ws: Path) -> None:
    found = routes.available()
    run = ws / 'repos/attentionbench/scripts/attentionbench/run.sh'
    if found:
        print(f'Model routes ready: {", ".join(found)}. Next: {run} --workspace {ws}')
    else:
        print(f'No model route is ready yet; only run.sh needs one. Next: {run} --workspace {ws} --model NAME, after one of:')
        for name in routes.AUTO_ORDER:
            print(f'  {name}: {routes.ROUTES[name].hint}')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default=hostenv.default_workspace(ROOT), help='defaults to the workspace holding this checkout')
    p.add_argument('--assets-from')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-codex-warmup', action='store_true')
    p.add_argument('--update', action='store_true', help='rebuild whatever the current checkout changed')
    p.add_argument('--skip-planner', action='store_true')
    p.add_argument('--fix-userns', action='store_true', help=argparse.SUPPRESS)  # Setup now asks by itself.
    p.add_argument('--rebuild-planner', action='store_true')
    p.add_argument('--reprove', action='store_true')
    p.add_argument('--planner-project-from')
    p.add_argument('--planner-native-assets-from')
    p.add_argument('--ui-port', type=int)
    p.add_argument('--memory-port', type=int)
    raw = sys.argv[1:] if argv is None else argv
    args = p.parse_args(raw)
    try:
        if not args.workspace:
            raise installer.StepError('workspace', 'no workspace given', 'Pass --workspace /absolute/WS.')
        ws = workspace_path(args.workspace)
        args.workspace = str(ws)
        if args.reprove:
            if args.rebuild_planner or args.skip_planner or args.dry_run:
                raise installer.StepError('reprove', '--reprove must run alone', 'Use --reprove with an existing planner workspace.')
            hostenv.apply(ws)
            print(f'Host reproof: {installer.reprove(ws)}')
            return 0
        if not args.dry_run:
            adopt_checkout(ws, raw)
        hostenv.apply(ws)
        if args.rebuild_planner:
            args.ui_port, args.memory_port = hostenv.saved_ports(ws)
            installer.validate_rebuild(ws, args)
        prepare_host(args.dry_run)
        if not prerequisites(ws, dry_run=args.dry_run, ui_port=args.ui_port, memory_port=args.memory_port,
                             rebuild_planner=args.rebuild_planner):
            raise installer.StepError('prerequisites', 'one or more required checks failed',
                                      'Run the install commands above and rerun setup.')
        bootstrap_args = SimpleNamespace(**vars(args))
        installer.bootstrap(bootstrap_args)
        if args.dry_run:
            return 0
        if installer.doctor(SimpleNamespace(workspace=str(ws), json=False)):
            raise installer.StepError('doctor', 'doctor reported failed checks',
                                      'Repair each FAIL item printed above and rerun setup.')
        if not args.skip_planner and verify.run(ws):
            raise installer.StepError('verify', 'a self-test failed', 'Repair each FAIL item printed above and rerun setup.')
        installer.clean_caches(ws)
        if routes.codex_logged_in():
            try:
                warmup(args.no_codex_warmup)
            except installer.StepError as exc:
                print(f'WARN {exc}', flush=True)
        report_routes(ws)
        return 0
    except installer.StepError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(installer.StepError('setup', str(exc), 'Inspect the error and rerun setup.'), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
