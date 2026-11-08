"""Collect stable reset fingerprints and print a host-variant JSON snippet.

Never writes a registered file, starts a planner or policy, or acquires sim.lock.
Review the evidence and obtain user approval before manually registering it and
updating REGISTERED_HOST_VARIANTS_SHA256.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys

from openarm365_dev_v2.scene_contract import load_contract, registered_contract_ref, seed_entry
from scripts.attentionbench.scene_fingerprint_check import fresh_probe, positive_int


def collect(task: str, seeds: list[int], processes: int, resets: int, variant_id: str) -> dict:
    if processes < 1 or resets < 1 or not seeds or not variant_id.strip():
        raise ValueError('positive processes/resets, seeds and a variant id are required')
    contract = load_contract(registered_contract_ref())
    references = {seed: seed_entry(contract, task, seed)['normalized_sha256'] for seed in seeds}
    entries = {}
    host = None
    for seed in seeds:
        fingerprints = []
        for _ in range(processes):
            row = fresh_probe(task, seed, resets)
            if row['seed'] != seed or len(row['normalized_sha256']) != resets:
                raise ValueError(f'incomplete reset evidence for seed {seed}')
            if host is not None and row['host'] != host:
                raise ValueError('host metadata changed during collection')
            host = row['host']
            fingerprints.extend(row['normalized_sha256'])
        if len(set(fingerprints)) != 1:
            raise ValueError(f'unstable normalized fingerprints for seed {seed}: {fingerprints}')
        if fingerprints[0] != references[seed]:
            entries[str(seed)] = {'normalized_sha256': fingerprints[0]}
    host = dict(host, observed_on=platform.node(), avx512='avx512f' in Path('/proc/cpuinfo').read_text().split())
    return {
        'id': variant_id,
        'host': host,
        'evidence': {
            'fresh_processes': processes, 'resets_per_process': resets,
            'date': datetime.now(timezone.utc).date().isoformat(),
        },
        'tasks': {task: {'seeds': entries}} if entries else {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--seeds', type=int, nargs='+', required=True)
    parser.add_argument('--processes', type=positive_int, default=5)
    parser.add_argument('--resets', type=positive_int, default=4)
    parser.add_argument('--id', required=True)
    args = parser.parse_args(argv)
    try:
        snippet = collect(args.task, args.seeds, args.processes, args.resets, args.id)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1
    print(json.dumps(snippet, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
