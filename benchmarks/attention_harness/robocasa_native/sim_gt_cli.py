"""Run one RoboCasa GT-perception development attempt on existing services.

The imported policy is trusted lab code, not a sandboxed generated policy.
Only development seeds are accepted; this CLI never produces a formal score.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

from attention_memory_service import MemoryServiceClient

from ..parcc_advisor import ParccGLMAdvisorTransport
from .agent_actions import AgentServerActionBackend
from .client import RobocasaSimClient
from .sim_gt_runner import run_robocasa_sim_gt_episode
from .tasks import ROBOCASA_TASKS


def _no_op(sdk, context):
    del sdk, context


def _load_policy(specification: str):
    if specification == "noop":
        return _no_op
    module_name, separator, function_name = specification.partition(":")
    if not separator or not module_name or not function_name or not function_name.isidentifier():
        raise ValueError("policy must be 'noop' or 'module:function'")
    policy = getattr(importlib.import_module(module_name), function_name)
    if not callable(policy):
        raise TypeError("policy entry point must be callable")
    return policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(ROBOCASA_TASKS), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--perception-mode", choices=["sim_gt"], required=True)
    parser.add_argument("--policy", required=True, help="trusted module:function or noop")
    parser.add_argument("--variation", type=Path, help="JSON variation object attested by simulator reset")
    parser.add_argument("--sim-url", default="http://127.0.0.1:5500")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--confirm-simulator-agent", action="store_true", required=False,
        help="confirm the agent_server endpoint controls only this simulator, never hardware",
    )
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-v2-gt"))
    parser.add_argument(
        "--store-path", type=Path,
        help="shared Attention SQLite path (default: ARTIFACT_ROOT/attention_memory.sqlite3)",
    )
    parser.add_argument(
        "--validation-memory-id",
        help="explicitly expose one candidate for a paired development-seed validation run",
    )
    parser.add_argument(
        "--no-memory", action="store_true", help="control run: do not retrieve trusted memory",
    )
    parser.add_argument("--advisor", action="store_true", help="ask PARCC GLM on failure")
    parser.add_argument(
        "--memory-service-url",
        help="authenticated Memory Service using the same Attention SQLite store",
    )
    args = parser.parse_args()
    if not args.confirm_simulator_agent:
        parser.error("--confirm-simulator-agent is required before enabling sim_gt actions")
    memory_gateway = None
    if args.memory_service_url:
        memory_gateway = MemoryServiceClient(
            args.memory_service_url,
            api_key=os.environ.get("ATTENTION_MEMORY_API_KEY", ""),
        )
    result = run_robocasa_sim_gt_episode(
        task_id=args.task,
        seed=args.seed,
        artifact_root=args.artifact_root,
        action_backend=AgentServerActionBackend(
            base_url=args.agent_url, simulator_attested=True
        ),
        policy=_load_policy(args.policy),
        policy_id=args.policy,
        perception_mode=args.perception_mode,
        client=RobocasaSimClient(args.task, base_url=args.sim_url),
        store_path=args.store_path or args.artifact_root / "attention_memory.sqlite3",
        validation_memory_id=args.validation_memory_id,
        runtime_variation=None if args.variation is None else json.loads(args.variation.read_text()),
        retrieve_memory=not args.no_memory,
        advisor_transport=ParccGLMAdvisorTransport() if args.advisor else None,
        assistance_credits=1 if args.advisor else 0,
        memory_gateway=memory_gateway,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
