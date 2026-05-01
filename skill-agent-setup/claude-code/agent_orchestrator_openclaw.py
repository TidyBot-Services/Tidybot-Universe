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
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))

# agent_type → base OpenClaw agent id. For multi-target runs, a per-target
# derived agent (e.g. tidybot-dev-env-1) is auto-created by _ensure_agent_exists.
#
# Why: OpenClaw local mode (`openclaw agent --local`) forces every invocation
# to use the single `agent:<id>:main` session key. Parallel subprocesses on
# the same agent all write to the same JSONL file, corrupting the transcript.
# Per-target agents give each dev its own agentDir + session files.
AGENT_TYPE_MAP = {
    "dev":       os.environ.get("OPENCLAW_DEV_AGENT",  "tidybot-dev"),
    "evaluator": os.environ.get("OPENCLAW_EVAL_AGENT", "tidybot-evaluator"),
    "test":      os.environ.get("OPENCLAW_TEST_AGENT", "tidybot-dev"),  # tests piggyback on dev
}

# Module-level cache: agent ids already known to exist (skip `agents list` check)
_known_agents: set[str] = set()

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


# IRON RULES — re-injected on every prompt (resume too) because openclaw `--local`
# mode shares one session per agent across all skills, so the original system
# prompt's rules get buried hundreds of turns deep. Caught 2026-05-01 v15 batch 3:
# microwave-press-button dev launched its own `maniskill_server` subprocess to
# "fix" an unreachable sim-1, and used the WRONG task name (Open-Single-Door,
# leaked from batch 2's session history). This single-paragraph reminder MUST
# stay short — it's prepended to every turn.
_IRON_RULES = """🚨 STRICT BOUNDARIES — VIOLATING ANY IS A HARD FAILURE 🚨

1. DO NOT start, restart, or kill ANY background process.
   - Never run: nohup ... &, tmux, systemctl, pkill, fuser, kill, killall.
   - Never launch: maniskill_server, server.py, sim, agent_server, robocasa_*.
   - If sim/agent_server is unreachable: write `import sys; print("ABORT: infra down"); sys.exit(1)`
     to your skill code and submit. Do NOT "fix" infrastructure.

2. SHELL exec only via the SDK's submit_and_wait.py. Never bash directly.
   - Read tool, Write tool: OK (only inside skills/<your-skill>/scripts/).
   - All robot/sim execution must go through `from robot_sdk import ...`.

3. SCOPE: You are writing code for ONE SKILL THIS TURN.
   - Stay focused on the {skill_name} task_env named in the user prompt below.
   - Ignore any prior session content about other skills/tasks.
   - Do NOT edit files outside skills/<this-skill>/scripts/.
"""


def _build_iron_rules_for(skill: str) -> str:
    """Return iron-rules block with skill_name substituted."""
    return _IRON_RULES.replace("{skill_name}", skill)


def _clean_stale_locks(sdir: Path) -> int:
    """Remove .lock files in `sdir` whose owning PID is dead.

    openclaw uses file-based locks (writes {pid, createdAt} to .jsonl.lock).
    When an openclaw subprocess gets SIGKILL'd (driver teardown, OOM, vLLM
    compaction crash), it can't run cleanup → lock file persists. The next
    subprocess waits 10s for the lock then errors out (`FailoverError:
    session file locked`). This caught us 2026-05-01 v15: PIDs in batch 2
    locks were dead but their lock files lived through batch 3, deadlocking
    every dev/eval call on those agent slots.

    Returns count of locks cleaned.
    """
    if not sdir.exists():
        return 0
    cleaned = 0
    for lock_path in sdir.glob("*.lock"):
        try:
            content = lock_path.read_text().strip()
            if not content:
                lock_path.unlink()
                cleaned += 1
                continue
            data = json.loads(content)
            pid = data.get("pid")
            # /proc/<pid> exists iff the process is alive (Linux)
            if pid and not Path(f"/proc/{pid}").exists():
                lock_path.unlink()
                print(f"[OC] cleaned stale lock {lock_path.name} (pid={pid} dead)")
                cleaned += 1
        except Exception as e:
            print(f"[OC] could not check lock {lock_path}: {e}")
    return cleaned


