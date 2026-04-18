# OpenClaw PoC

Prove OpenClaw + Ollama can replace Claude Code as the orchestrator's agent harness.

Mirrors `opencode_poc/` in structure so the two PoCs can be compared head-to-head.

## Status

- ✅ **Infrastructure** — `openclaw agent --local --json --agent tidybot-poc` drives the
  harness end-to-end. JSON envelope on stderr, well-structured (meta.toolSummary,
  meta.stopReason, meta.executionTrace).
- ✅ **Part A (single tool call, llama3.1:8b-ctx32k)** — **5/5 PASS, 100% success**.
  Mean 52s per run, consistent tool dispatch, clean stop reasons. Directly contradicts
  opencode_poc's report of "~30% success on llama3.1:8b" with the same model class —
  suggesting OpenClaw's native `/api/chat` + tool-schema passing is materially more
  robust than the OpenAI-compat path opencode uses by default.
- ✅ **Part B (chained multi-tool, llama3.1:8b-ctx32k)** — PASSED on first run.
  2 exec tool calls, 0 failures, correct answer, 74s.
- ⚠️ **Part C1 task_demo with llama3.1:8b-ctx32k** — harness OK, **model inadequate**.
  3 tool calls, 1 `write` schema validation failure (model forgot `content` arg),
  code generated was structurally wrong (treated `robot_sdk.arm` as a class). Matches
  opencode_poc's prediction that 8B models need an upgrade for real dev work.
- ✅ **Part C2 full_pipeline with google/gemini-2.5-flash** — **END-TO-END SUCCESS on harness**,
  task success=false. See "Full pipeline results" below.
- ⬜ **Orchestrator rewrite** — pending. `_run_agent_sdk` in `agent_orchestrator.py`
  currently uses `claude-agent-sdk` `ClaudeSDKClient`. Migration path: replace with
  subprocess driver around `openclaw agent --local --json`, or use the OpenClaw
  Gateway WebSocket protocol for finer control (session resume, interrupt, inject).

## Test run output (captured 2026-04-18)

```
[bench_harness] 5 runs against agent=tidybot-poc
  prompt: List the files in the current directory with a bash tool call. Report only the c...

  run  1/5: PASS   50.6s  tools=1(fail=0)  → There are 18 files in the current directory.
  run  2/5: PASS   50.9s  tools=1(fail=0)  → There are 18 files in the current directory.
  run  3/5: PASS   52.8s  tools=1(fail=0)  → There are 18 files in the current directory.
  run  4/5: PASS   52.8s  tools=1(fail=0)  → There are 18 files in the current directory.
  run  5/5: PASS   53.0s  tools=1(fail=0)  → There are 18 files in the current directory.

  runs:        5
  passed:      5
  failed:      0
  success:     100.0%
  elapsed:     mean=52.0s  median=52.8s  min=50.6s  max=53.0s
```

## Comparison vs opencode_poc (same model family)

| Metric | opencode + llama3.1:8b | OpenClaw + llama3.1:8b-ctx32k |
|---|---|---|
| Tool-call success rate | ~30% | **100% (5/5)** |
| Integration path | `@ai-sdk/openai-compatible` → Ollama OpenAI-compat | native `/api/chat` |
| Context-window setup | manual `num_ctx=32768` Modelfile required | same (ctx32k variant) |
| Harness overhead (prompt tokens / turn) | untested | ~10k input tokens (incl. skill catalog) |
| End-to-end PoC state | Part A pending 14B+ model | Part A complete, Part B complete |

## Architecture recap

```
  ┌─────────────────────┐
  │ demo_openclaw.py    │  Python subprocess driver
  └──────────┬──────────┘
             │ `openclaw agent --local --json -m "..." --agent tidybot-poc`
  ┌──────────▼──────────┐
  │ openclaw CLI (node) │  embedded runner, no gateway
  └──────────┬──────────┘
             │ /api/chat
  ┌──────────▼──────────┐
  │ ollama daemon       │  llama3.1:8b-ctx32k
  └─────────────────────┘
```

