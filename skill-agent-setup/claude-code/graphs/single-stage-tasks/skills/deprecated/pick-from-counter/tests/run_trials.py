#!/usr/bin/env python3
"""Auto-generated test for task root skill: pick-from-counter
Task: RoboCasa-Pn-P-Counter-To-Cab-v0

Runs the skill's main.py via the agent server, then checks success
via the sim's /task/success endpoint (port 5500).
"""
import json
import time
import urllib.request
import urllib.error

AGENT_SERVER = "http://localhost:8080"
SIM_API = "http://localhost:5500"
NUM_TRIALS = 3


def submit_code(code: str) -> str:
    """Submit code to agent server, return job_id."""
    data = json.dumps({"code": code}).encode()
    req = urllib.request.Request(
        f"{AGENT_SERVER}/code/submit",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["job_id"]


def wait_for_job(job_id: str, timeout: int = 300) -> dict:
    """Poll until job completes."""
    url = f"{AGENT_SERVER}/code/jobs/{job_id}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            job = json.loads(urllib.request.urlopen(url).read())
            if job["status"] in ("completed", "failed"):
                return job
        except urllib.error.URLError:
            pass
        time.sleep(2)
    return {"status": "timeout"}


def check_success() -> bool:
    """Check task success via sim endpoint."""
    try:
        resp = json.loads(urllib.request.urlopen(f"{SIM_API}/task/success").read())
        return resp.get("success", False)
    except Exception as e:
        print(f"Could not check sim success: {e}")
        return False


def run_trial(trial_num: int) -> bool:
    """Run one trial of the skill and check success."""
    # Read the skill's main.py
    import os
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(skill_dir, "scripts", "main.py")
    if not os.path.exists(main_py):
        print(f"Trial {trial_num}: SKIP - no scripts/main.py")
        return False

    with open(main_py) as f:
        code = f.read()

    print(f"Trial {trial_num}: submitting skill code...")
    job_id = submit_code(code)
    job = wait_for_job(job_id)

    result = job.get("result", {})
    status = job.get("status", "unknown")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    if status == "failed":
        error = result.get("error", stderr[:200])
        print(f"Trial {trial_num}: FAIL - execution error: {error}")
        return False

    # Check task success via sim
    success = check_success()
    label = "PASS" if success else "FAIL"
    print(f"Trial {trial_num}: {label} (sim _check_success={success})")
    return success


def main():
    passed = 0
    failed = 0
    failures = []

    for i in range(1, NUM_TRIALS + 1):
        try:
            if run_trial(i):
                passed += 1
            else:
                failed += 1
                failures.append(f"trial_{i}")
        except Exception as e:
            failed += 1
            failures.append(f"trial_{i}: {e}")
            print(f"Trial {i}: ERROR - {e}")

    total = passed + failed
    rate = (passed / total * 100) if total > 0 else 0

    result = {
        "skill": "pick-from-counter",
        "task_env": "RoboCasa-Pn-P-Counter-To-Cab-v0",
        "success_rate": rate,
        "total_trials": total,
        "passed": passed,
        "failed": failed,
        "failure_modes": failures,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
