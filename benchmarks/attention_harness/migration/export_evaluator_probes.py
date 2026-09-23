#!/usr/bin/env python3
"""Export evaluator probes from ASPIRE legacy or TidyBot native.

Run ``legacy`` with ASPIRE's Robosuite venv from the ASPIRE sim root. Run
``native`` with AttentionHarness's pinned venv from the TidyBot repository.
State mutation here only probes the native predicate and is never model-visible.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any, Callable

import numpy as np


TASKS = ("cube_lift", "cube_stack")
KNOWN_ENVIRONMENT_DIFFERENCES = [
    "legacy rotates Panda joint 7 by -pi after reset",
    "legacy settles physics for 50 raw simulation steps",
    "legacy uses a joint-position controller; native uses Robosuite's Panda composite controller",
    "legacy Stack replaces the default placement sampler ranges",
    "legacy enables reward shaping; evaluator predicates remain native",
]


def _predicate_hash(environment: Any, task_id: str) -> str:
    functions = [type(environment)._check_success]
    if task_id == "cube_stack":
        functions.append(type(environment).staged_rewards)
    normalized = []
    for function in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        normalized.append(ast.dump(tree, annotate_fields=True, include_attributes=False))
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def _set_object_pose(environment: Any, obj: Any, position: np.ndarray) -> None:
    environment.sim.data.set_joint_qpos(
        obj.joints[0],
        np.concatenate((np.asarray(position, dtype=float), [1.0, 0.0, 0.0, 0.0])),
    )
    environment.sim.forward()


def _probe_lift(environment: Any) -> list[tuple[str, bool]]:
    cube = np.array(environment.sim.data.body_xpos[environment.cube_body_id], copy=True)
    table = float(environment.model.mujoco_arena.table_offset[2])
    rows = [("reset_failure", bool(environment._check_success()))]
    cube[2] = table + 0.039
    _set_object_pose(environment, environment.cube, cube)
    rows.append(("below_lift_margin", bool(environment._check_success())))
    cube[2] = table + 0.041
    _set_object_pose(environment, environment.cube, cube)
    rows.append(("above_lift_margin", bool(environment._check_success())))
    return rows


def _probe_stack(environment: Any) -> list[tuple[str, bool]]:
    cube_b = np.array(environment.sim.data.body_xpos[environment.cubeB_body_id], copy=True)
    rows = [("reset_failure", bool(environment._check_success()))]
    separated = np.array(cube_b, copy=True)
    separated[0] += 0.10
    separated[2] += 0.045
    _set_object_pose(environment, environment.cubeA, separated)
    rows.append(("separated_cubes", bool(environment._check_success())))
    stacked = np.array(cube_b, copy=True)
    # Use a stable, shallow overlap rather than the exact 0.045 m sum of the
    # cubes' half-heights. At exact tangency MuJoCo contact generation differs
    # between the ASPIRE fork and PyPI build despite identical predicates.
    stacked[2] += 0.044
    _set_object_pose(environment, environment.cubeA, stacked)
    rows.append(("stable_contact_stack", bool(environment._check_success())))
    return rows


def _legacy_factory(task_id: str) -> tuple[Any, Callable[[int], None], Callable[[], None], Any]:
    if task_id == "cube_lift":
        from aspire.sim.cap.envs.simulators.robosuite_cube_lift import (
            FrankaRobosuiteCubeLiftLowLevel,
        )

        owner = FrankaRobosuiteCubeLiftLowLevel(
            privileged=True, enable_render=False, max_steps=500
        )
    else:
        from aspire.sim.cap.envs.simulators.robosuite_cubes import (
            FrankaRobosuiteCubesLowLevel,
        )

        owner = FrankaRobosuiteCubesLowLevel(
            privileged=True, enable_render=False, max_steps=500
        )
    return owner, lambda seed: owner.reset(seed=seed), owner.close, owner.robosuite_env


def _native_factory(task_id: str) -> tuple[Any, Callable[[int], None], Callable[[], None], Any]:
    from robosuite_sim.backend import BackendConfig, RobosuiteBackend

    owner = RobosuiteBackend(BackendConfig(task_id=task_id, horizon=500, camera=False))
    return owner, owner.reset, owner.close, owner._env


def export(
    implementation: str,
    seeds: list[int],
    *,
    source_revision: str | None = None,
) -> dict[str, Any]:
    factory = _legacy_factory if implementation == "legacy" else _native_factory
    records: list[dict[str, Any]] = []
    predicate_hashes: dict[str, str] = {}
    for task_id in TASKS:
        _owner, reset, close, environment = factory(task_id)
        try:
            predicate_hashes[task_id] = _predicate_hash(environment, task_id)
            probe = _probe_lift if task_id == "cube_lift" else _probe_stack
            for seed in seeds:
                reset(seed)
                for probe_id, result in probe(environment):
                    records.append(
                        {
                            "task_id": task_id,
                            "seed": seed,
                            "probe_id": probe_id,
                            "native_success": result,
                        }
                    )
        finally:
            close()
    return {
        "schema_version": "attentionbench.evaluator-probes.v1",
        "metadata": {
            "implementation": implementation,
            "source_revision": source_revision,
            "versions": {
                name: importlib.metadata.version(name)
                for name in ("robosuite", "mujoco", "numpy")
            },
            "predicate_ast_sha256": predicate_hashes,
            "known_environment_differences": KNOWN_ENVIRONMENT_DIFFERENCES,
            "probe_scope": "native evaluator only; not reset, observation, or controller parity",
        },
        "records": records,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation", choices=("legacy", "native"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default="101,102,103,104,105")
    parser.add_argument("--source-revision")
    return parser


def main() -> int:
    args = _parser().parse_args()
    seeds = [int(value) for value in args.seeds.split(",") if value.strip()]
    if not seeds:
        raise ValueError("at least one seed is required")
    dataset = export(args.implementation, seeds, source_revision=args.source_revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "implementation": args.implementation,
                "records": len(dataset["records"]),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
