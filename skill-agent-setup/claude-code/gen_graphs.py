#!/usr/bin/env python3
"""Generate one graph folder per registered RoboCasa single-stage task.

Scans the single-stage task python files for @register_env("RoboCasa-...") decorators
and emits, per task, a graphs/auto/<slug>/graph.json with a single root skill. Existing
graph folders are not overwritten unless --force.

Usage:
    python3 gen_graphs.py
    python3 gen_graphs.py --tasks-dir /path/to/single_stage --out graphs/auto --force
    python3 gen_graphs.py --only "RoboCasa-Open-Drawer-v0,RoboCasa-Close-Drawer-v0"
"""

import argparse
import json
import re
from pathlib import Path

DEFAULT_TASKS_DIR = Path("/home/truares/文档/maniskill-tidyverse/robocasa_tasks/single_stage")
DEFAULT_OUT = Path(__file__).resolve().parent / "graphs" / "auto"

REGISTER_RE = re.compile(r'@register_env\(\s*"([^"]+)"')


def slugify(task_env: str) -> str:
    """RoboCasa-Pn-P-Counter-To-Sink-v0 -> pnp-counter-to-sink"""
    s = task_env
    if s.startswith("RoboCasa-"):
        s = s[len("RoboCasa-"):]
    if s.endswith("-v0"):
        s = s[:-len("-v0")]
    s = s.replace("Pn-P", "pnp")
    return s.lower()


def scan(tasks_dir: Path) -> list[tuple[str, Path]]:
    """Yield (task_env, source_file) pairs found in the directory."""
    out = []
    for py in sorted(tasks_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text()
        for match in REGISTER_RE.finditer(text):
            out.append((match.group(1), py))
    return out


def write_graph(task_env: str, source: Path, slug: str, out_dir: Path, force: bool) -> bool:
    """Write a graph.json for the task. Returns True if written, False if skipped."""
    graph_dir = out_dir / slug
    graph_path = graph_dir / "graph.json"
    if graph_path.exists() and not force:
        return False

    graph_dir.mkdir(parents=True, exist_ok=True)
    skill_name = f"{slug}-root"

    graph = {
        "task_env": task_env,
        "task_source": str(source),
        "entries": [
            {
                "name": skill_name,
                "description": (
                    f"Solve the {task_env} task end-to-end. The robot must complete "
                    f"the scenario defined by the sim's _check_success() method. "
                    f"Read the task source ({source.name}) and the sim docs before "
                    f"writing code. Use cuRobo-first whole-body planning. Success is "
                    f"reported automatically via GET http://localhost:5500/task/success."
                ),
                "dependencies": [],
            }
        ],
    }

    graph_path.write_text(json.dumps(graph, indent=2))
    # Also create the skill scripts dir so the dev agent has somewhere to write.
    (graph_dir / "skills" / skill_name / "scripts").mkdir(parents=True, exist_ok=True)
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing graph.json files.")
    p.add_argument("--only", default="",
                   help="Comma-separated task_env subset (e.g. 'RoboCasa-Open-Drawer-v0,...').")
    args = p.parse_args()

    if not args.tasks_dir.is_dir():
        print(f"tasks dir not found: {args.tasks_dir}")
        return 1

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    pairs = scan(args.tasks_dir)
    if only:
        pairs = [(t, s) for t, s in pairs if t in only]
        missing = only - {t for t, _ in pairs}
        if missing:
            print(f"WARNING: --only filter has unknown tasks: {sorted(missing)}")

    if not pairs:
        print("no @register_env entries found")
        return 1

    written = skipped = 0
    for task_env, source in pairs:
        slug = slugify(task_env)
        if write_graph(task_env, source, slug, args.out, args.force):
            print(f"  + {slug:40s} ({task_env})")
            written += 1
        else:
            print(f"  = {slug:40s} (already exists, skipped)")
            skipped += 1

    print(f"\nDone. wrote={written} skipped={skipped} (out: {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
