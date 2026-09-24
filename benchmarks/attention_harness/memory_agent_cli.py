"""Run Memory Agent commands against the separate authenticated service."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict

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
    plan.add_argument("--seeds", help="comma-separated development seeds")
    plan.add_argument("--assistance-credits", type=int, default=0)
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
        seeds = None if args.seeds is None else tuple(int(item) for item in args.seeds.split(","))
        value = [
            {"control": asdict(control), "treatment": asdict(treatment)}
            for control, treatment in agent.plan_validation(
                args.memory_id, seeds=seeds,
                assistance_credits=args.assistance_credits,
            )
        ]
    elif args.command == "report":
        value = agent.service.impact_report(args.memory_id)
    else:
        value = agent.request_promotion(args.memory_id).artifact()
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
