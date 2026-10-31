"""Portable deployment-05 derivation from a locally locked official parent.

The source list and adapter rules come from the author's deployment-05/build_d05.py.
The pinned commit is recorded in attentionbench.lock.json and read with git show.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from curobo_v2 import identity

REPO = Path(__file__).resolve().parents[2]
SOURCE_LIST = Path(__file__).with_name('d05-sources.json')
YAMLS = ('config/openarm_fixed.yml',
         'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def files(root):
    paths = sorted(root.rglob('*'))
    if root.is_symlink() or any(path.is_symlink() for path in paths):
        raise ValueError('Symlinks prohibited in frozen runtime')
    return [path for path in paths if path.is_file()]


def build(out, *, parent, repo=REPO, source_commit=None, expected_parent_sha=None):
    out, parent, repo = Path(out).absolute(), Path(parent).absolute(), Path(repo).resolve()
    if source_commit is None:
        source_commit = json.loads((REPO / 'attentionbench.lock.json').read_text())['planner_author_provenance']['d05_source_commit']
    parent_path = parent / 'runtime-lock.json'
    parent_sha = sha(parent_path)
    if expected_parent_sha is not None and parent_sha != expected_parent_sha:
        raise ValueError('Official parent lock SHA256 mismatch')
    base = json.loads(parent_path.read_bytes())
    if not identity._official_content(base):
        raise ValueError('Official parent failed content verification')
    if out.exists() or out.is_symlink() or out.is_relative_to(parent) or parent.is_relative_to(out):
        raise ValueError('Deployment-05 destination exists or overlaps parent')
    old_project = Path(base['project_root'])
    if old_project != parent / 'runtime/project':
        raise ValueError('Unexpected parent project root')
    inventory = {str(path): sha(path) for path in files(old_project)}
    locked = {path: digest for path, digest in base['files'].items() if Path(path).is_relative_to(old_project)}
    if inventory != locked:
        raise ValueError('Parent project inventory or digest differs from lock')
    declared = json.loads(SOURCE_LIST.read_text())
    names = tuple(declared['SOURCE_FILES'] + declared['NEW_FILES'])
    source = {}
    for name in names:
        source[name] = subprocess.check_output(['git', 'show', f'{source_commit}:{name}'], cwd=repo)
    out.mkdir(parents=True)
    shutil.copytree(parent / 'runtime', out / 'runtime', copy_function=shutil.copy2)
    for path in [out / 'runtime', *(out / 'runtime').rglob('*')]:
        path.chmod(path.stat().st_mode | 0o200)
    project = out / 'runtime/project'
    for name, raw in source.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    relocated = []
    for path in files(project):
        raw = path.read_bytes()
        if str(parent).encode() in raw:
            path.write_bytes(raw.replace(str(parent).encode(), str(out).encode()))
            relocated.append(path.relative_to(project).as_posix())
    if set(relocated) != set(YAMLS):
        raise ValueError('Unexpected relocated project paths: ' + str(relocated))
    hashes = {str(path): sha(path) for path in files(project)}
    changed = sorted(path.relative_to(project).as_posix() for path in files(project)
                     if not (old_project / path.relative_to(project)).is_file()
                     or sha(old_project / path.relative_to(project)) != sha(path))
    if not set(changed) <= set(names) | set(relocated):
        raise ValueError('Unexpected deployment-05 project differences: ' + str(set(changed) - (set(names) | set(relocated))))
    changes = {name: {'base_sha256': sha(old_project / name) if (old_project / name).is_file() else None,
                      'adapter_sha256': sha(project / name)} for name in changed}
    lock = json.loads(json.dumps(base))
    for field in ('project_root', 'service_root', 'assets_root'):
        lock[field] = lock[field].replace(str(parent), str(out))
    outside = {path: digest for path, digest in base['files'].items() if not Path(path).is_relative_to(old_project)}
    lock['files'] = dict(sorted((outside | hashes).items()))
    lock['robot_configs'] = {path.replace(str(parent), str(out)): sha(path.replace(str(parent), str(out)))
                             for path in base['robot_configs']}
    lock['task_sources'] = {path.replace(str(parent), str(out)): sha(Path(path.replace(str(parent), str(out))) / 'robocasa_tasks/__init__.py')
                            for path in base['task_sources']}
    lock['provenance'] = {'adapter_revision': 'deployment-05',
                          'base_adapter_lock_sha256': parent_sha,
                          'parent_adapter_lock_path': str(parent_path),
                          'parent_adapter_lock_sha256': parent_sha,
                          'parent_lock_path': str(parent_path),
                          'parent_lock_sha256': parent_sha,
                          'official_provenance': base['provenance'],
                          'changed_project_files': changed,
                          'source_commit': source_commit,
                          'source_files_from_head': list(names),
                          'scope': 'Pinned deployment-05 adapter changes over locally locked official parent'}
    if not identity._derived_from_parent(lock, base):
        raise ValueError('Deployment-05 changed official or external identity')
    write(out / 'adapter-changes.json', changes)
    write(out / 'runtime-lock.json', lock)
    for path in [*out.rglob('*'), out]:
        path.chmod((path.stat().st_mode & ~0o222) if path.is_file() else 0o555)
    return {'lock_sha256': sha(out / 'runtime-lock.json'), 'changed_project_files': changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--source-commit')
    parser.add_argument('--expected-parent-sha')
    args = parser.parse_args()
    print(json.dumps(build(args.output, parent=args.parent, repo=args.repo,
                           source_commit=args.source_commit, expected_parent_sha=args.expected_parent_sha), indent=2))


if __name__ == '__main__':
    main()
