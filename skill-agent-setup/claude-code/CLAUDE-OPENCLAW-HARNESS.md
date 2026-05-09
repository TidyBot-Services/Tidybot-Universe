# OpenClaw Harness Setup

How to run the orchestrator (`agent_orchestrator.py`) with `--harness openclaw`,
which spawns dev/evaluator agents as `openclaw agent --local` subprocesses
instead of using the in-process Claude SDK client.

> **This is NOT the standalone OpenClaw chat setup.** For "open a chat with
> OpenClaw and develop skills interactively", see [`../openclaw/README.md`](../openclaw/README.md).
> This doc covers the orchestrator's `--harness openclaw` mode, where the
> orchestrator owns the workflow and openclaw is just the model-runner.

## When to use this vs `--harness claude-sdk` (default)

| | `claude-sdk` (default) | `openclaw` |
|---|---|---|
| Backend | `ClaudeSDKClient` in-process | `openclaw agent --local` subprocess |
| Models | Anthropic only | Any model openclaw knows (LiteLLM, Ollama, anthropic) |
| Auth | `ANTHROPIC_API_KEY` env | LiteLLM key file + wrapper |
| Per-iter cost | Anthropic API rates | Whatever your provider charges |
| Resume sessions | Yes (real session_id) | **No** — `--local` ignores `--session-id` |
| When to pick | Default, fastest path | Want non-Anthropic models (DeepSeek/Qwen/etc) |

## Prerequisites

1. **OpenClaw CLI installed**

   ```bash
   curl -fsSL https://openclaw.ai/install.sh | bash
   openclaw onboard --install-daemon       # one-time
   ```

   Verify: `which openclaw` should print a path.

2. **LiteLLM proxy access** (or equivalent OpenAI-compatible endpoint)

   This repo was developed against Penn's LiteLLM proxy
   (`https://litellm.parcc.upenn.edu/v1`). If you're outside Penn, you'll need:
   - Your own LiteLLM proxy, or
   - An OpenAI/Anthropic-compatible gateway, or
   - Replace `litellm-parcc/...` model strings with `ollama/...` / `anthropic/...` to bypass

3. **API key file**

   Save your LiteLLM key (or proxy key) to a 600-perm file:

   ```bash
   read -s -p 'Paste LITELLM_KEY: ' K && echo -n "$K" > ~/.litellm-key && chmod 600 ~/.litellm-key && unset K
   ```

   Verify: `stat -c '%a' ~/.litellm-key` prints `600`. Never `cat` or `echo` this file.

