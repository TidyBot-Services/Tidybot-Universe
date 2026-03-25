#!/usr/bin/env python3
"""
Run grasp-and-lift trials and evaluate success.

Skill under test
----------------
Given a target object XYZ position:
  1. Open gripper (pre-grasp clearance).
  2. Move arm to approach pose directly above target (top-down orientation,
     Z = grasp_z + clearance) using wb.move_to_pose.
  3. Lower arm to grasp pose at target XYZ.
  4. Close gripper — detect contact via gripper-width threshold.
  5. Lift the object upward by lift_height metres.

Success criteria (ALL must hold)
----------------------------------
  1. No exception during any move_to_pose call (approach, lower, lift).
  2. Gripper closes and width after close > grasp_width_threshold (contact made).
  3. End-effector Z after lift ≥ grasp_z + lift_height - tolerance (arm actually rose).
  4. Skill prints "Result: SUCCESS" to stdout.

Failure modes tracked
----------------------
  approach_failed    — move_to_pose raised an error on the pre-grasp approach.
  lower_failed       — could not lower arm to grasp height (IK / collision).
  grasp_failed       — gripper closed but width ≤ threshold (slipped / empty).
  lift_failed        — arm could not lift after grasping (too heavy / collision).
  timeout            — execution exceeded EXECUTION_TIMEOUT seconds.
  crash              — unexpected exception / non-zero exit code.
"""

import sys
import requests
import time
import json
from pathlib import Path

