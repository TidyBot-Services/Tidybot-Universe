#!/usr/bin/env python3
"""Submit code to the agent server and wait for results.

Usage:
    python submit_and_wait.py <code_file> [--holder dev:<skill>] [--timeout SECS] [--no-reset] [--no-eval]

Submits the code via POST /code/submit, polls until done.
If an orchestrator is running (port 8766), triggers an evaluator agent that reviews
camera recordings and robot behavior, then returns {passed, feedback, exit_code}.
Otherwise falls back to raw {stdout, stderr, exit_code}.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

AGENT_SERVER = "http://localhost:8080"
ORCHESTRATOR = "http://localhost:8766"
POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 300  # 5 minutes
EVAL_TIMEOUT = 600     # 10 minutes for evaluator


def submit(code: str, holder: str, reset_env: bool) -> str:
    """Submit code and return job_id."""
    data = json.dumps({
        "code": code,
        "holder": holder,
        "reset_env": reset_env,
    }).encode()
    req = urllib.request.Request(
        f"{AGENT_SERVER}/code/submit",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["job_id"]


def poll(job_id: str, timeout: float) -> dict:
    """Poll until job completes or timeout."""
    url = f"{AGENT_SERVER}/code/jobs/{job_id}"
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"status": "timeout", "error": f"Timed out after {timeout}s"}
        try:
            job = json.loads(urllib.request.urlopen(url).read())
        except urllib.error.URLError as e:
            print(f"Agent server unreachable: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL)
            continue
        if job.get("status") in ("completed", "failed"):
            return job
        time.sleep(POLL_INTERVAL)


def notify_job_done(skill: str, execution_id: str) -> bool:
    """Notify orchestrator that a job finished, triggering evaluator. Returns True if accepted."""
    data = json.dumps({"skill": skill, "execution_id": execution_id}).encode()
    req = urllib.request.Request(
        f"{ORCHESTRATOR}/job-done",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
        return True
    except urllib.error.URLError:
        return False


def poll_eval_result(skill: str, timeout: float) -> dict | None:
    """Poll orchestrator for evaluator result. Returns None if unavailable."""
    url = f"{ORCHESTRATOR}/eval-result/{skill}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = json.loads(urllib.request.urlopen(url).read())
            if resp.get("status") == "complete":
                return resp
            if resp.get("status") == "not_found":
                return None
        except urllib.error.URLError:
            return None
        time.sleep(POLL_INTERVAL)
    return {"passed": True, "feedback": "Evaluator timed out."}


def main():
    parser = argparse.ArgumentParser(description="Submit code to agent server and wait")
    parser.add_argument("code_file", help="Python file to submit")
    parser.add_argument("--holder", default="submit-and-wait",
                        help="Job holder name. Use 'dev:<skill>' format for orchestrator eval, or any name with --no-eval for debug runs")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout in seconds")
    parser.add_argument("--no-reset", action="store_true", help="Skip sim reset before running")
    parser.add_argument("--no-eval", action="store_true", help="Skip evaluator — return raw stdout/stderr")
    args = parser.parse_args()

    code = open(args.code_file).read()
    job_id = submit(code, args.holder, reset_env=not args.no_reset)

    job = poll(job_id, args.timeout)

    result = job.get("result", {})
    execution_id = job.get("execution_id", "")
    exit_code = result.get("exit_code", 1) if job.get("status") == "completed" else 1

    # Derive skill name from holder (format: "dev:<skill_name>")
    skill = None
    if ":" in args.holder:
        skill = args.holder.split(":", 1)[1]

    # Job failed to complete — report error without raw output
    if job.get("status") != "completed":
        output = {
            "job_id": job_id,
            "exit_code": exit_code,
            "passed": False,
            "feedback": f"Job {job.get('status', 'unknown')}: code did not complete successfully.",
        }
        print(json.dumps(output, indent=2))
        sys.exit(exit_code)

    # --no-eval: return raw stdout/stderr for exploratory/debug runs
    if args.no_eval:
        output = {
            "job_id": job_id,
            "execution_id": execution_id,
            "exit_code": exit_code,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
        print(json.dumps(output, indent=2))
        sys.exit(exit_code)

    # Notify orchestrator and wait for evaluator verdict
    if not skill:
        print(json.dumps({"error": "No skill name — use --holder dev:<skill_name>"}), file=sys.stderr)
        sys.exit(1)

    if not notify_job_done(skill, execution_id):
        # Orchestrator not running — fall back to raw output
        output = {
            "job_id": job_id,
            "execution_id": execution_id,
            "exit_code": exit_code,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
        print(json.dumps(output, indent=2))
        sys.exit(exit_code)

    eval_result = poll_eval_result(skill, timeout=EVAL_TIMEOUT)
    if not eval_result:
        # Evaluator didn't return — fall back to raw output
        output = {
            "job_id": job_id,
            "execution_id": execution_id,
            "exit_code": exit_code,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }
        print(json.dumps(output, indent=2))
        sys.exit(exit_code)

    output = {
        "job_id": job_id,
        "execution_id": execution_id,
        "exit_code": exit_code,
        "passed": eval_result.get("passed", True),
        "feedback": eval_result.get("feedback", ""),
    }
    print(json.dumps(output, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
