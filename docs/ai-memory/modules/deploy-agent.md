# Module — deploy-agent

A lightweight daemon that runs on each GPU compute node. Skill agents call it to deploy, query, and stop Docker-based services.

## What it does

- Listens on **port 9000** (e.g. `158.130.109.188:9000` for the Penn GPU node)
- Manages docker containers per service (`tidybot-<service-name>`)
- Pulls images on demand, assigns GPU + port, runs container, waits for health check
- Tracks state on disk (`/var/lib/deploy-agent/services.json`) so it can reconcile after restart

## Key code paths

| Path | Role |
|---|---|
| `service-agent-setup/deploy-agent/server.py` | FastAPI app, all endpoints |
| `service-agent-setup/docs/DEPLOY_AGENT_SPEC.md` | Formal API spec (port semantics, payloads, examples) |
| `service-agent-setup/probe_pipeline.sh` | End-to-end self-cleaning feasibility probe |
| `service-agent-setup/PIPELINE_PROBE_RESULT.md` | Last verification report (deleted by user; re-runnable via probe script) |

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Daemon liveness + GPU info |
| `GET /services` | List running services with status |
| `GET /services/{name}` | Specific service details |
| `POST /deploy` | Deploy / re-deploy. Idempotent (returns existing if healthy). |
| `POST /stop` | Stop + remove container |
| `GET /gpus` | GPU usage |

## ⚠️ Port semantics gotcha (critical)

The `port` field in `POST /deploy` is the **container's internal listen port**, NOT the external host port. Deploy-agent calls `docker run -p host_port:port` where `host_port` is auto-picked by `_pick_port(req.port)`.

If your image listens on a different port internally and you set `port: 18999`, docker maps `host_port:18999` — but nothing inside listens on 18999 → external connection refused → health check times out.

**Two safe patterns**:

1. **Match image's EXPOSE**: `port: 80` for stock nginx, `port: 8000` for stock FastAPI. `host_port` is auto-picked.

2. **Override with `command`**: pass CLI arg that makes container listen on `port` internally:
   ```json
   {
     "name": "smoke-test",
     "image": "traefik/whoami:latest",
     "port": 18999,
     "command": "--port 18999",
     "health": "/"
   }
   ```

Documented in `service-agent-setup/docs/DEPLOY_AGENT_SPEC.md` (port caveat section added 2026-05-09).

## Where deploy-agents run

| Host | Port | Status (last verified 2026-05-09) |
|---|---|---|
| `localhost:9000` | 9000 | Alive |
| `158.130.109.188:9000` (Penn GPU node) | 9000 | Alive — hosts 3 production services |

Currently-deployed production services on Penn GPU node:
- `lightweight-grasp-service:8006` (GR-ConvNet v2, ~20ms inference)
- `contact-graspnet:8011`
- `yolo-service:18000`

(More services discoverable via `curl http://158.130.109.188:9000/services`.)

## Verify it works

```bash
# Self-cleaning end-to-end probe — pulls + deploys + calls + tears down
bash service-agent-setup/probe_pipeline.sh
```

The script:
1. Hits `GET /health` (daemon alive + GPU count)
2. `GET /services` (baseline)
3. `POST /deploy` of `traefik/whoami` to port 18999 with `command: "--port 18999"`
4. `GET /services` (verify registered)
5. `curl :18999/` (deployed container responding)
6. `POST /stop`
7. `GET /services` (cleanup verified)

Uses timestamped service name (`feasibility-probe-<unix_ts>`) and `trap cleanup EXIT` to always tear down.

## Where this fits in the bigger picture

- **`services_wishlist`** (separate repo) — coordination hub. `wishlist.json` for requests, `catalog.json` for the menu of available services.
- **Deploy-agent** (this module) — the executor that brings catalog entries to life.
- **Skill agents** (the orchestrator's dev/eval) — consumers. They read catalog, call services.
- **Service authors** (today: human "steve") — write the service, deploy via deploy-agent, update catalog.

The whitepaper envisions an AI service agent that closes this loop, but it's not implemented today. See `decisions/0004-internet-services-via-deploy-agent.md`.

## Common pitfalls

- **Skill code uses hardcoded service URLs** (e.g. `GRASPGEN_SERVER_URL=http://10.102.245.84:8006`) instead of catalog lookup. Works but couples agent to deployment topology. Long-term: agents should read `services_wishlist/catalog.json` and route by name.
- **Port collision with existing service** if you reuse a name. `_pick_port` finds a free port but if you've already deployed `feasibility-probe-X` and re-deploy, it returns the existing (idempotent).

## Related

- `decisions/0004-internet-services-via-deploy-agent.md`
- `service-agent-setup/docs/DEPLOY_AGENT_SPEC.md`
- `service-agent-setup/probe_pipeline.sh`
- `services_wishlist/` (separate repo) — coordination
- `~/.claude/projects/.../memory/project_deploy_pipeline_verified.md` — private notes on the verification run
- `~/.claude/projects/.../memory/reference_service_catalog_unused.md` — about the OTHER catalog system (port 8090 SSH scanner, currently unused)
