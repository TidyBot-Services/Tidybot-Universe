# M-8 follow-up 3 report — 2026-10-23

Implemented `brief-main-3.md` in `wt-autoval`, without a commit, live service call,
model/simulator/GPU job, or protected model-file change. Service checkouts stayed
read-only. Both deployment examples remain Off and uninstalled.

## Current result

- Main-owned fake fixtures replace all service-test-package imports. The four
  specified files run fully with M-8 and skip only missing service features with
  the explicit reason “needs the M-8 Memory service” on the older checkout.
- Default `memory_validation_provision.Provisioner` resolves sealed queue
  provenance, original read-only ledger records, failed execution/source import
  descriptors, source/help/feedback digests, frozen config, signature and SDK
  source catalogs. Missing, altered or ambiguous evidence refuses visibly.
- The existing experiment creation path creates one idempotent validation
  experiment per operation, with ten cells, exactly ten executions and ten Dev
  calls, matching resource limits, no help/Advisor/model-Eval allocation, Codex,
  no Memory authority, run notes false and family false. Original native config
  bytes and identity-lock references remain unchanged. Ordinary UI sessions and
  reports exclude validation; the idle probe recognises its own experiment.
- New batches require ten remaining daily slots. Existing quota, idle, pause,
  grant, simulator binding and per-arm reservation checks remain in force.
- Validation execution records the existing harness GPU meter into an immutable
  receipt bound to its bundle/cell/version/attempt. Missing/unmeasured/altered
  evidence raises. Meter coverage explicitly includes other processes on the
  host GPU; it is not per-process attribution. Tests use synthetic measurements.
- Export preparation retains original config/lock references under the configured
  experiments root with an exact seal. Independent validation still gets the
  narrower experiment-root seal, with its strict root checks unchanged.
- The first fake e2e uses the default adapter and actual experiment/goal creation,
  with a controlled SDK registry and synthetic executor/replay. The actual
  in-process service promotes and demotes the note. The second scenario rejects
  a treatment safety regression after two arms.

## Remaining live-input requirements

Existing prepared executor and independent source-validator catalogs must be
listed by sealed reference in `runtime.json`. Missing prerequisites refuse;
the worker never invents an independent source verdict. The current provisional
importer does not itself produce that independent source-import evidence.

The configured interpreter path
`/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python`
is a symlink. Historical locks pinning that alias fail the existing canonical
source-file check before a validation budget is created. No historical lock or
path check was changed. The native-config/export test creates its own fresh test
lock with a canonical interpreter path. This limitation is recorded rather than
claiming live acceptance; it is not a missing M-8 service endpoint.

No remaining missing M-8 service features in `wt-autoval-svc`; no strict xfails.
The older service checkout deliberately skips dependent tests. No deployment or
real execution was performed.

## Commands and actual outputs

All commands ran from `/home/truares/桌面/AttentionBench-Workspace/development/wt-autoval`.

```bash
export PYTHONDONTWRITEBYTECODE=1
AUTOVAL_PY=/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python
M8_SERVICE=/home/truares/桌面/AttentionBench-Workspace/development/wt-autoval-svc
MAIN_SERVICE=/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service
export PYTHONPATH="$PWD:$M8_SERVICE"
```

Four specified files, M-8 service, exit 0:

```bash
"$AUTOVAL_PY" -B -m pytest -q benchmarks/attention_harness/tests/test_memory_{autovalidate_contracts,independent_validator,validation_runtime,validation_worker}.py -p no:cacheprovider
```
```text
........................................................................ [ 80%]
.................                                                        [100%]
89 passed in 17.73s
```

Same four files, older main service, exit 0:

```bash
PYTHONPATH="$PWD:$MAIN_SERVICE" "$AUTOVAL_PY" -B -m pytest -q -rs benchmarks/attention_harness/tests/test_memory_{autovalidate_contracts,independent_validator,validation_runtime,validation_worker}.py -p no:cacheprovider
```
```text
s........ssssssss..........s..ssss................s.....s............ss. [ 80%]
..sssss..........                                                        [100%]
=========================== short test summary info ============================
SKIPPED [19] benchmarks/attention_harness/tests/autovalidate_fakes.py:30: needs the M-8 Memory service
SKIPPED [4] benchmarks/attention_harness/tests/test_memory_independent_validator.py:15: needs the M-8 Memory service
66 passed, 23 skipped in 1.74s
```