def resolve_agent_id(agent_type: str, target_name: str = "") -> str:
    """Resolve agent_type (+ optional target) → concrete OpenClaw agent id.

    Single-target / empty target_name → base id (e.g. `tidybot-dev`).
    Multi-target (env-0, env-1, ...) → derived id (e.g. `tidybot-dev-env-0`).
    """
    base = AGENT_TYPE_MAP.get(agent_type)
    if not base:
        raise ValueError(f"no OpenClaw agent mapped for agent_type={agent_type!r}")
    if not target_name:
        return base
    safe_target = target_name.replace("_", "-").replace(".", "-").replace("/", "-").replace(":", "-")
    return f"{base}-{safe_target}"


def _agents_list_json() -> list[dict]:
    """Return openclaw agents list (best-effort; empty list on failure)."""
    try:
        p = subprocess.run(
            ["openclaw", "agents", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        # `openclaw agents list --json` may or may not be supported; fall back
        # to parsing the human format (lines starting with "- <id>")
        if p.returncode == 0:
            try:
                return json.loads(p.stdout)
            except json.JSONDecodeError:
                pass
        out = p.stdout
        agents = []
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("- ") and " " not in s[2:].split("(")[0].strip():
                agents.append({"id": s[2:].split()[0]})
        return agents
    except Exception:
        return []


def _agent_exists(agent_id: str) -> bool:
    if agent_id in _known_agents:
        return True
    for a in _agents_list_json():
        if a.get("id") == agent_id:
            _known_agents.add(agent_id)
            return True
    # Fallback: check directory existence
    if (OPENCLAW_HOME / "agents" / agent_id / "agent").is_dir():
        _known_agents.add(agent_id)
        return True
    return False


def _ensure_agent_exists(target_agent_id: str, base_agent_id: str,
                          workspace: str) -> None:
    """Create target_agent_id if missing, cloning model + auth from base.

    This is idempotent and cached; only pays the CLI cost on first call per
    orchestrator run.
    """
    if _agent_exists(target_agent_id):
        return

    # Look up base agent's configured model
    base_cfg_path = OPENCLAW_HOME / "openclaw.json"
    model = "ollama/llama3.1:8b-ctx32k"  # last-resort default
    try:
        cfg = json.loads(base_cfg_path.read_text())
        for a in cfg.get("agents", {}).get("list", []):
            if a.get("id") == base_agent_id:
                model = a.get("model", model)
                break
    except Exception as e:
        print(f"[OC] warn: could not read base config {base_cfg_path}: {e}")

    print(f"[OC] creating per-target agent {target_agent_id} (cloned from {base_agent_id}, model={model})")
    try:
        subprocess.run(
            ["openclaw", "agents", "add", target_agent_id,
             "--workspace", workspace,
             "--model", model,
             "--non-interactive", "--json"],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[OC] warn: agents add failed: {e.stderr[-300:] if e.stderr else e}")
        return
    except Exception as e:
        print(f"[OC] warn: agents add raised: {e}")
        return

    # Clone auth-profiles.json from base (so target inherits google/anthropic keys)
    src = OPENCLAW_HOME / "agents" / base_agent_id / "agent" / "auth-profiles.json"
    dst = OPENCLAW_HOME / "agents" / target_agent_id / "agent" / "auth-profiles.json"
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            os.chmod(dst, 0o600)
            print(f"[OC] cloned auth from {base_agent_id} → {target_agent_id}")
        except Exception as e:
            print(f"[OC] warn: auth copy failed: {e}")
    else:
        print(f"[OC] warn: {src} missing — {target_agent_id} has no auth profile, will fail on provider call")

    _known_agents.add(target_agent_id)


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
                                  timeout_s: float = 90.0) -> Optional[Path]:
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
        _update_entry, broadcast_full_sync,
    )

    # Session id IS the filename (without .jsonl). OpenClaw's JSONL records
    # don't carry the sessionId at record level, so populate state.session_id
    # from the file path NOW so dashboard live-sessions sees this run.
    if not state.session_id:
        try:
            state.session_id = session_file.stem
            _update_entry(state.skill, {"session_id": state.session_id})
            await broadcast_full_sync()
        except Exception:
            pass

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

    # Per-target agent id (isolation from other targets' runs)
    target_name = getattr(state, "target_name", "") or ""

    # Resolve which (agent_server, sim_api) this dev agent should talk to.
    # We rely on orch's spawn_agent() having stamped these onto `state` —
    # NOT on re-importing agent_orchestrator's module globals (which fail
    # because orch runs as __main__ and `import agent_orchestrator` from a
    # submodule loads a *separate* fresh copy of the module).
    bound_agent_server = (getattr(state, "_agent_server_url", None)
                          or "http://localhost:8080")
    bound_sim_api = (getattr(state, "_sim_api", None)
                     or "http://localhost:5500")
    base_agent_id = resolve_agent_id(state.agent_type)
    agent_id = resolve_agent_id(state.agent_type, target_name)

    # Auto-create target-specific agent if missing (clones model + auth from base)
    if agent_id != base_agent_id:
        _ensure_agent_exists(agent_id, base_agent_id, workspace=str(WORKSPACE_DIR))

    resume_id = state.session_id

    # IRON RULES re-injection: prepend on every prompt (first turn AND resume)
    # because openclaw `--local` shares one session per agent — the original
    # system prompt's rules get buried hundreds of turns deep across batches.
    # See _IRON_RULES module constant above for the full reasoning.
    iron_rules = _build_iron_rules_for(state.skill)

    # On first turn, also prepend _get_system_prompt() content so the agent has
    # full dev/evaluator context. _get_system_prompt() already substitutes
    # skill_name / agent_server internally, so the returned string is final —
    # do NOT run .format() on it again (JSON braces would break format parser).
    if not resume_id:
        sys_ctx = _get_system_prompt(
            state.agent_type, state.skill,
            agent_server_url=bound_agent_server,
        )
        full_prompt = f"{iron_rules}\n\n{sys_ctx}\n\n--- USER TASK ---\n{prompt}"
    else:
        full_prompt = f"{iron_rules}\n\n--- TASK ---\n{prompt}"

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

    tag = target_name or "primary"
    print(f"[OC] {state.skill}@{tag}: agent={agent_id} resume={resume_id[:12] + '...' if resume_id else '(new)'}")

    # Snapshot pre-existing session files + sizes so we can detect new / tail
    # from the right offset (OpenClaw reuses a single file per agent — the
    # file may already exist with prior history we don't want to re-broadcast).
    sdir = _sessions_dir(agent_id)

    # Stale-lock cleanup: any .lock file whose owning PID is dead is removed
    # before we spawn the subprocess. Without this, openclaw's first lock-acquire
    # call waits 10s on a dead-PID lock, then errors with FailoverError. See
    # _clean_stale_locks docstring for the v15 incident that drove this.
    _clean_stale_locks(sdir)

    before_snapshot = set(sdir.glob("*.jsonl")) if sdir.exists() else set()
    existing_sizes = {p: p.stat().st_size for p in before_snapshot if p.exists()}

    sub_env = {**os.environ,
               "AGENT_SERVER": bound_agent_server,
               "SIM_API": bound_sim_api}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE_DIR),
        env=sub_env,
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

    # Guard: if the dev subprocess errored before producing any tool calls,
    # don't push the skill into the eval/review pipeline. There is no fresh
    # execution recording to evaluate — proceeding would just re-evaluate
    # the prior submission and produce a misleading "passed" verdict.
    # Instead, mark the skill failed so autonomous mode picks it back up
    # via the regular re-spawn cascade in `_auto_spawn_ready_skills`.
    if state.agent_type == "dev" and total_calls == 0 and stop_reason == "error":
        msg = (
            f"Dev exited with stop=error and 0 tool calls — likely an LLM/network "
            f"hiccup or openclaw startup error. Skipping eval pipeline; marking "
            f"skill as failed so the orchestrator can re-spawn a fresh dev."
        )
        print(f"[OC] {state.skill}: {msg}")
        state.log.append({"text": msg, "role": "agent"})
        await ws_broadcast_agent_msg(state.skill, msg, state.agent_type)
        try:
            _update_entry(state.skill, {"status": "failed"})
        except Exception as _e:
            print(f"[OC] {state.skill}: failed to mark skill failed: {_e}")
        state.status = "error"
        return

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


