# AttentionBench Week 1: native Robosuite plan

## Goal

Freeze two official tasks and make the formal runtime independent of ASPIRE:

```text
TidyBot AttentionHarness
  -> TidyBot Robosuite service client
  -> robosuite_sim service
  -> official Robosuite environment/task/controller
  -> Robosuite native success evaluator
```

ASPIRE is read-only migration evidence. Removing its directory from
`PYTHONPATH` must not affect installation, reset, execution, artifacts, or
evaluation.

## Stage 1 — D1/D2/D3: freeze the native chain

1. Freeze `cube_lift -> Lift` and `cube_stack -> Stack`, version pins, seed
   splits, camera/proprioception schema, action shape, timeout, and artifacts.
2. Implement the TidyBot service, client adapter and runner. The service owns
   environment lifecycle and strips object-state oracle fields before sending
   observations; native success is queried only after execution. The
   TidyBot-owned `sensors / arm / gripper` SDK uses the service's local OSC and
   has no PyRoKi service requirement.
3. Validate deterministic reset, no-op `0/5`, reference policy at least `4/5`,
   and zero runtime imports from ASPIRE.

This stage does not require PARCC or an LLM. Its output is a stable benchmark
contract, not a model result.

## Stage 2 — D4/D5: reconnect models

Connect one PARCC development seed only after Stage 1 passes. Validate request,
response, code execution, trace, timeout, evaluator, and artifact continuity.
Model failure must not change task semantics.

Implemented status (development seed `101` only):

- `parcc/GLM` request/response and bounded invalid-program retries are wired.
- Generated code is AST-checked and executed in a separate process with PARCC
  and other credential variables removed.
- The worker attaches to the active `robosuite_sim` session through the public
  service API; it cannot reset the episode or call the evaluator.
- Empty action traces, broad exception swallowing, private attributes, file /
  network imports, and over-time execution are explicit failures.
- `parcc/Qwen` reviews public before/after RGB frames. The review is recorded as
  non-authoritative and can fail independently of native evaluation.
- Both frozen tasks completed the full seed-101 connectivity smoke. Their native
  success was `false`, as expected for a deliberately non-optimized motion
  program; task-solving policy development is not claimed by this gate.

Gate evidence:

- `cube_lift`: GLM valid program executed, 23 simulator steps, Qwen review saved.
- `cube_stack`: GLM valid program executed, 34 simulator steps, Qwen review saved.
- Offline suite: 63 passed; native integration suite: 8 passed.

## Stage 3 — D6/D7: parity and freeze

Export evaluator decisions from the legacy path and compare them offline by
`(task_id, seed)`. Required agreement is 100%. Then freeze the protocol and run
the first controlled model comparison; held-out seeds remain opt-in.

## Gate to leave Stage 1

- Official PyPI Robosuite runs from a TidyBot-owned environment.
- AttentionHarness and `robosuite_sim` run as separate processes.
- No formal runtime module imports ASPIRE.
- Both tasks reset deterministically for the same seed.
- Reference policy succeeds at least 4/5 per task; no-op succeeds 0/5.
- Artifacts contain result, trace, initial observation, and final observation.
