# Seed 102 host variants: implementation verification (2026-11-09)

Read the complete plan and `git show f4f6502:docs/REPORT-seed102-qifei-20261109.md` before implementation.
The qifei fingerprint is registered in a separate pinned table. Matching remains exact normalized SHA-256 equality.
An AST comparison confirmed that `registered_contract_ref`, `load_contract`, and `seed_entry` are unchanged.
All previously tracked files in `openarm365_dev_v2/scene_contracts/` match HEAD byte for byte.
The new variant table is 979 bytes and its pinned SHA-256 is
`9fa774f30b523fac866e74628dd081244d3de06a004c97faffbfac2ecbe9fb65`.

No real agents, planners, policies, simulator probes, remote operations, or commits were started.
No `sim.lock` was accessed. Probe/tool tests use inert environments and mocked subprocesses.
The initial implementation paired the origin-test helper's synthetic registered contract with a pinned empty
variant table; the Follow-up below supersedes that fixture adjustment.

## Results

| Invocation | Passed | Failed | Setup errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| Required exact command | 71 | 10 | 0 | 0 |
| Required files plus reproducibility, synthetic R4 fixture, Numba JIT disabled | 87 | 0 | 0 | 0 |
| Discovered harness files, before synthetic origin-fixture adjustment | 616 | 62 | 12 | 1 |
| Discovered harness files, final | 618 | 60 | 12 | 1 |
| Four deployment-dependent harness files, synthetic R4 fixture | 74 | 1 | 0 | 1 |
| Five diagnostic cases with original HEAD scene-contract implementation loaded in memory | 2 | 3 | 0 | 0 |
| Two focused deployment-failure diagnostics | 0 | 2 | 0 | 0 |
| Final phase-2 origin tests | 10 | 1 | 0 | 0 |
| Additional shared-fixture consumers | 73 | 0 | 0 | 0 |

The required command's nine deployment failures attempted to materialize a dev snapshot under a read-only
external directory. Its other failure was Numba trying to create an upstream cache. The isolated rerun uses the
repository's existing authenticated synthetic R4 deployment fixture and `NUMBA_DISABLE_JIT=1`; no production
check is weakened or edited. All new variant/tool test cases passed in both runs.

Final harness failures comprise 53 failed tests and five setup errors involving the external deployment snapshot,
two historical copied-evidence failures (`context_unavailable:DevConflict` / absent proposal), five obsolete
UI-handler fixtures lacking `Handler.server`, and seven socket-binding setup errors (`PermissionError`).
The historical-evidence failures and a representative UI failure also occur with the original implementation.
The initial two synthetic origin regressions were corrected in `agent_origin_fakes.py` and pass in the final run.
The harness skip is optional recorded evidence unavailable on this machine. None of the discovered test files
requires a live simulator or GPU planner; no such run was attempted.

An additional command including `test_dev_agent_memory_phase4.py` was interrupted with Ctrl-C (exit 130)
after its first mocked agent-workflow test stalled; it completed no test cases. The other two files from that
command were then run separately. This was a scripted fake session, with native execution and model sessions
replaced by test doubles.

## Exact test commands

Run from `/home/truares/桌面/AttentionBench-Workspace/development/wt-seed102`.

Required command (17.54 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider openarm365_dev_v2/tests/test_scene_contract.py openarm365_dev_v2/tests/test_scene_gate.py openarm365_dev_v2/tests/test_contract.py
```

Test discovery:

```bash
rg -n 'scene_contract|registered_contract_ref' benchmarks/attention_harness/tests -g 'test_*.py'
rg -l 'scene_contract|registered_contract_ref' benchmarks/attention_harness/tests -g 'test_*.py'
```

Initial discovered harness run (177.72 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider $(rg -l 'scene_contract|registered_contract_ref' benchmarks/attention_harness/tests -g 'test_*.py' | sort) > /tmp/seed102-harness-tests.log 2>&1
```

Final discovered harness run (178.59 seconds; temporary worktree log removed after inspection):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider --tb=short $(rg -l 'scene_contract|registered_contract_ref' benchmarks/attention_harness/tests -g 'test_*.py' | sort) > .seed102-harness-final.log 2>&1
```

Isolated required files plus reproducibility (18.36 seconds, three upstream deprecation warnings):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service NUMBA_DISABLE_JIT=1 /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python - <<'PY'
import pytest
import types
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
plugin=types.ModuleType('seed102_offline_fixtures')
plugin.base=base
plugin.r4_deployment=r4_deployment
@pytest.fixture(autouse=True)
def offline_snapshot(r4_deployment):
    pass
plugin.offline_snapshot=offline_snapshot
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',
    'openarm365_dev_v2/tests/test_scene_contract.py',
    'openarm365_dev_v2/tests/test_scene_gate.py',
    'openarm365_dev_v2/tests/test_scene_reproducibility.py',
    'openarm365_dev_v2/tests/test_contract.py'],plugins=[plugin]))
PY
```

Isolated deployment-dependent harness files (27.66 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python - <<'PY'
import pytest
import types
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
plugin=types.ModuleType('seed102_offline_fixtures')
plugin.base=base
plugin.r4_deployment=r4_deployment
@pytest.fixture(autouse=True)
def offline_snapshot(r4_deployment):
    pass
plugin.offline_snapshot=offline_snapshot
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--tb=short',
    'benchmarks/attention_harness/tests/test_dev_agent_advisor_model.py',
    'benchmarks/attention_harness/tests/test_memory_layers.py',
    'benchmarks/attention_harness/tests/test_memory_provisional.py',
    'benchmarks/attention_harness/tests/test_memory_run_notes.py'],plugins=[plugin]))
