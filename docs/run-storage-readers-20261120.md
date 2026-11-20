# Run storage reader inventory (2026-11-20)

Scope: explicit allowlist only: agent public/workspace/eval `state_log.json`,
`trace.json`, `diagnostics/planner.json`, `frames.json`; episode
`native-physics.jsonl`, `native-actions.jsonl`, `rpc-receipts.jsonl`,
`planner/{planner_http.jsonl,v2_client.jsonl,world-NNNN.json}`; private
`probe-owner-NNNN/planner/{planner_http.jsonl,v2_client.jsonl,world-NNNN.json}`.
Verified snapshot-object aliases of the public JSON classes use zstd only. Other JSON/JSONL
classes remain raw. Raw files always take precedence. This inventory was written
before implementation; the disposition column defines the migration boundary.

Searches: repository-wide named-basename search, `state_log`, `frames.json`,
`diagnostics/planner`, `rglob`/`glob` (including generic JSON scans), raw reads,
sha helpers, HTTP and subprocess transports. Historical frozen protocol packages
are not run consumers and are outside this allowlist. Source/config/lock files,
snapshot manifests/cut pointers, receipts, journals not named above, images and
videos are excluded. A snapshot object is eligible only in a group containing
allowlisted public/ws/Eval JSON aliases, with its hex64 name equal to the SHA-256
of the original bytes. All group aliases become links to one zstd inode. Any
other excluded, external, or Memory-pinned alias excludes the whole group.

