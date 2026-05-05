"""Render base_link_z spheres in world frame, BEFORE vs AFTER edit, with
kitchen fixtures from sim's /perceive overlaid.

Saves a side-by-side PNG to results/sphere_compare_<ts>.png. Highlights
spheres that overlap any fixture AABB in red — gives an at-a-glance
"are we still over-covering anything bad" check.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


def fetch_perceive(sim_api: str) -> dict:
    req = urllib.request.Request(sim_api + "/perceive", method="POST",
                                 data=b"{}",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_base_link_pose(perceive: dict) -> tuple[np.ndarray, float]:
    """base_link world position (z=0 floor) + yaw.

    panda_link0 (arm_base) sits ~0.472m above base_link and aligned in yaw.
    arm_base_quat is wxyz of panda_link0 in world; for our setup yaw is the
    only meaningful rotation.
    """
    arm_base = perceive["arm_base"]                    # [x,y,z]
    quat = perceive.get("arm_base_quat") or [1, 0, 0, 0]  # wxyz
    base_xy = np.array([arm_base[0], arm_base[1], 0.0])
    # extract yaw from quat (z-axis rotation)
    w, x, y, z = quat
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return base_xy, yaw


def sphere_world_centers(spheres: list[dict], base_xy: np.ndarray, yaw: float):
    """Transform sphere centers from base_link_z frame to world."""
    c, s = math.cos(yaw), math.sin(yaw)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    out = []
    for sph in spheres:
        local = np.asarray(sph["center"], dtype=float)
        world = base_xy + R @ local
        out.append((world, float(sph["radius"])))
    return out


def cuboids_from_perceive(perceive: dict, *, max_dist: float = 2.0):
    """Treat each perceived object as an AABB."""
    base_xy = np.array([perceive["arm_base"][0], perceive["arm_base"][1], 0.0])
    out = []
    for o in perceive.get("objects", []):
        c = np.array([o["x"], o["y"], o["z"]])
        if np.linalg.norm(c[:2] - base_xy[:2]) > max_dist:
            continue
        h = np.array([o["size_x"], o["size_y"], o["size_z"]]) / 2
        out.append((o["name"], c, h))
    return out


def sphere_intersects_aabb(c: np.ndarray, r: float,
                           aabb_c: np.ndarray, aabb_h: np.ndarray) -> bool:
    """Closest point on the AABB to c, distance ≤ r."""
    dx = max(abs(c[0] - aabb_c[0]) - aabb_h[0], 0.0)
    dy = max(abs(c[1] - aabb_c[1]) - aabb_h[1], 0.0)
    dz = max(abs(c[2] - aabb_c[2]) - aabb_h[2], 0.0)
    return dx * dx + dy * dy + dz * dz <= r * r


def add_aabb(ax, c: np.ndarray, h: np.ndarray, **kwargs):
    """Draw AABB wireframe. Faces semi-transparent."""
    cx, cy, cz = c
    hx, hy, hz = h
    pts = np.array([
        [cx-hx, cy-hy, cz-hz], [cx+hx, cy-hy, cz-hz],
        [cx+hx, cy+hy, cz-hz], [cx-hx, cy+hy, cz-hz],
        [cx-hx, cy-hy, cz+hz], [cx+hx, cy-hy, cz+hz],
        [cx+hx, cy+hy, cz+hz], [cx-hx, cy+hy, cz+hz],
    ])
    faces = [
        [pts[0], pts[1], pts[2], pts[3]],
        [pts[4], pts[5], pts[6], pts[7]],
        [pts[0], pts[1], pts[5], pts[4]],
        [pts[2], pts[3], pts[7], pts[6]],
        [pts[1], pts[2], pts[6], pts[5]],
        [pts[0], pts[3], pts[7], pts[4]],
    ]
    coll = Poly3DCollection(faces, **kwargs)
    ax.add_collection3d(coll)


def add_sphere(ax, c: np.ndarray, r: float, *, color: str, alpha: float = 0.35,
               u_steps: int = 12, v_steps: int = 8):
    """Cheap parametric sphere (low-res)."""
    u = np.linspace(0, 2 * np.pi, u_steps)
    v = np.linspace(0, np.pi, v_steps)
    x = c[0] + r * np.outer(np.cos(u), np.sin(v))
    y = c[1] + r * np.outer(np.sin(u), np.sin(v))
    z = c[2] + r * np.outer(np.ones_like(u), np.cos(v))
    ax.plot_surface(x, y, z, color=color, alpha=alpha, linewidth=0,
                    rstride=1, cstride=1, antialiased=False, shade=False)


def render_panel(ax, title: str, spheres_world, fixtures, hl_color: str = "#d62728"):
    n_hits = 0
    for c, r in spheres_world:
        in_collision = any(
            sphere_intersects_aabb(c, r, ac, ah) for _, ac, ah in fixtures
        )
        color = hl_color if in_collision else "#2ca02c"
        n_hits += int(in_collision)
        add_sphere(ax, c, r, color=color, alpha=0.45)

    for name, c, h in fixtures:
        add_aabb(ax, c, h, facecolors=(0.5, 0.5, 0.5, 0.10),
                 edgecolors=(0.2, 0.2, 0.2, 0.9), linewidths=0.7)

    ax.set_title(f"{title}  ·  {len(spheres_world)} spheres  ·  "
                 f"{n_hits} colliding", fontsize=10)
    ax.set_xlabel("x  (m)")
    ax.set_ylabel("y  (m)")
    ax.set_zlabel("z  (m)")
    return n_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-yaml",
                    default="/home/truares/文档/curobo_service_v0_8/curobo_service/assets/spheres/franka_tidyverse_mesh.yml")
    ap.add_argument("--new-yaml",
                    default=str(Path(__file__).parent.parent
                                / "assets/spheres/franka_tidyverse_mesh.yml"))
    ap.add_argument("--link", default="base_link_z")
    ap.add_argument("--sim-api", default="http://localhost:5500")
    ap.add_argument("--out-dir",
                    default=str(Path(__file__).parent.parent / "results"))
    args = ap.parse_args()

    perceive = fetch_perceive(args.sim_api)
    base_xy, yaw = get_base_link_pose(perceive)
    print(f"base_link world: ({base_xy[0]:.3f},{base_xy[1]:.3f}) yaw={math.degrees(yaw):.1f}°")
    fixtures = cuboids_from_perceive(perceive)
    print(f"{len(fixtures)} fixtures within 2m of base")

    with open(args.old_yaml) as f:
        old = yaml.safe_load(f)["collision_spheres"][args.link]
    with open(args.new_yaml) as f:
        new = yaml.safe_load(f)["collision_spheres"][args.link]
    print(f"old spheres: {len(old)}   new spheres: {len(new)}")

    old_world = sphere_world_centers(old, base_xy, yaw)
    new_world = sphere_world_centers(new, base_xy, yaw)

    fig = plt.figure(figsize=(16, 10))
    bx, by = base_xy[0], base_xy[1]

    # Row 1: isometric BEFORE / AFTER
    ax1 = fig.add_subplot(2, 2, 1, projection="3d")
    ax2 = fig.add_subplot(2, 2, 2, projection="3d")
    n_old = render_panel(ax1, "BEFORE (60 spheres, 3 layers @ z=.1/.3/.5)",
                         old_world, fixtures)
    n_new = render_panel(ax2, "AFTER edit A (40 spheres, z=.5 layer removed)",
                         new_world, fixtures)
    for ax in (ax1, ax2):
        ax.set_xlim(bx - 1.2, bx + 0.7)
        ax.set_ylim(by - 0.9, by + 0.9)
        ax.set_zlim(0.0, 1.4)
        ax.view_init(elev=20, azim=-65)

    # Row 2: side view (X-Z), z-axis upright so the deleted top layer is
    # immediately visible
    ax3 = fig.add_subplot(2, 2, 3, projection="3d")
    ax4 = fig.add_subplot(2, 2, 4, projection="3d")
    render_panel(ax3, "BEFORE — side view", old_world, fixtures)
    render_panel(ax4, "AFTER — side view", new_world, fixtures)
    for ax in (ax3, ax4):
        ax.set_xlim(bx - 1.2, bx + 0.7)
        ax.set_ylim(by - 0.9, by + 0.9)
        ax.set_zlim(0.0, 1.4)
        ax.view_init(elev=5, azim=0)        # almost pure side
        ax.set_box_aspect((2.0, 2.0, 1.4))

    plt.suptitle(
        f"base_link_z spheres around home pose @ ({bx:.2f},{by:.2f}) · "
        f"AABB-collide check (primitive/v1 view): {n_old} → {n_new} · "
        f"v2 mesh-collide is stricter and is what we're trying to fix",
        fontsize=11, y=0.99)
    plt.tight_layout()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"sphere_compare_{ts}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    raise SystemExit(main())
