# 0001 — Shared SDK Between Simulation and Real Hardware

**Status:** Accepted
**Date:** Project inception (long-standing)
**Last verified:** 2026-05-09

## Context

Agents writing robot code can target either the real Franka+Tidybot or a simulated equivalent (ManiSkill / RoboCasa). The risk: if sim and real have different API surfaces, agents must learn two dialects, and skills don't port across.

The two main alternatives:
1. **Separate APIs** — sim has its own SDK (ManiSkill conventions); real has its own (Franka libfranka). Agents pick the right one. Code from sim doesn't drop into hardware.
2. **Unified SDK** — one `robot_sdk` module surface; sim and hardware are interchangeable backends that implement it.

## Decision

**Unified `robot_sdk`** (option 2). Both sim and real expose:

- `arm` (move_to_joints, move_to_pose, go_home, get_state)
- `base` (plan_path, move_to_pose, get_state)
- `gripper` (open, close, get_state)
- `wb` (whole-body via cuRobo)
- `sensors` (find_objects, get_arm_base_world, get_task_info)
- `yolo`, `graspgen`, `rewind`, `display`

Underneath, **bridges** translate the same wire protocols (ZMQ for arm/gripper, RPC for base, WebSocket for camera) into either real-hardware or sim implementations. Same port numbers on both sides.

## Consequences

- **Skills are portable**: agent develops on sim, runs on hardware, no code change.
- **Sim becomes a first-class development environment**: full reset, deterministic replay, parallel instances. Hardware can be reserved for final-stage validation.
- **One bug class introduced**: when sim diverges from hardware (e.g. mocap publishing rate, finger lock semantics), it can silently mislead the agent. Need ongoing sim-fidelity work.
- **Tested at:** `agent_server` lease + code execution doesn't know which backend it's hitting — only `agent_server/backends/*` knows.

## Alternatives Considered

- **ROS-based abstraction**: rejected for the same reason ROS is becoming optional in the field — heavy, opinionated, slow to iterate, friction with non-ROS LLM tooling.
- **Sim-only project, real hardware deferred**: would let us move faster on agentic algorithms but defeats the project's purpose of being a real-hardware tool.

## Related

- `modules/agent-server.md` — how the SDK is implemented on the server side
- `modules/robot-sdk.md` — SDK surface details
- `modules/simulation.md` — sim-side implementation
