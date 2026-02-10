# Skill Agent Setup

Your robot's skill agent is the AI that develops, tests, and runs skills on your hardware. This directory contains setup options for different agent platforms.

## Options

| Platform | Description | Setup |
|----------|-------------|-------|
| [OpenClaw](openclaw/) | AI agent platform with workspace templates, skill development workflow, and community sharing | [openclaw/README.md](openclaw/README.md) |

## Prerequisites

Before setting up a skill agent, you need:

1. **A running robot** — the agent server must be accessible (default: `http://localhost:8080`)
2. **Hardware services** — arm server, gripper server, etc. started via `start_robot.sh` or the service manager

See the [main README](../README.md) for full setup instructions.
