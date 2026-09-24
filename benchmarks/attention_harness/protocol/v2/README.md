# AttentionBench v2: perception tracks

This is a new experiment definition, not an amendment to the frozen v1
Robosuite non-oracle result. The v1 freeze manifest and its evidence remain
unchanged.

`sim_gt` means that a simulator may supply object identity and position,
including through RoboCasa `/perceive`. It is the **GT-perception
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

The RoboCasa GT track is not frozen or held-out eligible yet. First complete
the two-task, five-development-seed chain check; then meet 25/25 native
successes per task on the frozen development split. Privileged teleport
probes do not count as policy successes.

## Memory v2

`memory.json` defines the additive v2 memory contract. The implementation is
the separately versioned [attention_memory_service](https://github.com/TidyBot-Services/attention_memory_service)
package; the old Universe module paths are compatibility imports. RoboCasa GT runs use
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
are fingerprinted, so a changed policy cannot masquerade as a memory benefit.
Source human-attention cost, subsequent help
credits, robot time, and safety are reported separately. The RoboCasa paired
executor wraps the backend in an independent command/proprioception monitor.
It writes one safety artifact per attempt, including sampled state, proposed
actions, limit violations, and explicit coverage. It can reject an excessive
command before dispatch. The backend has no independent collision/contact
telemetry, so this is **not** a full physical-safety certification.
The frozen validation plan names every scene, object set, camera configuration,
and task variant. Both arms must attest the same applied variation and
execution-configuration digest in their raw traces; all planned cases must
finish before promotion. The impact artifact lists supporting successes,
counterexamples, empirical confidence, and the exact variation tuples still
eligible for trusted retrieval. Any counterexample excludes its tuple.

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
python -m benchmarks.attention_harness.memory_agent_cli plan MEMORY_ID \
  --cases /path/to/frozen-validation-cases.json
```

The daemon binds to `127.0.0.1:8768` by default. Remote client URLs require
HTTPS; do not expose the daemon on a public interface without TLS and normal
deployment controls. HTTP validation pairs upload safety-monitor JSON into the
Service's persistent evidence directory; they do not depend on shared local
paths. `RoboCasaPairedTrialExecutor` now drives the GT runner through the same
task, seed, policy code, budget, and simulator configuration for both arms.
Only candidate exposure differs. Each episode keeps `trial_config.json`,
`result.json`, its raw trace/bundle, and `safety_monitor.json`. The Memory
Service independently checks the stored attempts, native outcomes, retrieval
events, and safety-file hashes before promotion. Registered seed pairs survive
restart and are skipped on a resumed validation run. This is not yet wired into
the legacy skill-DAG orchestrator's automatic agent-spawn path.

For a candidate already produced by a failed GT run, start the RoboCasa and
simulator-only agent services, then run five development-seed pairs:

```bash
python -m benchmarks.attention_harness.memory_agent_cli validate MEMORY_ID \
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
