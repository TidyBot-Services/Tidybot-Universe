"""Check every registered pick/place seed, each in a fresh reset-only process.

Run from the repository root with the RoboCasa365 environment and workspace
path registry configured:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD python scripts/attentionbench/scene_fingerprint_check.py

One seed also renders a frame. No planner, policy, agent or sim.lock is used.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from openarm365_dev_v2.scene_contract import (
    accepted_fingerprints, capture_state, fingerprint, host_info, load_contract,
    registered_contract_ref,
)

TASK = 'pick_place_counter_to_sink'


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def probe_scene(task: str, seed: int, resets: int, render: bool = False) -> dict:
    """Reset only; repeated resets use the same deterministic scene seed."""
    from benchmarks.attention_harness.dev_tasks import get_task
    from openarm365_dev_v2.environment import create_env

    env = create_env(
        get_task(task).official_env, robots='OpenArm365', seed=seed,
        obj_instance_split='pretrain', layout_and_style_ids=[(11, 34)],
        camera_widths=128, camera_heights=128, randomize_cameras=False,
        generative_textures=None, control_freq=20, horizon=9600,
        scene_reset_seed=seed,
    )
    fingerprints = []
    frame_rendered = None
    try:
        for _ in range(resets):
            env.reset()
            fingerprints.append(fingerprint(capture_state(env), rounded=True))
        if render:
            frame = env.sim.render(128, 128, camera_name='robot0_agentview_left', depth=False)
            frame_rendered = bool(float(frame.std()) > 0)
    finally:
        env.close()
    return dict(seed=seed, normalized_sha256=fingerprints, host=host_info(), frame_rendered=frame_rendered)


def fresh_probe(task: str, seed: int, resets: int, render: bool = False) -> dict:
    """Use a new interpreter with the worker's hash/library environment."""
    environ = os.environ | {'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'MUJOCO_GL': 'egl'}
    for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH'):
        environ.pop(name, None)
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--probe',
               '--task', task, '--seed', str(seed), '--resets', str(resets)]
    if render:
        command.append('--render')
    result = subprocess.run(command, env=environ, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def check_registered() -> bool:
    ref = registered_contract_ref()
    seeds = sorted(map(int, load_contract(ref)['tasks'][TASK]['seeds']))
    matches = True
    frame_rendered = False
    for index, seed in enumerate(seeds):
        accepted = accepted_fingerprints(ref, TASK, seed)
        row = fresh_probe(TASK, seed, 1, render=index == 0)
        actual = row['normalized_sha256'][0]
        variant = next((name for name, sha in accepted if sha == actual), None)
        print(f'seed: {seed} normalized_sha256: {actual} matched_variant: {variant}')
        if variant is None:
            matches = False
            print('host: ' + json.dumps(row['host'], sort_keys=True))
            print('Collect evidence with scripts/attentionbench/register_host_variant.py; '
                  'register the snippet and update its pinned digest only after user approval.')
        if index == 0:
            frame_rendered = row['frame_rendered'] is True
    print(f'matches_registered: {matches}')
    print(f'frame_rendered: {frame_rendered}')
    return matches and frame_rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--task', default=TASK, help=argparse.SUPPRESS)
    parser.add_argument('--seed', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--resets', type=positive_int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--render', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        if args.seed is None:
            parser.error('--probe requires --seed')
        # Upstream Python and native libraries may print: reserve stdout for JSON.
        with os.fdopen(os.dup(sys.stdout.fileno()), 'w') as output:
            os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
            row = probe_scene(args.task, args.seed, args.resets, args.render)
            print(json.dumps(row), file=output, flush=True)
        return 0
    return 0 if check_registered() else 1


if __name__ == '__main__':
    raise SystemExit(main())
