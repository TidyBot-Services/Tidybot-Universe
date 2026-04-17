"""Harness-only benchmark: opencode vs Claude Code, holding the model fixed.

Both harnesses run the SAME model (Claude Haiku 4.5) and SAME prompt.
Any difference = harness-layer overhead.

Prereqs:
- export ANTHROPIC_API_KEY=...
- opencode serve --port 4096 (with anthropic provider configured)
- pip install claude-agent-sdk requests psutil

Run:
    python3 bench_harness.py --runs 10
"""
import argparse
import json
import os
import statistics
import subprocess
import threading
import time
from pathlib import Path

import requests

MODEL = "claude-haiku-4-5-20251001"
PROMPT = (
    "Read README.md and summarize it in one short sentence."
)

OPENCODE = "http://localhost:4096"


# ============ opencode side ============

def opencode_single_run(tag: str) -> dict:
    """One full session. Returns timing dict."""
    t_create = time.time()
    sid = requests.post(
        f"{OPENCODE}/session",
        json={"title": f"bench-{tag}"},
        timeout=30,
    ).json()["id"]
    t_created = time.time()

    first_event = [None]
    first_token = [None]
    first_tool_start = [None]
    first_tool_running = [None]
    done = threading.Event()

    def stream():
        with requests.get(f"{OPENCODE}/event", stream=True, timeout=None) as r:
            for raw in r.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                try:
                    evt = json.loads(raw[5:].strip())
                except Exception:
                    continue
                props = evt.get("properties", {})
                if props.get("sessionID") != sid:
                    continue
                now = time.time()
                if first_event[0] is None:
                    first_event[0] = now
                et = evt.get("type")
                if et == "message.part.updated":
                    part = props.get("part", {})
                    pt = part.get("type")
                    if pt == "text" and first_token[0] is None:
                        first_token[0] = now
                    elif pt == "tool":
                        st = part.get("state", {}).get("status")
                        if st == "pending" and first_tool_start[0] is None:
                            first_tool_start[0] = now
                        elif st == "running" and first_tool_running[0] is None:
                            first_tool_running[0] = now
                elif et == "session.idle":
                    done.set()
                    return

    t = threading.Thread(target=stream, daemon=True)
    t.start()

    t_prompt = time.time()
    requests.post(
        f"{OPENCODE}/session/{sid}/message",
        json={
            "providerID": "anthropic",
            "modelID": MODEL,
            "parts": [{"type": "text", "text": PROMPT}],
        },
        timeout=600,
    )
    done.wait(timeout=300)
    t_done = time.time()

    return {
        "cold_start_ms": (first_event[0] - t_create) * 1000 if first_event[0] else None,
        "ttft_ms": (first_token[0] - t_prompt) * 1000 if first_token[0] else None,
        "tool_dispatch_ms": (
            (first_tool_running[0] - first_tool_start[0]) * 1000
            if first_tool_running[0] and first_tool_start[0]
            else None
        ),
        "e2e_s": t_done - t_prompt,
        "session_id": sid,
    }


def opencode_resume(session_id: str) -> float:
    """How long to fetch a prior session's history."""
    t0 = time.time()
    requests.get(f"{OPENCODE}/session/{session_id}/message", timeout=30)
    return (time.time() - t0) * 1000


def opencode_concurrent(n=5) -> float:
    """Fire n sessions in parallel, wait for all to finish."""
    t0 = time.time()
    threads = []
    for i in range(n):
        t = threading.Thread(target=opencode_single_run, args=(f"concurrent-{i}",))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return time.time() - t0


# ============ Claude Code side ============