Full main M-8 suite, M-8 service, exit 0:

```bash
"$AUTOVAL_PY" -B -m pytest -q benchmarks/attention_harness/tests/test_{memory_independent_validator,memory_validation_worker,memory_validation_runtime,memory_validation_provision,memory_autovalidate_contracts,memory_layers,codex_quota,dev_memory_prompt_bound,notices,memory_provisional,memory_run_notes,memory_monitor,daily_memory_wiring,pause_resume}.py -p no:cacheprovider
```
```text
........................................................................ [ 30%]
........................................................................ [ 60%]
......................................................................
Offline replay devexp-4a6bb2d8387428ca4f32b849: [{"version": 1, "read": [], "learned": false}, {"version": 2, "read": [], "learned": true}, {"version": 3, "read": [2], "learned": false}, {"version": 4, "read": [2], "learned": true}, {"version": 5, "read": [4, 2], "learned": true}]
.. [ 90%]
.......................                                                  [100%]
239 passed in 52.69s
```

Same full main M-8 suite, older main service, exit 0:

```bash
PYTHONPATH="$PWD:$MAIN_SERVICE" "$AUTOVAL_PY" -B -m pytest -q benchmarks/attention_harness/tests/test_{memory_independent_validator,memory_validation_worker,memory_validation_runtime,memory_validation_provision,memory_autovalidate_contracts,memory_layers,codex_quota,dev_memory_prompt_bound,notices,memory_provisional,memory_run_notes,memory_monitor,daily_memory_wiring,pause_resume}.py -p no:cacheprovider
```
```text
ssss..........................ss...sssss...........s.....s.ssssssssssss. [ 30%]
....s.s........ssssssss..........s...................................... [ 60%]
......................................................................
Offline replay devexp-4a6bb2d8387428ca4f32b849: [{"version": 1, "read": [], "learned": false}, {"version": 2, "read": [], "learned": true}, {"version": 3, "read": [2], "learned": false}, {"version": 4, "read": [2], "learned": true}, {"version": 5, "read": [4, 2], "learned": true}]
.. [ 90%]
.......................                                                  [100%]
203 passed, 36 skipped in 34.82s
```

Related experiment/configuration/family regressions, exit 0:

```bash
"$AUTOVAL_PY" -B -m pytest -q benchmarks/attention_harness/tests/test_{run_configuration,experiment_summary,codex_configuration,memory_allmodes,daily_bridge_compatibility}.py -p no:cacheprovider
```
```text
........................................................................ [ 69%]
...............................                                          [100%]
103 passed in 31.74s
```

Read-only M-8 service compatibility suite, exit 0:

```bash
"$AUTOVAL_PY" -B -m pytest -q ../wt-autoval-svc/tests/test_{auto_validation,arm_generation,scene_contract,source_catalog,failed_checks}.py -p no:cacheprovider
```
```text
........................................................................ [ 45%]
........................................................................ [ 91%]
.............                                                            [100%]
157 passed in 76.08s (0:01:16)
```

Fake e2e, exit 0:

```bash
"$AUTOVAL_PY" -B scripts/autovalidate_fake_e2e.py
```
```text
provisional → queued (1 use)
default adapter → one experiment, 10 cells, 10 Dev calls + 10 executions budgeted
5 pairs → verdict (control failure 5/5; treatment failure 0/5)
promoted → trusted on this scene
3 failing uses in last 5 → demoted to provisional
second scenario: treatment independent safety failed → rejected (2 arms; 0 accepted pairs)
Notice: Note rejected after check: Avoid PlanningRejected on left_arm.move_delta
PASS: fake runner, fake replay, real in-process service; no external calls
```

Node tests, exit 0:

```bash
node --test benchmarks/attention_harness/tests/test_{memory_autovalidate_ui,memory_monitor_ui,memory_monitor_runtime,memory_allmodes,dev_memory_lifecycle_ui,notices_ui}.cjs
```
```text
# tests 6
# suites 0
# pass 6
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 107.233335
```

`git diff --check`: exit 0, no output.

## Files changed in follow-up 3

