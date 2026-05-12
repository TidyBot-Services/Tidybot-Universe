# Active Context

> **Last updated:** 2026-05-13 (single-arm FR3 service shipped, Day 1 validation complete)

## Current Focus

Building out support for a **single-arm FR3 workstation** as a parallel deployment alongside the existing Panda + Tidybot setup. The core architectural pieces just landed in 4 repos; physical FR3 hardware is not yet acquired.

What just shipped (2026-05-13):

- **`arm_franka_fr3_service/`** — new hardware service repo (libfranka 0.13.3 target). Sibling of `arm_franka_service/` (which stays pinned to libfranka 0.9.1 for Panda). Same ZMQ wire protocol — `agent_server` is interchangeable.
- **Capability profile system** in `agent_server` — robot profile YAML declares which backends/services exist on this deployment. Drives backend wiring, `/code/sdk` filtering, and `CapabilityStub` hard-fail.
- **`start_robot.sh --profile`** flag — selects arm service dir + per-component defaults. `--profile full` (default) is identical to pre-change behavior.

See `decisions/0007-single-arm-fr3-service.md` for the full design rationale, including why this couldn't be solved as a "same-PC switchable profile" (libfranka 0.10+ is FR3-exclusive, Panda dropped).

## Recently Completed

- **4-repo push (2026-05-13):**
  - `arm_franka_fr3_service` — initial commit `9095501`, new repo at github.com/TidyBot-Services/arm_franka_fr3_service
  - `agent_server` — `057abfd` profile system
  - `common` — `017f810` start_robot.sh --profile flag
  - `Tidybot-Universe` — `6fafaba` ADR 0007 + .gitignore
- **Day 1 zero-regression validation:** new agent_server on port 8280 (dry-run, default profile=full) produces a `/code/sdk` response that is **byte-identical** to the May-9 production agent_server on :8080. No backend is skipped when profile=full, no behavior change. The production :8080 server was not touched during validation.
- **Day 1 single_arm_fr3 verification:** new agent_server on :8380 with `--profile single_arm_fr3` correctly exposes only 4 modules (arm, gripper, rewind, sensors) + 2 advanced backends, and logs `Skipping base backend / mocap backend` as expected.

## Next Steps

- **Day 2 — end-to-end single_arm_fr3 against the running sim.** The May-9 sim (`RoboCasa-Pn-P-Counter-To-Cab-v0`, PID 1688506) is still up on :5555-:5580 + :50000. Plan: start a new agent_server `--profile single_arm_fr3 --port-offset 400`, submit `from robot_sdk import base; base.move_delta(dx=0.5)`, confirm the subprocess raises `CapabilityNotAvailableError` with the expected message and the stdout/job log captures it cleanly. This validates the env-var inheritance from parent agent_server → code_executor subprocess → robot_sdk capability filter.
- **Day 3 — libfranka 0.13.3 compile (no FR3 required).** Run `arm_franka_fr3_service/setup_server.sh` on a Linux box with cmake + Eigen + Poco. Verifies the build matrix (and the RT-PREEMPT warning) before the hardware arrives. Conda env decision deferred (don't conflate with Panda's pylibfranka 0.9.1).
- **Day 4+ — physical FR3 setup.** Mount, network (172.16.0.2 on private subnet), Desk 5.x unlock, FCI activation, collision-threshold tuning. Hardware not yet acquired.
- **Skill metadata for base-dependent skills** — the 3 skills under `graphs/` that call `base.*` (counter-to-cab/approach-counter, counter-to-sink/pnp-counter-to-sink, unified-single-stage/open-single-door) need `requires: [base]` so the orchestrator can hide them from single-arm profiles. Deferred — capability system is in place, this is just metadata + an orchestrator filter pass.

## Open Questions

- **`/code/sdk` loads RobotProfile on every request** (saw 5 `Loaded robot profile 'full'` log lines during Day 1 validation across 5 curls). Functionally correct but should cache. Cleanup task — not blocking anything.
- **CapabilityStub `__setattr__` raises**, which is correct for "agent shouldn't reassign", but if something legitimate in the code_executor tries `robot_sdk.base.foo = …` it'll fail. Worth a quick grep before real deployment.
- **Pre-existing untracked items in parent repo** (`eval/curobo_v1_v2/results/*.log`, `sync_catalog.sh`) are still untouched — they were there at session start and aren't my concern, but worth deciding whether to commit/ignore.
- **Hardware decision still pending** from the user: FR3 with or without Robotiq 2F-85 gripper? With or without RealSense camera on EE? Profile YAML defaults to having both; easy to override.

## Relevant Files (current session's touched code)

- `arm_franka_fr3_service/` (new repo) — `franka_server/franka_server/server.py:74,687`, `command_buffer.py:635` (FR3 joint limits)
- `agent_server/robot_profile.py` (new) — `RobotProfile` dataclass + YAML loader
- `agent_server/profiles/full.yaml`, `profiles/single_arm_fr3.yaml` (new)
- `agent_server/config.py` — `ServerConfig.profile: RobotProfile`
- `agent_server/server.py` — profile arg + env publish + backend skip
- `agent_server/routes/sdk_docs.py` — capability-filtered docs
- `agent_server/robot_sdk/__init__.py` — `CapabilityStub`, `CapabilityNotAvailableError`, `apply_capability_filter`
- `agent_server/code_executor.py` — bootstrap calls `apply_capability_filter()`
- `common/start_robot.sh` (symlinked from parent) — `--profile` + per-component routing
- `Tidybot-Universe/.gitignore` — added `/arm_franka_fr3_service/`
- `docs/ai-memory/decisions/0007-single-arm-fr3-service.md` (new ADR)

## What's running right now (local)

- **Production-ish:** `agent_server` :8080 (PID 1772976, started May 9, **old code, not impacted by today's push**), maniskill sim :5555-:5580 + :50000 (PID 1688506, Counter-To-Cab-v0 task), cuRobo service :7000.
- **From Day 1 validation:** all dry-run instances on :8180/:8280/:8380 are killed clean.
- Deploy-agent :9000 local + 158.130.109.188:9000 remote — alive.

## Branch state

All work on `master` of the four repos. No active feature branches. The deleted `eval/curobo-v1-v2` branch from earlier is gone.
