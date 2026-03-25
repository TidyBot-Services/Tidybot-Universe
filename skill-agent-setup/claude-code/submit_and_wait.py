#!/usr/bin/env python3
"""Submit code to the agent server and wait for results.

Usage:
    python submit_and_wait.py <code_file> [--holder NAME] [--timeout SECS] [--no-reset]

Submits the code via POST /code/submit, polls until done, prints JSON result.
Exit code mirrors the submitted code's exit code.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

AGENT_SERVER = "http://localhost:8080"
POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 300  # 5 minutes


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


def main():
    parser = argparse.ArgumentParser(description="Submit code to agent server and wait")
    parser.add_argument("code_file", help="Python file to submit")
    parser.add_argument("--holder", default="submit-and-wait", help="Job holder name")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Timeout in seconds")
    parser.add_argument("--no-reset", action="store_true", help="Skip sim reset before running")
    args = parser.parse_args()

    code = open(args.code_file).read()
    job_id = submit(code, args.holder, reset_env=not args.no_reset)

    job = poll(job_id, args.timeout)

    result = job.get("result", {})
    output = {
        "job_id": job_id,
        "status": job.get("status"),
        "execution_id": job.get("execution_id"),
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }
    print(json.dumps(output, indent=2))
    sys.exit(result.get("exit_code", 1) if job.get("status") == "completed" else 1)


if __name__ == "__main__":
    main()
