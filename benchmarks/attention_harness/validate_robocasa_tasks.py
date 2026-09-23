"""Validate one live RoboCasa task and merge non-oracle evidence into a report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .robocasa_native.client import RobocasaSimClient
from .robocasa_native.probe import PrivilegedRobocasaProbe
from .robocasa_native.tasks import ROBOCASA_TASKS
from .seed_guard import validate_seed


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "protocol"
    / "v1"
    / "evidence"
    / "robocasa_task_report.json"
)


def validate_task(
    *,
    task_id: str,
    base_url: str,
    seeds: tuple[int, ...],
    service_revision: str,
    task_revision: str,
) -> dict[str, Any]:
    client = RobocasaSimClient(task_id, base_url=base_url)
    info = client.assert_task()
    probe = PrivilegedRobocasaProbe(
        task_id,
        base_url=client.base_url,
        transport=client._http_json,
    )
    episodes = []
    for seed in seeds:
        validate_seed(seed, allow_heldout=False)
        initial = client.reset(seed)
        no_op_success = client.native_success()
        probe.force_reference_success()
        reference_success = client.native_success()
        recovered = client.reset(seed)
        reset_cleared_success = not client.native_success()
        episodes.append(
            {
                "seed": seed,
                "initial_observation_sha256": initial.fingerprint(),
                "recovered_observation_sha256": recovered.fingerprint(),
                "no_op_success": no_op_success,
                "privileged_reference_success": reference_success,
                "reset_cleared_success": reset_cleared_success,
            }
        )
    passed = all(
        not row["no_op_success"]
        and row["privileged_reference_success"]
        and row["reset_cleared_success"]
        for row in episodes
    )
    return {
        "task_id": task_id,
        "environment_id": ROBOCASA_TASKS[task_id].environment_id,
        "language": info["lang"],
        "service_revision": service_revision,
        "task_revision": task_revision,
        "native_success_endpoint": "/task/success",
        "public_observation": "task info plus existing RGB-D segmentation perception",
        "privileged_reference_role": "infrastructure validation only",
        "passed": passed,
        "episodes": episodes,
        "summary": {
            "no_op_failures": sum(not row["no_op_success"] for row in episodes),
            "reference_successes": sum(
                row["privileged_reference_success"] for row in episodes
            ),
            "reset_recoveries": sum(row["reset_cleared_success"] for row in episodes),
            "total": len(episodes),
        },
    }


def merge_report(output: Path, task: dict[str, Any]) -> dict[str, Any]:
    if output.is_file():
        report = json.loads(output.read_text(encoding="utf-8"))
    else:
        report = {
            "schema_version": "attentionbench.robocasa-task-validation.v1",
            "seed_split": "dev",
            "oracle_visible_to_policy": False,
            "tasks": {},
        }
    report["tasks"][task["task_id"]] = task
    report["passed"] = set(report["tasks"]) == set(ROBOCASA_TASKS) and all(
        item.get("passed") for item in report["tasks"].values()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(ROBOCASA_TASKS), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5500")
    parser.add_argument("--seeds", default="101,102,103,104,105")
    parser.add_argument("--service-revision", required=True)
    parser.add_argument("--task-revision", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    seeds = tuple(int(value) for value in args.seeds.split(",") if value)
    task = validate_task(
        task_id=args.task,
        base_url=args.base_url,
        seeds=seeds,
        service_revision=args.service_revision,
        task_revision=args.task_revision,
    )
    report = merge_report(args.output, task)
    print(json.dumps(task["summary"], indent=2, sort_keys=True))
    return 0 if task["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
