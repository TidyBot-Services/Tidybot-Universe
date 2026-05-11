# 0005 — Stdout / Stderr as Files in Exec Recording Dir

**Status:** Accepted (committed + pushed 2026-05-09)
**Date:** 2026-05-09 (after debugging an infinite false-fail loop)

## Context

Each code execution by an agent produces a recording directory at `agent_server/logs/code_executions/<exec_id>/`. Originally this dir contained:

- `metadata.json` — execution summary (timing, frame_count, cameras)
- `state_log.jsonl` — robot state samples
- Camera frame JPEGs

Stdout/stderr from the subprocess were captured by `code_executor.py` but only returned via the `/code/jobs/{id}` API (in `result.stdout`). The evaluator agent's prompt told it to "Read metadata.json for execution summary, stdout, stderr, duration" — but **stdout was never a field in metadata.json**.

Net effect: evaluator looks for stdout in metadata.json, doesn't find it, concludes "no code output", false-fails every dev iteration. Autonomous mode re-spawns dev forever.

This had been silently affecting evaluations for an unknown duration before being noticed.

## Decision

**Persist stdout/stderr to disk as separate files in the exec recording dir.**

Two coordinated changes:

1. **`agent_server/code_executor.py`** at end of `execute()`: write `result.stdout` → `stdout.log`, `result.stderr` → `stderr.log`. Files live alongside `metadata.json` and `state_log.jsonl`.

2. **`SYSTEM_PROMPT_EVALUATOR`** in `agent_orchestrator.py`: update instructions to read `stdout.log` + `stderr.log` files directly. Clarify metadata.json describes the run, not its output.

`metadata.json` stays a structured summary; logs stay separate. Industry-standard separation (CI logs aren't usually embedded in metadata blobs).

## Consequences

- **Evaluator now grounded in actual output**: success markers like `APPROACH_OK 0.746`, `DETECT_OK`, `FAILURE: <reason>` are visible. False-fail loops mostly eliminated.
- **stdout duplicated**: lives both in `/code/jobs/{id}` API result AND in `stdout.log`. Acceptable cost (typically <50KB per run).
- **Old exec dirs lack stdout.log**: pre-fix recordings will look "empty" to evaluator. Not worth backfilling; they're not actively analyzed.
- **Disk usage rises slightly** — stdout files. Not significant compared to camera frames.

## Alternatives Considered

- **Write stdout into metadata.json** (add field): rejected — couples structured summary with free-form text. Doesn't scale if we add stderr / profile / network logs.
- **Have evaluator query `/code/jobs/{id}` API directly**: rejected — evaluator runs as a Claude SDK / OpenClaw subprocess in its own working dir; doesn't have orch-level access to the agent_server. Files in a known dir are simpler.
- **Update evaluator prompt only (leave files un-written)**: rejected — evaluator can't read what doesn't exist on disk. Both halves of the fix are necessary.

## Lesson — generalizable

When three components share a contract and each makes independent assumptions about it ("producer puts X in Y", "consumer reads X from Z"), bugs that aren't logged anywhere can run for weeks. The fix is **explicit, documented data interfaces** between layers — not "stdout is somewhere, you'll figure it out".

## Related

- `patterns/stdout-disconnect.md` — the bug pattern + how to spot similar disconnects
- `modules/agent-server.md` — code_executor change location
- `modules/orchestrator.md` — evaluator prompt location