| Reader file / function | Logical inputs | Mechanism | When | Disposition |
|---|---|---|---|---|
| `free_view.py:build`, `SOURCES` | physics, RPC receipts | file-presence check; exporter subprocess | after | unified existence; exporter reads original bytes |
| `scripts/attentionbench/free_view_export.py:jsonl,load_motion,follow` | physics, RPC receipts | raw text/binary stream; live offsets | live/after | unified reads/stream, retain live incremental semantics |
| `dev_agent_workspace.py:read_beneath` | public/eval state, trace, planner, frames | no-follow dirfd, regular-file, inode and size checks | both | secure unified fallback; decompressed limit; unchanged raw checks |
| `dev_agent_workspace.py:digest_beneath,stat_beneath` | same | bounded streaming SHA / safe stat | both | secure fallback, original-byte SHA and logical size |
| `dev_agent_workspace.py:publish,_restore,regenerate,share` | same | encode, compare, hardlink / copy | live/resume | materialize compressed canonical evidence before direct stat/share; preserve mode |
| `dev_agent_broker.py:_execution` | projected physics state, planner; published trace/frames | feedback then Workspace.publish | live | unchanged publisher; pinned state-log-v1 reproduces this exact path |
| `dev_agent_adapters.py:Executor` | physics, v2_client | raw text -> public_observation | live/recovery | unified reader |
| `dev_agent_eval.py:build_workspace` | public state/trace/planner/frames | read_beneath -> private raw copies | live/replay | secure reader inherited; Eval copies derived only with exact execution mapping |
| `dev_agent_view.py:_read,_link,artifact` | public state/trace/planner/frames | read/stat_beneath, ws/canonical byte comparison | both | secure fallback inherited; sanitization and equality retained |
| `dev_agent_ui_projection.py:public,version_evidence,evidence` | same | artifact/read_beneath; wire SHA | both | inherited secure reader; original logical names |
| `dev_agent_replay.py:freeze_origin,provision_replay` | public evidence; sealed snapshot objects | rglob; SHA; share; no-follow read | live/after/continue | canonical tree rehydrated before freeze; existing raw/zstd objects securely read and original SHA checked; replay uses secure fallback and verifies SHA/length |
| `dev_agent_recording.py:recording_frames` | recording frames.jsonl | read_beneath | both | excluded producer journal; unchanged |
| `dev_agent_try_story.py:_json,_failure,tries` | public trace; result metadata | read_beneath | both | inherited secure reader; metadata/video excluded |
| `dev_agent_export.py:Exporter.capture,Exporter.settle,reference` | public frames/trace/planner via public-feedback | read_beneath and dev_loop.sha | live/recovery | secure reader and original-byte SHA |
| `dev_agent_bundle.py:bundle` | authored sources and bundle map | no-follow source reads | live/continue | excluded authored sources; unchanged |
| `dev_ui_server.py:Handler.do_GET` | one-call evidence | raw file/range HTTP | both | allowlist excludes one-call public trace; agent route inherits secure reader |
| `ui_server.py` agent evidence routes | public evidence | artifact/HTTP, range media | both | inherited secure reader; original names and existing screening |
| `remote_gateway/server.py:Handler.relay` | upstream public evidence | authenticated HTTP pass-through | both | unchanged pass-through; upstream expands original bytes |
| `dev_agent_diagnose.py:read_json,build` | state/trace/planner | safe_path/read_text; generic rglob/stat listing | both | unified read; logical listing and sizes; standalone copied tool keeps stdlib raw fallback |
| `dev_agent_exec_summary.py:build` | in-memory state/trace/planner | object arguments, no file reader | live | unchanged; no on-disk reader |
| `dev_agent_attention_v2.py:HelperV2._public_execution,public_request_view` | trace/planner/frames | read_beneath, digest | live/recovery | inherited secure reader; policy-journal metadata stays raw |
| `dev_agent_attention.py`, `dev_v2_loop.py` recording selection | recording frames.jsonl | raw journal | live | excluded journal |
| `event_timing.py:_rows,derive_event_times` | RPC receipts, native actions | raw JSONL read | both | unified original-byte reader |
| `home_status.py:planner_seconds` | planner_http | glob, raw text | both | logical glob + unified text |
| `diagnostics/base_motion_probe.py:_jsonl,collision_command` | actions, physics, planner world | raw text | after | unified reader/existence |
| `diagnostics/grasp_chain_probe.py:held_physics_samples` | actions, physics | raw text | after | unified reader/existence |
| `diagnostics/motion_tracking_matrix.py:read_jsonl,last_jsonl` | actions, physics | raw text/stream | after | unified reader/existence |
| `diagnostics/reference_ladder.py:summarize` | physics | raw stream | after | unified stream/existence |
| `diagnostics/start_collision_diagnose.py:read_recording` | planner_http, world | text stream/glob | after | unified stream/logical glob |
| `dev_scene_diag.py:reset_rpc_receipt` | RPC receipts | raw bytes + per-line SHA | live/after | unified bytes; SHA unchanged |
| `openarm365_dev/planning_rejection.py:call_with_rejection_facts` | v2_client | fd/raw stream and authentication | live | raw reader retained: pre-dispatch safety check only, never terminal artifact replay |
| `openarm365_dev/executor.py`, workers, `curobo_v2/client.py,worker.py,sapien_planner.py` | physics/actions/RPC/planner logs/world | append / write | live | unchanged writers; terminal/owner guard prevents concurrent compaction |
| `openarm365_dev_v2/fixture_pilot.py:start_probe_report` | RPC receipts | glob/text | producer lifetime only | unchanged: called directly by main immediately after runner.execute, outside the UI-ledger compaction lifecycle; no standalone replay reader |
| `scripts/run_reference_policy.py:build_verdict` | RPC receipts, native actions | subprocess/report reader | after | logical glob + unified text |
| `scripts/attentionbench/export_evidence_bundle.py:export,copy` | public state/trace; generic selected artifacts | raw read / presence / SHA | after | unified reads/existence; existing credential screening and translation retained |
| `scripts/family_live_path_replay.py:read,digest,CopyEvidence` | selected logs via transitive refs or whole experiment copy | raw paths, rglob, SHA, copy | after | logical enumeration, expanded reads and copies |
| `memory_auto_import.py:_verified` | generic sealed refs to execution evidence | existence, SHA, raw-path return | after | original-byte checks then rehydrate for existing raw-path consumers |
| `memory_agent_validation.py:shared`, `memory_validation_provision.py:read` | generic sealed refs | checked -> read_beneath / raw Path | after | checked rehydrates; all SHA checks retained |
| `episode_trace.py:_evidence_refs,_sha256_file` | explicit observation/code/camera files and trace.jsonl | raw stat, streaming SHA | live/after | all its enumerated classes excluded; does not enumerate selected native journals |
| `scripts/attentionbench/*` report/build scripts | source trees, frozen packages, manifests, summary outputs | rglob, sha | after | excluded classes; no additional selected log reader found |
| `frame_cleanup.py:plan,load,_expected` | public frames.json; recording journals; snapshots | _scan, raw JSON, SHA | after | logical scan + unified load; journals and snapshot metadata remain raw; compressed non-image objects do not enter image deletion/missing logic |
| `scripts/attentionbench/frame_cleanup_backfill.py:inventory` | manifests/cleanup receipts | rglob/load | after | excluded receipts; inherits aware frame plan |
| `scripts/attentionbench/state_log_dedupe.py:groups` | raw state logs / snapshot objects | rglob, inode+SHA | after | skips already compacted state logs and eval copies; securely verifies compressed object SHA/length before skipping it; never links sidecars into raw objects |
| `dev_v2_memory_import.py:prepare,pin,walk` | all transitively pinned evidence | raw path, read_beneath, SHA, JSON recursion | after/live import | restore before pinning; retain all digest checks and raw registration contract |
| `dev_planner_memory.py` import entry points | refs to execution evidence | sha/prepare | live/after | original-byte dev_loop.sha; prepare rehydrates |
| `memory_validation_runtime.py`, `memory_origin_evidence.py`, `memory_origin_registration.py` | catalogs and sealed refs | checked/prepare, raw SHA | after | prepare rehydrates; catalog-pinned files excluded by Memory guard |
| `memory_independent_validator.py:sealed_files,sealed` | evidence tree and refs | generic rglob, SHA | after | logical enumeration and original-byte reads; rehydrate before catalog sealing |
| `dev_v2_loop.py:checked` and `dev_loop.py:sha` | arbitrary sealed refs incl. selected logs | raw SHA/checked path | both | SHA over original bytes; checked materializes selected evidence for raw-path consumers |
| Memory service (separate repository) | registered root+files | raw path re-read + SHA | after | untouched; configured catalogs exclude whole inode groups; importer restores originals |
| resume/continue: `dev_agent_loop.py`, `dev_wait_recovery.py`, replay provisions | public evidence and snapshots | Workspace.regenerate / provision_replay | resume | restore before canonical stat/share/scan; snapshot objects use secure original-byte reads |

