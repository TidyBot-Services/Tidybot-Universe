#!/usr/bin/env python3
"""Start and stop the workspace's loopback user processes."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.attentionbench import hostenv

# A cold disk and a loaded machine can take far longer than a warm start's few seconds.
START_TIMEOUT = 180


def process_identity(pid: int) -> dict | None:
    """Same record as benchmarks.attention_harness.dev_loop.process_identity, kept here so
    stop runs under bare system Python without importing the harness."""
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] == 'Z':
            return None
        return dict(pid=pid, start_ticks=stat[19], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError, IndexError):
        return None


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


def live_workers(ws: Path) -> list[tuple[str, int, dict, Path]]:
    workers = []
    for receipt in (ws / 'data/daily-experiments').glob('*/scheduler-process-*.json'):
        try:
            identity = json.loads(receipt.read_text())
            pid = identity['pid']
            if isinstance(pid, int) and pid > 0 and process_identity(pid) == identity:
                workers.append((receipt.parent.name, pid, identity, receipt.parent / 'scheduler.log'))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return workers


def control_token(ui_url: str) -> str:
    with urllib.request.urlopen(ui_url, timeout=3) as response:
        page = response.read().decode('utf-8')
    match = re.search(r'data-control-token="([^"]+)"', page)
    if not match:
        raise RuntimeError('UI control token missing from /ui/')
    return html.unescape(match.group(1))


def cancel_worker(ui_url: str, token: str, experiment: str) -> str:
    url = ui_url.rstrip('/') + f'/api/dev-v2/experiments/{experiment}/cancel'
    request = urllib.request.Request(url, data=b'{}', method='POST', headers={
        'X-Attention-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        return exc.read().decode('utf-8', errors='replace')


def wait_workers(workers: list[tuple[str, int, dict, Path]], seconds: float) -> list[tuple[str, int, dict, Path]]:
    end = time.monotonic() + seconds
    remaining = [worker for worker in workers if process_identity(worker[1]) == worker[2]]
    while remaining and time.monotonic() < end:
        time.sleep(min(.1, max(0, end - time.monotonic())))
        remaining = [worker for worker in remaining if process_identity(worker[1]) == worker[2]]
    return remaining


def stop_workers(ws: Path, ui_port: int, timeout: float) -> int:
    workers = live_workers(ws)
    if not workers:
        return 0
    ui_url = f'http://127.0.0.1:{ui_port}'
    if health(ui_url + '/ui/'):
        try:
            token = control_token(ui_url + '/ui/')
            for experiment in sorted({worker[0] for worker in workers}):
                try:
                    print(f'{experiment} cancel: {cancel_worker(ui_url, token, experiment)}', flush=True)
                except Exception as exc:
                    print(f'{experiment} cancel: {exc}', flush=True)
        except Exception as exc:
            print(f'worker cancellation failed: {exc}', flush=True)
    remaining = wait_workers(workers, timeout)
    for experiment, pid, identity, log in remaining:
        if process_identity(pid) == identity:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
                else:
                    print(f'{experiment} pid {pid}: not a process group leader; inspect {log}', flush=True)
            except ProcessLookupError:
                pass
    remaining = wait_workers(remaining, 10)
    if remaining:
        details = '; '.join(f'{experiment} pid {pid} log {log}' for experiment, pid, _, log in remaining)
        raise RuntimeError(f'workers still live: {details}')
    return len(workers)


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
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if health(url):
            return
        if process.poll() is not None:
            break
        time.sleep(.2)
    stop_one(run, name)
    raise RuntimeError(f'{name} failed health check at {url}; inspect {run / (name + ".log")}')


def emptiest_gpu() -> dict:
    """On a machine with several GPUs, run on the one with the most free memory."""
    if os.environ.get('CUDA_VISIBLE_DEVICES'):
        return {}
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.free', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=60)
        rows = [tuple(int(part) for part in line.split(',')) for line in result.stdout.splitlines() if line.strip()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if len(rows) < 2:
        return {}
    index = str(max(rows, key=lambda row: row[1])[0])
    # nvidia-smi numbers cards by PCI bus; CUDA must use the same numbering for this index to mean the same card.
    return dict(CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=index, MUJOCO_EGL_DEVICE_ID=index)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('start', 'stop'))
    p.add_argument('--workspace', default=os.environ.get('ATTENTIONBENCH_WORKSPACE') or hostenv.default_workspace(REPO) or str(Path.cwd()))
    p.add_argument('--ui-port', type=int, help='default: the workspace port, or the next free one')
    p.add_argument('--memory-port', type=int, help='default: the workspace port, or the next free one')
    p.add_argument('--worker-timeout', type=float, default=60)
    a = p.parse_args()
    ws = Path(a.workspace).expanduser().resolve()
    hostenv.apply(ws)
    run = ws / 'data/run'
    if a.action == 'stop':
        if a.worker_timeout < 0:
            raise ValueError('--worker-timeout must be non-negative')
        stopped = stop_workers(ws, a.ui_port or hostenv.saved_ports(ws)[0], a.worker_timeout)
        stop_one(run, 'ui'); stop_one(run, 'memory')
        print(f'Stopped workspace processes; {stopped} workers stopped')
        return 0
    run.mkdir(parents=True, exist_ok=True)
    keys = ws / 'repos/memory-m3/keys.env'
    if not keys.is_file() or keys.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('keys.env missing or not mode 0600; rerun bootstrap')
    up, mp = hostenv.choose_ports(ws, a.ui_port, a.memory_port)
    hostenv.save_ports(ws, up, mp)
    env = os.environ.copy()
    env.update(emptiest_gpu())
    env.update(ATTENTIONBENCH_WORKSPACE=str(ws), ATTENTIONBENCH_SERVICE_PORTS=f'{up},{mp}')
    for line in keys.read_text().splitlines():
        key, value = line.split('=', 1)
        if key not in {'ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_OPERATOR_KEY', 'ATTENTION_MEMORY_IMPORTER_KEY'}:
            raise RuntimeError('Unexpected keys.env entry')
        env[key] = value
    memory_root = ws / 'repos/attention_memory_service'
    env['PYTHONPATH'] = str(memory_root)
    memory = ws / 'repos/memory-m3'
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
