"""OpenClaw + Ollama PoC — minimal harness-loop demo.

Drives the existing `tidybot-poc` OpenClaw agent (configured with
`ollama/llama3.1:8b-ctx32k`) via `openclaw agent --local --json` subprocess
and parses the result. Mirrors opencode_poc/demo_opencode.py in structure.

Usage:
    python demo_openclaw.py --part A             # smoke test (no sim)
    python demo_openclaw.py --part B             # robot_sdk-adjacent task
    python demo_openclaw.py --prompt "..." --agent tidybot-poc

Prereqs:
    1. OpenClaw CLI installed (`openclaw --version` → 2026.4.x)
    2. Ollama running on 11434 with llama3.1:8b-ctx32k pulled
    3. The `tidybot-poc` agent present (`openclaw agents list`)
    4. For Part B: running agent_server on localhost:8080 (optional —
       we only *read* SDK docs, we don't submit code)
"""
import argparse
import json
import os
import subprocess
import sys
import time

DEFAULT_AGENT = "tidybot-poc"
DEFAULT_TIMEOUT = 300

PART_A_PROMPT = (
    "List the files in the current directory with a bash tool call. "
    "Report only the count, nothing else."
)

PART_B_PROMPT = (
    "Using the bash tool, run: `ls graphs/` in the current workspace. "
    "Then pick the first folder in the output, and list its contents with "
    "`ls graphs/<folder>/`. Report in ONE sentence: the name of that folder "
    "and how many entries it contains. Use exactly 2 tool calls."
)


def run_openclaw(agent: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Invoke `openclaw agent --local --json -m <prompt>` and return parsed JSON."""
    cmd = [
        "openclaw",
        "agent",
        "--local",
        "--agent", agent,
        "--json",
        "--timeout", str(timeout),
        "-m", prompt,
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    elapsed = time.time() - t0

    result = {
        "cmd": " ".join(cmd[:5]),
        "elapsed_s": round(elapsed, 2),
        "returncode": proc.returncode,
    }

    # OpenClaw writes the JSON envelope to stderr; stdout is empty.
    raw = proc.stderr or ""
    if proc.returncode != 0:
        result["error"] = raw[-500:] if raw else "no output"
        return result

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        result["error"] = f"JSON parse failed: {e}"
        result["stderr_tail"] = raw[-500:]
        return result

    meta = data.get("meta", {})
    agent_meta = meta.get("agentMeta", {})
    tool_summary = meta.get("toolSummary", {})
    trace = meta.get("executionTrace", {})

    result["assistant_text"] = meta.get("finalAssistantVisibleText") or (
        data.get("payloads", [{}])[0].get("text", "") if data.get("payloads") else ""
    )
    result["stop_reason"] = meta.get("stopReason")
    result["tool_calls"] = tool_summary.get("calls", 0)
    result["tool_failures"] = tool_summary.get("failures", 0)
    result["tools_used"] = tool_summary.get("tools", [])
    result["usage"] = agent_meta.get("usage", {})
    result["model"] = f"{trace.get('winnerProvider') or agent_meta.get('provider', '?')}/"\
                      f"{trace.get('winnerModel') or agent_meta.get('model', '?')}"
    result["runner"] = trace.get("runner", "?")
    result["session_id"] = agent_meta.get("sessionId")
    return result


def print_result(r: dict, verbose: bool = False):
    print(f"  model:      {r.get('model', '?')}")
    print(f"  runner:     {r.get('runner', '?')}")
    print(f"  elapsed:    {r['elapsed_s']}s")
    print(f"  stop_reason: {r.get('stop_reason', '?')}")
    print(f"  tool_calls: {r.get('tool_calls', 0)} ({r.get('tool_failures', 0)} failures)  "
          f"tools={r.get('tools_used', [])}")
    if r.get("assistant_text"):
        text = r["assistant_text"]
        if not verbose and len(text) > 300:
            text = text[:300] + "..."
        print(f"  reply:\n    {text}")
    if r.get("error"):
        print(f"  ERROR: {r['error']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["A", "B"], default="A",
                    help="A = smoke test (no sim needed); B = robot_sdk-adjacent")
    ap.add_argument("--prompt", help="Custom prompt (overrides --part)")
    ap.add_argument("--agent", default=DEFAULT_AGENT)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.prompt:
        prompt = args.prompt
        tag = "custom"
    elif args.part == "A":
        prompt = PART_A_PROMPT
        tag = "Part A — smoke test"
    else:
        prompt = PART_B_PROMPT
        tag = "Part B — robot_sdk docs"

    print(f"[demo_openclaw] {tag}")
    print(f"  agent:      {args.agent}")
    print(f"  prompt:     {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
    print()

    r = run_openclaw(args.agent, prompt, timeout=args.timeout)
    print_result(r, verbose=args.verbose)

    success = (
        r.get("returncode") == 0
        and r.get("stop_reason") == "stop"
        and r.get("tool_calls", 0) >= 1
        and r.get("tool_failures", 0) == 0
    )
    print()
    print(f"[demo_openclaw] {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
