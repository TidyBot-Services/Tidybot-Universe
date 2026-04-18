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

    Logs may precede/surround it. Strategy: find a '{\\n  "payloads"' or
    '{\\n  "meta"' marker, then parse balanced braces forward.
    """
    if not stderr_raw:
        return None
    # Try the whole output first (common case — no interleaved logs)
    try:
        return json.loads(stderr_raw)
    except json.JSONDecodeError:
        pass
    # Find likely start of envelope. OpenClaw always emits `{` at column 0.
    for marker in ('{\n  "payloads"', '{\n  "meta"', '{\n  "result"', '{\n'):
        idx = stderr_raw.find(marker)
        if idx < 0:
            continue
        # Try parsing from idx to end
        try:
            return json.loads(stderr_raw[idx:])
        except json.JSONDecodeError:
            # Balanced-brace scan
            depth = 0
            in_str = False
            esc = False
            for i in range(idx, len(stderr_raw)):
                ch = stderr_raw[i]
                if esc:
                    esc = False; continue
                if ch == '\\':
                    esc = True; continue
                if ch == '"':
                    in_str = not in_str; continue
                if in_str: continue
                if ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(stderr_raw[idx:i+1])
                        except Exception:
                            break
    return None


def _count_session_tools_from_offset(session_file: Path, start_offset: int) -> tuple[int, int, list[str]]:
    """Count tool calls in the JSONL file from a given offset to end.

    Returns (calls, failures, unique_tools).
    """
    calls = 0
    failures = 0
    tools: set[str] = set()
    try:
        with open(session_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start_offset)
            for line in f:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "message":
                    continue
                m = entry.get("message", {})
                role = m.get("role")
                if role == "assistant":
                    for c in m.get("content", []) or []:
                        if c.get("type") == "toolCall":
                            calls += 1
                            if c.get("name"):
                                tools.add(c["name"])
                elif role == "toolResult":
                    if m.get("isError"):
                        failures += 1
    except Exception:
        pass
    return calls, failures, sorted(tools)


async def _wait_for_session_file(agent_id: str, known_session_id: Optional[str],
                                  before_snapshot: set[Path],
                                  timeout_s: float = 20.0) -> Optional[Path]:
    """Wait for the session JSONL file to appear / be touched.

    Lookup order:
      1. known_session_id is given → wait for that specific file
      2. A NEW file (not in before_snapshot) appears → pick it
      3. Fallback: an existing file gets its mtime bumped past our spawn timestamp
         (OpenClaw reuses a single session per agent in local mode, so the
         "single existing file keeps growing" case is normal — just tail it)
    """
    sdir = _sessions_dir(agent_id)
    deadline = time.time() + timeout_s
    spawn_ts = time.time()
    # Record mtimes at snapshot time so we can detect bumps
    before_mtimes = {p: p.stat().st_mtime for p in before_snapshot if p.exists()}

    while time.time() < deadline:
        if known_session_id:
            f = _session_file(agent_id, known_session_id)
            if f.exists():
                return f
        elif sdir.exists():
            now_files = set(sdir.glob("*.jsonl"))
            # (2) new file
            new = now_files - before_snapshot
            if new:
                return max(new, key=lambda p: p.stat().st_mtime)
            # (3) existing file got touched after we spawned
            for p in now_files & before_snapshot:
                try:
                    cur_mt = p.stat().st_mtime
                except FileNotFoundError:
                    continue
                if cur_mt > before_mtimes.get(p, 0) and cur_mt > spawn_ts - 5:
                    return p
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
    # _get_system_prompt() already substitutes skill_name / agent_server internally,
    # so the returned string is final — do NOT run .format() on it again (JSON
    # braces inside it would break Python's format parser).
    if not resume_id:
        sys_ctx = _get_system_prompt(
            state.agent_type, state.skill,
            agent_server_url=getattr(state, "_agent_server_url", "") or "",
        )
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

    # Snapshot pre-existing session files + sizes so we can detect new / tail
    # from the right offset (OpenClaw reuses a single file per agent — the
    # file may already exist with prior history we don't want to re-broadcast).
    sdir = _sessions_dir(agent_id)
    before_snapshot = set(sdir.glob("*.jsonl")) if sdir.exists() else set()
    existing_sizes = {p: p.stat().st_size for p in before_snapshot if p.exists()}

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

    # Shared state between tail_wrapper and envelope-parsing at end
    tail_info = {"file": None, "start_offset": 0}

    async def _tail_wrapper():
        sf = await _wait_for_session_file(agent_id, resume_id, before_snapshot)
        if sf is None:
            print(f"[OC] {state.skill}: session file never appeared — dashboard tail disabled")
            return
        # Start offset = existing size if we're appending to an old file; 0 if new.
        start = existing_sizes.get(sf, 0)
        tail_info["file"] = sf
        tail_info["start_offset"] = start
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
    # stopReason: 3-level fallback (some runs have it only in completion)
    stop_reason = (
        meta.get("stopReason")
        or meta.get("completion", {}).get("stopReason")
        or meta.get("completion", {}).get("finishReason")
        or "unknown"
    )
    provider = agent_meta.get("provider") or ""
    model = agent_meta.get("model") or ""

    # Persist session_id (may have been set earlier by tail, but ensure it's here)
    sess_id = agent_meta.get("sessionId")
    if sess_id and sess_id != state.session_id:
        state.session_id = sess_id
        _update_entry(state.skill, {"session_id": sess_id})

    # toolSummary from envelope is the FINAL TURN's tally, not cumulative. For
    # multi-turn runs where the last turn is text-only, envelope says 0 even
    # though the session had many tool calls. Count cumulatively from the JSONL.
    env_calls = tool_sum.get("calls", 0)
    env_fails = tool_sum.get("failures", 0)
    env_tools = tool_sum.get("tools", []) or []
    jsonl_calls, jsonl_fails, jsonl_tools = (0, 0, [])
    if tail_info["file"] is not None:
        jsonl_calls, jsonl_fails, jsonl_tools = _count_session_tools_from_offset(
            tail_info["file"], tail_info["start_offset"]
        )
    total_calls = max(env_calls, jsonl_calls)
    total_fails = max(env_fails, jsonl_fails)
    total_tools = env_tools or jsonl_tools

    # Final summary message (mirrors Claude SDK's "Done — N turns, $X" message)
    cost = _estimate_cost(provider, model, usage)
    cost_str = f"${cost:.4f}" if cost > 0 else "free"
    done_msg = (
        f"Done — {total_calls} tool calls "
        f"({total_fails} failures) tools={total_tools}, "
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
