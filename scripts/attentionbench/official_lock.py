"""Portable r12 lock generation.

The file inventory, distribution metadata and dependency collection below are
adapted from r12/scripts/build_lock.py (author artifact, 2026-10-11). Paths are
parameters here; no author directory is imported or required at runtime.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from curobo_v2 import identity

DEPS = ('torch', 'sapien', 'numpy', 'scipy', 'fastapi', 'uvicorn', 'yaml',
        'warp', 'setuptools_scm', 'cuda.core', 'cuda.pathfinder')
ROBOT_CONFIGS = ('config/openarm_fixed.yml',
                 'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _difference(actual, expected):
    return {name: {'actual': actual.get(name), 'expected': expected.get(name)}
            for name in sorted(actual.keys() | expected.keys())
            if actual.get(name) != expected.get(name)}


def official_differences(lock, manifests=None):
    """Mirror identity._official_content checks with actionable entry differences."""
    root = manifests or identity.OFFICIAL_MANIFESTS
    read = lambda name: json.loads((root / name).read_bytes())
    result = {}
    source = identity._portable_files(lock, lock['library_root'])
    expected = read('curobo-78fd485-source.json')
    mismatch = _difference(source, expected)
    for name, digest in source.items():
        path = Path(lock['library_root']) / name
        if not path.is_file() or sha(path) != digest:
            mismatch[name] = {'actual': sha(path) if path.is_file() else None, 'expected': digest}
    if mismatch:
        result['source'] = mismatch
    mismatch = _difference(lock['distributions'], read('r12-distributions.json'))
    if mismatch:
        result['distributions'] = mismatch
    prefix = Path(lock['prefix'])
    allowed = set(read('allowed-path-files.json'))
    dependencies = {name: {'path': str(Path(item['path']).relative_to(prefix)), 'sha256': item['sha256']}
                    for name, item in lock['dependencies'].items()}
    expected = read('r12-dependencies.json')
    mismatch = {name: values for name, values in _difference(dependencies, expected).items()
                if name not in expected or name not in dependencies
                or dependencies[name]['path'] != expected[name]['path']
                or dependencies[name]['path'] not in allowed}
    if mismatch:
        result['dependencies'] = mismatch
    actual = identity._portable_files(lock, prefix)
    expected = read('r12-prefix-files.json')
    mismatch = {name: values for name, values in _difference(actual, expected).items()
                if name not in allowed or name not in actual or name not in expected}
    if mismatch:
        result['prefix files'] = mismatch
    native = lock['native_assets']
    report = Path(native['manifest'])
    if not report.is_file() or sha(report) != native['manifest_sha256']:
        result['native assets'] = {'manifest': {'actual': sha(report) if report.is_file() else None,
                                                 'expected': native['manifest_sha256']}}
    else:
        entries = json.loads(report.read_bytes())['files']
        assets = {row['relative']: row['sha256'] for row in entries}
        expected = read('r12-native-assets.json')
        if expected.get('format') == 'canonical-relative-path-sha256-v1':
            encoded = json.dumps(assets, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            actual = {'count': len(assets), 'sha256': hashlib.sha256(encoded).hexdigest()}
            mismatch = _difference(actual, {key: expected[key] for key in actual})
        else:
            mismatch = _difference(assets, expected)
        if mismatch:
            result['native assets'] = mismatch
    if not lock.get('official_only') or lock.get('library_revision') != identity.OFFICIAL_COMMIT or lock.get('library_base') != identity.OFFICIAL_COMMIT:
        result['source'] = result.get('source', {}) | {'official identity': {'actual': lock.get('library_revision'), 'expected': identity.OFFICIAL_COMMIT}}
    host = lock.get('native_host_dependencies', {})
    if not host or not any('libcuda' in name for name in host) or not any('libc.so.6' in name for name in host):
        result['native assets'] = result.get('native assets', {}) | {'native host dependencies': {'actual': sorted(host), 'expected': ['libcuda', 'libc.so.6']}}
    return result


def build(library, project, report, out, native_host):
    library, project, report, out = map(Path, (library, project, report, out))
    head = subprocess.check_output(['git', '-C', str(library), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(library), 'status', '--porcelain'], text=True).strip()
    if head != identity.OFFICIAL_COMMIT or dirty:
        raise ValueError(f'official source HEAD/status mismatch: {head}; {dirty}')
    files = {}
    names = subprocess.check_output(['git', '-C', str(library), 'ls-files', '-z']).decode().split('\0')
    for name in names:
        if name and (library / name).is_file():
            files[str(library / name)] = sha(library / name)
    official_count = len(files)
    for path in project.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            files[str(path)] = sha(path)
    dependencies = {}
    for name in DEPS:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        files[str(path)] = sha(path)
        dependencies[name] = {'path': str(path), 'sha256': sha(path)}
    distributions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name'].lower().replace('_', '-')
        if name in distributions:
            raise ValueError('duplicate distribution ' + name)
        distributions[name] = distribution.version
        for item in distribution.files or []:
            if Path(str(item)).name in {'METADATA', 'direct_url.json', 'WHEEL', 'RECORD'}:
                path = Path(distribution.locate_file(item)).resolve()
                if path.is_file():
                    files[str(path)] = sha(path)
    for path in Path(sys.prefix).glob('lib/python*/site-packages/__editable__*curobo*'):
        if path.is_file():
            files[str(path)] = sha(path)
    python = Path(sys.executable).resolve()
    files[str(python)] = sha(python)
    report_data = json.loads(report.read_bytes())
    files[str(report)] = sha(report)
    mani = Path(sys.prefix) / 'lib/python3.11/site-packages/mani_skill/__init__.py'
    files[str(mani)] = sha(mani)
    import curobo
    lock = {'schema': 'attentionbench.curobo-v2.runtime.v1', 'generation': 2,
            'prefix': sys.prefix, 'python': str(python), 'library_root': str(library),
            'library_revision': identity.OFFICIAL_COMMIT, 'library_base': identity.OFFICIAL_COMMIT,
            'module_version': curobo.__version__,
            'distribution_version': importlib.metadata.version('nvidia-curobo'),
            'files': files, 'dependencies': dependencies, 'distributions': distributions,
            'service_root': str(project / 'curobo_v2/service'), 'project_root': str(project),
            'assets_root': str(project / 'curobo_v2/service/curobo_service/assets'),
            'robot_configs': {str(project / name): sha(project / name) for name in ROBOT_CONFIGS},
            'official_only': True, 'official_files': official_count,
            'task_sources': {str(project / 'native-task-source'): sha(project / 'native-task-source/robocasa_tasks/__init__.py')},
            'native_assets': {'manifest': str(report), 'manifest_sha256': sha(report),
                              'env_root': report_data['asset_env_root'], 'data_root': report_data['data_root'],
                              'file_count': report_data['count'], 'bytes': report_data['bytes']},
            'native_host_dependencies': {str(Path(path).resolve()): sha(path) for path in native_host},
            'provenance': 'Fresh clone from https://github.com/NVlabs/curobo.git at exact upstream HEAD 78fd485; no solver edits or method replacement. Service/config kept separate.'}
    differences = official_differences(lock)
    if differences or not identity._official_content(lock):
        raise ValueError('official-content check failed: ' + json.dumps(differences or {'unknown': 'identity._official_content rejected lock'}, sort_keys=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(lock, indent=2, sort_keys=True) + '\n').encode()
    out.write_bytes(raw)
    (out.parent / 'runtime-lock.sha256').write_text(hashlib.sha256(raw).hexdigest() + '  runtime-lock.json\n')
    return lock
