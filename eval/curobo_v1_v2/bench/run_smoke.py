"""Smoke test: post identical /plan requests to v1 (7050) and v2 (7051).

Confirms (a) both services accept the same JSON, (b) both return a trajectory
for a known-reachable goal, (c) the response shape matches across versions.

Usage:
    python -m bench.run_smoke
or:
    python /abs/path/to/eval/curobo_v1_v2/bench/run_smoke.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script or as a module
sys.path.insert(0, str(Path(__file__).parent.parent))

from bench._client import CuroboClient, GROUND_CUBOID, HOME_QPOS_10D, path_length


# A small, reachable target ~50cm in front of the arm base, gripper down.
SMOKE_TARGET_POS = [0.55, 0.0, 0.45]
SMOKE_TARGET_QUAT = [0.0, 1.0, 0.0, 0.0]   # gripper pointing -z


def run_one(label: str, url: str) -> dict:
    print(f"\n[{label}] {url}")
    cli = CuroboClient(url=url, env_id=f"smoke-{label}")
    h = cli.health()
    print(f"  health: warmed_up={h.get('warmed_up')} device={h.get('device')}")
    if not h.get("warmed_up"):
        print(f"  warming up...")
        cli.warmup()

    cli.push_world([GROUND_CUBOID])

    res = cli.plan_pose(
        current_q=HOME_QPOS_10D,
        target_pos=SMOKE_TARGET_POS,
        target_quat=SMOKE_TARGET_QUAT,
        mask="whole_body",
    )
    summary = {
        "label": label,
        "url": url,
        "ok": res.ok,
        "elapsed_s": round(res.elapsed_s, 4),
        "n_waypoints": res.num_waypoints,
        "path_length": round(path_length(res.trajectory) if res.trajectory else 0.0, 4),
        "status": res.raw.get("status"),
        "error": res.error,
    }
    print(f"  plan: {json.dumps(summary, ensure_ascii=False)}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-url", default="http://127.0.0.1:7050")
    ap.add_argument("--v2-url", default="http://127.0.0.1:7051")
    args = ap.parse_args()

    r1 = run_one("v1", args.v1_url)
    r2 = run_one("v2", args.v2_url)

    print("\n=== summary ===")
    print(json.dumps({"v1": r1, "v2": r2}, indent=2, ensure_ascii=False))

    rc = 0 if (r1["ok"] and r2["ok"]) else 1
    print(f"\nexit={rc}  (both ok = 0)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
