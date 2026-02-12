# Service Agent Setup

Your service agent develops the backend components that skills depend on — hardware drivers, SDKs, APIs, and utility libraries. This directory contains setup options for different agent platforms.

**Important:** Services run *below* the agent server safety layer. They talk directly to hardware and system resources, so they require more care and human oversight than skill agents. Always review changes before running them.

## Options

| Platform | Description | Setup |
|----------|-------------|-------|
| [Claude Code](claude-code/) | CLI-based AI coding agent with human-in-the-loop review for each change | [claude-code/README.md](claude-code/README.md) |

## Prerequisites

Before setting up a service agent, you need:

1. **A running robot** — the agent server must be accessible (default: `http://localhost:8080`)
2. **Hardware services** — arm server, gripper server, etc. started via `start_robot.sh` or the service manager
3. **Familiarity with the services org** — read the [TidyBot-Services](https://github.com/TidyBot-Services) org profile

See the [main README](../README.md) for full setup instructions.
