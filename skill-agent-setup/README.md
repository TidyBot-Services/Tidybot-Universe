# Skill Agent Setup

Your robot's skill agent is the AI that develops, tests, and runs skills on your hardware. This directory contains setup options for different ways of running the agent.

## Two modes — pick one

| Mode | What it is | Best for | Setup doc |
|------|------------|----------|-----------|
| **Standalone OpenClaw chat** | You chat directly with an OpenClaw agent. The agent reads your robot's docs, picks skills from a catalog, develops new ones interactively. No orchestrator. | Exploratory development, single skill at a time, human-in-the-loop | [`openclaw/README.md`](openclaw/README.md) |
| **Orchestrator + harness** | `agent_orchestrator.py` runs a skill DAG end-to-end. Spawns dev + evaluator agents per skill, ground-truth tests root skills via sim. Two harness backends: `claude-sdk` (Anthropic) or `openclaw` (any LLM via LiteLLM/Ollama). | Batch task evaluation, autonomous retry loops, multi-target benchmarking | [`claude-code/CLAUDE.md`](claude-code/CLAUDE.md) (planner instructions) + [`claude-code/CLAUDE-OPENCLAW-HARNESS.md`](claude-code/CLAUDE-OPENCLAW-HARNESS.md) (when using `--harness openclaw`) |

The two modes are independent — pick whichever fits your workflow. They don't share state. Pick standalone if you want a chat interface; pick orchestrator if you want autonomous DAG execution.

## Harness choice (orchestrator mode only)

If you're using the orchestrator, you also choose a **harness**:

```bash
python3 agent_orchestrator.py --graph <...> --harness claude-sdk    # default, Anthropic SDK
python3 agent_orchestrator.py --graph <...> --harness openclaw      # OpenClaw subprocess (any model via LiteLLM)
```

| Harness | Backend | API key | Model options |
|---------|---------|---------|---------------|
| `claude-sdk` | `ClaudeSDKClient` in-process | `ANTHROPIC_API_KEY` env | Anthropic Claude only |
| `openclaw` | `openclaw agent --local` subprocess | LiteLLM key file + wrapper | Any model openclaw knows (DeepSeek, Qwen, Ollama, Anthropic, ...) |

Setup details for openclaw harness: [`claude-code/CLAUDE-OPENCLAW-HARNESS.md`](claude-code/CLAUDE-OPENCLAW-HARNESS.md).

## Prerequisites (both modes)

Before setting up either mode, you need:

1. **A running robot** — the agent server must be accessible (default: `http://localhost:8080`)
2. **Hardware services** — arm server, gripper server, etc. started via `start_robot.sh` or the service manager
3. **(Sim mode)** ManiSkill / RoboCasa sim launched — see [main README](../README.md)

See the [main README](../README.md) for full sim/hardware setup.
