# Tidybot Service Development

You are developing services for the Tidybot Universe platform.

## Context

- **Services org:** https://github.com/TidyBot-Services — one repo per service
- **Skills org:** https://github.com/tidybot-skills — the skills that depend on your services
- **Agent server:** The unified API layer between AI agents and robot hardware (default: `http://localhost:8080`)
- **Deploy-agent:** HTTP daemon on compute nodes that manages service lifecycle (default: `http://<compute-node>:9000`)

## Service Types

Services fall into these categories:

- **Hardware drivers** — arm servers, gripper servers, sensor interfaces
- **AI/ML models** — object detection, pose estimation, segmentation, grasp detection
- **Utility libraries** — coordinate transforms, trajectory planning, common helpers
- **Platform services** — agent server extensions, monitoring, logging

## How Services Work

Services are Docker containers running on GPU compute nodes, managed by the deploy-agent.

1. **Build:** Developer creates a service repo with `main.py`, `client.py`, `service.yaml`, `Dockerfile`
2. **Image:** Build and test the Docker image on the compute node (SSH in, iterate until it works)
3. **Deploy:** Skill agents call `POST /deploy` on the deploy-agent with the `service.yaml` fields
4. **Use:** Deploy-agent returns a host URL; skill agents use `client.py` with that URL

### Key Specs

| Spec | Description |
|------|-------------|
| `SERVICE_MANIFEST_SPEC.md` | How to write `service.yaml` |
| `CLIENT_SDK_SPEC.md` | How to write `client.py` (urllib only, no external deps) |
| `DEPLOY_AGENT_SPEC.md` | Deploy-agent HTTP API reference |

### Service Repo Structure

```
<service-name>/
├── service.yaml        # Deploy manifest (image, port, GPU requirements)
├── client.py           # Client SDK (urllib only, per CLIENT_SDK_SPEC)
├── main.py             # FastAPI server
├── Dockerfile          # Container build
├── requirements.txt    # Pinned dependencies
└── README.md           # API docs, usage, hardware requirements
```

## Development Workflow

1. Read the service specs for contribution guidelines
2. Each service gets its own repo in the TidyBot-Services org
3. Build and test the Docker image locally or on the compute node
4. Test against the agent server before pushing
5. Push to GitHub so skill agents can discover and deploy it

## Safety

Services run below the agent server safety layer — they talk directly to hardware. Be careful with:

- Motor commands and force limits
- GPIO and electrical interfaces
- System resources (ports, file handles, processes)
- Network exposure (bind to localhost unless explicitly needed)

Always test with the robot in a safe configuration. Use the agent server's safety envelope when available.