PY
```

Original-implementation comparison (6.14 seconds; no tracked file was reverted or rewritten):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python - <<'PY'
import pytest
import subprocess
from openarm365_dev_v2 import scene_contract
source=subprocess.check_output(['git','show','HEAD:openarm365_dev_v2/scene_contract.py'],text=True)
exec(compile(source,scene_contract.__file__,'exec'),scene_contract.__dict__)
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--tb=short',
    'benchmarks/attention_harness/tests/test_dev_agent_memory_phase1.py::test_real_data_offline_copy',
    'benchmarks/attention_harness/tests/test_dev_agent_memory_phase2.py::test_agent_origin_fake_e2e',
    'benchmarks/attention_harness/tests/test_dev_agent_memory_phase2.py::test_agent_replay_receipt_tamper_refuses',
    'benchmarks/attention_harness/tests/test_dev_agent_memory_phase2.py::test_real_data_offline_origin_copy',
    'benchmarks/attention_harness/tests/test_memory_monitor.py::test_route_reports_passed_and_unknown_attempt_outcomes']))
PY
```

Focused deployment diagnostics (1.67 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider --tb=short benchmarks/attention_harness/tests/test_dev_agent_advisor_model.py::test_created_agent_config_with_sol_advisor_passes_run_time_validation benchmarks/attention_harness/tests/test_memory_layers.py::test_daily_default_and_manifest_switch
```

Final origin-fixture verification (7.20 seconds; remaining failure also reproduced with HEAD):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider --tb=short benchmarks/attention_harness/tests/test_dev_agent_memory_phase2.py
```

Interrupted additional suite (exit 130, no completed cases):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider --tb=short benchmarks/attention_harness/tests/test_dev_agent_memory_phase4.py benchmarks/attention_harness/tests/test_dev_v2_memory_import_evidence.py benchmarks/attention_harness/tests/test_memory_origin_registration_retry.py
```

Additional fixture consumers (277.75 seconds):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider --tb=short benchmarks/attention_harness/tests/test_dev_v2_memory_import_evidence.py benchmarks/attention_harness/tests/test_memory_origin_registration_retry.py
```

Additional checks passed: `git diff --check`, AST parsing of all changed Python files, unchanged AST bodies of
the three protected functions, unchanged bytes of existing contract JSON files, the registration CLI's `--help`,
and a mocked check of the fresh-process command/environment (hash seed 0, EGL, no LD preload/library overrides).

## Follow-up

Host variants now require both equality with `registered_contract_ref()` and the real `REGISTERED_SHA256`.
The variant-file digest check remains intact, and the table's base digest must equal `REGISTERED_SHA256`.
Synthetic registered contracts return only their reference fingerprint without reading the variant file.
`agent_origin_fakes.py` was restored byte for byte from HEAD (`git checkout` could not create its index lock
in the read-only Git metadata directory, so restoration used `git show HEAD:...`).

The worker catches exceptions from diagnostic `host_info()` and stores `{'error': repr(exc)}` under
`condition['host']`. Unit coverage executes the worker's actual diagnostic branch without starting it,
covering successful metadata, an exception, and an absent condition. Contract tests cover a synthetic
registration with an absent variant file and a mismatched table base against the real registered reference.

Requested follow-up command (5.31 seconds): **32 passed, 7 failed, 0 setup errors, 0 skipped**, with three
upstream deprecation warnings. All follow-up cases passed. The seven failures attempt to create a deployment
snapshot lock under the read-only external `openarm-dev-integration/dev-local` directory.

```bash
PYTHONDONTWRITEBYTECODE=1 NUMBA_DISABLE_JIT=1 PYTHONPATH=$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m pytest -q -p no:cacheprovider openarm365_dev_v2/tests/test_scene_contract.py
```

`git diff --check` passed. No simulators were started and no commits were made.

## Claude verification outside the Codex sandbox (2026-11-09)

The Codex counts above were taken inside a read-only sandbox (deployment snapshot dirs, sockets). Re-run unsandboxed:

- Test set: every `benchmarks/attention_harness/tests/test_*.py` that mentions `scene_contract|registered_contract_ref`
  plus `openarm365_dev_v2/tests/test_scene_{contract,gate,reproducibility}.py` and `test_contract.py`
  (29 files; `test_dev_agent_memory_phase4.py` excluded on both sides because its mocked workflow stalls).
- Baseline worktree at `ca72c4c` (this branch's base): **747 passed, 7 failed, 1 skipped**.
- This branch: **774 passed, 7 failed, 1 skipped**. The 7 failures are the same tests on both sides
  (`test_dev_agent_memory_phase1::test_real_data_offline_copy`, `phase2::test_real_data_offline_origin_copy`,
  four `test_memory_monitor` route tests, `test_memory_run_notes::test_monitor_and_kill_switch_endpoint_auth`); none new.
- Real reset-only check, truares (i5-14600KF, glibc 2.35): `scene_fingerprint_check.py` → 101/102/103 `reference`,
  `matches_registered: True`, `frame_rendered: True` (15 s).
- Real reset-only check, qifei (Ryzen 9 9950X, glibc 2.39), branch files overlaid on a scratch copy of the qifei repo
  (`~/scratch-seed102-20261109/repo`; the qifei workspace was not edited): 101/103 `reference`,
  102 `zen5-glibc239-20261109`, `matches_registered: True`; `validate()` for 102 → `verified`, contract ref still `bb28cd3e…`.
- `register_host_variant.py` on qifei (2 processes × 2 resets, `--id dryrun`) printed only the 102 entry, wrote nothing.
- qifei stability evidence for the registered entry: 5 fresh processes × 4 resets per seed, one fingerprint per seed
  (101 `90b0bf96`, 102 `149932e2`, 103 `d7e16c54`).
