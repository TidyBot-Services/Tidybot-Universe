"""Minimal E2E runner: submit a skill main.py to agent_server N times, record
per-trial success / time / plan counts. Caller is responsible for swapping
the curobo service backing the sim (we just record results).

Usage:
    python e2e/run_e2e.py \
        --skill /abs/path/to/main.py \
        --version v1 \
        --trials 3 \
        --label counter-to-sink \
        --agent-url http://localhost:8080 \
        --sim-api http://localhost:5500
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TrialResult:
    label: str
    version: str
    trial_idx: int
    seed: int | None
    success: bool
    elapsed_s: float
    job_id: str | None
    job_status: str | None       # "completed" / "failed" / "timed_out"
    sim_success: bool | None      # /task/success at end
    error: str | None = None
    stdout_tail: str = ""
    n_trajectories: int = 0
    n_graspgen_calls: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _post_json(url: str, body: dict, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(url: str, timeout: float = 10.0) -> dict:
    # urlopen can raise socket.timeout (TimeoutError) which doesn't match
    # URLError on Py 3.10+. Catch broadly — caller treats "_error" as "skip".
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def reset_sim(sim_api: str) -> None:
    """Try POST /reset on sim. Best-effort — returns silently on failure."""
    try:
        _post_json(sim_api + "/reset", {}, timeout=10)
    except Exception as e:
        print(f"  [reset] failed (non-fatal): {e}", flush=True)


def submit_and_wait(agent_url: str, code: str, holder: str,
                    timeout: float = 600.0) -> dict:
    """Submit code to /code/submit, poll /code/jobs/<id> until done.

    Returns the final job dict (with status, stdout, etc).
    """
    sub = _post_json(agent_url + "/code/submit",
                     {"code": code, "holder": holder}, timeout=10)
    job_id = sub.get("job_id")
    if not job_id:
        raise RuntimeError(f"no job_id in submit response: {sub}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _get(agent_url + f"/code/jobs/{job_id}")
        if job.get("_error"):
            time.sleep(1.0)
            continue
        st = job.get("status")
        # agent_server emits "completed" (with exit_code) for finished jobs,
        # not the "succeeded" the API guide hints at — verify before adding more
        # statuses here.
        if st in ("completed", "failed", "timed_out", "cancelled", "error"):
            return job
        time.sleep(1.5)
    return {"status": "wait_timeout", "job_id": job_id}


def run_one(skill_path: Path, label: str, version: str, idx: int,
            agent_url: str, sim_api: str,
            timeout_s: float = 240.0) -> TrialResult:
    code = skill_path.read_text()
    print(f"\n[trial {idx} / {label} / {version}]", flush=True)

    reset_sim(sim_api)
    time.sleep(1.0)  # let sim settle

    t0 = time.time()
    try:
        job = submit_and_wait(agent_url, code,
                              holder=f"eval-{version}-{idx}",
                              timeout=timeout_s)
    except Exception as e:
        return TrialResult(label=label, version=version, trial_idx=idx,
                           seed=None, success=False, elapsed_s=time.time()-t0,
                           job_id=None, job_status=None, sim_success=None,
                           error=f"submit: {type(e).__name__}: {e}")
    elapsed = time.time() - t0

    job_id = job.get("job_id")
    status = job.get("status")
    stdout = job.get("stdout") or ""
    stdout_tail = "\n".join(stdout.splitlines()[-20:]) if stdout else ""

    sim_success = None
    sim_resp = _get(sim_api + "/task/success")
    if "_error" not in sim_resp:
        sim_success = bool(sim_resp.get("success", False))

    success = (status == "completed") and bool(sim_success)
    # Count plan calls + graspgen candidates from stdout — useful even when
    # the trial fails, since they tell us how the planner is performing.
    n_traj = stdout.count("[wb] Executing trajectory") + stdout.count("[wb] Going home")
    n_graspgen_calls = stdout.count("[GraspGen] Generated ")

    r = TrialResult(
        label=label, version=version, trial_idx=idx,
        seed=None,
        success=success,
        elapsed_s=round(elapsed, 2),
        job_id=job_id, job_status=status,
        sim_success=sim_success,
        error=None if status == "completed" else (job.get("error") or status),
        stdout_tail=stdout_tail,
    )
    r.n_trajectories = n_traj            # type: ignore[attr-defined]
    r.n_graspgen_calls = n_graspgen_calls  # type: ignore[attr-defined]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", required=True, help="Absolute path to main.py")
    ap.add_argument("--version", required=True, choices=["v1", "v2"])
    ap.add_argument("--label", required=True,
                    help="Task label, e.g. counter-to-sink")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--agent-url", default="http://localhost:8080")
    ap.add_argument("--sim-api", default="http://localhost:5500")
    ap.add_argument("--timeout", type=float, default=240.0,
                    help="per-trial timeout seconds")
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    skill_path = Path(args.skill).resolve()
    if not skill_path.is_file():
        print(f"skill not found: {skill_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).parent.parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== E2E run: {args.label} / {args.version} / {args.trials} trials ===")
    print(f"  skill: {skill_path}")
    print(f"  agent: {args.agent_url}")
    print(f"  sim:   {args.sim_api}")

    results: list[TrialResult] = []
    for i in range(args.trials):
        r = run_one(skill_path, args.label, args.version, i,
                    args.agent_url, args.sim_api, args.timeout)
        results.append(r)
        print(f"  trial {i}: success={r.success}  elapsed={r.elapsed_s}s  "
              f"job_status={r.job_status}  sim_success={r.sim_success}",
              flush=True)
        if r.stdout_tail and not r.success:
            print(f"  stdout tail:\n    " + r.stdout_tail.replace("\n", "\n    "),
                  flush=True)

    n = len(results)
    n_ok = sum(1 for r in results if r.success)
    times = [r.elapsed_s for r in results if r.success]
    median_t = sorted(times)[len(times)//2] if times else None

    summary = {
        "label": args.label,
        "version": args.version,
        "n_trials": n,
        "n_success": n_ok,
        "success_rate": round(n_ok / n, 3) if n else 0.0,
        "median_elapsed_s": median_t,
        "trial_elapsed_s": [r.elapsed_s for r in results],
    }
    print(f"\n=== summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"e2e_{args.label}_{args.version}_{ts}.json"
    with out_path.open("w") as f:
        json.dump({
            "args": vars(args),
            "summary": summary,
            "trials": [r.to_dict() for r in results],
        }, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {out_path}")
    return 0 if n_ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
