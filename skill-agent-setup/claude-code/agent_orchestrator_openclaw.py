"""OpenClaw harness backend for agent_orchestrator.py.

Drop-in replacement for `_run_agent_sdk` / `_consume_sdk_response` / `inject_hint` /
`stop_agent` / `kill_agent`, driving the `openclaw agent --local --json` CLI as a
subprocess instead of `claude-agent-sdk`'s `ClaudeSDKClient`.

Design (Path A, see openclaw_poc/README.md):
  - One subprocess per agent run (spawned by _run_agent_openclaw)
  - Live streaming to dashboard via JSONL file tail (not stdout streaming)
  - Session resume via `--session-id <id>` CLI flag
  - inject_hint → SIGINT current subprocess, spawn new one with hint as -m
  - Two OpenClaw agents required (user creates once):
      tidybot-dev        — dev role
      tidybot-evaluator  — evaluator role

Activated via env: `HARNESS=openclaw`
Default agent id mapping can be overridden via:
  OPENCLAW_DEV_AGENT=<id>   OPENCLAW_EVAL_AGENT=<id>
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Optional

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))

# agent_type → OpenClaw agent id. Both agents must exist in openclaw.json:
#   openclaw agents add tidybot-dev       --workspace <WS> --model <model>
#   openclaw agents add tidybot-evaluator --workspace <WS> --model <model>
AGENT_TYPE_MAP = {
    "dev":       os.environ.get("OPENCLAW_DEV_AGENT",  "tidybot-dev"),
    "evaluator": os.environ.get("OPENCLAW_EVAL_AGENT", "tidybot-evaluator"),
    "test":      os.environ.get("OPENCLAW_TEST_AGENT", "tidybot-dev"),  # tests piggyback on dev
}

# Provider pricing ($/M tokens) for rough cost estimation.
# input, output, cacheRead. Extend as needed.
PROVIDER_PRICING: dict[tuple[str, Optional[str]], tuple[float, float, float]] = {
    ("google", "gemini-2.5-flash"):         (0.30,  2.50,  0.075),
    ("google", "gemini-2.5-pro"):           (1.25, 10.00,  0.3125),
    ("anthropic", "claude-sonnet-4-6"):     (3.00, 15.00,  0.30),
    ("anthropic", "claude-opus-4-6"):       (15.00, 75.00, 1.50),
    ("ollama", None):                       (0.0,   0.0,   0.0),
}

DEFAULT_TURN_TIMEOUT_S = 900  # mirrors SDK_IDLE_TIMEOUT_S


def resolve_agent_id(agent_type: str) -> str:
    aid = AGENT_TYPE_MAP.get(agent_type)
    if not aid:
        raise ValueError(f"no OpenClaw agent mapped for agent_type={agent_type!r}")
    return aid


def _sessions_dir(agent_id: str) -> Path:
    return OPENCLAW_HOME / "agents" / agent_id / "sessions"


def _session_file(agent_id: str, session_id: str) -> Path:
    return _sessions_dir(agent_id) / f"{session_id}.jsonl"


def _estimate_cost(provider: str, model: str, usage: dict) -> float:
    key: tuple[str, Optional[str]] = (provider or "", model or "")
    if key not in PROVIDER_PRICING:
        key = (provider or "", None)  # wildcard fallback
    if key not in PROVIDER_PRICING:
        return 0.0
    pi, po, pc = PROVIDER_PRICING[key]
    return (usage.get("input", 0) * pi
            + usage.get("output", 0) * po
            + usage.get("cacheRead", 0) * pc) / 1e6


def _parse_final_envelope(stderr_raw: str) -> Optional[dict]:
    """`openclaw agent --local --json` emits one JSON doc on stderr at end.

    Logs may precede it, so try parsing whole output first, then scan for brace.
    """
    if not stderr_raw:
        return None
    try:
        data = json.loads(stderr_raw)
        return data
    except json.JSONDecodeError:
        start = stderr_raw.find('{\n')
        if start < 0:
            start = stderr_raw.find('{')
        if start < 0:
            return None
        try:
            return json.loads(stderr_raw[start:])
        except Exception:
            return None


async def _wait_for_session_file(agent_id: str, known_session_id: Optional[str],
                                  before_snapshot: set[Path],
                                  timeout_s: float = 20.0) -> Optional[Path]:
    """Wait for the session JSONL file to appear.

    If known_session_id is given, wait for that specific file.
    Otherwise wait for a NEW file (not in before_snapshot) to show up.
    """
    sdir = _sessions_dir(agent_id)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if known_session_id:
            f = _session_file(agent_id, known_session_id)
            if f.exists():
                return f
        elif sdir.exists():
            now_files = set(sdir.glob("*.jsonl"))
            new = now_files - before_snapshot
            if new:
                # Pick the most recently modified new file
                return max(new, key=lambda p: p.stat().st_mtime)
        await asyncio.sleep(0.5)
    return None


async def _tail_session_jsonl(state, agent_id: str, session_file: Path,
                               start_offset: int = 0):
    """Tail the session JSONL file, broadcast assistant text + tool calls.

    Runs concurrently with the subprocess. Exits when state.status leaves
    {starting, running, paused} (set by parent after subprocess completes).
    """
    from agent_orchestrator import (
        ws_broadcast_agent_msg, ws_broadcast_status,
        _update_entry,
    )

    # Open file, seek past pre-existing content (only stream NEW entries)
    try:
        f = open(session_file, "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return
    try:
        f.seek(start_offset)
        while state.status in ("starting", "running", "paused"):
            line = f.readline()
            if not line:
                await asyncio.sleep(0.5)
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") != "message":
                continue
            msg = entry.get("message", {})
            role = msg.get("role")
            if role == "assistant":
                # Capture session_id early if we didn't have it
                sid = entry.get("sessionId") or entry.get("session_id")
                if sid and not state.session_id:
                    state.session_id = sid
                    _update_entry(state.skill, {"session_id": sid})

                for c in msg.get("content", []) or []:
                    ct = c.get("type")
                    if ct == "text":
                        text = (c.get("text") or "").strip()
                        if text:
                            state.log.append({"text": text, "role": "agent"})
                            if len(state.log) > 200:
                                state.log[:] = state.log[-200:]
                            await ws_broadcast_agent_msg(
                                state.skill, text, state.agent_type
                            )
                    elif ct == "toolCall":
                        tool_name = c.get("name", "?")
                        await ws_broadcast_status(
                            state.skill, state.agent_id,
                            "running", f"Using {tool_name}..."
                        )
    finally:
        try: f.close()
        except Exception: pass


async def _run_agent_openclaw(state, prompt: str):
    """Spawn `openclaw agent --local --json` subprocess, tail output, finalize."""
    from agent_orchestrator import (
        ws_broadcast_agent_msg, ws_broadcast_status,
        _update_entry, _resolve_completion,
        _get_system_prompt, WORKSPACE_DIR,
    )

    agent_id = resolve_agent_id(state.agent_type)
    resume_id = state.session_id

    # On first turn, prepend _get_system_prompt() content so the agent has
    # full dev/evaluator context. On resume, session transcript already has it.
    if not resume_id:
        sys_ctx = _get_system_prompt(
            state.agent_type, state.skill,
            agent_server_url=getattr(state, "_agent_server_url", "") or "",
        ).format(skill_name=state.skill)
        full_prompt = f"{sys_ctx}\n\n--- USER TASK ---\n{prompt}"
    else:
        full_prompt = prompt

    cmd = [
        "openclaw", "agent",
        "--local",
        "--agent", agent_id,
        "--json",
        "--timeout", str(DEFAULT_TURN_TIMEOUT_S),
        "-m", full_prompt,
    ]
    if resume_id:
        cmd[-2:-2] = ["--session-id", resume_id]
        print(f"[OC] {state.skill}: RESUME {agent_id} session {resume_id}")
    else:
        print(f"[OC] {state.skill}: NEW {agent_id} session")

    # Snapshot pre-existing session files so we can detect a new one
    sdir = _sessions_dir(agent_id)
    before_snapshot = set(sdir.glob("*.jsonl")) if sdir.exists() else set()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE_DIR),
    )
    state.proc = proc
    state.status = "running"
    await ws_broadcast_status(
        state.skill, state.agent_id, "running",
        "Resuming..." if resume_id else "Working...",
    )

    # Launch file-tail task (may take a moment to find the session file)
    async def _tail_wrapper():
        sf = await _wait_for_session_file(agent_id, resume_id, before_snapshot)
        if sf is None:
            print(f"[OC] {state.skill}: session file never appeared — dashboard tail disabled")
            return
        # Seek to end on new sessions; for resume, start from current EOF too
        # (we want NEW deltas, not re-broadcasting old history)
        start = sf.stat().st_size
        await _tail_session_jsonl(state, agent_id, sf, start_offset=start)

    tail_task = asyncio.create_task(_tail_wrapper())

    try:
        stdout_bytes, stderr_bytes = await proc.communicate()
    except asyncio.CancelledError:
        # parent cancelled us — kill subprocess, re-raise
        if proc.returncode is None:
            try: proc.send_signal(signal.SIGINT)
            except Exception: pass
            try: await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                try: proc.kill()
                except Exception: pass
        raise
    finally:
        tail_task.cancel()
        try: await tail_task
        except asyncio.CancelledError: pass

    stderr_raw = stderr_bytes.decode(errors="replace")
    envelope = _parse_final_envelope(stderr_raw)
    if not envelope:
        err_tail = stderr_raw[-500:] if stderr_raw else "(no stderr)"
        state.status = "error"
        state.log.append({"text": f"ERROR: no JSON envelope. tail: {err_tail}",
                          "role": "agent"})
        await ws_broadcast_status(state.skill, state.agent_id, "error", "no envelope")
        await ws_broadcast_agent_msg(
            state.skill, f"OpenClaw subprocess failed: {err_tail}", state.agent_type)
        return

    meta = envelope.get("meta", {})
    agent_meta = meta.get("agentMeta", {}) or {}
    tool_sum = meta.get("toolSummary", {}) or {}
    usage = agent_meta.get("usage", {}) or {}
    stop_reason = meta.get("stopReason") or meta.get("completion", {}).get("stopReason")
    provider = agent_meta.get("provider") or ""
    model = agent_meta.get("model") or ""

    # Persist session_id (may have been set earlier by tail, but ensure it's here)
    sess_id = agent_meta.get("sessionId")
    if sess_id and sess_id != state.session_id:
        state.session_id = sess_id
        _update_entry(state.skill, {"session_id": sess_id})

    # Final summary message (mirrors Claude SDK's "Done — N turns, $X" message)
    cost = _estimate_cost(provider, model, usage)
    cost_str = f"${cost:.4f}" if cost > 0 else "free"
    done_msg = (
        f"Done — {tool_sum.get('calls', 0)} tool calls "
        f"({tool_sum.get('failures', 0)} failures), "
        f"tokens in={usage.get('input', 0)} out={usage.get('output', 0)}, "
        f"cost ≈ {cost_str}, stop={stop_reason}, model={provider}/{model}"
    )
    state.log.append({"text": done_msg, "role": "agent"})
    await ws_broadcast_agent_msg(state.skill, done_msg, state.agent_type)
    print(f"[OC] {state.skill}: {done_msg}")

    # Let the rest of the orchestrator state machine run
    await _resolve_completion(state)


async def _inject_hint_openclaw(state, text: str):
    """Interrupt current subprocess (if running) and restart with hint as new prompt.

    Uses the same session id so conversation context is preserved.
    """
    from agent_orchestrator import ws_broadcast_status

    proc = state.proc
    if proc is not None and proc.returncode is None:
        print(f"[OC inject] {state.skill}: SIGINT current subprocess")
        try:
            proc.send_signal(signal.SIGINT)
        except Exception as e:
            print(f"[OC inject] SIGINT failed: {e}")
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            print(f"[OC inject] subprocess didn't exit, SIGKILL")
            try: proc.kill()
            except Exception: pass
            try: await proc.wait()
            except Exception: pass

    state.status = "running"
    await ws_broadcast_status(state.skill, state.agent_id, "writing", "Resumed with hint")

    # Start a new subprocess turn with the hint as the user message
    async def _hint_turn():
        await _run_agent_openclaw(state, text)
    state.task = asyncio.create_task(_hint_turn())


async def _stop_agent_openclaw(state):
    """SIGINT the subprocess; session stays resumable."""
    proc = state.proc
    if proc is not None and proc.returncode is None:
        try: proc.send_signal(signal.SIGINT)
        except Exception as e:
            print(f"[OC stop] SIGINT failed: {e}")
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try: proc.kill()
            except Exception: pass
            try: await proc.wait()
            except Exception: pass


async def _kill_agent_openclaw(state):
    """Hard-kill subprocess. Called from orchestrator kill_agent()."""
    proc = state.proc
    if proc is not None and proc.returncode is None:
        try: proc.kill()
        except Exception: pass
        try: await proc.wait()
        except Exception: pass