Automatic compaction is chained after frame cleanup in its detached worker only
when `ATTENTIONBENCH_RUN_COMPACT=1`; startup compaction recovery has the same gate.
No live run, foreign checkout or existing run data is modified by this work.


## Round 2

The A table above now includes verified snapshot aliases. These additional rows
record the probe-owner audit and its transitive reader boundaries. Repository
searches covered `probe-owner`, `planner_http.jsonl`, `v2_client.jsonl`, numbered
world JSON, and generic glob/rglob readers in Python, shell and UI sources.

| Reader file / function | Logical inputs | Mechanism | When | Disposition |
|---|---|---|---|---|
| `home_status.py:planner_seconds` | execution planner HTTP journals | logical glob + unified text | both | already migrated; deliberately selects `v*/executions/*`, never probe-owner logs; timing selection unchanged |
| `diagnostics/start_collision_diagnose.py:read_recording` | caller-selected probe-owner planner HTTP/world files | unified binary stream, logical world glob, original payload world SHA check | after | already migrated; exercised against compacted probe-owner directory |
| `diagnostics/base_motion_probe.py:collision_command` | caller-selected planner world | unified existence/read | after | already migrated; accepts the same logical world path |
| `dev_agent_adapters.py:Executor._execute` | planner client journal | unified text/existence | live/recovery | already migrated; probe owner itself returns RPC results in memory |
| `openarm365_dev_v2/agent_probe.py:probe`, `dev_agent_broker.py` probe branch | probe planner responses | in-memory RPC/result projection | live | no saved planner-log reader; public probe JSON remains raw |
| `dev_agent_ui_projection.py`, `dev_agent_view.py`, `dev_agent_diagnose.py` | public probe results and execution planner diagnostics | secure public reader; logical diagnostic listing | both | no direct private probe-owner log endpoint; shared secure reader recognizes selected read-only execution JSON aliases |
| `dev_v2_memory_import.py:prepare,pin,walk` and Memory import entry points | sealed refs into private probe-owner planner logs/world | restore cell; rehydrate transitive logical refs; original-byte SHA; raw-path pin | after/import | already migrated; added compressed private-world pin regression with catalog digest verification |
| `memory_independent_validator.py:sealed_files,sealed` | generic selected evidence, including probe directories if selected | logical enumeration, rehydrate, original-byte SHA | after | already migrated; hex64 object sidecars now enumerate under original names too |
| `scripts/family_live_path_replay.py:CopyEvidence` | whole-run copies / sealed refs, potentially probe directories | logical enumeration + original-byte copies/SHA | after | already migrated |
| `scripts/attentionbench/export_evidence_bundle.py:safe_file,export` | probe-owner directories | explicit `probe-owner-*` exclusion | after | still excluded from this export; no direct reader |
| `diagnostics/grasp_chain_probe.py`, `reference_ladder.py` report paths | planner `continuous-*.json` certificates | raw glob/text | after | different class, still excluded from compaction |
| `openarm365_dev/planning_rejection.py:call_with_rejection_facts` | current probe/client rejection journal | raw fd with identity/safety checks | live | unchanged pre-dispatch reader; owner/terminal guard excludes concurrent compaction |
| `curobo_v2/{client,worker,sapien_planner}.py`, `openarm365_dev/planner.py` | private probe planner journals/world | append/write | live | unchanged producers; terminal/owner guard applies |

