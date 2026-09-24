"""Run AttentionBench using the pinned, independently installed Robosuite service."""

from __future__ import annotations

import json
from contextlib import nullcontext

from .external_robosuite_service import ManagedExternalRobosuiteService
from .runner import _parser, run_episode


def main() -> int:
    args = _parser().parse_args()
    service = (
        nullcontext(args.service_url)
        if args.service_url
        else ManagedExternalRobosuiteService(log_path=args.artifact_root / "external-robosuite-service.log")
    )
    with service as url:
        if args.policy == "parcc":
            from .parcc_runner import run_parcc_episode

            result = run_parcc_episode(
                task_id=args.task,
                seed=args.seed,
                artifact_root=args.artifact_root,
                service_url=url,
                policy_timeout_seconds=args.timeout,
            )
        else:
            result = run_episode(
                task_id=args.task,
                seed=args.seed,
                policy=args.policy,
                artifact_root=args.artifact_root,
                camera=not args.no_camera,
                timeout_seconds=args.timeout,
                allow_heldout=args.allow_heldout,
                service_url=url,
            )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
