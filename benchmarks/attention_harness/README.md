# TidyBot AttentionHarness: primary-suite services and Attention core

This package owns the service client adapter, runner, seed policy, artifacts,
timeout, and success-result envelope. It never imports the simulator or
MuJoCo directly. The peer `robosuite_sim` process owns the official Robosuite
environment and native evaluator; another benchmark harness is not a runtime
dependency.

The shared `TidyBotSDK` is the code-execution boundary. It consumes the
backend-neutral, client-side `RobotBackend` protocol rather than a concrete
simulator class, and exposes `sensors`, `arm`, and `gripper`. Its position
controller sends actions to the service-owned local OSC controller, so basic
motion does not require PyRoKi.

The intended service topology is:

```text
TidyBot robot_sdk (`tidybot_sdk` shared core)
  -> RobotBackend client contract
     -> RoboCasa service adapter (`robocasa_native`, existing ManiSkill service)
     -> Robosuite robot backend (implemented here)
     -> real-robot service adapter (existing platform path; not changed here)
```

The Robosuite observation includes RGB, metric depth, camera intrinsics,
camera-to-world pose, and robot proprioception. `sensors.pixel_to_world()` is a
thin SDK helper over those public arrays. `find_objects()` remains absent: the
Robosuite benchmark must not turn simulator object poses or segmentation IDs
into an oracle policy input.

The shared SDK supports `find_objects()` as an optional backend capability.
Existing RoboCasa deployments can opt in through
`PerceptionModuleRobotBackend`; `RobosuiteRobotBackend` does not implement that
capability until a non-oracle RGB-D perception service exists.

## v2 formal-runner engineering chain

`formal_attention_cli` is the seven-policy entrypoint for the two dedicated
`FormalSuiteRunner` implementations. It requires the exact post-Dev policy
SHA-256, simulator config SHA-256, and the suite's Service source paths. The
Skill DAG bridge selects this CLI only for `runner_boundary=formal` with
`approved_generated_policy=true`; after Dev writes a new file, the node waits
for approval of its new digest. New Dev generation also requires
`allow_generated_sandbox=true`. Re-run the node after updating its approval
fields. A changed source or config, missing formal runner, bad evidence digest,
or mismatched run/attempt identity fails the node without a development fallback.

Every attempt retains its suite-owned isolated worker, dedicated Service,
independent safety monitor, and native evaluator. The Harness adds one run and
attempt identity, approved hashes, decision and Advisor request records, a
projected trace in AttentionStore, Memory use grants when an approved
applicability context is supplied, and references to the four formal runner
artifacts. Eval reads and verifies the formal trace, safety, sandbox receipt,
and native result directly. UI can follow `attentionbench_last_run` to the
summary, store, attempt IDs, request IDs, and formal result directories.

This path is currently an engineering gate: every summary has
`formal_eligible=false`. The protocol freeze, independent acceptance matrix,
and held-out run have not been completed. Single seed smoke commands and
their results are indexed in `STATUS.md`.

For this path specifically, `RobosuiteRobotBackend` translates SDK actions and
benchmark lifecycle calls into `RobosuiteSimClient` HTTP requests. The client
then talks to the independent `robosuite_sim` server process; adapter/client
code and service/server code are separate layers.

The cross-repository rollout status and remaining agent_server work are tracked
in `docs/shared_sdk_migration.md`.

Frozen tasks:

- `cube_lift` -> Robosuite `Lift`
- `cube_stack` -> Robosuite `Stack`

Setup and smoke test:

The setup script expects the independent `attention_memory_service` checkout
next to this Universe checkout. Set `TIDYBOT_MEMORY_SERVICE_SOURCE` if it lives
elsewhere; setup stops if that checkout lacks the Dev-use evidence API.

```bash
./benchmarks/attention_harness/setup_env.sh
~/.cache/tidybot-attention/venv/bin/python -m pytest benchmarks/attention_harness/tests
MUJOCO_GL=egl TIDYBOT_ROBOSUITE_INTEGRATION=1 \
  ~/.cache/tidybot-attention/venv/bin/python -m pytest \
  benchmarks/attention_harness/tests/test_native_integration.py
```

Run one episode through the independently installed, commit-pinned service
(recommended for new work):

