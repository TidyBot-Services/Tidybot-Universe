"""opencode + Ollama/Gemini PoC — minimal agent-loop demo.

Runs a real dev-style task end-to-end against a running sim + agent_server,
to prove opencode can replace the Claude Code harness.

Usage:
    python demo_opencode.py --provider ollama --model qwen2.5-coder:7b
    python demo_opencode.py --provider google --model gemini-1.5-flash

Prereqs:
    1. `opencode serve --port 4096` running (reads opencode.json in CWD)
    2. For ollama: `OLLAMA_NUM_CTX=32768 ollama serve` + a tool-capable model
    3. For gemini: `export GEMINI_API_KEY=...`
    4. Sim + agent_server running if you want Part B (real task) to succeed
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

OPENCODE = os.environ.get("OPENCODE_URL", "http://localhost:4096")
AGENT_SERVER = os.environ.get("AGENT_SERVER", "http://localhost:8080")

DEV_SYSTEM_PROMPT = """You are a robot skill developer for the TidyBot project.

You have these tools: Read, Write, Edit, Bash, Grep, Glob.

Task workflow:
1. Fetch the SDK reference from the running agent_server with Bash:
   curl -s {AGENT_SERVER}/code/sdk/markdown | head -200
2. Write a small Python file that uses `robot_sdk` (arm, gripper, base).
3. Submit it for execution with Bash:
   curl -X POST {AGENT_SERVER}/code/submit -H 'Content-Type: application/json' \\
     -d '{{"code": <json-encoded-code>, "holder": "opencode-demo"}}'
4. Poll GET {AGENT_SERVER}/code/jobs/<job_id> until status is done/error.
5. Report success/failure with the job output.

Keep it short. Use at most 5 tool calls total.
""".replace("{AGENT_SERVER}", AGENT_SERVER)

PART_A_PROMPT = "Say hello in one sentence, then call the Read tool on README.md if it exists, and summarize what you see."

PART_B_PROMPT = """Write a minimal robot skill that moves the arm forward 10cm and back, then submit it and report the result.

Steps:
1. Bash: curl -s {AGENT_SERVER}/code/sdk/markdown | head -100   — to see arm API
2. Write a file opencode_poc/generated_skill.py using robot_sdk.arm move_delta
3. Bash: submit it via /code/submit (see system prompt), capture job_id
4. Bash: poll /code/jobs/<job_id> once after 3 seconds, print status
""".replace("{AGENT_SERVER}", AGENT_SERVER)


def create_session(title: str) -> str:
    r = requests.post(f"{OPENCODE}/session", json={"title": title}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def send_prompt(session_id: str, prompt: str, provider: str, model: str, system: str):
    payload = {
        "providerID": provider,
        "modelID": model,
        "system": system,
        "parts": [{"type": "text", "text": prompt}],
    }
    r = requests.post(
        f"{OPENCODE}/session/{session_id}/message",
        json=payload,
        timeout=600,
    )
    r.raise_for_status()
    return r.json()


def stream_events(session_id: str, stop_on_idle: float = 30.0):
    """Subscribe to SSE and print assistant text + tool calls for this session."""
    url = f"{OPENCODE}/event"
    print(f"\n── streaming events from {url} ──")
    last_event = time.time()
    with requests.get(url, stream=True, timeout=None) as r:
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                if time.time() - last_event > stop_on_idle:
                    print("\n[idle timeout — stopping stream]")
                    return
                continue
            if raw.startswith("data:"):
                try:
                    evt = json.loads(raw[5:].strip())
                except Exception:
                    continue
                last_event = time.time()
                handle_event(evt, session_id)
                if evt.get("type") == "session.idle" and \
                        evt.get("properties", {}).get("sessionID") == session_id:
                    print("\n[session idle — agent turn complete]")
                    return


def handle_event(evt: dict, session_id: str):
    et = evt.get("type", "")
    props = evt.get("properties", {})
    if props.get("sessionID") not in (session_id, None):
        return
    if et == "message.part.updated":
        part = props.get("part", {})
        pt = part.get("type")
        if pt == "text":
            sys.stdout.write(part.get("text", ""))
            sys.stdout.flush()
        elif pt == "tool":
            state = part.get("state", {}).get("status")
            tool = part.get("tool", "?")
            if state == "pending":
                print(f"\n🔧 tool call: {tool}")
            elif state == "running":
                args = part.get("state", {}).get("input", {})
                print(f"   args: {json.dumps(args)[:200]}")
            elif state == "completed":
                out = part.get("state", {}).get("output", "")
                print(f"   ✓ result: {str(out)[:200]}")
            elif state == "error":
                print(f"   ✗ error: {part.get('state', {}).get('error')}")
    elif et == "message.updated":
        info = props.get("info", {})
        if info.get("role") == "assistant" and info.get("time", {}).get("completed"):
            print()  # newline after message


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ollama", choices=["ollama"])
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--part", default="A", choices=["A", "B"],
                    help="A=harness smoke test, B=real robot task")
    args = ap.parse_args()

    prompt = PART_A_PROMPT if args.part == "A" else PART_B_PROMPT
    system = "" if args.part == "A" else DEV_SYSTEM_PROMPT

    print(f"opencode: {OPENCODE}")
    print(f"provider: {args.provider}  model: {args.model}  part: {args.part}")

    sid = create_session(f"poc-{args.part}-{args.provider}")
    print(f"session: {sid}")

    # SSE first (in a thread would be cleaner; simple sequential works since
    # the POST returns after completion, but we want to see the stream live —
    # so we fire the prompt in a background thread).
    import threading
    result = {}

    def fire():
        try:
            result["resp"] = send_prompt(sid, prompt, args.provider, args.model, system)
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=fire, daemon=True)
    t.start()
    stream_events(sid, stop_on_idle=60.0)
    t.join(timeout=5)

    if "error" in result:
        print(f"\nERROR: {result['error']}")
    else:
        print("\n── done ──")


if __name__ == "__main__":
    main()
