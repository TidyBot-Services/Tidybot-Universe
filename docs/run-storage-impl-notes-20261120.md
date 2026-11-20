# Run storage implementation notes — 2026-11-20

Implemented in this worktree only, without a commit or installation. Reader
inventory: [run-storage-readers-20261120.md](run-storage-readers-20261120.md).
The implementation's focused tests pass. The existing test selection is **not
fully green**: the exact outstanding results and baseline checks appear below.
No deployment, real simulator run, production backfill, remote-host access, port
8769/8878 operation, or Memory service source edit was performed.

## Storage contract and decisions

`benchmarks.attention_harness.run_storage` provides `read_bytes`, `read_text`,
`iter_lines`, `open_binary`, `exists`, and `logical_size`. Raw data wins over
`.zst`, which wins over `.derived`. Line iteration preserves line endings.
Optional decoders are tried in order: `zstandard`, `compression.zstd`, then
`zstd -dc`. Compression uses the corresponding level-3 encoder. No package or
hard runtime dependency was added. The CLI implements `plan`, `compact`,
`restore`, and inode-aware allocated-byte `measure`, with repeated
`--memory-store`, `--trash`, and `--json` options. Measurement counts
`st_blocks * 512`, including receipts, once per inode.

The original workspace raw read/digest/stat implementations retain their
checks. Only FileNotFoundError enters the fallback. Sidecars are opened through
no-follow directory descriptors and regular-file/shared-inode checks; changes
to size, mode, mtime or ctime during the read fail. Digest and public-size limits
count expanded bytes. The CLI decoder receives an already opened descriptor,
not a path it could reopen. Derived sources also use the secure reader for
workspace access. Physics work has the existing 1 GiB public-evidence bound;
the derived result has the caller's limit. Generic path readers retain their
callers' existing path boundaries.

`state-log-v1` calls the current broker components in precisely this order:
`encode(feedback([public_observation(json.loads(line)) ...]))`. Removal requires
identical SHA-256 **and** length. The stub records schema, derivation, original
SHA/length, cell-relative physics path and original physics SHA. Every read
verifies physics and output. Public copies must name their own execution;
Eval copies use `versions/vNNNN/submission.json`'s execution binding. Unknown
mappings or nonidentical bytes fall back to zstd. A literal byte golden, fixed output SHA and source/dependency digest pin
bind the projection and encoding; any component source change fails that pin. Future projection changes must
retain a v1 implementation for existing stubs rather than silently update it.

The allowlist is deliberately closed: public/workspace/Eval state, trace,
planner diagnostics and frame manifests; native physics/actions/RPC receipts;
planner HTTP/client journals and numbered world snapshots under executions.
Files must exceed 1 MiB. As with frame cleanup, a symlink anywhere in the
scanned tree refuses the operation instead of traversing it. Snapshots,
manifests, config/identity/lock files,
cleanup/compaction receipts, SQLite, other JSON/JSONL classes, images and video
are excluded. Every inode alias must be enumerated and eligible. An external,
snapshot, excluded or Memory-pinned alias excludes the entire group. This is
more conservative than unlinking public aliases while retaining a snapshot
object; it also means the forecast cannot be assumed achievable on sealed runs.

Compaction uses frame cleanup's terminal/paused/owner checks and run lock. A
receipt is written before changes. It records SHA, original length/mode,
aliases, action, status and any in-flight compression temporary. Compressed
bytes are fsynced and round-trip verified before publication. Every alternative
alias is published before any original is removed. Representations share one
inode per group. Re-entry can finish partially published/unlinked groups or
rebuild a bad alternate from a verified surviving raw file. Recorded partial
compression temporaries are discarded on recovery; deterministic link
temporaries can be recovered after verifying their bytes. Trash mode keeps the
originals on the same filesystem. Restore verifies receipt SHA/length, restores
shared aliases and removes representations. Restoring an uncompacted run is a
read-only no-op, including no new lock file.

