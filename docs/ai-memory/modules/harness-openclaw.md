# Module — harness/openclaw

The `--harness openclaw` backend for `agent_orchestrator.py`. Lets the orch drive any LLM via OpenClaw's `--local --json` subprocess mode, instead of being locked to `claude-agent-sdk`.

## What it does

For each dev / evaluator spawn, the orch:
1. Resolves `(agent_type, target, skill)` → an OpenClaw agent id (e.g. `tidybot-dev-default-detect-objects`)
2. Auto-creates the agent if missing (cloning model + auth from the base agent)
3. Spawns `openclaw agent --local --agent <id> --json -m "<prompt>"` as a subprocess
4. Tails the agent's session JSONL to stream to the dashboard
5. Parses the final JSON envelope from stderr to extract tool counts, stop reason, cost
6. Returns dev/eval result to orch core

## Key code paths

| File | Role |
|---|---|
| `skill-agent-setup/claude-code/agent_orchestrator_openclaw.py` | All OpenClaw harness logic (700+ lines) |
| `~/bin/with-litellm.sh` | Wrapper that injects `LITELLM_KEY` from `~/.litellm-key` into env without echoing |
| `~/.litellm-key` | LiteLLM proxy API key (chmod 600, never echo) |
| `~/.openclaw/openclaw.json` | Global OpenClaw config (provider URLs, agent list, models) |
| `~/.openclaw/agents/<agent_id>/` | Per-agent dirs (AGENTS.md system prompt + agent/ metadata + sessions/) |
| `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md` | User-facing setup doc |

## Setup (high-level, full version in CLAUDE-OPENCLAW-HARNESS.md)

1. Install `openclaw` CLI: `curl -fsSL https://openclaw.ai/install.sh | bash && openclaw onboard --install-daemon`
2. Save LiteLLM key to `~/.litellm-key` with `chmod 600`
3. Write `~/bin/with-litellm.sh` wrapper
4. In `~/.openclaw/openclaw.json`, configure provider:
   ```json
   "models": {"providers": {
       "litellm-parcc": {
           "baseUrl": "https://litellm.parcc.upenn.edu/v1",
           "apiKey": "${LITELLM_KEY}",
           "type": "openai-compatible"
       }
   }}
   ```
5. Create base agents:
   ```bash
   openclaw agent create tidybot-dev \
       --model litellm-parcc/deepseek-ai/DeepSeek-V4-Flash \
       --workspace $WORKSPACE
   openclaw agent create tidybot-evaluator \
       --model litellm-parcc/Qwen/Qwen3.6-35B-A3B \
       --workspace $WORKSPACE
   ```
6. Launch orch:
   ```bash
   ~/bin/with-litellm.sh python3 agent_orchestrator.py \
       --graph graphs/<X> --harness openclaw [--autonomous]
   ```

## OpenClaw `--local` mode quirks

These are limitations of OpenClaw, not the harness. Document the workarounds.

### 1. Single session per agent

`--local` mode locks every invocation to one session per `agent_id` (`agent:<id>:main`). `--session-id <new-uuid>` is **silently ignored** for creating a new session. Parallel subprocesses on the same agent corrupt the shared JSONL by interleaving writes.

**Workaround**: per-(target, skill) agent ids. Implemented in `_run_agent_openclaw` ~line 395:

```python
target_name = getattr(state, "target_name", "") or ""
if state.agent_type == "evaluator":
    agent_id = resolve_agent_id(state.agent_type, target_name)
else:
    suffix = f"{target_name}-{state.skill}" if target_name else state.skill
    agent_id = resolve_agent_id(state.agent_type, suffix)
_ensure_agent_exists(agent_id, base_agent_id, workspace=str(WORKSPACE_DIR))
```

Results: dev for `detect-objects` runs on `tidybot-dev-default-detect-objects`, dev for `approach-counter` on `tidybot-dev-default-approach-counter`. Their session JSONLs are in different dirs → no lock collision.

### 2. Stale session_id from graph.json after orch restart

The orchestrator persists `session_id` in `graph.json` after each iter. On restart with `--harness openclaw`, that ID is loaded and passed as `--session-id <stale>` — but the corresponding file doesn't exist on disk (OpenClaw `--local` always makes a fresh session per spawn, ignoring the resume hint).

`_wait_for_session_file(known_session_id=stale)` then blocks 90s waiting for that file, never finds it, the dashboard tail stays disabled for the whole iter.

**Workaround**: detect-and-clear in `_run_agent_openclaw` ~line 412:

```python
resume_id = state.session_id
if resume_id and not _session_file(agent_id, resume_id).exists():
    print(f"[OC] {state.skill}: stale session_id {resume_id[:12]}… clearing")
    state.session_id = None
    resume_id = None
```

After clearing, `_wait_for_session_file` falls back to "find new/touched file" path, picks up the freshly-created session, dashboard tail works.

### 3. Penn LiteLLM outages

The LiteLLM proxy at `litellm.parcc.upenn.edu` has periodic outages (observed several times in 2026-05). When down:
- Both `Qwen3.6` and `Kimi-K2.6` (VLM models for evaluator) fail simultaneously
- `DeepSeek-V4-Flash` (dev model) also fails
- Recovery is usually 5min-14hr (variable)

There's a Monitor task probing every 90s; check `/health` of the proxy if eval iterations stall.

## Per-target / per-skill agent matrix (current convention)

| Use case | Agent id pattern |
|---|---|
| Single-target dev, single skill | `tidybot-dev` |
| Single-target dev, named skill | `tidybot-dev-<skill>` |
| Multi-target dev, env 0 | `tidybot-dev-env-0` |
| Multi-target dev, env 0, named skill | `tidybot-dev-env-0-<skill>` |
| Single-target evaluator | `tidybot-evaluator` |
| Multi-target evaluator, env 1 | `tidybot-evaluator-env-1` |

`_ensure_agent_exists` lazily creates these on first spawn, cloning model + auth from the base `tidybot-dev` or `tidybot-evaluator`. So you only ever manually create the bases.

## Restart flow

```bash
# Kill cleanly
ORCH=$(pgrep -f "agent_orchestrator.py")
OC=$(pgrep -f "^openclaw")
for p in $OC $ORCH; do kill $p; done; sleep 3

# Lock cleanup (only if any subprocess died hard)
rm -f ~/.openclaw/agents/tidybot-dev-default-*/sessions/*.lock

# Restart
cd ~/文档/Tidybot-Universe/skill-agent-setup/claude-code
~/bin/with-litellm.sh python3 agent_orchestrator.py \
    --graph graphs/<X> --harness openclaw --autonomous \
    > /tmp/orch_autonomous.log 2>&1 &
```

## Related

- `decisions/0003-multi-harness-llm-agnostic.md`
- `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md` — full user-facing setup
- `skill-agent-setup/openclaw/README.md` — different mode (standalone chat, not orch harness)
- `~/.claude/projects/.../memory/reference_openclaw_local_mode.md`
- `~/.claude/projects/.../memory/reference_openclaw_setup.md`
- `~/.claude/projects/.../memory/project_dashboard_session_id_fix.md`
- `~/.claude/projects/.../memory/project_harness_migration.md`
