"""Discover real development-seed variation IDs before freezing a plan.

This resets a simulator and reads evaluator-side variation identities. It is
not a benchmark trial and does not produce a score or candidate memory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..seed_guard import validate_seed
from .client import RobocasaSimClient


def discover_cases(
    client: RobocasaSimClient, *, seeds: tuple[int, ...],
    camera_configs: tuple[tuple[str, ...], ...],
    task_prompts: tuple[str, ...],
    repeats: int = 2,
) -> list[dict]:
    if len(seeds) < 5 or len(set(seeds)) != len(seeds):
        raise ValueError("discovery needs five distinct development seeds")
    if len(set(camera_configs)) < 2 or len(set(task_prompts)) < 2:
        raise ValueError("discovery needs distinct camera views and task prompts")
    if repeats < 2:
        raise ValueError("discovery needs at least two resets per seed")
    if any(not names or any(not name for name in names) for names in camera_configs):
        raise ValueError("camera configuration must name at least one camera")
    if any(not prompt.strip() for prompt in task_prompts):
        raise ValueError("task prompts must be nonempty")
    client.assert_task()
    cases = []
    for index, seed in enumerate(seeds):
        if validate_seed(seed) != "dev":
            raise PermissionError("variation discovery is development-only")
        applied = None
        for _ in range(repeats):
            reset = client._call(
                "POST", "/reset", {"seed": seed, "discover_variation": True}, timeout=120.0,
            )
            observed = reset.get("applied_variation")
            if reset.get("status") != "ok" or not isinstance(observed, dict) or set(observed) != {"scene_id", "object_set_id"}:
                raise RuntimeError("simulator did not attest discovered scene/object variation")
            if applied is not None and applied != observed:
                raise RuntimeError("same seed produced different scene/object variation IDs")
            applied = observed
        camera_index = index % len(camera_configs)
        task_index = index % len(task_prompts)
        cameras = list(camera_configs[camera_index])
        perception = client._call(
            "POST", "/perceive",
            {"camera_names": cameras, "target_names": None}, timeout=120.0,
        )
        if perception.get("cameras") != cameras:
            raise RuntimeError("simulator did not attest discovered camera views")
        cases.append({
            "seed": seed,
            **applied,
            "camera_config_id": f"camera-{camera_index}",
            "camera_names": cameras,
            "task_variant_id": f"task-variant-{task_index}",
            "task_prompt": task_prompts[task_index],
        })
    for axis in ("scene_id", "object_set_id", "camera_config_id", "task_variant_id"):
        if len({case[axis] for case in cases}) < 2:
            raise RuntimeError(f"discovered cases do not vary {axis}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--sim-url", default="http://127.0.0.1:5500")
    parser.add_argument("--seeds", required=True, help="five or more comma-separated development seeds")
    parser.add_argument(
        "--camera-config", action="append", required=True,
        help="repeat for each camera view; comma-separate cameras within one view",
    )
    parser.add_argument("--task-prompt", action="append", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = discover_cases(
        RobocasaSimClient(args.task, base_url=args.sim_url),
        seeds=tuple(int(seed) for seed in args.seeds.split(",")),
        camera_configs=tuple(tuple(name for name in item.split(",") if name) for item in args.camera_config),
        task_prompts=tuple(args.task_prompt),
        repeats=args.repeats,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
