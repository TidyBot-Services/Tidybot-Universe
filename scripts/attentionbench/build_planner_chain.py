#!/usr/bin/env python3
"""Build and authenticate the portable, development-only cuRobo planner chain."""
from __future__ import annotations

import argparse
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from curobo_v2 import identity
from scripts.attentionbench import official_lock
from scripts.attentionbench.installer import StepError, command, data, digest

REQUESTS = Path(__file__).with_name('planner-proof-requests.json')
PROJECT_FILES = Path(__file__).with_name('r12-project-files.json')


def stamp(ws, step, inputs, outputs, action):
    path = ws / 'data/installer/stamps' / ('planner-' + step + '.json')
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    if path.is_file():
        old = json.loads(path.read_text())
        if old.get('input_sha256') == key and all(Path(p).exists() for p in outputs):
            print(f'[planner:{step}] already complete')
            return
        raise StepError('planner:' + step, 'stamp inputs or outputs changed',
                        'Restore the stamped outputs or remove this planner step and its stamp, then rerun.')
    try:
        action()
        if not all(Path(p).exists() for p in outputs):
            raise ValueError('step did not produce every required output')
    except StepError:
        raise
    except Exception as exc:
        raise StepError('planner:' + step, str(exc),
                        'Inspect the differing entry or command, repair the local input, and rerun this step.') from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'input_sha256': key}, indent=2) + '\n')


def project_snapshot(source, project):
    names = json.loads(PROJECT_FILES.read_text())
    missing = [name for name in names if not (source / name).is_file()]
    if missing:
        raise ValueError(f'project source lacks {len(missing)} r12 files: ' + ', '.join(missing[:15]))
    if project.exists():
        raise ValueError('Project snapshot exists without stamp: ' + str(project))
    for name in names:
        src, dst = source / name, project / name
        if src.is_symlink() or not src.resolve().is_relative_to(source.resolve()):
            raise ValueError('Linked project source: ' + name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # The two robot YAMLs in the r12 snapshot contain its deployment root.
    # Relocate those active paths before the first lock, exactly as the d05/d06
    # builders relocate their parent deployment paths.
    if source.parts[-2:] == ('runtime', 'project'):
        old_root = str(source.parent.parent).encode()
        new_root = str(project.parent.parent).encode()
        for name in ('config/openarm_fixed.yml',
                     'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml'):
            target = project / name
            raw = target.read_bytes()
            if old_root not in raw:
                raise ValueError('Project source lacks relocatable r12 root: ' + name)
            target.write_bytes(raw.replace(old_root, new_root))


def native_assets(source, dest, report):
    if not source.is_dir() or source.is_symlink():
        raise ValueError('native asset source must be an unlinked directory containing data/')
    root = source / 'data' if (source / 'data').is_dir() else source
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked native asset: ' + str(path))
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            rows.append((rel, official_lock.sha(path), path.stat().st_size, path))
    expected = json.loads((identity.OFFICIAL_MANIFESTS / 'r12-native-assets.json').read_text())
    hashes = {rel: sha for rel, sha, _, _ in rows}
    encoded = json.dumps(hashes, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    actual = {'count': len(hashes), 'sha256': hashlib.sha256(encoded).hexdigest()}
    wanted = {key: expected[key] for key in actual}
    if actual != wanted:
        raise ValueError('native assets manifest mismatch: ' + json.dumps(official_lock._difference(actual, wanted)))
    data_root = dest / 'data'
    if source.resolve() != dest.resolve():
        for rel, _, _, path in rows:
            target = data_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or official_lock.sha(target) != hashes[rel]:
                shutil.copy2(path, target)
    entries = []
    for rel, sha, size, path in rows:
        target = data_root / rel
        if not target.is_file() or official_lock.sha(target) != sha:
            raise ValueError('native asset copy differs: ' + rel)
        entries.append({'path': str(target), 'relative': rel, 'source': str(path), 'sha256': sha, 'bytes': size})
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({'schema': 'attentionbench.native-assets.v1', 'result': 'PASS',
        'source_root': str(source), 'asset_env_root': str(dest), 'data_root': str(data_root),
        'files': entries, 'count': len(entries), 'bytes': sum(row[2] for row in rows)}, indent=2) + '\n')


def host_libraries():
    # ldconfig resolves SONAMEs to absolute host paths, as r12 native-maps did.
    output = subprocess.check_output(['ldconfig', '-p'], text=True)
    result = []
    for token in ('libcuda.so.1', 'libc.so.6'):
        candidates = [line.rsplit('=>', 1)[1].strip() for line in output.splitlines()
                      if line.strip().startswith(token + ' ') and '=>' in line]
        paths = [Path(path).resolve() for path in candidates if Path(path).is_file()]
        if not paths:
            raise ValueError('native host library unavailable: ' + token)
        result.append(paths[0])
    return result


def proof(d06_lock, output, requests=REQUESTS):
    import yaml
    from curobo_v2.session import V2Service
    spec = json.loads(Path(requests).read_bytes())
    lock = json.loads(Path(d06_lock).read_bytes())
    config = Path(lock['project_root']) / spec['robot_config']
    doc = yaml.safe_load(config.read_text())
    names = doc['service']['joint_names']
    cspace = doc['robot_cfg']['kinematics']['cspace']
    values = dict(zip(cspace['joint_names'], cspace['default_joint_position']))
    q = [float(values[name]) for name in names]
    evidence = output.parent / 'proof-evidence'
    results = []
    with V2Service(lock_path=d06_lock, robot_config=config, evidence=evidence,
                   deadline=time.monotonic() + 600, tier='dev', boundary='dev_loop') as client:
        world = {'env_id': spec['env_id'], **spec['world']}
        client.call('/world/cuboids', world)
        for item in spec['plans']:
            target = q.copy()
            target[item['joint_index']] += item['delta_rad']
            request = {'env_id': spec['env_id'], 'current_q': q, 'target_qpos': target,
                       'joint_names': names}
            result = client.call('/plan/joint', request)
            if result.get('status') != 'success':
                raise ValueError('plan request did not succeed: ' + str(result.get('reason')))
            results.append({'request': request, 'result': result})
    raw = json.dumps(results, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'passed': True, 'request_set_sha256': digest(Path(requests)),
                                  'result_sha256': hashlib.sha256(raw).hexdigest()}, indent=2) + '\n')


