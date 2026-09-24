"""Run one development-only Robosuite GT-perception Memory attempt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from attention_memory_service import MemoryServiceClient

from ..parcc_advisor import ParccGLMAdvisorTransport
from ..robocasa_native.sim_gt_cli import _load_policy
from ..task_registry import TASKS
from .adapter import RobosuiteSimGTBackend
from .sim_gt_runner import run_robosuite_sim_gt_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--perception-mode", choices=["sim_gt"], required=True)
    parser.add_argument("--policy", required=True, help="trusted module:function or noop")
    parser.add_argument("--camera-name", default="agentview")
    parser.add_argument("--variation", type=Path, help="JSON variation object attested by simulator reset")
    parser.add_argument("--sim-url", default="http://127.0.0.1:8082")
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-v2-gt"))
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--advisor", action="store_true")
    parser.add_argument("--memory-service-url")
    args = parser.parse_args()
    gateway = None
    if args.memory_service_url:
        gateway = MemoryServiceClient(
            args.memory_service_url,
            api_key=os.environ.get("ATTENTION_MEMORY_API_KEY", ""),
        )
    adapter = RobosuiteSimGTBackend(
        args.task, service_url=args.sim_url, camera_name=args.camera_name,
    )
    try:
        result = run_robosuite_sim_gt_episode(
            task_id=args.task, seed=args.seed,
            artifact_root=args.artifact_root, adapter=adapter,
            policy=_load_policy(args.policy), policy_id=args.policy,
            perception_mode=args.perception_mode,
            store_path=args.store_path or args.artifact_root / "attention_memory.sqlite3",
            runtime_variation=None if args.variation is None else json.loads(args.variation.read_text()),
            retrieve_memory=not args.no_memory,
            advisor_transport=ParccGLMAdvisorTransport() if args.advisor else None,
            assistance_credits=1 if args.advisor else 0,
            memory_gateway=gateway,
        )
    finally:
        adapter.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
