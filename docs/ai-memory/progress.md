# Progress

Append-only milestone log. Don't edit history; add new entries at the top.

## Milestones

### 2026-06-17 — cuRobo launch-trap root-caused + counter-to-sink re-developed 5/5
- **THE big infra fix: `CUROBO_SERVICE_URL` was never set on the sim launch.** Symptom was the session-long "cuRobo unavailable" on every `wb.move_to_pose`. Cause: without `CUROBO_SERVICE_URL`, `maniskill_server` falls back to the *in-process* cuRobo planner, which isn't installed in the `tidybot` env (`ModuleNotFoundError: curobo.wrap`) → `self._curobo=None` → every plan returns "cuRobo unavailable". The healthy cuRobo **service on :7000 was simply never connected.** Fix: launch the sim with `CUROBO_SERVICE_URL=http://localhost:7000` (verify `[curobo] Using remote service ... Loaded N cuboids` in the sim log, NOT `Using in-process planner`). This silently crippled the earlier sink-to-counter run's placement ("marginal reliability" = no collision-aware planner). The CLAUDE.md quick-launch snippet OMITTED the var — now fixed. See personal memory `curobo-finger-lock-dof-mismatch` (cause #1).
- **counter-to-sink (`RoboCasa-Pn-P-Counter-To-Sink-v0`) fully re-developed, 5/5 done** with cuRobo wired. 4 sub-skills re-developed cleanly in ~10 min; `place-object-in-sink` passed `/task/success` first try (validating the cuRobo fix).
- **Root `pnp-counter-to-sink` — two-part fix:** (1) its inlined grasp was marginal on the small/deformable **yogurt** (~14mm slip-prone neck grip, circled it ~1h); replaced the root with the proven `place-object-in-sink` full pipeline → solid **pmm≈36** grip. (2) That exposed a pre-existing **~40% near-edge release-Y variance** — the base nav aligns the *sink-drop point* to a fixed arm_y, but the *held object* sits at the grasp arm_y, so it landed ~8cm past the sink's near edge / didn't settle. Fix = **STEP 7.5 object-Y centering**: before release, nudge the held object to the sink Y-midline via a small lateral `base.move_delta` (`local_dy = obj_y − sink_cy`; heading≈π so local +dy = world −Y), plus release 4cm (not 8cm) above the basin floor + 1.0s settle. Ground-truth test → `sr=100%`, object centered (`obj_y≈-2.07`). Robust fix, not a lucky pass.
- **Pattern (reusable):** for top-down place-in-container skills, center the HELD OBJECT (not the EE / not the container-drop point) in the target before release, and release low for settle. See personal memory `place-center-held-object-not-eef`.

### 2026-06-13 — sink-to-counter graph solved end-to-end (5/5 skills done)
- `graphs/sink-to-counter/` (`RoboCasa-Pn-P-Sink-To-Counter-v0`) all 5 skills `done`: find-target-object, find-counter-container, pick-object-from-sink, place-object-on-counter, pnp-sink-to-counter (root). Root confirmed done on a passing ground-truth eval (`f660d5d0`, `/task/success=True`).
- **Key technique — cross-skill seeding.** `place-object-on-counter` was stuck (dev re-deriving the sink grasp for ~40 min). Fixed by **seeding it with `pick-object-from-sink`'s proven Jacobian-IK grasp+lift verbatim (STEP 1-6)**, then a **one-grasp held transport (STEP 7-11)**: find container → strafe the BASE toward it *while gripping* (grip holds pmm~17-22 across steps) → arm-only carry → descend → **gradual position-based release** (avoids the force-open lateral fling) → **vertical-only j1 retract** (avoids `go_home`/arm-body sweeping the placed object off the plate). This replaced a fragile release→strafe→**re-pick**-from-counter design whose second grasp was the dominant failure surface.
- **Robustness caveat (NOT robustly reliable).** Marked done on a passing eval, but most runs failed under `wb.move_to_pose: Planning failed: cuRobo unavailable` → imprecise Jacobian-IK fallbacks. Failure modes whack-a-mole'd across a long session: carry-drop, release-fling, placement y-precision, **AABB-proxy-vs-real-`_check_success` mismatch** (code's ±5cm "in container" check is looser than the sim's actual plate region), j1-retract arm-body-sweep. **Fixing cuRobo planning is the real unblock** for reliable placement (port :7000 up but planning fails — likely a residual config issue, cf. the franka-tidyverse finger-lock DOF fix in personal memory).
- **Plan A holder-tagging validated under concurrency** — every eval routed to its own `dev:<skill>`/`test:<skill>`-tagged recording; no cross-skill recording pollution across the whole multi-skill run.
- **Bundler trap (lesson).** A skill's `scripts/deps.txt` makes the orchestrator bundler **PREPEND + RUN each dep's top-level `main.py`**; for a self-contained skill (grasp inlined, not imported) deps.txt MUST stay empty, else the dep's whole routine runs first. See personal memory `deps-txt-bundler-prepends-top-level`.
- **M6 note:** the root's *mechanical* test loop still didn't cleanly pass (exhausted 3 attempts on variance); promotion came via the autonomous **eval** path on a passing run, not the mechanical test.

### 2026-05-13 — Single-arm FR3 service + capability profile system shipped
- **New repo:** `arm_franka_fr3_service` at github.com/TidyBot-Services/arm_franka_fr3_service. Sibling of `arm_franka_service` — same ZMQ wire protocol, different libfranka version (0.13.3 vs 0.9.1) because libfranka 0.10+ dropped Panda support and is FR3-exclusive. Initial commit `9095501` includes the Python wrapper code (~8.3k lines, copied from the Panda service + 3 surgical edits: version pin, collision thresholds `[100]→[30]`, FR3 joint limits). libfranka source is intentionally not committed — `setup_server.sh` clones 0.13.3 fresh.
- **Capability profile system in `agent_server`** (`057abfd`): `RobotProfile` YAML loader (`agent_server/profiles/full.yaml` + `single_arm_fr3.yaml`), `--profile` CLI flag, env publishing for code-exec subprocesses, profile-aware backend connect, capability-filtered `/code/sdk` endpoint (9 modules → 4 modules under single_arm_fr3), `CapabilityStub` hard safety net in `robot_sdk` that raises `CapabilityNotAvailableError` on disabled-capability calls.
- **`common/start_robot.sh --profile` flag** (`017f810`): chooses arm service directory (`arm_franka_service` for Panda, `arm_franka_fr3_service` for FR3) and toggles base/controller defaults per profile. Default profile (`full`) is byte-identical to old behavior — no regression for Tidybot.
- **Parent repo** (`6fafaba`): ADR `0007-single-arm-fr3-service.md` + `.gitignore` entry for the new service dir.
- **Day 1 zero-regression validation:** new agent_server (default profile=full, dry-run on :8280) produces a `/code/sdk` response **byte-identical** to the May-9 production server on :8080. No backend is skipped when profile=full. Production :8080 was not touched.
- **Day 1 single_arm_fr3 verification:** new agent_server on :8380 with `--profile single_arm_fr3` exposes 4 modules + 2 advanced backends, logs `Skipping base backend / mocap backend`.

### 2026-05-12 — Shared AI-memory tree bootstrapped
- New `docs/ai-memory/` directory in `Tidybot-Universe` parent repo (commit `cf0be50`).
- 18 seed files: README + active-context + progress + project-brief + 5 ADRs + 6 module docs + 3 cross-module patterns.
- `CLAUDE.md` rewritten from 215 → 155 lines (commit `26566f2` in `common` repo, symlinked from parent).
- `.gitignore` updated to track top-level CLAUDE.md (commit `bd0a784`).
- Memory Policy + Startup Routine sections added to CLAUDE.md so new sessions auto-discover the structure.
- See `decisions/0006-shared-memory-tree.md` for rationale.

### 2026-05-09 — Skill DAG pipeline end-to-end mechanism verified
- Test graph `counter-to-cab/` (detect-objects + approach-counter + pnp-counter-to-cab)
- 5 of 6 orchestration mechanism points pass (M1-M5). M6 (root mechanical test) didn't fire because evaluator kept failing root.
- Per-(target, skill) agent fix unblocked parallel-leaf execution.
- See `decisions/0002-skill-dag-decomposition.md`.

### 2026-05-09 — Cross-repo desync permanently resolved (option-3 rewrite)
- Reverted broken "shim" approach (commit `3c02392` in maniskill-tidyverse, reverted as `fbbac3a`).
- Rewrote 36 file imports in `maniskill-robocasa-tasks` to use canonical `robocasa_tasks` package directly.
- Updated `maniskill_sim/server.py` import accordingly.
- See `patterns/pathfinder-subdir-shadow.md` for the lesson.

### 2026-05-09 — Deploy pipeline verified
- Probed `158.130.109.188:9000` deploy-agent end-to-end via `service-agent-setup/probe_pipeline.sh`.
- POST /deploy → docker pull → /health → external call → POST /stop all worked.
- Spec gotcha documented: `req.port` is container-internal port; needs `command:` arg or match image's EXPOSE.

### 2026-05-09 — stdout/stderr capture disconnect fixed
- `agent_server` `03ed465`: write `stdout.log` + `stderr.log` to exec dir.
- Orch `94bb530`: evaluator prompt reads those files instead of expecting stdout in metadata.json.

### 2026-05-09 — OpenClaw harness fully documented
- New `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md` walks through setup of `--harness openclaw` mode.
- `skill-agent-setup/README.md` updated with two-modes table (standalone chat vs orchestrator harness).

### 2026-05-09 — Dashboard chat panel blank-on-restart fixed (openclaw)
- Orch `9e992c4`: clear stale state.session_id if file doesn't exist on disk → tail correctly finds fresh session file.

### 2026-05-09 — Sim wb perturbation search widened
- `maniskill_sim` `cabdb2e`: ±0.1m × 8 dirs → 3 shells (0.15/0.30/0.45m) × 16 dirs = 48 candidates.
- Unblocks cuRobo whole_body plans whose base_goal lands inside kitchen fixtures.

### 2026-05-07 — Counter-to-sink ground-truth pass (single-task milestone)
- Disabled spurious env.reset() in sim run loop (commit `ab439ae`).
- Counter-to-sink mechanical test now passes when dev's pick succeeds.

### 2026-05-04 — cuRobo v0.8 production swap
- Sim's cuRobo backend swapped from in-process v0.7.6 to standalone v0.8 service on `:7000` in `maniskill_v0_8` env.
- 2.3× faster trajopt. v0.7 service preserved at `~/文档/curobo_service/` for rollback.

### 2026-04-30 — Multi-target orch hardening (Fix #11b/#14/#14b/#15/#16)
- Path A vs path B eval lock race resolved.
- Path A missing `_last_feedback` (EMPTY-feedback rows) fixed.
- Evaluator default-pass loophole closed + regex tolerance added.
- OpenClaw retry counter adjusted (was too aggressive vs SDK).
- See `feedback_orch_multitarget_bug_patterns.md` in personal memory for 22-pattern dump.

### 2026-04-25 — Sim auto-reset blocker
- Sim's run loop was calling `env.reset()` the instant `_check_success` returned True.
- Disabled in commit `ab439ae`. Reset is now explicit via `POST /reset`.

### 2026-04-23 — cuRobo migration complete
- Both planner-layer (maniskill-tidyverse sim-integration) and server-layer (maniskill_sim feature/curobo-plan-endpoints) migrated.
- ManiSkill server now first-class cuRobo (mplib fallbacks removed).

### 2026-04-22 — OpenClaw harness migration stage 1 done
- `feature/openclaw-harness-poc` branch (5 commits, merged) added drop-in OpenClaw subprocess driver to orch.
- Dev path migrated; evaluator still on Claude SDK for vision-heavy review.

## Known Issues / Deferred Work

- **DS-V4-Flash dev exhibits "silent success misread" failure mode** when its own code lacks prints after side-effect calls. See personal memory `feedback_dev_silent_success_misread.md`.
- **Dev reverts to TopDown heuristic** after one graspgen failure, despite prompt saying "always graspgen first". Persistent across sessions. See personal memory `feedback_dev_agent_reverts_to_heuristic.md`.
- **Service catalog server (port 8090) designed but unused** — `service-server-setup/service_scanner.py` SSH-scans remote GPU server. Not currently running. Replaced in practice by the deploy-agent on port 9000.
- **M6 (root mechanical test) not yet observed** in DAG decomposition mode. Need a clean run where evaluator passes root for the first time to verify the testing-state trigger fires.
- **No automated stale-memory sweep** — `docs/ai-memory/` claims age; need periodic verify-against-code.
