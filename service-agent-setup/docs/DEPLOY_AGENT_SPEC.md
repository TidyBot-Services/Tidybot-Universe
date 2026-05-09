# Deploy Agent Specification

A lightweight daemon that runs on each compute node. Skill agents call it to deploy, query, and stop services.

## Overview

```
Skill Agent                    Compute Node (deploy-agent :9000)
    │                                │
    ├── GET /services ──────────────►│  what's running?
    │◄── [{name, host, gpu, ...}] ──┤
    │                                │
    ├── POST /deploy ───────────────►│  start yolo-service
    │   {service.yaml content}       │  → docker pull + run
    │◄── {host: "http://...:8010"} ──┤  → wait for /health
    │                                │
    ├── POST /stop ─────────────────►│  stop yolo-service
    │◄── {ok: true} ────────────────┤
```

## API

### `GET /health`

Deploy-agent's own health check.

```json
{"status": "ok", "hostname": "gpu-server-1", "gpus": 2, "services_running": 3}
```

### `GET /services`

List all running services.

```json
[
  {
    "name": "yolo-service",
    "host": "http://10.0.0.5:8010",
    "port": 8010,
    "gpu": 0,
    "status": "healthy",
    "uptime": 3600,
    "image": "tidybot/yolo-service:0.2.0"
  }
]
```

### `GET /services/{name}`

Single service status. Returns 404 if not deployed.

### `POST /deploy`

Deploy a service. If already running and healthy, returns existing endpoint (idempotent).

**Request:**
```json
{
  "name": "yolo-service",
  "image": "tidybot/yolo-service:0.2.0",
  "port": 8010,
  "gpu": true,
  "vram_gb": 8,
  "env": {"MODEL": "yoloe-11l-seg.pt"},
  "volumes": ["models:/app/models"],
  "health": "/health",
  "ready_timeout": 120
}
```

These fields map directly from `service.yaml`. The skill agent reads the manifest and forwards it.

> **⚠️ Port semantics — read this before deploying:**
>
> `port` is the **container's internal listen port**, not the external host port. Deploy-agent calls `docker run -p <host_port>:<port>` where `host_port` is auto-assigned by `_pick_port(req.port)` (defaults to `req.port` if free, else next available).
>
> If your image listens on a different port internally and you set `port` to something else, the docker mapping will publish a port that nothing inside the container is listening on → external connection refused → `/health` times out at `ready_timeout` seconds, and you get:
> ```json
> {"detail": "Service '<name>' failed health check after Ns"}
> ```
>
> **Two safe patterns:**
>
> 1. **Match the image's `EXPOSE` directive** — set `port` to whatever the image listens on internally (e.g. `port: 80` for a stock nginx, `port: 8000` for a stock FastAPI app). `host_port` is auto-picked by deploy-agent.
>
> 2. **Override with `command`** — pass a CLI argument that makes the image listen on `port` internally. Example:
>    ```json
>    {
>      "name": "smoke-test",
>      "image": "traefik/whoami:latest",
>      "port": 18999,
>      "command": "--port 18999",
>      "health": "/"
>    }
>    ```
>    Now the container listens on 18999 internally AND the host publishes 18999, so external/internal align.
>
> A self-contained probe that exercises the full pipeline (with the correct `command`) is at `service-agent-setup/probe_pipeline.sh`.

**Response (success):**
```json
{
  "ok": true,
  "name": "yolo-service",
  "host": "http://10.0.0.5:8010",
  "gpu": 0,
  "status": "healthy",
  "already_running": false
}
```

**Response (already running):**
```json
{
  "ok": true,
  "name": "yolo-service",
  "host": "http://10.0.0.5:8010",
  "gpu": 0,
  "status": "healthy",
  "already_running": true
}
```

**GPU assignment:** Deploy-agent picks the least-loaded GPU that has enough free VRAM. Returns error if no GPU available.

### `POST /stop`

Stop and remove a service container.

**Request:**
```json
{"name": "yolo-service"}
```

### `GET /gpus`

GPU status for the node.

```json
[
  {"id": 0, "name": "RTX 4090", "vram_total_gb": 24, "vram_used_gb": 8, "services": ["yolo-service"]},
  {"id": 1, "name": "RTX 4090", "vram_total_gb": 24, "vram_used_gb": 0, "services": []}
]
```

## Port allocation

Deploy-agent manages ports automatically:
- Each service declares its preferred port in `service.yaml`
- If that port is taken, deploy-agent assigns the next available port
- The actual port is always returned in the response

## Container naming

Containers are named `tidybot-{service-name}` (e.g., `tidybot-yolo-service`). This makes them easy to identify in `docker ps`.

## Persistence

Running service state is stored in `/var/lib/deploy-agent/services.json`. On restart, deploy-agent checks which containers are still running and reconciles.

## Discovery

Skill agents need to know compute node addresses. Options:

1. **Environment variable** — `COMPUTE_NODES=http://10.0.0.5:9000,http://10.0.0.6:9000`
2. **Agent server config** — agent server exposes known compute nodes at `GET /compute/nodes`
3. **mDNS** — deploy-agent advertises via `_tidybot-compute._tcp`

Start with option 1. Upgrade later if needed.

## Running the deploy-agent

```bash
pip install deploy-agent
deploy-agent --port 9000
```

Or via Docker:

```bash
docker run -d \
  --name deploy-agent \
  -p 9000:9000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/deploy-agent:/var/lib/deploy-agent \
  tidybot/deploy-agent:latest
```

The deploy-agent needs access to the Docker socket to manage containers.
