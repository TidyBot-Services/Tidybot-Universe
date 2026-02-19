# Tidybot Service Development

You are developing services for the Tidybot Universe platform.

## Context

- **Services org:** https://github.com/TidyBot-Services — one repo per service
- **Services wishlist:** https://github.com/TidyBot-Services/services_wishlist — open requests for new services
- **Skills org:** https://github.com/tidybot-skills — the skills that depend on your services
- **Agent server:** The unified API layer between AI agents and robot hardware (default: `http://localhost:8080`)

## Service Types

Services fall into these categories:

- **Hardware drivers** — arm servers, gripper servers, sensor interfaces
- **AI/ML models** — object detection, pose estimation, segmentation
- **Utility libraries** — coordinate transforms, trajectory planning, common helpers
- **Platform services** — agent server extensions, monitoring, logging

## Service Catalog Workflow

Services are managed via a shared git repo (`backend_wishlist/`) and a backend compute server.

1. **Request:** Add service to `backend_wishlist/wishlist.json`
2. **Setup:** Backend agent ("steve") on the compute server (158.130.109.188) sets up the service
3. **Publish:** Backend agent updates `backend_wishlist/catalog.json` with host, endpoints, and client SDK URL
4. **Sync:** `sync_catalog.sh` (cron, every 30 min) pulls `catalog.json` and downloads client SDKs into `tidybot-agent-server/service_clients/<name>/`
5. **Use:** Agent server imports service clients for code execution

### Current Services in Catalog

- `yolo-detection` — YOLO object detection
- `grounded-sam2` — Grounded SAM2 segmentation
- `graspgen` — Grasp pose generation
- `foundation-stereo` — Foundation Stereo depth estimation
- `nav-mapping` — Navigation mapping
- `realsense-slam` — RealSense SLAM
- `local-obstacle-avoidance` — Local obstacle avoidance
- `lightweight-grasp-net` — Lightweight grasp network

### Key Files

| File | Description |
|------|-------------|
| `backend_wishlist/wishlist.json` | Service requests for the backend agent |
| `backend_wishlist/catalog.json` | Published service catalog (host, endpoints, client SDK) |
| `sync_catalog.sh` | Cron script to pull catalog and download client SDKs |
| `tidybot-agent-server/service_clients/<name>/client.py` | Downloaded client SDK for each service |

## Development Workflow

1. Read the wishlist `RULES.md` for contribution guidelines
2. Each service gets its own repo in the TidyBot-Services org
3. Services must include a README with setup instructions and API documentation
4. Test locally against the agent server before pushing
5. Update the wishlist item when the service is ready

## Safety

Services run below the agent server safety layer — they talk directly to hardware. Be careful with:

- Motor commands and force limits
- GPIO and electrical interfaces
- System resources (ports, file handles, processes)
- Network exposure (bind to localhost unless explicitly needed)

Always test with the robot in a safe configuration. Use the agent server's safety envelope when available.
