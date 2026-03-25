#!/usr/bin/env python3
"""
Run detect-scene-objects trials and evaluate success.

Skill under test:
  Call sensors.find_objects() to detect all objects in the scene and return
  their names and world-frame (x, y, z) positions. Identify the target object
  (obj) and key fixtures (cabinet, counter).

Success criteria (ALL must hold):
  1. sensors.find_objects() returns a non-empty detection list
  2. Every detection has a valid 'name' (non-empty string)
  3. Every detection has a valid world-frame 'position' with finite x, y, z
  4. The target object (labelled 'obj') is present in the detections
  5. At least one key fixture ('cabinet' OR 'counter') is present
  6. Skill prints "Result: SUCCESS" to stdout

Failure modes tracked:
  - no_objects_detected : find_objects() returned an empty list
  - target_not_found    : 'obj' was not in any detection name
  - fixtures_not_found  : neither 'cabinet' nor 'counter' was detected
  - invalid_positions   : one or more detections lack valid xyz coordinates
  - timeout             : execution exceeded the time limit
  - crash               : unexpected exception / non-zero exit code
"""

import sys
import json
import math
import time
import requests
from pathlib import Path

# Unbuffered output
sys.stdout.reconfigure(line_buffering=True)

API = "http://localhost:8080"
HEADERS = {}

SKILL_CODE = Path(__file__).parent.parent / "scripts" / "main.py"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

NUM_TRIALS = 3
EXECUTION_TIMEOUT = 60  # seconds — detection only, no motion required


# ---------------------------------------------------------------------------
# API helpers  (same pattern as other skills in this repo)
# ---------------------------------------------------------------------------

def acquire_lease():
    r = requests.post(
        f"{API}/lease/acquire",
        headers=HEADERS,
        json={"holder": "detect-scene-objects-trials", "rewind_on_release": True},
    )
    r.raise_for_status()
    return r.json()["lease_id"]


def release_lease(lease_id):
    requests.post(
        f"{API}/lease/release",
        headers=HEADERS,
        json={"lease_id": lease_id},
    )


def extend_lease(lease_id):
    r = requests.post(
        f"{API}/lease/extend",
        headers={**HEADERS, "X-Lease-Id": lease_id},
        json={},
    )
    return r.status_code == 200


def run_skill(lease_id):
    if not SKILL_CODE.exists():
        raise FileNotFoundError(
            f"Skill code not found at {SKILL_CODE}. "
            "Implement scripts/main.py before running trials."
        )
    code = SKILL_CODE.read_text()
    r = requests.post(
        f"{API}/code/execute",
        headers={**HEADERS, "X-Lease-Id": lease_id},
        json={"code": code, "timeout": EXECUTION_TIMEOUT},
    )
    r.raise_for_status()
    return r.json()["execution_id"]


def wait_for_completion(lease_id, timeout=EXECUTION_TIMEOUT):
    start = time.time()
    stdout_offset = 0
    all_stdout = ""

    while time.time() - start < timeout:
        if int(time.time() - start) % 60 == 0 and int(time.time() - start) > 0:
            extend_lease(lease_id)

        r = requests.get(
            f"{API}/code/status",
            headers=HEADERS,
            params={"stdout_offset": stdout_offset},
        )
        data = r.json()

        if data.get("stdout"):
            print(data["stdout"], end="", flush=True)
            all_stdout += data["stdout"]
            stdout_offset += len(data["stdout"])

        is_running = data.get("is_running", data.get("running", False))
        if not is_running:
            time.sleep(0.5)
            result_r = requests.get(f"{API}/code/result", headers=HEADERS)
            return result_r.json().get("result", {})

        time.sleep(0.5)

    print(f"\nWARNING: Timeout after {timeout}s", flush=True)
    return {"stdout": all_stdout, "timeout": True}


def get_recording(execution_id):
    r = requests.get(f"{API}/code/recordings/{execution_id}", headers=HEADERS)
    if r.status_code == 200:
        return r.json()
    return None


