"""Backend-only development diagnostic; never exposed to a policy or score."""
import hashlib
import json
from pathlib import Path

import gymnasium as gym
import mani_skill.envs  # noqa: F401
import maniskill_tidyverse.tidyverse_agent  # noqa: F401
import numpy as np
import robocasa_tasks  # noqa: F401
import torch

ROOT = Path(__file__).resolve().parent
TASK = 'RoboCasa-Pn-P-Counter-To-Sink-v0'
SEED = 101
env = gym.make(TASK, num_envs=1, robot_uids='tidyverse',
               control_mode='whole_body', obs_mode='rgb+depth+segmentation')
try:
    env.reset(seed=0, options={'reconfigure': True})
    old_robot = env.unwrapped.agent.robot
    env.reset(seed=SEED, options={'reconfigure': True})
    robot = env.unwrapped.agent.robot
    print('robot_identity', id(old_robot), id(robot), old_robot is robot)
    controller = env.unwrapped.agent.controller
    initial = robot.get_qpos()[0].cpu().numpy()
    action = np.concatenate([initial[3:10], [0.0], initial[:3] + [0.125, -0.1, 0.0]])
    rows = []
    for index in range(30):
        env.step(torch.tensor(action, dtype=torch.float32).unsqueeze(0))
        qpos = robot.get_qpos()[0].cpu().numpy()
        target = controller.controllers['base']._target_qpos[0].cpu().numpy()
        rows.append({'step': index + 1, 'base_qpos': qpos[:3].tolist(),
                     'controller_target': target.tolist(), 'arm_base_world': robot.links_map['panda_link0'].pose.p[0].cpu().numpy().tolist()})
    report = {'schema_version': 'attentionbench.base-backend-diagnostic.v1',
              'formal_eligible': False, 'task': TASK, 'seed': SEED,
              'action': action.tolist(), 'action_sha256': hashlib.sha256(action.tobytes()).hexdigest(),
              'rows': rows}
    (ROOT / 'base_physics_reconfigure_link_diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'initial': rows[0], 'final': rows[-1]}))
finally:
    env.close()
