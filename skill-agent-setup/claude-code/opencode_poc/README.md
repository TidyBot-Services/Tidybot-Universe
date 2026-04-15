# opencode PoC

Prove opencode + Ollama can replace Claude Code as the orchestrator's agent harness.

## Status
- ✅ **Infrastructure** — session creation, SSE streaming, provider registration, end-to-end HTTP plumbing all work.
- ⚠️ **Tool-calling reliability on llama3.1:8b** — flaky. ~30% of runs trigger a real tool call with a real result; ~70% of runs the model hallucinates `{"name":"Task", "parameters":...}` as text. This is a model-quality issue, not a harness bug (verified: raw Ollama + same model does tool-calling perfectly when called with plain curl). Expect a stronger model (14B+ or cloud Gemini/Claude) to fix this — which matches the memory prediction that small-model tool-calling is the real PoC risk, not the harness swap.
- ⬜ **Part B** — real robot task (sim + agent_server): pending, blocked on tool-calling stability.

## Gotchas hit during setup
1. **Ollama default `num_ctx=4096` is a hard blocker**. opencode's system prompt + tool-schemas alone fill 4096 tokens, so the user message gets truncated and the model falls back to narrating tool calls as prose. MUST build a model variant with `num_ctx=32768`.
2. **`@ai-sdk/openai-compatible` didn't forward tools** properly to Ollama. Switched to `ollama-ai-provider-v2` (community npm, purpose-built for Ollama) — that fixed the tool schema passing.
3. **Deleting the base Ollama model broke the ctx32k variant** (they share layer hashes). Keep both, or delete carefully.

## One-time install

```bash
# 1. opencode CLI
curl -fsSL https://opencode.ai/install | bash

# 2. ollama daemon
curl -fsSL https://ollama.com/install.sh | sh

# 3. base model + ctx32k variant (MUST do both)
ollama pull llama3.1:8b
ollama create llama3.1:8b-ctx32k -f Modelfile.llama32k

ollama list   # expect BOTH llama3.1:8b and llama3.1:8b-ctx32k
```

## Run Part A — harness smoke test

```bash
cd opencode_poc
opencode serve --port 4096 &                      # terminal 1
python3 demo_opencode.py --provider ollama --model llama3.1:8b-ctx32k --part A
```

**Successful run** (best-case, ~30% of the time with 8B model):
```
🔧 tool call: task
   args: {"description": "...", "subagent_type": "general"}
   ✓ result: task_id: ses_...
<task_result>
The README.md file was read and summarized as: ...
```

**Failed run** (hallucinated tool):
```
{"name": "Task", "parameters": {...}}   ← plain text, no real invocation
[session idle — agent turn complete]
```

Re-run a few times to see the reliability variance.

## Run Part B — real robot task (TODO)

Prereqs: sim + agent_server running on default ports (see parent CLAUDE.md).

```bash
python3 demo_opencode.py --provider ollama --model llama3.1:8b-ctx32k --part B
```

## What this PoC proves (and doesn't)
**Proves:**
- opencode is a viable drop-in harness from an infrastructure standpoint (HTTP API, SSE, session model, multi-provider).
- Integration with local Ollama via `ollama-ai-provider-v2` is working end-to-end.
- The orchestrator rewrite is feasible — `_run_agent_sdk` maps cleanly to `POST /session/:id/message` + SSE consumer.

**Does NOT prove:**
- That local 8B models can reliably run dev-agent workflows. They currently can't (Part A ~30% success). Production PoC needs either (a) 14B+ model on more VRAM, (b) Parcc/cloud, or (c) cloud Gemini as a bridge.

## Files
- `demo_opencode.py` — Python client (creates session, streams events, prints text + tools)
- `opencode.json` — provider config (Ollama via `ollama-ai-provider-v2`)
- `Modelfile.llama32k` — Ollama modelfile for 32k-context variant
- `.env.example` — template for Gemini fallback

## Scope
- Branch: `feature/opencode-poc`
- No changes to `agent_orchestrator.py` or other project code
- Self-contained under `opencode_poc/`