4. **Wrapper script `~/bin/with-litellm.sh`**

   ```bash
   mkdir -p ~/bin
   cat > ~/bin/with-litellm.sh << 'EOF'
   #!/bin/bash
   # Inject LITELLM_KEY into env without printing. Usage: with-litellm.sh <cmd> [args...]
   set -euo pipefail
   KEY_FILE="${HOME}/.litellm-key"
   [ -f "$KEY_FILE" ] || { echo "with-litellm: $KEY_FILE not found" >&2; exit 1; }
   PERMS=$(stat -c '%a' "$KEY_FILE")
   [ "$PERMS" = "600" ] || [ "$PERMS" = "400" ] || {
       echo "with-litellm: $KEY_FILE permissions $PERMS — run: chmod 600 $KEY_FILE" >&2; exit 1;
   }
   [ "$#" -ge 1 ] || { echo "with-litellm: no command" >&2; exit 2; }
   export LITELLM_KEY="$(cat "$KEY_FILE")"
   export HARNESS="${HARNESS:-openclaw}"
   exec "$@"
   EOF
   chmod +x ~/bin/with-litellm.sh
   ```

   Verify: `~/bin/with-litellm.sh env | grep LITELLM_KEY` prints **the line exists**
   (don't worry — `LITELLM_KEY=...` value is the secret; if the env var name appears
   it's working).

5. **LiteLLM provider configured in openclaw**

   Edit `~/.openclaw/openclaw.json`, find `models.providers`, and add:

   ```json
   "models": {
     "providers": {
       "litellm-parcc": {
         "baseUrl": "https://litellm.parcc.upenn.edu/v1",
         "apiKey": "${LITELLM_KEY}",
         "type": "openai-compatible"
       }
     }
   }
   ```

   (Replace URL with your own proxy if not at Penn. The `${LITELLM_KEY}` placeholder
   is read from the env injected by `with-litellm.sh`.)

## Step 1: Create the dev + evaluator agents

The orchestrator spawns two named openclaw agents:
- `tidybot-dev` — writes/iterates skill code
- `tidybot-evaluator` — reviews exec recordings, returns pass/fail + feedback

Per-target clones (`tidybot-dev-sim-0`, `tidybot-dev-default`, etc.) are
auto-created by the orch's `_ensure_agent_exists()` on first spawn. You only
need to create the two **base** agents manually.

```bash
# Workspace = where openclaw chroots agent file ops
WORKSPACE=$HOME/path/to/Tidybot-Universe/skill-agent-setup/claude-code

openclaw agent create tidybot-dev \
    --model litellm-parcc/deepseek-ai/DeepSeek-V4-Flash \
    --workspace "$WORKSPACE"

openclaw agent create tidybot-evaluator \
    --model litellm-parcc/Qwen/Qwen3.6-35B-A3B \
    --workspace "$WORKSPACE"
```

**Model choice notes**:
- `tidybot-dev` writes code → needs decent reasoning. We use `DeepSeek-V4-Flash`
  (cheap + fast). Stronger options: `anthropic/claude-opus-4-7`, `anthropic/claude-sonnet-4-6`.
  Weak models can hit a death-loop pattern — see
  `memory/feedback_dev_silent_success_misread.md`.
- `tidybot-evaluator` reviews multi-modal evidence (text + images) → use a
  vision-capable model. We use `Qwen3.6-35B-A3B`. Could swap for `Kimi-K2.6` or
  `claude-haiku-4-5` for VLM tasks.

Verify:
```bash
ls ~/.openclaw/agents/tidybot-dev ~/.openclaw/agents/tidybot-evaluator
# both should have AGENTS.md + agent/ + sessions/ subdirs
```

## Step 2: Launch the orchestrator

```bash
# Sim already running on localhost:5500/5555/.../8080 — see ../../README.md

cd $WORKSPACE
conda run -n maniskill --no-capture-output \
  env -u ANTHROPIC_API_KEY \
       LD_PRELOAD="$HOME/miniconda3/envs/maniskill/lib/libstdc++.so.6" \
       PYTHONUNBUFFERED=1 \
  ~/bin/with-litellm.sh \
  python3 agent_orchestrator.py \
      --graph graphs/<your-graph> \
      --harness openclaw \
      [--autonomous]
```

Argument breakdown:
- `env -u ANTHROPIC_API_KEY` — clear Anthropic key so SDK path can't accidentally engage
- `LD_PRELOAD=...libstdc++.so.6` — workaround for Sapien linking issue (pre-existing)
- `PYTHONUNBUFFERED=1` — stream stdout immediately (don't buffer)
- `~/bin/with-litellm.sh` — injects `LITELLM_KEY`, sets `HARNESS=openclaw` default
- `--harness openclaw` — explicit (also defaulted by wrapper)
- `--autonomous` — auto-respawn dev on failure with feedback prompt

`with-litellm.sh` sets `HARNESS=openclaw` by default, so `--harness openclaw`
is redundant when launched through it (kept here for explicitness).

## Step 3: Verify

```bash
# Orch HTTP API up?
curl -sf http://localhost:8766/entries | python3 -m json.tool

# WebSocket up?
curl -sf -i http://localhost:8765 | head -5  # 426 Upgrade Required is fine

# After kicking off /xbot-start:
curl -X POST http://localhost:8766/xbot-start

# Within ~30s, dev agent's session jsonl should appear:
ls -lt ~/.openclaw/agents/tidybot-dev-default/sessions/*.jsonl 2>/dev/null | head -3

# Watch sim activity:
tail -f /tmp/maniskill_server.log | grep -E "plan|target=|cuRobo"
```

Dashboard: `http://localhost:8070/local/` (start via `python3 -m http.server 8070`
in the dashboard repo).

## Multi-target setup

For running the same task across multiple sim instances (different layouts/seeds):

```bash
# Launch sim 0 (default ports) + sim 1 (offset 100) + sim 2 (offset 200)
# See main CLAUDE.md "Multi-Target Testing" section for full launch commands.
```

The orchestrator auto-creates per-target agent clones (`tidybot-dev-sim-0`,
`tidybot-evaluator-sim-1`, etc.) on first spawn — you don't need to create
them manually. They inherit the model + workspace from the base
`tidybot-dev` / `tidybot-evaluator` agents.

## Known quirks

### `--local` mode ignores `--session-id`

OpenClaw's `--local` mode always creates a fresh session per spawn,
regardless of `--session-id` argument. The orchestrator has a
[stale-session detection patch](#stale-session-id-blanks-dashboard-chat-panel)
to handle this gracefully on restart.

See: `memory/reference_openclaw_local_mode.md`.

### Stale `session_id` blanks dashboard chat panel

Symptom: after orch restart, dashboard's "Send hint to agent" panel shows
empty even though dev is actively writing.

Cause: orch persisted `session_id` from a previous run into `graph.json`. On
restart, this stale ID is loaded and passed as `--session-id` to openclaw.
Since openclaw `--local` makes a new session anyway, the orch's tail blocks
forever waiting for a file that won't exist.

Fix: orch detects stale ID and clears it before spawn (in
`agent_orchestrator_openclaw.py` `_run_agent_openclaw`). See:
`memory/project_dashboard_session_id_fix.md`.

### LiteLLM key file permissions

`with-litellm.sh` refuses to run if `~/.litellm-key` is not `chmod 600`.
If the file got world-readable (e.g. after a backup restore), fix with:
```bash
chmod 600 ~/.litellm-key
```

### dev agent stuck on wrong tool / death-loops

If you see dev pivot to `arm.move_to_pose` and ignore `wb.move_to_pose`
(or similar wrong-tool fixation), it's likely a model-judgment issue, not
missing prompt info. See `memory/feedback_dev_silent_success_misread.md`
for diagnosis pattern. The empirical test: switch model to `claude-opus-4-7`
and see if behavior changes.

## Architecture overview

```
agent_orchestrator.py  (--harness openclaw)
        │
        │  spawns subprocess
        ▼
~/bin/with-litellm.sh ──► env LITELLM_KEY=...
        │
        ▼
openclaw agent --local --agent tidybot-dev-default --json -m "<prompt>"
        │
        │  HTTPs to LiteLLM proxy
        ▼
LiteLLM proxy ──► routes to deepseek-ai/DeepSeek-V4-Flash (or chosen model)
        │
        ▼
JSONL streamed back to ~/.openclaw/agents/tidybot-dev-default/sessions/<uuid>.jsonl
        │
        │  orch tails this file, broadcasts to dashboard via WebSocket
        ▼
Dashboard at :8070/local/  ──► shows dev's reasoning + tool calls live
```

## Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| `with-litellm: $KEY_FILE not found` | First-time setup not done | Re-run step 3 in Prerequisites |
| `with-litellm: $KEY_FILE permissions are 644` | Key file not 600 | `chmod 600 ~/.litellm-key` |
| Orch starts but no dev spawns | `tidybot-dev` agent missing | Re-run Step 1, check `~/.openclaw/agents/` |
| `LITELLM_KEY=` empty in subprocess | Wrapper not invoked | Verify launch command goes through `~/bin/with-litellm.sh` |
| Dev agent hits 401 / auth errors | Stale or wrong key | Update `~/.litellm-key`, re-launch |
| Dev session jsonl never appears | openclaw subprocess crashed silently | `tail -f /tmp/orch_autonomous.log` |
| Dashboard chat panel empty | Stale session_id | See "Known quirks" above |

## Related docs

- [`CLAUDE.md`](CLAUDE.md) — Skill planner runtime instructions (what the agent reads when invoked)
- [`../openclaw/README.md`](../openclaw/README.md) — Standalone OpenClaw chat setup (different mode!)
- [`docs/harness-layer-technical-spec.md`](docs/harness-layer-technical-spec.md) — Why harness layer exists, design tradeoffs