The detached frame-cleanup child runs compaction after cleanup. An explicit
pending marker closes the crash gap between the two operations. Startup sweep
resumes in-progress receipts/pending chains; it does not enroll every old
completed frame receipt for bulk backfill. Process classifiers and idle/deploy
receipt scans recognize compaction. Live loops retain their existing detached
schedule call.

Configured Memory stores are read read-only, including committed WAL contents,
from `dev_v2_catalogs.payload` (`root` plus `files`). A catalog protects all
aliases of a referenced inode; unresolved/unreadable configured stores stop
compaction. `ATTENTIONBENCH_MEMORY_STORES` is an OS-path-separator list; explicit
CLI stores override it. **The requested default is no stores.** The deployment
owner must configure the relevant stores before enabling automatic compaction
on a host with Memory catalogs. The importer restores the cell before pinning
and materializes missing transitive paths before catalog completion; all
existing SHA checks remain. `checked` and auto-import also materialize verified
logical paths for their raw-path consumers. Registration and service code are
unchanged. Catalog registration and compaction are separate operations; callers
must configure stores and avoid racing registration with compaction.

Workspace publication/regeneration/share and replay freeze restore raw public
files before direct stat/link or sandbox use. Snapshot bytes and existing
unsafe-file checks remain intact. UI public reads inherit the secure fallback
and retain screening and canonical/workspace equality checks. Logical names
remain unchanged. The selected one-call HTTP artifact classes are excluded;
private native logs are not exposed as new HTTP endpoints. Evidence export
expands selected artifacts before its existing credential scans/translation;
sidecar encodings are not exported. The standalone published diagnose tool
keeps its stdlib raw-file fallback in live sandboxes.

The disk alarm is code only. It reads abctl's default/configured data convention,
writes `disk-low.json` below 130 GiB, and touches `stop-new-runs` below 60 GiB.
Recovery clears the low marker but never the stop latch. CLI paths/thresholds
and dry-run are configurable. User unit templates run every 30 minutes; neither
unit was installed or started.

## Changed files

- `benchmarks/attention_harness/dev_agent_adapters.py`
- `benchmarks/attention_harness/dev_agent_diagnose.py`
- `benchmarks/attention_harness/dev_agent_replay.py`
- `benchmarks/attention_harness/dev_agent_workspace.py`
- `benchmarks/attention_harness/dev_loop.py`
- `benchmarks/attention_harness/dev_scene_diag.py`
- `benchmarks/attention_harness/dev_v2_loop.py`
- `benchmarks/attention_harness/dev_v2_memory_import.py`
- `benchmarks/attention_harness/diagnostics/base_motion_probe.py`
- `benchmarks/attention_harness/diagnostics/grasp_chain_probe.py`
- `benchmarks/attention_harness/diagnostics/motion_tracking_matrix.py`
- `benchmarks/attention_harness/diagnostics/reference_ladder.py`
- `benchmarks/attention_harness/diagnostics/start_collision_diagnose.py`
- `benchmarks/attention_harness/event_timing.py`
- `benchmarks/attention_harness/frame_cleanup.py`
- `benchmarks/attention_harness/free_view.py`
- `benchmarks/attention_harness/home_status.py`
- `benchmarks/attention_harness/memory_auto_import.py`
- `benchmarks/attention_harness/memory_independent_validator.py`
- `benchmarks/attention_harness/run_storage.py`
- `benchmarks/attention_harness/tests/test_run_storage.py`
- `docs/run-storage-impl-notes-20261120.md`
- `docs/run-storage-readers-20261120.md`
- `remote_gateway/deploy/abctl.py`
- `remote_gateway/deploy/disk_watch.py`
- `remote_gateway/deploy/systemd/ab-disk-watch.service`
- `remote_gateway/deploy/systemd/ab-disk-watch.timer`
- `remote_gateway/tests/test_disk_watch.py`
- `scripts/attentionbench/export_evidence_bundle.py`
- `scripts/attentionbench/free_view_export.py`
- `scripts/attentionbench/idle-check.sh`
- `scripts/attentionbench/state_log_dedupe.py`
- `scripts/family_live_path_replay.py`
- `scripts/run_reference_policy.py`

