# Service Agent Setup

Your service agent develops the backend components that skills depend on — ML models, APIs, and utility libraries. This directory contains setup options for different agent platforms.

**Important:** Services can talk directly to hardware and system resources, so they require care. Review the setup instructions for your chosen platform to understand the level of autonomy and oversight involved.

## Options

| Platform | Description | Setup |
|----------|-------------|-------|
| [OpenClaw](openclaw/) | Autonomous AI agent with wishlist monitoring, auto-build, systemd deployment, and community sharing | [openclaw/README.md](openclaw/README.md) |
| [Claude Code](claude-code/) | CLI-based AI coding agent with human-in-the-loop review for each change | [claude-code/README.md](claude-code/README.md) |

**Which should I use?**
- **OpenClaw** — fully autonomous: monitors the wishlist, builds services, deploys them, and keeps them running without human intervention. Best for dedicated GPU servers.
- **Claude Code** — human-in-the-loop: you review and approve each change. Best when you want more control over what gets deployed.

## Prerequisites

Before setting up a service agent, you need:

1. **A GPU server** — for running ML models (recommended: NVIDIA GPU with ≥16 GB VRAM, ≥64 GB RAM)
2. **GitHub access** — a GitHub account with push access to the `TidyBot-Services` org
3. **System packages** — Python 3.10+, pip, git, CUDA toolkit (matching your GPU driver)
4. **Familiarity with the services org** — read the [TidyBot-Services](https://github.com/TidyBot-Services) org profile

See the [main README](../README.md) for full setup instructions.
