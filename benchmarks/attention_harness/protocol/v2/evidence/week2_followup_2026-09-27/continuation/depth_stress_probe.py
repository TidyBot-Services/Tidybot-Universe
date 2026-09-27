"""Bounded HTTP diagnostic for development seeds; never a formal score."""
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from robosuite_sim.client import RobosuiteSimClient

ROOT = Path(__file__).resolve().parent
SERVICE = Path('/home/truares/桌面/robosuite_sim-service')
CASES = [(task, seed) for task in ('cube_lift', 'cube_stack') for seed in (101, 102, 103, 104, 105)]


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (ROOT / 'depth_stress_service.log').open('wb')
    process = subprocess.Popen(
        [sys.executable, '-m', 'robosuite_sim', '--port', str(port), '--enable-sim-gt'],
        cwd=SERVICE, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, env={**os.environ, 'MUJOCO_GL': 'egl'},
    )
    client = RobosuiteSimClient(f'http://127.0.0.1:{port}', timeout=90)
    results = []
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
        for task, seed in CASES:
            case = {'task': task, 'seed': seed, 'steps': [], 'formal_eligible': False}
            results.append(case)
            try:
                obs, low, high, metadata = client.reset(
                    task_id=task, seed=seed, camera=True, camera_name='agentview',
                    camera_height=64, camera_width=64, horizon=500,
                )
                case['versions'] = metadata['versions']
                for index in range(20):
                    action = np.zeros_like(low)
                    action[-1] = -1.0 if index % 2 == 0 else 1.0
                    step = client.step(action)
                    depth = step.observation['agentview_depth']
                    case['steps'].append({
                        'index': index, 'action': action.tolist(),
                        'depth_min_m': float(depth.min()), 'depth_max_m': float(depth.max()),
                        'depth_finite': bool(np.isfinite(depth).all()),
                        'depth_sha256': hashlib.sha256(depth.tobytes()).hexdigest(),
                        'native_success': client.native_success(),
                    })
                case['status'] = 'completed'
            except Exception as exc:
                case['status'] = 'failed'
                case['error'] = str(exc)
                try:
                    case['native_success_after_error'] = client.native_success()
                except Exception as native_exc:
                    case['native_error'] = str(native_exc)
            (ROOT / 'depth_stress_progress.json').write_text(json.dumps(results, indent=2) + '\n')
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        log.close()
        try:
            os.killpg(process.pid, 0)
            group_gone = False
        except ProcessLookupError:
            group_gone = True
        receipt = {'pid': process.pid, 'exit_code': process.returncode,
                   'leader_reaped': process.poll() is not None,
                   'process_group_gone': group_gone}
        (ROOT / 'depth_stress_service_stop.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'cases': len(results), 'failed': sum(x['status'] == 'failed' for x in results),
                      'stop': receipt}, indent=2))


if __name__ == '__main__':
    main()
