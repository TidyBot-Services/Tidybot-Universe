# Service Manifest Specification

Every deployable service MUST include a `service.yaml` at the repo root. This manifest tells skill agents everything they need to deploy and use the service.

## Example

```yaml
name: yolo-service
version: 0.2.0
description: YOLO-E object detection and segmentation

requires:
  gpu: true
  vram_gb: 8
  ram_gb: 4

deploy:
  image: tidybot/yolo-service:0.2.0
  port: 8010
  env:
    MODEL: yoloe-11l-seg.pt
  volumes:
    - models:/app/models
  health: /health
  ready_timeout: 120

client: client.py
```

## Fields

### Required

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Service name, matches repo name |
| `version` | string | Semver |
| `description` | string | One-line description |
| `deploy.image` | string | Docker image (registry/name:tag) |
| `deploy.port` | int | Port the service listens on inside the container |
| `deploy.health` | string | Health check endpoint path |
| `client` | string | Path to client SDK file (relative to repo root) |

### Optional

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `requires.gpu` | bool | false | Needs a GPU |
| `requires.vram_gb` | int | 0 | Minimum GPU VRAM in GB |
| `requires.ram_gb` | int | 2 | Minimum system RAM in GB |
| `deploy.env` | map | {} | Environment variables passed to container |
| `deploy.volumes` | list | [] | Named volumes (name:mountpoint) |
| `deploy.ready_timeout` | int | 60 | Seconds to wait for health check after start |
| `deploy.runtime` | string | nvidia | Docker runtime (`nvidia` if `requires.gpu`, else default) |
| `deploy.command` | string | (image default) | Override container command |

## How skill agents use this

1. **Select** — agent browses service repos, reads `service.yaml` + `client.py`
2. **Check** — agent calls `GET /services` on compute node's deploy-agent to see if already running
3. **Deploy** — if not running, agent sends `service.yaml` content to `POST /deploy`
4. **Use** — deploy-agent returns `{"host": "http://10.0.0.5:8010"}`, agent uses `client.py` with that host

## Relationship to CLIENT_SDK_SPEC

`service.yaml` tells the agent how to deploy. `client.py` (per CLIENT_SDK_SPEC) tells the agent how to use it. Both live in the same repo.

```
yolo-service/
├── service.yaml    # deploy manifest (this spec)
├── client.py       # client SDK (CLIENT_SDK_SPEC)
├── main.py         # server implementation
├── requirements.txt
├── Dockerfile
└── README.md
```