def check_parent(parent_data):
    differences = official_lock.official_differences(parent_data)
    if differences or not identity._official_content(parent_data):
        raise StepError('planner:official', 'official-content check failed: ' + json.dumps(differences, sort_keys=True),
                        'Restore the listed source, distribution, dependency, prefix, or native asset entries.')


def build(args):
    ws = Path(args.workspace).expanduser().resolve()
    os.environ['ATTENTIONBENCH_WORKSPACE'] = str(ws)
    lock = data()
    if args.dry_run:
        for step in ('env: exact Conda/Pip pins and editable official cuRobo',
                     'assets: native digest and local project snapshot',
                     'official: r12 schema and official-content checks',
                     'd05: pinned adapter sources', 'd06: current checkout adapter differences',
                     'proof: two owned dev-tier plans', 'registry: local 0444 trust',
                     'snapshot: dev-local materialization'):
            print(step)
        return
    prefix = ws / 'envs/curobo-env'
    library = ws / 'repos/curobo'
    parent = ws / 'deployments/official-parent'
    project = parent / 'runtime/project'
    native = ws / 'sim/curobo-native-assets'
    report = native / 'native-assets-files.json'
    d05 = ws / 'deployments/dev05'
    d06 = ws / 'deployments/dev06'
    proof_path = ws / 'deployments/planner-build-proof.json'
    registry = ws / 'deployments/local-dev-trust.json'
    snapshot = ws / 'deployments/openarm-dev-integration/dev-local'
    spec = REPO / lock['curobo_env']['conda_explicit']
    req = REPO / lock['curobo_env']['pip_requirements']
    if digest(spec) != lock['curobo_env']['conda_explicit_sha256'] or digest(req) != lock['curobo_env']['pip_requirements_sha256']:
        raise StepError('planner:env', 'committed environment spec or requirements digest differs', 'Restore committed installer inputs.')
    def make_env():
        if prefix.exists():
            raise ValueError('unstamped cuRobo environment exists: ' + str(prefix))
        command('planner:env', ['conda', 'create', '--yes', '--prefix', str(prefix), '--file', str(spec)])
        command('planner:env', [str(prefix / 'bin/python'), '-m', 'pip', 'install', '-r', str(req)])
        command('planner:env', [str(prefix / 'bin/python'), '-m', 'pip', 'install', '--no-deps', '-e', str(library)])
        status = subprocess.check_output(['git', '-C', str(library), 'status', '--porcelain'], text=True).strip()
        if status:
            raise ValueError('cuRobo checkout is dirty after editable install: ' + status)
        for path in library.rglob('*'):
            if path.is_file() and not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o222)
    stamp(ws, 'env', {'spec': digest(spec), 'requirements': digest(req), 'commit': lock['repos']['curobo']['commit']},
          [prefix / 'bin/python'], make_env)
    source = Path(args.project_from).resolve() if args.project_from else REPO
    stamp(ws, 'project', {'source': str(source), 'inventory': digest(PROJECT_FILES)}, [project],
          lambda: project_snapshot(source, project))
    asset_source = Path(args.native_assets_from).resolve() if args.native_assets_from else ws / 'data/robocasa365/assets'
    stamp(ws, 'native-assets', {'source': str(asset_source), 'manifest': digest(identity.OFFICIAL_MANIFESTS / 'r12-native-assets.json')},
          [report], lambda: native_assets(asset_source, native, report))
    parent_lock = parent / 'runtime-lock.json'
    def make_parent():
        if parent_lock.exists():
            raise ValueError('unstamped official parent lock exists')
        host = host_libraries()
        cmd = [str(prefix / 'bin/python'), '-B', str(Path(__file__).with_name('write_official_lock.py')),
               '--library', str(library), '--project', str(project), '--report', str(report),
               '--output', str(parent_lock), '--native-host', *map(str, host)]
        command('planner:official', cmd, cwd=REPO)
    stamp(ws, 'official', {'env': str(prefix), 'project': str(project), 'native': digest(report)}, [parent_lock], make_parent)
    parent_data = json.loads(parent_lock.read_bytes())
    check_parent(parent_data)
    parent_sha = digest(parent_lock)
    from scripts.attentionbench.build_d05 import build as build_d05
    stamp(ws, 'd05', {'parent': parent_sha, 'source_commit': lock['planner_author_provenance']['d05_source_commit']},
          [d05 / 'runtime-lock.json'], lambda: build_d05(d05, parent=parent, expected_parent_sha=parent_sha))
    from scripts.build_d06 import build as build_d06
    d05_sha = digest(d05 / 'runtime-lock.json')
    d05_data = json.loads((d05 / 'runtime-lock.json').read_bytes())
    if not identity._derived_from_parent(d05_data, parent_data):
        raise StepError('planner:d05', 'derived lock changes official or external identity', 'Rebuild d05 from the verified official parent.')
    stamp(ws, 'd06', {'parent': d05_sha, 'repo': str(REPO)}, [d06 / 'runtime-lock.json'],
          lambda: build_d06(d06, parent=d05, repo=REPO, expected_parent_sha=d05_sha, expected_official_sha=parent_sha))
    d06_sha = digest(d06 / 'runtime-lock.json')
    d06_data = json.loads((d06 / 'runtime-lock.json').read_bytes())
    if not identity._derived_from_parent(d06_data, parent_data):
        raise StepError('planner:d06', 'derived lock changes official or external identity', 'Rebuild d06 from the verified d05 and checkout.')
    stamp(ws, 'proof', {'d06': d06_sha, 'requests': digest(REQUESTS)}, [proof_path],
          lambda: proof(d06 / 'runtime-lock.json', proof_path))
    def register():
        from scripts.attentionbench.register_local_dev_trust import register as register_trust
        register_trust(parent_lock, d05 / 'runtime-lock.json', d06 / 'runtime-lock.json', snapshot, proof_path)
    stamp(ws, 'registry', {'parent': parent_sha, 'd05': d05_sha, 'd06': d06_sha, 'proof': digest(proof_path)},
          [registry], register)
    if registry.stat().st_mode & 0o777 != 0o444:
        raise StepError('planner:registry', 'registry mode is not 0444', 'Restore the protected local registry.')
    def make_snapshot():
        from curobo_v2.dev_local import materialize
        materialize(REPO)
    stamp(ws, 'snapshot', {'d06': d06_sha, 'checkout': str(REPO)}, [snapshot], make_snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--project-from', help='complete r12 runtime/project input tree; defaults to this checkout')
    parser.add_argument('--native-assets-from', help='complete native asset source tree; defaults to workspace data assets')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        build(args)
    except StepError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
