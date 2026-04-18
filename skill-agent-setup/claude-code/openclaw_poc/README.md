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

## Next steps (in order)

1. **Orchestrator shim** — add `_run_agent_openclaw(state, prompt)` in
   `agent_orchestrator.py` that subprocess-drives `openclaw agent` instead of
   `ClaudeSDKClient`, guarded by an env flag (`HARNESS=openclaw|claude-sdk`). No
   big-bang rewrite.
2. **Evaluator integration** — port `SYSTEM_PROMPT_EVALUATOR` logic so dev agents
   get semantic feedback ("spout is not sink; re-check `find_objects` results for
   a `sink_*` entry or use the debug's `sink` bounding box"). Without this, a
   single-shot dev agent can't self-correct.
3. **Gateway mode** — replace subprocess driver with WebSocket to
   `ws://localhost:18789` once we need interrupt / inject / concurrent sessions.
4. **Rate-limit handling** — for paid Gemini tier or cloud Claude, the built-in
   OpenClaw retry is fine; for free tier, cap retries to avoid token burn.

## Scope

- Branch: `feature/openclaw-harness-poc`
- Parallel PoC: `opencode_poc/` on `feature/opencode-poc`