def rewind_to_home(lease_id):
    print("Rewinding to home...", flush=True)
    r = requests.post(
        f"{API}/rewind/reset-to-home",
        headers={**HEADERS, "X-Lease-Id": lease_id},
    )
    if r.status_code == 200:
        for _ in range(120):
            status = requests.get(f"{API}/rewind/status", headers=HEADERS).json()
            if not status.get("is_rewinding", False):
                break
            time.sleep(1)
        print("Rewind complete", flush=True)
    else:
        print(f"Rewind failed: {r.text}", flush=True)


def wait_for_idle():
    for _ in range(30):
        r = requests.get(f"{API}/code/status", headers=HEADERS)
        data = r.json()
        if not data.get("is_running", data.get("running", False)):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Output parsing helpers
# ---------------------------------------------------------------------------

def _is_finite(v):
    """Return True if v is a real, finite number."""
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def parse_detections(stdout):
    """
    Try to extract the JSON detections list printed by the skill.

    The skill is expected to print a line like:
        Detections: [{"name": "mug", "position": [x, y, z]}, ...]
    or embed a JSON array anywhere in its stdout.

    Returns a list (possibly empty) of detection dicts.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        # Look for a JSON array in the output
        if stripped.startswith("["):
            try:
                data = json.loads(stripped)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        # Look for "Detections: [...]"
        if "detections:" in stripped.lower():
            idx = stripped.lower().index("detections:")
            candidate = stripped[idx + len("detections:"):].strip()
            try:
                data = json.loads(candidate)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
    return []


def validate_detections(detections):
    """
    Validate the detection list against success criteria 2–5.
    Returns (passed: bool, issues: list[str]).
    """
    issues = []

    if not detections:
        issues.append("no_objects_detected")
        return False, issues

    # Criteria 2 & 3: names + valid positions
    for i, det in enumerate(detections):
        name = det.get("name", "")
        if not isinstance(name, str) or not name.strip():
            issues.append(f"invalid_positions")  # reuse bucket; name is part of struct
            break
        pos = det.get("position", [])
        if len(pos) < 3 or not all(_is_finite(c) for c in pos[:3]):
            issues.append("invalid_positions")
            break

    # Criteria 4: target object present
    names_lower = [d.get("name", "").lower() for d in detections]
    if not any("obj" in n for n in names_lower):
        issues.append("target_not_found")

    # Criteria 5: at least one key fixture
    if not any("cabinet" in n or "counter" in n for n in names_lower):
        issues.append("fixtures_not_found")

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# Failure-mode classification
# ---------------------------------------------------------------------------

def classify_failure(stdout, result, detections):
    """Return a short failure-mode string."""
    s = stdout.lower()

    if result.get("timeout"):
        return "timeout"

    exit_code = result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return "crash"

    # Specific detection failures
    if not detections and (
        "no objects" in s or "empty" in s or "nothing detected" in s or "find_objects returned" in s
    ):
        return "no_objects_detected"

    if "target not found" in s or "obj not found" in s or "target_not_found" in s:
        return "target_not_found"

    if "fixture" in s and ("not found" in s or "missing" in s):
        return "fixtures_not_found"

    if "invalid position" in s or "nan" in s or "inf" in s:
        return "invalid_positions"

    # Fall back to structural validation failures
    if not detections:
        return "no_objects_detected"

    _, issues = validate_detections(detections)
    if issues:
        return issues[0]  # most specific first

    return "unknown"


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------

def run_trial(trial_num, lease_id):
    print(f"\n{'=' * 60}", flush=True)
    print(f"TRIAL {trial_num}", flush=True)
    print(f"{'=' * 60}\n", flush=True)

    wait_for_idle()

    try:
        exec_id = run_skill(lease_id)
    except FileNotFoundError as e:
        print(f"Skill not implemented: {e}", flush=True)
        return {
            "trial": trial_num,
            "success": False,
            "failure_mode": "crash",
            "error": str(e),
        }
    except requests.exceptions.HTTPError as e:
        print(f"Failed to start execution: {e}", flush=True)
        return {
            "trial": trial_num,
            "success": False,
            "failure_mode": "crash",
            "error": str(e),
        }

    print(f"Execution ID: {exec_id}\n", flush=True)

    result = wait_for_completion(lease_id, timeout=EXECUTION_TIMEOUT)

    stdout = result.get("stdout", "")

    # Primary success gate: skill must self-report success
    skill_says_success = "Result: SUCCESS" in stdout

    # Secondary gate: parse and validate detections from output
    detections = parse_detections(stdout)
    struct_ok, struct_issues = validate_detections(detections)

    success = skill_says_success and struct_ok

    if success:
        failure_mode = None
        print(f"\n  Detected {len(detections)} object(s):", flush=True)
        for d in detections:
            pos = d.get("position", [])
            pos_str = f"({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})" if len(pos) >= 3 else str(pos)
            print(f"    • {d.get('name', '?')}  @  {pos_str}", flush=True)
    else:
        if not skill_says_success:
            failure_mode = classify_failure(stdout, result, detections)
        else:
            # Skill claimed success but structural validation failed
            failure_mode = struct_issues[0] if struct_issues else "unknown"

    recording = get_recording(exec_id)

    trial_info = {
        "trial": trial_num,
        "execution_id": exec_id,
        "success": success,
        "failure_mode": failure_mode,
        "num_detections": len(detections),
        "exit_code": result.get("exit_code"),
        "duration": result.get("duration"),
        "stdout": stdout,
        "stderr": result.get("stderr", ""),
        "recording": recording,
    }

    with open(RESULTS_DIR / f"trial_{trial_num}.json", "w") as f:
        json.dump(trial_info, f, indent=2)

    print(f"\n{'=' * 60}", flush=True)
    status_str = "SUCCESS" if success else f"FAILED ({failure_mode})"
    print(f"TRIAL {trial_num} RESULT: {status_str}", flush=True)
    print(f"{'=' * 60}", flush=True)

    return trial_info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    num_trials = int(sys.argv[1]) if len(sys.argv) > 1 else NUM_TRIALS
    results = []

    print("Checking for running code...", flush=True)
    wait_for_idle()

    print("Acquiring lease...", flush=True)
    lease_id = acquire_lease()
    print(f"Lease acquired: {lease_id}\n", flush=True)

    try:
        for i in range(1, num_trials + 1):
            # Rewind to home so every trial starts from a clean sensor vantage
            rewind_to_home(lease_id)
            time.sleep(2)

            info = run_trial(i, lease_id)
            results.append(info)

            if i < num_trials:
                print("\nPausing 5s before next trial...", flush=True)
                time.sleep(5)

        # ----- Summary -----
        passed = sum(1 for r in results if r.get("success"))
        failed = num_trials - passed
        failure_modes = [r["failure_mode"] for r in results if r.get("failure_mode")]
        success_rate = int(100 * passed / num_trials) if num_trials else 0

        print(f"\n{'=' * 60}", flush=True)
        print("SUMMARY", flush=True)
        print(f"{'=' * 60}", flush=True)
        for r in results:
            mark = "\u2713" if r.get("success") else "\u2717"
            extra = "" if r.get("success") else f" ({r.get('failure_mode', '?')})"
            n_det = r.get("num_detections", "?")
            print(
                f"  Trial {r['trial']}: {mark}{extra}"
                f"  detections={n_det}"
                f"  (exec_id: {r.get('execution_id', 'N/A')})"
            )

        summary = {
            "skill": "detect-scene-objects",
            "success_rate": success_rate,
            "total_trials": num_trials,
            "passed": passed,
            "failed": failed,
            "failure_modes": failure_modes,
        }

        # Machine-readable JSON summary on its own line
        print(f"\n{json.dumps(summary)}")

        # Detailed summary saved to disk
        with open(RESULTS_DIR / "summary.json", "w") as f:
            json.dump(
                {
                    **summary,
                    "trials": [
                        {
                            "trial": r["trial"],
                            "success": r.get("success", False),
                            "failure_mode": r.get("failure_mode"),
                            "num_detections": r.get("num_detections"),
                            "execution_id": r.get("execution_id"),
                            "duration": r.get("duration"),
                        }
                        for r in results
                    ],
                },
                f,
                indent=2,
            )

    finally:
        release_lease(lease_id)
        print("\nLease released", flush=True)


if __name__ == "__main__":
    main()
