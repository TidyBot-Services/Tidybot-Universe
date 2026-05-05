"""Pin down why v2 flags 'Start or End state in collision' at home pose.

Replicates the v2 service's collision setup using the same robot_cfg + the
real fixture cuboids fetched from running sim's /debug/fixture_cuboids.
Calls RobotCollisionChecker directly, asks what's colliding, sweeps over
several activation distances to find the threshold where home pose
becomes collision-free.

Hypothesis: the optimizer's activation_distance (1cm, set explicitly) is
not the same knob that gates start/end-state validation. In cuRoboV2 the
validation default is 0.2m (20cm), and home pose has arm spheres within
20cm of the kitchen counter / cabinet meshes.

Usage:
    DISPLAY=:0 python scripts/diag_v2_collision.py --sim-api http://localhost:5500
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import torch


HOME_QPOS_10D = [0.0, 0.0, 0.0,
                 0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]


def fetch_cuboids(sim_api: str) -> list[dict]:
    with urllib.request.urlopen(sim_api + "/debug/fixture_cuboids", timeout=5) as r:
        d = json.loads(r.read())
    return d["cuboids"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-api", default="http://localhost:5500")
    ap.add_argument("--robot-cfg", default="franka_tidyverse.yml")
    args = ap.parse_args()

    print("=== fetch real cuboids from sim ===")
    cuboids_raw = fetch_cuboids(args.sim_api)
    print(f"  {len(cuboids_raw)} fixtures from sim")

    from curobo.collision_checking import RobotCollisionChecker
    from curobo.scene import Scene, Cuboid

    cu_cuboids = []
    for c in cuboids_raw:
        cx, cy, cz = c["center_local"]
        hx, hy, hz = c["half_size"]
        q = c.get("quat_wxyz", [1, 0, 0, 0])
        cu_cuboids.append(Cuboid(
            name=c["name"],
            pose=[cx, cy, cz, q[0], q[1], q[2], q[3]],
            dims=[hx * 2, hy * 2, hz * 2],
        ))
    scene = Scene(cuboid=cu_cuboids)

    print("\n=== sweep activation distances ===")
    # validate expects (batch, horizon, dof)
    q = torch.tensor([[HOME_QPOS_10D]], dtype=torch.float32, device="cuda")

    for act_dist in [0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2]:
        cfg = RobotCollisionChecker.load_from_config(
            robot_config=args.robot_cfg,
            scene_model=scene,
            collision_activation_distance=act_dist,
            self_collision_activation_distance=0.0,
            n_cuboids=200,
        )
        checker = RobotCollisionChecker(cfg)
        # validate returns mask: True per env if any contact within activation_distance
        with torch.no_grad():
            ok = checker.validate(q)
        ok_b = bool(ok[0].item())
        print(f"  collision_activation_distance={act_dist:.3f}m → "
              f"validate(home)={'OK' if ok_b else 'IN COLLISION'}  raw={ok}")

    print("\n=== detailed: at default 0.2m, which spheres are closest to obstacles? ===")
    checker = RobotCollisionChecker.load_from_config(
        robot_config=args.robot_cfg,
        scene_model=scene,
        collision_activation_distance=0.2,
        self_collision_activation_distance=0.0,
        n_cuboids=200,
    )
    # Use the higher-level distance API
    with torch.no_grad():
        try:
            dist = checker.get_collision_distance(
                checker.get_kinematics().compute_kinematics(q.cpu().numpy()
                                                            if not torch.is_tensor(q) else q).robot_spheres
            )
            print(f"  sphere distances shape: {dist.shape}")
            arr = dist[0].cpu().numpy()
            print(f"  min={arr.min():.4f}  max={arr.max():.4f}  count<0.2: {(arr<0.2).sum()}")
            negs = np.where(arr < 0.0)[0]
            small = np.where((arr >= 0) & (arr < 0.2))[0]
            print(f"  sphere idxs with dist<0 (penetrating): {negs.tolist()[:20]}")
            print(f"  sphere idxs 0..0.2 m (within margin): {small.tolist()[:20]}")
        except Exception as e:
            print(f"  get_collision_distance: {type(e).__name__}: {e}")

    print("\n=== same check at 1 cm (the optimizer's activation distance) ===")
    checker2 = RobotCollisionChecker.load_from_config(
        robot_config=args.robot_cfg,
        scene_model=scene,
        collision_activation_distance=0.01,
        self_collision_activation_distance=0.0,
        n_cuboids=200,
    )
    with torch.no_grad():
        ok2 = checker2.validate(q)
    print(f"  at 0.01m: validate={'OK' if bool(ok2[0].item()) else 'IN COLLISION'}")


if __name__ == "__main__":
    sys.exit(main())
