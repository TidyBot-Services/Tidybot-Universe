# 0004 — Internet-Deployable Services Wrapped as Skills

**Status:** Accepted (deploy-agent live on remote GPU; verified 2026-05-09)
**Date:** Project inception (whitepaper section 2)

## Context

Robot agents need access to:
- Perception models (YOLO, GroundedSAM, foundation stereo, etc.)
- Grasping models (GraspGen, contact-graspnet, lightweight grasp net)
- Planning services (nav-mapping, RTAB-SLAM)
- More to come

These models are heavy (8GB+ VRAM) and not always sitting on the robot's local machine. They live on shared GPU compute nodes. Reinventing them inside the robot codebase wastes the hundreds of person-years of upstream work.

## Decision

**Services run as containerized HTTP APIs on remote GPU nodes**, deployed and discovered via a **deploy-agent daemon** (port 9000 on each compute host). Skills wrap service clients as plug-in tools.

Three components:

1. **`services_wishlist`** — GitHub-tracked coordination repo with `catalog.json` (what's available) and `wishlist.json` (what's requested but not yet deployed).
2. **Deploy-agent** (`service-agent-setup/deploy-agent/server.py`) — runs on each compute node. HTTP API:
   - `GET /services` — list running
   - `POST /deploy {name, image, port, gpu, ...}` — pull docker image + run + health check
   - `POST /stop {name}` — stop + remove container
   - `GET /gpus` — GPU usage
3. **Client SDKs** — each service repo has a `client.py` that the agent imports. Catalog entry includes the URL. Sync via `sync_catalog.sh` cron pulls latest client.py to local `service_clients/` dir.

Skill agent flow:
```
skill needs YOLO → reads catalog.json → finds yolo-service at 158.130.109.188:8000 →
imports the cached client.py → calls client.detect(...) → HTTPS to remote service
```

If a needed service doesn't exist yet, skill agent adds it to `wishlist.json`. A human (today) or future "service agent" implements it, deploys via deploy-agent, updates catalog.

## Consequences

- **Massive reuse**: agents tap into existing model ecosystem instead of re-implementing.
- **GPU isolation**: heavy compute stays on dedicated nodes; the robot's mini-PC isn't burdened.
- **Service availability is a separate axis**: skills don't break when a model is added; new capabilities appear via catalog.
- **Hard dependency on a separate person/process for new-service deployment**: at present, "service agent" is `steve` (a human). The whitepaper envisions an AI service agent but it's not implemented.
- **Network is on the critical path**: outages or latency on the compute node degrade skill performance. Mitigated by health check + alerting in deploy-agent.

## Verification

End-to-end probe 2026-05-09 (`service-agent-setup/probe_pipeline.sh`):
- POST /deploy `traefik/whoami` to remote GPU node
- docker pull + run + health check pass (~30s including image pull)
- GET on deployed container returns expected HTTP response
- POST /stop cleans up

3 production services currently running on `158.130.109.188`:
- `lightweight-grasp-service:8006`
- `contact-graspnet:8011`
- `yolo-service:18000`

## Alternatives Considered

- **All services on robot's local machine**: rejected — no GPU on mini PC, would need to ship dedicated GPU box per robot.
- **One monolithic "model server" per robot**: rejected — couples model lifecycle to robot lifecycle, harder to update independently.
- **Skill agents write models from scratch**: rejected — the whitepaper's whole "save agent cost by reusing the hundreds of years of engineering" point.

## Related

- `modules/deploy-agent.md` — daemon details, port semantics gotcha
- `service-agent-setup/docs/DEPLOY_AGENT_SPEC.md` — formal API spec
- `services_wishlist/` repo — coordination hub
