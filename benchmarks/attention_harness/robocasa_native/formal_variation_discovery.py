"""Discover every RoboCasa development variation with an isolated real Service.

Each seed is recorded independently so one broken reset does not hide later
seeds. This is engineering evidence, never a formal score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path

from ..seed_guard import validate_seed
from .client import RobocasaSimClient
from .formal_services import DedicatedRobocasaServices, source_identity


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("counter_to_cab", "counter_to_sink"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sim-source-root", type=Path, required=True)
    parser.add_argument("--agent-source-root", type=Path, required=True)
    parser.add_argument("--task-source-root", type=Path, required=True)
    parser.add_argument("--sim-python", type=Path, required=True)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--seed-start", type=int, default=101)
    parser.add_argument("--seed-end", type=int, default=125)
    parser.add_argument("--port-offset", type=int, default=1400)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    seeds = range(args.seed_start, args.seed_end + 1)
    if not seeds or any(validate_seed(seed) != "dev" for seed in seeds):
        parser.error("only a nonempty development seed range is allowed")
    config = json.loads(args.config.read_text())
    if config["task_id"] != args.task:
        parser.error("config task differs from requested task")
    report = {
        "schema_version": "attentionbench.robocasa-variation-discovery.v1",
        "task_id": args.task,
        "seeds_requested": list(seeds),
        "formal_eligible": False,
        "command": shlex.join(sys.orig_argv),
        "config_uri": str(args.config.resolve()),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "source_identities": {
            "sim": source_identity(args.sim_source_root),
            "agent": source_identity(args.agent_source_root),
            "task": source_identity(args.task_source_root),
        },
        "cases": [],
    }
    services = DedicatedRobocasaServices(
        task_id=args.task,
        sim_source_root=args.sim_source_root,
        agent_source_root=args.agent_source_root,
        task_source_root=args.task_source_root,
        sim_python=args.sim_python,
        agent_python=args.agent_python,
        expected_sim=config["sim_service"],
        expected_agent=config["agent_service"],
        expected_task=config["task_source"],
        expected_runtime=config["sim_runtime"],
        port_offset=args.port_offset,
        log_dir=args.log_dir,
        deadline=time.monotonic() + 1200,
    )
    try:
        with services:
            client = RobocasaSimClient(args.task, base_url=services.sim_url)
            client.assert_task()
            for seed in seeds:
                case = {"seed": seed, "passed": False}
                if services.stop_receipt is not None:
                    case["error"] = "dedicated Service stopped before this seed"
                    report["cases"].append(case)
                    _write(args.evidence, report)
                    continue
                try:
                    variations = []
                    for _ in range(2):
                        reset = client._call("POST", "/reset", {
                            "seed": seed, "discover_variation": True,
                        }, timeout=min(120.0, max(1.0, services.deadline - time.monotonic())))
                        if reset.get("status") != "ok":
                            raise RuntimeError(f"reset returned {reset!r}")
                        variations.append(reset.get("applied_variation"))
                    if (not isinstance(variations[0], dict)
                            or set(variations[0]) != {"scene_id", "object_set_id"}
                            or variations[0] != variations[1]):
                        raise RuntimeError("repeated reset variation identities differ")
                    ground_truth = client._call("POST", "/objects/ground_truth", {})
                    names = [item.get("name") for item in ground_truth.get("objects", [])]
                    if "obj" not in names or "distr_counter" not in names:
                        raise RuntimeError(f"task objects missing from evaluator: {names!r}")
                    observed = client.observe()
                    case.update({
                        "passed": True,
                        "variation": variations[0],
                        "evaluator_object_names": names,
                        "public_object_count": len(observed.objects),
                        "cameras": list(observed.cameras),
                        "task_prompt": observed.language,
                        "native_success_after_reset": client.native_success(),
                    })
                    if case["native_success_after_reset"]:
                        raise RuntimeError("native success was true immediately after reset")
                except Exception as exc:
                    case.update({"passed": False,
                                 "error": f"{type(exc).__name__}: {exc}"})
                report["cases"].append(case)
                _write(args.evidence, report)
    finally:
        report["service_stop"] = services.stop_receipt
        report["source_unchanged"] = services.source_unchanged()
        report["logs"] = {
            name: {"uri": str((args.log_dir / f"{name}.log").resolve()),
                   "sha256": hashlib.sha256((args.log_dir / f"{name}.log").read_bytes()).hexdigest()}
            for name in ("simulator", "agent")
            if (args.log_dir / f"{name}.log").is_file()
        }
        report["all_discoveries_passed"] = (
            len(report["cases"]) == len(seeds)
            and all(case["passed"] for case in report["cases"])
            and report["source_unchanged"]
        )
        _write(args.evidence, report)
    print(json.dumps({"all_discoveries_passed": report["all_discoveries_passed"],
                      "passed": sum(case["passed"] for case in report["cases"]),
                      "total": len(report["cases"]),
                      "evidence": str(args.evidence.resolve())}, ensure_ascii=False))
    return 0 if report["all_discoveries_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
