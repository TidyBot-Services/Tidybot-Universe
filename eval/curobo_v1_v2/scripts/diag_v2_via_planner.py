"""Use the same MotionPlanner that v2 service uses, call kinematics on home
pose, dump sphere world positions, find which sphere penetrates which cuboid.

Bypasses RobotCollisionChecker API contortions by going through MotionPlanner
directly (same path v2 service takes).
"""
from __future__ import annotations

import json
import urllib.request

import numpy as np
import torch


HOME_QPOS_10D = [0.0, 0.0, 0.0, 0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]


def fetch_cuboids() -> list[dict]:
    with urllib.request.urlopen("http://localhost:5500/debug/fixture_cuboids", timeout=5) as r:
        return json.loads(r.read())["cuboids"]


def main():
    from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
    from curobo.scene import Scene, Cuboid

    cuboids = fetch_cuboids()
    print(f"[fetch] {len(cuboids)} fixture cuboids")

    cu_cuboids = []
    for c in cuboids:
        cx, cy, cz = c["center_local"]
        hx, hy, hz = c["half_size"]
        q = c.get("quat_wxyz", [1, 0, 0, 0])
        cu_cuboids.append(Cuboid(
            name=c["name"],
            pose=[cx, cy, cz, q[0], q[1], q[2], q[3]],
            dims=[hx * 2, hy * 2, hz * 2],
        ))
    scene = Scene(cuboid=cu_cuboids)

    print("[init] MotionPlanner whole_body cfg ...")
    cfg = MotionPlannerCfg.create(
        robot="franka_tidyverse.yml",
        scene_model=scene,
        collision_cache={"cuboid": 100, "mesh": 10},
        optimizer_collision_activation_distance=0.01,
        use_cuda_graph=False,
    )
    planner = MotionPlanner(cfg)
    planner.warmup(num_warmup_iterations=1, enable_graph=False)
    print("[init] done")

    # Compute kinematics via planner.kinematics; needs a JointState wrapper
    from curobo.types import JointState
    kin = planner.kinematics
    q = torch.tensor([HOME_QPOS_10D], dtype=torch.float32, device="cuda")
    js = JointState.from_position(q)
    state = kin.compute_kinematics(js)
    raw = state.robot_spheres
    print(f"[kin] state.robot_spheres tensor shape: {tuple(raw.shape)}")
    arr = raw.cpu().numpy()
    # Squeeze singleton batch / horizon dims so we end up at (n_spheres, 4)
    while arr.ndim > 2:
        arr = arr[0]
    spheres = arr
    n_spheres = spheres.shape[0]
    print(f"[kin] {n_spheres} robot spheres at home pose, last-dim={spheres.shape[1]}")

    # Dump sphere world positions split by approximate region (base/arm/hand)
    print(f"[kin] sphere stats: x range=[{spheres[:,0].min():.3f},{spheres[:,0].max():.3f}], "
          f"y=[{spheres[:,1].min():.3f},{spheres[:,1].max():.3f}], "
          f"z=[{spheres[:,2].min():.3f},{spheres[:,2].max():.3f}]")

    # For each sphere, find penetration with any cuboid (axis-aligned approx)
    penetrations = []
    for i, s in enumerate(spheres):
        sx, sy, sz, r = s
        for cub in cuboids:
            cx, cy, cz = cub["center_local"]
            hx, hy, hz = cub["half_size"]
            # closest point on cuboid AABB
            nx = max(cx - hx, min(sx, cx + hx))
            ny = max(cy - hy, min(sy, cy + hy))
            nz = max(cz - hz, min(sz, cz + hz))
            d = np.sqrt((sx - nx) ** 2 + (sy - ny) ** 2 + (sz - nz) ** 2)
            penet = r - d   # positive = penetrating, negative = clearance
            if penet > -0.20:   # within 20cm of contact, including penetration
                penetrations.append((i, cub["name"], d, r, penet, sx, sy, sz))

    penetrations.sort(key=lambda x: -x[4])  # most-penetrating first
    print(f"\n[contact] {len(penetrations)} sphere/cuboid pairs within 20cm clearance")
    print(f"  (negative penet = clearance, positive = real penetration)\n")
    print(f"{'sphere':>8} {'fixture':<55} {'dist':>6} {'r':>5} {'penet':>7}")
    for (i, name, d, r, penet, sx, sy, sz) in penetrations[:30]:
        marker = "  ← PENETRATING" if penet > 0 else ""
        print(f"{i:>8} {name:<55} {d:>6.3f} {r:>5.3f} {penet:>+7.4f}{marker}")

    # Try plan with self_collision_check disabled — does it still fail?
    print("\n[plan] re-init with self_collision_check=False ...")
    cfg2 = MotionPlannerCfg.create(
        robot="franka_tidyverse.yml",
        scene_model=scene,
        collision_cache={"cuboid": 100, "mesh": 10},
        optimizer_collision_activation_distance=0.01,
        use_cuda_graph=False,
        self_collision_check=False,
    )
    planner2 = MotionPlanner(cfg2)
    planner2.warmup(num_warmup_iterations=1, enable_graph=False)

    from curobo.types import Pose, GoalToolPose, JointState

    state_home = kin.compute_kinematics(JointState.from_position(q))
    ee_pos = state_home.ee_pos_seq[0].clone().squeeze()
    ee_quat = state_home.ee_quat_seq[0].clone().squeeze()
    # Tiny offset so it's not literally same point
    ee_pos_target = ee_pos.clone()
    ee_pos_target[0] += 0.05
    print(f"  home EE: pos={ee_pos.cpu().numpy().tolist()}  +5cm in x for target")

    def try_plan(p, label):
        target_pose = Pose(position=ee_pos_target.unsqueeze(0).unsqueeze(0),
                           quaternion=ee_quat.unsqueeze(0).unsqueeze(0))
        goal = GoalToolPose.from_poses({p.tool_frames[0]: target_pose},
                                        num_goalset=1)
        js_start = JointState.from_position(q)
        result = p.plan_pose(goal, js_start, max_attempts=1)
        succ = bool(result.success.any().item())
        dbg = getattr(result, 'debug_info', None)
        print(f"  [{label}] success={succ}  debug_info={dbg}  status={getattr(result,'status',None)}")

    try_plan(planner, "with self_collision_check=True")
    try_plan(planner2, "with self_collision_check=False")


if __name__ == "__main__":
    main()
