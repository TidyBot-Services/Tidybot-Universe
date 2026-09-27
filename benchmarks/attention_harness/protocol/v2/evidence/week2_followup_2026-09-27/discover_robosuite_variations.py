"""Discover simulator-attested development variations for engineering smoke."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from robosuite_sim.client import RobosuiteSimClient

ROOT = Path(__file__).resolve().parent
SERVICE = Path('/home/truares/桌面/robosuite_sim-service')
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
with (ROOT / 'discovery_service.log').open('wb') as log:
    process = subprocess.Popen(
        [sys.executable, '-m', 'robosuite_sim', '--port', str(port), '--enable-sim-gt'],
        cwd=SERVICE, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, env={**os.environ, 'MUJOCO_GL': 'egl'},
    )
    client = RobosuiteSimClient(f'http://127.0.0.1:{port}', timeout=90)
    cases = []
    try:
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError('service exited during startup')
            try:
                if client.health()['status'] == 'ok':
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise TimeoutError('service startup')
        for task in ('cube_lift', 'cube_stack'):
            for seed in (101, 102, 103):
                _, _, _, metadata, applied = client.reset_attested(
                    task_id=task, seed=seed, camera=True,
                    camera_name='agentview', camera_height=64, camera_width=64,
                    horizon=500, discover_variation=True,
                )
                cases.append({'task': task, 'seed': seed, 'variation': applied,
                              'versions': metadata['versions'], 'native_at_reset': client.native_success()})
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        try:
            os.killpg(process.pid, 0)
            group_gone = False
        except ProcessLookupError:
            group_gone = True
        stop = {'pid': process.pid, 'exit_code': process.returncode,
                'leader_reaped': process.poll() is not None,
                'process_group_gone': group_gone}
(ROOT / 'robosuite_variations.json').write_text(
    json.dumps({'cases': cases, 'service_stop': stop, 'formal_eligible': False}, indent=2) + '\n'
)
print(json.dumps({'cases': len(cases), 'stop': stop}))
