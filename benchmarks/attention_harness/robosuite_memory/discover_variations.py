"""Discover attested Robosuite development-seed variations for Memory validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from ..seed_guard import validate_seed
from ..task_registry import TASKS
from .adapter import RobosuiteSimGTBackend


def discover_cases(
    task_id: str, *, seeds: tuple[int, ...], cameras: tuple[str, ...],
    task_prompts: tuple[str, ...],
    adapter_factory: Callable[[str, str], RobosuiteSimGTBackend], repeats: int = 2,
) -> list[dict]:
    if len(seeds) < 5 or len(set(seeds)) != len(seeds):
        raise ValueError("discovery needs five distinct development seeds")
    if len(set(cameras)) < 2 or len(set(task_prompts)) < 2 or repeats < 2:
        raise ValueError("discovery needs distinct cameras/prompts and repeated reset")
    if any(not name for name in cameras) or any(not prompt.strip() for prompt in task_prompts):
        raise ValueError("camera names and task prompts must be nonempty")
    cases = []
    for index, seed in enumerate(seeds):
        if validate_seed(seed) != "dev":
            raise PermissionError("variation discovery is development-only")
        camera = cameras[index % len(cameras)]
        adapter = adapter_factory(task_id, camera)
        try:
            applied = None
            for _ in range(repeats):
                _, observed = adapter.reset_attested(seed)
                if not isinstance(observed, dict) or set(observed) != {"scene_id", "object_set_id"}:
                    raise RuntimeError("simulator did not attest scene/object variation")
                if applied is not None and applied != observed:
                    raise RuntimeError("same seed produced different scene/object variation IDs")
                applied = observed
            response = adapter.perceive_gt(camera_names=[camera])
            if response.get("cameras") != [camera]:
                raise RuntimeError("simulator did not attest discovered camera view")
            cases.append({
                "seed": seed, **applied,
                "camera_config_id": f"camera:{camera}", "camera_names": [camera],
                "task_variant_id": f"task-variant-{index % len(task_prompts)}",
                "task_prompt": task_prompts[index % len(task_prompts)],
            })
        finally:
            adapter.close()
    for axis in ("scene_id", "object_set_id", "camera_config_id", "task_variant_id"):
        if len({case[axis] for case in cases}) < 2:
            raise RuntimeError(f"discovered cases do not vary {axis}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--sim-url", default="http://127.0.0.1:8082")
    parser.add_argument("--seeds", required=True, help="five or more comma-separated development seeds")
    parser.add_argument("--camera", action="append", required=True)
    parser.add_argument("--task-prompt", action="append", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = discover_cases(
        args.task, seeds=tuple(int(seed) for seed in args.seeds.split(",")),
        cameras=tuple(args.camera), task_prompts=tuple(args.task_prompt),
        adapter_factory=lambda task, camera: RobosuiteSimGTBackend(
            task, service_url=args.sim_url, camera_name=camera,
        ), repeats=args.repeats,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cases, indent=2, sort_keys=True) + "\n")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
