#!/usr/bin/env python3
"""Summarize a results.csv produced by run_all_tasks.sh.

Prints aggregate pass rate + breakdown by failure reason. Surfaces a few
representative feedback excerpts per failure mode so a human can diagnose
fast without opening the CSV.
"""

import argparse
import collections
import csv
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("results", type=Path)
    p.add_argument("--show-feedback", type=int, default=2,
                   help="Show this many feedback excerpts per failure reason (0 to skip)")
    args = p.parse_args()

    if not args.results.exists():
        print(f"results file not found: {args.results}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(args.results.open()))
    if not rows:
        print("(empty results file)")
        return 0

    total = len(rows)
    statuses = collections.Counter()
    by_reason = collections.defaultdict(list)
    iter_used = []
    walltime = []

    for r in rows:
        st = r.get("status", "")
        statuses[st] += 1
        if st != "done":
            reason = r.get("fail_reason") or st or "unknown"
            by_reason[reason].append(r)
        try:
            iter_used.append(int(r.get("iter_count", 0) or 0))
            walltime.append(float(r.get("wall_time_s", 0) or 0))
        except ValueError:
            pass

    passed = statuses.get("done", 0)
    pct = (100.0 * passed / total) if total else 0.0

    print()
    print("=" * 64)
    print(f"  {args.results}")
    print("=" * 64)
    print(f"  Total tasks:     {total}")
    print(f"  PASSED:          {passed}/{total}  ({pct:.1f}%)")
    print(f"  FAILED:          {total - passed}/{total}")
    if iter_used:
        print(f"  iters / task:    avg={sum(iter_used)/len(iter_used):.1f}  "
              f"max={max(iter_used)}  min={min(iter_used)}")
    if walltime:
        print(f"  walltime / task: avg={sum(walltime)/len(walltime):.0f}s  "
              f"max={max(walltime):.0f}s")

    if by_reason:
        print()
        print("Failure breakdown:")
        for reason, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
            print(f"  {reason:25s} {len(items)}")

        if args.show_feedback > 0:
            print()
            print("Representative feedback (first {} per reason):".format(args.show_feedback))
            for reason, items in sorted(by_reason.items()):
                print(f"\n  ── {reason} ──")
                for r in items[: args.show_feedback]:
                    fb = (r.get("last_feedback") or "").strip()
                    if not fb:
                        fb = "(no feedback recorded)"
                    short = fb[:300] + ("…" if len(fb) > 300 else "")
                    print(f"    [{r.get('task_env','?')}] iters={r.get('iter_count','?')} walltime={r.get('wall_time_s','?')}s")
                    print(f"      {short}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
