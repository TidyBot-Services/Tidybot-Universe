"""Synthetic planner benchmark: same cases through v1 and v2, dump JSON.

This is the version-isolation layer. No sim, no graspgen, no perception —
just (start_qpos, goal_pose, scene_cuboids) tuples replayed against two
HTTP services. Any difference in plan_success / plan_time / path_length
is purely curobo's contribution.

Output: results/planner_bench_<timestamp>.json with per-case results for
both versions, plus an aggregate summary.

Usage:
    # generate cases (once)
    python bench/cases.py --n 30 --out bench/cases.json

    # run benchmark
    python bench/run_planner_bench.py --cases bench/cases.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bench._client import CuroboClient, path_length
from bench.cases import PlanCase, load_cases


@dataclass
class CaseRunResult:
    case_id: str
    layout: str
    version: str           # "v1" or "v2"
    ok: bool
    elapsed_s: float
    n_waypoints: int
    path_length: float
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_case(cli: CuroboClient, case: PlanCase, version: str) -> CaseRunResult:
    # One env per version — push_world overwrites the cuboid set and invalidates
    # the active flag, so the next plan call reloads. Per-case env_ids would
    # overflow CUROBO_MAX_ENVS (default 8) once we have many cases.
    try:
        cli.push_world(case.cuboids)
    except Exception as e:
        return CaseRunResult(
            case_id=case.case_id, layout=case.layout, version=version,
            ok=False, elapsed_s=0.0, n_waypoints=0, path_length=0.0,
            error=f"push_world: {type(e).__name__}: {e}",
        )

    try:
        res = cli.plan_pose(
            current_q=case.start_qpos,
            target_pos=case.target_pos,
            target_quat=case.target_quat,
            mask=case.mask,
        )
    except Exception as e:
        return CaseRunResult(
            case_id=case.case_id, layout=case.layout, version=version,
            ok=False, elapsed_s=0.0, n_waypoints=0, path_length=0.0,
            error=f"plan_pose: {type(e).__name__}: {e}",
        )

    pl = path_length(res.trajectory) if res.trajectory else 0.0
    return CaseRunResult(
        case_id=case.case_id, layout=case.layout, version=version,
        ok=res.ok, elapsed_s=res.elapsed_s, n_waypoints=res.num_waypoints,
        path_length=pl, error=res.error,
    )


def summarize(results: list[CaseRunResult]) -> dict:
    """Per-version, per-layout aggregates."""
    by_key: dict[tuple[str, str], list[CaseRunResult]] = defaultdict(list)
    for r in results:
        by_key[(r.version, r.layout)].append(r)
    by_key_all: dict[str, list[CaseRunResult]] = defaultdict(list)
    for r in results:
        by_key_all[r.version].append(r)

    summary: dict = {"by_layout": {}, "overall": {}}

    def stats(group: list[CaseRunResult]) -> dict:
        n = len(group)
        ok = [r for r in group if r.ok]
        n_ok = len(ok)
        if n_ok == 0:
            return {"n": n, "n_ok": 0, "success_rate": 0.0,
                    "median_plan_ms": None, "p95_plan_ms": None,
                    "median_path_len": None, "median_waypoints": None,
                    "errors": _err_breakdown(group)}
        plan_ms = sorted(r.elapsed_s * 1000 for r in ok)
        path_lens = sorted(r.path_length for r in ok)
        wps = sorted(r.n_waypoints for r in ok)
        return {
            "n": n,
            "n_ok": n_ok,
            "success_rate": round(n_ok / n, 3),
            "median_plan_ms": round(statistics.median(plan_ms), 1),
            "p95_plan_ms": round(plan_ms[max(0, int(0.95 * len(plan_ms)) - 1)], 1),
            "median_path_len": round(statistics.median(path_lens), 4),
            "median_waypoints": int(statistics.median(wps)),
            "errors": _err_breakdown(group),
        }

    for (version, layout), group in sorted(by_key.items()):
        summary["by_layout"].setdefault(layout, {})[version] = stats(group)
    for version, group in sorted(by_key_all.items()):
        summary["overall"][version] = stats(group)
    return summary


def _err_breakdown(group: list[CaseRunResult]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in group:
        if not r.ok:
            key = (r.error or "unknown").split(":")[0][:60]
            out[key] += 1
    return dict(out)


def print_summary(summary: dict) -> None:
    print("\n=== overall ===")
    for v, s in summary["overall"].items():
        print(f"  {v}: {s['n_ok']}/{s['n']} ({100*s['success_rate']:.1f}%)  "
              f"median_plan={s['median_plan_ms']}ms  p95={s['p95_plan_ms']}ms  "
              f"median_path={s['median_path_len']}rad  "
              f"median_wps={s['median_waypoints']}")

    print("\n=== by layout ===")
    for layout in sorted(summary["by_layout"].keys()):
        print(f"  -- {layout} --")
        for v in sorted(summary["by_layout"][layout].keys()):
            s = summary["by_layout"][layout][v]
            print(f"    {v}: {s['n_ok']}/{s['n']} ({100*s['success_rate']:.1f}%)  "
                  f"plan={s['median_plan_ms']}ms  path={s['median_path_len']}rad")

    # Compute v2 vs v1 deltas where we have both
    print("\n=== v2 vs v1 delta (overall) ===")
    o = summary["overall"]
    if "v1" in o and "v2" in o:
        s1, s2 = o["v1"], o["v2"]
        if s1["success_rate"] is not None and s2["success_rate"] is not None:
            print(f"  success_rate:  v1={s1['success_rate']}  v2={s2['success_rate']}  "
                  f"Δ={s2['success_rate'] - s1['success_rate']:+.3f}")
        if s1["median_plan_ms"] and s2["median_plan_ms"]:
            speedup = s1["median_plan_ms"] / s2["median_plan_ms"]
            print(f"  median_plan:   v1={s1['median_plan_ms']}ms  v2={s2['median_plan_ms']}ms  "
                  f"speedup={speedup:.2f}x")
        if s1["median_path_len"] and s2["median_path_len"]:
            print(f"  median_path:   v1={s1['median_path_len']}rad  v2={s2['median_path_len']}rad  "
                  f"Δ={s2['median_path_len'] - s1['median_path_len']:+.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="bench/cases.json")
    ap.add_argument("--v1-url", default="http://127.0.0.1:7050")
    ap.add_argument("--v2-url", default="http://127.0.0.1:7051")
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap number of cases (0 = all)")
    args = ap.parse_args()

    root = Path(__file__).parent.parent
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = root / cases_path
    cases = load_cases(cases_path)
    if args.limit:
        cases = cases[: args.limit]
    print(f"loaded {len(cases)} cases from {cases_path}")

    cli_v1 = CuroboClient(url=args.v1_url, env_id="bench-v1")
    cli_v2 = CuroboClient(url=args.v2_url, env_id="bench-v2")

    # Confirm both up + warmed
    for label, cli in [("v1", cli_v1), ("v2", cli_v2)]:
        h = cli.health()
        if not h.get("warmed_up"):
            print(f"[warn] {label} not warmed; sending /warmup (~30s)")
            cli.warmup()

    results: list[CaseRunResult] = []
    t0 = time.time()
    for i, c in enumerate(cases):
        r1 = run_case(cli_v1, c, "v1")
        r2 = run_case(cli_v2, c, "v2")
        results.append(r1)
        results.append(r2)
        marker_v1 = "✓" if r1.ok else "✗"
        marker_v2 = "✓" if r2.ok else "✗"
        if (i + 1) % 5 == 0 or i == len(cases) - 1:
            print(f"  [{i+1}/{len(cases)}] {c.case_id}  v1{marker_v1}{r1.elapsed_s*1000:.0f}ms"
                  f"  v2{marker_v2}{r2.elapsed_s*1000:.0f}ms")

    elapsed = time.time() - t0

    summary = summarize(results)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"planner_bench_{ts}.json"
    with out_path.open("w") as f:
        json.dump({
            "timestamp": ts,
            "elapsed_s": elapsed,
            "v1_url": args.v1_url,
            "v2_url": args.v2_url,
            "n_cases": len(cases),
            "results": [r.to_dict() for r in results],
            "summary": summary,
        }, f, indent=2)
    print(f"\nwrote {out_path}  ({elapsed:.1f}s total)")

    print_summary(summary)


if __name__ == "__main__":
    sys.exit(main())
