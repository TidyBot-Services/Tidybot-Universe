# Agent Server Setup

The agent server is the unified API layer between AI agents and robot hardware. It provides safety guardrails (rewind, safety envelope, lease system, code execution sandbox) so that skill agents can freely experiment without damaging hardware.

## Quick Start

```bash
git clone https://github.com/TidyBot-Services/agent_server.git
cd agent_server
pip install -r requirements.txt
python3 server.py --dry-run  # simulated mode, no hardware needed
```

The API is available at `http://localhost:8080`. The web dashboard is at `http://localhost:8080/services/dashboard`.

## Service Discovery

Skill agents discover available services by querying the **deploy-agent** on compute nodes:

```bash
# List running services
curl http://<compute-node>:9000/services

# Deploy a service if not running
curl -X POST http://<compute-node>:9000/deploy \
  -H "Content-Type: application/json" \
  -d '{"name": "grasp-service", "image": "tidybot/grasp-service:0.1.0", "port": 8006, "gpu": true}'
```

Set the compute node address via environment variable:

```bash
export COMPUTE_NODES=http://<compute-node>:9000
```

## What's Included

```
agent-server-setup/
├── README.md           # You are here
├── setup.sh            # Agent server setup script
└── sync_catalog.sh     # Legacy catalog sync (deprecated — use deploy-agent)
```
