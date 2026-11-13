#!/usr/bin/env python3
"""Inventory by default; apply only an explicitly supplied run list; never empty trash."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.attention_harness.frame_cleanup import plan, apply, restore, state, safe, _scan


def inventory(runs_root):
    root = safe(runs_root)
    runs = sorted({p.parent for p in root.rglob('manifest.json') if p.parent.name.startswith('devexp-')})
    rows = []
    for run in runs:
        row = dict(run_id=run.name, path=str(run), mode='unknown', terminal=False, active=True)
        try:
            row.update(state(run)); p = plan(run)
            row.update(bytes_now=p['bytes_now'], bytes_freed=p['bytes_freed'], frames_deleted=p['frames_deleted'],
                       skipped=[dict(execution=g['execution'], view=g['view'], reason=g['skipped']) for g in p['views'] if g['skipped']])
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            row.update(error=f'{type(exc).__name__}: {exc}', bytes_now=0, bytes_freed=0, frames_deleted=0, skipped=[])
        rows.append(row)
    # Physical occupancy across runs counts each shared inode once.
    seen = set(); now = 0
    for run in runs:
        try: paths = _scan(run)
        except (OSError, ValueError): continue
        for path in paths:
            st = path.stat(); key = (st.st_dev, st.st_ino)
            if key not in seen:now += st.st_size; seen.add(key)
    totals = dict(runs=len(rows), bytes_now=now,
                  bytes_freed=sum(r['bytes_freed'] for r in rows), frames_deleted=sum(r['frames_deleted'] for r in rows))
    return dict(runs=rows, totals=totals)


def markdown(value):
    lines = ['| Run | Mode | Terminal | Active | Bytes now | Bytes freed | Frames | Skipped views / reasons |',
             '| --- | --- | --- | --- | ---: | ---: | ---: | --- |']
    for r in value['runs']:
        reason = r.get('error') or '; '.join(g['view']+': '+g['reason'] for g in r['skipped'])
        lines.append('| '+' | '.join(str(v).replace('|','/').replace('\n',' ') for v in (
            r['run_id'], r['mode'], r['terminal'], r['active'], r['bytes_now'], r['bytes_freed'], r['frames_deleted'], reason))+' |')
    lines.append('\nTotals: '+json.dumps(value['totals'], sort_keys=True))
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs-root', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', default=False)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--restore', type=Path)
    parser.add_argument('--trash-dir', type=Path)
    parser.add_argument('--runs', type=Path, help='Approved JSON list of run IDs or absolute run paths (or one per line)')
    parser.add_argument('--output', type=Path, default=Path('frame-cleanup-backfill.json'))
    args = parser.parse_args()
    if args.restore:
        print(json.dumps(dict(restored=restore(args.restore)))); return
    if not args.runs_root:parser.error('--runs-root is required')
    value = inventory(args.runs_root)
    if args.apply:
        if not args.trash_dir or not args.runs:parser.error('--apply requires --trash-dir and --runs')
        raw = args.runs.read_text()
        try:approved = json.loads(raw)
        except ValueError:approved = [line.strip() for line in raw.splitlines() if line.strip()]
        if not isinstance(approved,list) or not all(isinstance(n,str) for n in approved):parser.error('runs file must contain a list of IDs or paths')
        matches = []
        for name in approved:
            candidates = [r for r in value['runs'] if name in {r['run_id'],r['path']}]
            if len(candidates) != 1:parser.error('run is missing or ambiguous: '+name)
            matches.append(candidates[0])
        # Resolve every requested run before the first mutation.
        value['applied'] = [apply(r['path'], trash_dir=args.trash_dir) for r in matches]
    args.output.write_text(json.dumps(value,indent=2)+'\n')
    table = markdown(value); args.output.with_suffix('.md').write_text(table)
    print(table)


if __name__ == '__main__': main()
