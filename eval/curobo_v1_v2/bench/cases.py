"""Generate planning test cases for the synthetic benchmark.

A case is a tuple (start_qpos[10], target_pos[3], target_quat[4], cuboids[],
mask) that we can replay against both v1 and v2 planners. Generation is
deterministic (seeded numpy RNG) so re-runs produce the same case set.

Layouts:
- "free_space": no obstacles, gripper-down goals in front of the arm
- "shelf": one tall vertical wall in front simulating a shelf back panel —
  forces the planner around an obstacle
- "table_under": a low table-like cuboid at z=0.4 with goals below/above it
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class PlanCase:
    case_id: str
    layout: str
    start_qpos: list[float]
    target_pos: list[float]
    target_quat: list[float]
    cuboids: list[dict]
    mask: str = "whole_body"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


HOME_QPOS_10D = [0.0, 0.0, 0.0,
                 0.0, -0.785, 0.0, -2.356,
                 0.0, 1.571, 0.785]

# Quaternions are wxyz throughout (matches cuRobo + curobo_planner.py).
GRIPPER_DOWN = [0.0, 1.0, 0.0, 0.0]      # tool +z pointing -z (world)
GRIPPER_FORWARD = [0.7071, 0.0, 0.7071, 0.0]  # tool +z pointing +x


def _ground_cuboid() -> dict:
    return {"name": "ground",
            "center": [0.0, 0.0, -0.51],
            "half_size": [5.0, 5.0, 0.01]}


def free_space_cases(n: int, seed: int = 0) -> Iterator[PlanCase]:
    """Random reachable EE goals around the arm with only a ground plane.

    Goals sample (r, theta, z) in cylindrical coords:
        r in [0.35, 0.75] m   — clearly reachable for Franka on tidybot base
        theta in [-1.0, 1.0]  — front hemisphere
        z in [0.15, 0.95]     — counter-height range
    """
    rng = random.Random(seed)
    for i in range(n):
        r = rng.uniform(0.35, 0.75)
        theta = rng.uniform(-1.0, 1.0)
        z = rng.uniform(0.15, 0.95)
        x = r * math.cos(theta)
        y = r * math.sin(theta)
        yield PlanCase(
            case_id=f"free_{i:03d}",
            layout="free_space",
            start_qpos=list(HOME_QPOS_10D),
            target_pos=[round(x, 4), round(y, 4), round(z, 4)],
            target_quat=list(GRIPPER_DOWN),
            cuboids=[_ground_cuboid()],
            mask="whole_body",
        )


def shelf_cases(n: int, seed: int = 1) -> Iterator[PlanCase]:
    """Goals on the far side of a vertical wall — planner must go around.

    Wall: 0.6m wide, 1.2m tall, 5cm thick at x=0.45m. Goals are placed
    behind it at x in [0.5, 0.7] (just barely reachable) which forces the
    planner to find a non-trivial path."""
    rng = random.Random(seed)
    wall = {
        "name": "wall",
        "center": [0.45, 0.0, 0.6],
        "half_size": [0.025, 0.30, 0.60],
    }
    for i in range(n):
        # Goal slightly behind the wall but still in workspace
        x = rng.uniform(0.50, 0.65)
        y = rng.uniform(-0.20, 0.20)
        z = rng.uniform(0.20, 0.85)
        yield PlanCase(
            case_id=f"shelf_{i:03d}",
            layout="shelf",
            start_qpos=list(HOME_QPOS_10D),
            target_pos=[round(x, 4), round(y, 4), round(z, 4)],
            target_quat=list(GRIPPER_DOWN),
            cuboids=[_ground_cuboid(), wall],
            mask="whole_body",
        )


def table_under_cases(n: int, seed: int = 2) -> Iterator[PlanCase]:
    """A horizontal table top at z=0.4. Goals are above and below — forces
    arm to plan around the slab. Counter-style scene."""
    rng = random.Random(seed)
    table = {
        "name": "counter_top",
        "center": [0.55, 0.0, 0.40],
        "half_size": [0.20, 0.40, 0.02],
    }
    for i in range(n):
        # Half above, half just barely under the slab edge
        if i % 2 == 0:
            z = rng.uniform(0.45, 0.85)
        else:
            z = rng.uniform(0.20, 0.36)
        x = rng.uniform(0.40, 0.70)
        y = rng.uniform(-0.30, 0.30)
        yield PlanCase(
            case_id=f"table_{i:03d}",
            layout="table_under",
            start_qpos=list(HOME_QPOS_10D),
            target_pos=[round(x, 4), round(y, 4), round(z, 4)],
            target_quat=list(GRIPPER_DOWN),
            cuboids=[_ground_cuboid(), table],
            mask="whole_body",
        )


def all_cases(n_per_layout: int = 30) -> list[PlanCase]:
    cases: list[PlanCase] = []
    cases.extend(free_space_cases(n_per_layout, seed=0))
    cases.extend(shelf_cases(n_per_layout, seed=1))
    cases.extend(table_under_cases(n_per_layout, seed=2))
    return cases


def save_cases(cases: list[PlanCase], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump([c.to_dict() for c in cases], f, indent=2)


def load_cases(path: Path) -> list[PlanCase]:
    with path.open() as f:
        raw = json.load(f)
    return [PlanCase(**r) for r in raw]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="cases per layout")
    ap.add_argument("--out", default="bench/cases.json")
    args = ap.parse_args()

    cases = all_cases(args.n)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path(__file__).parent.parent / out_path
    save_cases(cases, out_path)
    print(f"wrote {len(cases)} cases to {out_path}")
    by_layout = {}
    for c in cases:
        by_layout[c.layout] = by_layout.get(c.layout, 0) + 1
    for k, v in sorted(by_layout.items()):
        print(f"  {k}: {v}")