```bash
MUJOCO_GL=egl ~/.cache/tidybot-attention/venv/bin/python -m \
  benchmarks.attention_harness.external_robosuite_runner \
  --task cube_lift --seed 101 --policy reference
```

The original `benchmarks.attention_harness.runner` remains available for
frozen v1 replay. Its managed-process source snapshot cannot be changed
without invalidating the v1 freeze manifest.

Use `--policy frozen-public` to run the D7 task-solving policy. It executes in
the same credential-free sandbox as generated programs and can see only public
RGB-D, camera calibration, and proprioception.

The runner starts and stops a local `robosuite_sim` process by default. To use
an already-running service instead:

```bash
python -I -m robosuite_sim --port 8082
python -m benchmarks.attention_harness.external_robosuite_runner \
  --service-url http://127.0.0.1:8082 \
  --task cube_lift --seed 101 --policy reference
```

The reference policy runs inside `robosuite_sim`, where it may read privileged
object positions solely to prove the task/controller/evaluator chain is
solvable. Those positions never cross the service boundary. Model policies
receive only camera calibration / RGB-D and robot proprioception.

## PARCC development smoke (D4/D5)

The model path is deliberately restricted to development seeds `101-125`.
`parcc/GLM` writes one SDK program, invalid programs may receive at most two
validation-feedback retries, and the accepted program runs in a credential-free
subprocess behind an AST capability gate. `parcc/Qwen` reviews the public before
and after camera images. Its review is diagnostic; only Robosuite native success
is authoritative.

Use the protected key wrapper so the credential is never placed on a command
line or in an artifact:

```bash
MUJOCO_GL=egl ~/bin/with-litellm.sh \
  ~/.cache/tidybot-attention/venv/bin/python -m \
  benchmarks.attention_harness.runner \
  --task cube_lift --seed 101 --policy parcc
```

Each model episode additionally records `developer_request.json`,
`developer_response.json`, `generated_policy.py`, `sandbox_worker.json`,
`execution.json`, and `evaluator.json`. A Qwen timeout is recorded but cannot
change or erase the native evaluator result.

## D6 evaluator parity

The legacy ASPIRE wrapper and TidyBot-native service intentionally have
different reset, controller, settle, and Stack sampling behavior. D6 therefore
freezes evaluator parity only; it does not claim observation or trajectory
parity. The exporter compares the normalized native predicate source and 30
behavioral decisions over development seeds 101--105.

```bash
ASPIRE_SIM_ROOT=/path/to/ASPIRE/aspire/sim \
  ./benchmarks/attention_harness/run_d6_parity.sh
```

The strict gate rejects missing or duplicate `(task_id, seed, probe_id)` rows,
non-Boolean decisions, version differences, predicate-source differences, or
agreement below 100%.

## D7 protocol freeze

Regenerate the public-policy development evidence before freezing:

```bash
MUJOCO_GL=egl ~/.cache/tidybot-attention/venv/bin/python -m \
  benchmarks.attention_harness.validate_frozen_policies
```

The strict gate covers all development seeds 101--125 and requires 25/25
native successes for both `cube_lift` and `cube_stack`. The report binds each
result to the exact policy SHA-256. It does not run held-out seeds.

Create and verify the deterministic manifest:

```bash
python -m benchmarks.attention_harness.freeze create
python -m benchmarks.attention_harness.freeze verify --require-heldout-ready
```

The D7 manifest also freezes the camera convention, non-oracle task policies,
AdvisorProxy prompt/cache identity, and both assistance modes:

- `benchmark_proxy`: fixed cached `parcc/GLM`; humans cannot answer; eligible
  for reproducible benchmark ranking.
- `live_human_first`: waits up to 60 seconds for a human, then falls back to the
  same proxy; validation/case-study only.

`heldout_ready: true` means only that this frozen Robosuite task-policy gate may
be run with explicit opt-in. No D7 command runs held-out seeds automatically,
and the full seven-policy Attention experiment remains a later-stage gate.

## RoboCasa frozen infrastructure tasks

The RoboCasa adapter is a peer of the Robosuite adapter. It talks over HTTP to
the existing ManiSkill-based RoboCasa service and returns only the native
success Boolean. Its public client intentionally has no teleport method and
strips evaluator debug. A separate privileged probe exists only to validate
task/reset/evaluator infrastructure before model policies are run.