def claude_code_single_run(tag: str) -> dict:
    """Spawn claude CLI via subprocess, stream stream-json output, measure timings."""
    t_spawn = time.time()
    env = dict(os.environ, ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY", ""))
    cmd = [
        "claude",
        "--model", MODEL,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        PROMPT,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True
    )
    first_event = None
    first_token = None
    first_tool = None
    for line in proc.stdout:
        now = time.time()
        try:
            evt = json.loads(line)
        except Exception:
            continue
        if first_event is None:
            first_event = now
        t = evt.get("type", "")
        if t == "assistant":
            for blk in evt.get("message", {}).get("content", []):
                if blk.get("type") == "text" and first_token is None:
                    first_token = now
                if blk.get("type") == "tool_use" and first_tool is None:
                    first_tool = now
    proc.wait(timeout=300)
    t_done = time.time()
    return {
        "cold_start_ms": (first_event - t_spawn) * 1000 if first_event else None,
        "ttft_ms": (first_token - t_spawn) * 1000 if first_token else None,
        "tool_dispatch_ms": (first_tool - t_spawn) * 1000 if first_tool else None,
        "e2e_s": t_done - t_spawn,
    }


def claude_code_concurrent(n=5) -> float:
    t0 = time.time()
    procs = []
    for i in range(n):
        t = threading.Thread(target=claude_code_single_run, args=(f"concurrent-{i}",))
        t.start()
        procs.append(t)
    for t in procs:
        t.join()
    return time.time() - t0


# ============ Memory monitoring ============

def measure_rss(proc_name: str) -> float:
    """Return peak RSS in MB for any process matching name."""
    try:
        import psutil
        total = 0
        for p in psutil.process_iter(["name", "memory_info"]):
            if proc_name in (p.info["name"] or ""):
                total += p.info["memory_info"].rss
        return total / 1024 / 1024
    except Exception:
        return -1


# ============ Reporter ============

def summarize(name: str, runs: list[dict]):
    keys = ["cold_start_ms", "ttft_ms", "tool_dispatch_ms", "e2e_s"]
    print(f"\n=== {name} ({len(runs)} runs) ===")
    for k in keys:
        vals = [r[k] for r in runs if r.get(k) is not None]
        if not vals:
            print(f"  {k:20s}: n/a")
            continue
        mean = statistics.mean(vals)
        stdev = statistics.stdev(vals) if len(vals) > 1 else 0
        p95 = sorted(vals)[max(0, int(len(vals) * 0.95) - 1)]
        print(f"  {k:20s}: mean={mean:8.1f}  p95={p95:8.1f}  σ={stdev:8.1f}  n={len(vals)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--concurrent", type=int, default=5)
    ap.add_argument("--side", default="both", choices=["both", "opencode", "claude"])
    args = ap.parse_args()

    assert os.environ.get("ANTHROPIC_API_KEY"), "set ANTHROPIC_API_KEY"
    out = {"runs": args.runs, "model": MODEL, "timestamp": time.time()}

    if args.side in ("both", "opencode"):
        print(f"▶ opencode — {args.runs} serial runs …")
        runs = [opencode_single_run(f"s{i}") for i in range(args.runs)]
        summarize("opencode (serial)", runs)
        print(f"\n▶ opencode — {args.concurrent} concurrent …")
        t = opencode_concurrent(args.concurrent)
        print(f"  total wallclock: {t:.2f}s")
        print(f"\n▶ opencode — resume 1 prior session …")
        rt = opencode_resume(runs[-1]["session_id"])
        print(f"  resume: {rt:.1f} ms")
        print(f"\n▶ opencode — peak RSS:")
        print(f"  {measure_rss('opencode'):.1f} MB")
        out["opencode"] = {"serial": runs, "concurrent_s": t, "resume_ms": rt}

    if args.side in ("both", "claude"):
        print(f"\n▶ claude-code — {args.runs} serial runs …")
        runs = [claude_code_single_run(f"s{i}") for i in range(args.runs)]
        summarize("claude-code (serial)", runs)
        print(f"\n▶ claude-code — {args.concurrent} concurrent …")
        t = claude_code_concurrent(args.concurrent)
        print(f"  total wallclock: {t:.2f}s")
        print(f"\n▶ claude-code — peak RSS:")
        print(f"  {measure_rss('claude'):.1f} MB")
        out["claude_code"] = {"serial": runs, "concurrent_s": t}

    Path("/tmp/bench_harness.json").write_text(json.dumps(out, indent=2, default=str))
    print("\nRaw data: /tmp/bench_harness.json")


if __name__ == "__main__":
    main()
