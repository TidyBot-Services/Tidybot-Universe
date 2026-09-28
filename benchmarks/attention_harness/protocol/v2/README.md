# AttentionBench v2: perception tracks

This is a new experiment definition, not an amendment to the frozen v1
Robosuite non-oracle result. The v1 freeze manifest and its evidence remain
unchanged.

`sim_gt` means that a simulator may supply object identity and position,
through RoboCasa `/perceive` or Robosuite `/v2/perceive_gt`. It is the **GT-perception
AttentionBench** track: the experimental question is when to request help and
how to use trace and memory, not visual recognition. `vision` means an RGB-D
detector and calibrated coordinate conversion. Real hardware is `vision` only.

The mode is required at run creation, never changes during the run, and cannot
fall back to `sim_gt` when vision fails. Both modes expose the same
`sensors.find_objects()` object shape; positions are in the backend's declared
control frame. The `source` field, run artifact, SDK event trace, and memory
applicability retain provenance. Native success and evaluator debug are
evaluation-only. Neither the legacy v1 non-oracle score nor real-robot vision
success rates may be pooled with the v2 simulator GT score.

The RoboCasa and Robosuite GT tracks are not frozen or held-out eligible yet. First complete
the two-task, five-development-seed chain check; then meet 25/25 native
successes per task on the frozen development split. Privileged teleport
probes do not count as policy successes.

The current Trace/Advisor closure records a terminal attempt before creating
Raw Trace and its Advisor-safe projection. The packet explicitly marks absent
Dev hypotheses as `unknown`, includes the approved code/config identity and
up to three prior Advisor-visible failures with evidence digests, and omits
native evaluator and simulator-private fields. The v2 proxy uses a shared
SQLite cache under the experiment artifact root; its semantic key includes
visible evidence, experiment conditions, model request, prompt, schema and
projection protocol, while ignoring run-local IDs and clocks. The real-Service
development smoke and exact commands are indexed in
`evidence/trace_advisor_ui_closure_2026-09-27.json`.
The formal CLI accepts a Dev hypothesis only through a generation receipt
whose source path and SHA-256 match the approved policy; the run summary keeps
that receipt's path and digest. A direct policy run without such a receipt
records the hypothesis as unknown.

## Shared seven-policy development scheduler

### Independent Robosuite formal attempt path (seed-101 engineering acceptance)

`robosuite_memory/formal_cli.py` calls the separate
`sim_gt_attention_run.run_robosuite_formal_attempt` entry and the real
`RobosuiteFormalSuiteRunner`. The seven-policy `trusted_dev` scheduler above
is unchanged. A formal attempt pins the reviewed policy and config SHA-256,
starts a dedicated Service from the clean sibling `robosuite_sim-service`
checkout at the config's full commit, and gives the policy only a bubblewrap
process with SDK RPC to the parent. The parent owns independent safety,
native evaluation, and hashed trace/safety/sandbox/native artifacts. Because
the Service handles MuJoCo on one HTTP thread, an episode deadline or operator
cancel terminates its dedicated process group and confirms it is gone; it
never treats an HTTP client timeout as evidence that an action stopped.

Run the four development-seed probes separately and write a machine-checked
report (normal, in-flight action deadline, operator cancellation, and safety
rejection):

```bash
python -m benchmarks.attention_harness.robosuite_memory.formal_acceptance \
  --service-source-root ../robosuite_sim-service \
  --artifact-root ../attentionbench-formal-robosuite-dev \
  --evidence benchmarks/attention_harness/protocol/v2/evidence/robosuite_formal_seed101_acceptance_2026-09-27.json
```

The acceptance script records exact commands, revisions, results, process
stop receipts and SHA-256 values. This single-seed engineering check does not
freeze the seven-policy matrix or enable held-out experiments. Every result
still has `formal_eligible=false` until the independent acceptance and
development stability matrix are complete.

### Independent RoboCasa formal attempt path (seed-101 engineering acceptance)

