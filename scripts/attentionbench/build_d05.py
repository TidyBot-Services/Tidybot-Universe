"""Portable deployment-05 derivation with the author's carried d02-d04 layer.

The d05 source list and adapter rules follow the author's d05 builder. Earlier
adapter source files are read from the pinned commit; non-checkout files are
shipped as relocatable data. The GPU observation probe is compiled locally.
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
LAYER = Path(__file__).with_name('d04-layer')
AUTHOR_PROJECT_FILES = Path(__file__).with_name('d05-author-project-files.json')
YAMLS = ('config/openarm_fixed.yml',
         'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml')
PINNED_LAYER_SOURCES = (
    'benchmarks/attention_harness/formal_memory_contract.py',
    'benchmarks/attention_harness/formal_runner_boundary.py',
    'benchmarks/attention_harness/memory_service_client.py',
    'benchmarks/attention_harness/parcc_client.py',
    'benchmarks/attention_harness/robocasa365/tasks.py',
    'benchmarks/attention_harness/tests/test_main_ui_robocasa365_catalog.py',
    'benchmarks/attention_harness/tests/test_openarm365.py',
    'benchmarks/attention_harness/tests/test_openarm_robocasa.py',
    'benchmarks/attention_harness/tests/test_parcc_client.py',
    'curobo_v2/attachment.py', 'curobo_v2/client.py', 'curobo_v2/evidence.py',
    'curobo_v2/sapien_monitor.py',
    'curobo_v2/service/curobo_service/cooperative.py',
    'curobo_v2/service/curobo_service/geometry.py',
    'curobo_v2/service/curobo_service/planner.py',
    'curobo_v2/service/curobo_service/planner_core.py',
    'curobo_v2/service/curobo_service/server.py',
    'curobo_v2/session.py', 'curobo_v2/worker.py',
    'openarm_robocasa/run_ui.sh',
    'curobo_v2/deployment.py', 'curobo_v2/ik_path.py',
    'curobo_v2/ik_path_jump.py', 'curobo_v2/measured_kinematics.py',
    'curobo_v2/prepare_config.py',
    'curobo_v2/service/curobo_service/contact_scene.py',
    'curobo_v2/service/curobo_service/kinematics_diagnostic.py',
)
LAYER_FILES = (
    *YAMLS, 'config/openarm-model/robot.urdf',
    'curobo_v2/service/curobo_service/assets/ik_native_limits.yml',
    'curobo_v2/gpu_activity_probe.cpp',
    'openarm_phase0_recheck/acceptance_config.json',
    'openarm_phase0_recheck/acceptance_config.sha256',
    'openarm_phase0_recheck/gate_result.json',
    'openarm_phase0_recheck/validate.py',
)
PROBE = 'curobo_v2/gpu_activity_probe.so'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')


def files(root):
    paths = sorted(root.rglob('*'))
    if root.is_symlink() or any(path.is_symlink() for path in paths):
        raise ValueError('Symlinks prohibited in frozen runtime')
    return [path for path in paths if path.is_file()]


def build_probe(project, prefix):
    """Compile the author's CUPTI observation source in the pinned environment."""
    prefix = Path(prefix)
    include = prefix / 'targets/x86_64-linux/include'
    lib = prefix / 'targets/x86_64-linux/lib'
    if not (include / 'cupti.h').is_file() or not (lib / 'libcupti.so').is_file():
        raise ValueError('Pinned environment lacks CUPTI headers or library')
    subprocess.run(['g++', '-std=c++17', '-O2', '-fPIC', '-shared', '-pthread',
                    '-I', str(include), str(project / 'curobo_v2/gpu_activity_probe.cpp'),
                    '-L', str(lib), '-lcupti', '-Wl,-rpath,' + str(lib),
                    '-o', str(project / PROBE)], check=True)


def apply_layer(project, layer=LAYER):
    """Materialize the carried d02-d04 files after the r12 root was relocated."""
    project = Path(project)
    for name in LAYER_FILES:
        raw = (Path(layer) / name).read_bytes()
        if name in YAMLS:
            if b'@PROJECT_ROOT@' not in raw:
                raise ValueError('Adapter YAML lacks project-root marker: ' + name)
            raw = raw.replace(b'@PROJECT_ROOT@', str(project).encode())
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def build(out, *, parent, repo=REPO, source_commit=None, expected_parent_sha=None,
          layer=LAYER):
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
    names = tuple(declared['SOURCE_FILES'] + declared['NEW_FILES']) + (PINNED_LAYER_SOURCES if layer else ())
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
    if layer:
        apply_layer(project, layer)
        build_probe(project, base['prefix'])
        expected_files = json.loads(AUTHOR_PROJECT_FILES.read_text())
        actual_files = sorted(path.relative_to(project).as_posix() for path in files(project)
                              if not ({'.git', '__pycache__'} & set(path.relative_to(project).parts))
                              and not any(part.endswith('.egg-info') for part in path.relative_to(project).parts))
        if actual_files != expected_files:
            raise ValueError('Deployment-05 project file list differs from author inventory')
    hashes = {str(path): sha(path) for path in files(project)}
    changed = sorted(path.relative_to(project).as_posix() for path in files(project)
                     if not (old_project / path.relative_to(project)).is_file()
                     or sha(old_project / path.relative_to(project)) != sha(path))
    expected_changes = set(names) | set(relocated) | (set(LAYER_FILES) | {PROBE} if layer else set())
    if not set(changed) <= expected_changes:
        raise ValueError('Unexpected deployment-05 project differences: ' + str(set(changed) - expected_changes))
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
