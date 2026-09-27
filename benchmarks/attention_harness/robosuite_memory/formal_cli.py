"""Run one Robosuite formal-path development attempt through its boundary."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

from ..formal_runner_boundary import FormalRunRequest
from ..sim_gt_attention_run import run_robosuite_formal_attempt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--approved-policy-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--approved-config-sha256", required=True)
    parser.add_argument("--service-source-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--overall-deadline-seconds", type=float, required=True)
    parser.add_argument("--cancel-after-policy-start-seconds", type=float,
                        help="acceptance probe: request operator cancellation after policy starts")
    args = parser.parse_args()
    if args.cancel_after_policy_start_seconds is not None and args.cancel_after_policy_start_seconds <= 0:
        parser.error("cancel delay must be positive")
    cancel = threading.Event()
    timer: threading.Timer | None = None

    def start_timer() -> None:
        nonlocal timer
        if args.cancel_after_policy_start_seconds is not None:
            timer = threading.Timer(args.cancel_after_policy_start_seconds, cancel.set)
            timer.start()

    request = FormalRunRequest(
        suite="robosuite", task_id=args.task, seed=args.seed,
        policy_code_path=args.code,
        policy_sha256=args.approved_policy_sha256,
        config_sha256=args.approved_config_sha256,
        config_path=args.config,
        artifact_root=args.artifact_root,
        overall_deadline_seconds=args.overall_deadline_seconds,
    )
    try:
        result = run_robosuite_formal_attempt(
            request,
            service_source_root=args.service_source_root,
            cancel_event=cancel, policy_start_hook=start_timer,
        )
    finally:
        if timer is not None:
            timer.cancel()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["boundary_checked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
