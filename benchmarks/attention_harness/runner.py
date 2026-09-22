"""CLI and programmatic runner for native Robosuite episodes."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .artifacts import create_episode_dir, write_episode_artifacts
from .reference_policy import EpisodeTimeout, run_reference_policy
from .robosuite_adapter import RobosuiteAdapter, observation_fingerprint
from .seed_guard import validate_seed
from .service_process import ManagedRobosuiteService
from .task_registry import TASKS


def run_episode(
    *,
    task_id: str,
    seed: int,
    policy: str,
    artifact_root: Path,
    camera: bool = True,
    timeout_seconds: float = 60.0,
    allow_heldout: bool = False,
    service_url: str | None = None,
) -> dict[str, Any]:
    split = validate_seed(seed, allow_heldout=allow_heldout)
    episode_dir = create_episode_dir(artifact_root, task_id, seed)
    started = time.monotonic()
    status = "completed"
    error: str | None = None
    initial = {}
    final = {}

    service_context = (
        nullcontext(service_url)
        if service_url
        else ManagedRobosuiteService(log_path=episode_dir / "service.log")
    )
    with service_context as active_service_url:
        adapter = RobosuiteAdapter(task_id, camera=camera, service_url=active_service_url)
        try:
            initial = adapter.reset(seed)
            if policy == "reference":
                run_reference_policy(adapter, timeout_seconds=timeout_seconds)
            elif policy == "no-op":
                for _ in range(50):
                    if time.monotonic() - started > timeout_seconds:
                        raise EpisodeTimeout("no-op policy exceeded episode timeout")
                    adapter.step(adapter.no_op_action())
            else:
                raise ValueError(f"unknown policy {policy!r}")
            final = adapter.observe()
            success = adapter.native_success()
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            success = False
            if initial:
                final = adapter.observe()
        finally:
            elapsed = time.monotonic() - started
            metadata = adapter.metadata
            trace = list(adapter.trace)
            adapter.close()

    result = {
        "schema_version": "attentionbench.episode.v1",
        "status": status,
        "error": error,
        "task_id": task_id,
        "robosuite_env": TASKS[task_id].robosuite_env,
        "seed": seed,
        "seed_split": split,
        "policy": policy,
        "native_success": success,
        "steps": len(trace),
        "elapsed_seconds": elapsed,
        "initial_observation_sha256": observation_fingerprint(initial) if initial else None,
        "final_observation_sha256": observation_fingerprint(final) if final else None,
        "runtime": metadata,
        "artifact_dir": str(episode_dir.resolve()),
    }
    write_episode_artifacts(
        episode_dir,
        result=result,
        trace=trace,
        initial_observation=initial,
        final_observation=final,
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--policy", choices=("no-op", "reference", "parcc"), required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--allow-heldout", action="store_true")
    parser.add_argument(
        "--service-url",
        help="Use an already-running robosuite_sim; otherwise the runner manages one.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.policy == "parcc":
        from .parcc_runner import run_parcc_episode

        result = run_parcc_episode(
            task_id=args.task,
            seed=args.seed,
            artifact_root=args.artifact_root,
            service_url=args.service_url,
            policy_timeout_seconds=args.timeout,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "completed" else 1
    result = run_episode(
        task_id=args.task,
        seed=args.seed,
        policy=args.policy,
        artifact_root=args.artifact_root,
        camera=not args.no_camera,
        timeout_seconds=args.timeout,
        allow_heldout=args.allow_heldout,
        service_url=args.service_url,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
