"""OpenClaw + Gemini — full dev-agent pipeline against live sim + agent_server.

Drives the `tidybot-gemini` OpenClaw agent through the complete loop a real
dev agent would do: read SKILL.md + SDK + task info → write main.py →
submit via /code/submit → poll jobs → verify via /task/success.

This is NOT a benchmark (single run, on purpose — Gemini costs money).

Prereqs (verify before running):
    curl -sf http://localhost:5500/task/success
    curl -sf http://localhost:8080/state
    openclaw agents list | grep tidybot-gemini

Run:
    python3 full_pipeline.py
"""
import argparse
import json
import os
import sys
import time
import urllib.request

from demo_openclaw import run_openclaw, print_result

WORKSPACE = "/home/truares/文档/Tidybot-Universe/skill-agent-setup/claude-code"
GRAPH = "counter-to-sink-demo"
SKILL = "pnp-counter-to-sink"
AGENT_SERVER = "http://localhost:8080"
SIM_API = "http://localhost:5500"

PROMPT = f"""You are a robot skill dev agent for the TidyBot project. Implement the skill
`{SKILL}` and verify it works end-to-end against the live sim.

## Context
- Skill dir: `graphs/{GRAPH}/skills/{SKILL}/`
- Agent server (code exec API): {AGENT_SERVER}
- Sim API (task state): {SIM_API}
- Task env: `RoboCasa-Pn-P-Counter-To-Sink-v0`
- Language description (from sim): use `curl -sf {SIM_API}/task/info` to read the
  natural-language task description — this tells you WHICH object to pick.

## Tools you must use in order

1. **Read `graphs/{GRAPH}/skills/{SKILL}/SKILL.md`** — preconditions, postconditions,
   success criteria, critical API notes.

2. **Fetch the SDK reference**:
   `curl -sf {AGENT_SERVER}/code/sdk/markdown > /tmp/sdk.html`
   Then `cat /tmp/sdk.html | grep -E '^<h|<p><strong' | head -80` — skim the module
   structure. Or grep specific modules:
   `grep -A 3 'robot_sdk.arm' /tmp/sdk.html | head -40`

3. **Fetch task info**:
   `curl -sf {SIM_API}/task/info` — returns JSON with `task`, `lang`, etc.

4. **Write `graphs/{GRAPH}/skills/{SKILL}/scripts/main.py`** using `robot_sdk`:
   - Import modules as: `from robot_sdk import arm, base, gripper, sensors, wb, http`
     (these are MODULES, not classes — don't instantiate them)
   - Use `sensors.find_objects()` to locate objects (returns list of dicts with
     name, position, fixture_context)
   - Use `wb.move_to_pose(x, y, z, quat=...)` for whole-body moves (base + arm)
   - Use `arm.move_delta(dx, dy, dz)` for small arm adjustments
   - Use `gripper.open()`, `gripper.close(force=255)`, `gripper.grasp(force=255)`
   - Use `base.move_to(x, y, theta)` or wb for navigation
   - Use `http.get(url)` for HTTP requests (NOT urllib — sandboxed)
   - DO NOT call `arm.go_home()` at the end — server auto-runs go_home on lease release

5. **Submit the code** via
   `curl -s -X POST {AGENT_SERVER}/code/submit -H 'Content-Type: application/json' \\
      -d '{{"code": <JSON_STRING>, "holder": "gemini-poc"}}'`
   JSON-encode the file content properly (use python3 -c 'import json; ...').
   The response has `job_id`.

6. **Poll** `{AGENT_SERVER}/code/jobs/<job_id>` every 5s (sleep in between) until
   `status` is `completed` or `failed`. Show the final result's stdout, stderr, error.

7. **Check success**: `curl -sf {SIM_API}/task/success` — JSON with `success: bool`.

## Constraints
- Keep main.py under 200 lines.
- Under 12 tool calls total.
- If submit fails, look at stderr/error and either fix or report what went wrong.
- Final output: ONE paragraph summarizing what you did and whether the task succeeded.
"""


def preflight():
    for url, label in [
        (f"{AGENT_SERVER}/state", "agent_server"),
        (f"{SIM_API}/task/info", "sim_api"),
    ]:
        try:
            urllib.request.urlopen(url, timeout=5).read()
            print(f"  [OK]   {label}: {url}")
        except Exception as e:
            print(f"  [FAIL] {label}: {url} — {e}")
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="tidybot-gemini")
    ap.add_argument("--timeout", type=int, default=900,
                    help="Per-turn timeout in seconds (15 min default)")
    args = ap.parse_args()

    print("[full_pipeline] preflight:")
    if not preflight():
        print("ABORT — services not up")
        return 2

    print(f"[full_pipeline] agent={args.agent}  timeout={args.timeout}s")
    print("  prompt length:", len(PROMPT), "chars")
    print()
    t0 = time.time()
    r = run_openclaw(args.agent, PROMPT, timeout=args.timeout)
    elapsed = time.time() - t0
    print_result(r, verbose=True)

    # Post-run: verify task state
    print()
    try:
        success = json.loads(urllib.request.urlopen(f"{SIM_API}/task/success", timeout=5).read())
        print(f"[full_pipeline] POST-RUN sim task_success: {success}")
    except Exception as e:
        print(f"[full_pipeline] POST-RUN task_success check FAILED: {e}")

    usage = r.get("usage", {})
    if usage:
        # Gemini 2.5 Flash pricing as of 2026-04: $0.30/M input, $2.50/M output
        cost = usage.get("input", 0) * 0.30/1e6 + usage.get("output", 0) * 2.50/1e6
        print(f"[full_pipeline] tokens: {usage}  rough_cost: ${cost:.4f}")
    print(f"[full_pipeline] total elapsed: {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
