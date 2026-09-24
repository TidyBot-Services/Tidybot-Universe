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
credits, robot time, and safety are reported separately. The current runner
does **not** produce independent safety-monitor files, so its live runs cannot
yet pass this gate; unit tests use controlled synthetic fixtures. This is an
intentional fail-closed boundary, not a claim of formal benchmark readiness.

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
python -m benchmarks.attention_harness.memory_agent_cli plan MEMORY_ID
```

The daemon binds to `127.0.0.1:8768` by default. Remote client URLs require
HTTPS; do not expose the daemon on a public interface without TLS and normal
deployment controls. HTTP validation pairs upload safety-monitor JSON into the
Service's persistent evidence directory; they do not depend on shared local
paths. `MemoryAgent.run_validation()` accepts an injected trial
executor, but no live executor is registered yet because the current RoboCasa
GT chain does not supply independent safety-monitor artifacts. The agent can
plan and request validation; the Service will still reject promotion until
real paired evidence exists. Registered seed pairs survive restart and are
skipped on a resumed validation run. This is not yet wired into the legacy skill-DAG
orchestrator's automatic agent-spawn path.

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
    │   └── impact.json            # current paired outcome and attention costs
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
