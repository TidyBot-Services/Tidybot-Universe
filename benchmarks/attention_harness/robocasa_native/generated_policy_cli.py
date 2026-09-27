"""Run one RoboCasa sim_gt generated-policy sandbox smoke attempt.

This command does not emit a formal score. The action service's cancellation
semantics and the frozen-seed gate still require acceptance evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..v2_advisor import SimGTGLMAdvisorTransport
from .agent_actions import AgentServerActionBackend
from .client import RobocasaSimClient
from .generated_policy_runner import run_robocasa_generated_policy_episode
from .tasks import ROBOCASA_TASKS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(ROBOCASA_TASKS), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--hypothesis", default="")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--perception-mode", choices=["sim_gt"], required=True)
    parser.add_argument("--variation", type=Path)
    parser.add_argument("--sim-url", default="http://127.0.0.1:5500")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8080")
    parser.add_argument("--confirm-simulator-agent", action="store_true")
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-v2-gt"))
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--advisor", action="store_true")
    args = parser.parse_args()
    if not args.confirm_simulator_agent:
        parser.error("--confirm-simulator-agent is required")
    result = run_robocasa_generated_policy_episode(
        task_id=args.task, seed=args.seed, artifact_root=args.artifact_root,
        action_backend=AgentServerActionBackend(
            base_url=args.agent_url, simulator_attested=True,
            timeout_seconds=min(args.timeout_seconds, 90.0),
        ),
        policy_code_path=args.code, policy_id=args.code.stem,
        perception_mode=args.perception_mode,
        client=RobocasaSimClient(args.task, base_url=args.sim_url),
        store_path=args.store_path or args.artifact_root / "attention_memory.sqlite3",
        runtime_variation=None if args.variation is None else json.loads(args.variation.read_text()),
        advisor_transport=SimGTGLMAdvisorTransport() if args.advisor else None,
        assistance_credits=1 if args.advisor else 0,
        hypothesis=args.hypothesis,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
