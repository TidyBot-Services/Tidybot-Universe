# M-8 automatic validation handoff

The worker is **Off by default**. No unit has been installed and no live service,
model, simulator, or GPU job was started by this change. The implementation and
acceptance fixtures belong to `wt-autoval`; pinned source links also require the
corresponding service change in `wt-autoval-svc`.

The deterministic worker, validator, Dev request/receipt producer, ledger runtime
adapter, registration client, Memory page, Notices, trusted use outcomes, and
family defaults are implemented. The in-process fake lifecycle completes all ten
Dev-generated arms and promotes/demotes through the actual service. This is a
synthetic engineering test, not evidence of a real useful note or a model run.

The two initial service gaps are now wired and their old strict xfails have been
replaced with passing tests. Original external SDK/model files use `source`
catalogs. A failed independent check is submitted to `/dev-v2/failed-checks` with
the original granted execution and sealed independent descriptor. Treatment safety
failure rejects the candidate and produces a Notice; scene failure or control
safety failure closes the batch as inconclusive. Its note stays Unvalidated, and
a fresh use/outcome is required before the service queues it again. The worker
includes the use count in its durable operation ID so a new check gets a new
candidate/plan rather than reopening the closed batch. Failed arms never become
accepted trials or pairs. The Memory page and Notices explain inconclusive results
as “Could not finish the check: …”, without inventing five-pair counts.

The final source/contract layout gap is also closed. The runtime builds source
catalogs from the origin and all arm identity locks before choosing a contract
catalog. When the registered contract lies inside a source root, its exact file
and digest are added to that sealed list and the service reads it through the
source role. No separate overlapping contract catalog is registered. If no source
catalog covers the exact contract reference, the separate contract catalog remains
the fallback. Historical locks, file paths and bytes are unchanged. The previous
strict xfail is now a passing nested-layout registration/import test, alongside
refusal tests for an unsealed contract, wrong digest and post-registration tampering.
There are no remaining known service gaps or M-8 xfails.

## Worker and runtime boundary

`memory_validation_worker.tick(state, deps)` accepts a client, runner, clock,
quota reader, activity probe, state directory and atomic saver. Its pending head,
arm dispatch intent, daily slot reservations, pair cursor, outcomes and gaps are
persisted before external effects. Runner operations are idempotent by immutable
cell/import IDs. Unknown claimed jobs are never redispatched. The CLI holds a
single-worker file lock. Daily slots are conservatively reserved before generation;
a crash before execution may consume a slot, but cannot create an extra run.

### Interrupted origin recovery without a receipt

After an operator has reviewed an interrupted origin replay, exit the worker so
it releases `worker.lock`, then run:

```sh
python -m benchmarks.attention_harness.memory_validation_worker \
  --state-dir /path/to/validation-state \
  --abandon-interrupted-origin MEMORY_ID \
  --reason 'Reviewed the interrupted replay; retain partial evidence and retry with fresh evidence'
```

This command requires current work to be the matching origin item, a matching
`dispatch.json`, no `receipt.json`, normal queue eligibility, and no independent
validator result or service candidate. It fails closed when the worker lock is
held. Turning automatic checks Off alone does not release that lock.

Before any rename, it atomically journals `origin_interrupted_abandoned` in
`state.json` under `origin_interruptions`, including actor, UID, time, reason,
export key, origin archive destination, partial export path and validator replay
paths. Old dispatches lack a validator directory pointer: recovery discovers
native replay ownership or matching sealed policy bytes conservatively and
records empty, unattributed arms separately. These paths remain local; the Memory
page displays the recovery event and any incomplete recovery error.

Only the origin directory is renamed into `origins-archive/<key>-<stamp>`.
Partial exports and validator replay directories remain intact and are never
reused. Current work and matching pending work are cleared, related errors are
cleared, and the note remains queued. The previous reservation stays counted;
the next attempt reserves another run, gets a UUID export key and allocates a
fresh validator `arm-` directory.

If recovery crashes, rerun the same command with a nonempty reason. It resumes
the original journal intent and preserves the original reason, whether the
rename happened or not. The worker refuses to resume an incomplete abandonment.
Origins with a receipt must use their existing recovery rules instead.

`memory_validation_runtime.build_runtime` (also available as `from_config`)
uses `memory_validation_provision.provision_factory` by default. The adapter reads
queue provenance, verifies the original ledger through SQLite `mode=ro`, checks
its settled execution against the sealed export, and binds the source, public
feedback, failure signature, help input/source, config and import descriptors.
It never rewrites an original run. Missing or altered evidence produces a plain
worker status and Notice; it does not trigger an inferred replacement.