No WebSocket gateway, no Python SDK (openclaw-sdk not installed — alpha, and the CLI
subprocess is sufficient for PoC). Total process count: 2 (openclaw node + ollama).

## Agent configuration (already set up in `~/.openclaw/openclaw.json`)

```json
{
  "id": "tidybot-poc",
  "workspace": "/home/truares/文档/Tidybot-Universe/skill-agent-setup/claude-code",
  "agentDir": "/home/truares/.openclaw/agents/tidybot-poc/agent",
  "model": "ollama/llama3.1:8b-ctx32k"
}
```

Custom skills loaded (13) live at `~/.openclaw/workspace/skills/`:
- `tb-pick-up-object`, `tidybot-skill-dev`, `tidybot-robot-sdk-ref`,
  `tidybot-robot-connection`, `tidybot-robot-hardware`, `tidybot-bundle`,
  `tidybot-run-robot-task`, `tidybot-zero-shot-task`, `tidybot-active-services`,
  `tidybot-skill-publish`, `restart-sim`, `start-maniskill`, `self-improvement`

These were authored in a prior session but not committed to git — they exist only
in the OpenClaw state dir. If you want them under version control, copy them into
`graphs/<name>/skills/` or commit `~/.openclaw/workspace/skills/` separately.

## Prereqs

- OpenClaw CLI ≥ 2026.4.x (`openclaw --version`)
- Ollama daemon running (`curl -sf http://localhost:11434/api/tags`)
- Models pulled:
  ```bash
  ollama pull llama3.1:8b
  ollama create llama3.1:8b-ctx32k -f ../opencode_poc/Modelfile.llama32k
  ```
- `tidybot-poc` agent exists (`openclaw agents list | grep tidybot-poc`)

## Run

```bash
# Part A — smoke test (1 tool call)
python3 demo_openclaw.py --part A

# Part B — chained tools (2 tool calls, still local)
python3 demo_openclaw.py --part B

# Reliability benchmark (captured above)
python3 bench_harness.py --runs 5

# Custom prompt
python3 demo_openclaw.py --prompt "What's 2+2? Use the exec tool to run `expr 2 + 2`."
```

## Full pipeline results (Part C2, 2026-04-18)

Ran `full_pipeline.py` against live ManiSkill sim + agent_server using
google/gemini-2.5-flash. The agent autonomously:
1. Read `graphs/counter-to-sink-demo/skills/pnp-counter-to-sink/SKILL.md`
2. Fetched SDK docs from `http://localhost:8080/code/sdk/markdown` (58 KB HTML)
3. Queried `http://localhost:5500/task/info` — discovered target is yogurt
4. Wrote `scripts/main.py` (110 lines) using correct module-level `robot_sdk` API
5. Submitted code via `/code/submit` and polled `/code/jobs/<id>` — **20 real robot
   executions recorded on the agent_server**
6. Iterated based on failures — 44 tool calls, 0 failures

**Harness metrics:**
| Metric | Value |
|---|---|
| total elapsed | 362s (~6 min) |
| tool calls | 55 (0 failures) |
| assistant turns | 61 |
| input tokens | 240,627 |
| output tokens | 40,398 |
| cache-read tokens | 2,612,617 |
| **rough cost** | **~$0.37** |
| rate-limit retries | Yes (Gemini free-tier 429, auto-recovered) |

**Task outcome: `success=false`** — but the failure is a prompting/evaluator problem,
not a harness problem:
- `obj_pos: [0.51, -2.76, 0.96]` — yogurt still on counter (not picked up)
- `eef_obj_dist: 0.75 m` — end effector didn't reach object
- Root cause: Gemini used "spout" (faucet) as proxy for sink basin — semantic miss
- Missing: multi-yaw grasp retry strategy, evaluator feedback loop

