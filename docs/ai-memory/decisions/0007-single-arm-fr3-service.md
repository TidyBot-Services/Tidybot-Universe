# 0007 — Single-Arm FR3 as a Parallel Hardware Service

**Status:** Accepted
**Date:** 2026-05-12
**Last verified:** 2026-05-12

## Context

We need to support a second hardware target: a workstation with **only a Franka Production FR3 arm** (no mobile base, optional gripper / camera). Two architectural questions:

1. **How to add FR3 alongside the existing Panda?** libfranka 0.10+ is FR3-exclusive — Panda is no longer supported in those releases (per Franka official changelog). The Tidybot's Panda is locked to libfranka 0.9.1; the FR3 needs ≥ 0.13. A single libfranka build cannot serve both.

2. **How to make the agent stack handle a robot without a base?** `robot_sdk` exposes `base`, `wb`, etc. Without filtering, an LLM agent reads `/code/sdk`, sees `base.move_delta`, generates code that calls it — and the call either hangs (no base service running) or fails with a generic connection error. We need the absent-capability case to be explicit, ideally invisible to the agent's prompt.

Mode-A "same PC switches between Panda and FR3 via flags" was considered first but ruled out: ABI-linking to two libfranka versions on one machine is impractical, and `pylibfranka` is a single Python package name. Mode B (independent deployment) is the only feasible path.

## Decision

### 1. New parallel hardware service — `arm_franka_fr3_service/`

A sibling of `arm_franka_service/` (which stays pinned to libfranka 0.9.1 for Panda). The new service:

- Same wire protocol (ZMQ msgpack on ports 5555/5556/5557) — agent_server is unchanged.
- Same Python class structure (`FrankaServer`, `FrankaClient`, `CommandBuffer`, etc.) — copied from Panda service.
- Pins libfranka **0.13.3** (FR3-compatible) — `REQUIRED_LIBFRANKA_VERSION = "0.13.3"` in `server.py:74`.
- Adjusted defaults: collision thresholds reduced from `[100.0]*7` to `[30.0]*7` (FR3 sensors are more sensitive); joint-limit guards in `command_buffer.py` updated to FR3 values.
- Setup script clones libfranka 0.13.3 fresh; that source is **not** committed to the repo.

This mirrors the existing pattern for sim multi-backend support (`arm_franka_maniskill_service`, `arm_franka_sim_service`) — adding a hardware target is "another backend that speaks the same wire protocol", not a refactor.

### 2. Robot capability profile system — `agent_server/profiles/*.yaml`

A profile is a YAML file declaring which hardware/services are available on the current deployment:

```yaml
# profiles/single_arm_fr3.yaml
name: single_arm_fr3
arm: true
gripper: true
camera: true
base: false        # ← key gate
mocap: false
perception_server: false
yolo_service: false
graspgen_service: false
display: false
```

Selected at startup via `--profile <name>` CLI arg or `ROBOT_PROFILE` env var. Default is `full` (everything enabled — Tidybot configuration). The profile flows through `ServerConfig.profile: RobotProfile`.

### 3. Two-layer capability filtering

- **Layer A (soft barrier):** `/code/sdk` doc endpoint omits modules whose required capability is disabled. The LLM agent reading the SDK reference simply doesn't see `base`, `wb`, `display`, etc. on a single-arm workstation, so its generated code naturally won't reference them.
- **Layer B (hard safety net):** `robot_sdk/__init__.py` registers a `CapabilityStub` for each disabled module after the code-execution bootstrap injects the real instances. Any attribute access on a stub raises `CapabilityNotAvailableError` with a message naming the profile and capability. This catches the case where an agent ignores the docs or recalls APIs from training data.

### 4. Profile-aware launcher

`start_robot.sh --profile <name>` selects the arm service directory (`arm_franka_service` vs `arm_franka_fr3_service`) and toggles which sibling services to start (base, controller, etc.). Existing `--no-X` flags still override per-component.

## Consequences

- **Tidybot deployment is unchanged.** The existing Panda PC keeps using `arm_franka_service/` with libfranka 0.9.1; no git history, no checkout, no behavior changes.
- **FR3 workstation is independent.** Different PC, different libfranka, different conda env. Two-PC mode B; not a runtime toggle.
- **Code duplication.** ~1000 lines of ZMQ wrapper code exist in both `arm_franka_service/` and `arm_franka_fr3_service/`. Acceptable because the duplicated parts (protocol, command buffer, Desk client) rarely change. If maintenance pain emerges, factor out `franka_server_core` later.
- **`docs/ai-memory/decisions/0001-shared-hardware-sdk.md` is reinforced**, not contradicted. Both arm services implement the same wire protocol; the SDK remains a single surface; capability filtering happens *on top* of the SDK.
- **Profile env vars (`ROBOT_PROFILE`, `ROBOT_CAPABILITIES`)** are inherited by code-execution subprocesses, so the SDK stubs activate inside the user code sandbox without extra wiring.
- **The 3 base-dependent skills in `graphs/`** (counter-to-cab, counter-to-sink, open-single-door) will fail to import / execute on single-arm profiles. Future work: add `requires: [base]` metadata to skill graphs and have the orchestrator filter them per active profile.

## Alternatives Considered

- **Mode A — same PC, switchable profile, both libfranka versions side-by-side.** Rejected: `pylibfranka` is a single Python package, libfranka is a single `.so`. Workable only via conda env per profile, which doubles maintenance and offers no real benefit over independent PCs.
- **Replace libfranka in place + use git branches for Panda vs FR3.** Rejected: PC-specific git state is fragile; the two installations would diverge on commits, fixes wouldn't propagate uniformly.
- **Keep using Panda for the single-arm workstation (buy used Panda).** Considered. Lowest engineering cost but Panda is EOL — long-term parts and service supply is shrinking; FR3 is the strategic direction.
- **Filter only at SDK doc level (skip CapabilityStub).** Considered minimal MVP. Rejected because LLM agents occasionally recall APIs not present in their current prompt; a hard safety net is cheap to add and prevents silent hangs.

## Related

- `modules/agent-server.md` — server-side capability profile loading + backend gating
- `modules/robot-sdk.md` — CapabilityStub semantics
- `0001-shared-hardware-sdk.md` — the unified SDK surface this builds on
- `arm_franka_fr3_service/README.md` — FR3 service operator guide (libfranka version, FR3 differences)
- `agent_server/profiles/single_arm_fr3.yaml` — the FR3 workstation profile
- Franka official libfranka changelog (FR3-exclusive from 0.10.0 onward, FR3 system 5.5+ required from 0.13.x)
