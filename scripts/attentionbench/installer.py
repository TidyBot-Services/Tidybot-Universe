#!/usr/bin/env python3
"""Workspace-local installer. No author environment is ever a write target."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
LOCK = REPO / 'attentionbench.lock.json'
TEMPLATE = REPO / 'config/attentionbench/paths.template.json'
DOCTOR_PATHS = REPO / 'config/attentionbench/doctor-paths.json'
TRUST_MESSAGE = 'local planner chain or protected dev trust registry is missing'


class StepError(RuntimeError):
    def __init__(self, step: str, why: str, fix: str):
        super().__init__(f'step: {step}\nwhat failed: {why}\nhow to fix: {fix}')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def data() -> dict:
    return json.loads(LOCK.read_text())


def command(step: str, argv: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    print(f'[{step}] {" ".join(argv)}', flush=True)
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise StepError(step, f'command exited {result.returncode}: {(result.stderr or result.stdout)[-1800:].strip()}',
                        'Check prerequisites, available disk/network, and the command above; then rerun bootstrap.')


def stamped(ws: Path, step: str, inputs: object, update: bool, action) -> None:
    stamp = ws / 'data/installer/stamps' / f'{step}.json'
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    if stamp.exists():
        old = json.loads(stamp.read_text())
        if old.get('input_sha256') == key:
            print(f'[{step}] already complete')
            return
        if not update:
            raise StepError(step, 'lock inputs changed since this step completed', 'Rerun with --update.')
    try:
        action()
    except StepError:
        raise
    except Exception as exc:
        raise StepError(step, str(exc), 'Inspect the command error and rerun this step; use --update if its inputs changed.') from exc
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({'input_sha256': key}, indent=2) + '\n')


def clone(ws: Path, name: str, rel: str, pin: dict, update: bool) -> None:
    target = ws / rel
    fresh = not target.exists()
    if fresh:
        target.parent.mkdir(parents=True, exist_ok=True)
        command('repos', ['git', 'clone', '--filter=blob:none', pin['url'], str(target)])
    elif not (target / '.git').exists():
        raise StepError('repos', f'{target} exists but is not a Git checkout', 'Move it aside and rerun.')
    head = subprocess.run(['git', '-C', str(target), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    if head.returncode or head.stdout.strip() != pin['commit']:
        if not fresh and not update and head.returncode == 0 and head.stdout.strip():
            raise StepError('repos', f'{name} is at {head.stdout.strip()}, expected {pin["commit"]}', 'Rerun with --update after preserving local changes.')
        command('repos', ['git', '-C', str(target), 'fetch', 'origin', pin['commit']])
        command('repos', ['git', '-C', str(target), 'checkout', '--detach', pin['commit']])
    dirty = subprocess.run(['git', '-C', str(target), 'status', '--porcelain', '--untracked-files=no'], capture_output=True, text=True)
    if dirty.stdout.strip():
        raise StepError('repos', f'{name} has modified tracked files', 'Restore or move the checkout, then rerun.')


def verify_assets(source: Path, manifest: Path, expected_sha: str) -> list[str]:
    if not manifest.is_file() or digest(manifest) != expected_sha:
        return [f'manifest SHA256: {manifest}']
    records = json.loads(manifest.read_text())
    bad = []
    for rel, sha in records.items():
        p = source / rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or not p.is_file() or p.is_symlink() or digest(p) != sha:
            bad.append(rel)
    return bad


def _extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            p = Path(item.filename)
            if p.is_absolute() or '..' in p.parts or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise StepError('assets', f'unsafe archive member {item.filename}', 'Use the official archive named in the locked asset registry.')
        bundle.extractall(destination)


def download_assets(ws: Path, lock: dict) -> Path:
    simrepo = ws / 'repos/robocasa365_sim'
    asset_lock = simrepo / 'assets-installation-lock.json'
    if digest(asset_lock) != lock['assets']['installation_lock_sha256']:
        raise StepError('assets', 'asset installation registry SHA256 differs from lock', 'Restore the pinned simulator checkout.')
    registry = json.loads(asset_lock.read_text())
    source = ws / 'data/robocasa365/assets'
    baseline = ws / 'sim/robocasa/robocasa/models/assets'
    if not source.exists():
        shutil.copytree(baseline, source)
    downloads = ws / 'data/robocasa365/downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    for pack in registry['packs']:
        archive = downloads / (pack['name'] + '.zip')
        if not archive.is_file() or digest(archive) != pack['archive_sha256']:
            urllib.request.urlretrieve(pack['source_url'], archive)
        if digest(archive) != pack['archive_sha256']:
            raise StepError('assets', f'{pack["name"]} archive SHA256 mismatch', 'Remove the bad download and retry.')
        folder = source / 'objects' / pack['name']
        if not folder.exists():
            _extract(archive, source / 'objects')
    return source


def write_paths(ws: Path) -> None:
    paths = json.loads(TEMPLATE.read_text())['paths']
    (ws / 'paths.json').write_text(json.dumps({'schema': 'attentionbench.paths.v1', 'paths': paths}, indent=2) + '\n')


def bootstrap(args) -> int:
    ws = Path(args.workspace).expanduser().resolve()
    lock = data()
    plan = ['layout', 'repos: attentionbench, robocasa365_sim, attention_memory_service, robocasa, robosuite, curobo, robocasa_tasks',
            'ui-venv: Python 3.10 and pinned harness requirements', 'assets: official registry archives and 123577 SHA256 checks',
            'sim-env: Python 3.11 simulator installer', 'curobo-env: conda and pinned pip environment in planner chain',
            'planner: official cuRobo env/assets/lock, d05/d06, proof, local trust, dev-local', 'paths: all template keys',
            'stores: UI, Memory, random 0600 keys.env']
    if args.dry_run:
        for line in plan:
            print(line)
        return 0
    for rel in ('repos', 'envs', 'sim', 'deployments', 'data'):
        (ws / rel).mkdir(parents=True, exist_ok=True)
    repo_dirs = {'attentionbench': 'repos/attentionbench', 'robocasa365_sim': 'repos/robocasa365_sim',
                 'attention_memory_service': 'repos/attention_memory_service', 'robocasa': 'sim/robocasa',
                 'robosuite': 'sim/robosuite', 'curobo': 'repos/curobo',
                 'robocasa_tasks': 'repos/robocasa_tasks'}
    if REPO != (ws / 'repos/attentionbench').resolve():
        raise StepError('repos', f'this checkout is {REPO}, not {ws}/repos/attentionbench',
                        f'Clone Tidybot-Universe into {ws}/repos/attentionbench (no symlink) and run bootstrap from there.')
    head = subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    for name, pin in lock['repos'].items():
        if name == 'attentionbench':
            continue
        stamped(ws, 'repo-' + name, pin, args.update,
                lambda name=name, pin=pin: clone(ws, name, repo_dirs[name], pin, args.update))
    runtime_manifest = ws / lock['assets']['runtime_manifest']
    if digest(runtime_manifest) != lock['assets']['runtime_manifest_sha256']:
        raise StepError('repos', 'simulator runtime asset manifest SHA256 mismatch', 'Restore the pinned simulator checkout.')
    def ui():
        env = os.environ | {'TIDYBOT_ATTENTION_VENV': str(ws / 'envs/ui'),
                            'TIDYBOT_ATTENTION_PYTHON': lock['python']['ui'],
                            'TIDYBOT_MEMORY_SERVICE_SOURCE': str(ws / 'repos/attention_memory_service')}
        command('ui-venv', ['bash', str(ws / 'repos/attentionbench/benchmarks/attention_harness/setup_env.sh')], env=env)
    stamped(ws, 'ui-venv', {'python': lock['python']['ui'], 'repo': head, 'memory': lock['repos']['attention_memory_service']}, args.update, ui)
    try:
        source = Path(args.assets_from).expanduser().resolve() if args.assets_from else download_assets(ws, lock)
    except StepError:
        raise
    except Exception as exc:
        raise StepError('assets', str(exc), 'Check the official download URL, disk space, and network; then rerun.') from exc
    manifest = ws / lock['assets']['installed_manifest']
    bad = verify_assets(source, manifest, lock['assets']['installed_manifest_sha256'])
    if bad:
        raise StepError('assets', f'{len(bad)} mismatching files:\n' + '\n'.join(bad), 'Supply the complete official asset tree; no files were repaired.')
    verified = ws / 'data/robocasa365/assets-verified.json'
    verified.parent.mkdir(parents=True, exist_ok=True)
    verified.write_text(json.dumps({'manifest_sha256': lock['assets']['installed_manifest_sha256'], 'source': str(source)}) + '\n')
    def sim():
        runtime = ws / 'sim/runtime'
        if runtime.exists():
            if not args.update:
                raise StepError('sim-env', 'runtime already exists without matching stamp', 'Rerun with --update after preserving local data.')
            shutil.rmtree(runtime)
        command('sim-env', [sys.executable, str(ws / 'repos/robocasa365_sim/install.py'), '--runtime-root', str(runtime), '--asset-source', str(source)])
        command('sim-env', ['uv', 'pip', 'install', '--python', str(runtime / 'venv/bin/python'), '--no-deps', '-e', str(ws / 'repos/attention_memory_service')])
    stamped(ws, 'sim-env', {'repos': [lock['repos'][x] for x in ('robocasa365_sim', 'robocasa', 'robosuite')], 'assets': lock['assets']}, args.update, sim)
    write_paths(ws)
    store = ws / 'data/ui/attention.sqlite3'
    artifacts = ws / 'data/ui/artifacts'
    if not store.exists():
        command('ui-store', [str(ws / 'envs/ui/bin/python'), '-m', 'benchmarks.attention_harness.init_store', '--store', str(store), '--artifact-root', str(artifacts)], cwd=ws / 'repos/attentionbench', env=os.environ | {'ATTENTIONBENCH_WORKSPACE': str(ws)})
    memory = ws / 'repos/memory-m3'
    memory.mkdir(exist_ok=True)
    keys = memory / 'keys.env'
    if not keys.exists():
        fd = os.open(keys, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            for key in ('ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_OPERATOR_KEY', 'ATTENTION_MEMORY_IMPORTER_KEY'):
                stream.write(f'{key}={secrets.token_hex(32)}\n')
    if keys.stat().st_mode & 0o777 != 0o600:
        raise StepError('keys', 'keys.env mode is not 0600', 'Run chmod 600 on the workspace keys.env file.')
    import sqlite3
    import uuid
    with sqlite3.connect(memory / 'store.sqlite3') as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS memory_service_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), value TEXT NOT NULL)')
        row = connection.execute('SELECT value FROM memory_service_identity WHERE singleton=1').fetchone()
        store_id = row[0] if row else str(uuid.uuid4())
        if row is None:
            connection.execute('INSERT INTO memory_service_identity(singleton, value) VALUES (1, ?)', (store_id,))
    (memory / 'registration-roots.json').write_text('{}\n') if not (memory / 'registration-roots.json').exists() else None
    (memory / 'store_id.txt').write_text(store_id + '\n')
    (memory / 'namespace.txt').write_text('attentionbench-' + secrets.token_hex(8) + '\n') if not (memory / 'namespace.txt').exists() else None
    for rel in ('data/daily-experiments', 'data/robocasa365', 'deployments/openarm-dev-integration/dev-local'):
        (ws / rel).mkdir(parents=True, exist_ok=True)
    if not args.skip_planner:
        # The chain imports the Harness (numpy, robosuite adapter), so it runs in the UI venv,
        # not in the bare system Python that starts bootstrap.
        argv = [str(ws / 'envs/ui/bin/python'), str(REPO / 'scripts/attentionbench/build_planner_chain.py'),
                '--workspace', str(ws)]
        if getattr(args, 'planner_project_from', None):
            argv += ['--project-from', str(args.planner_project_from)]
        if getattr(args, 'planner_native_assets_from', None):
            argv += ['--native-assets-from', str(args.planner_native_assets_from)]
        command('planner', argv, cwd=REPO, env=os.environ | {'PYTHONPATH': str(REPO)})
    print(f'Bootstrap complete{" without planner" if args.skip_planner else ""}: {ws}')
    return 0


def _check(results: list, name: str, ok: bool | None, reason: str, fix: str) -> None:
    results.append({'check': name, 'status': 'SKIP' if ok is None else ('PASS' if ok else 'FAIL'), 'reason': reason, 'fix': fix})


def check_doctor_paths(results: list, ws: Path, paths: dict) -> None:
    template = json.loads(TEMPLATE.read_text())['paths']
    policy = json.loads(DOCTOR_PATHS.read_text())['paths']
    if set(policy) != set(template):
        raise StepError('doctor', 'doctor path policy does not cover the template exactly',
                        'Update config/attentionbench/doctor-paths.json with every template key.')
    for key, entry in policy.items():
        if type(entry.get('required')) is not bool or (not entry['required'] and not entry.get('reason')):
            raise StepError('doctor', f'invalid path policy for {key}', 'Fix doctor-paths.json.')
        value = paths.get(key)
        safe = isinstance(value, str) and bool(value) and not Path(value).is_absolute() and '..' not in Path(value).parts
        if value is not None and not safe:
            _check(results, 'path:' + key, False, f'invalid relative path key {key}', 'Correct this paths.json entry.')
        elif safe and (ws / value).exists():
            _check(results, 'path:' + key, True, value, '')
        elif entry['required']:
            _check(results, 'path:' + key, False, f'missing or nonexistent path key {key}', 'Run bootstrap or correct this relative paths.json entry.')
        else:
            _check(results, 'path:' + key, None, f'optional ({entry["reason"]})', '')


def doctor(args) -> int:
    lock = data(); ws = Path(args.workspace or os.environ.get('ATTENTIONBENCH_WORKSPACE', Path.cwd())).expanduser().resolve()
    results = []
    py = ws / 'envs/curobo-env/bin/python'
    probe = subprocess.run([str(py), '-c', 'import torch; print(torch.cuda.is_available())'], capture_output=True, text=True) if py.is_file() else None
    gpu_ok = bool(probe and probe.returncode == 0 and probe.stdout.strip() == 'True')
    _check(results, 'gpu', gpu_ok, 'torch CUDA available' if gpu_ok else 'GPU/driver not visible to cuRobo torch', 'Install a compatible NVIDIA driver and cuRobo env; verify torch.cuda.is_available().')
    bwrap = shutil.which('bwrap')
    userns = Path('/proc/sys/kernel/unprivileged_userns_clone')
    enabled = not userns.exists() or userns.read_text().strip() == '1'
    namespace_probe = subprocess.run([bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--ro-bind', '/', '/', '--proc', '/proc', '/bin/true'], capture_output=True, text=True) if bwrap and enabled else None
    isolated = bool(namespace_probe and namespace_probe.returncode == 0)
    _check(results, 'isolation', isolated, 'bwrap user/PID/network namespaces available' if isolated else ('bwrap namespace probe failed: ' + namespace_probe.stderr[-300:].strip() if namespace_probe else 'bwrap missing or user namespaces disabled'), 'Install bubblewrap and enable unprivileged user namespaces; inspect kernel/container policy.')
    codex = shutil.which('codex')
    version = subprocess.run(['codex', '--version'], capture_output=True, text=True).stdout.strip() if codex else ''
    _check(results, 'codex-version', version == lock['codex']['version'], version or 'codex missing', f'Install {lock["codex"]["version"]}.')
    login = subprocess.run(['codex', 'login', 'status'], capture_output=True, text=True) if codex else None
    _check(results, 'codex-login', bool(login and login.returncode == 0), 'logged in' if login and login.returncode == 0 else 'Codex is not logged in', 'Run codex login.')
    registry = ws / 'paths.json'
    try:
        paths = json.loads(registry.read_text())['paths']
    except (OSError, ValueError, KeyError):
        paths = {}
    check_doctor_paths(results, ws, paths)
    runtime_manifest = ws / lock['assets']['runtime_manifest']
    runtime_ok = runtime_manifest.is_file() and digest(runtime_manifest) == lock['assets']['runtime_manifest_sha256']
    _check(results, 'runtime-asset-manifest', runtime_ok, 'SHA256 matches lock' if runtime_ok else 'runtime asset manifest SHA256 mismatch or missing', 'Restore the pinned simulator checkout.')
    stamp = ws / 'data/robocasa365/assets-verified.json'
    source = None
    try:
        record = json.loads(stamp.read_text()); source = Path(record['source'])
        bad = verify_assets(source, ws / lock['assets']['installed_manifest'], lock['assets']['installed_manifest_sha256'])
    except (OSError, ValueError, KeyError):
        bad = ['verification stamp missing or unreadable']
    _check(results, 'assets', not bad, 'all asset SHA256 values match' if not bad else 'asset SHA256 mismatch: ' + ', '.join(bad[:10]), 'Restore the complete official asset tree and rerun bootstrap.')
    for name in ('dev05', 'dev06'):
        p = ws / 'deployments' / name / 'runtime-lock.json'
        _check(results, 'deployment-' + name, p.is_file(), str(p) if p.is_file() else 'deployment lock missing', 'Build the local planner deployment after P5 is reviewed.')
    trust = ws / 'deployments/local-dev-trust.json'
    identity = ws / 'repos/attentionbench/curobo_v2/identity.py'
    p5 = identity.is_file() and 'def _locally_trusted_dev' in identity.read_text()
    try:
        trusted = json.loads(trust.read_text()) if trust.is_file() else {}
    except (OSError, ValueError):
        trusted = {}
    trusted_ok = p5 and trusted.get('schema') == 'attentionbench.curobo-v2.local-dev-trust.v1' and not trust.is_symlink() and not (trust.stat().st_mode & 0o022)
    _check(results, 'planner-trust', trusted_ok, str(trust) if trusted_ok else TRUST_MESSAGE, 'Run bootstrap without --skip-planner to build and register the local chain.')
    for port in (8769, 8878):
        with socket.socket() as sock:
            free = sock.connect_ex(('127.0.0.1', port)) != 0
        pidfile = ws / 'data/run' / ('ui.pid' if port == 8769 else 'memory.pid')
        ours = False
        if pidfile.is_file() and pidfile.read_text().strip().isdigit():
            pid = pidfile.read_text().strip()
            proc = Path('/proc') / pid / 'cmdline'
            if proc.is_file():
                cmdline = proc.read_bytes().replace(b'\0', b' ').decode(errors='replace')
                expected = 'attention_memory_service' if port == 8878 else 'benchmarks.attention_harness.ui_server'
                ours = expected in cmdline and str(ws) in cmdline
        _check(results, f'port-{port}', free or ours, 'free' if free else ('our process' if ours else 'busy'), f'Stop the process using {port} or choose another start port.')
    free_gb = shutil.disk_usage(ws if ws.exists() else ws.parent).free / 1024**3
    _check(results, 'disk', free_gb >= 60, f'{free_gb:.1f} GB free', 'Free at least 60 GB on the workspace filesystem.')
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for row in results:
            fix = f'; fix: {row["fix"]}' if row['fix'] else ''
            print(f'{row["status"]} {row["check"]}: {row["reason"]}{fix}')
    return int(any(row['status'] == 'FAIL' for row in results))


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    b = sub.add_parser('bootstrap'); b.add_argument('--workspace', required=True); b.add_argument('--assets-from'); b.add_argument('--update', action='store_true'); b.add_argument('--skip-planner', action='store_true'); b.add_argument('--dry-run', action='store_true'); b.add_argument('--planner-project-from'); b.add_argument('--planner-native-assets-from')
    d = sub.add_parser('doctor'); d.add_argument('--workspace'); d.add_argument('--json', action='store_true')
    args = p.parse_args()
    try:
        return bootstrap(args) if args.command == 'bootstrap' else doctor(args)
    except StepError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'step: installer\nwhat failed: {exc}\nhow to fix: Inspect the workspace state and rerun bootstrap or doctor.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