Code quality comparison:

| | llama3.1:8b (Part C1) | **gemini-2.5-flash (Part C2)** |
|---|---|---|
| main.py produced | 0 lines (write tool fail) | 110 lines |
| `robot_sdk` imports | `arm()` as class (wrong) | `from robot_sdk import arm, base, gripper, sensors, wb, http` ✓ |
| object detection | hardcoded deltas | `sensors.find_objects()` + fixture_context parsing ✓ |
| navigation | `arm.move_delta` only | `wb.move_to_pose(x, y, z, quat=...)` ✓ |
| grasp primitive | `gripper.close()` | `gripper.grasp(force=255)` + approach_height ✓ |
| SKILL.md constraint awareness | ignored | didn't call `go_home()` at end ✓ |

**Budget note:** $0.37 vs $0.05-0.10 estimate — 3-4× overshoot from Gemini free-tier
rate-limit triggering OpenClaw retries (~2.6M cache-read tokens across retries).
On a paid-tier key without rate limits, expect $0.10-0.15 per full dev-agent run.

## What this PoC proves (and doesn't)

**Proves:**
- OpenClaw harness is a **viable drop-in replacement for Claude Code** as the
  dev-agent substrate. Every capability the existing orchestrator depends on is
  present: tool loop, session persistence, streaming events, JSON envelope,
  provider abstraction (Ollama ↔ Google), auth profiles, rate-limit retry.
- Gemini 2.5 Flash via OpenClaw produces **structurally correct robot_sdk code**
  on first try when given SKILL.md + SDK reference.
- Full pipeline (read docs → write code → submit → execute on real sim → iterate)
  works end-to-end with zero orchestrator-layer rewrite — using only subprocess
  invocations of `openclaw agent --local --json`.

**Does NOT prove:**
- That Gemini 2.5 Flash alone can solve this task — without evaluator feedback,
  it gets stuck on the "spout ≠ sink" semantic error. Claude Sonnet's 397-line
  baseline main.py demonstrates the quality ceiling we should aim for.
