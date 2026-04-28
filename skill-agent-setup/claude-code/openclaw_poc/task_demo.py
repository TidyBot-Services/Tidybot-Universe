"""OpenClaw task demo — run a real skill-writing task through the harness.

Gives the `tidybot-poc` OpenClaw agent a realistic dev-agent prompt (read
SKILL.md, write scripts/main.py) and captures the result. Use this to see
what the Ollama 8B harness can actually produce for a real robot task.

Usage:
    python task_demo.py --graph counter-to-sink-demo --skill pnp-counter-to-sink
    python task_demo.py --graph counter-to-sink-demo --skill pnp-counter-to-sink --backup

The `--backup` flag copies the existing main.py to a timestamped backup in
the same directory before running (paranoid mode — harness should not wipe
your baseline).
"""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from demo_openclaw import run_openclaw, print_result

WORKSPACE = Path(__file__).resolve().parents[1]  # claude-code/

PROMPT_TEMPLATE = """Task: implement the `{skill}` robot skill.

1. Read `graphs/{graph}/skills/{skill}/SKILL.md` — it contains the full spec
   (preconditions, postconditions, success criteria, API notes).
2. Fetch the robot SDK reference if the agent_server is running:
   `curl -sf http://localhost:8080/code/sdk/markdown | head -120`
   (If the server is down, skip this step and write code using the SKILL.md
   notes + your prior knowledge of `robot_sdk.arm / base / gripper / sensors`.)
3. Write the implementation to
   `graphs/{graph}/skills/{skill}/scripts/main.py`
   using robot_sdk. Be concrete — detect, grasp, navigate, release, verify.
4. Stop. Do NOT try to submit or execute the code (sim may be offline).

Keep main.py under 250 lines. Be concise.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="counter-to-sink-demo")
    ap.add_argument("--skill", default="pnp-counter-to-sink")
    ap.add_argument("--agent", default="tidybot-poc")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Per-turn timeout in seconds (default 600)")
    ap.add_argument("--backup", action="store_true",
                    help="Back up existing main.py before running")
    args = ap.parse_args()

    skill_dir = WORKSPACE / "graphs" / args.graph / "skills" / args.skill
    main_py = skill_dir / "scripts" / "main.py"

    if not (skill_dir / "SKILL.md").exists():
        print(f"ERROR: SKILL.md not found at {skill_dir}/SKILL.md")
        return 1

    baseline_lines = None
    if main_py.exists():
        baseline_lines = len(main_py.read_text().splitlines())
        if args.backup:
            ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = main_py.with_suffix(f".py.bak-{ts}")
            shutil.copy2(main_py, bak)
            print(f"  backed up → {bak}")

    prompt = PROMPT_TEMPLATE.format(graph=args.graph, skill=args.skill)
    print(f"[task_demo] graph={args.graph}  skill={args.skill}  agent={args.agent}")
    if baseline_lines is not None:
        print(f"  existing main.py: {baseline_lines} lines (baseline)")
    print(f"  prompt: {prompt.splitlines()[0]}")
    print()

    t0 = time.time()
    r = run_openclaw(args.agent, prompt, timeout=args.timeout)
    elapsed = time.time() - t0

    print_result(r, verbose=False)

    print()
    if main_py.exists():
        new_lines = len(main_py.read_text().splitlines())
        delta = new_lines - (baseline_lines or 0)
        print(f"[task_demo] main.py after run: {new_lines} lines "
              f"(delta vs baseline: {delta:+d})")
    else:
        print("[task_demo] main.py NOT WRITTEN — agent failed to use write tool")

    print(f"[task_demo] total elapsed: {elapsed:.1f}s")

    success = (
        r.get("returncode") == 0
        and r.get("stop_reason") == "stop"
        and r.get("tool_failures", 0) == 0
        and main_py.exists()
    )
    print(f"[task_demo] {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
