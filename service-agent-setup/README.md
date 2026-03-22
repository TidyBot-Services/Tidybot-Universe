# Compute Node & Service Development Setup

Services are Docker containers that run on GPU compute nodes. The **deploy-agent** daemon manages their lifecycle — deploying, monitoring, and stopping containers via a simple HTTP API. **Skill agents** deploy and use services directly; there is no separate "service agent."

## Architecture

```
Skill Agent                    Compute Node (deploy-agent :9000)
    |                                |
    |-- GET /services ------------->|  what's running?
    |<-- [{name, host, gpu, ...}] --|
    |                                |
    |-- POST /deploy -------------->|  start grasp-service
    |   {from service.yaml}        |  -> find image locally
    |                                |  -> assign GPU + port
    |                                |  -> start container
    |<-- {host: "http://...:8006"} -|  -> wait for /health
    |                                |
    |-- POST /stop ---------------->|  stop grasp-service
    |<-- {ok: true} ----------------|
```

## What's In This Directory

```
service-agent-setup/
├── README.md               # You are here
├── deploy-agent/           # Deploy-agent daemon (runs on compute nodes)
│   ├── server.py
│   └── requirements.txt
├── claude-code/            # CLAUDE.md for human developers building services
│   ├── README.md
│   └── CLAUDE.md
└── docs/                   # Specs referenced by skill agents and humans
    ├── DEPLOY_AGENT_SPEC.md
    ├── SERVICE_MANIFEST_SPEC.md
    └── CLIENT_SDK_SPEC.md
```

## Who Does What

| Role | Responsibility |
|------|---------------|
| **Humans** | Build new services (main.py, client.py, Dockerfile), debug CUDA/GPU issues, push to GitHub |
| **Skill agents** | Deploy services via deploy-agent API, use them via client.py |
| **Deploy-agent** | Manages Docker containers, GPU assignment, health monitoring |

## Setup

### 1. Deploy-agent (one-time per compute node)

SSH into your GPU server and start the deploy-agent:

```bash
pip install -r deploy-agent/requirements.txt
python deploy-agent/server.py --port 9000
```

The user running deploy-agent needs Docker access (`sudo usermod -aG docker $USER`).

### 2. Build service images (one-time per service)

SSH into the compute node, clone the service repo, and build:

```bash
git clone https://github.com/TidyBot-Services/<service-name>.git
cd <service-name>
docker build -t tidybot/<service-name>:0.1.0 .
```

This is where you debug CUDA compatibility, missing dependencies, model weights, etc. Once the image builds and runs, skill agents can deploy it anytime.

### 3. Skill agents deploy via HTTP

No SSH needed — skill agents call the deploy-agent API:

```bash
curl -X POST http://<compute-node>:9000/deploy \
  -H "Content-Type: application/json" \
  -d '{"name": "grasp-service", "image": "tidybot/grasp-service:0.1.0", "port": 8006, "gpu": true, "vram_gb": 2}'
```

## Specs

- [Deploy Agent Spec](docs/DEPLOY_AGENT_SPEC.md) — API reference
- [Service Manifest Spec](docs/SERVICE_MANIFEST_SPEC.md) — `service.yaml` format
- [Client SDK Spec](docs/CLIENT_SDK_SPEC.md) — client.py standards

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU VRAM | 16 GB | 24+ GB |
| RAM | 64 GB | 128+ GB |
| Disk | 500 GB | 1+ TB |
| CUDA | 12.0+ | 12.8+ |
| Docker | 24+ | 29+ |
