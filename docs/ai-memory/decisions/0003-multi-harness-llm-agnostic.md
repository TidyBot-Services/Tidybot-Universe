# 0003 — Multi-Harness for LLM-Agnostic Agent Execution

**Status:** Accepted (both harnesses live in production)
**Date:** 2026-04-19 (PoC) → 2026-04-22 (Stage 1 in orch) → 2026-05-09 (per-skill agent extension)

## Context

The orchestrator drives "dev" and "evaluator" agents. Initially this was hard-coded to Anthropic's Claude via `claude-agent-sdk`'s `ClaudeSDKClient`. Two motivations to add a second harness:

1. **Cost / model choice**: the user wants the option of running on cheaper or open-source models (DeepSeek, Qwen, Llama via Ollama) — not locked to Anthropic API pricing.
2. **Provider diversity**: research / pedagogical interest in comparing model families on the same workload.

## Decision

**Two harness backends, selected via `--harness <name>`:**

- **`claude-sdk`** (default) — `ClaudeSDKClient` in-process. Requires `ANTHROPIC_API_KEY`. Real session resume via `--session-id`.
- **`openclaw`** — `openclaw agent --local --json` subprocess. Driven by `agent_orchestrator_openclaw.py` (a sibling module imported only when active). Supports any model OpenClaw knows: LiteLLM, Ollama, Anthropic, Google, etc.

Both harnesses present the **same API surface** to the orchestrator core (`_run_agent_X`, `_consume_X_response`, `inject_hint`, `stop_agent`, `kill_agent`). Dispatch in `agent_orchestrator.py`:

```python
HARNESS = args.harness  # "claude-sdk" or "openclaw"
if HARNESS == "openclaw":
    import agent_orchestrator_openclaw as _openclaw_backend
...
if HARNESS == "openclaw":
    _runner = _openclaw_backend._run_agent_openclaw
else:
    _runner = _run_agent_sdk
```

## Consequences

- **No vendor lock-in** for the dev/eval roles.
- **Some features asymmetric**: claude-sdk has true session resume; openclaw `--local` ignores `--session-id` and creates fresh sessions per spawn (see `modules/harness-openclaw.md` for the workarounds: per-skill agent id, stale session_id clear).
- **Two code paths to maintain**: bugs need to be considered in both. In practice, the orch-side dispatch keeps the divergence narrow.
- **OpenClaw harness needs more scaffolding** (LiteLLM proxy, key file, wrapper script, per-target agent creation). Documented in `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md`.

## Per-skill agent extension (2026-05-09)

OpenClaw's single-session-per-agent limitation meant parallel sub-skills (leaves in a DAG) collided on the same JSONL lock when sharing `tidybot-dev-default` agent. Resolved by deriving agent id from `(target_name, skill_name)`: `tidybot-dev-default-detect-objects`, `tidybot-dev-default-approach-counter`, etc. `_ensure_agent_exists` auto-creates per-skill clones from the base agent.

This extends — does not replace — the per-target id scheme already used for multi-target runs.

## Alternatives Considered

- **Stay claude-sdk only**: rejected for the cost/model-choice reason.
- **Build our own subprocess driver instead of using OpenClaw**: rejected — OpenClaw already handles auth profiles, model dispatch, tool schemas, telemetry. Reusable infra worth the dependency.
- **Use LangGraph / LangChain agents**: rejected — heavier framework, more opinion than fits our needs (we want orchestrator owning the workflow, not the agent framework).

## Related

- `modules/harness-openclaw.md`
- `modules/orchestrator.md`
- `~/.claude/projects/.../memory/reference_openclaw_local_mode.md` (private quirks doc)
