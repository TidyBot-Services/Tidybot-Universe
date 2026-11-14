#!/usr/bin/env bash
# Usage: idle-check.sh [daily-experiments-root] — read-only; exits 1 while busy.
set -eu
runs_root=${1:-/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments}
python3 - "$runs_root" <<'PY'
import json
from pathlib import Path
import subprocess
import sys
busy = False
for label, pattern in [('run workers', '[d]aily_dev_v2 --worker'), ('frame cleanup', '[f]rame_cleanup(\.py)?( |$)')]:
    result = subprocess.run(['pgrep', '-af', pattern], capture_output=True, text=True)
    if result.stdout.strip():
        print(label+':\n'+result.stdout.strip()); busy = True
root = Path(sys.argv[1])
try:
    for receipt in root.rglob('frame-cleanup.json'):
        try:
            if json.loads(receipt.read_bytes()).get('status') == 'in_progress':
                print('in_progress cleanup: '+str(receipt)); busy = True
        except (OSError, ValueError) as exc:
            print('cannot check cleanup: '+str(receipt)+': '+str(exc)); busy = True
except OSError as exc:
    print('cannot scan runs: '+str(exc)); busy = True
if not busy: print('idle')
sys.exit(1 if busy else 0)
PY
