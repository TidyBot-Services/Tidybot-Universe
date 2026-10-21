#!/usr/bin/env python3
"""Reseed the five labelled seed-102 Memory notes for the configured Dev SDK.

The 8769 service's drop-in supplies both the experiments root and namespace.
We run the same make_lock -> make_dev_config -> context code as experiment
creation in that service's source checkout, writing only a temporary lock.
The existing experiment root is inspected read-only for a ground-truth check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
DEFAULT_KEYS = Path('/home/truares/桌面/AttentionBench-Workspace/development/memory-m3/keys.env')
DROP_IN = Path.home() / '.config/systemd/user/attentionbench-8769.service.d/override.conf'
MEMORY_ENV = Path.home() / '.config/attentionbench-8769/memory-online.env'
ACTOR = 'claude-offline-operator (authorized by the user)'
OFF_REASON = 'SDK identity changed; replaced by notes for the current SDK'
SEED_REASON = 'test note for the Memory monitor; excluded from results'


def _env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f'Keys file not found: {path}')
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        if '=' not in line:
            continue
        name, value = line.split('=', 1)
        values[name.strip()] = value.strip().strip('"\'')
    return values


def keys(path: Path) -> dict[str, str]:
    values = _env_file(path)
    names = ('ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_OPERATOR_KEY',
             'ATTENTION_MEMORY_IMPORTER_KEY')
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise ValueError('Missing required key names: ' + ', '.join(missing))
    return {name: values[name] for name in names}


def service_settings() -> tuple[Path, str | None]:
    if not DROP_IN.is_file():
        raise FileNotFoundError(f'8769 service drop-in not found: {DROP_IN}')
    commands = [line.split('=', 1)[1] for line in DROP_IN.read_text().splitlines()
                if line.startswith('ExecStart=') and line.split('=', 1)[1]]
    command = commands[-1] if commands else None
    if command is None:
        raise ValueError('8769 service drop-in has no ExecStart')
    words = shlex.split(command)
    def argument(flag):
        return words[words.index(flag) + 1] if flag in words and words.index(flag) + 1 < len(words) else None
    root = argument('--dev-v2-root')
    if not root:
        raise ValueError('8769 service has no --dev-v2-root')
    namespace = argument('--dev-v2-memory-namespace')
    if not namespace and MEMORY_ENV.is_file():
        namespace = _env_file(MEMORY_ENV).get('ATTENTION_MEMORY_NAMESPACE')
    return Path(root), namespace


_BUILD_CONTEXT = r'''
import json, sys, tempfile
from pathlib import Path
from openarm365_dev_v2.dev_config import make_lock, make_dev_config
from openarm365_dev_v2.scene_contract import registered_contract_ref
from benchmarks.attention_harness.dev_planner_memory import context

with tempfile.TemporaryDirectory(prefix='attentionbench-reseed-') as directory:
    ref = make_lock(Path(directory) / 'identity-lock.json')
    config = make_dev_config('pick_place_counter_to_sink', 102, ref, budget_tier='dev')
    config['scene_contract'] = registered_contract_ref()
    config['scene_reset_seed'] = 102
    print(json.dumps(context(config, sys.argv[1]), sort_keys=True))
'''


def current_context(experiments_root: Path, namespace: str) -> dict:
    """Use the service checkout's run-creation primitives, without a run or DB write."""
    # An experiment lock is the authoritative recorded source checkout.
    locks = sorted(experiments_root.glob('devexp-*/artifacts/devexp-*/identity-lock.json'),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not locks:
        raise FileNotFoundError(f'No experiment identity lock under {experiments_root}')
    source_root = Path(json.loads(locks[0].read_text())['source_root'])
    if not (source_root / 'openarm365_dev_v2/dev_config.py').is_file():
        raise FileNotFoundError(f'Configured Dev source checkout not found: {source_root}')
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(source_root) + os.pathsep + environment.get('PYTHONPATH', '')
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    result = subprocess.run([sys.executable, '-c', _BUILD_CONTEXT, namespace],
                            cwd=source_root, env=environment, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        # Child diagnostics may include paths or service details; never echo them.
        raise RuntimeError('Could not build the current Dev SDK context from the configured checkout')
    value = json.loads(result.stdout)
    digest = value.get('sdk_sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Dev SDK context has no valid sdk_sha256')
    return value


class MemoryHTTP:
    def __init__(self, base_url: str, credentials: dict[str, str]):
        if not base_url.startswith(('http://127.0.0.1:', 'http://localhost:', 'https://')):
            raise ValueError('Memory service URL must use localhost HTTP or HTTPS')
        self.base_url = base_url.rstrip('/')
        self.credentials = credentials

    def request(self, method: str, path: str, body: dict | None = None, *, role: str = 'online'):
        headers = {'X-Memory-Service-Key': self.credentials['ATTENTION_MEMORY_API_KEY'],
                   'Content-Type': 'application/json'}
        if role == 'operator':
            headers['X-Memory-Operator-Key'] = self.credentials['ATTENTION_MEMORY_OPERATOR_KEY']
        elif role == 'importer':
            headers['X-Memory-Importer-Key'] = self.credentials['ATTENTION_MEMORY_IMPORTER_KEY']
        request = Request(self.base_url + path, method=method, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            with urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f'Memory service returned HTTP {error.code} for {method} {path}') from None
        except URLError:
            raise RuntimeError(f'Memory service unavailable for {method} {path}') from None


def plan(rows: list[dict], context: dict, notes: list[dict]) -> tuple[list[str], list[dict], list[str]]:
    digest = context['sdk_sha256']
    active = {'trusted', 'active'}
    old = []
    by_id = {row.get('memory_id'): row for row in rows}
    current_titles = set()
    for row in rows:
        scope = row.get('applicability_summary') or row.get('applicability') or {}
        if (row.get('seeded_for_tests') is True
                and row.get('status') in active
                and scope.get('sdk_sha256') == digest
                and isinstance(row.get('title'), str)):
            current_titles.add(row['title'].strip().casefold())
        if (row.get('seeded_for_tests') is True
                and scope.get('namespace') == context['namespace']
                and scope.get('sdk_sha256') != digest
                and row.get('status') in active):
            old.append(row['memory_id'])
    missing = []
    kept = []
    for note in notes:
        note_id = f"t-{digest[:8]}-{note['key']}"
        row = by_id.get(note_id)
        if row is not None:
            scope = row.get('applicability_summary') or row.get('applicability') or {}
            if (row.get('seeded_for_tests') is not True or row.get('status') not in active
                    or any(scope.get(key) != value for key, value in context.items())):
                raise ValueError(f'Current note ID is occupied or inactive: {note_id}')
        if row is not None or note['title'].strip().casefold() in current_titles:
            kept.append(note['title'])
        else:
            missing.append({'memory_id': note_id, 'context': context,
                            'actor': ACTOR, 'reason': SEED_REASON,
                            **{name: note[name] for name in ('title', 'summary', 'group', 'guidance', 'repair')}})
    return old, missing, kept


def reseed(gateway, context: dict, notes: list[dict], *, check=False, dry_run=False, out=print) -> int:
    rows = gateway.request('GET', '/dev-v2/memories')['memories']
    old, missing, kept = plan(rows, context, notes)
    digest = context['sdk_sha256']
    suffix = ' (changed since last seed)' if old else ''
    out(f'Current SDK identity {digest[:8]}…{suffix}')
    for title in kept:
        out(f'kept existing: {title}')
    if check:
        out('Ready' if not old and not missing else f'Needs reseed: {len(old)} old active, {len(missing)} missing')
        return 0 if not old and not missing else 1
    if dry_run:
        out(f'Would turn off {len(old)} old test notes')
        out(f'Would seed {len(missing)} notes')
        if not old and not missing:
            out('Nothing to do')
        return 0
    for note_id in old:
        gateway.request('POST', '/memories/' + quote(note_id, safe='') + '/disable',
                        {'actor': ACTOR, 'reason': OFF_REASON}, role='operator')
    for payload in missing:
        gateway.request('POST', '/dev-v2/test-seeded-notes', payload, role='importer')
    if old:
        out(f'Turned off {len(old)} old test notes')
    if missing:
        out(f'Seeded {len(missing)} notes')
    if not old and not missing:
        out('Nothing to do')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--namespace')
    parser.add_argument('--service-url', default='http://127.0.0.1:8878')
    parser.add_argument('--keys-file', type=Path, default=DEFAULT_KEYS)
    args = parser.parse_args(argv)
    try:
        experiments_root, configured_namespace = service_settings()
        namespace = args.namespace or configured_namespace
        if not namespace:
            raise ValueError('Memory namespace unavailable; pass --namespace')
        context = current_context(experiments_root, namespace)
        notes = json.loads((HERE / 'test_notes_seed102.json').read_text())
        credentials = keys(args.keys_file)
        return reseed(MemoryHTTP(args.service_url, credentials), context, notes,
                      check=args.check, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as error:
        # Never include HTTP response bodies, request headers, or credentials.
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