The runtime's `evidence_catalogs` maps existing importer catalog IDs to sealed
`{uri, sha256}` references to their `catalog.json` files. Use the original output
of `dev_v2_memory_import.prepare` and the existing independent source validator
catalog, including the exact `validator_id` named by the source descriptor. The
original executor catalog must declare `ledger_origin` under `experiments_root`.
This inventory is deployment configuration, not a go per batch. The worker does
not invent a missing independent source verdict or create unverified catalogs.
The existing provisional auto-importer alone does not produce these independent
source prerequisites; notes lacking them visibly refuse until they are supplied.

Provisioning calls `dev_experiment.create` with a private sealed-origin argument.
One operation creates one tagged experiment and ten distinct cells (five pairs)
for the same task/seed/policy. The config bytes and original SDK epoch are retained;
Codex generation is pinned separately. Every cell has exactly one Dev call and
one execution, zero help/Advisor/Eval calls, no Memory authority, and run/family
notes disabled. Goal resource limits equal ten cell budgets. Native evaluation
and independent replay remain separate from the disabled model Eval agent.
There is no motion approval minted. The original simulator binding and native
config validation still apply. Ordinary daily UI sessions and reports exclude
these tagged experiments; the Memory page owns their outcomes.

A new batch requires at least ten daily slots remaining. Arm dispatch still
reserves each daily slot durably, and idle/quota checks remain mandatory. Repeated
operation IDs reuse the same experiment and goal, including after a worker retry.
The idle probe recognises its experiment before the first arm is dispatched.

`LedgerRunner` connects this experiment to Dev generation, validation grants,
executor import, independent replay and pause/resume. Export catalogs use the
configured experiments root with an exact sealed file list, so the preserved
original config/lock references remain importable at their original locations.
The independent validator receives a narrower seal rooted at the validation
experiment directory; its strict root and digest checks are unchanged.

Validation execution now records the existing harness `GpuMeter` reading in a
sealed `gpu-measurement.json`, bound to the cell/version/attempt and bundle digest.
The adapter reads that settled receipt. Missing samples, unavailable readings,
wrong bindings or changed bytes refuse; zero is accepted only when measured.
The measurement retains its explicit coverage: host GPU utilization during the
attempt, including other processes. It is not per-process GPU attribution.
Fake executors never start this meter; all acceptance evidence is synthetic.

The provision callback returns:

- `memory_id`, `origin` and `origin_feedback` artifact references, `config`
  reference, complete `context`, sealed `failure_signature`, `policy_id`, `budget`,
  and `dev_model: {provider: codex, model: gpt-6-astra}`;
- `source_catalogs` keyed by catalog ID for original executor/independent source
  evidence, plus `failed_execution` and `source_import` payloads including
  `catalog_id` and relative `descriptor`. The runtime reads each descriptor’s
  original manifest/identity lock and additionally builds and registers `source`
  catalogs for every external locked SDK/model/resource file;
- five `pairs` with distinct `control_cell` / `treatment_cell` IDs and a `cells`
  mapping from each ID to `{ledger: /absolute/original/ledger}`. Cells must have
  the exact frozen configuration, no run/family/ordinary Memory binding, and a
  valid pre-existing authorization. Provisioning must be idempotent by operation ID.

The Dev producer copies the frozen origin bytes into each arm's catalog, creates
one system and one JSON user message, and supplies exactly `memory: []` or the
full candidate guidance. It bypasses ordinary Memory compaction. The receipt is
read from the actual sealed request, then attached with `parent_source` when the
immutable version is created. Grants bind the generated SHA after generation.
The original runner consumption receipt and dispatch event remain required.

The validator rechecks sealed bytes before writing, verifies native outcome and
source/permission binding, independently reconstructs monitor events/violations,
and replays twice into its own root. V2 full states include their computed
validation receipts. Every output file is sealed. False checks remain evidence;
tampered/missing/wrong-root evidence is refused without writing. Real replay uses
a fresh process and the existing experiment reset path, with credentials removed
from its environment. It was wired but never invoked here.

## Configuration (not installed or enabled)

Set `ATTENTION_MEMORY_VALIDATION_STATE` on the UI server to the same worker state
directory. The read-only `/api/dev-v2/automatic-checks` route shows switch, queue,
current pair/arm/pause, “Runs today: N of 20”, known weekly Codex use, result
counts, demotions and errors in both page themes. Results use “Trusted on this
scene”, “Rejected”, and “Could not finish”.
The switch is controlled explicitly by the CLI; the page does not turn it on.

```sh
python -B -m benchmarks.attention_harness.memory_validation_worker \
  --state-dir /absolute/worker-state --set-enabled false
python -B -m benchmarks.attention_harness.memory_validation_worker \
  --state-dir /absolute/worker-state \
  --adapter benchmarks.attention_harness.memory_validation_runtime:from_config
```

