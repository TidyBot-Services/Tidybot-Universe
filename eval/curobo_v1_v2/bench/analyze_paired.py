"""Paired analysis of synthetic bench results.

Compare v1 vs v2 only on cases BOTH solved. This corrects for the bias
where v2's median path is 'longer' simply because v2 also solves the
hard cases v1 fails on.

Usage:
    python bench/analyze_paired.py results/planner_bench_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()

    p = Path(args.path)
    with p.open() as f:
        data = json.load(f)

    by_case: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in data["results"]:
        by_case[r["case_id"]][r["version"]] = r

    paired_both_ok: list[tuple[dict, dict]] = []
    v1_only: list[str] = []
    v2_only: list[str] = []
    both_fail: list[str] = []
    for cid, vs in by_case.items():
        v1, v2 = vs.get("v1"), vs.get("v2")
        if not (v1 and v2):
            continue
        if v1["ok"] and v2["ok"]:
            paired_both_ok.append((v1, v2))
        elif v1["ok"] and not v2["ok"]:
            v1_only.append(cid)
        elif v2["ok"] and not v1["ok"]:
            v2_only.append(cid)
        else:
            both_fail.append(cid)

    print(f"=== paired analysis: {p.name} ===")
    print(f"  total cases:     {len(by_case)}")
    print(f"  both succeeded:  {len(paired_both_ok)}")
    print(f"  only v1 OK:      {len(v1_only)}")
    print(f"  only v2 OK:      {len(v2_only)}  <- v2 net wins: {len(v2_only) - len(v1_only):+d}")
    print(f"  both failed:     {len(both_fail)}")

    if not paired_both_ok:
        print("\nNo cases both succeeded — nothing to compare.")
        return

    plan1 = [r1["elapsed_s"] * 1000 for r1, _ in paired_both_ok]
    plan2 = [r2["elapsed_s"] * 1000 for _, r2 in paired_both_ok]
    path1 = [r1["path_length"] for r1, _ in paired_both_ok]
    path2 = [r2["path_length"] for _, r2 in paired_both_ok]
    wp1 = [r1["n_waypoints"] for r1, _ in paired_both_ok]
    wp2 = [r2["n_waypoints"] for _, r2 in paired_both_ok]

    def fmt(v: float | None, suffix: str = "") -> str:
        return f"{v:.3f}{suffix}" if v is not None else "n/a"

    print(f"\n--- on the {len(paired_both_ok)} cases both solved ---")
    print(f"  median plan_ms:  v1={fmt(statistics.median(plan1))}  "
          f"v2={fmt(statistics.median(plan2))}  "
          f"speedup={statistics.median(plan1)/statistics.median(plan2):.2f}x")
    print(f"  median path_len: v1={fmt(statistics.median(path1))}rad  "
          f"v2={fmt(statistics.median(path2))}rad  "
          f"Δ={statistics.median(path2) - statistics.median(path1):+.3f}rad")
    print(f"  median waypoints: v1={int(statistics.median(wp1))}  "
          f"v2={int(statistics.median(wp2))}")

    # Per-case path comparison
    pdiff = [path2[i] - path1[i] for i in range(len(paired_both_ok))]
    n_v2_shorter = sum(1 for d in pdiff if d < -1e-3)
    n_v1_shorter = sum(1 for d in pdiff if d > 1e-3)
    n_tie = len(pdiff) - n_v2_shorter - n_v1_shorter
    print(f"\n  per-case path winner (smaller is better):")
    print(f"    v1 shorter: {n_v1_shorter} / {len(pdiff)}")
    print(f"    v2 shorter: {n_v2_shorter} / {len(pdiff)}")
    print(f"    tie:        {n_tie} / {len(pdiff)}")
    if pdiff:
        print(f"    mean Δ (v2-v1): {statistics.mean(pdiff):+.3f}rad")

    # Per-layout breakdown
    print(f"\n--- per-layout (paired wins only) ---")
    by_layout: dict[str, list[float]] = defaultdict(list)
    by_layout_speed: dict[str, list[float]] = defaultdict(list)
    for r1, r2 in paired_both_ok:
        layout = r1["layout"]
        by_layout[layout].append(r2["path_length"] - r1["path_length"])
        by_layout_speed[layout].append(
            (r1["elapsed_s"] / r2["elapsed_s"]) if r2["elapsed_s"] else float("inf")
        )
    for layout in sorted(by_layout):
        diffs = by_layout[layout]
        speeds = by_layout_speed[layout]
        print(f"  {layout:12s}: n={len(diffs)}  "
              f"path Δ med={statistics.median(diffs):+.3f}rad  "
              f"speedup med={statistics.median(speeds):.2f}x")


if __name__ == "__main__":
    sys.exit(main())
