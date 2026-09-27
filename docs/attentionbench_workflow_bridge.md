# AttentionBench Skill-DAG integration (development only)

This opt-in entrypoint connects one existing Skill-DAG Dev node to the shared
seven-policy RoboCasa/Robosuite v2 runner. Ordinary graph nodes keep the legacy
Agent Server execution/evaluation path. The new path does not make a generated
policy sandboxed or formal-experiment eligible.

## Entry point

Run `skill-agent-setup/claude-code/attention_app.py --graph PATH` with the
AttentionBench Python environment. The existing UI can connect to its normal
Orchestrator HTTP/WS ports. `agent_orchestrator.py` remains the legacy entrypoint.

An opted-in graph node needs an explicit `attentionbench` object:

```json
{
  "entries": [{
    "name": "lift",
    "description": "Develop cube-lift policy",
    "dependencies": [],
    "service_dependencies": [{
      "capability": "yolo-detection",
      "runtime_name": "yolo",
      "reason": "Need object detection for this policy"
    }],
    "attentionbench": {
      "suite": "robosuite",
      "task": "cube_lift",
      "seed": 101,
      "attention_policy": "trace_aware_full",
      "runner_boundary": "trusted_dev",
      "robot_policy": "lab_policy:run",
      "approved_trusted_policy": true,
      "approved_policy_sha256": "<sha256 of reviewed lab_policy.py>",
      "artifact_root": "artifacts/attentionbench-v2-attention",
      "store_path": "artifacts/attentionbench-v2-attention/attention_memory.sqlite3"
    },
    "dev_memory": {
      "enabled": true,
      "memory_id": "<trusted-memory-id>",
      "development_id": "<graph-folder>:lift:<unique-session>",
      "context": {
        "suite": "robosuite", "task_id": "cube_lift", "perception_mode": "sim_gt",
        "scene_id": "<id>", "object_set_id": "<id>",
        "camera_config_id": "<id>", "task_variant_id": "<id>",
        "camera_names": ["agentview"], "task_prompt": "<prompt>"
      }
    }
  }],
  "service_catalog": "catalog.json",
  "service_wishlist": "wishlist.json",
  "memory_service_url": "http://127.0.0.1:8768",
  "deploy_agent_url": "http://127.0.0.1:9000"
}
```

For this uncommitted two-repository development state,
`./benchmarks/attention_harness/setup_env.sh` installs the sibling
`attention_memory_service` checkout in editable mode by default. Set
`TIDYBOT_MEMORY_SERVICE_SOURCE` if it lives elsewhere. Setup fails when the
checkout is missing or lacks the Dev-use evidence API. This local install is
not yet a reproducible collaborator release: the Memory Service changes must
be committed and the dependency pinned to that revision before handoff.

`approved_trusted_policy` must be an operator decision. The bridge rejects
held-out seeds, unknown suites/policies, arbitrary script paths, and RoboCasa
jobs without `confirm_simulator_agent: true`. It uses shell-free subprocess
arguments and requires a persisted `attention_run.json` that matches the
returned summary. A CLI exit code of 1 means native task failure and is kept
as evidence, not an infrastructure crash. The result link and store path are
added to the graph entry, so the UI's existing graph snapshot can find the
v2 run. The v2 store remains the source for attempts, requests and memory use.
The UI offers a graph-node → run link only when it is connected to the same
AttentionStore; otherwise it reports that the run is absent from its store.

After Dev finishes editing, a human reviews the exact policy source and writes
its SHA-256 into `approved_policy_sha256` (for example, run
`sha256sum lab_policy.py`). Any subsequent edit invalidates approval and
sends the node to review before trusted execution.

After the run, an Eval Agent reads the persisted bundle and writes diagnostic
feedback. Its text cannot override `native_success`; the native evaluator
controls success. Failed runs return to graph review without silently spending
another seed. This is a **trusted callback development workflow** with
`formal_eligible=false`; do not use it for unreviewed model-generated code or
claim it is a formal-policy sandbox.

## Service discovery boundary

The independent [services_wishlist](https://github.com/TidyBot-Services/services_wishlist)
catalog at revision `f612b1e` uses `{"capabilities": {"capability-id": {...}}}`;
its Wishlist uses `{"items": [...]}`. The independent
[deploy-agent](https://github.com/TidyBot-Services/deploy-agent) at revision
`6bc4af9` returns a running-instance list from `GET /services`. Catalog
`host` is **not** a live endpoint. When a capability ID differs from the
instance name, the graph must declare `runtime_name` explicitly.
`service_evidence.py` records catalog/Wishlist/inventory digests and
resolution on the graph node. Missing capabilities yield a reviewable Wishlist
item proposal; this bridge never writes the independent repo automatically.
`prepare_deploy_plan` requires an operator, approved manifest digest, and
immutable image digest before constructing (but not submitting) a
`POST /deploy` body. No online Deploy Agent was contacted for acceptance.

## Remaining target-architecture gaps

- Dev Memory guidance is opt-in for the `trace_aware_full` treatment arm only.
  It requires the same Attention store, trusted exact-scope retrieval and a
  Service-issued `devgrant` before exposure. The grant records Memory version,
  source trace, raw evidence refs and pre-edit policy SHA-256. The subsequent
  native-run artifact is hashed and linked with the post-edit source SHA-256.
  This is **development exposure**, not an attempt-bound use or causal benefit.
  Existing sessions do not re-expose guidance on resume; start a newly
  approved development ID if a new session needs it.
- `formal_runner_boundary.py` defines a shared suite-runner interface and
  verifies policy/config identity and persisted sandbox, cancellation, safety
  and native-evaluator artifacts. No certified suite runner is registered;
  `attentionbench_bridge.py` rejects `runner_boundary=formal`. RoboCasa's
  generated-policy subprocess smoke is not an OS-level hostile-code jail;
  Robosuite still needs a sandbox/overall-deadline/cancellation adapter.
  Formal eligibility remains false until real-service acceptance is frozen.
- Catalog/Wishlist/Deploy **schemas** were checked against independent public
  repository revisions, but no live Deploy Agent or end-to-end Service-Agent
  request/deployment workflow was tested. Internet discovery remains separate.
- The old UI displays graph state and v2 run state, but no claim is made that
  this opt-in bridge has passed a live end-to-end model/simulator test.

Small checks: run the Memory Service `pytest tests -q`, Universe
`pytest benchmarks/attention_harness/tests
skill-agent-setup/claude-code/tests/test_*bridge.py
skill-agent-setup/claude-code/tests/test_service_evidence.py -q`,
and the existing `test_orchestrator_pipeline.py` script. These do not run
a robot or produce AttentionBench performance data.
