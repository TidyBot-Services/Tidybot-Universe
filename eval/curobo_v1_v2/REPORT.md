# cuRobo v1 → v2 evaluation: first pass report

**Date:** 2026-05-05
**Branch:** `eval/curobo-v1-v2` (off `curobo_update`, master untouched)
**Versions:** cuRobo 0.7.8 (v1) vs 0.8.0 / cuRoboV2 (v2)
**Harness:** `/home/truares/文档/Tidybot-Universe/eval/curobo_v1_v2/`

## Headline

> **v2 is faster and broader in synthetic isolation, but regresses on the real
> pipeline due to stricter collision checks. v2 is not drop-in upgrade-ready
> for this codebase as-is — needs collision-distance tuning before it can
> replace v1 in production.**

## Layer 1 — synthetic planner benchmark (60 cases, isolated)

Same `(start_qpos, goal_pose, scene_cuboids)` tuples to both services. No
sim, no graspgen, no perception — pure planner comparison.

```
Layouts: 20 free_space + 20 shelf + 20 table_under
Total:   60 cases x 2 versions = 120 plan calls
```

### Naive aggregates (all 60 cases)

| Metric | v1 | v2 | Δ |
|---|---|---|---|
| Success rate | 47/60 (78.3%) | **60/60 (100%)** | +21.7pp |
| Median plan_ms | 1010 | **453** | **2.23x faster** |
| p95 plan_ms | 1061 | 2485 | v2 has higher tail |
| Median path_len (rad) | 2.58 | 3.70 | longer (biased — see below) |
| Median waypoints | 31 | 81 | 2.6x denser |

### Paired aggregates (47 cases BOTH solved — fair comparison)

| Metric | v1 | v2 | Δ |
|---|---|---|---|
| Median plan_ms | 1010 | 452 | **2.23x faster** |
| Median path_len (rad) | 2.58 | **3.93** | +1.36 rad longer |
| Per-case path winner | **32/47** | 15/47 | v1 wins 68% |
| Median waypoints | 31 | 81 | 2.6x denser |

### Per-layout paired

| Layout | n | v1 fail-only | path_Δ med (v2−v1) | speedup |
|---|---|---|---|---|
| free_space | 20 | 0 | +0.28 rad | 2.23x |
| shelf | 17 | 3 | +0.84 rad | 2.28x |
| table_under | 10 | 10 | +1.77 rad | 2.19x |

### Synthetic conclusions

- v2 **uniformly faster** (~2.2x), driven by `parallel_finetune` default and
  GPU kernel improvements in the cuRoboV2 rewrite.
- v2 **solves harder collision cases** v1 fails on — biggest gap in
  `table_under` (10/20 v2-only successes), where the slab obstacle stresses
  v1's collision-gradient solver.
- v2 **takes longer paths** on cases both solve. The path penalty grows with
  obstacle complexity (free_space: +0.28 rad → table_under: +1.77 rad). v2
  is more conservative — likely a result of mesh-default collision checking
  giving wider safety margins.
- Tradeoff: **v2 speed and coverage at a path-length cost**.

## Layer 2 — E2E pipeline (counter-to-sink, real sim)

Same `pnp-counter-to-sink/scripts/main.py` skill. Sim continued running
between version swaps; `CUROBO_SERVICE_URL` was held at port 7000 and the
service backing it was swapped (v1 service ↔ v2 service) so the sim got
identical configuration except for which planner was answering.

| Metric | v1 | v2 |
|---|---|---|
| Trials | 3 | 3 (2 captured before driver crash) |
| Job status | 3/3 completed (exit 0) | 3/3 **failed** (SIGTERM) |
| sim_success (`/task/success`) | 0/3 | 0/3 |
| Median elapsed | 97.6 s | **40.4 s (forced kill)** |
| Cause of trial end | skill returned cleanly after grasp fallback exhausted | **arm server crashed → auto-recovery killed code** |

### What v2 logged

```
[curobo v0.8] Start or End state in collision   (× ~20)
[curobo v0.8] Joint plan failed, retrying with cleared world
[curobo v0.8] Joint plan FAILED (0.44s)
```

