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

Implemented D6 status:

- Both runtimes report `robosuite==1.5.1`, `mujoco==3.3.0`, and
  `numpy==1.26.4`.
- The normalized AST hash of Lift `_check_success` and Stack
  `_check_success + staged_rewards` matches between the ASPIRE submodule and
  official PyPI installation.
- Thirty decisions were compared across tasks `cube_lift / cube_stack`, seeds
  `101-105`, and reset-failure / boundary-failure / synthetic-success probes.
- Final agreement is `30/30 = 100%`, with no missing keys or mismatches.
- The first Stack contact probe used exact geometric tangency and produced
  `83.33%`; that failed report is retained. The fixed probe uses a documented
  shallow overlap because contact generation at exact tangency is not stable
  across the ASPIRE fork and PyPI build.
- This gate proves evaluator semantics only. It explicitly does not claim reset,
  observation, controller, placement-sampler, or trajectory parity.

Implemented D7 status:

- `protocol/v1/protocol.json` freezes tasks, versions, seed policy, model
  parameters, SDK boundary, timeouts, schema names, native success authority,
  assistance modes, AdvisorProxy boundary, and policy roles.
- `freeze_manifest.json` hashes every semantic source/evidence file and can be
  deterministically regenerated and verified.
- The native harness contract and privileged reference policy are frozen.
- Two non-oracle public RGB-D task policies run in the credential-free sandbox.
  On development seeds 101--125, `cube_lift` passes 25/25 and `cube_stack`
  passes 25/25 using Robosuite native success. The evidence report records each
  episode and the exact policy hashes.
- Public RGB and depth are now both canonicalized to OpenCV top-left pixel
  coordinates. This fixes the earlier RGB-D deprojection mismatch without
  exposing simulator object state.
- `Benchmark--Proxy` fixes `parcc/Qwen` as the cached AdvisorProxy and prohibits
  human responses. `Live--Human-first` waits 60 seconds and then routes to the
  same proxy; it is validation-only and cannot enter primary ranking.
- Run mode and assistance budget become immutable when a run starts. Request
  states cover pending, answered, timeout, cancelled, and fallback.
- The manifest sets `heldout_ready=true`; held-out execution still requires an
  explicit flag and no held-out seed has been run during D7 development.

Stage-3 boundary: D6 and D7 are complete for the Robosuite harness. This means
the frozen task-policy gate is eligible for an explicitly authorized held-out
run. It does not mean the paper's primary experiment is ready: the seven
Attention-System policies, full request/memory loop, RoboCasa matrix, and UI
integration remain the next stage.

## Post-D7 service-boundary refinement

Before any held-out run, the SDK implementation moved out of AttentionHarness
into the top-level `tidybot_sdk` shared core. Its client-side interface is named
`RobotBackend`, avoiding confusion with the actual server process. The concrete
Robosuite chain is `TidyBotSDK -> RobosuiteRobotBackend -> RobosuiteSimClient ->
robosuite_sim service`; RoboCasa and real-robot adapters are intended peers.
The Robosuite service owns metric-depth and camera-calibration publication,
while the shared SDK supplies a deterministic `pixel_to_world()` convenience
method using only those public values.

The shared contract exposes high-level arm / gripper operations rather than
Robosuite's seven-dimensional action vector. `ModuleRobotBackend` adapts the
existing agent_server `ArmAPI / SensorAPI / GripperAPI` objects, and
`PerceptionModuleRobotBackend` explicitly opts RoboCasa into its existing
`find_objects()` service. Robosuite does not advertise that optional capability.

This is intentionally not a Robosuite `find_objects()` implementation. The
RoboCasa simulation version uses privileged segmentation, so copying its
behavior would violate AttentionBench's non-oracle observation contract. The
D7 manifest is regenerated after this pre-heldout protocol change.

## Gate to leave Stage 1

- Official PyPI Robosuite runs from a TidyBot-owned environment.
- AttentionHarness and `robosuite_sim` run as separate processes.
- No formal runtime module imports ASPIRE.
- Both tasks reset deterministically for the same seed.
- Reference policy succeeds at least 4/5 per task; no-op succeeds 0/5.
- Artifacts contain result, trace, initial observation, and final observation.
