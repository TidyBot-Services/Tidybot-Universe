"""Open a SAPIEN viewer showing the Franka tidyverse robot at home pose with
the base_link_z collision spheres rendered as semi-transparent actors.

Two modes:
    --layer before   show all 60 spheres (3 layers, original)
    --layer after    show 40 spheres (top z=0.5 layer dropped)
    --layer both     show old in red, new in green, side-by-side at offset

Also drops a few key kitchen fixture cuboids (counter top, cabinet doors)
sampled from sim's /perceive — gives you context without bringing in the
whole RoboCasa scene. The user can rotate / zoom in the SAPIEN viewer.

Usage (X11 display required, e.g. DISPLAY=:1):
    python scripts/viz_spheres_sapien.py --layer both
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import sapien
import yaml


HOME_QPOS_7 = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785]


def fetch_perceive(sim_api: str) -> dict | None:
    try:
        req = urllib.request.Request(sim_api + "/perceive", method="POST",
                                     data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[warn] /perceive failed ({e}); using fallback fixtures")
        return None


# Fallback fixture set if sim isn't reachable. Hand-picked from a previous
# perceive sample of RoboCasa-Pn-P-Counter-To-Sink-v0 with robot at home.
FALLBACK_FIXTURES = [
    {"name": "hingerightdoor", "x": 0.614, "y": -1.950, "z": 0.369,
     "size_x": 0.065, "size_y": 0.497, "size_z": 0.621},
    {"name": "hingeleftdoor", "x": 0.613, "y": -2.450, "z": 0.369,
     "size_x": 0.067, "size_y": 0.497, "size_z": 0.622},
    {"name": "counter", "x": 0.332, "y": -2.320, "z": 0.908,
     "size_x": 0.636, "size_y": 2.764, "size_z": 0.031},
    {"name": "dishwasher", "x": 0.617, "y": -3.025, "z": 0.467,
     "size_x": 0.070, "size_y": 0.636, "size_z": 0.760},
]


def fixtures_from_perceive(perceive: dict | None,
                           base_xy: tuple[float, float]) -> list[dict]:
    if perceive is None:
        return FALLBACK_FIXTURES
    out = []
    bx, by = base_xy
    for o in perceive.get("objects", []):
        if "floor" in o["name"] or "wall" in o["name"] or "window" in o["name"]:
            continue
        d = math.hypot(o["x"] - bx, o["y"] - by)
        if d > 1.5:
            continue
        out.append(o)
    return out[:12]


def _add_box_visual(scene: sapien.Scene, name: str,
                    cx: float, cy: float, cz: float,
                    hx: float, hy: float, hz: float,
                    color: tuple[float, float, float, float]):
    builder = scene.create_actor_builder()
    builder.add_box_visual(half_size=[hx, hy, hz],
                           material=sapien.render.RenderMaterial(
                               base_color=color, metallic=0.0, roughness=0.7))
    actor = builder.build_static(name=name)
    actor.set_pose(sapien.Pose([cx, cy, cz]))
    return actor


def _add_sphere_visual(scene: sapien.Scene, name: str,
                       cx: float, cy: float, cz: float, r: float,
                       color: tuple[float, float, float, float]):
    builder = scene.create_actor_builder()
    builder.add_sphere_visual(radius=r,
                              material=sapien.render.RenderMaterial(
                                  base_color=color, metallic=0.0, roughness=0.5))
    actor = builder.build_static(name=name)
    actor.set_pose(sapien.Pose([cx, cy, cz]))
    return actor


def load_spheres(yaml_path: Path, link: str = "base_link_z") -> list[dict]:
    with yaml_path.open() as f:
        d = yaml.safe_load(f)
    return d["collision_spheres"][link]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", choices=["before", "after", "both"],
                    default="both")
    ap.add_argument("--old-yaml",
                    default="/home/truares/文档/curobo_service_v0_8/curobo_service/assets/spheres/franka_tidyverse_mesh.yml")
    ap.add_argument("--new-yaml",
                    default=str(Path(__file__).parent.parent
                                / "assets/spheres/franka_tidyverse_mesh.yml"))
    # v0.7.8 URDF has working visual meshes; v0.8.0's URDF references
    # meshes/visual/hand.obj which doesn't exist (only hand.dae). Use the
    # older one — they're kinematically identical, only visuals differ.
    ap.add_argument("--urdf",
                    default="/home/truares/workspace/curobo-v0.7.8/src/curobo/content/assets/robot/franka_description/franka_panda_tidyverse.urdf")
    ap.add_argument("--sim-api", default="http://localhost:5500")
    args = ap.parse_args()

    perceive = fetch_perceive(args.sim_api)
    if perceive:
        ab = perceive["arm_base"]
        # base_link world is below panda_link0 by ~47cm.
        base_world_xy = (ab[0], ab[1])
        ab_quat = perceive.get("arm_base_quat") or [1, 0, 0, 0]
        w, x, y, z = ab_quat
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        print(f"[info] base_world=({ab[0]:.3f},{ab[1]:.3f})  yaw={math.degrees(yaw):.1f}°")
    else:
        base_world_xy = (1.40, -2.20)
        yaw = math.pi
        print(f"[info] using fallback base_world=({base_world_xy[0]},{base_world_xy[1]}) yaw=180°")

    fixtures = fixtures_from_perceive(perceive, base_world_xy)
    print(f"[info] {len(fixtures)} fixtures to render")

    scene = sapien.Scene()
    scene.set_timestep(1 / 60)
    scene.add_ground(altitude=0)
    scene.add_directional_light([0, -0.5, -1], color=[1, 1, 1])
    scene.add_point_light([1.0, -2.5, 1.5], color=[1, 0.95, 0.9])
    scene.set_ambient_light([0.4, 0.4, 0.4])

    # SAPIEN URDF loader resolves mesh paths relative to cwd, not the URDF
    # file itself. cd into the asset root before loading.
    import os
    urdf_path = Path(args.urdf).resolve()
    os.chdir(urdf_path.parent)
    loader = scene.create_urdf_loader()
    loader.fix_root_link = True
    robot = loader.load(urdf_path.name)
    if robot is None:
        print(f"[err] failed to load URDF: {args.urdf}", file=sys.stderr)
        return 2
    # Place robot's base_link at base_world_xy, oriented by yaw.
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    robot.set_root_pose(sapien.Pose(
        p=[base_world_xy[0], base_world_xy[1], 0],
        q=[cy, 0, 0, sy],   # wxyz
    ))

    # Push the arm joints to home so the robot is in the same configuration
    # cuRobo evaluates start-state collision from. Joint name → home value.
    home_arm = dict(zip(
        [f"panda_joint{i}" for i in range(1, 8)], HOME_QPOS_7))
    home_arm["panda_finger_joint1"] = 0.04
    home_arm["panda_finger_joint2"] = 0.04
    qpos = []
    for j in robot.get_active_joints():
        qpos.append(home_arm.get(j.name, 0.0))
    robot.set_qpos(qpos)

    # Add fixtures as semi-transparent gray boxes
    for f in fixtures:
        _add_box_visual(scene, f["name"],
                        f["x"], f["y"], f["z"],
                        f["size_x"] / 2, f["size_y"] / 2, f["size_z"] / 2,
                        color=(0.6, 0.6, 0.6, 0.35))

    # Sphere drawing ----------------------------------------------------
    cy_, sy_ = math.cos(yaw), math.sin(yaw)

    def world_xyz(local: list[float]) -> tuple[float, float, float]:
        x = base_world_xy[0] + cy_ * local[0] - sy_ * local[1]
        y = base_world_xy[1] + sy_ * local[0] + cy_ * local[1]
        z = local[2]
        return x, y, z

    # "both" mode: compare per-sphere radius between old and new yaml.
    # Same radius = green (unchanged), changed = orange (resized) showing
    # the NEW radius. Removed = red wireframe at OLD radius. This handles
    # both "drop" and "scale" edits cleanly.
    old = load_spheres(Path(args.old_yaml))
    new = load_spheres(Path(args.new_yaml))

    # Index old by (x,y,z) so we can match identity
    def key(c: list[float]) -> tuple:
        return (round(c[0], 4), round(c[1], 4), round(c[2], 4))

    old_by_key = {key(s["center"]): float(s["radius"]) for s in old}
    new_by_key = {key(s["center"]): float(s["radius"]) for s in new}

    if args.layer == "before":
        for i, s in enumerate(old):
            x, y, z = world_xyz(s["center"])
            _add_sphere_visual(scene, f"o_{i}", x, y, z, float(s["radius"]),
                               (0.20, 0.75, 0.30, 0.50))
    elif args.layer == "after":
        for i, s in enumerate(new):
            x, y, z = world_xyz(s["center"])
            r = float(s["radius"])
            old_r = old_by_key.get(key(s["center"]))
            modified = (old_r is not None) and abs(old_r - r) > 1e-6
            color = (0.95, 0.55, 0.20, 0.65) if modified else (0.20, 0.75, 0.30, 0.50)
            _add_sphere_visual(scene, f"n_{i}", x, y, z, r, color)
    else:  # both
        # Step 1: each NEW sphere at its current radius
        for i, s in enumerate(new):
            x, y, z = world_xyz(s["center"])
            r = float(s["radius"])
            old_r = old_by_key.get(key(s["center"]))
            modified = (old_r is not None) and abs(old_r - r) > 1e-6
            color = (0.95, 0.55, 0.20, 0.75) if modified else (0.20, 0.75, 0.30, 0.45)
            _add_sphere_visual(scene, f"n_{i}", x, y, z, r, color)
        # Step 2: removed spheres (in old but not in new) shown red, ghosted
        for i, s in enumerate(old):
            if key(s["center"]) in new_by_key:
                continue
            x, y, z = world_xyz(s["center"])
            _add_sphere_visual(scene, f"removed_{i}", x, y, z,
                               float(s["radius"]),
                               (0.95, 0.20, 0.20, 0.30))

    # Viewer ------------------------------------------------------------
    viewer = scene.create_viewer()
    viewer.set_camera_xyz(base_world_xy[0] - 2.0, base_world_xy[1] + 0.0, 1.5)
    viewer.set_camera_rpy(0, -0.3, 0)
    viewer.window.set_camera_parameters(near=0.05, far=100, fovy=1.0)

    n_modified = sum(1 for k, r in new_by_key.items()
                     if k in old_by_key and abs(old_by_key[k] - r) > 1e-6)
    n_removed = len(old_by_key) - len(set(old_by_key) & set(new_by_key))
    print("\n=== SAPIEN viewer is open ===")
    print(f"Layer mode: {args.layer}  (old={len(old)}, new={len(new)})")
    if args.layer == "both":
        print(f"  GREEN  = unchanged spheres ({len(new) - n_modified})")
        print(f"  ORANGE = resized spheres at NEW radius ({n_modified})")
        print(f"  RED    = removed spheres at OLD radius ({n_removed}, ghosted)")
    elif args.layer == "after":
        print(f"  GREEN  = unchanged ({len(new) - n_modified})")
        print(f"  ORANGE = resized ({n_modified})")
    print("  GRAY  = kitchen fixtures (counter, cabinet doors, etc.)")
    print("Mouse drag to rotate, scroll to zoom. Close window to exit.\n")

    while not viewer.closed:
        scene.update_render()
        viewer.render()


if __name__ == "__main__":
    sys.exit(main())
