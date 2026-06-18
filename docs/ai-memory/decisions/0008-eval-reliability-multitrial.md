# 0008 — Skill Evaluation Reliability: Programmatic Precheck + Multi-Trial Promotion

**Status:** Proposed (precheck + rubric implemented in working tree, not yet committed; multi-trial promotion not yet implemented)
**Date:** 2026-06-16 (surfaced during a counter-to-sink re-dev run after the cuRobo fix)

## Context

The orchestrator validates each skill with an LLM evaluator (`run_evaluator`,
Path A) plus, for the root skill only, a mechanical ground-truth test
(`run_mechanical_test`, Path B → sim `/task/success`). Two reliability problems
became measurable during a single counter-to-sink run:

1. **The LLM evaluator's PASS criterion was self-contradictory.** The Evidence
   Hierarchy ranked stdout highest, but the final PASS rule demanded the *camera
   frames* clearly show the object in the sink — making the weakest, most
   hallucination-prone signal a hard gate. It also hard-coded "sink basin" into a
   prompt shared by every skill.

2. **Single-pass promotion cannot measure reliability.** `place-object-in-sink`
   was marked `done` because it passed **one** lucky evaluation. The root's
   ground-truth test then exposed that the underlying placement was only **~60%**
   reliable (object lands just outside the sink in Y, or lands in-AABB but
   `_check_success` doesn't register due to settle/proxy timing). No amount of
   re-running the same code makes it solidly done — it just promotes on a lucky
   trial and stays ~60%.

A third, subtler finding: **`/task/success` itself is not perfectly
deterministic.** It intermittently returns False on a genuinely-successful
placement because the AABB proxy is sampled before the object settles. So even
"ground truth" has flakiness that a single read can be fooled by.

## Decision

Two-part direction. Part A is implemented (working tree); Part B is the proposed
next step.

### Part A — Programmatic precheck before the LLM (implemented)

In `agent_orchestrator.py`, `run_evaluator` now calls `_programmatic_precheck(stdout)`
before spinning up the LLM:

- `/task/success = True|False` present → deterministic verdict, trusted both
  ways, **skip the LLM**. Makes the root and any sub-skill that calls
  `/task/success` (e.g. `place-object-in-sink`) deterministic.
- explicit `FAILURE` marker (no ground truth) → fail. A false FAILURE only costs
  a retry, so trusting it is safe.
- bare `SUCCESS` self-report, or no markers → defer to the LLM (this is the
  "pos=0 reported as success" failure mode; never auto-PASS on a self-report).

The `SYSTEM_PROMPT_EVALUATOR` PASS criterion was also rewritten into an
evidence-anchored boolean rubric (R1 ground truth / R2 final marker / R3 numbers
agree / R4 frames-as-override-only), generalised away from the sink hard-coding.

### Part B — Multi-trial promotion (proposed, not yet built)

`done` must mean "passed K of N trials", not "passed once". A single pass — LLM
or single mechanical trial — measures *capability on one run*, not *reliability*.
Sub-skills need a multi-trial gate with a pass-rate threshold, the same way the
root's mechanical test should run N trials and require a rate rather than
promoting on any single pass.

This also absorbs the `/task/success` settle-timing flakiness: re-running and
requiring a rate is the right way to handle a ground truth that itself jitters
(optionally combined with a settle-wait before the check, or a multi-signal
intersection: placement-coords-in-region AND `/task/success`).

## Consequences

- **Deterministic cases bypass the VLM.** When ground truth or a FAILURE marker
  exists, evaluation no longer depends on the LLM reading sparse JPEGs. In effect
  a *de facto* VLM ablation for those cases — though no quantified ablation
  (log-only vs log+image accuracy over historical recordings) has been run yet.
- **`done` is currently overstated.** Until Part B lands, a `done` sub-skill may
  be as low as ~60% reliable. Treat existing `done` statuses as "passed once",
  not "robust".
- **Real-world gap.** The precheck's strongest rule (`/task/success`) is sim-only.
  On hardware the reliable anchors shift to gripper sensors (pmm,
  object_detected) via rubric R3; the rubric structure transfers, the top anchor
  does not.

## Alternatives Considered

- **Just swap to a stronger evaluator model (e.g. Opus):** orthogonal. A stronger
  model judges a single run slightly better but still can't measure reliability,
  and still can't invent a ground truth that sub-skills lack.
- **Promote on a lucky pass to reach 5/5:** rejected — it manufactures false
  reliability, which is precisely the bug this work exists to eliminate.
- **Trust a single `/task/success` read unconditionally:** kept for now (Part A)
  but known-imperfect due to settle-timing false negatives; Part B (multi-trial)
  is the mitigation.

## Lesson — generalizable

Passing once is capability; passing K/N is reliability. An evaluation gate that
promotes on a single success will silently bless marginal skills, and a stricter
downstream test (or the real world) will later expose them. Where a programmatic
ground truth exists, prefer it over an LLM judge — but remember even ground truth
can jitter, so reliability must be measured over trials, not asserted from one.

## Related

- `modules/orchestrator.md` — `run_evaluator` (Path A), `run_mechanical_test` (Path B), prompt locations
- `decisions/0005-stdout-via-files.md` — why the evaluator reads stdout.log (the markers the precheck keys on)
- `decisions/0002-skill-dag-decomposition.md` — root composes sub-skills; root's ground-truth test is what exposed the sub-skill's hidden ~60%
- `patterns/dev-model-failure-modes.md` — the over-tuning/regression thrash seen on the root grasp
