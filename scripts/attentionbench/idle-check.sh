#!/usr/bin/env bash
# Usage: idle-check.sh [daily-experiments-root] — read-only; exits 1 while busy.
set -eu
runs_root=${1:-/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments}
python3 - "$runs_root" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys

def _processes(proc_root=Path('/proc')):
    for proc in proc_root.glob('[0-9]*/cmdline'):
        if int(proc.parent.name) == os.getpid(): continue
        try: args = proc.read_bytes().decode(errors='replace').split('\0')
        except OSError: continue
        if args and args[-1] == '': args.pop()
        if args: yield proc.parent, args

def _is_cleanup_process(args):
    return (any(a == '-m' and b == 'benchmarks.attention_harness.frame_cleanup'
                for a, b in zip(args, args[1:])) or
            bool(args and Path(args[0]).name.startswith('python') and
                 any(Path(arg).name in {'frame_cleanup_backfill.py', 'state_log_dedupe.py'} for arg in args)))

busy = False
result = subprocess.run(['pgrep', '-af', '[d]aily_dev_v2 --worker'], capture_output=True, text=True)
if result.stdout.strip():
    print('run workers:\n'+result.stdout.strip()); busy = True
for proc, args in _processes():
    if _is_cleanup_process(args):
        print('frame cleanup:\n'+proc.name+' '+repr(args)); busy = True
root = Path(sys.argv[1])
budget = 100000
def entries(path):
    global budget
    try:
        with os.scandir(path) as rows:
            for entry in rows:
                budget -= 1
                if budget < 0: raise ValueError('receipt scan limit exceeded')
                if entry.is_dir(follow_symlinks=False): yield Path(entry.path)
    except FileNotFoundError: return
def inspect(folder, run_id):
    global busy
    receipt = folder/'frame-cleanup.json'
    try:
        with receipt.open('rb') as stream: raw = stream.read(1024*1024*1024+1)
        if len(raw) > 1024*1024*1024: raise ValueError('receipt too large')
        value = json.loads(raw)
        if not isinstance(value, dict): raise ValueError('cleanup receipt must be an object')
        if value.get('status') == 'in_progress':
            print('in_progress cleanup: '+run_id+' '+str(receipt)); busy = True
    except FileNotFoundError: pass
    except (OSError, ValueError) as exc:
        print('cannot check cleanup: '+run_id+' '+str(receipt)+': '+str(exc)); busy = True
def inspect_run(folder, run_id):
    inspect(folder, run_id)
    for cell in entries(folder/'cells'):
        inspect(cell, run_id)
try:
    for folder in entries(root):
        inspect_run(folder, folder.name)
        for artifact in entries(folder/'artifacts'):
            inspect_run(artifact, folder.name)
except (OSError, ValueError) as exc:
    print('cannot scan runs: '+str(exc)); busy = True
if not busy: print('idle')
sys.exit(1 if busy else 0)
PY
