"""Run the frozen public policies on the development split and write evidence."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from .frozen_policy import execute_frozen_policy, get_frozen_policy
from .robosuite_adapter import RobosuiteRobotBackend
from .service_process import ManagedRobosuiteService
from .task_registry import TASKS


DEV_SEEDS = tuple(range(101, 126))


def validate_development_split() -> dict[str, Any]:
    results = []
    for task_id in sorted(TASKS):
        policy = get_frozen_policy(task_id)
        with tempfile.TemporaryDirectory(prefix=f"attentionbench-{task_id}-") as temp:
            temp_root = Path(temp)
            with ManagedRobosuiteService(log_path=temp_root / "service.log") as service_url:
                adapter = RobosuiteRobotBackend(task_id, service_url=service_url)
                try:
                    for seed in DEV_SEEDS:
                        adapter.reset(seed)
                        execution = execute_frozen_policy(
                            task_id=task_id,
                            service_url=service_url,
                            output_path=temp_root / "sandbox_worker.json",
                            timeout_seconds=90.0,
                        )
                        adapter.refresh()
                        results.append(
                            {
                                "task_id": task_id,
                                "seed": seed,
                                "sandbox_status": execution.status,
                                "steps": len(execution.trace),
                                "native_success": adapter.native_success(),
                                "error": execution.error,
                            }
                        )
                finally:
                    adapter.close()
        if not all(
            row["sandbox_status"] == "completed" and row["native_success"]
            for row in results
            if row["task_id"] == task_id
        ):
            break

    task_summary = {}
    for task_id in sorted(TASKS):
        rows = [row for row in results if row["task_id"] == task_id]
        task_summary[task_id] = {
            "passed": sum(
                row["sandbox_status"] == "completed" and row["native_success"]
                for row in rows
            ),
            "total": len(rows),
            "policy_sha256": get_frozen_policy(task_id).sha256(),
        }
    passed = len(results) == len(TASKS) * len(DEV_SEEDS) and all(
        row["sandbox_status"] == "completed" and row["native_success"]
        for row in results
    )
    return {
        "schema_version": "attentionbench.public-policy-validation.v1",
        "seed_split": "dev",
        "seeds": {"start": DEV_SEEDS[0], "stop": DEV_SEEDS[-1]},
        "observation_boundary": "public RGB-D, camera calibration, and proprioception only",
        "authoritative_success": "Robosuite native _check_success",
        "passed": passed,
        "task_summary": task_summary,
        "episodes": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent
        / "protocol"
        / "v1"
        / "evidence"
        / "public_policy_dev_report.json",
    )
    args = parser.parse_args()
    report = validate_development_split()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["task_summary"], indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
