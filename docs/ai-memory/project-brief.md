# Project Brief — Tidybot Universe

## What

An end-to-end platform that unifies simulation, real hardware, and multi-agent AI into a single autonomous mobile-manipulator development loop.

The platform lets AI agents iterate on robot skills in parallel — writing code, executing on shared hardware (or its simulated equivalent), getting evaluator feedback, and retrying — with minimal human intervention.

## Why

Today's "AI for robotics" papers report end-to-end autonomy, but **running these systems requires a human as the physical agent**: queuing experiments, resetting failed runs, repositioning objects, pressing start again. Five-task demos hide that the human is the bottleneck.

We're building the missing infrastructure so the human can focus on **taste and judgement** instead of being a scene-reset tool.

## Core Architecture

Three resources balanced at runtime: **hardware time** (or sim time), **agent cost**, **human attention**.

```
┌──────────────────────────────────────────────────────────────┐
│  Human (taste, approval gates)                                │
│       ▲                                                       │
│       │   "this agent stuck", "this looks done"               │
│  Collaborative UI ←──────────────────────────────────────┐    │
└─────────────────────────────────────────────────────────┬┘    │
                                                          │     │
┌─────────────────────────────────────────────────────────┴┐    │
│  Multi-agent Harness (Claude SDK or OpenClaw)           │    │
│  ┌────────┐  ┌────────┐  ┌────────┐                     │    │
│  │ dev_A  │  │ dev_B  │  │ dev_C  │  ← per-skill agents │    │
│  └───┬────┘  └───┬────┘  └───┬────┘                     │    │
│      └───────────┼───────────┘                          │    │
│                  ▼                                      │    │
│  Skill DAG decomposition + closed-loop iteration        │    │
└──────────────────────────────────────────────────────────┘    │
                  │                                             │
                  ▼                                             │
┌────────────────────────────────────────────────────────────┐  │
│  Agent Server (job scheduling, lease, safety, auto-reset)  │  │
└──────┬──────────────────────────────────────────┬──────────┘  │
       │                                          │             │
       ▼                                          ▼             │
┌──────────────┐                       ┌──────────────────┐     │
│ Real hardware│  ←─── same SDK ───→   │ Simulation        │     │
│ (Franka +    │                       │ (ManiSkill /      │     │
│  Tidybot)    │                       │  RoboCasa)        │     │
└──────────────┘                       └──────────────────┘     │
                                                                 │
       ▲ deploy ↓ fetch                                          │
┌──────────────────────────────────────────────────────────────┐│
│ Service catalog (services_wishlist + deploy-agent on remote ││
│  GPU). Skills wrap internet-deployed models as plug-in tools.││
└──────────────────────────────────────────────────────────────┘│
```

## Key contributions (from whitepaper)

1. **Hardware job scheduling, safety, reset** — robot becomes a shared resource for agent fleets
2. **Sim/hardware-equivalent SDK** — identical API surface; agents don't care which they're hitting
3. **Collaborative UI + multi-agent harness** — humans approve, agents execute, system surfaces only what needs taste
4. **Autonomous task-decomposition strategies + SOTA model benchmarks** — skill DAG with closed-loop dev/evaluator iteration

## Repository layout

| Path | Role |
|---|---|
| `agent_server/` | FastAPI hardware server (lease, code execution, recording, safety) |
| `skill-agent-setup/claude-code/` | Orchestrator + harnesses (`agent_orchestrator.py`, `_openclaw.py`) |
| `sims/maniskill/` | ManiSkill server (cuRobo planner, sim env) |
| `sims/robocasa_tasks/` | RoboCasa task definitions (canonical `robocasa_tasks` package) |
| `service-agent-setup/` | Deploy-agent daemon (Docker-based remote service deployment, port 9000) |
| `service-server-setup/` | Service catalog scanner (SSH discovery, port 8090, currently inactive) |
| `services_wishlist/` | Coordination hub for wishlist + catalog of available services |
| `hardware/` | Real-hardware service clients (Franka, Robotiq, RealSense, Tidybot) |
| `eval/` | Evaluation runs, benchmark results |

## Benchmark snapshot

| Setup | Result |
|---|---|
| RoboCasa-TidyBot, 10 tasks, Claude Opus 4.7, Skill DAG | 70% success / 2.1 hr / 54 min robot time / $18.20 / 6 human approvals / workload 3.2/7 |

## Read next

- `active-context.md` — what we're working on right now
- `decisions/` — why architecture is the way it is
- `modules/<name>.md` — specific component deep dives
- `progress.md` — what's been shipped, what's known broken
