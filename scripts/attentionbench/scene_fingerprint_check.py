"""Check the registered pick/place seed 101 scene in a fresh process.

Run from the repository root with the RoboCasa365 environment and workspace
path registry configured:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD python scripts/attentionbench/scene_fingerprint_check.py

Each invocation constructs and resets one scene, renders one frame, then closes
it. This script does not start a planner or an agent.
"""

from __future__ import annotations

from benchmarks.attention_harness.dev_tasks import get_task
from openarm365_dev_v2.environment import create_env
from openarm365_dev_v2.scene_contract import (
    capture_state, fingerprint, load_contract, registered_contract_ref, seed_entry,
)


def main() -> None:
    task_id, seed = 'pick_place_counter_to_sink', 101
    env = create_env(
        get_task(task_id).official_env, robots='OpenArm365', seed=seed,
        obj_instance_split='pretrain', layout_and_style_ids=[(11, 34)],
        camera_widths=128, camera_heights=128, randomize_cameras=False,
        generative_textures=None, control_freq=20, horizon=9600,
        scene_reset_seed=seed,
    )
    try:
        env.reset()
        normalized = fingerprint(capture_state(env), rounded=True)
        frame = env.sim.render(128, 128, camera_name='robot0_agentview_left', depth=False)
    finally:
        env.close()
    expected = seed_entry(load_contract(registered_contract_ref()), task_id, seed)['normalized_sha256']
    print(f'normalized_sha256: {normalized}')
    print(f'registered_sha256: {expected}')
    print(f'matches_registered: {normalized == expected}')
    # A blank frame means the renderer started but drew nothing.
    print(f'frame_rendered: {float(frame.std()) > 0}')


if __name__ == '__main__':
    main()
