# cuRobo v1 vs v2 isolation harness

Compares cuRobo 0.7.8 (v1 API) vs 0.8.0 (cuRoboV2 API) **without touching
master branch or the user's prod curobo_service**. Runs both versions side
by side as separate HTTP services on different ports; switching between
them is a single env var (`CUROBO_SERVICE_URL`).

## Setup proven so far

```
v1 service: 127.0.0.1:7050   ← curobo 0.7.8 in conda env maniskill_v0_7_8
                                source: /home/truares/文档/curobo_service
v2 service: 127.0.0.1:7051   ← curobo 0.8.0 in conda env maniskill_v0_8
                                source: /home/truares/文档/curobo_service_v0_8
```

Both expose **identical HTTP routes**; only `planner_core.py` differs
internally to map v1 cuRobo API → v2 cuRobo API. Higher-level callers
(sim's `_plan_with_curobo`, robot_sdk) see the same JSON shape.

Branch: `eval/curobo-v1-v2` (off `curobo_update`). Master untouched.

## How to start the two services

```bash
# Background — logs to results/v{1,2}_service.log
./scripts/start_v1.sh --bg
./scripts/start_v2.sh --bg

# Wait for both ready + warmup (~15-20s each)
~/miniconda3/envs/maniskill_v0_7_8/bin/python scripts/wait_ready.py \
  --url http://127.0.0.1:7050 --warmup
~/miniconda3/envs/maniskill_v0_7_8/bin/python scripts/wait_ready.py \
  --url http://127.0.0.1:7051 --warmup
```

To stop:

```bash
fuser -k 7050/tcp 7051/tcp
```

## Phase 1 — synthetic planner benchmark (done)

Pure planner comparison: same `(start_qpos, goal_pose, scene_cuboids)`
tuples to both services. No sim, no graspgen, no perception. Any
difference is purely cuRobo's contribution.

```bash
python bench/cases.py --n 30 --out bench/cases.json    # generate
python bench/run_planner_bench.py --cases bench/cases.json
# results/planner_bench_<ts>.json with per-case + summary
```

**Layouts** (from `bench/cases.py`):
- `free_space` — gripper-down goals around the arm, ground plane only
- `shelf` — 60cm-wide vertical wall at x=0.45, goals behind it
- `table_under` — horizontal slab at z=0.4, goals above + just under it

**First-run results** (60 cases, see `results/planner_bench_20260504-233652.json`):

| Metric | v1 | v2 | Δ |
|---|---|---|---|
| Success rate | 78.3% (47/60) | 100% (60/60) | +21.7pp |
| Median plan_ms | 1010.5 | 453.1 | 2.23x faster |
| p95 plan_ms | 1061 | 2485 | v2 higher tail |
| Median path_len | 2.58 rad | 3.70 rad | v2 longer (sees harder cases as wins) |
| Median waypoints | 31 | 81 | 2.6x denser |

Per-layout: free_space both 100%; shelf v1=85%/v2=100%; table_under
**v1=50%/v2=100%** — biggest gap on collision-heavy scenes.

> **Caveat on path length**: v2's median path is longer because it
> includes cases v1 couldn't solve. A paired comparison (both succeeded
> on same case) is needed to fairly measure path quality. TODO in next
> revision.

## Phase 2 — E2E with cached grasps (TODO)

Designed but not built yet. Plan:
1. Run a real sim task (e.g., `RoboCasa-Pn-P-Counter-To-Cab-v0`) with v1.
   Record `/perceive` responses and graspgen full ordered candidate lists
   per trial.
2. Re-run with v2, same sim seed, replay cached perception + grasp
   candidates so curobo gets identical inputs.
3. Diff success/failure with `failure_layer` tag per trial.

The shim sits as an HTTP proxy in front of (a) the sim's `/perceive`
endpoint, (b) the graspgen service. No sim source modification.

## Phase 3 — free-run E2E control (TODO)

Same task set, no caching. Diff `(free-run v2) - (cached-replay v2)` =
the "synergy" v2 unlocks by enabling different graspgen choices.

## Phase 4 — failure attribution + report (TODO)

Tag each failed trial with which layer reported failure
(`grasp_empty / grasp_unreachable / plan_no_solution / plan_collision /
exec_collision / success_check_fail`). Output stacked-bar plot per
category.

## Files

```
scripts/
  start_v1.sh         # launches curobo_service v1 on :7050
  start_v2.sh         # launches curobo_service v2 on :7051
  wait_ready.py       # polls /health + optional /warmup
bench/
  _client.py          # tiny stdlib HTTP client + helpers
  cases.py            # deterministic test-case generator
  run_smoke.py        # 1 plan against both, sanity check
  run_planner_bench.py# main synthetic benchmark
  cases.json          # generated cases (committed for reproducibility)
results/              # benchmark output + service logs
records/              # (Phase 2) cached /perceive + graspgen responses
proxy/                # (Phase 2) HTTP recording proxy
e2e/                  # (Phase 2/3) E2E driver scripts
```

## Things that surprised me / future me should know

- The service README advertises `{name, size, pose}` for cuboids. The
  *actual* shape it accepts (and that the sim sends) is
  `{name, center, half_size}`. Don't trust the README on this.
- Setting `CUROBO_DEFAULT_ENV` per service avoids cross-talk in
  `WorldStore`. Bench uses `bench-v1` / `bench-v2` fixed env_ids
  rather than per-case ones — `CUROBO_MAX_ENVS=8` is too tight for
  per-case envs.
- v2 first plan after warmup costs 2-3x median time (caching effect?)
  — exclude or warm-prime before measuring.
- Both services share `cuda:0`. Combined VRAM ~5 GB after warmup, fits
  the 16 GB 5070 Ti easily.

## Side effects of running this harness

When `start_v1.sh` was first run on default port 7000 it killed the
user's prod curobo_service via `fuser -k`. Ports moved to 7050/7051 to
avoid collision. To restart prod service:
`cd /home/truares/文档/curobo_service && python -m curobo_service`.