Frozen infrastructure tasks:

- `counter_to_cab` -> `RoboCasa-Pn-P-Counter-To-Cab-v0`
- `counter_to_sink` -> `RoboCasa-Pn-P-Counter-To-Sink-v0`

The committed development evidence uses seeds 101--105 and requires, per task,
reference 5/5, no-op 0/5, and reset recovery 5/5. This does **not** claim that a
non-oracle RoboCasa task policy is complete or held-out eligible.

## Persistent Attention core

`benchmarks.attention_harness.core` provides one backend-neutral path for both
primary suites:

```text
suite adapter -> run/attempt -> trace packet -> policy decision
             -> attention request -> human or fixed AdvisorProxy
             -> candidate memory -> validation -> trusted retrieval
             -> native result + deterministic run bundle
```

Run, attempt, trace, request, response, cache, assistance reservation, memory,
memory-use, and event data share one SQLite store. Writes and state transitions
are idempotent; a process restart reconstructs the same attention inbox and UI
state projection. Benchmark-proxy cache hits keep the same logical latency and
cost as uncached responses. Live-human-first requests fall back to that same
proxy after their fixed deadline.

The seven policy implementations share `PolicyContext` / `PolicyDecision` and
cannot call a model or mutate budget directly. The scripted validator checks
their routing contracts; it is system validation, not benchmark data:

```bash
python -m benchmarks.attention_harness.validate_attention_core
pytest benchmarks/attention_harness/tests/test_attention_e2e.py
```

The UI backend projection exposes the required run context, resource budget,
autonomous work, attention inbox, request detail, and live station from real
persisted events. A production browser frontend is still a separate remaining
deliverable.

### Trace evidence boundary

The Attention core stores two linked trace records rather than exposing the
execution log directly:

```text
SDK / sandbox / recorder / evaluator events
                  -> RawExecutionTrace (internal, complete)
                  -> VisibilityProjector (deterministic redaction)
                  -> AdvisorTracePacket (human/advisor-visible evidence only)
                  -> TraceAssessment (deterministic failure/progress/provenance)
                  -> trace-aware Attention decision
```

`RawExecutionTrace` may contain native-evaluator results and simulator-only
diagnostics. Every event and evidence reference defaults to `internal`; it must
be explicitly labelled `advisor` or `public` before it is eligible for the
projection. The projector then removes oracle, reward/success, evaluator, and
credential fields recursively. It also rebuilds the failure summary from the
visible event stream instead of copying an internal diagnosis. The resulting
packet links back through `raw_trace_id`, `run_id`, `attempt_id`, and
`execution_id`, plus deterministic source/projection hashes.

The two trace-aware policies consume the same deterministic assessment of the
projected packet and earlier projected attempts. It classifies visible SDK or
execution errors, compares failure signatures and public code hashes, records
whether more SDK operations completed before failure, and cites event/evidence
IDs. An agent hypothesis remains explicitly unverified. Generic task outcome
failure alone does not establish a robot cause. The hint-only policy can
inspect, retry, request a text hint, or stop; the full policy can also retrieve
applicable trusted Memory and request approval. Independent safety signals
stop a run before any Advisor request. The rule version and assessment are
saved with each decision. This first version uses no extra model, tokens, or
latency; a future video/complex-trace model must fix its model and prompt and
charge its tokens and latency to the same experiment budget.
The hint-only arm has no Memory catalog or retrieval access during replay.

Use `TracePipeline.persist()` for new Attention runs. It writes the immutable
raw ledger first and then its immutable advisor packet to the same SQLite event
store. `AttentionRuntime` loads that persisted packet by default, so callers do
not need to reconstruct or hand-filter Advisor input.

The shared `TidyBotSDK` accepts an optional event sink and emits the same
semantic operations for every backend (`sensor_read`, `perception`,
`frame_transform`, `arm_command`, and `gripper_command`). Sandbox workers save
these events atomically after every SDK call, allowing a timeout to retain the
latest complete partial trace.

