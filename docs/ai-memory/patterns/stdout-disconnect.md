# Pattern — Data Captured to API but Not to Disk (Evaluator False-Fail Loop)

**Discovered:** 2026-05-09 (debugging an infinite dev re-spawn loop)

## TL;DR

Three components shared an undocumented contract about where stdout lives. Producer wrote it to one place, consumer looked in another. Evaluator never saw the dev's output, false-failed every iteration, autonomous mode re-spawned dev forever. The system "worked" without complaint because nothing logs "consumer disagrees with producer about contract".

## Concrete instance

- **`code_executor.py`** captured subprocess stdout into a list, returned via `/code/jobs/{id}` JSON (`result.stdout`)
- **`ExecutionRecorder`** wrote `metadata.json` + `state_log.jsonl` + camera frames to the exec dir
- **Evaluator prompt** said "Read metadata.json for execution summary, stdout, stderr, duration"

But `metadata.json` was never populated with stdout. The evaluator dutifully read it, found no stdout field, reported "No stdout or stderr — zero code output", failed the dev iteration. Dev retried. Same outcome. Infinite loop.

Dev's actual code printed clear success markers (`APPROACH_OK 0.746`, `DETECT_OK`) that should have triggered a pass. None of it was visible to the evaluator.

## Why it was hard to spot

- API consumers (orchestrator status, dashboard `/entries`, `submit_and_wait.py`) all worked fine — they fetched via API.
- The recording dir for each exec looked "complete" — metadata + state log + frames all present. Only careful inspection revealed no `stdout.log` file there.
- The evaluator's prompt was 70+ lines describing how to read recordings. The wrong-location-for-stdout instruction was on line 12. Easy to miss.
- Evaluator agent never explicitly said "I expected stdout in X but found nothing" — it just reported its verdict as "no output".

## Generalization

Whenever data flows through `producer → storage → consumer`, **document the contract explicitly**. "Stdout will be available somewhere" is not a contract. "Stdout will be at `<exec_dir>/stdout.log`" is.

Smell checks:
- A consumer's prompt or code says "read X for Y" but X never explicitly contains Y → contract violation
- Two layers were written by different people / sessions with no shared interface doc → high risk
- A "this always seems to fail but the code looks right" loop → check what the failing component is *actually* looking at, vs what's *actually* there

## Fix patterns

- **Make storage match consumer's mental model**: write files at the paths consumers expect to read. We added `stdout.log` + `stderr.log` to the exec dir.
- **Or update the consumer to know the right path**: we also updated the evaluator prompt to explicitly read `stdout.log` (so even if files aren't there, the prompt tells evaluator to handle that case).
- **Belt-and-suspenders**: do both, as we did. Future evaluator agents will read the right file; old recordings still don't have stdout.log and the prompt notes that gracefully.

## How to spot in other parts of the codebase

Audit any place where:
1. A subprocess produces output (CodeExecutor, sim launch, evaluator subprocess itself)
2. That output is captured by the parent
3. Something else (logs, UI, downstream agent) later wants to read it

Verify: is the output **persisted to disk** at a path the downstream component knows? Or does it live in memory / a transient API only?

For us, candidates to audit (not yet done):
- Evaluator agent's own stdout/stderr — is it captured for debugging when evaluator misbehaves? (Probably no, only the `EVAL_RESULT` JSON envelope is parsed.)
- Sim's HTTP logs — are 4xx responses logged somewhere I can read after the fact?
- cuRobo service `:7000` logs — accessible if a plan fails?

## Related

- `decisions/0005-stdout-via-files.md` — the design decision
- `modules/agent-server.md` — code_executor change location
- `modules/orchestrator.md` — evaluator prompt location
- Commits: `agent_server 03ed465`, `Tidybot-Universe 94bb530`