Existing untracked `.codex-brief-run-compact.md`, `openarm_phase0/` and the
pre-existing untracked `openarm_phase1` artifacts were left alone. Broker,
projection, Eval and view modules whose shared-reader boundary already suffices
are intentionally unchanged. `fixture_pilot.start_probe_report` is a producer
report called directly after execute, outside the terminal UI-ledger lifecycle;
its native snapshot dependency boundary is unchanged.

## Verification

All pytest invocations used the requested interpreter and options:

```sh
PYTHONPATH=/tmp/run-storage-test-deps:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service \
  /home/truares/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider TEST_FILES...
```

The requested interpreter lacks PyYAML. The initial broader collection stopped
with 11 missing-yaml errors. For subsequent tests, the existing system
`/usr/lib/python3/dist-packages/yaml` package was copied to
`/tmp/run-storage-test-deps/yaml`; no environment or dependency installation was
changed. `zstd` CLI was exercised for real compression/decompression. The two
Python decoder adapters and missing-module fallback were tested with mocks;
Python 3.14's actual decoder was unavailable in this Python 3.10 interpreter.
Constructor/file-object contracts were checked against the official
[Python zstd API](https://docs.python.org/3/library/compression.zstd.html) and
[python-zstandard decoder API](https://python-zstandard.readthedocs.io/en/latest/decompressor.html).

New tests (`test_run_storage.py`, `remote_gateway/tests/test_disk_watch.py`):
**54 new cases passed as part of the final 63-case group (42.51 s)**. The group also included the 4 existing summary and 5 existing bundle-export cases. Coverage includes fallback order and all decoder
adapters; byte golden and mismatches; Eval mapping; hardlinked derived and
nonidentical/zstd state logs; active/paused/owner refusal; unreadable Memory
stores and catalog inode exclusion; snapshot/external aliases; secure sidecar
checks, raw precedence over unused symlink sidecars, partial stream closure and expanded limits; interrupted publication/unlink/receipt, bad alternate
recovery, a subprocess forced to `os._exit(23)` mid-compression, trash and restore;
raw-run restore immutability; view/projection/diagnose/Eval/Memory registration,
free-view exporter, bundle, dedupe/frame cleanup and replay; automatic chain,
startup and idle/deploy recognition; disk thresholds, latch and dry-run.

A storage/cleanup/export reporting selection also passed **261 tests in 7.59 s**.
A standalone d5 run passed **39 tests in 28.16 s**. These overlap the per-file
results below and must not be added to them as independent coverage counts.

Existing coverage was finally run per file in separate pytest processes (48
files), avoiding cross-module collection effects. A stable rerun of
`test_memory_origin_c9.py`, `test_dev_agent_d5.py` and
`test_dev_agent_product_v2.py` produced **85 passed, 1 failed in 73.83 s**; it
supersedes the initial observations for those files. The no-op restore regression
was fixed. Two temporary source-drift observations during an earlier run were
resolved by that stable rerun.

The first unredirected per-file totals were **1,337 passed, 84 failed,
18 setup errors, 3 skipped**. The five cache-blocked files were then retried with
an additional test-only pytest plugin (`/tmp/run_storage_test_cache.py`,
loaded with `-p run_storage_test_cache` and `/tmp` on `PYTHONPATH`). Its autouse
fixture patches only
`curobo_v2.dev_local.DEV_LOCAL_ROOT` to an owned `TemporaryDirectory` below
`/tmp`, which is removed at session end. No production cache, path registry or
source configuration was changed. The 157-case group produced **153 passed,
4 failed in 244.91 s**. Its one Memory context failure did not reproduce in a
stable full-file rerun: **32 passed in 108.89 s** on this code and **32 passed in
116.09 s** with HEAD-loaded modules. The other three planner/UI assertions
reproduced individually with HEAD-loaded modules and the same temporary cache.

Replacing superseded observations with these stable reruns gives **1,421
passed, 18 failed, 3 skipped** across the 48 existing files. No setup errors
remain with that test-only cache configuration. This is still not a fully green
regression run. The per-file table identifies which results used the temporary
cache; pass counts include all actually executed per-file test cases.

| Existing test file | Latest result |
|---|---|
| `benchmarks/attention_harness/tests/test_daily_memory_wiring.py` | 7 passed in 2.22s |
| `benchmarks/attention_harness/tests/test_dev_agent_d1.py` | 2 failed, 41 passed in 12.14s |
| `benchmarks/attention_harness/tests/test_dev_agent_d2.py` | 44 passed in 43.09s |
| `benchmarks/attention_harness/tests/test_dev_agent_d4.py` | 32 passed in 56.65s |
| `benchmarks/attention_harness/tests/test_dev_agent_d5.py` | 39 passed (stable rerun; earlier source-drift error resolved) |
| `benchmarks/attention_harness/tests/test_dev_agent_d7.py` | 44 passed in 28.48s |
| `benchmarks/attention_harness/tests/test_dev_agent_eval_repair.py` | 6 passed in 0.70s |
| `benchmarks/attention_harness/tests/test_dev_agent_fault_injection.py` | 15 passed in 12.57s |
| `benchmarks/attention_harness/tests/test_dev_agent_framelink.py` | 11 passed, 3 skipped in 1.90s |
| `benchmarks/attention_harness/tests/test_dev_agent_policy_v2_flow.py` | 8 failed, 101 passed in 127.70s (0:02:07) |
| `benchmarks/attention_harness/tests/test_dev_agent_product_v2.py` | 1 failed, 29 passed (stable rerun; old tools golden) |
| `benchmarks/attention_harness/tests/test_dev_agent_replay_ui.py` | 27 passed in 8.38s |
| `benchmarks/attention_harness/tests/test_dev_agent_try_story.py` | 24 passed in 1.10s |
| `benchmarks/attention_harness/tests/test_dev_agent_validation_runner.py` | 4 failed, 60 passed in 211.72s (0:03:31) |
| `benchmarks/attention_harness/tests/test_dev_agent_workspace.py` | 12 passed in 40.04s |
| `benchmarks/attention_harness/tests/test_dev_exec_summary.py` | 4 passed in 0.30s |
| `benchmarks/attention_harness/tests/test_dev_loop.py` | 34 passed in 1.98s |
| `benchmarks/attention_harness/tests/test_dev_v2_memory_import_evidence.py` | 24 passed in 4.44s |
| `benchmarks/attention_harness/tests/test_event_timing.py` | 13 passed in 0.23s |
| `benchmarks/attention_harness/tests/test_family_live_path_replay.py` | 3 passed in 0.28s |
| `benchmarks/attention_harness/tests/test_frame_cleanup.py` | 109 passed in 6.16s |
| `benchmarks/attention_harness/tests/test_free_view.py` | 33 passed in 0.25s |
| `benchmarks/attention_harness/tests/test_home_status.py` | 18 passed in 0.21s |
| `benchmarks/attention_harness/tests/test_memory_autovalidate_contracts.py` | 30 passed in 11.32s |
| `benchmarks/attention_harness/tests/test_memory_independent_validator.py` | 19 passed in 1.04s |
| `benchmarks/attention_harness/tests/test_memory_origin_c9.py` | 17 passed (rerun after no-op restore fix) |
| `benchmarks/attention_harness/tests/test_memory_origin_evidence.py` | 41 passed in 87.55s (0:01:27) |
| `benchmarks/attention_harness/tests/test_memory_origin_registration_retry.py` | 49 passed in 58.32s |
| `benchmarks/attention_harness/tests/test_memory_provisional.py` | 32 passed in 108.89 s (stable temporary-cache rerun) |
| `benchmarks/attention_harness/tests/test_memory_validation_runtime.py` | 10 passed in 9.39s |
| `benchmarks/attention_harness/tests/test_reference_ladder.py` | 25 passed in 0.42s |
| `benchmarks/attention_harness/tests/test_reference_policy.py` | 76 passed in 5.07s |
| `benchmarks/attention_harness/tests/test_replay_trim.py` | 44 passed in 19.64s |
| `benchmarks/attention_harness/tests/test_sim_timeshare.py` | 18 passed in 10.34s |
| `benchmarks/attention_harness/tests/test_sim_timeshare_acceptance.py` | 8 passed in 3.82s |
| `benchmarks/attention_harness/tests/test_start_collision_diagnose.py` | 3 passed in 0.18s |
| `benchmarks/attention_harness/tests/test_state_log_dedupe.py` | 10 passed in 1.51s |
| `openarm365_dev_v2/tests/test_base_motion.py` | 20 passed in 0.44s |
| `openarm365_dev_v2/tests/test_diag_epochs.py` | 2 passed (temporary cache rerun) |
| `openarm365_dev_v2/tests/test_grasp_chain_probe.py` | 49 passed in 0.51s |
| `openarm365_dev_v2/tests/test_grasp_stability_diagnostics.py` | 5 passed in 0.26s |
| `openarm365_dev_v2/tests/test_motion_tracking_diagnostics.py` | 48 passed in 0.27s |
| `openarm365_dev_v2/tests/test_planner_features.py` | 3 failed, 39 passed (temporary cache rerun) |
| `openarm365_dev_v2/tests/test_prespawn.py` | 42 passed (temporary cache rerun) |
| `openarm365_dev_v2/tests/test_scene_contract.py` | 39 passed (temporary cache rerun) |
| `openarm365_dev_v2/tests/test_worker_inputs.py` | 19 passed in 49.28s |
| `remote_gateway/tests/test_abctl.py` | 41 passed in 2.29s |
| `scripts/attentionbench/tests/test_evidence_bundle.py` | 5 passed in 1.00s |

Outstanding failures, without changing unrelated behavior or relaxing tests:

- `test_dev_agent_d1`: existing eager agent-provider import violates its old-path
  sentinel; real bubblewrap transport cannot create a NETLINK_ROUTE socket in
  this sandbox. The original HEAD versions of touched modules reproduced the
  sentinel failure, and a separate HEAD-loader baseline reproduced bubblewrap's
  same permission failure.
- `test_dev_agent_product_v2`: the old tools golden lacks the Memory citation
  rule already appended on HEAD. The baseline loader reproduced this exact
  assertion failure.
- `test_dev_agent_policy_v2_flow`: eight legacy-public-byte goldens differ from
  older frozen broker artifacts. HEAD-loader reproduction of the autonomous
  case fails at the same public-byte equality assertion. The other seven
  parameter cases were not individually rerun against HEAD.
- `test_dev_agent_validation_runner`: four runner-byte comparisons with `main`
  fail. HEAD-loader reproduction of the onecall case fails at the same sandbox
  receipt comparison; the other three parameter cases were not individually
  rerun against HEAD.
- `test_planner_features`: with a writable test-only cache, two existing
  Memory assertions expect content-only notes but receive the existing
  title/level/memory-id metadata, and one existing UI assertion expects an old
  five-module string. All three fail in the same way with HEAD-loaded modules.
  No Memory policy or UI code was changed to silence them.

The original read-only-cache failures are no longer outstanding after the
explicit test-only relocation above. The representative unredirected planner
case also reproduced the read-only-cache error using HEAD-loaded modules. The
legacy cache was never made writable, and no test was skipped to obtain the
updated results. The isolated Memory file is now fully green.

Initial broad runs were stopped after discovering unrelated/import-scope
failures and then superseded by the per-file selection; they are not claimed
as completed runs. Baseline loading compiled `git show HEAD:<file>` content
inside temporary Python import loaders, without changing this checkout or any
other checkout. Existing Memory tests include a read-only historical-origin
copy check; compaction itself was exercised only on temporary synthetic runs.
`git diff --check`, AST parsing of all 29 changed/new Python files, both CLI
help commands and shell syntax checking of idle-check passed. A real
`disk_watch.py --data /tmp --dry-run` also completed without writing markers.

## Measurement and limits

Before creating the temporary measurement fixture, `df -B1 /tmp` reported
321,438,150,656 free bytes. The fixture repeats two physics rows 6,500 times and
has two hardlinks to the projected state log. It was removed on completion.
Measured allocation (one inode counted once):

| Class | Before bytes | After bytes |
|---|---:|---:|
| state_log*.json | 1,847,296 | 4,096 |
| native-physics.jsonl | 1,466,368 | 4,096 |
| other JSON (receipt) | 0 | 4,096 |
| all other classes | 0 | 0 |
| Total | 3,313,664 | 12,288 |

The after/before ratio is 0.3708%. Logical candidate bytes were 3,308,503:
1,462,500 physics bytes plus 1,846,003 projected state bytes. This highly
repetitive synthetic input verifies allocation accounting and round-trip
behavior; it is **not** evidence for the plan's real-run compression forecasts.
No real-run or qifei before/after comparison was performed. Real-run acceptance measurements remain unperformed in this worktree.

Residual limits: derived state logs are reconstructed and verified before being
returned, which buffers their physics/projected JSON and can cost substantial
memory/CPU on large logs; compressed stat/link availability may require a full
expanded read. Snapshot/Memory/external aliases may greatly reduce reclaimable
space. The decoder or zstd executable must exist for compressed files. Generic
zstd reads rely on decoder validation plus the caller's existing SHA checks;
restore additionally verifies the persisted original SHA. Trash is retained
until an operator explicitly removes it. Automatic compaction eligibility is
only as authoritative as the existing frame-cleanup ledger/owner checks.

The plan identifies an overlap with `wt-oracle-perception`. This change touches
`dev_agent_workspace.py` and leaves `dev_agent_broker.py` unchanged. The reviewer
should merge the oracle work first, then integrate these reader fallbacks and
materialization hooks around the resulting raw workspace functions; preserve
both changes and rerun workspace/reader, policy and golden tests. The oracle
worktree was not inspected or changed, and no merge was attempted here.


## Round 2

This section supersedes Round 1's snapshot exclusion, unbounded derivation and
automatic-compaction behavior described above. All work remains confined to this
worktree and synthetic temporary fixtures, with no commit, deployment, live run,
other-checkout mutation or Memory service source change. The extra Round 1
`docs/REPORT-run-storage-compress-20261120.md` has been deleted as requested.

Changes:

- Snapshot-object groups are eligible when every non-object alias is an
  allowlisted public/ws/Eval JSON path and every object's hex64 basename equals
  the SHA-256 of the original bytes. They always use zstd, never derivation.
  Standalone objects, image aliases, wrong digests, metadata, native-log aliases,
  external aliases and Memory-pinned groups remain excluded. Receipt recovery
  and restore validate the same group rule. All compressed aliases share one
  inode; restore re-creates the original shared raw inode and verifies bytes.
- The secure reader sharing contract now covers historical read-only state,
  trace, frames and planner JSON under executions. Current publisher hardlink/
  copy behavior is unchanged. Existing public, Eval, UI and snapshot readers inherit it;
  no-follow, read-only shared-inode and expanded-byte limits are retained.
  Freeze now securely reads/verifies existing raw or compressed object seals.
  Replay already used the secure fallback and verifies original SHA/length;
  missing non-image errors and image-cleaned classification remain intact.
  State-log dedupe verifies compressed object bytes securely, then skips the
  representation rather than linking it into a raw path.
- Private probe-owner planner HTTP/client journals and numbered world snapshots
  join the allowlist. The reader audit and A-table additions are in the companion
  inventory. Their existing unified readers and Memory rehydration boundaries
  suffice; the diagnostic, catalog-pin and original-byte regressions exercise
  actual compressed private probe paths. Unrelated planner classes stay raw.
- Run-end chaining and startup compaction recovery require
  `ATTENTIONBENCH_RUN_COMPACT=1`; frame cleanup still runs without it. The pending
  marker is created only when enabled. Manual compaction remains ungated. An
  enabled host without `ATTENTIONBENCH_MEMORY_STORES` still compacts with an empty
  store list. **Deployments with Memory must set all relevant stores before
  enabling automatic compaction.** Plans and persisted receipts record absolute
  consulted store paths; recovered receipts also retain earlier consulted paths.
- `DERIVE_MAX_BYTES = 64 * 1024 * 1024`, overridden in bytes by
  `ATTENTIONBENCH_DERIVE_MAX_BYTES`, bounds new derivations by original state-log
  size. Above the cap, the planner skips projection entirely and selects zstd.
  Existing derived stubs retain their pinned reader semantics.

Verification uses the requested interpreter, PYTHONPATH and pytest options.
Final requested selection — **271 passed in 81.46 s (0:01:21)**:

```sh
PYTHONPATH=/tmp/run-storage-test-deps:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider benchmarks/attention_harness/tests/test_run_storage.py benchmarks/attention_harness/tests/test_frame_cleanup.py benchmarks/attention_harness/tests/test_state_log_dedupe.py benchmarks/attention_harness/tests/test_dev_agent_replay_ui.py benchmarks/attention_harness/tests/test_dev_agent_workspace.py benchmarks/attention_harness/tests/test_home_status.py benchmarks/attention_harness/tests/test_start_collision_diagnose.py benchmarks/attention_harness/tests/test_dev_v2_memory_import_evidence.py
```

| Requested file | Cases passed in final selection |
|---|---:|
| `test_run_storage.py` | 65 |
| `test_frame_cleanup.py` | 111 |
| `test_state_log_dedupe.py` | 11 |
| `test_dev_agent_replay_ui.py` | 27 |
| `test_dev_agent_workspace.py` | 12 |
| `test_home_status.py` | 18 |
| `test_start_collision_diagnose.py` | 3 |
| `test_dev_v2_memory_import_evidence.py` | 24 |

Additional affected-reader selection — **91 passed in 29.38 s**:

```sh
PYTHONPATH=/tmp/run-storage-test-deps:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider benchmarks/attention_harness/tests/test_dev_agent_d5.py benchmarks/attention_harness/tests/test_dev_agent_eval_repair.py benchmarks/attention_harness/tests/test_dev_agent_try_story.py benchmarks/attention_harness/tests/test_memory_independent_validator.py benchmarks/attention_harness/tests/test_family_live_path_replay.py
```

New coverage includes three-link ws/public/object groups for each selected public
JSON class, zstd-only action selection, secure UI reads, identical-byte replay,
one-inode/nlink-3 restore, raw/zstd object seals at freeze/replay, genuinely
missing object errors, and exclusion of image/native/metadata/standalone/wrong-SHA
object groups. Two image tests compact a historically shared frame-index object,
then verify cleaned-image replay both with a cleanup receipt and with only a
verified index. Compressed-object dedupe verifies original bytes and rejects a
valid zstd frame with a wrong original SHA. Probe tests cover all three named
planner classes, unchanged diagnostic parsing/world checks, and Memory catalog
pinning of original bytes. Gate cases cover unset, `0`, `true` and exact `1`,
startup pending recovery, enabled children without configured Memory stores,
and ungated manual CLI behavior. Cap tests cover the exact boundary, above-cap
zstd selection without projection, and the 64 MiB default.

Development runs before the final fixes produced **62 passed, 2 failed in
60.60 s**, **202 passed, 1 failed in 30.01 s**, and **267 passed, 1 failed in
80.89 s**. These exposed incomplete historical-alias fixture construction and a
producer-permission change. The final implementation preserves current producer
behavior while extending secure readers; valid frame-index fixtures and the
unchanged corruption test now pass. The final complete runs above supersede
those observations. No failing cases were skipped or checks relaxed.

`git diff --check` and AST parsing of all eight changed Python files passed.
The existing temporary PyYAML support under `/tmp/run-storage-test-deps` was reused;
no dependencies were installed. All compaction/replay fixtures were synthetic
and temporary. Round 1's broad historical failure table is retained above as a
record of that round, not a claim that those unrelated suites were rerun here.