The native Robosuite and PARCC runners now finalize this path automatically.
Every episode writes `attention.sqlite3` and `attention_bundle.json`; every
episode has a raw trace, while only an assistance-eligible failed attempt gets
an AdvisorTracePacket. The result artifact links the records through its
`attention_trace` field. The RoboCasa package exposes
`persist_robocasa_episode_trace()` for its production runner / agent_server.
The current privileged RoboCasa infrastructure validator intentionally does not
use this entry point because teleport-based probes are not agent executions.

### PARCC GLM Advisor development loop

The development runner can execute a PARCC-generated policy, apply a fixed
Attention policy to its Advisor-safe failure trace, ask GLM for structured
advice, and pass the guidance into the next code-generation attempt. A single
SQLite run contains both attempts, the request/response, token usage, logical
latency, and assistance credit consumption. The default policy is
`reactive_help`; `retry_k_then_ask` is also supported.

```bash
MUJOCO_GL=egl python -m benchmarks.attention_harness.advisor_run \
  --task cube_lift --seed 101 --max-attempts 2 --skip-review
```

This command uses `PARCC_API_KEY` or `LITELLM_KEY` in the harness process.
Its summary is `advisor_run.json` under the output run directory. The Advisor
must return `attentionbench.advisor-advice.v1` JSON; invalid responses stop the
run without injecting advice into a retry. Only development seeds are accepted.

### Formal Attention run operator console (development only)

The single-page UI can start the two approved simulator profiles in
`protocol/v2/dev_ui_run_catalog_2026-09-27.json`. Start it with an existing
AttentionStore and an artifact directory:

```bash
python -m benchmarks.attention_harness.ui_server \
  --store /path/to/attention.sqlite3 \
  --artifact-root /path/to/attention-artifacts \
  --run-catalog benchmarks/attention_harness/protocol/v2/dev_ui_run_catalog_2026-09-27.json
```

The operator selects an approved task/seed profile, Attention policy,
simulator target, assistance mode, attempt count, assistance credits, token
budget, human deadline and per-attempt execution deadline. The backend
checks the policy/config hashes, rejects held-out seeds and target mismatches,
persists the locked launch selection, then starts the formal runner in a
detached process. A launch worker records the runner's terminal exit code,
linked run ID, and native result in the same store; `/api/runs` restores that
receipt after a UI restart. Each Advisor request launched through the UI is
limited to one PARCC provider call. The current catalog is local to this checkout; replace its
absolute paths when moving machines. It contains no real-robot profile.

During a live-human wait, the only pre-authorized background job indexes at
most eight Advisor-visible evidence references. It has no robot actions and
its own read-only validation. The human request has a bounded deadline;
deferral is a persisted event that does not extend it. The UI exposes request,
deferral, timeout, proxy fallback, cancellation, response and actual response
use from the SQLite store after restart. A cancelled human request stops the
current run. All these runs keep `formal_eligible=false`.

### Cross-seed automatic checks (M-10)

The offline worker still starts with same-scene checks. Cross-seed checks require
both `enabled: true` and `cross_seed.enabled: true` in the state directory's
`autovalidate.json`; `scripts/memory-autovalidate.example.json` leaves both off
and the candidate list empty. The web page cannot enable either switch.

`cross_seed.candidate_seeds` is an ordered list of development seeds.
`cross_seed.contracts` maps each seed string to its own absolute contract file.
The runtime's `contract_registration_roots` must cover those files, and the
service's M-8 contract registration roots must also allow them. The importer
checks the seed entry and registers the exact file digest before any run.
Missing/refused contracts are skipped with a reason; the global contract is
never substituted for an unregistered target. This configuration does not
register the screening candidates or change the frozen matrix.

One direct origin-code probe uses no Dev call or notes. The service decides
whether to create five transfer pairs or three neutral pairs; the whole batch
must fit the remaining daily budget. These checks share M-8's idle gate,
Pause/Resume, 20-run cap, quota stops and independent validator. Only seed,
its reset/random-seed aliases and its contract change in the frozen origin
config; all other configuration bytes are preserved. Probe manifests retain
one unused Dev allocation from the existing one-cell budget; probes never
reserve a model job, and both the validator and service reject model/advice jobs.

Offline verification with the new service checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD:/path/to/wt-crossseed-svc" \
  /path/to/service_env/bin/python -B scripts/cross_seed_fake_e2e.py