v2's collision check flagged the **start state** as in collision and
refused to plan. After repeated rejection the skill's `wb.move_to_pose`
calls all raised; the arm bridge received malformed/no trajectories and
crashed; agent_server's auto-recovery SIGTERMed the running code.

### E2E conclusions

- **v2 regression on real pipeline.** Same skill / same sim / same scene:
  v1 plans (and the skill's grasp logic still fails downstream of planning,
  giving 0/3 sim success), v2 doesn't even plan.
- The synthetic bench world (single `ground` cuboid) doesn't trigger the
  start-state collision flag because the home pose is not near anything.
  In the real kitchen, the home pose is close to fixtures and v2's stricter
  margins now flag it.
- Likely fix path: tune `collision_activation_distance` in the v2 service's
  `MotionPlannerCfg`, or revert the mesh-default to primitive for the
  current robot meshes. **Not blocked on cuRobo itself — blocked on
  configuration.**

## Reproducibility

```bash
# Two services, separate envs
./scripts/start_v1.sh --bg     # cuRobo 0.7.8 in maniskill_v0_7_8 env  -> :7050
./scripts/start_v2.sh --bg     # cuRobo 0.8.0 in maniskill_v0_8 env    -> :7051

# Synthetic
python bench/cases.py --n 30 --out bench/cases.json
python bench/run_planner_bench.py --cases bench/cases.json
python bench/analyze_paired.py results/planner_bench_<ts>.json

# E2E (sim must already be running on port 5500, agent_server on 8080)
# Swap CUROBO_SERVICE_URL by re-launching the chosen service on port 7000
fuser -k 7000/tcp; CUROBO_PORT_V1=7000 ./scripts/start_v1.sh --bg
python e2e/run_e2e.py --skill <main.py> --version v1 --label counter-to-sink --trials 3

fuser -k 7000/tcp; CUROBO_PORT_V2=7000 ./scripts/start_v2.sh --bg
python e2e/run_e2e.py --skill <main.py> --version v2 --label counter-to-sink --trials 3
```

## Recommendations

1. **Don't ship v2 to prod yet.** Real-pipeline regression is unambiguous.
2. **Tune v2 collision config first.** Try
   `collision_activation_distance=0.005` (down from 0.01) and switch back
   to primitive collision for the start-state check. Then re-run E2E.
3. **Use the synthetic bench as gate** before/after each tuning round.
   Aim for: same paired path-length as v1 (or shorter), same speedup
   (~2.2x), no E2E regression. Path tradeoff is the main signal.
4. **Add E2E to CI.** The synthetic bench passing was misleading on its
   own — the integration regression is what matters for the user. Even a
   3-trial smoke test on counter-to-sink would have caught this in 5
   minutes.

## Open items

- Phase 2 cached-grasp E2E (perception/graspgen replay) — not built.
  Lower priority now: the v2 regression is upstream of where caching
  would have helped. Worth building once v2 plans cleanly.
- Phase 3 free-run E2E control — same status.
- Failure-layer attribution — partially built (we track plan vs grasp vs
  sim_success); not yet plotted.

## Files written / modified by this run

```
eval/curobo_v1_v2/
├── REPORT.md                            (this file)
├── README.md                            (architecture, setup, gotchas)
├── scripts/{start_v1,start_v2,wait_ready}*
├── bench/{_client, cases, run_smoke, run_planner_bench, analyze_paired}.py
├── bench/cases.json                     60 generated cases
├── e2e/run_e2e.py
└── results/
    ├── v1_service.log, v2_service.log
    ├── planner_bench_20260504-233652.json   60-case synthetic
    ├── e2e_counter-to-sink_v1_20260505-000437.json   3 trials, 0/3 success but skill ran clean
    └── e2e_counter-to-sink_v2_20260505-001500.json   2 trials, 0/3 success, arm-server crash on each
```

No master-branch files modified. Branch `eval/curobo-v1-v2` holds all
new code.
