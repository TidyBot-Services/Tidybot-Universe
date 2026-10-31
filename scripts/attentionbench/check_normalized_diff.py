#!/usr/bin/env python3
"""Check a saved official diff against real files without starting services."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from curobo_v2 import official_normalization


def check(diff: Path, env: Path, local_prefix: str, local_library: str, manifests: Path):
    raw = diff.read_text()
    rows = json.loads(raw[raw.index('{'):])['files']
    author_paths = json.loads((manifests / 'author-paths.json').read_bytes())
    wheels = json.loads((manifests / 'r12-wheel-normalized.json').read_bytes())
    records = json.loads((manifests / 'r12-record-normalized.json').read_bytes())
    shebangs = json.loads((manifests / 'r12-bin-shebangs.json').read_bytes())
    expected_files = json.loads((manifests / 'r12-prefix-files.json').read_bytes())
    counts = Counter()
    failed = []
    seen = set()
    for row in rows:
        if (row['category'] != 'prefix' or row['file'] in seen
                or expected_files.get(row['file']) != row['expected_sha256']):
            failed.append(row['file'])
            continue
        seen.add(row['file'])
        rule = official_normalization.matches(
            row['file'], env / row['file'], row['actual_sha256'], row['expected_sha256'],
            local_prefix, local_library, author_paths, wheels, records, shebangs)
        if rule is None:
            failed.append(row['file'])
        else:
            counts[rule] += 1
    return {'total_differences': len(rows), 'accepted_by_rule': dict(sorted(counts.items())),
            'failed': failed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diff', required=True, type=Path)
    parser.add_argument('--env', required=True, type=Path)
    parser.add_argument('--local-prefix', required=True)
    parser.add_argument('--local-library', required=True)
    parser.add_argument('--manifests', type=Path, default=Path(__file__).resolve().parents[2] / 'curobo_v2/official')
    args = parser.parse_args()
    result = check(args.diff, args.env, args.local_prefix, args.local_library, args.manifests)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