Secure readers now accept historical read-only shared state/trace/frames/planner
JSON under executions. Current publisher hardlink/copy behavior is unchanged. This lets UI, Eval and replay safely read historical
three-link groups for every selected public class; no-follow, mode, inode and
expanded-byte checks remain enforced. Freeze verifies an existing compressed
object before reusing it. Replay continues to reject truly missing/corrupt
non-image objects; frame cleanup and replay retain their existing image-cleaned
rules. Image objects and snapshot manifests/cut pointers are never compressed.

`DERIVE_MAX_BYTES` defaults to 64 MiB; `ATTENTIONBENCH_DERIVE_MAX_BYTES` overrides
it in bytes. Larger state logs skip projection and use zstd. Manual compaction
is ungated. Automatic run-end/startup compaction requires the exact value `1`
for `ATTENTIONBENCH_RUN_COMPACT`. With it enabled, an unset Memory-stores variable
still runs the child; Memory hosts must configure `ATTENTIONBENCH_MEMORY_STORES`
before enabling the gate. Plans/receipts record the absolute store paths consulted
(including attempted unreadable stores; resumed receipts retain prior paths).

Final Round 2 results: the eight requested pytest files passed **271 tests in
81.46 s**; five additional replay/Eval/story/validator/copy files passed **91 tests
in 29.38 s**. Both used the exact requested interpreter, PYTHONPATH and pytest
options. `git diff --check` and AST parsing of all eight changed Python files
also passed. Exact commands and development-run observations are recorded in the
companion [implementation notes](run-storage-impl-notes-20261120.md#round-2).