`robocasa_native/formal_cli.py` calls the separate
`sim_gt_attention_run.run_robocasa_formal_attempt` entry and the real
`RobocasaFormalSuiteRunner`. The runner starts its own simulator and Agent
Server on an isolated port offset; it checks both source revisions and
working-tree digests against the approved config. Its v3 config also checks
the imported `robocasa_tasks` checkout, simulator Python binary, ManiSkill
version, the ManiSkill Python-source tree digest, and relevant file hashes
before and after the attempt.
The policy runs inside the
same bubblewrap SDK-RPC boundary used by Robosuite, with RoboCasa's additive
`base.move_delta` capability. The parent owns reset and scene/camera
attestation, independent safety monitoring, `/task/success` evaluation, and
hashed trace/safety/sandbox/native artifacts. Deadline or cancellation stops
both service process groups and records their reaping status.
After reset, the Runner also compares the Service's task language with the
approved config; a mismatched prompt fails before policy execution. Earlier
seed-101 configs contained stale object descriptions (`yogurt` and
`condiment bottle`); current configs use the Service-attested language.
`evidence/robocasa_sink_seed101_language_rejection_2026-09-27.json` verifies
the old `yogurt` config is rejected before the policy worker starts; the
attempt still writes failure artifacts and reaps both Services.
`evidence/robocasa_cab_seed101_language_attested_noop_2026-09-27.json`
checks the corrected cab prompt on a separate real-Service no-op attempt.

The seed-101 `counter_to_sink` configuration is
`formal_robocasa_counter_to_sink_seed101.json`. Reproduce its five engineering
probes with the Python runtimes used for the simulator and Agent Server:

```bash
python -m benchmarks.attention_harness.robocasa_native.formal_acceptance \
  --sim-source-root ../maniskill_sim-attention-variation \
  --agent-source-root /path/to/agent_server \
  --task-source-root /path/to/robocasa_tasks \
  --sim-python /path/to/maniskill/bin/python \
  --agent-python /path/to/maniskill/bin/python \
  --artifact-root ../attentionbench-robocasa-formal-seed101 \
  --evidence benchmarks/attention_harness/protocol/v2/evidence/robocasa_formal_seed101_language_attested_2026-09-27.json
```

The report covers normal operation, overall timeout, operator cancellation,
in-flight SDK action cancellation, and independent safety rejection. It
records the exact commands, service identities, results, process
stop receipts, and artifact hashes. The current Agent Server and simulator
checkouts contain pre-existing uncommitted changes; the config pins their
exact working-tree digests for this engineering check. Earlier seed-101
reports predate the current task fix, v3 runtime pin, or language attestation;
use the language-attested report above for the current configuration. The no-op normal probe
does not solve the task. This single-seed result does not satisfy the
two-task development stability gate or freeze a formal seven-policy matrix;
all runs remain `formal_eligible=false`.

The original `counter_to_cab` seed-106 Service failure and sampler diagnostics
are preserved in the [blocker evidence](evidence/robocasa_cab_seed106_variation_blocker_2026-09-27.json).
The registered `single_stage/kitchen_pnp.py` task selected a counter near the
cabinet, then restricted its object sampling region to the strip immediately
under the cabinet; at seed 106 the strip was too narrow for the target object.
Removing that second restriction, while keeping the same counter, front/back
placement, and native evaluator, restored both task objects. The independent
[25-seed discovery](evidence/robocasa_cab_101_125_variation_discovery_2026-09-27.json)
now records two stable reset identities and evaluator objects for every
`counter_to_cab` development seed 101–125. This is variation discovery,
not 25/25 task-solving policy success or formal eligibility.
The former failure seed also ran through the v3 formal Runner as a no-op
negative control: [seed-106 formal evidence](evidence/robocasa_cab_seed106_formal_noop_2026-09-27.json)
checks reset identity, native failure, independent safety, artifact hashes,
and both Service stop receipts. Its `formal_eligible` remains false.

`sim_gt_attention_run.py` runs the same seven decision policies on either
RoboCasa or Robosuite. It keeps the simulator-specific reset, actions,
perception, native evaluator and raw trace in the existing suite runners;
between attempts it dispatches demo-first, retries, Advisor requests, trace
inspection and trusted Memory retrieval. One `attention_run.json` links every
attempt, request, decision and cross-attempt assistance total. A hint creates
a candidate memory only when the source native evaluator is authoritative.
Both suite bindings wrap actions with the independent command/proprioception
safety monitor and link its per-attempt evidence. Additional approval flags
must come from an independent source; approval is fail-closed without an
external human decision callback. This remains a
trusted-callback development path, not a formal sandboxed policy run.