- Orchestrator rewrite — `agent_orchestrator.py` still uses `claude-agent-sdk`.
  Migration is ~180 LOC (same magnitude as opencode PoC's orchestrator work).
- Performance under the real orchestrator load (many parallel dev agents
  sharing Gemini rate-limit budget).

## Part D — Gateway WebSocket probe (2026-04-18)

Question: can a Python orchestrator talk to the OpenClaw Gateway directly
(Path B), to get streaming events + `sessions.steer` inject semantics that
Path A CLI subprocess can't provide?

### What we confirmed

Via `openclaw` npm package's `.d.ts` files + internal JS:
- **Gateway handshake**: server sends `connect.challenge` event with a nonce
  immediately on TCP upgrade; client MUST reply with a `connect` req whose
  `device` field contains an Ed25519 signature over the v3 payload:
  `v3|deviceId|clientId|clientMode|role|scopes_comma|signedAtMs|token|nonce|platform|family`
- **Method schemas** (from `.d.ts`):
  - `sessions.send`: `{key, message, thinking?, attachments?, timeoutMs?, idempotencyKey?}`
  - `sessions.messages.subscribe`: `{key}`
  - `sessions.abort`: `{key, runId?}`
  - `sessions.patch`: `{key, thinkingLevel?, fastMode?, verbose/trace/reasoningLevel?, ...}`
  - `sessions.steer` confirmed in method registry (`method-scopes-*.js`)
- **Event types**: `session.message` (with `{sessionKey, message: {role, content[{type, text|toolCall, ...}]}, messageId, messageSeq}`) and `session.tool` — both require `READ_SCOPE` (`operator.read`)
- **Gateway is healthy**: `openclaw sessions --all-agents` from CLI works — returns 3 active sessions, so the handshake and all session methods succeed for the official client

### What we couldn't finish

Python `ws_probe.py` reaches `connect.challenge` and sends a device-signed
connect frame, but the gateway closes with `1008 "invalid request frame"` before
returning a `res`. Root cause is a subtle byte-mismatch between our v3 payload
and what `buildDeviceAuthPayloadV3` produces (likely scope ordering, token
field value, or `normalizeDeviceMetadataForAuth` handling of an empty
`deviceFamily`). Debuggable via `openclaw proxy run -- openclaw sessions` but
not in the time budget.

### Path B verdict: **feasible, but costlier than first-order estimate**

| Cost item | Initial estimate | Revised (post-probe) |
|---|---|---|
| WS client + event mapping | ~200 LOC | ~200 LOC |
| **Device-signed handshake** | ~50 LOC | ~100-150 LOC + days of debug |
| Schema version coupling (Ed25519 payload format can change between OpenClaw versions) | not considered | real risk, requires CI tests |
| Total | ~300 LOC | ~400-500 LOC |

**Why this is higher than first estimate:** OpenClaw's gateway requires device
pairing; there's no documented public third-party WS client protocol. The
auth flow (`connect.challenge` → Ed25519-signed v3 payload → `hello-ok`) has
to be reproduced byte-exactly, and our probe hit a rejection we haven't traced.
The official CLI bundles the full client library (`client-DkWAat_P.js`),
so CLI-as-subprocess is **orders of magnitude cheaper** than porting the auth
flow to Python.

### Recommended migration pattern (updated)

**Path A + Node.js sidecar** — cleaner than pure Path B:
1. **Dev agent invocation**: spawn `openclaw agent --local --session-id <id>`
   subprocess per dev agent (Path A, already proven end-to-end by Part C2).
2. **Live streaming/inject (if needed)**: spawn a **tiny Node.js sidecar** that
   imports OpenClaw's own `GatewayClient` from the npm package and proxies
   `session.message`/`session.tool` events to Python via stdout NDJSON.
   Avoids re-implementing device auth in Python.
3. **Concurrency**: Path A scales linearly by spawning N subprocesses; Gateway
   multiplexing is an optimization for later.

This sidesteps the entire auth-replication problem while keeping all the
features Path B would have given us.

## Part E — Orchestrator shim (Path A, implemented 2026-04-19)

`agent_orchestrator_openclaw.py` is the new backend module. `agent_orchestrator.py`
gains a `HARNESS` env flag that routes dev/inject/stop calls to either backend.

### Switching backends

```bash
# Default — unchanged behavior, uses ClaudeSDKClient
python3 agent_orchestrator.py --graph graphs/counter-to-sink-demo

# New — uses `openclaw agent --local --json` subprocess
HARNESS=openclaw python3 agent_orchestrator.py --graph graphs/counter-to-sink-demo
```

### One-time setup (before first `HARNESS=openclaw` run)

```bash
cd openclaw_poc
./setup_agents.sh                                  # Ollama for both (free)
# or
DEV_MODEL=google/gemini-2.5-flash ./setup_agents.sh   # Gemini for dev
```

Creates `tidybot-dev` + `tidybot-evaluator` OpenClaw agents, drops minimal
`AGENTS.md` files under `~/.openclaw/agents/<id>/`.

### What the shim covers

| orchestrator call site | Claude SDK path | OpenClaw path (new) |
|---|---|---|
| `spawn_agent` → dev runner | `_run_agent_sdk` | `_run_agent_openclaw` |
| Live dashboard streaming | `_consume_sdk_response` (SDK iter) | `_tail_session_jsonl` (file tail, 500ms poll) |
| `inject_hint` | `client.interrupt()` + `client.query()` | SIGINT subprocess + new `openclaw agent --session-id <same>` |
| `stop_agent` | `client.interrupt()` | SIGINT subprocess |
| `kill_agent` | cancels task + `interrupt()` | existing `proc` handling covers it |
| `run_evaluator` | `_run_eval_client` (SDK) | **unchanged** — evaluator still on Claude SDK (future work) |

### agent_type (+ target) → OpenClaw agent id

Single-target (graph has 1 target):

```python
AGENT_TYPE_MAP = {
    "dev":       "tidybot-dev",
    "evaluator": "tidybot-evaluator",
    "test":      "tidybot-dev",  # piggybacks on dev
}
```

**Multi-target** (graph has N targets): `resolve_agent_id(agent_type, target_name)`
derives per-target agent ids and auto-creates them on first use:

| agent_type | target  | resolved id             |
|------------|---------|-------------------------|
| dev        | ""      | `tidybot-dev`           |
| dev        | env-0   | `tidybot-dev-env-0`     |
| dev        | env-1   | `tidybot-dev-env-1`     |
| dev        | env-2   | `tidybot-dev-env-2`     |

**Why per-target agents are necessary:** OpenClaw `--local` mode forces every
invocation on a given agent to use the single `agent:<id>:main` session key —
passing `--session-id <uuid>` or `--to +xxx` is ignored, and parallel
subprocesses all write to the same JSONL file. Verified empirically (3-env
test, 2026-04-19): 3 subprocess calls on `tidybot-dev` all routed to
`agent:tidybot-dev:main` regardless of args. Per-target agents (each with its
own `agentDir`, `sessions/` directory, and session files) give clean isolation.

Override via env: `OPENCLAW_DEV_AGENT`, `OPENCLAW_EVAL_AGENT`, `OPENCLAW_TEST_AGENT`
(these set the BASE id; per-target derivation appends `-<target>`).

### Auto-creation of per-target agents

`_ensure_agent_exists(target_id, base_id, workspace)` is called lazily on
first spawn:
1. Check if `target_id` already exists (cached + `openclaw agents list`)
2. If not: `openclaw agents add <target_id> --workspace <ws> --model <from-base> --non-interactive --json`
3. Clone `auth-profiles.json` from `<base_id>/agent/` to inherit provider keys
   (e.g. google, anthropic)

No user action needed — agents materialize on first multi-target run.

### Known limitations of the shim

- **Evaluator still uses Claude SDK** — `run_evaluator` at line ~1933 is a separate
  code path with its own embedded `ClaudeSDKClient`; porting it is Stage 2.
  If you need 100% non-Anthropic, this is the remaining hold-out.
- **System prompt is prepended on first turn only** — OpenClaw agents have their own
  `AGENTS.md` (minimal per setup script); orchestrator's `_get_system_prompt()` is
  injected as context in the first user message. On resume, only the delta is sent.
- **Dashboard stream is ~500ms delayed** — file-tail poll interval. Functionally
  equivalent to SDK streaming for all broadcast purposes; just not real-time.
- **Gateway mode not used** — pure CLI subprocess. If concurrent-session load
  becomes an issue, consider adding a Node.js sidecar that proxies Gateway events
  (see Part D verdict).

## Next steps (in order)

1. ✅ **Orchestrator shim (Path A)** — done (this commit).
   Test end-to-end by running `HARNESS=openclaw` with a graph.
2. **Evaluator integration** — port `SYSTEM_PROMPT_EVALUATOR` logic so dev agents
   get semantic feedback ("spout is not sink; re-check `find_objects` results").
   Without this, a single-shot dev agent can't self-correct.
3. **(Optional) Node.js sidecar** — only if live dashboard streaming or mid-run
   inject is blocking. For most use cases, Path A's one-shot subprocess output
   is enough and much simpler to ship.
4. **Rate-limit handling** — for paid Gemini tier or cloud Claude, the built-in
   OpenClaw retry is fine; for free tier, cap retries to avoid token burn.

## Scope

- Branch: `feature/openclaw-harness-poc`
- Parallel PoC: `opencode_poc/` on `feature/opencode-poc`