node scripts/cross_seed_ui_preview.cjs
```

The second command writes static light/dark HTML with fake data. Neither
command contacts live services, models or simulators.

### Dev agent policy v2 (P3)

New `dev_mode=agent` experiments created by `dev_experiment.create()` default to
`agent-policy-v2`. One-call keeps its seven policies; `options().agent_policies`
contains the eight agent policies, complete default parameters and version.
An absent version on an existing run means v1. Retrying its request key preserves
its original manifest. For offline legacy reproduction, pass the explicit Python
keyword `agent_policy_version='agent-policy-v1'` to `create()` (or the same field
in an explicit agent selection). The UI does not expose the v1 entry.

Each v2 manifest seals the version at the top level, selection, execution config
and cell, plus complete policy parameters, per-policy parameter SHA-256 and the
source SHA-256 of `dev_agent_policies.py` and `dev_agent_attention_v2.py`.
`verify_manifest()` and the agent cell entry check these seals; policy changes
require a new run. `validate_fairness([manifest_a, manifest_b, ...])` additionally
checks cross-run versions, model/scene/execution conditions, budgets, initial
source hashes and consistency of parameters for a repeated policy id. A check
of one manifest alone does not establish cross-run comparability.

For a planned comparison, explicitly set **`help_budget=3` for every policy**.
Individual runs continue to default to 1. There is no agent comparison preset
in this checkout; no new preset is introduced. This recommendation is separate
from formal/legacy benchmark presets and does not change those configurations.

Operator views retain practice-run help and multiple help requests per interval,
identified by `help:eNNNNNN`. Delivery and persisted charges are separate: a
provider failure can consume help and human attention. Pending obligations,
`ask_required`, fallback blocks and controller errors appear in Help & Replies.
Agent v2 reports use the existing agent JSON report endpoint; `dev_report` rejects
the one-call ledger interpretation of these runs.

### Dev agent on lab PARCC

Model source `Lab models · PARCC` runs all three agent roles on the lab key, with no
Codex quota. The selection keeps `model_source=codex` (the frozen agent schema) and
names each role in `agent_provider` with `credential=lab, provider=parcc`; public views
label such a run `parcc`. Dev and Eval are `parcc/GLM`; the Advisor defaults to `parcc/GLM`
and may use `parcc/Qwen`, which reads images. GLM reads text only: its Eval bridge replaces
images with a notice, its Advisor uses the text prompt, and the start preflight skips the
image check for these lab roles only. Results are not comparable with Codex runs. The Codex
CLI still drives each role through the loopback bridge, so it must be installed on the host.

PARCC applies `response_format` to every turn, which would leave a tool-using role no tool
calls, so the bridge asks for the schema in the instructions instead. GLM follows that; Qwen
as Eval rarely does, so Eval stays on GLM. Qwen also rejects `reasoning_effort=high`, which
`reasoning_effort()` drops for the Advisor's calls.

### New run offers Agent runs only

The New run dialog creates Agent runs. One call, fixed-policy runs and "continue from an
attempt" have no entry there any more; their records still open, and Run again appears only
on Agent runs. The HTTP routes behind them are unchanged: this removed the entries, not the
backend, so a client that posts directly can still reach them.

Remote shows this same form plus section 07, Sharing & queue. Four things differ: no Codex
source, the shared host's limits (`Lobby.newRunLimits`), a goal ID set by the host, and a
join that replays a recording, because real runs are not connected yet. Start run stays off
and the settings are not used. A key saved here goes to the same server memory as the main
UI, with no separation between people, so do not open this page to others before the gateway
in the public design exists.

For browser review only, manually run `scripts/preview_agentpolicy.sh`. It creates
three synthetic fixtures under `/tmp/agentpolicy-preview/` and starts this
worktree's `ui_server` at `127.0.0.1:8779`, with every POST refused. Printed links
open the trace-v2, agent-initiated-v2 and legacy-v1 pages. Check the creation
policy dropdown in both modes, multiple practice help entries and their fees,
pending/required/fallback states, visible controller errors and refresh behavior.
The trace fixture deliberately combines display-state samples for forced asks;
real forced asks belong to reactive/retry policies. The script has no model,
Memory Service, simulator or live-camera connection. It is not a runtime canary.
