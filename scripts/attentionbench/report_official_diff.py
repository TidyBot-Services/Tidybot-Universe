#!/usr/bin/env python3
"""Read-only byte differences against committed official manifests."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_differences(category, root, names, expected, workspace_bytes):
    rows = []
    for name in sorted(set(names) | set(expected)):
        path = root / name
        actual = sha(path) if path.is_file() else None
        if actual != expected.get(name):
            rows.append({'category': category, 'file': name, 'path': str(path), 'actual_sha256': actual,
                         'expected_sha256': expected.get(name),
                         'contains_workspace_path': path.is_file() and workspace_bytes in path.read_bytes()})
    return rows


def report(library, manifests, workspace):
    library, manifests = Path(library).resolve(), Path(manifests).resolve()
    prefix = Path(sys.prefix).resolve()
    workspace_bytes = str(Path(workspace).resolve()).encode()
    read = lambda name: json.loads((manifests / name).read_bytes())
    source_names = [n for n in subprocess.check_output(['git', '-C', str(library), 'ls-files', '-z']).decode().split('\0') if n]
    expected_source = read('curobo-78fd485-source.json')
    expected_prefix = read('r12-prefix-files.json')
    prefix_names = set(expected_prefix)
    actual_dependencies = {}
    for name in ('torch', 'sapien', 'numpy', 'scipy', 'fastapi', 'uvicorn', 'yaml',
                 'warp', 'setuptools_scm', 'cuda.core', 'cuda.pathfinder'):
        path = Path(importlib.import_module(name).__file__).resolve()
        actual_dependencies[name] = {'path': str(path.relative_to(prefix)), 'sha256': sha(path)}
        prefix_names.add(str(path.relative_to(prefix)))
    distributions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata['Name'].lower().replace('_', '-')
        distributions[name] = distribution.version
        for item in distribution.files or []:
            if Path(str(item)).name in {'METADATA', 'direct_url.json', 'WHEEL', 'RECORD'}:
                path = Path(distribution.locate_file(item)).resolve()
                if path.is_file() and path.is_relative_to(prefix):
                    prefix_names.add(str(path.relative_to(prefix)))
    for path in prefix.glob('lib/python*/site-packages/__editable__*curobo*'):
        if path.is_file():
            prefix_names.add(str(path.relative_to(prefix)))
    prefix_names.add(str(Path(sys.executable).resolve().relative_to(prefix)))
    mani = prefix / 'lib/python3.11/site-packages/mani_skill/__init__.py'
    prefix_names.add(str(mani.relative_to(prefix)))
    rows = []
    for category, root, names, expected in (
            ('source', library, set(source_names) | set(expected_source), expected_source),
            ('prefix', prefix, prefix_names, expected_prefix)):
        rows.extend(file_differences(category, root, names, expected, workspace_bytes))
    for name, expected in sorted(read('r12-dependencies.json').items()):
        actual = actual_dependencies.get(name)
        if actual != expected:
            path = prefix / actual['path'] if actual else None
            rows.append({'category': 'dependency', 'file': actual['path'] if actual else expected['path'],
                         'path': str(path) if path else None,
                         'module': name, 'actual_sha256': actual['sha256'] if actual else None,
                         'expected_sha256': expected['sha256'],
                         'contains_workspace_path': bool(path and path.is_file() and workspace_bytes in path.read_bytes())})
    expected_distributions = read('r12-distributions.json')
    print(json.dumps({'schema': 'attentionbench.official-diff.v1', 'files': rows,
                      'distributions': {name: {'actual': distributions.get(name), 'expected': expected_distributions.get(name)}
                          for name in sorted(distributions.keys() | expected_distributions.keys())
                          if distributions.get(name) != expected_distributions.get(name)}},
                     sort_keys=True, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'manifests', 'workspace'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    report(args.library, args.manifests, args.workspace)


if __name__ == '__main__':
    main()