```bash
python -m benchmarks.attention_harness.sim_gt_attention_cli \
  --suite robosuite --task cube_lift --seed 101 \
  --attention-policy full_trace_aware_attention_planner \
  --robot-policy my_lab_policy:run --max-attempts 3
python -m benchmarks.attention_harness.sim_gt_attention_cli \
  --suite robocasa --task counter_to_sink --seed 101 \
  --attention-policy reactive_help --robot-policy my_lab_policy:run \
  --confirm-simulator-agent
```

The seven IDs are `autonomous`, `demo_first`, `reactive_help`,
`retry_k_then_ask`, `budget_matched_random_escalation`,
`trace_aware_hint_only`, and `full_trace_aware_attention_planner`.
`demo_first` requires `--demo-prior` pointing to an approved JSON manifest and
`--approved-demo-sha256` containing the manifest file's pre-run digest. The
manifest schema is `attentionbench.public-demo.v1`: `suite`, `task_id`,
`approval: {id, approved_by}`, and one or two `assets` with unique kinds
`public_video` / `action_trajectory`, absolute paths, SHA-256 digests, and
`source: public_sdk`. Trajectory JSON contains `source: public_sdk` and at
most 32 `{operation: "sdk.…", arguments: {...}}` steps; private/oracle fields
are rejected. The runner verifies all digests before attempt 0, snapshots the
assets, and passes only the bounded public projection to the robot policy.
No online assistance request is allowed after viewing the demo.

`--policy-config` accepts the decision policy's JSON parameters (for example,
`{"k": 2}` for retry-k). `budget_matched_random_escalation` requires an
explicit, externally pre-registered `target_request_count`, chosen as the
planned full-method help quota **before either arm's outcomes are observed**.
Both CLIs require `--approved-policy-config-sha256` for this arm and verify
the exact config bytes at read time.
It must fit the assistance budget and the `max_attempts - 1` failure slots.
At run creation, SHA-256 of `seed:failure_slot` ranks all slots; the lowest
`target_request_count` slots are fixed in `random_preregistration.json` before
attempt 0. The random arm uses only its own failure slot and remaining budget;
it never reads the full method's trace or outcome. `random_matching` reports
target, selected and realized slots, count deviation, and whether early native
success made matching impossible. Neither the target nor slots change after
the run starts. An approved UI profile must supply a digest-locked random
policy config. Real service/GLM and frozen-seed effectiveness
testing are separate from the fake-world wiring tests. Use `--store-path` to
share trusted Memory across runs; without it, each development sequence has
an isolated store and cannot retrieve memories from earlier sequences.

The full trace-aware arm records projected event/evidence IDs for hint,
approval, and safety interrupt decisions. An independent unsafe signal stops
before policy deliberation; a safety-classified public trace produces a local
critical interrupt decision and stops without creating an Advisor request or
waiting for a human. Approval remains fail-closed. All runs here retain
`formal_eligible=false`.

## Memory v2