# Unbuffered output so trial progress streams in real time
sys.stdout.reconfigure(line_buffering=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API = "http://localhost:8080"
HEADERS = {}

SKILL_SCRIPT = Path(__file__).parent.parent / "scripts" / "main.py"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

NUM_TRIALS = 3           # override with: python run_trials.py <N>
EXECUTION_TIMEOUT = 120  # seconds — grasp-and-lift is a short primitive

# Test target positions (XYZ in robot base frame, metres).
# Each trial cycles through these to exercise a range of reach distances.
TEST_TARGETS = [
    {"x": 0.40, "y":  0.00, "z": -0.42},  # centre, mid-range
    {"x": 0.45, "y":  0.10, "z": -0.42},  # slight right offset
    {"x": 0.35, "y": -0.10, "z": -0.42},  # slight left offset
]

# Inline skill-invocation code sent to the robot executor.
# Imports the bundled skill and calls grasp_and_lift with a target from
# TEST_TARGETS, indexed by trial number (wraps around if more trials than
# targets).
_INVOKE_TEMPLATE = """\
import sys, math
from robot_sdk import arm, gripper, sensors

# ── Inline skill implementation (top-down grasp + lift) ──────────────────────
APPROACH_CLEARANCE = 0.15   # m above grasp height
LIFT_HEIGHT        = 0.20   # m to rise after grasp
GRASP_WIDTH_MIN    = 0.010  # m — below this → empty hand (grasp failed)
MOVE_TIMEOUT       = 10     # s per move_to_pose call
STRAIGHT_DOWN_ROLL = math.pi  # roll=π → EE points straight down

target_x = {target_x}
target_y = {target_y}
target_z = {target_z}

def _move(x, y, z, label):
    print(f"  move_to_pose → {{label}} ({{x:.3f}}, {{y:.3f}}, {{z:.3f}})", flush=True)
    arm.move_to_pose(
        x=x, y=y, z=z,
        roll=STRAIGHT_DOWN_ROLL, pitch=0.0, yaw=0.0,
        timeout=MOVE_TIMEOUT,
    )

try:
    # 1. Open gripper
    print("Step 1: opening gripper", flush=True)
    gripper.open()

    # 2. Approach from above
    print("Step 2: approaching from above", flush=True)
    try:
        _move(target_x, target_y, target_z + APPROACH_CLEARANCE, "approach")
    except Exception as e:
        print(f"Result: FAILED – approach_failed: {{e}}", flush=True)
        sys.exit(1)

    # 3. Lower to grasp height
    print("Step 3: lowering to grasp height", flush=True)
    try:
        _move(target_x, target_y, target_z, "grasp")
    except Exception as e:
        print(f"Result: FAILED – lower_failed: {{e}}", flush=True)
        sys.exit(1)

    # 4. Close gripper and check contact
    print("Step 4: closing gripper", flush=True)
    gripper.close()
    width = gripper.get_width()
    print(f"  gripper width after close: {{width:.4f}} m", flush=True)
    if width <= GRASP_WIDTH_MIN:
        print(f"Result: FAILED – grasp_failed: width={{width:.4f}} ≤ threshold={{GRASP_WIDTH_MIN}}", flush=True)
        gripper.open()
        sys.exit(1)

    # 5. Lift
    print("Step 5: lifting object", flush=True)
    try:
        _move(target_x, target_y, target_z + LIFT_HEIGHT, "lift")
    except Exception as e:
        print(f"Result: FAILED – lift_failed: {{e}}", flush=True)
        sys.exit(1)

    # Verify arm reached lift height
    state = arm.get_state()
    ee_z = state.get("ee_z", None)
    lift_target_z = target_z + LIFT_HEIGHT
    if ee_z is not None and ee_z < lift_target_z - 0.05:
        print(
            f"Result: FAILED – lift_failed: ee_z={{ee_z:.3f}} < expected={{lift_target_z:.3f}}",
            flush=True,
        )
        sys.exit(1)

    print("Result: SUCCESS", flush=True)

except Exception as e:
    print(f"Result: FAILED – crash: {{e}}", flush=True)
    sys.exit(1)
"""


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def acquire_lease() -> str:
    r = requests.post(
        f"{API}/lease/acquire",
        headers=HEADERS,
        json={"holder": "grasp-and-lift-trials", "rewind_on_release": True},
    )
    r.raise_for_status()
    return r.json()["lease_id"]


def release_lease(lease_id: str) -> None:
    requests.post(
        f"{API}/lease/release",
        headers=HEADERS,
        json={"lease_id": lease_id},
    )


def extend_lease(lease_id: str) -> bool:
    r = requests.post(
        f"{API}/lease/extend",
        headers={**HEADERS, "X-Lease-Id": lease_id},
        json={},
    )
    return r.status_code == 200


def run_skill_code(lease_id: str, code: str) -> str:
    """Submit code for execution; return execution_id."""
    r = requests.post(
        f"{API}/code/execute",
        headers={**HEADERS, "X-Lease-Id": lease_id},
        json={"code": code, "timeout": EXECUTION_TIMEOUT},
    )
    r.raise_for_status()
    return r.json()["execution_id"]


def wait_for_completion(lease_id: str, timeout: int = EXECUTION_TIMEOUT) -> dict:
    """Poll /code/status until done; stream stdout. Return final result dict."""
    start = time.time()
    stdout_offset = 0
    all_stdout = ""

    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 60 == 0:
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
            return result_r.json().get("result", {"stdout": all_stdout})

        time.sleep(0.5)

    print(f"\nWARNING: Timeout after {timeout}s", flush=True)
    return {"stdout": all_stdout, "timeout": True}


def get_recording(execution_id: str) -> dict | None:
    r = requests.get(f"{API}/code/recordings/{execution_id}", headers=HEADERS)
    return r.json() if r.status_code == 200 else None


def rewind_to_home(lease_id: str) -> None:
    print("Rewinding to home...", flush=True)
    r = requests.post(
        f"{API}/rewind/reset-to-home",
        headers={**HEADERS, "X-Lease-Id": lease_id},
    )
    if r.status_code == 200:
        for _ in range(120):  # wait up to 2 minutes
            status = requests.get(f"{API}/rewind/status", headers=HEADERS).json()
            if not status.get("is_rewinding", False):
                break
            time.sleep(1)
        print("Rewind complete", flush=True)
    else:
        print(f"Rewind failed: {r.text}", flush=True)


def wait_for_idle() -> bool:
    """Block until no code execution is running (up to 30 s)."""
    for _ in range(30):
        data = requests.get(f"{API}/code/status", headers=HEADERS).json()
        if not data.get("is_running", data.get("running", False)):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Failure-mode classification
# ---------------------------------------------------------------------------

def classify_failure(stdout: str, result: dict) -> str:
    """Infer a concise failure label from skill output and exit metadata."""
    if result.get("timeout"):
        return "timeout"

    # Check for explicit failure tags printed by the skill
    for tag in ("approach_failed", "lower_failed", "grasp_failed", "lift_failed", "crash"):
        if tag in stdout:
            return tag

    # Fallback: non-zero exit code means unhandled crash
    exit_code = result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return "crash"

    return "unknown"


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------

def run_trial(trial_num: int, lease_id: str, num_trials: int) -> dict:
    print(f"\n{'=' * 60}", flush=True)
    print(f"TRIAL {trial_num}", flush=True)

    # Pick a test target (cycle through the list)
    target = TEST_TARGETS[(trial_num - 1) % len(TEST_TARGETS)]
    print(
        f"Target: x={target['x']:.2f}  y={target['y']:.2f}  z={target['z']:.2f}",
        flush=True,
    )
    print(f"{'=' * 60}\n", flush=True)

    wait_for_idle()

    # Build and submit code
    code = _INVOKE_TEMPLATE.format(
        target_x=target["x"],
        target_y=target["y"],
        target_z=target["z"],
    )

    try:
        exec_id = run_skill_code(lease_id, code)
    except requests.exceptions.HTTPError as e:
        print(f"Failed to start execution: {e}", flush=True)
        return {
            "trial": trial_num,
            "target": target,
            "success": False,
            "failure_mode": "crash",
            "error": str(e),
        }

    print(f"Execution ID: {exec_id}\n", flush=True)

    result = wait_for_completion(lease_id, timeout=EXECUTION_TIMEOUT)

    stdout = result.get("stdout", "")
    success = "Result: SUCCESS" in stdout
    failure_mode = None if success else classify_failure(stdout, result)

    recording = get_recording(exec_id)

    trial_info = {
        "trial": trial_num,
        "target": target,
        "execution_id": exec_id,
        "success": success,
        "failure_mode": failure_mode,
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

def main() -> None:
    num_trials = int(sys.argv[1]) if len(sys.argv) > 1 else NUM_TRIALS
    results = []

    print("grasp-and-lift — trial runner", flush=True)
    print(f"Trials: {num_trials}  |  Timeout per trial: {EXECUTION_TIMEOUT}s", flush=True)
    print(flush=True)

    print("Checking for running code...", flush=True)
    wait_for_idle()

    print("Acquiring lease...", flush=True)
    lease_id = acquire_lease()
    print(f"Lease acquired: {lease_id}\n", flush=True)

    try:
        for i in range(1, num_trials + 1):
            rewind_to_home(lease_id)
            time.sleep(2)  # brief settle after rewind

            info = run_trial(i, lease_id, num_trials)
            results.append(info)

            if i < num_trials:
                print("\nPausing 5 s before next trial...", flush=True)
                time.sleep(5)

        # ── Summary ──────────────────────────────────────────────────────────
        passed = sum(1 for r in results if r.get("success"))
        failed = num_trials - passed
        failure_modes = [r["failure_mode"] for r in results if r.get("failure_mode")]
        success_rate = int(100 * passed / num_trials) if num_trials else 0

        print(f"\n{'=' * 60}", flush=True)
        print("SUMMARY", flush=True)
        print(f"{'=' * 60}", flush=True)
        for r in results:
            mark = "✓" if r.get("success") else "✗"
            extra = "" if r.get("success") else f" ({r.get('failure_mode', '?')})"
            tgt = r["target"]
            print(
                f"  Trial {r['trial']}: {mark}{extra}"
                f"  target=({tgt['x']:.2f},{tgt['y']:.2f},{tgt['z']:.2f})"
                f"  exec_id={r.get('execution_id', 'N/A')}"
            )

        summary = {
            "skill": "grasp-and-lift",
            "success_rate": success_rate,
            "total_trials": num_trials,
            "passed": passed,
            "failed": failed,
            "failure_modes": failure_modes,
        }

        # Machine-readable JSON summary — one line for easy parsing
        print(f"\n{json.dumps(summary)}")

        # Detailed summary to disk
        with open(RESULTS_DIR / "summary.json", "w") as f:
            json.dump(
                {
                    **summary,
                    "trials": [
                        {
                            "trial": r["trial"],
                            "target": r["target"],
                            "success": r.get("success", False),
                            "failure_mode": r.get("failure_mode"),
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
