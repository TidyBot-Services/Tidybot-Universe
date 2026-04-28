"""Harness reliability benchmark — OpenClaw + Ollama llama3.1:8b-ctx32k.

Runs the same Part A prompt N times through the `tidybot-poc` OpenClaw agent,
then reports success rate and timing. Mirrors the intent of
opencode_poc/bench_harness.py (same model family, same prompt shape) so the
two PoCs can be compared directly.

Success = exit 0 AND stop_reason=="stop" AND >=1 clean tool call.

Usage:
    python bench_harness.py --runs 10
    python bench_harness.py --runs 20 --prompt "..."
"""
import argparse
import statistics
import sys
import time

from demo_openclaw import DEFAULT_AGENT, PART_A_PROMPT, run_openclaw


def bench(runs: int, agent: str, prompt: str, timeout: int):
    passed = 0
    failed = 0
    times = []
    fail_reasons = []

    for i in range(1, runs + 1):
        t0 = time.time()
        r = run_openclaw(agent, prompt, timeout=timeout)
        times.append(time.time() - t0)

        ok = (
            r.get("returncode") == 0
            and r.get("stop_reason") == "stop"
            and r.get("tool_calls", 0) >= 1
            and r.get("tool_failures", 0) == 0
        )
        if ok:
            passed += 1
            label = "PASS"
        else:
            failed += 1
            label = "FAIL"
            reason = r.get("stop_reason") or r.get("error") or "?"
            fail_reasons.append(f"run{i}: {reason}")

        text = (r.get("assistant_text") or "").replace("\n", " ")[:80]
        print(f"  run {i:2d}/{runs}: {label}  "
              f"{r['elapsed_s']:5.1f}s  "
              f"tools={r.get('tool_calls', 0)}"
              f"(fail={r.get('tool_failures', 0)})  "
              f"→ {text}")

    total = passed + failed
    rate = 100.0 * passed / total if total else 0
    print()
    print("=" * 60)
    print(f"  runs:        {total}")
    print(f"  passed:      {passed}")
    print(f"  failed:      {failed}")
    print(f"  success:     {rate:.1f}%")
    if times:
        print(f"  elapsed:     "
              f"mean={statistics.mean(times):.1f}s  "
              f"median={statistics.median(times):.1f}s  "
              f"min={min(times):.1f}s  max={max(times):.1f}s")
    if fail_reasons:
        print(f"  failures:")
        for f in fail_reasons:
            print(f"    - {f}")
    return 0 if failed == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--agent", default=DEFAULT_AGENT)
    ap.add_argument("--prompt", default=PART_A_PROMPT)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    print(f"[bench_harness] {args.runs} runs against agent={args.agent}")
    print(f"  prompt: {args.prompt[:80]}{'...' if len(args.prompt) > 80 else ''}")
    print()
    return bench(args.runs, args.agent, args.prompt, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
