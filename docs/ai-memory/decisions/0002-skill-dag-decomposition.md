# 0002 — Skill DAG as the Unit of Agent Development

**Status:** Accepted (verified end-to-end 2026-05-09)
**Date:** Whitepaper section 2 "Skills and services"

## Context

When agents receive a list of tasks, they need a structure to:
1. Decompose tasks into smaller, manageable pieces
2. Reuse common sub-pieces across tasks
3. Get human feedback at the right granularity (not too coarse, not too fine)
4. Track completion / dependency state

A flat list of tasks loses reuse. A pure tree per task duplicates effort across tasks that share sub-skills.

## Decision

**Tasks form the roots of a directed acyclic graph (DAG) where every node is a skill.** Sub-skills can be shared between root tasks.

Development flow:
1. Agent decomposes each task top-down to discover its DAG of sub-skills
2. Human confirms the structure (one approval gate per skill, on the dashboard)
3. Agents implement skills bottom-up — leaves before composites
4. Each skill goes through: `planned → writing → evaluating → review → done` (or `→ failed → retry` in autonomous mode)
5. Root skills also get a mechanical test via sim `_check_success()` when `task_env` is set

Dependencies are **code dependencies by default** — a higher-level skill may import its dependencies' SKILL.md for design context but doesn't have to read their code. This keeps the agent's context window bounded.

## Consequences

- **Smaller scope per agent iteration**: each sub-skill is a tiny script. Dev can finish in 5-30 sec.
- **Per-step human review**: catches problems early, before they compound into a 1000-line root.
- **Reuse**: `pnp-counter-to-sink` becomes a sub-skill of `coffee-serve-mug` etc.
- **Runtime state does NOT chain across sub-skills**: each sub-skill's `main.py` runs from a fresh sim reset. So later sub-skills are supersets, not continuations. The DAG's value is dev-time and human-attention management, not runtime composition.
- **Sub-skills lack mechanical tests**: no automatic ground-truth for partial state. Sub-skills go straight from `evaluating → review`. Only the ROOT has a `_check_success` mechanical gate.

## Alternatives Considered

- **Monolithic skill per task**: simpler infra, no DAG bookkeeping. But agent loses help on long tasks and can't reuse work across tasks. Tested implicitly via single-entry graphs (`counter-to-sink`, `sink-to-counter`) — works but bigger scope per iteration.
- **Trees, not DAGs**: simpler but loses cross-task reuse.
- **Have sub-skills chain runtime state**: would require persistent sim state across `/code/submit` calls. Conflicts with sim's reset-between-trials assumption. Could revisit if reset overhead becomes the bottleneck.

## Verification

End-to-end pipeline test 2026-05-09 on `graphs/counter-to-cab/`:
- 3 entries (detect-objects + approach-counter + pnp-counter-to-cab)
- 5 of 6 mechanism points passed (M1-M5). M6 (root mechanical test) not observed in that run because evaluator kept failing the root grasp.

See `patterns/dev-model-failure-modes.md` for why sub-skill quality varies by model.

## Related

- `modules/orchestrator.md` — state machine implementation
- `modules/harness-openclaw.md` — per-(target, skill) agent id required to let parallel leaves run without lock collision
- `progress.md` — 2026-05-09 "Skill DAG pipeline end-to-end mechanism verified"
