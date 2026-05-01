#!/usr/bin/env python3
"""Emit a 1- or 2-task graph.json for a single batch of run_all_tasks.sh.

Reads the canonical unified graph (`graphs/unified-single-stage/graph.json`),
filters its entries down to the chosen task_envs, and binds them to the
two slot ports the runner brings up. Writes the JSON to stdout.

Usage:
    python3 gen_batch_graph.py "RoboCasa-Open-Drawer-v0" "RoboCasa-Close-Drawer-v0"
    python3 gen_batch_graph.py "RoboCasa-Open-Drawer-v0"   # solo, slot 1 unused
"""

import argparse
import json
import sys
from pathlib import Path

UNIFIED = Path(__file__).resolve().parent / "graphs" / "unified-single-stage" / "graph.json"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("task_a")
    p.add_argument("task_b", nargs="?", default="")
    p.add_argument("--unified", type=Path, default=UNIFIED)
    args = p.parse_args()

    if not args.unified.exists():
        print(f"unified graph not found: {args.unified}", file=sys.stderr)
        return 1

    full = json.loads(args.unified.read_text())
    by_env = {e["task_env"]: e for e in full["entries"] if e.get("task_env")}

    targets = [
        {
            "name": "sim-0", "primary": True,
            "agent_server": "http://localhost:8080",
            "sim_api": "http://localhost:5500",
            "task_env": args.task_a,
        }
    ]
    entries: list[dict] = []
    if args.task_a in by_env:
        e = dict(by_env[args.task_a])
        # Strip dependencies — in batch mode each task is a standalone root.
        e["dependencies"] = []
        entries.append(e)
    else:
        print(f"WARNING: {args.task_a} not in unified graph", file=sys.stderr)

    if args.task_b:
        targets.append({
            "name": "sim-1",
            "agent_server": "http://localhost:8180",
            "sim_api": "http://localhost:5600",
            "task_env": args.task_b,
        })
        if args.task_b in by_env:
            e = dict(by_env[args.task_b])
            e["dependencies"] = []
            entries.append(e)
        else:
            print(f"WARNING: {args.task_b} not in unified graph", file=sys.stderr)

    out = {
        "task_source": full.get("task_source", ""),
        "comment": (
            f"Auto-generated batch graph for run_all_tasks.sh. "
            f"Tasks: {args.task_a} | {args.task_b or '(solo)'}"
        ),
        "targets": targets,
        "entries": entries,
    }
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
