"""Run Memory Agent commands against the separate authenticated service."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from .memory_agent import MemoryAgent
from .memory_service_client import MemoryServiceClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-url", default="http://127.0.0.1:8768")
    parser.add_argument("--api-key-env", default="ATTENTION_MEMORY_API_KEY")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("request_id")
    ingest.add_argument("--memory-id")
    ingest.add_argument("--human-attention-seconds", type=float, default=0.0)
    plan = commands.add_parser("plan")
    plan.add_argument("memory_id")
    plan.add_argument("--cases", type=Path, required=True, help="JSON array of frozen variation cases")
    plan.add_argument("--assistance-credits", type=int, default=0)
    validate = commands.add_parser("validate")
    validate.add_argument("memory_id")
    validate.add_argument("--cases", type=Path, required=True, help="same frozen variation cases used in the plan")
    validate.add_argument("--assistance-credits", type=int, default=0)
    validate.add_argument("--policy", required=True, help="same trusted module:function policy as source run")
    validate.add_argument("--sim-url", default="http://127.0.0.1:5500")
    validate.add_argument("--agent-url", default="http://127.0.0.1:8080")
    validate.add_argument("--artifact-root", type=Path, required=True)
    validate.add_argument("--store-path", type=Path, required=True)
    validate.add_argument("--confirm-simulator-agent", action="store_true", required=True)
    validate.add_argument("--max-delta-m", type=float, default=0.25)
    validate.add_argument("--max-observed-step-m", type=float, default=0.5)
    validate.add_argument("--promote", action="store_true", help="ask the Memory Service to apply its promotion gate")
    report = commands.add_parser("report")
    report.add_argument("memory_id")
    promote = commands.add_parser("promote")
    promote.add_argument("memory_id")
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env, "")
    agent = MemoryAgent(MemoryServiceClient(args.service_url, api_key=key))
    if args.command == "ingest":
        value = agent.ingest_answered_hint(
            args.request_id, memory_id=args.memory_id,
            human_attention_seconds=args.human_attention_seconds,
        ).artifact()
    elif args.command == "plan":
        cases = tuple(json.loads(args.cases.read_text()))
        value = [
            {"control": asdict(control), "treatment": asdict(treatment)}
            for control, treatment in agent.plan_validation(
                args.memory_id, cases=cases,
                assistance_credits=args.assistance_credits,
            )
        ]
    elif args.command == "report":
        value = agent.service.impact_report(args.memory_id)
    elif args.command == "validate":
        if not args.confirm_simulator_agent:
            parser.error("--confirm-simulator-agent is required for simulator validation")
        from .robocasa_native.agent_actions import AgentServerActionBackend
        from .robocasa_native.client import RobocasaSimClient
        from .robocasa_native.paired_trials import RoboCasaPairedTrialExecutor
        from .robocasa_native.sim_gt_cli import _load_policy

        cases = tuple(json.loads(args.cases.read_text()))
        executor = RoboCasaPairedTrialExecutor(
            policy=_load_policy(args.policy), policy_id=args.policy,
            artifact_root=args.artifact_root, store_path=args.store_path,
            backend_factory=lambda: AgentServerActionBackend(
                base_url=args.agent_url, simulator_attested=True,
            ),
            client_factory=lambda task: RobocasaSimClient(task, base_url=args.sim_url),
            memory_gateway=agent.service,
            max_delta_m=args.max_delta_m,
            max_observed_step_m=args.max_observed_step_m,
        )
        value = agent.run_validation(
            args.memory_id, executor=executor, cases=cases,
            assistance_credits=args.assistance_credits,
        )
        if args.promote:
            value = {"impact": value, "memory": agent.request_promotion(args.memory_id).artifact()}
    else:
        value = agent.request_promotion(args.memory_id).artifact()
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