The default factory reads deployment configuration and credentials only after
the switch is on. When `autovalidate.json` is absent, `runtime.json` may supply
the initial `enabled` boolean (default false); an explicit UI/settings switch
takes precedence. That file names `base_url`, `key_file` (path only), `experiments_root`,
`validator_root`, optional `provision_factory` (`module:factory`), `evidence_catalogs`, `activity_ledger_roots`,
`activity_ledgers`, and optionally `quota_home`. The provisioning factory receives
this configuration and returns an idempotent callable with `gpu_seconds(store,
cell)` for independent measurement. Include every daily/UI and main-matrix ledger
in the activity inventory; missing/unreadable inventories fail closed. Configure
the process's local timezone (for this workspace, `TZ=Asia/Shanghai`).

The service registration roots must allow disjoint `executor`, `validator`,
`contract`, and `source` roots. Source catalogs retain the original paths, exact
complete Python source sets and all locked file digests, with streaming digest
checks before registration and service rechecks on reads. They contain only
explicitly locked files plus the exact registered contract when covered by the
source root, never a recursive home-directory inventory. The producer
refuses implicit symlink traversal. Locked symlinks retain their original paths
and pin every one-hop target with source-only `links`; the terminal regular file
and each link name share the locked digest. The builder emits a source catalog
for every directory along a chain, seals only needed files, and orders target
catalogs before their referring catalogs. Self-directory links are supported;
cycles and chains longer than eight hops are refused. Provisioning and export
recheck every supplied pin before the service independently verifies it. When exporting original prerequisites, pass
`source_catalogs=manifest_sources(manifest_ref, context, executor_root,
contract=config["scene_contract"])` to
`dev_v2_memory_import.prepare`; the arm exporter does this automatically.
When needed, the separate contract catalog contains **only** the registered
repository contract at its exact digest. When a registered source catalog seals
that exact contract file instead, no separate contract catalog is registered. Importer credentials are loaded from `key_file`
only and never added to model packets. Off performs no adapter/credential setup.
The worker quota reader uses the maximum weekly (`window_minutes == 10080`)
`used_percent` over the latest rate-limit receipt of every session JSONL file
modified in the past 24 hours. Only the latest receipt in each file contributes;
old peaks and five-hour windows do not. A fresh empty/low session cannot conceal
another recent session’s high value. No recent valid weekly receipt means pause.
The first reading of each local day uses this same maximum for the durable
day-start baseline; restarting does not reset it. Limits are 20 reserved
arms per local day, weekly use at least 70%, or a rise greater than five points.

New daily Dev experiments default family references on; old manifests with no
field stay off. Non-Dev defaults remain off. Only provisional/trusted, active,
unexpired, non-test family notes can be read; the cap stays one, with label
`Reference (task T)` and results exclusion. Trusted ordinary use outcomes now
reach the observational importer even when a run also used excluded notes.
Validation arms never enter the ordinary-use queue.

## Deployment examples and test compatibility

`scripts/memory-validation-runtime.example.json` starts with `enabled: false`.
Copy it as `<state-dir>/runtime.json` only after replacing the placeholder paths
and catalog hashes. `scripts/memory-autovalidate.service.example` is an uninstalled
user-unit example; its pre-start command explicitly sets the switch Off on every
start. Nothing in this handoff installs, starts or enables either example.

The M-8 test files import only main-repository fixture helpers. Tests requiring
service auto-validation, scene contracts, source catalogs, arm generation or failed
checks guard those features with “needs the M-8 Memory service”. Independent tests
continue to run against the older service checkout. The first fake e2e scenario
uses the default provisioning adapter, real experiment/goal creation, a controlled
SDK registry, synthetic executor receipts/replay and the actual in-process service.
It proves wiring and bookkeeping, not native or GPU execution.

The historical interpreter alias
`/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python`
is pinned through `/home/truares/miniconda3/envs/maniskill/bin/python` to that
directory's regular `python3.11`. Historical locks are not changed.
`scripts/memory-registration-roots.example.json` includes both interpreter
directories as source roots. Pass an operator-adjusted copy to the Memory service's
`--dev-v2-registration-roots`; the runtime example remains disabled. Add the other
exact original SDK/model/asset directories required by your lock. Keep all roles
disjoint; a scene contract inside the SDK source directory shares its source
catalog and needs no overlapping contract root. No examples are installed or run.

A synthetic two-hop lock is imported through the in-process service. A separate
builder test hashes the real interpreter chain without executing it. The native
config/export test now uses the original interpreter alias, without canonicalizing
its new test lock. Tests requiring link-aware service resolution skip explicitly
when run against a checkout without that feature.
