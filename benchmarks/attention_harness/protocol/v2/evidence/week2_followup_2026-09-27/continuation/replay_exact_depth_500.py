"""Bounded fresh-process replay of the original seed-101 first gripper action."""
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
FREEZE = json.loads((ROOT / 'depth_exact_replay_freeze.json').read_text())
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == FREEZE['script_sha256']
assert sys.executable == FREEZE['interpreter']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SERVICE, text=True).strip() == FREEZE['service_revision']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SERVICE, text=True).strip()
OUT = ROOT / 'depth_exact_replay'
OUT.mkdir(exist_ok=False)
results = []


def sha(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


for index in range(FREEZE['repetitions']):
    case_dir = OUT / f'fresh_service_{index:02d}'
    case_dir.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (case_dir / 'service.log').open('wb')
    process = subprocess.Popen(
        [sys.executable, '-m', 'robosuite_sim', '--port', str(port), '--enable-sim-gt'],
        cwd=SERVICE, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True, env={**os.environ, 'MUJOCO_GL': 'egl'},
    )
    client = RobosuiteSimClient(f'http://127.0.0.1:{port}', timeout=90)
    row = {'index': index, 'task': 'cube_lift', 'seed': 101, 'formal_eligible': False}
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
        observation, low, _, metadata = client.reset(
            task_id='cube_lift', seed=101, camera=True, camera_name='agentview',
            camera_height=64, camera_width=64, horizon=500,
        )
        row['versions'] = metadata['versions']
        row['initial_depth_sha256'] = sha(observation['agentview_depth'])
        row['initial_native_success'] = client.native_success()
        row['perception_counts'] = [len(client.perceive_gt()['objects']) for _ in range(2)]
        action = np.zeros_like(low)
        action[-1] = -1.0
        row['action'] = action.tolist()
        result = client.step(action)
        depth = result.observation['agentview_depth']
        row['step_depth_sha256'] = sha(depth)
        row['step_depth_finite'] = bool(np.isfinite(depth).all())
        row['step_depth_min_m'] = float(depth.min())
        row['step_depth_max_m'] = float(depth.max())
        row['native_success'] = client.native_success()
        row['status'] = 'completed'
    except Exception as exc:
        row['status'] = 'failed'
        row['error'] = f'{type(exc).__name__}: {exc}'
        row['safety_interpretation'] = 'action_outcome_unknown; stop after first failure'
        try:
            row['native_success_after_error'] = client.native_success()
        except Exception as native_exc:
            row['native_error'] = str(native_exc)
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
            gone = False
        except ProcessLookupError:
            gone = True
        row['service_stop'] = {'pid': process.pid, 'exit_code': process.returncode,
                               'leader_reaped': process.poll() is not None,
                               'process_group_gone': gone}
        (case_dir / 'result.json').write_text(json.dumps(row, indent=2, sort_keys=True) + '\n')
        results.append(row)
        (OUT / 'progress.json').write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'index': index, 'status': row['status'],
                          'error': row.get('error')}), flush=True)
