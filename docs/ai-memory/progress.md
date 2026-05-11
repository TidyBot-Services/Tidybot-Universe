# Progress

Append-only milestone log. Don't edit history; add new entries at the top.

## Milestones

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