- `benchmarks/attention_harness/memory_validation_provision.py` (new)
- `benchmarks/attention_harness/memory_validation_runtime.py`
- `benchmarks/attention_harness/memory_validation_worker.py`
- `benchmarks/attention_harness/dev_experiment.py`
- `benchmarks/attention_harness/dev_v2_loop.py`
- `benchmarks/attention_harness/daily_dev_v2.py`
- `benchmarks/attention_harness/dev_report.py`
- `benchmarks/attention_harness/tests/autovalidate_fakes.py`
- `benchmarks/attention_harness/tests/test_memory_autovalidate_contracts.py`
- `benchmarks/attention_harness/tests/test_memory_independent_validator.py`
- `benchmarks/attention_harness/tests/test_memory_validation_runtime.py`
- `benchmarks/attention_harness/tests/test_memory_validation_provision.py` (new)
- `scripts/autovalidate_fake_e2e.py`
- `scripts/memory-validation-runtime.example.json` (new, Off)
- `scripts/memory-autovalidate.service.example` (new, Off, not installed)
- `docs/memory-autovalidate/README.md`
- `docs/memory-autovalidate/REPORT.md`

Earlier uncommitted work was retained. Unrelated output/robot directories were
not changed by this follow-up. No file under `openarm365/model` or
`openarm_mobile/model` was changed.



## Pinned source symlinks — 2026-10-24

The service now verifies explicit source-only `links` at registration and read,
including each one-hop target, registered source catalog, exact sealed digest,
regular terminal file, eight-hop limit and cycle refusal. Existing allowlists,
role separation and legacy symlink checks are unchanged.

The main patch builds one catalog per link-chain directory and registers target
catalogs first, including after merging origin and arm inventories. Provisioning
and the offline exporter check supplied link pins. Both historical interpreter
aliases and the terminal file are sealed without changing historical locks.
The registration-roots example includes both actual interpreter directories.

Service changes were applied in `wt-autoval-svc`. The session filesystem policy
allowed writes only there and in `/tmp`, so the main changes were prepared and
tested in `/tmp/autoval-symlink-main`, with the original main worktree used only
as a read-only import fallback. They are delivered as `/tmp/main-symlink-pin.patch`
and have not been applied to `wt-autoval`. No commits, live service calls,
simulators, model calls or GPU jobs were made.

Validation (all commands used `PYTHONDONTWRITEBYTECODE=1`):

```bash
AUTOVAL_PY=/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python
SERVICE=/home/truares/桌面/AttentionBench-Workspace/development/wt-autoval-svc
MAIN_SERVICE=/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service
MAIN=/home/truares/桌面/AttentionBench-Workspace/development/wt-autoval
SHADOW=/tmp/autoval-symlink-main

# From SERVICE; external sandbox-only AnyIO wakeup plugin, no assertion changes.
PYTHONPATH="$SERVICE:/tmp" PYTEST_PLUGINS=autoval_pytest_wakeup "$AUTOVAL_PY" -m pytest -q tests --tb=short
# 394 passed in 197.57s (370 existing + 24 new).

# From SHADOW; repeat with SERVICE and MAIN_SERVICE respectively.
PYTHONPATH="$SHADOW:$MAIN:$SERVICE" "$AUTOVAL_PY" -m pytest -q benchmarks/attention_harness/tests/test_{memory_independent_validator,memory_validation_worker,memory_validation_runtime,memory_validation_provision,memory_autovalidate_contracts,memory_source_links,memory_layers,codex_quota,dev_memory_prompt_bound,notices,memory_provisional,memory_run_notes,memory_monitor,daily_memory_wiring,pause_resume,memory_validation_task}.py -p no:cacheprovider --tb=short
# Worktree service: 260 passed in 62.98s.
# Main service: 259 passed, 1 skipped in 62.52s.
# Only the new in-process linked-lock import is skipped: main service lacks links.

PYTHONPATH="$SHADOW:$MAIN:$SERVICE" "$AUTOVAL_PY" scripts/autovalidate_fake_e2e.py
# PASS: provisioned ten-arm promotion, later demotion, early safety rejection.

node --test benchmarks/attention_harness/tests/test_{memory_autovalidate_ui,memory_monitor_ui,memory_monitor_runtime,memory_allmodes,dev_memory_lifecycle_ui,notices_ui}.cjs
# 6 passed, 0 failed.
```

New tests cover the exact two-directory catalog shape and registration order,
provisioning and export pin checks, real interpreter aliases (hashing only),
cycle/depth/directory-link/digest refusal, and a synthetic two-hop execution import
through the in-process service with retarget refusal. The native config/export
regression now uses the historical interpreter alias without canonicalizing it.