# ---------------------------------------------------------------------------
# Evaluator path — same idea as _run_agent_openclaw but a one-shot, returns
# collected assistant text + cost. The orchestrator's run_evaluator() wraps
# this and parses EVAL_RESULT JSON out of the returned text.
# ---------------------------------------------------------------------------
async def _run_eval_openclaw(
    skill: str,
    system_prompt: str,
    user_prompt: str,
    on_text=None,
    timeout_s: int = 900,
    target_name: str = "",
) -> dict:
    """One-shot evaluator via openclaw subprocess.

    With ``target_name`` set, uses a per-target evaluator agent
    (``tidybot-evaluator-<target>``) so concurrent evals across multiple
    skills don't fight over the single shared session file lock.

    Returns:
      {"ok": bool, "text": str, "session_id": str, "cost_usd": float,
       "num_turns": int, "error": str (only if ok=False)}
    """
    from agent_orchestrator import WORKSPACE_DIR

    base_agent_id = AGENT_TYPE_MAP.get("evaluator")
    if not base_agent_id:
        return {"ok": False, "text": "", "error": "no evaluator agent mapped"}
    agent_id = resolve_agent_id("evaluator", target_name)

    # If a per-target derived agent is needed, auto-create it from base
    # (mirrors the dev-agent isolation logic).
    if agent_id != base_agent_id:
        _ensure_agent_exists(agent_id, base_agent_id, workspace=str(WORKSPACE_DIR))
    if not _agent_exists(agent_id):
        return {"ok": False, "text": "", "error": f"agent {agent_id!r} not configured in OpenClaw"}

    # OpenClaw doesn't have a flag for system-prompt override on per-call basis;
    # prepend system context to the message.
    full_prompt = f"{system_prompt}\n\n--- USER TASK ---\n{user_prompt}"

    cmd = [
        "openclaw", "agent",
        "--local",
        "--agent", agent_id,
        "--json",
        "--timeout", str(timeout_s),
        "-m", full_prompt,
    ]

    sdir = _sessions_dir(agent_id)
    # Stale-lock cleanup before spawning eval subprocess (mirror dev path).
    _clean_stale_locks(sdir)
    before_snapshot = set(sdir.glob("*.jsonl")) if sdir.exists() else set()
    existing_sizes = {p: p.stat().st_size for p in before_snapshot if p.exists()}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(WORKSPACE_DIR),
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        if proc.returncode is None:
            try: proc.send_signal(signal.SIGINT)
            except Exception: pass
            try: await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception: pass
            try:
                if proc.returncode is None: proc.kill()
            except Exception: pass
        return {"ok": False, "text": "", "error": f"openclaw eval timed out after {timeout_s}s"}

    stderr_raw = stderr_bytes.decode(errors="replace")
    envelope = _parse_final_envelope(stderr_raw)
    if not envelope:
        return {
            "ok": False,
            "text": "",
            "error": f"no JSON envelope in stderr; tail: {stderr_raw[-500:]}",
        }

    meta = envelope.get("meta", {}) or {}
    agent_meta = meta.get("agentMeta", {}) or {}
    usage = agent_meta.get("usage", {}) or {}
    sess_id = agent_meta.get("sessionId") or ""
    provider = agent_meta.get("provider") or ""
    model = agent_meta.get("model") or ""
    cost = _estimate_cost(provider, model, usage)

    # Locate the session jsonl that grew during this run
    target_file = None
    target_start = 0
    if sess_id:
        candidate = sdir / f"{sess_id}.jsonl"
        if candidate.exists():
            target_file = candidate
            # If pre-existing (continuation), skip the prefix
            if candidate in before_snapshot:
                target_start = existing_sizes.get(candidate, 0)
    if target_file is None and sdir.exists():
        new_files = set(sdir.glob("*.jsonl")) - before_snapshot
        if new_files:
            target_file = next(iter(new_files))
        else:
            # fall back to file with biggest growth
            for p, old_sz in existing_sizes.items():
                if p.exists() and p.stat().st_size > old_sz:
                    target_file = p
                    target_start = old_sz
                    break

    text_parts: list[str] = []
    if target_file is not None:
        try:
            with open(target_file, "rb") as f:
                f.seek(target_start)
                for raw in f:
                    try:
                        rec = json.loads(raw.decode("utf-8", errors="replace"))
                    except Exception:
                        continue
                    # OpenClaw shape: {"type":"message","message":{"role":..,
                    # "content":[{"type":"text","text":...}, {"type":"toolCall",...}]}}
                    # The dev tail uses the same access pattern (~L342-363).
                    if rec.get("type") != "message":
                        continue
                    msg = rec.get("message", {}) or {}
                    if msg.get("role") != "assistant":
                        continue
                    for blk in msg.get("content", []) or []:
                        if not isinstance(blk, dict):
                            continue
                        if blk.get("type") != "text":
                            continue
                        t = (blk.get("text") or "").strip()
                        if not t:
                            continue
                        text_parts.append(t)
                        if on_text:
                            try: await on_text(t)
                            except Exception: pass
        except Exception as e:
            print(f"[OC eval] {skill}: failed to read session file {target_file}: {e}")

    full_text = "\n".join(text_parts)

    return {
        "ok": True,
        "text": full_text,
        "session_id": sess_id,
        "cost_usd": cost,
        "num_turns": int(agent_meta.get("turns") or len(text_parts) or 0),
    }
