# Active Context

> **Last updated:** 2026-05-12 (memory system bootstrap session complete)

## Current Focus

The shared AI-memory system at `docs/ai-memory/` is now live and pushed. CLAUDE.md was shortened to 155 lines + Memory Policy + Startup Routine. The previously-gitignored top-level CLAUDE.md is now tracked (symlinked from common/, both repos pushed).

The project is **ready for normal development cadence** using the new memory workflow:

1. Sessions start by reading `active-context.md` (this file).
2. Touch a module → read the corresponding `modules/<name>.md`.
3. End of session → user invokes the end-of-session prompt and I distribute updates to active-context / progress / decisions / personal memory.

## Recently Completed

- **`docs/ai-memory/` tree created and seeded** — 18 files total: README + active-context + progress + project-brief + 5 ADRs + 6 module docs + 3 cross-module patterns. See `decisions/0006-shared-memory-tree.md` for the rationale.
- **CLAUDE.md rewritten** from 215 → 155 lines. Removed inline rewind/error/port details that now live under `modules/`. Added Memory Policy + Startup Routine sections.
- **`.gitignore` updated** to track top-level CLAUDE.md (removed from ignore list). It's a symlink to `common/CLAUDE.md`, so both the parent repo and the common repo got pushes.
- **Per-skill agent + stdout.log + evaluator prompt + skill-DAG mechanism** all fixes pushed earlier today (2026-05-09) — see `progress.md`.

## Next Steps

- **Use the new memory system in the next session** — invoke the end-of-session prompt to keep `active-context.md` and `progress.md` fresh. If we drift back to dumping everything in CLAUDE.md, the system has decayed.
- **Decide whether to retire the SSH-scan `service-server-setup/` catalog** (port 8090, currently inactive). The deploy-agent on port 9000 covers service discovery in practice.
- **Switch dev to Claude Opus 4.7 for a clean comparison** on the same DAG (`counter-to-cab` or `counter-to-sink`) to isolate "model limit" from "infrastructure". This is the cleanest test of `patterns/dev-model-failure-modes.md`.
- **Verify M6 (root mechanical test)** on a trivially-passable root skill — we never observed `testing` state firing during the counter-to-cab run because evaluator kept failing root.
- **Stale-memory sweep policy** — pick a cadence (monthly? per major version?) to audit `docs/ai-memory/` claims against current code. Some `modules/<X>.md` already reference file paths that could drift.

## Open Questions

- Are there other top-level files in `.gitignore` (`AGENTS.md`, `IDENTITY.md`, etc.) that should also be tracked for the same reason CLAUDE.md was promoted? These are also symlinks into `common/`. Probably yes for `AGENTS.md` (project-level agent instructions), maybe yes for the others — but lower priority.
- Should `~/.claude/` auto-memory entries that have shared value (e.g. `feedback_pathfinder_subdir_shadowing.md`) be deleted now that the patterns/ doc covers them? Risk of duplication. Currently keeping both — the auto-memory entry has more raw debug context, the patterns/ doc has the distilled lesson. Worth deciding the policy.
- M6 mechanical test path may have a subtle bug we haven't caught — worth a clean test with `task_env` set and a guaranteed-pass root skill.

## Relevant Files (current session's touched code)

- `docs/ai-memory/` — entire new directory (18 files)
- `CLAUDE.md` → symlink to `common/CLAUDE.md` (now both tracked)
- `.gitignore` — removed `CLAUDE.md` line at row 29

## What's running right now (local)

- `agent_server` :8080 — alive (PID 1772976)
- `cuRobo service` :7000 — alive
- Sim — Counter-To-Cab-v0 was running with GUI earlier (may have been Ctrl-C'd)
- Orchestrator :8765/8766 — stopped at end of DAG test
- Deploy-agent :9000 (local) + 158.130.109.188:9000 (remote) — alive
- Qwen3.6 + Kimi-K2.6 (Penn LiteLLM) — periodic outages observed this session, currently alive

## Branch state

All recent work on Tidybot-Universe `master` and common `master`. No active feature branches. `eval/curobo-v1-v2` was deleted 2026-05-09 after merging.
