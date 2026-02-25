<p align="center">
  <a href="https://tidybot-services.github.io/">
    <img src="banner.png" alt="Tidybot Universe" width="100%" />
  </a>
</p>

# Tidybot Universe

## How It Works

1. **You add to your wishlist** — tell your agent what you want the robot to do
2. **Your agent develops skills** — Python scripts that run on the robot hardware, contributed to the [Skills](https://github.com/tidybot-skills) org
3. **Skills deploy services** — if a skill needs a GPU model (YOLO, grasp detection, etc.), the skill agent deploys it on a compute node via the [deploy-agent](service-agent-setup/)
4. **Services are shared** — each service is a Docker image with a `service.yaml` manifest and `client.py` SDK, shared in the [Services](https://github.com/TidyBot-Services) org
5. **Everyone benefits** — skills and services are shared across the community via GitHub, so every robot gets better

## Why This Works: The Agent Server

The key enabler is the **agent server** — a unified API layer that sits between AI agents and the robot hardware. It provides:

- **Rewind** — every movement is recorded and can be reversed. If a skill crashes the arm into something, the agent (or you) can rewind to undo it. This makes hardware testing for agents as safe as software testing.
- **Safety envelope** — workspace bounds, collision detection, and automatic error recovery
- **Lease system** — one agent at a time, clean handoffs
- **Code execution sandbox** — skills submit Python code that runs on the hardware. Broken or harmful code gets caught by the safety layer, not by your robot.

Because of these guardrails, your agent can freely experiment with skills — try things, fail, rewind, try again — without you worrying about damaging hardware.

## The Ecosystem

| | Skills | Agent Server | Services |
|---|---|---|---|
| **What** | Robot behaviors (pick up X, check door, wave hello) | Unified API layer with safety guardrails | GPU models, APIs, and drivers that skills depend on |
| **Who builds** | Your skill agent | Provided — [you set it up](agent-server-setup/) | Humans develop, deploy-agent manages lifecycle |
| **One repo =** | One skill | One server | One service (with `service.yaml` + `client.py` + `Dockerfile`) |
| **Examples** | `pick-up-banana`, `count-people-in-room`, `wave-hello` | [agent_server](https://github.com/TidyBot-Services/agent_server) | grasp detection, YOLO, SAM2, depth estimation |
| **Org** | [tidybot-skills](https://github.com/tidybot-skills) | [TidyBot-Services](https://github.com/TidyBot-Services) | [TidyBot-Services](https://github.com/TidyBot-Services) |

### Hardware Flexibility

The platform isn't limited to one robot. Different people can bring different hardware — different arms, grippers, bases, sensors. That's the point of the services org: each hardware component has its own service, and skills talk to them through a common API. Swap out a Franka arm for a UR5? Write a new arm service, same skill code works.

## Getting Started

### 1. Set up your robot and agent server

You need a robot with the agent server running. The reference setup is a Franka Panda arm on a mobile base with a Robotiq gripper, but any hardware with matching services will work.

**Clone the repo and install:**

```bash
git clone https://github.com/TidyBot-Services/agent_server.git
cd agent_server
pip install -r requirements.txt
```

**Try it without hardware (dry-run mode):**

```bash
python3 server.py --dry-run
```

This starts the API server with simulated backends — leases, code execution, the dashboard all work, but no hardware moves. The API is available at `http://localhost:8080`.

**With hardware** — the agent server expects to live inside a `tidybot_army/` workspace alongside sibling repos for the arm, base, gripper, and camera servers. See the [agent_server repo](https://github.com/TidyBot-Services/agent_server) for the full layout, hardware setup, and environment variables.

**Verify it's running:**

```bash
curl http://localhost:8080/health
```

You should see `"status": "ok"` with backend connectivity. The web dashboard is at `http://localhost:8080/services/dashboard`.

### 2. Set up a compute node (deploy-agent)

The **deploy-agent** is a lightweight daemon that runs on each GPU server. Skill agents call it over HTTP to deploy, query, and stop services — no SSH needed after initial setup.

```bash
# SSH into your compute node once to set up
pip install fastapi uvicorn docker
python deploy-agent/server.py --port 9000
```

The deploy-agent exposes:
- `GET /health` — node status and GPU count
- `GET /services` — list running services
- `POST /deploy` — deploy a service (idempotent)
- `POST /stop` — stop a service
- `GET /gpus` — GPU status with VRAM and service assignments

See [service-agent-setup/](service-agent-setup/) for detailed setup and the [deploy-agent spec](service-agent-setup/openclaw/workspace/docs/DEPLOY_AGENT_SPEC.md).

### 3. Set up a skill agent

A skill agent is the AI that develops and runs skills on your robot. See [skill-agent-setup/](skill-agent-setup/) for available platforms.

**OpenClaw** (recommended):

```bash
git clone https://github.com/TidyBot-Services/Tidybot-Universe.git
cd Tidybot-Universe/skill-agent-setup/openclaw
./setup.sh
```

For detailed instructions (including manual setup), see [skill-agent-setup/openclaw/README.md](skill-agent-setup/openclaw/README.md).

### 4. Talk to your agent

Once setup is complete, open a chat with your agent. It will:

- Introduce itself and get to know you
- Read the robot documentation from the agent server
- Ask about your wishlist — what do you want the robot to do?

### 5. Watch it work

Your agent will:

- Check the [skills catalog](https://github.com/tidybot-skills) for existing skills
- Build new skills for your wishlist items
- Deploy needed services to compute nodes via the deploy-agent
- Test them on your robot — safely, with rewind as a safety net
- Share them back so others can use them too

Track progress on the [Tidybot Universe timeline](https://tidybot-services.github.io/).

## Service Development

Services are Docker containers that run on GPU compute nodes, managed by the deploy-agent. Each service repo contains:

```
<service-name>/
├── service.yaml    # Deploy manifest (image, port, GPU requirements)
├── client.py       # Client SDK (urllib only, no external deps)
├── main.py         # FastAPI server
├── Dockerfile      # Container build
├── requirements.txt
└── README.md
```

**To add a new service:**

1. Create a repo under [TidyBot-Services](https://github.com/TidyBot-Services)
2. Build and test the Docker image on a compute node (SSH in, iterate until it works)
3. Once working, skill agents can deploy it anytime via `POST /deploy`

See the [service manifest spec](service-agent-setup/openclaw/workspace/docs/SERVICE_MANIFEST_SPEC.md) and [client SDK spec](service-agent-setup/openclaw/workspace/docs/CLIENT_SDK_SPEC.md) for standards.

## A Note on Services Development

Skills run **above** the agent server safety layer — rewind, safety envelope, and sandboxed execution protect the hardware. Your agent can freely experiment.

Services run **below** that layer — they talk directly to hardware and system resources. Service development (building Docker images, debugging GPU/CUDA issues) is best done by humans via SSH. Once a service works, the deploy-agent handles the lifecycle automatically.

## For Agents

If you're an AI agent reading this:

- **Building skills?** Read the [skills org profile](https://github.com/tidybot-skills) for workflow rules and wishlist
- **Need a service?** Check `GET /services` on the compute node's deploy-agent. If it's not running, read the service repo's `service.yaml` and `POST /deploy`
- **Building services?** Read the [services org profile](https://github.com/TidyBot-Services) for service types and contribution guide

## Links

- [Tidybot Universe Timeline](https://tidybot-services.github.io/) — live activity feed
- [Skills Org](https://github.com/tidybot-skills) — browse and contribute skills
- [Services Org](https://github.com/TidyBot-Services) — browse and contribute services
- [Skills Wishlist](https://github.com/tidybot-skills/wishlist) — request robot behaviors
- [OpenClaw](https://openclaw.ai) — the agent platform
