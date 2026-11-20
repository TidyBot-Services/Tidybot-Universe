#!/usr/bin/env python3
"""Inventory state logs by default. Apply only --apply --runs approved.json.

Usage: state_log_dedupe.py --runs-root ROOT [--apply --runs approved.json]
Finished runs only; bytes and SHA-256 stay unchanged. Never follows symlinks.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.attention_harness.frame_cleanup import safe, state, run_lock, atomic, load
from benchmarks.attention_harness.dev_agent_workspace import shared_name


def fingerprint(path):
    path = safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):raise ValueError('not a regular file: '+str(path))
        digest = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):digest.update(block)
        after = os.fstat(fd)
        named = path.stat()
        if signature(before) != signature(after) or signature(named) != signature(after):
            raise ValueError('file changed while hashing: '+str(path))
        return digest.hexdigest(), after
    finally:os.close(fd)


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def groups(root):
    for canonical in sorted(root.rglob('agent/private/public/executions/e*/state_log.json')):
        if not re.fullmatch(r'e\d+', canonical.parent.name): continue
        safe(canonical)
        agent = canonical.parents[4]
        sha, info = fingerprint(canonical)
        candidates = [agent/'ws/executions'/canonical.parent.name/'state_log.json']
        # Content-addressed objects may be used by several cuts. Do not link an
        # object that is also read under a non-shared logical name.
        snapshots = agent/'private/snapshots'
        compatible = True
        for manifest in snapshots.rglob('*.json'):
            for name, entry in load(manifest).get('files', {}).items():
                if entry.get('sha256') == sha and not shared_name(name): compatible = False
        if compatible:candidates.append(snapshots/'objects'/sha)
        candidates.extend(sorted((agent/'private/eval').glob('v*/ws/state_log.json')))
        duplicates = []
        for path in candidates:
            # Sidecars are representations, never raw snapshot objects.
            if any(Path(str(path)+suffix).exists() for suffix in ('.zst', '.derived')):continue
            safe(path)
            if not path.exists(): continue
            if path.stat().st_size != info.st_size: continue
            other_sha, other = fingerprint(path)
            if other_sha == sha: duplicates.append((path, other))
        yield canonical, sha, info, duplicates


def inventory_run(run):
    root = safe(run)
    try:
        status = state(root)
        inodes = defaultdict(list); canonical_inodes = set()
        for canonical, sha, info, duplicates in groups(root):
            canonical_inodes.add((info.st_dev, info.st_ino))
            for path, other in duplicates: inodes[(other.st_dev, other.st_ino)].append((path, other))
        reclaim = sum(rows[0][1].st_size for key, rows in inodes.items() if key not in canonical_inodes
                      and len({p for p, _ in rows}) == rows[0][1].st_nlink)
        return dict(run_id=root.name, path=str(root), bytes_reclaimable=reclaim, **status)
    except Exception as exc:
        return dict(run_id=root.name, path=str(root), bytes_reclaimable=0, error=str(exc))


def guard(root):
    status = state(root)
    if not status['terminal'] or status['paused'] or status['active']:
        raise ValueError('dedupe requires a terminal, unpaused run with no live owner')


def apply_run(run):
    root = safe(run)
    with run_lock(root):
        guard(root)
        receipt = dict(schema='attentionbench.state-log-dedupe.v1', run=str(root), status='in_progress', files=[])
        receipt_path = root/'state-log-dedupe.json'
        atomic(receipt_path, receipt)
        try:
            for canonical, sha, info, duplicates in groups(root):
                # Check once again immediately before changing permissions.
                checked_sha, checked_info = fingerprint(canonical)
                if checked_sha != sha or signature(checked_info) != signature(info):raise ValueError('canonical digest/inode changed')
                guard(root)
                fd = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    if signature(os.fstat(fd)) != signature(checked_info):raise ValueError('canonical changed before chmod')
                    os.fchmod(fd, 0o444)
                finally:os.close(fd)
                for target, old in duplicates:
                    current_sha, current = fingerprint(target)
                    if signature(current)[:4] != signature(old)[:4] and (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError('duplicate changed before replacement: '+str(target))
                    if current_sha != sha:raise ValueError('duplicate digest changed')
                    source_sha, source = fingerprint(canonical)
                    if source_sha != sha or source.st_mode & 0o222:raise ValueError('canonical changed before replacement')
                    if (source.st_dev, source.st_ino) == (current.st_dev, current.st_ino):continue
                    temp = target.with_name('.state-log-link-'+uuid.uuid4().hex)
                    try:
                        os.link(canonical, temp, follow_symlinks=False)
                        linked_sha, linked = fingerprint(temp)
                        target_sha, checked = fingerprint(target)
                        source_sha, checked_source = fingerprint(canonical)
                        if (linked_sha != sha or target_sha != sha or source_sha != sha
                                or signature(checked) != signature(current)
                                or (linked.st_dev, linked.st_ino) != (source.st_dev, source.st_ino)
                                or (checked_source.st_dev, checked_source.st_ino) != (source.st_dev, source.st_ino)
                                or checked_source.st_mode & 0o222):
                            raise ValueError('sha/inode changed before replacement')
                        os.replace(temp, target)
                        fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:os.fsync(fd)
                        finally:os.close(fd)
                    finally:
                        if temp.exists():temp.unlink()
                    receipt['files'].append(dict(canonical=str(canonical.relative_to(root)),
                                                 path=str(target.relative_to(root)), sha256=sha))
                    atomic(receipt_path, receipt)
            receipt['status'] = 'completed'
        except Exception as exc:
            receipt.update(status='error', error=str(exc))
            raise
        finally:atomic(receipt_path, receipt)
        return receipt


def apply_batch(runs):
    rows = []
    for root in runs:
        try:rows.append(apply_run(root))
        except Exception as exc:rows.append(dict(run=str(root), status='refused: '+str(exc)))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--runs', type=Path, help='Approved JSON list of run IDs/paths, or one per line')
    args = parser.parse_args(argv)
    root = safe(args.runs_root)
    runs = sorted(p for p in root.iterdir() if p.name.startswith('devexp-') and p.is_dir())
    value = dict(runs=[inventory_run(p) for p in runs])
    if args.apply:
        if not args.runs:parser.error('--apply requires --runs')
        raw = args.runs.read_text()
        try: approved = json.loads(raw)
        except ValueError:approved = [line.strip() for line in raw.splitlines() if line.strip()]
        if not isinstance(approved, list) or not all(isinstance(n, str) for n in approved):parser.error('runs must be a list of strings')
        matches = []
        for name in approved:
            candidates = [p for p in runs if name in (p.name, str(p))]
            if len(candidates) != 1:parser.error('run is missing or ambiguous: '+name)
            matches.append(candidates[0])
        value['applied'] = apply_batch(matches)
    print(json.dumps(value, indent=2))


if __name__ == '__main__': main()
