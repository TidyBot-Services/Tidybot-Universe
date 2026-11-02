#!/usr/bin/env python3
"""Start a locked development-tier agent run and collect a collaborator summary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.attentionbench import control, installer, setup

TEMPLATE = ROOT / 'config/attentionbench/run-request.template.json'
TERMINAL = {'completed', 'failed', 'cancelled', 'needs_review', 'condition_invalid', 'not_started'}
EID = re.compile(r'devexp-[a-f0-9]{24}\Z')


def request_body(task: str, seed: int, minutes: float, overrides: dict | None = None) -> dict:
    if not re.fullmatch(r'[a-z][a-z0-9_]*', task):
        raise ValueError('task must be a task id containing lowercase letters, digits and underscores')
    if seed < 0 or minutes <= 0:
        raise ValueError('seed must be non-negative and minutes must be positive')
    if overrides and set(overrides) - {'task_id', 'seed', 'wall_limit', 'goal_key', 'request_key'}:
        raise ValueError('request override is not allowed')
    body = json.loads(TEMPLATE.read_text())
    selection = body['selection']
    key = f'collab-{task}-seed{seed}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(3)}'
    selection.update(tasks=[task], seeds=[seed], wall_limit=int(minutes * 60) + 240,
                     goal_key=key, request_key=key)
    if overrides:
        for name, value in overrides.items():
            if name == 'task_id':
                selection['tasks'] = [value]
            elif name == 'seed':
                selection['seeds'] = [value]
            else:
                selection[name] = value
    def forbidden(value):
        if isinstance(value, dict):
            return any(k in {'key', 'api_key'} or forbidden(v) for k, v in value.items())
        if isinstance(value, list):
            return any(forbidden(v) for v in value)
        return False
    if forbidden(body):
        raise ValueError('request template contains a forbidden credential field')
    if any(selection[role] != 'gpt-6.1-sol' for role in ('dev_model', 'eval_model', 'advisor_model')):
        raise ValueError('run request models differ from the L2 lock')
    return body


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def post_json(url: str, token: str, body: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST', headers={
        'X-Attention-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode('utf-8', errors='replace')) from exc


def command(action: str, ws: Path, ui_port: int, memory_port: int) -> None:
    argv = [sys.executable, str(Path(control.__file__)), action, '--workspace', str(ws),
            '--ui-port', str(ui_port), '--memory-port', str(memory_port)]
    result = subprocess.run(argv, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end='', flush=True)
    if result.returncode:
        raise installer.StepError(action, (result.stderr or result.stdout).strip(),
                                  f'Inspect {ws / "data/run"} logs and retry {action}.')


def summary(ws: Path, eid: str, key: str, snapshot: dict) -> dict:
    cells = snapshot.get('cells', [])
    versions = [version for cell in cells for version in cell.get('versions', [])]
    verdicts = [version.get('eval_verdict', version.get('eval')) for version in versions
                if version.get('eval_verdict', version.get('eval')) is not None]
    run_dir = ws / 'data/run'
    run_dir.mkdir(parents=True, exist_ok=True)
    experiment_file = run_dir / f'{eid}-experiment.json'
    experiment_file.write_text(json.dumps(snapshot, indent=2) + '\n')
    result = {
        'experiment_id': eid, 'request_key': key, 'final_state': snapshot.get('state'),
        'version_count': len(versions), 'eval_verdict': verdicts[-1] if verdicts else None,
        'l3_files': ['bootstrap log (capture setup output)', 'doctor log (capture setup output)',
                     'fingerprint output', str(experiment_file)],
    }
    (run_dir / f'{eid}-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def execute(args: argparse.Namespace) -> int:
    ws = Path(args.workspace)
    if not ws.is_absolute() or ws != ws.resolve(strict=False):
        raise installer.StepError('workspace', 'workspace must be an absolute real path without symlinks',
                                  'Pass the same absolute workspace used by setup.sh.')
    if args.ui_port == args.memory_port:
        raise ValueError('UI and Memory ports must differ')
    body = request_body(args.task, args.seed, args.minutes)
    lock_handle = None
    if args.lock:
        lock_file = Path(args.lock)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = lock_file.open('a+')
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            raise installer.StepError('lock', f'{lock_file} is busy', 'Wait for the other run to finish.')
    try:
        base = f'http://127.0.0.1:{args.ui_port}'
        # The quota reading from setup's warm-up is usually stale by now (runs accept 10 minutes).
        setup.warmup(False)
        command('start', ws, args.ui_port, args.memory_port)
        try:
            token = control.control_token(base + '/ui/')
            created = post_json(base + '/api/dev-v2/experiments', token, body)
            eid = created.get('experiment_id')
            if not isinstance(eid, str) or not EID.fullmatch(eid):
                raise RuntimeError(f'create response has no valid experiment_id: {created}')
            print(f'experiment_id: {eid}\nUI: {base}/ui/', flush=True)
            resource = base + '/api/dev-v2/experiments/' + eid
            deadline = time.monotonic() + args.minutes * 60
            latest = created
            interrupted = False
            try:
                while True:
                    latest = get_json(resource)
                    if latest.get('state') in TERMINAL:
                        break
                    if time.monotonic() >= deadline:
                        print('Run time limit reached; cancelling.', flush=True)
                        break
                    time.sleep(min(20, max(0, deadline - time.monotonic())))
            except KeyboardInterrupt:
                interrupted = True
                print('Interrupted; cancelling.', flush=True)
            if latest.get('state') not in TERMINAL:
                print('cancel: ' + json.dumps(post_json(resource + '/cancel', token, {})), flush=True)
                cancel_deadline = time.monotonic() + 120
                while time.monotonic() < cancel_deadline:
                    latest = get_json(resource)
                    if latest.get('state') in TERMINAL:
                        break
                    time.sleep(2)
                if latest.get('state') not in TERMINAL:
                    raise RuntimeError(f'{eid} did not reach a terminal state after cancellation')
            result = summary(ws, eid, body['selection']['request_key'], latest)
            print(json.dumps(result, indent=2), flush=True)
            return 130 if interrupted else 0
        finally:
            if not args.no_stop:
                command('stop', ws, args.ui_port, args.memory_port)
    finally:
        if lock_handle:
            lock_handle.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', required=True)
    p.add_argument('--task', default='pick_place_counter_to_sink')
    p.add_argument('--seed', type=int, default=101)
    p.add_argument('--minutes', type=float, default=30)
    p.add_argument('--lock')
    p.add_argument('--no-stop', action='store_true')
    p.add_argument('--ui-port', type=int, default=8769)
    p.add_argument('--memory-port', type=int, default=8878)
    try:
        return execute(p.parse_args(argv))
    except installer.StepError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(installer.StepError('run', str(exc), 'Inspect data/run logs and rerun.'), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
