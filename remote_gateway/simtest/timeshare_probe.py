"""Read-only recorder for the real time-share test: the robot token's line, GPU memory, disk and Codex quota.

  python -m remote_gateway.simtest.timeshare_probe --lock <sim.lock> --run-root <runs> --out probe.jsonl

It never takes the lock or writes the line: the line is opened read-only (sqlite mode=ro) and the lock is only looked
up in /proc/locks. One JSON object per line:
  token   a ticket appeared (asked), was granted, or left the line (released); with its label and times
  gpu     memory used / total in MiB and the processes using it (every --gpu-seconds)
  disk    free GB of the run root's file system (every --slow-seconds)
  quota   Codex weekly used percent (every --slow-seconds)
`report` turns the file into the time-share numbers for the report.
"""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time


def tickets(queue):
    try:
        db = sqlite3.connect(f'file:{queue}?mode=ro', uri=True, timeout=2)
        try:return {seq: dict(label=label, requested_at=r, granted_at=g) for seq, label, r, g in db.execute('SELECT seq, label, requested_at, granted_at FROM tickets')}
        finally:db.close()
    except sqlite3.Error:return None


def gpu():
    try:
        total = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout.split(',')
        apps = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_memory', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout
        return dict(used_mib=int(total[0]), total_mib=int(total[1]), apps=[dict(pid=int(a), mib=int(b)) for a, b in (l.split(',') for l in apps.strip().splitlines() if ',' in l)])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:return dict(error=str(error))


def record(args):
    queue = Path(args.lock).with_name(Path(args.lock).name + '.queue.sqlite3')
    out = open(args.out, 'a', encoding='utf-8', buffering=1)
    write = lambda **row:out.write(json.dumps(dict(at=round(time.time(), 3), **row)) + '\n')
    known = {};last_gpu = last_slow = 0
    while True:
        now = time.time();line = tickets(queue)
        if line is not None:
            for seq, row in line.items():
                if seq not in known:write(kind='token', event='asked', seq=seq, **row)
                if row['granted_at'] and not (known.get(seq) or {}).get('granted_at'):write(kind='token', event='granted', seq=seq, **row)
            for seq in set(known) - set(line):write(kind='token', event='released', seq=seq, **known[seq])
            known = line
        if now - last_gpu >= args.gpu_seconds:last_gpu = now;write(kind='gpu', **gpu())
        if now - last_slow >= args.slow_seconds:
            last_slow = now;write(kind='disk', free_gb=round(shutil.disk_usage(args.run_root).free / 2**30, 1))
            try:
                from benchmarks.attention_harness.dev_agent_quota import read_weekly_quota
                q = read_weekly_quota();write(kind='quota', used_percent=q and q['used_percent'], observed_at=q and q['observed_at'])
            except Exception as error:write(kind='quota', error=str(error))
        time.sleep(args.poll)


def report(path):
    rows = [json.loads(l) for l in open(path, encoding='utf-8')]
    turns = {}
    for r in rows:
        if r['kind'] != 'token':continue
        t = turns.setdefault(r['seq'], dict(seq=r['seq'], label=r['label'], asked=r['requested_at'], granted=r.get('granted_at'), released=None))
        if r['event'] == 'granted':t['granted'] = r['granted_at']
        if r['event'] == 'released':t['released'] = r['at']
    spans = sorted((t for t in turns.values() if t['granted']), key=lambda t:t['granted'])
    overlaps = [(a['seq'], b['seq']) for a, b in zip(spans, spans[1:]) if a['released'] and a['released'] > b['granted'] + 1.0]  # The line is sampled every second.
    waits = [t['granted'] - t['asked'] for t in spans]
    gpus = [r for r in rows if r['kind'] == 'gpu' and 'used_mib' in r]
    return dict(turns=list(turns.values()), executions=len(spans), never_granted=[t for t in turns.values() if not t['granted']], overlaps=overlaps,
                wait_mean_seconds=round(sum(waits) / len(waits), 1) if waits else None, wait_max_seconds=round(max(waits), 1) if waits else None,
                gpu_peak_mib=max((g['used_mib'] for g in gpus), default=None), gpu_total_mib=gpus[0]['total_mib'] if gpus else None,
                disk_free_gb=[r['free_gb'] for r in rows if r['kind'] == 'disk'][::max(1, len(rows) // 400)],
                quota=[r.get('used_percent') for r in rows if r['kind'] == 'quota'])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lock');parser.add_argument('--run-root');parser.add_argument('--out', required=True)
    parser.add_argument('--poll', type=float, default=1);parser.add_argument('--gpu-seconds', type=float, default=5);parser.add_argument('--slow-seconds', type=float, default=60)
    parser.add_argument('--report', action='store_true', help='print the numbers from --out and exit')
    args = parser.parse_args()
    if args.report:
        json.dump(report(args.out), sys.stdout, ensure_ascii=False, indent=1);return
    record(args)


if __name__ == '__main__':
    main()