`memory.json` defines the additive v2 memory contract. The implementation is
the separately versioned [attention_memory_service](https://github.com/TidyBot-Services/attention_memory_service)
package; the old Universe module paths are compatibility imports. Both simulator GT runners use
one shared SQLite database by default (`ARTIFACT_ROOT/attention_memory.sqlite3`)
and retain a separate bundle inside each episode directory. GLM advice is
recorded as `advisor_proxy`, **not** human attention. A manually promoted v1
memory lacking v2 provenance cannot be retrieved by the v2 runner.
The policy sees a `memory_catalog` without guidance and must call
`retrieve_memory(memory_id)` to receive a hint. Merely listing the catalog is
not recorded as a memory use.

The promotion gate derives paired results from stored native attempts and raw
traces. It requires five different development seeds, explicit candidate
exposure only in treatment, identical run conditions, and hashed outputs from
an independent safety monitor. The policy callback's code and captured values
are fingerprinted. Every validation pair and case must use one fixed policy
version; the failed source policy's digest is reported separately because a
repair may change it before validation.
Source human-attention cost, subsequent help
credits, robot time, and safety are reported separately. The RoboCasa paired
executor wraps the backend in an independent command/proprioception monitor.
It writes one safety artifact per attempt, including sampled state, proposed
actions, limit violations, and explicit coverage. It can reject an excessive
command before dispatch. The Memory Service rejects safety files with a
violation count inconsistent with their event evidence. The backend has no independent collision/contact
telemetry, so this is **not** a full physical-safety certification.
The frozen validation plan names every scene, object set, camera configuration,
and task variant. Both arms must attest the same applied variation and
execution-configuration digest in their raw traces; all planned cases must
finish before promotion. The impact artifact lists supporting successes,
counterexamples, empirical confidence, and the exact variation tuples still
eligible for trusted retrieval. Any counterexample excludes its tuple.
Assistance savings count toward promotion only on paired trials where both
arms achieve native success; savings from two failed arms remain a cost metric,
not evidence of an effective intervention. An empty validated retrieval scope
blocks promotion. Each pair is also checked for safety regression; an unsafe
treatment cannot be offset by a safer treatment in another variation.
The impact report also gives the treatment success fraction's sample size and
a Wilson 95% binomial reference interval. Because the frozen variations are
not an IID population sample, that interval is descriptive only and does not
change the preregistered promotion gate or establish transfer confidence.

## Memory Agent and Memory Service

The v2 memory workflow has an agent orchestration layer and a service-owned
persistence layer. Dev and Deploy are workflows, not peer backends of the
Memory Service:

```text
Dev workflow / Deploy workflow → AttentionHarness → per-run trace and result
                                      │ answered hints / memory use
                                      ▼
                         Memory Agent (distill, plan trials)
                                      │
                                      ▼
                         Memory Service (authority and retrieval)
                           ├── attention_memory.sqlite3
                           └── memory/<safe-memory-id>/ (generated view)
```

`MemoryAgent` can run in-process or through the authenticated HTTP
`MemoryServiceClient`; it never edits SQLite directly. The Memory Service is
the only authority for candidate creation, evidence registration, and
promotion. The RoboCasa GT runner now routes answered GLM hints through the
Agent, and policy retrieval through the Service. The Agent's default distiller
is deterministic and strictly parses GLM's hint schema. A model-based
distiller can be injected later, but its output still passes the same Service
gate.

For a local daemon, provide a nonempty `ATTENTION_MEMORY_API_KEY` of at least
16 characters and run:

```bash
python -m attention_memory_service \
  --store-path artifacts/attentionbench-v2-gt/attention_memory.sqlite3
python -m benchmarks.attention_harness.memory_agent_cli preflight MEMORY_ID \
  --cases /path/to/frozen-validation-cases.json \
  --code /path/to/robocasa-generated-policy.py
python -m benchmarks.attention_harness.memory_agent_cli plan MEMORY_ID \
  --cases /path/to/frozen-validation-cases.json
```

`preflight` verifies the candidate's raw source evidence, policy source,
variation matrix, and any already frozen plan. It reports completed and
remaining seeds without registering the plan, resetting a simulator, or
running paired trials. Use `--policy module:function` for a trusted callback;
`--code` is currently RoboCasa-only. It does not establish intervention
effectiveness or qualify a memory for promotion.

The daemon binds to `127.0.0.1:8768` by default. Remote client URLs require
HTTPS; do not expose the daemon on a public interface without TLS and normal
deployment controls. HTTP validation pairs upload safety-monitor JSON into the
Service's persistent evidence directory; they do not depend on shared local
paths. `RoboCasaPairedTrialExecutor` drives either a trusted development
callback or a sandboxed generated policy through the same task, seed, policy
code, budget, and simulator configuration for both arms. The generated path
retains deadline and cancellation preflight under the independent safety
monitor, which also checks mobile-base commands.
Only candidate exposure differs. Each episode keeps `trial_config.json`,
`result.json`, its raw trace/bundle, and `safety_monitor.json`. The Memory
Service independently checks the stored attempts, native outcomes, retrieval
events, and safety-file hashes before promotion. Registered seed pairs survive
restart and are skipped on a resumed validation run. The opt-in
`attention_orchestrator.py` now schedules formal-run candidates through
`attention_memory_dispatch.py`. An approved Robosuite validation recipe runs
the durable `memory_validation_task.py`; each arm has a hashed receipt and
the Service alone decides promotion. A candidate without an approved repair
remains `awaiting_approved_repair`, as in the RoboCasa engineering smoke.
An approved RoboCasa recipe uses the same durable task with a sandboxed
generated policy and a fresh, attested RoboCasa Service plus Agent Server for
each arm. Its approval names `candidate_memory_id` when a new candidate was
derived from the same verified source request, exact `cases_file` and
`policy_file` SHA-256 values, all three source identities and the simulator
runtime identity, `port_offset`, zero assistance credits, the 200 SDK-call
limit, at most 90/240/300 seconds for Agent job/policy/Service, and Safety
limits no looser than 0.25/0.5 m. The task checks source request and attempt
lineage, freezes the plan before actions, and records each arm's result, raw
trace, bundle, trial config, Safety and dual-Service stop receipt with SHA-256.
An arm begun without a durable receipt is blocked on restart rather than
replayed; a per-task process lock also serializes concurrent dispatchers.
This route does not change the Memory Service promotion gate.
Paired validation remains a separate development-only executor; the task
source and later trusted-use attempts run through the formal Harness boundary.
No branch of this route falls back to `trusted_dev` for a formal attempt.

For a candidate already produced by a failed GT run, start the RoboCasa and
simulator-only agent services, then run five development-seed pairs:

```bash
python -m benchmarks.attention_harness.memory_agent_cli validate MEMORY_ID \
  --suite robocasa \
  --cases /path/to/frozen-validation-cases.json \
  --policy my_lab_policy:run \
  --artifact-root artifacts/attentionbench-v2-gt \
  --store-path artifacts/attentionbench-v2-gt/attention_memory.sqlite3 \
  --confirm-simulator-agent --promote
```

The policy entry point must match the source run; `--promote` asks the Service
to apply its gate and may fail even when all trials execute. This command is
development-only, not a formal score. The in-memory fake-world smoke test
proves candidate-to-promotion behavior. A live RoboCasa no-op smoke completed
five control/treatment pairs with both camera views and correctly refused
promotion; successful policy trials and the two-task 25/25 stability gate
remain pending. The monitor defaults to a 0.25 m commanded
delta and a 0.5 m observed step; both limits are CLI-configurable, recorded in
each trial configuration, and must remain identical within each pair.
For a RoboCasa candidate sourced from generated code, replace `--policy` with
`--code /path/to/policy.py --timeout-seconds 300`. This uses the spawned policy
process on both arms. Its fake-world paired smoke passes, but live generated-
policy memory promotion has not been accepted. A separate fake-world test
exercises trusted-memory retrieval from the spawned policy process through
the parent RPC, Service-issued use grant, raw trace, and versioned outcome
ledger; this is interface evidence, not a simulator-effectiveness result.
The cases file is a JSON array with at least five unique development seeds;
each item contains `seed`, `scene_id`, `object_set_id`, `camera_config_id`,
`camera_names`, `task_variant_id`, and `task_prompt`. The two prompt variants
share one `task_id` and native evaluator. Use the discovery tool against an
updated `maniskill_sim` to obtain actual scene/object IDs and camera receipts:

```bash
python -m benchmarks.attention_harness.robocasa_native.discover_variations \
  --task counter_to_sink --seeds 102,103,104,105,106 \
  --camera-config base_camera --camera-config wrist_camera \
  --task-prompt 'place mug in sink' \
  --task-prompt 'move the mug into the sink' \
  --output /path/to/frozen-validation-cases.json
```

Every axis must vary. The simulator-side variation contract is implemented in
`maniskill_sim` at commit `4e59015` and was smoke-tested against a live local
RoboCasa task. Its reset reconfigures the scene for each development seed and
reports IDs computed from realized fixture geometry and object configurations;
it never treats a requested label as evidence. Scene IDs attest geometry, not
texture/style, and must not be used as evidence of visual-style variation.

Robosuite uses the same Memory Service, candidate and promotion gate through
an additive v2 adapter/runner. The frozen v1 non-oracle adapter and score are
untouched. Start a dedicated instance with
`benchmarks/attention_harness/start_robosuite_v2_service.sh` after
`setup_env.sh`; this launcher imports the separately installed service instead
of the frozen v1 package retained in the Universe checkout. Its
privileged endpoint must not be reachable by formal v1 non-oracle policies.
Discover actual reset identities and active camera views first:

```bash
python -m benchmarks.attention_harness.robosuite_memory.discover_variations \
  --task cube_lift --seeds 102,103,104,105,106 \
  --camera agentview --camera frontview \
  --task-prompt 'Lift the cube' --task-prompt 'Raise the cube' \
  --output /path/to/robosuite-cases.json
python -m benchmarks.attention_harness.robosuite_memory.sim_gt_cli \
  --task cube_lift --seed 102 --perception-mode sim_gt --policy my_lab_policy:run \
  --camera-name agentview --variation /path/to/one-case.json \
  --artifact-root artifacts/attentionbench-v2-gt
python -m benchmarks.attention_harness.memory_agent_cli validate MEMORY_ID \
  --suite robosuite --cases /path/to/robosuite-cases.json \
  --policy my_lab_policy:run --sim-url http://127.0.0.1:8082 \
  --artifact-root artifacts/attentionbench-v2-gt \
  --store-path artifacts/attentionbench-v2-gt/attention_memory.sqlite3 --promote
```

`sim_gt_cli --variation` expects one JSON object without `seed`, selected
from the discovered cases. The scene and object-set IDs hash realized
object/robot positions at reset; they prove repeatable initial state, not
independent changes in room geometry or object type. Task-prompt variants
keep the same `task_id` and native evaluator. This trusted callback track is
development-only (`formal_eligible=false`); successful strategy promotion
and use remain for the later effectiveness tests.

RoboCasa also has a generated-policy sandbox smoke entry point (still not a
formal score):

```bash
python -m benchmarks.attention_harness.robocasa_native.generated_policy_cli \
  --task counter_to_sink --seed 101 --perception-mode sim_gt \
  --code /path/to/policy.py --hypothesis 'Expected repair mechanism' \
  --confirm-simulator-agent --artifact-root artifacts/attentionbench-v2-gt
```

The policy runs in a spawned child process. It can import only the shared
`robot_sdk` sensors/arm/gripper facade plus the additive RoboCasa v2 `base`
facade, and calls those methods through a
parent-owned RPC broker; the child has no simulator, evaluator, or Memory
Service client. Source code and hypothesis are preserved in RawExecutionTrace
and its Advisor projection. `run_robocasa_generated_policy_sequence` carries
only projected failure summaries and received guidance between attempts. The
v2 sim_gt Advisor cache ignores run IDs/timestamps but retains the prompt,
policy, evidence, actions, and failure history in its key. A whole-policy
process timeout is enforced. The independently versioned `agent_server` now
issues a per-job cancellation token and confirms terminal `cancelled/stopped`
state before the runner reports an action deadline. The result artifact keeps
the cancellation receipt without the token. This has a live simulator-backed
smoke on both RoboCasa tasks at seed 101; the complete timeout/transport
acceptance matrix and development-seed
stability gate are still outstanding, so runs remain `formal_eligible=false`.

The first simulator-backed generated-policy smoke is recorded in
`evidence/robocasa_generated_policy_live_smoke.json`: both tasks completed
no-op negative controls on development seeds 101–105, and each completed a
single simulator-only gripper action on seed 101. The Advisor→candidate Memory
link was exercised with a clearly separated synthetic responder. PARCC GLM,
task-solving policy success, and formal scoring were **not** validated by
that smoke.

An additional isolated motion/timeout check is recorded in
`evidence/robocasa_generated_policy_motion_timeout_smoke.json`. A 1 cm SDK arm
delta moved the simulated end effector about 9.3 mm. A looping policy timed
out with no action job. A deliberately mismatched action-job deadline exposed
an in-flight action completing after policy timeout; the generated-policy
entry point now rejects an action backend timeout longer than its policy
timeout before resetting the simulator. That report predates the per-job
cancellation implementation. See
`evidence/robocasa_generated_policy_cancellation_smoke.json` for the live
follow-up; it does not retroactively turn earlier attempts into formal scores.

The independent ManiSkill Franka service originally ignored orientation in
Cartesian IK, so a policy's wrist-rotation action could report completion with
an unchanged gripper attitude. The service now uses a 6D pose Jacobian and
rejects unreachable poses. The live wrist probe in
`evidence/robocasa_franka_pose_bridge_smoke.json` shows the end-effector
quaternion and relative fingertip heights changing after a 0.5-radian pitch
command. Cabinet grasp and task-level success remain unverified.

The subsequent PARCC GLM smoke is recorded in
`evidence/robocasa_glm_live_smoke.json`. On a live RoboCasa failure trace,
the v2 Advisor produced schema-valid advice grounded in the public task
instruction and correctly identified `boxed_drink`; the answered request
consumed one assistance credit and 6,056 tokens, then staged a linked candidate
Memory. This is a connectivity/provenance result, not evidence that the advice
improves policy success. The v2 Advisor now has a fixed 1,024-token output cap
and two-attempt JSON-format retry with aggregate token accounting; both v2
simulator runners default to a 30,000-token run budget.

`evidence/robocasa_glm_guided_action_smoke.json` records the next live
development-seed check: the generated-policy runner itself requested GLM after
a no-action failure; a developer manually turned the hint into a bounded
`boxed_drink` approach policy; the next raw trace and Advisor packet retained
the guidance, and the simulator-backed arm moved about 1.6 cm. Native task
success remained false. This checks the advice-to-action handoff only, not an
automatic repair agent or policy effectiveness.

Human lifecycle controls use a separate `ATTENTION_MEMORY_OPERATOR_KEY` in
addition to `ATTENTION_MEMORY_API_KEY`. For example:

```bash
python -m attention_memory_service.operator_cli --actor operator-1 \
  disable MEMORY_ID --reason 'camera mismatch'
```

`rollback` and `set-expiry --expires-at UNIX_SECONDS` use the same operator
CLI. The Service records actor and reason; disabled, rolled-back, or expired
memories stop appearing in retrieval immediately.

The RoboCasa GT CLI can use the same daemon with `--memory-service-url
http://127.0.0.1:8768`. Set `ATTENTION_MEMORY_API_KEY` in its environment and
point the daemon at the **same** `--store-path`; the runner verifies the
database's opaque identity before actions begin. Without the option, the
runner uses the independently installed package in-process.

### Per-memory artifact package

The Service materializes each v2 memory beneath the run `artifact_root`.
SQLite remains the authoritative state and index; the directory is a
hash-checked, rebuildable view, not a second writable memory database:

```text
<artifact_root>/
├── attention_memory.sqlite3
├── <task>-seed<seed>-<time>/       # existing per-run trace/result/bundle
└── memory/<safe-memory-id>/
    ├── MEMORY.json                # identity, status, applicability, file hashes
    ├── knowledge/
    │   ├── guidance.md
    │   └── repair.md
    ├── source/provenance.json      # source IDs, cost, and episode URI; no raw debug
    ├── validation/
    │   ├── plan.json              # immutable dev-seed paired-trial plan
    │   ├── pairs.jsonl            # control/treatment evidence references
    │   ├── impact.json            # current paired outcome and attention costs
    │   └── scope.json             # validated variation tuples after counterexamples
    ├── lifecycle.jsonl            # recorded memory transitions
    └── usage.jsonl                # explicit trusted-memory uses
```

The safe directory name is derived from a sanitized hint plus a full ID hash,
so arbitrary memory IDs cannot become paths. The Service writes `MEMORY.json`
last and verifies both file hashes and current SQLite-derived content. A
missing, modified, or stale package can be rebuilt with `export_package()` or
the authenticated `POST /memories/{id}/export` endpoint. Verification is
available through `verify_package()` or `GET /memories/{id}/artifact`.
The Service publishes after candidate creation, validation-plan registration,
pair registration, promotion, and explicit use. It does not duplicate source
RGB-D clips or evaluator-only data; those remain in their original run
artifacts, with URI/ID references in provenance.
Provenance also records the actual received guidance and a hash of the
Advisor/human response; the Service rechecks request/response lineage and
content before promotion or trusted retrieval. The default GLM distiller
keeps the proposed repair separate from the hint by recording its diagnosis,
proposed change, and caution.
An answered hint is only a proposed candidate, not a claim that the repair
worked. The paired variation gate establishes effectiveness before promotion.
If a deadline or evaluator outage prevents a native verdict, the raw trace
marks that verdict non-authoritative: Advisor guidance may still be retained,
but no candidate is minted from that run. A trusted memory used in such a run
records `timeout`, `cancelled`, or `evaluator_unavailable` as its use outcome;
the run cannot count as a completed validation pair.
Trusted retrieval requires the exact validated scene/object/camera/task tuple,
the concrete camera names and task prompt, and intact original source files.
Each trusted policy retrieval event carries the memory version and an
attempt-bound grant issued by the Service while the memory is still trusted.
The Service checks that grant, retrieval time, version, and native outcome
before accepting a use record, including the granted suite/task/perception
and variation context against the raw attempt.
The SDK rechecks trusted status and applicability when guidance is actually
requested, so an operator disable or expiry after catalog construction does
not release stale guidance.
If guidance was retrieved before revocation, its version and eventual outcome
remain in `usage.jsonl`; the use timestamp is the retrieval event time.
