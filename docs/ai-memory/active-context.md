# Active Context

> **Last updated:** 2026-05-12 (post skill-DAG pipeline test + cross-repo desync resolution + memory system reorg)

## Current Focus

Setting up the project's **shared AI memory** (`docs/ai-memory/`). Migrating high-value patterns and decisions out of personal Claude Code memory into the team-visible git-tracked structure. Updating `CLAUDE.md` to point new sessions at `active-context.md` first.

## Recently Completed

- **Skill DAG decomposition pipeline verified** on `counter-to-cab` graph (3 entries: detect + approach + root). 5 of 6 mechanism points pass; root mechanical test (M6) not observed because evaluator kept failing root grasp.
- **Per-(target, skill) agent id** fix in `agent_orchestrator_openclaw.py` so parallel sub-skills don't collide on OpenClaw's single-session-per-agent lock.
- **stdout/stderr capture fix** — `agent_server` now writes `stdout.log` + `stderr.log` to exec dir; evaluator prompt updated to read those files instead of expecting stdout in `metadata.json`.
- **Cross-repo desync (cleanup of `maniskill-tidyverse/robocasa_tasks/` subdir) resolved permanently** — reverted shim approach (which broke fresh sim launches via PathFinder shadowing), rewrote 36 imports in `maniskill-robocasa-tasks` to use canonical `robocasa_tasks` directly, updated `sim/server.py` import.
- **Deploy-agent pipeline verified end-to-end** on `158.130.109.188:9000` via `service-agent-setup/probe_pipeline.sh` — POST /deploy → docker pull/run → /health → external call → /stop all work.
- **OpenClaw harness setup documented** in `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md`.

## Next Steps

- Reorganize `CLAUDE.md` to be short + point at this directory.
- Optionally: rewrite the dev/evaluator prompt's evidence hierarchy to encourage dev to print results of every side-effect call (combats "silent success misread" failure mode).
- Optionally: improve evaluator robustness — dev produces `APPROACH_OK 0.746` but eval still false-failed because evaluator was sometimes browsing pre-fix exec dirs. Make eval choose the latest exec deterministically.
- Long-term: switch to Claude Opus 4.7 (claude-sdk harness) for a clean comparison run on the same DAG to isolate "model limit" vs "infrastructure" failures.

## Open Questions

- Should the `service-server-setup/` SSH-scan catalog (port 8090, currently inactive) be retired since `service-agent-setup/` deploy-agent (port 9000, active) provides better service discovery?
- M6 (root mechanical test) was never observed in the counter-to-cab run — is the testing-state trigger logic actually exercised end-to-end with autonomous mode? Worth a fresh probe with a trivially-passable root.
- The per-skill agent change creates per-skill agent dirs (`tidybot-dev-default-<skill>`). For projects with 30+ skills this is a lot of dirs. Garbage-collection policy?

## Relevant Files

- `skill-agent-setup/claude-code/agent_orchestrator.py` — main orchestrator (SDK + openclaw dispatch, evaluator prompt at line ~2266)
- `skill-agent-setup/claude-code/agent_orchestrator_openclaw.py` — OpenClaw harness backend (per-skill agent id around line ~395, stale session_id detection ~412)
- `agent_server/code_executor.py` — code execution + stdout.log/stderr.log write (~line 565)
- `sims/maniskill/maniskill_server/server.py` — sim server (perturbation widening `~line 942`, canonical robocasa_tasks import `~line 1563`)
- `graphs/counter-to-cab/` — the test graph (gitignored, not committed)
- `service-agent-setup/probe_pipeline.sh` — deploy-agent feasibility probe

## What's running right now (local)

- `agent_server` :8080 — alive (PID may change, latest restart 2026-05-09 10:50)
- `cuRobo service` :7000 — alive
- ManiSkill sim :5500/5555/etc — Counter-To-Cab-v0 task running with GUI (may have been Ctrl-C'd by now)
- Orchestrator :8765/8766 — last stopped after DAG test verification
- Deploy-agent :9000 (local) + 158.130.109.188:9000 (remote) — alive

## Branch state (Tidybot-Universe parent repo)

`master` is the active branch. `eval/curobo-v1-v2` was deleted after merging into master 2026-05-09. All session work pushed to origin.
