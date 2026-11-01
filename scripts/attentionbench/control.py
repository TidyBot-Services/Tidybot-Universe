#!/usr/bin/env python3
"""Start and stop the workspace's loopback user processes."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def stop_one(run: Path, name: str) -> None:
    file = run / (name + '.pid')
    if not file.exists():
        return
    pid = int(file.read_text().strip())
    if alive(pid):
        cmdline = (Path('/proc') / str(pid) / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        expected = 'attention_memory_service' if name == 'memory' else 'benchmarks.attention_harness.ui_server'
        if expected not in cmdline or str(run.parent.parent) not in cmdline or os.getpgid(pid) != pid:
            raise RuntimeError(f'{name} PID file does not identify this workspace process; inspect {file}')
        os.killpg(pid, signal.SIGTERM)
        for _ in range(30):
            if not alive(pid):
                break
            time.sleep(.1)
        if alive(pid):
            os.killpg(pid, signal.SIGKILL)
    file.unlink(missing_ok=True)


def launch(run: Path, name: str, argv: list[str], cwd: Path, env: dict, url: str) -> None:
    file = run / (name + '.pid')
    if file.exists():
        pid = int(file.read_text().strip())
        if alive(pid) and health(url):
            return
        if alive(pid):
            raise RuntimeError(f'{name} PID exists but health check failed; inspect {run / (name + ".log")}')
        file.unlink()
    with (run / (name + '.log')).open('ab') as log:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    file.write_text(str(process.pid) + '\n')
    for _ in range(100):
        if health(url):
            return
        if process.poll() is not None:
            break
        time.sleep(.2)
    stop_one(run, name)
    raise RuntimeError(f'{name} failed health check at {url}; inspect {run / (name + ".log")}')


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('start', 'stop'))
    p.add_argument('--workspace', default=os.environ.get('ATTENTIONBENCH_WORKSPACE', str(Path.cwd())))
    p.add_argument('--ui-port', type=int, default=8769)
    p.add_argument('--memory-port', type=int, default=8878)
    a = p.parse_args()
    ws = Path(a.workspace).expanduser().resolve()
    run = ws / 'data/run'
    if a.action == 'stop':
        stop_one(run, 'ui'); stop_one(run, 'memory')
        print('Stopped workspace processes')
        return 0
    run.mkdir(parents=True, exist_ok=True)
    keys = ws / 'repos/memory-m3/keys.env'
    if not keys.is_file() or keys.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('keys.env missing or not mode 0600; rerun bootstrap')
    env = os.environ.copy()
    env['ATTENTIONBENCH_WORKSPACE'] = str(ws)
    for line in keys.read_text().splitlines():
        key, value = line.split('=', 1)
        if key not in {'ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_OPERATOR_KEY', 'ATTENTION_MEMORY_IMPORTER_KEY'}:
            raise RuntimeError('Unexpected keys.env entry')
        env[key] = value
    memory_root = ws / 'repos/attention_memory_service'
    env['PYTHONPATH'] = str(memory_root)
    memory = ws / 'repos/memory-m3'
    mp = a.memory_port
    up = a.ui_port
    launch(run, 'memory', [str(ws / 'sim/runtime/venv/bin/python'), '-m', 'attention_memory_service',
                           '--store-path', str(memory / 'store.sqlite3'), '--port', str(mp),
                           '--dev-v2-registration-roots', str(memory / 'registration-roots.json')],
           memory_root, env, f'http://127.0.0.1:{mp}/health')
    env['PYTHONPATH'] = str(ws / 'repos/attentionbench')
    try:
        launch(run, 'ui', [str(ws / 'envs/ui/bin/python'), '-m', 'benchmarks.attention_harness.ui_server',
                           '--store', str(ws / 'data/ui/attention.sqlite3'),
                           '--artifact-root', str(ws / 'data/ui/artifacts'),
                           '--dev-v2-root', str(ws / 'data/daily-experiments'),
                           # Same as the author's 8769: each run declares its own budget in its request.
                           '--dev-v2-per-run-budget',
                           '--memory-service-url', f'http://127.0.0.1:{mp}',
                           '--dev-v2-memory-store-id', (memory / 'store_id.txt').read_text().strip(),
                           '--dev-v2-memory-namespace', (memory / 'namespace.txt').read_text().strip(),
                           '--port', str(up)],
               ws / 'repos/attentionbench', env, f'http://127.0.0.1:{up}/ui/')
    except Exception:
        stop_one(run, 'memory')
        raise
    print(f'http://127.0.0.1:{up}/ui/')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'process control failed: {exc}; inspect data/run logs and rerun', file=sys.stderr)
        raise SystemExit(1)
