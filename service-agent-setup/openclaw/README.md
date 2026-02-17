# OpenClaw Setup — Service Agent

[OpenClaw](https://openclaw.ai) is an AI agent platform that runs on your GPU server and automatically builds backend ML services for the TidyBot ecosystem. The service agent monitors the [services wishlist](https://github.com/TidyBot-Services/services_wishlist) and deploys FastAPI services with client SDKs that skill agents can consume.

## Quick Start

```bash
# Add service agent to an existing OpenClaw workspace
./setup.sh

# Or start fresh (wipes workspace, sessions, memory — keeps auth)
./setup.sh --fresh
```

**Integrate mode** (default) adds service agent files and patches to your existing workspace without touching your sessions or memory.

**Fresh mode** (`--fresh`) wipes the workspace, sessions, and memory, regenerates default OpenClaw files, then applies service agent additions. Auth and config are preserved.

Both modes will:
- Install OpenClaw and run onboarding if needed
- Copy service agent files (MISSION.md, HEARTBEAT.md) to `~/.openclaw/workspace/`
- Patch AGENTS.md with service agent session checklist items
- Create the wishlist-monitor cron job (hourly polling)
- Restart the OpenClaw gateway

Once complete, open a chat with your agent via `openclaw dashboard`.

## What's Included

These files are service-agent-specific additions to the OpenClaw workspace. The default OpenClaw files (AGENTS.md, SOUL.md, TOOLS.md, IDENTITY.md, USER.md, BOOTSTRAP.md) are created during onboarding — `setup.sh` patches AGENTS.md with service agent additions rather than replacing it.

```
workspace/
├── MISSION.md          # Service agent mission and build workflow
├── HEARTBEAT.md        # Periodic service health checks
├── BOOTSTRAP.md        # First-run instructions (agent deletes after setup)
├── SOUL.md             # Service agent personality (replaces default)
├── TOOLS.md            # Systemd patterns, port allocation, GPU monitoring, gh api patterns
├── skills/
│   └── tidybot-service-dev/
│       └── SKILL.md    # Step-by-step guide for building a new service
└── docs/
    └── CLIENT_SDK_SPEC.md  # Client SDK specification for all services
```

## What Happens Next

Once your agent is running, it will:

1. Introduce itself and get to know you
2. Read the mission and learn its role in the TidyBot ecosystem
3. Set up the wishlist-monitor cron job (polls hourly for new requests)
4. When a new wishlist item appears:
   - Claim it (set status to `building`)
   - Create a GitHub repo under `TidyBot-Services`
   - Build a FastAPI service + client SDK + README
   - Deploy as a systemd unit on the server
   - Update `catalog.json` with service details
   - Mark the wishlist item as `done`
5. Maintain running services (health checks, restarts)

## Configuration

The setup script creates a cron job that polls the wishlist repo hourly. You can adjust:

- **Poll frequency** — edit the cron job via OpenClaw dashboard or `/cron` command
- **Port range** — services are assigned ports starting at 8000 (configured in MISSION.md)
- **GPU allocation** — the agent auto-detects available CUDA devices

## Service Architecture

Each deployed service follows this structure:

```
<service-name>/
├── server.py           # FastAPI app (uvicorn)
├── client.py           # Client SDK (urllib only, no requests)
├── requirements.txt    # Pinned dependencies
├── README.md           # Usage docs
└── test_service.py     # Basic tests
```

Services run as systemd units (`tidybot-<name>.service`) for automatic restart and boot persistence.

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU VRAM | 16 GB | 24+ GB |
| RAM | 64 GB | 128+ GB |
| Disk | 500 GB | 1+ TB |
| CPU cores | 16 | 32+ |
| CUDA | 11.8+ | 12.0+ |
