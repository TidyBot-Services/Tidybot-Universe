# OpenClaw Setup — Service Development Agent

[OpenClaw](https://openclaw.ai) can be used as a service development assistant on your GPU server. It helps build, test, and maintain backend ML services for the TidyBot ecosystem.

> **Note:** Service deployment is now handled by the [deploy-agent](workspace/docs/DEPLOY_AGENT_SPEC.md) — a lightweight HTTP daemon on each compute node. OpenClaw's role is service *development*, not lifecycle management.

## Quick Start

```bash
# Add service agent to an existing OpenClaw workspace
./setup.sh

# Or start fresh (wipes workspace, sessions, memory — keeps auth)
./setup.sh --fresh
```

Once complete, open a chat with your agent via `openclaw dashboard`.

## What's Included

```
workspace/
├── MISSION.md          # Service development mission and workflow
├── HEARTBEAT.md        # Periodic service health checks (via deploy-agent)
├── BOOTSTRAP.md        # First-run instructions (agent deletes after setup)
├── SOUL.md             # Service agent personality
├── TOOLS.md            # Docker, deploy-agent, GPU monitoring patterns
├── skills/
│   └── tidybot-service-dev/
│       └── SKILL.md    # Step-by-step guide for building a new service
└── docs/
    ├── CLIENT_SDK_SPEC.md      # Client SDK specification
    ├── DEPLOY_AGENT_SPEC.md    # Deploy-agent API reference
    └── SERVICE_MANIFEST_SPEC.md # service.yaml format
```

## What Happens Next

Once your agent is running, it will:

1. Introduce itself and get to know you
2. Read the mission and learn its role in the TidyBot ecosystem
3. Help you build new services:
   - Create a GitHub repo under `TidyBot-Services`
   - Build a FastAPI service + client SDK + Dockerfile
   - Test locally, debug CUDA/dependency issues
   - Build the Docker image
4. Services are deployed and managed via the deploy-agent

## Service Architecture

Each service repo follows this structure:

```
<service-name>/
├── service.yaml        # Deploy manifest (image, port, GPU, health)
├── client.py           # Client SDK (urllib only, no requests)
├── main.py             # FastAPI server
├── Dockerfile          # Container build
├── requirements.txt    # Pinned dependencies
└── README.md           # Usage docs
```

Services run as Docker containers managed by the deploy-agent, with automatic GPU assignment and health monitoring.

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU VRAM | 16 GB | 24+ GB |
| RAM | 64 GB | 128+ GB |
| Disk | 500 GB | 1+ TB |
| CPU cores | 16 | 32+ |
| CUDA | 12.0+ | 12.8+ |
