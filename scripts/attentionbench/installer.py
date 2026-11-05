#!/usr/bin/env python3
"""Workspace-local installer. No author environment is ever a write target."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from curobo_v2.host_fingerprint import host_fingerprint, host_libraries, gate, ldconfig_cache
from scripts.attentionbench import fetch, hostenv, routes, tools
LOCK = REPO / 'attentionbench.lock.json'
TEMPLATE = REPO / 'config/attentionbench/paths.template.json'
DOCTOR_PATHS = REPO / 'config/attentionbench/doctor-paths.json'
TRUST_MESSAGE = 'local planner chain or protected dev trust registry is missing'
USERNS_REASON = 'Ubuntu 23.10+ AppArmor blocks unprivileged user namespaces for bwrap (kernel.apparmor_restrict_unprivileged_userns=1)'
USERNS_PROFILE = REPO / 'config/attentionbench/apparmor/attentionbench-bwrap-userns'
USERNS_TARGET = '/etc/apparmor.d/attentionbench-bwrap-userns'
USERNS_FIX = ('C (recommended): AppArmor profile for /usr/bin/bwrap only; rerun setup in a terminal and answer y, or run '
              f'sudo install -m 0644 {USERNS_PROFILE} {USERNS_TARGET} && sudo apparmor_parser -r {USERNS_TARGET}. '
              'Keeps the machine-wide restriction; any local user can then use bwrap to create user namespaces. '
              'A: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 (machine-wide, until reboot). '
              "B: echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-attentionbench-userns.conf && sudo sysctl --system (machine-wide, permanent).")
DEPLOYMENT_STEPS = ('project', 'official', 'd05', 'd06', 'proof', 'registry', 'snapshot')
# Shared libraries the simulator, its renderer and OpenCV load, and the Ubuntu package for each.
SYSTEM_LIBRARIES = {'libEGL.so.1': 'libegl1', 'libGL.so.1': 'libgl1', 'libglib-2.0.so.0': 'libglib2.0-0', 'libgomp.so.1': 'libgomp1'}
GPU_FREE_WARN_MIB = 6144  # Below this the planner and renderer have not been tried; 16 GB cards had room.
# A first install peaks near 75 GB and ends near 53 GB, because archives, the staged asset tree and
# download caches are removed as soon as nothing needs them. See bootstrap and clean_caches.
INSTALL_FREE_GB = 80
RUN_FREE_GB = 10


HEARTBEAT_SECONDS = 60
STALE_PLANNER = 'stamp inputs or outputs changed'  # build_planner_chain's message for a stale step.


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


def apparmor_userns_restricted(probe, policy: Path = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')) -> bool:
    # The error depends on the kernel and on how far bwrap got (uid map, RTM_NEWADDR, ...), so only
    # the failure and the policy decide.
    return probe is not None and probe.returncode != 0 and apparmor_policy_on(policy)


def apparmor_policy_on(policy: Path = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')) -> bool:
    try:
        return policy.read_text().strip() == '1'
    except OSError:
        return False


def namespace_status() -> tuple[bool, str, str]:
    bwrap = shutil.which('bwrap')
    userns = Path('/proc/sys/kernel/unprivileged_userns_clone')
    enabled = not userns.exists() or userns.read_text().strip() == '1'
    probe = subprocess.run([bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--ro-bind', '/', '/', '--proc', '/proc', '/bin/true'], capture_output=True, text=True) if bwrap and enabled else None
    if probe and probe.returncode == 0:
        return True, 'bwrap user/PID/network namespaces available', ''
    if apparmor_userns_restricted(probe):
        return False, USERNS_REASON, USERNS_FIX
    reason = 'bwrap namespace probe failed: ' + probe.stderr[-300:].strip() if probe else 'bwrap missing or user namespaces disabled'
    return False, reason, 'Install bubblewrap and enable unprivileged user namespaces; inspect kernel/container policy.'


def _port_free(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False


def validate_rebuild(ws: Path, args) -> None:
    if args.skip_planner:
        raise StepError('rebuild-planner', '--skip-planner conflicts with --rebuild-planner', 'Remove --skip-planner.')
    if not (ws / 'paths.json').is_file():
        raise StepError('rebuild-planner', 'paths.json missing', 'Run a first setup before rebuilding the planner.')
    if not (ws / 'deployments').is_dir():
        raise StepError('rebuild-planner', 'deployments missing', 'Restore the deployment tree or run a first setup.')
    from scripts.attentionbench import control
    if not _port_free(getattr(args, 'ui_port', 8769)) or not _port_free(getattr(args, 'memory_port', 8878)) or control.live_workers(ws):
        raise StepError('rebuild-planner', 'UI, Memory, or a worker is running', 'Run stop.sh, then rerun setup.')


def rebuild_planner(ws: Path, args) -> None:
    validate_rebuild(ws, args)
    old = ws / 'deployments'
    target = ws / ('deployments.superseded-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    if target.exists():
        raise StepError('rebuild-planner', f'{target} already exists', 'Wait a second and rerun setup.')
    old.rename(target)
    for step in DEPLOYMENT_STEPS:
        stamp = ws / 'data/installer/stamps' / f'planner-{step}.json'
        if stamp.exists():
            (target / 'stamps').mkdir(exist_ok=True)
            stamp.rename(target / 'stamps' / stamp.name)
    print(f'Planner deployments moved to {target}. Keep superseded-* while old run manifests matter.')


def reprove(ws: Path) -> Path:
    """Rerun the planner proof on today's host. Writes only a new file under data/run."""
    d06 = ws / 'deployments/dev06/runtime-lock.json'
    if not d06.is_file():
        raise StepError('reprove', 'existing d06 lock missing', 'Build the planner first.')
    output = ws / 'data/run' / ('host-reproof-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    script = REPO / 'scripts/attentionbench'
    command('reprove', [str(ws / 'envs/curobo-env/bin/python'), '-B', '-I', str(script / 'planner_proof.py'),
                        '--library', str(ws / 'repos/curobo'), '--lock', str(d06),
                        '--requests', str(script / 'planner-proof-requests.json'),
                        '--output', str(output), '--record-host'])
    if json.loads(output.read_text()).get('passed') is not True:
        raise StepError('reprove', 'planner proof did not pass', f'Inspect {output}.')
    return output


def command(step: str, argv: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    print(f'[{step}] {" ".join(argv)}', flush=True)
    started = time.monotonic()
    with tempfile.TemporaryFile('w+', errors='replace') as log:
        process = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdout=log, stderr=subprocess.STDOUT)
        while True:
            try:
                code = process.wait(timeout=HEARTBEAT_SECONDS)
                break
            except subprocess.TimeoutExpired:
                print(f'[{step}] still running after {int(time.monotonic() - started)} s', flush=True)
        if code:
            log.seek(0)
            raise StepError(step, f'command exited {code}: {log.read()[-1800:].strip()}',
                            'Check prerequisites, available disk/network, and the command above; then rerun setup.')


def stamp_key(inputs: object) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def stamp_current(ws: Path, step: str, inputs: object) -> bool:
    try:
        return json.loads((ws / 'data/installer/stamps' / f'{step}.json').read_text()).get('input_sha256') == stamp_key(inputs)
    except (OSError, ValueError):
        return False


def stamped(ws: Path, step: str, inputs: object, update: bool, action) -> None:
    stamp = ws / 'data/installer/stamps' / f'{step}.json'
    key = stamp_key(inputs)
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


def asset_sources(lock: dict) -> dict:
    file = REPO / lock['assets']['sources']
    if digest(file) != lock['assets']['sources_sha256']:
        raise StepError('assets', 'asset source list SHA256 differs from lock', 'Restore the committed installer inputs.')
    return json.loads(file.read_text())


def safe_members(bundle: zipfile.ZipFile, archive: Path) -> list:
    members = []
    for item in bundle.infolist():
        p = Path(item.filename)
        if p.is_absolute() or '..' in p.parts or ((item.external_attr >> 16) & 0o170000) == 0o120000:
            raise StepError('assets', f'unsafe archive member {item.filename} in {archive.name}', 'Use the official archive named in asset-sources.json.')
        if not item.is_dir():
            members.append(item)
    return members


def write_file(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def fetch_pack(pack: dict, downloads: Path) -> Path:
    """Download one archive. A wrong size or digest is reported; the file check after unpacking decides."""
    archive = fetch.download(pack['url'], downloads / (pack['name'] + '.zip'), size=pack.get('bytes'), label=pack['name'])
    if pack.get('sha256') and digest(archive) != pack['sha256']:
        print(f'WARN {pack["name"]}: archive digest differs from asset-sources.json; its files are checked one by one', flush=True)
    return archive


def unpack_pack(archive: Path, root: Path, wanted: dict, prefix: str) -> int:
    """Write the members the manifest lists, once their bytes match it. Everything else is ignored."""
    written = 0
    with zipfile.ZipFile(archive) as bundle:
        for item in safe_members(bundle, archive):
            rel = prefix + item.filename
            if rel in wanted:
                data = bundle.read(item)
                if hashlib.sha256(data).hexdigest() == wanted[rel]:
                    write_file(root / rel, data)
                    written += 1
    return written


def download_assets(ws: Path, lock: dict) -> Path:
    """The installed tree: the pinned simulator checkout plus every official archive."""
    source = ws / 'data/robocasa365/assets'
    baseline = ws / 'sim/robocasa/robocasa/models/assets'
    wanted = json.loads((ws / lock['assets']['installed_manifest']).read_text())
    stamps = ws / 'data/installer/stamps'
    copied = stamps / 'asset-baseline.json'
    if not copied.exists():
        shutil.rmtree(source, ignore_errors=True)
        shutil.copytree(baseline, source)
        stamps.mkdir(parents=True, exist_ok=True)
        copied.write_text('{}\n')
    downloads = ws / 'data/robocasa365/downloads'
    for pack in asset_sources(lock)['robocasa']:
        stamp = stamps / f'asset-{pack["name"]}.json'
        if stamp.exists():
            continue
        archive = fetch_pack(pack, downloads)
        print(f'[assets] unpacking {pack["name"]}', flush=True)
        try:
            written = unpack_pack(archive, source, wanted, pack['prefix'])
        except zipfile.BadZipFile as exc:
            archive.unlink()
            raise StepError('assets', f'{pack["name"]}: {exc}; the bad download was removed', 'Rerun setup.') from exc
        stamp.write_text(json.dumps({'written': written}) + '\n')
        archive.unlink()  # Unpacked and checked; the archive only costs disk from here on.
    return source


def download_native_assets(ws: Path, lock: dict, dest: Path) -> None:
    """Rebuild the planner's native tree: public archives, then the local plate_17 fix the lock carries."""
    sources = asset_sources(lock)['native']
    shutil.rmtree(dest / 'data', ignore_errors=True)  # No stamp yet: whatever is here is an interrupted attempt.
    downloads = ws / 'data/robocasa365/downloads'
    for pack in sources['archives']:
        archive = fetch_pack(pack, downloads)
        print(f'[assets] unpacking {pack["name"]}', flush=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                for item in safe_members(bundle, archive):
                    if not item.filename.startswith(pack['strip']):
                        raise StepError('assets', f'unexpected archive member {item.filename}', 'Check asset-sources.json.')
                    write_file(dest / pack['target'] / item.filename[len(pack['strip']):], bundle.read(item))
        except zipfile.BadZipFile as exc:
            archive.unlink()
            raise StepError('assets', f'{pack["name"]}: {exc}; the bad download was removed', 'Rerun setup.') from exc
        archive.unlink()
    overlay = sources['overlay']
    file = REPO / overlay['path']
    if digest(file) != overlay['sha256']:
        raise StepError('assets', f'{file.name} SHA256 differs from asset-sources.json', 'Restore the committed installer inputs.')
    for rel in overlay['backups']:
        shutil.copy2(dest / 'data' / rel, dest / 'data' / (rel + '.bak'))
    with tarfile.open(file) as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts:
                raise StepError('assets', f'unsafe overlay member {member.name}', 'Restore the committed installer inputs.')
            write_file(dest / 'data' / member.name, bundle.extractfile(member).read())


def clean_caches(ws: Path) -> None:
    """Environments keep hard links to what they use, so the caches can go once setup has passed."""
    shutil.rmtree(ws / 'cache', ignore_errors=True)
    shutil.rmtree(ws / 'data/robocasa365/downloads', ignore_errors=True)


def write_paths(ws: Path) -> None:
    paths = json.loads(TEMPLATE.read_text())['paths']
    (ws / 'paths.json').write_text(json.dumps({'schema': 'attentionbench.paths.v1', 'paths': paths}, indent=2) + '\n')


REGISTRATION_ROOTS = {
    'executor': ['data/daily-experiments'],
    'validator': ['data/memory-validator'],
    # Must not overlap the source roots (the repository); seed 101 uses the contract registered in the repo.
    'contract': ['data/scene-contracts'],
    'source': ['repos/attentionbench', 'sim/runtime/venv/bin', 'sim/runtime/robocasa/robocasa',
               'sim/runtime/robosuite/robosuite', 'data/robocasa365', 'deployments/dev06',
               'deployments/openarm-dev-integration/dev-local'],
}


def write_registration_roots(ws: Path) -> None:
    """Memory registration roots for this workspace; the service requires executor and validator lists."""
    file = ws / 'repos/memory-m3/registration-roots.json'
    if file.exists() and json.loads(file.read_text() or '{}'):
        return
    roots = {role: [str((ws / rel).resolve()) for rel in rels if (ws / rel).is_dir()]
             for role, rels in REGISTRATION_ROOTS.items()}
    file.write_text(json.dumps(roots, indent=1) + '\n')


def run_planner(ws: Path, args, argv: list[str]) -> None:
    env = os.environ | {'PYTHONPATH': str(REPO)}
    try:
        command('planner', argv, cwd=REPO, env=env)
    except StepError as exc:
        if not args.update or STALE_PLANNER not in str(exc):
            raise
        print('Planner inputs changed; moving the old deployments aside and rebuilding.', flush=True)
        rebuild_planner(ws, args)
        command('planner', argv, cwd=REPO, env=env)


def bootstrap(args) -> int:
    ws = Path(args.workspace).expanduser().resolve()
    lock = data()
    plan = ['layout', 'tools: pinned uv, conda and Codex CLI in WS/tools',
            'repos: attentionbench, robocasa365_sim, attention_memory_service, robocasa, robosuite, curobo, robocasa_tasks',
            'ui-venv: Python 3.10 and pinned harness requirements', 'assets: seven official archives and 123577 SHA256 checks',
            'sim-env: Python 3.11 simulator installer', 'curobo-env: conda and pinned pip environment in planner chain',
            'planner: native assets, official cuRobo env and lock, d05/d06, proof, local trust, dev-local', 'paths: all template keys',
            'stores: UI, Memory, random 0600 keys.env']
    if args.dry_run:
        if getattr(args, 'rebuild_planner', False):
            validate_rebuild(ws, args)
        for line in plan:
            print(line)
        return 0
    if getattr(args, 'rebuild_planner', False):
        rebuild_planner(ws, args)
    for rel in ('repos', 'envs', 'sim', 'deployments', 'data'):
        (ws / rel).mkdir(parents=True, exist_ok=True)
    hostenv.apply(ws)
    try:
        tools.ensure(ws, lock['tools'])
    except (tools.ToolError, OSError) as exc:
        raise StepError('tools', str(exc), 'Check the network and disk space, then rerun setup; finished tools are kept.') from exc
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
    harness = REPO / 'benchmarks/attention_harness'
    stamped(ws, 'ui-venv', {'python': lock['python']['ui'], 'memory': lock['repos']['attention_memory_service'],
                            'requirements': {name: digest(harness / name) for name in
                                             ('setup_env.sh', 'requirements-robosuite.txt', 'requirements-orchestrator.txt')}},
            args.update, ui)
    sim_inputs = {'repos': [lock['repos'][x] for x in ('robocasa365_sim', 'robocasa', 'robosuite')],
                  'assets': {key: value for key, value in lock['assets'].items() if not key.startswith('sources')}}
    runtime_assets = ws / 'sim/runtime/robocasa/robocasa/models/assets'
    try:
        if args.assets_from:
            source = Path(args.assets_from).expanduser().resolve()
        elif stamp_current(ws, 'sim-env', sim_inputs) and runtime_assets.is_dir():
            source = runtime_assets
        else:
            source = download_assets(ws, lock)
    except StepError:
        raise
    except Exception as exc:
        raise StepError('assets', str(exc), 'Check the official download URL, disk space, and network; then rerun.') from exc
    manifest = ws / lock['assets']['installed_manifest']
    print(f'[assets] checking {source}', flush=True)
    bad = verify_assets(source, manifest, lock['assets']['installed_manifest_sha256'])
    if bad:
        raise StepError('assets', f'{len(bad)} mismatching files:\n' + '\n'.join(bad), 'Supply the complete official asset tree; no files were repaired.')
    verified = ws / 'data/robocasa365/assets-verified.json'
    verified.parent.mkdir(parents=True, exist_ok=True)
    def record_source(tree: Path) -> None:
        verified.write_text(json.dumps({'manifest_sha256': lock['assets']['installed_manifest_sha256'], 'source': str(tree)}) + '\n')
    record_source(source)
    def sim():
        runtime = ws / 'sim/runtime'
        if runtime.exists():  # Rebuilt from pinned inputs: an interrupted attempt, or inputs --update changed.
            print(f'[sim-env] removing incomplete or outdated {runtime}', flush=True)
            shutil.rmtree(runtime)
        command('sim-env', [sys.executable, str(ws / 'repos/robocasa365_sim/install.py'), '--runtime-root', str(runtime), '--asset-source', str(source)])
        command('sim-env', ['uv', 'pip', 'install', '--python', str(runtime / 'venv/bin/python'), '--no-deps', '-e', str(ws / 'repos/attention_memory_service')])
    stamped(ws, 'sim-env', sim_inputs, args.update, sim)
    if not args.assets_from and source != runtime_assets:
        # The runtime now holds the checked tree, so the staged copy is 23 GB of duplicates.
        record_source(runtime_assets)
        shutil.rmtree(source, ignore_errors=True)
        for stamp in (ws / 'data/installer/stamps').glob('asset-*.json'):
            stamp.unlink()
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
    (memory / 'store_id.txt').write_text(store_id + '\n')
    (memory / 'namespace.txt').write_text('attentionbench-' + secrets.token_hex(8) + '\n') if not (memory / 'namespace.txt').exists() else None
    for rel in ('data/daily-experiments', 'data/memory-validator', 'data/scene-contracts', 'data/robocasa365', 'deployments/openarm-dev-integration/dev-local'):
        (ws / rel).mkdir(parents=True, exist_ok=True)
    write_registration_roots(ws)
    if not args.skip_planner:
        # The chain imports the Harness (numpy, robosuite adapter), so it runs in the UI venv,
        # not in the bare system Python that starts bootstrap.
        argv = [str(ws / 'envs/ui/bin/python'), str(REPO / 'scripts/attentionbench/build_planner_chain.py'),
                '--workspace', str(ws)]
        if getattr(args, 'planner_project_from', None):
            argv += ['--project-from', str(args.planner_project_from)]
        if getattr(args, 'planner_native_assets_from', None):
            argv += ['--native-assets-from', str(args.planner_native_assets_from)]
        if args.update:
            argv.append('--update')
        run_planner(ws, args, argv)
    fingerprint = host_fingerprint()
    record = ws / 'data/run/host-fingerprint.json'
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(fingerprint, indent=2) + '\n')
    print(f'Host: {fingerprint["os"]["PRETTY_NAME"]}; kernel {fingerprint["kernel"]}; '
          f'glibc {fingerprint["glibc"]}; driver {fingerprint["driver"]}; gpu {fingerprint["gpu"]}')
    print(f'Host fingerprint: {record}')
    print(f'Bootstrap complete{" without planner" if args.skip_planner else ""}: {ws}')
    return 0


def missing_libraries() -> list[str]:
    cache = ldconfig_cache()
    return [name for name in SYSTEM_LIBRARIES if f'{name} ' not in cache]


def gpu_memory() -> list[tuple[int, int]]:
    """(index, free MiB) for every GPU; empty when nvidia-smi gives no answer."""
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.free', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=60)
        return [tuple(int(part) for part in line.split(',')) for line in result.stdout.splitlines() if line.strip()] if result.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _check(results: list, name: str, ok: bool | None, reason: str, fix: str) -> None:
    results.append({'check': name, 'status': 'SKIP' if ok is None else ('PASS' if ok else 'FAIL'), 'reason': reason, 'fix': fix})


def check_host_gate(results: list, value=None) -> None:
    failures, warnings = gate(value if value is not None else host_fingerprint())
    _check(results, 'host-prerequisites', not failures,
           '; '.join(failures) if failures else 'driver and glibc meet minimum versions',
           'Upgrade the NVIDIA driver or Ubuntu release to the required versions.' if failures else '')
    for warning in warnings:
        results.append({'check': 'host-os', 'status': 'WARN', 'reason': warning,
                        'fix': 'Upgrade to Ubuntu 22.04 or 24.04.'})


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


def _latest_host_upgrade() -> str:
    try:
        lines = Path('/var/log/apt/history.log').read_text().splitlines()
    except OSError:
        return ''
    return next((line.strip() for line in reversed(lines) if 'libc6' in line or 'nvidia' in line.lower()), '')


def check_host_libraries(results: list, ws: Path) -> None:
    path = ws / 'deployments/official-parent/runtime-lock.json'
    if not path.is_file():
        _check(results, 'host-libraries', None, 'official parent runtime lock missing', '')
        return
    try:
        dependencies = json.loads(path.read_text())['native_host_dependencies']
        if not isinstance(dependencies, dict):
            raise ValueError('native_host_dependencies is not an object')
    except (OSError, ValueError, KeyError) as exc:
        _check(results, 'host-libraries', False, f'unreadable native host dependencies: {exc}', 'Rebuild with setup.sh --workspace WS --rebuild-planner.')
        return
    resolved = host_libraries()
    bad = []
    for name, sha in dependencies.items():
        file = Path(name)
        token = 'libcuda.so.1' if file.name.startswith('libcuda') else 'libc.so.6' if file.name.startswith('libc') else None
        current = resolved.get(token)
        current_path = current['path'] if current else 'missing'
        if not file.is_file():
            bad.append(f'missing {name} (ldconfig now: {current_path})')
        elif digest(file) != sha:
            bad.append(f'changed {name} (ldconfig now: {current_path})')
    upgrade = _latest_host_upgrade()
    fix = ('The planner still starts in development tier; results are marked. Formal tier refuses. '
           + (f'Latest apt entry: {upgrade}. ' if upgrade else '')
           + 'setup.sh --workspace WS --reprove reruns the proof; --rebuild-planner makes a fresh lock.')
    if bad:
        results.append({'check': 'host-libraries', 'status': 'WARN', 'reason': '; '.join(bad), 'fix': fix})
    else:
        _check(results, 'host-libraries', True, 'native host dependency SHA256 values match', '')


def doctor(args) -> int:
    lock = data(); ws = Path(args.workspace or os.environ.get('ATTENTIONBENCH_WORKSPACE', Path.cwd())).expanduser().resolve()
    results = []
    check_host_gate(results)
    py = ws / 'envs/curobo-env/bin/python'
    probe = subprocess.run([str(py), '-c', 'import torch; print(torch.cuda.is_available())'], capture_output=True, text=True) if py.is_file() else None
    gpu_ok = bool(probe and probe.returncode == 0 and probe.stdout.strip() == 'True')
    _check(results, 'gpu', gpu_ok, 'torch CUDA available' if gpu_ok else 'GPU/driver not visible to cuRobo torch', 'Install a compatible NVIDIA driver and cuRobo env; verify torch.cuda.is_available().')
    isolated, reason, fix = namespace_status()
    _check(results, 'isolation', isolated, reason, fix)
    codex = shutil.which('codex')
    version = subprocess.run(['codex', '--version'], capture_output=True, text=True).stdout.strip() if codex else ''
    # The agents run inside this program on every model route; only the codex route needs a login.
    _check(results, 'codex-version', version == lock['codex']['version'], version or 'codex missing',
           f'Rerun setup.sh; it installs {lock["codex"]["version"]} in the workspace.')
    found = routes.available()
    results.append({'check': 'model-routes', 'status': 'PASS' if found else 'WARN',
                    'reason': 'ready: ' + ', '.join(found) if found else 'no model route is ready; setup and verify do not need one, run.sh does',
                    'fix': '' if found else '; '.join(f'{name}: {routes.ROUTES[name].hint}' for name in routes.AUTO_ORDER)})
    media = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
    _check(results, 'ffmpeg', media, 'ffmpeg and ffprobe available' if media else 'ffmpeg or ffprobe missing', 'Rerun setup.sh or install the ffmpeg package.')
    gaps = missing_libraries()
    _check(results, 'system-libraries', not gaps, 'found' if not gaps else 'missing ' + ', '.join(gaps),
           'sudo apt-get install ' + ' '.join(SYSTEM_LIBRARIES[name] for name in gaps))
    memory = gpu_memory()
    if memory and max(free for _, free in memory) < GPU_FREE_WARN_MIB:
        results.append({'check': 'gpu-memory', 'status': 'WARN', 'reason': f'{max(free for _, free in memory)} MiB free on the emptiest GPU',
                         'fix': 'Stop other GPU work or choose a GPU with CUDA_VISIBLE_DEVICES.'})
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
    check_host_libraries(results, ws)
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
    for name, port in zip(('ui', 'memory'), hostenv.saved_ports(ws)):
        with socket.socket() as sock:
            free = sock.connect_ex(('127.0.0.1', port)) != 0
        ours = hostenv.owned(ws, name)
        if free or ours:
            _check(results, f'port-{port}', True, 'free' if free else 'our process', '')
        else:  # start.sh and run.sh move to the next free port and remember it.
            results.append({'check': f'port-{port}', 'status': 'WARN', 'reason': 'busy; start will use the next free port', 'fix': ''})
    parent = ws
    while not parent.exists():
        parent = parent.parent
    free_gb = shutil.disk_usage(parent).free / 1024**3
    need = RUN_FREE_GB if registry.is_file() else INSTALL_FREE_GB
    _check(results, 'disk', free_gb >= need, f'{free_gb:.1f} GB free', f'Free at least {need} GB on the workspace filesystem.')
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
    b = sub.add_parser('bootstrap'); b.add_argument('--workspace', required=True); b.add_argument('--assets-from'); b.add_argument('--update', action='store_true'); b.add_argument('--skip-planner', action='store_true'); b.add_argument('--dry-run', action='store_true'); b.add_argument('--rebuild-planner', action='store_true'); b.add_argument('--planner-project-from'); b.add_argument('--planner-native-assets-from')
    d = sub.add_parser('doctor'); d.add_argument('--workspace'); d.add_argument('--json', action='store_true')
    args = p.parse_args()
    try:
        if args.command == 'bootstrap':
            return bootstrap(args)
        ws = args.workspace or os.environ.get('ATTENTIONBENCH_WORKSPACE') or hostenv.default_workspace(REPO) or str(Path.cwd())
        hostenv.apply(Path(ws).expanduser().resolve())
        args.workspace = ws
        return doctor(args)
    except StepError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'step: installer\nwhat failed: {exc}\nhow to fix: Inspect the workspace state and rerun bootstrap or doctor.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
