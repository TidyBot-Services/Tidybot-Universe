"""Build deployment-06 without commits or starting any runtime.

Default: freeze a source manifest and build. --register and --verify-against
operate on an existing output. The registry belongs to THIS script's checkout.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.attention_harness.workspace_paths import lazy
from curobo_v2.dev_local import source_names, source_manifest, checked_source

REPO = Path(__file__).resolve().parents[1]
ROOT = lazy('ui.dev_root')
PARENT = lazy('deployments.dev05_root')
DEFAULT_OUT = lazy('deployments.dev06_root')
PARENT_SHA = '0058f2d4d3d4a7d7894f53dcb99128c0db377cb216ab3cf747cb038f12b4e1a0'
OFFICIAL_SHA = '207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c'
OFFICIAL_COMMIT = '78fd485fa82d9b9a063fb4985e371814587e666a'
RELOCATED_YAMLS = ('config/openarm_fixed.yml', 'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml')
CONTROL = 'curobo_v2/trusted_deployments.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def real_files(root):
    paths = sorted(root.rglob('*'))
    if root.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('Symlinks prohibited in frozen runtime')
    return [p for p in paths if p.is_file()]


def verify_manifest(repo, manifest):
    for name, expected in manifest.items():
        if sha(checked_source(repo, name)) != expected:
            raise ValueError('Source manifest mismatch: ' + name)


def build(out=DEFAULT_OUT, *, parent=PARENT, repo=REPO, manifest=None,
          expected_parent_sha=PARENT_SHA):
    out, parent, repo = Path(out).absolute(), Path(parent).resolve(), Path(repo).resolve()
    if out.exists() or out.is_symlink():
        raise FileExistsError('Refusing existing destination: ' + str(out))
    if out.is_relative_to(parent) or parent.is_relative_to(out) or out.is_relative_to(repo):
        raise ValueError('Destination must be separate from parent and source')
    lock_path = parent / 'runtime-lock.json'
    if sha(lock_path) != expected_parent_sha:
        raise ValueError('Frozen deployment-05 lock changed')
    base = json.loads(lock_path.read_bytes())
    if (base['schema'] != 'attentionbench.curobo-v2.runtime.v1' or base['generation'] != 2
            or base['library_revision'] != OFFICIAL_COMMIT
            or base['provenance']['parent_lock_sha256'] != OFFICIAL_SHA):
        raise ValueError('Unexpected official parent identity')
    old_project = Path(base['project_root'])
    if old_project != parent / 'runtime/project':
        raise ValueError('Unexpected parent project root')
    real_files(parent / 'runtime')
    inventory = {str(p): sha(p) for p in real_files(old_project)}
    locked = {p: h for p, h in base['files'].items() if Path(p).is_relative_to(old_project)}
    if inventory != locked:
        raise ValueError('Parent project inventory or digest differs from lock')
    changes = json.loads((parent / 'adapter-changes.json').read_bytes())
    for name, entry in changes.items():
        if inventory.get(str(old_project / name)) != entry['adapter_sha256']:
            raise ValueError('Parent adapter change mismatch: ' + name)
    actual = source_manifest(repo, old_project)
    if manifest is None:
        manifest = actual
    if manifest != actual:
        raise ValueError('Source manifest differs from exact parent/source differences')
    verify_manifest(repo, manifest)
    out.mkdir(parents=True)
    shutil.copytree(parent / 'runtime', out / 'runtime', copy_function=shutil.copy2)
    for p in [out / 'runtime', *(out / 'runtime').rglob('*')]:
        p.chmod(p.stat().st_mode | 0o200)
    project = out / 'runtime/project'
    for name, expected in manifest.items():
        raw = checked_source(repo, name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Source changed during build: ' + name)
        dest = project / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        if sha(dest) != expected:
            raise ValueError('Copied file manifest mismatch: ' + name)
    for name in RELOCATED_YAMLS:
        raw = (old_project / name).read_bytes()
        if str(parent).encode() not in raw:
            raise ValueError('Expected parent path missing from relocated YAML: ' + name)
        (project / name).write_bytes(raw.replace(str(parent).encode(), str(out).encode()))
    if any(str(parent).encode() in (project / name).read_bytes() for name in RELOCATED_YAMLS):
        raise ValueError('Parent deployment path remains in runtime project')
    verify_manifest(repo, manifest)  # Detect edits while copying.
    for name, expected in manifest.items():
        if sha(project / name) != expected:
            raise ValueError('Final runtime manifest mismatch: ' + name)
    hashes = {str(p): sha(p) for p in real_files(project)}
    changed = sorted(p.relative_to(project).as_posix() for p in real_files(project)
                     if not (old_project / p.relative_to(project)).is_file()
                     or sha(old_project / p.relative_to(project)) != sha(p))
    if set(changed) != set(manifest) | set(RELOCATED_YAMLS):
        raise ValueError('Unexpected copied project differences')
    for name in changed:
        before = old_project / name
        changes[name] = dict(base_sha256=changes.get(name, {}).get('base_sha256', sha(before) if before.exists() else None),
                             adapter_sha256=sha(project / name))
    lock = json.loads(json.dumps(base))
    for field in ('project_root', 'service_root', 'assets_root'):
        lock[field] = lock[field].replace(str(parent), str(out))
    outside = {p: h for p, h in base['files'].items() if not Path(p).is_relative_to(old_project)}
    lock['files'] = dict(sorted((outside | hashes).items()))
    lock['robot_configs'] = {p.replace(str(parent), str(out)): sha(Path(p.replace(str(parent), str(out))))
                             for p in base['robot_configs']}
    lock['task_sources'] = {p.replace(str(parent), str(out)): sha(Path(p.replace(str(parent), str(out))) / 'robocasa_tasks/__init__.py')
                           for p in base['task_sources']}
    lock['provenance'] = dict(adapter_revision='deployment-06', profile='r3-20261025',
        base_adapter_lock_sha256=expected_parent_sha, parent_adapter_lock_path=str(lock_path),
        parent_adapter_lock_sha256=expected_parent_sha, parent_lock_sha256=OFFICIAL_SHA,
        parent_lock_path=base['provenance']['parent_lock_path'],
        official_provenance=base['provenance']['official_provenance'],
        source_manifest=manifest, relocated_project_files=list(RELOCATED_YAMLS), changed_project_files=changed)
    active = {k: v for k, v in lock.items() if k != 'provenance'}
    if str(parent) in json.dumps(active):
        raise ValueError('Parent deployment path remains in active lock')
    write_json(out / 'source-manifest.json', manifest)
    write_json(out / 'adapter-changes.json', changes)
    write_json(out / 'runtime-lock.json', lock)
    registration = {sha(out / 'runtime-lock.json'): dict(lock_path=str(out / 'runtime-lock.json'),
        official_commit=OFFICIAL_COMMIT, parent_sha256=OFFICIAL_SHA, derived_from_sha256=expected_parent_sha,
        adapter_changes=str(out / 'adapter-changes.json'), adapter_changes_sha256=sha(out / 'adapter-changes.json'),
        allowed_tiers=['dev'], profile='r3-20261025')}
    write_json(out / 'registration.json', registration)
    shutil.copy2(__file__, out / 'build_d06.py')
    for p in [*out.rglob('*'), out]:
        p.chmod((p.stat().st_mode & ~0o222) if p.is_file() else 0o555)
    return registration


def verify_against(out, repo):
    lock = json.loads((out / 'runtime-lock.json').read_bytes())
    manifest = lock['provenance']['source_manifest']
    if json.loads((out / 'source-manifest.json').read_bytes()) != manifest:
        raise ValueError('Provenance/source manifest mismatch')
    verify_manifest(repo, manifest)
    verify_manifest(Path(lock['project_root']), manifest)
    return {'verified_files': len(manifest), 'lock_sha256': sha(out / 'runtime-lock.json')}


def register(out, *, registry=None, receipt=None):
    registry = REPO / CONTROL if registry is None else Path(registry)
    receipt = REPO / 'deployment-06-registration-receipt.json' if receipt is None else Path(receipt)
    registration = json.loads((out / 'registration.json').read_bytes())
    key = sha(out / 'runtime-lock.json')
    if set(registration) != {key}:
        raise ValueError('Registration lock hash mismatch')
    entry = registration[key]
    if (entry['lock_path'] != str(out / 'runtime-lock.json') or entry['allowed_tiers'] != ['dev']
            or entry['profile'] != 'r3-20261025' or entry['derived_from_sha256'] != PARENT_SHA
            or entry['parent_sha256'] != OFFICIAL_SHA or entry['official_commit'] != OFFICIAL_COMMIT
            or entry['adapter_changes'] != str(out / 'adapter-changes.json')
            or entry['adapter_changes_sha256'] != sha(out / 'adapter-changes.json')):
        raise ValueError('Invalid deployment-06 registration')
    lock = json.loads((out / 'runtime-lock.json').read_bytes())
    if lock['provenance']['parent_adapter_lock_sha256'] != PARENT_SHA:
        raise ValueError('Invalid deployment-06 parent provenance')
    for path, expected in lock['files'].items():
        if Path(path).is_relative_to(lock['project_root']) and sha(path) != expected:
            raise ValueError('Runtime changed before registration: ' + path)
    raw = registry.read_bytes()
    entries = json.loads(raw)
    if key in entries or any(v.get('lock_path') == entry['lock_path'] for v in entries.values()):
        raise ValueError('Deployment already registered')
    end = len(raw.rstrip()) - 1
    if raw[end:end+1] != b'}':
        raise ValueError('Expected registry object')
    addition = json.dumps(registration, indent=2)[1:-1].encode()
    updated = raw[:end].rstrip() + (b',' if entries else b'') + addition + b'\n' + raw[end:]
    if json.loads(updated) != entries | registration:
        raise ValueError('Registry append failed')
    if receipt.exists():
        raise FileExistsError('Refusing existing registration receipt')
    record = dict(registry=str(registry), before_sha256=hashlib.sha256(raw).hexdigest(),
                  after_sha256=hashlib.sha256(updated).hexdigest(), lock_sha256=key)
    if registry.read_bytes() != raw:
        raise ValueError('Registry changed during registration')
    write_json(receipt, record)
    temporary = registry.with_suffix('.json.tmp')
    with temporary.open('xb') as stream:
        stream.write(updated)
    temporary.replace(registry)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--source-manifest', type=Path)
    parser.add_argument('--register', action='store_true')
    parser.add_argument('--verify-against', type=Path)
    args = parser.parse_args()
    out = args.output.absolute()
    if args.verify_against:
        result = verify_against(out, args.verify_against.resolve())
    elif args.register:
        result = register(out)
    else:
        manifest = None if args.source_manifest is None else json.loads(args.source_manifest.read_bytes())
        result = build(out, manifest=manifest)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
