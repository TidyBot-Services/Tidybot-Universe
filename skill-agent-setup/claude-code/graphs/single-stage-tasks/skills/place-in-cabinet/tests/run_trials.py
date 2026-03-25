#!/usr/bin/env python3
"""
Run place-in-cabinet trials and evaluate success.

Skill under test
----------------
With an object already grasped, the skill must:
  1. Call sensors.find_objects() to locate the cabinet and derive an interior
     placement position.
  2. Move the held object to an approach pose above the cabinet interior
     (arm.move_to_pose, straight-down orientation, Z = place_z + clearance).
  3. Lower the arm to the final placement height (place_z) inside the cabinet.
  4. Open the gripper to release the object.
  5. Retract the arm upward and clear of the cabinet interior.

Pre-condition for every trial
------------------------------
  The robot starts holding an object — simulated here by first executing a
  quick top-down grasp at a known object position (TEST_SCENARIOS[i].object_*)
  before running the placement logic.  The rewind between trials resets the
  scene so the object is back in its original place.

Success criteria (ALL must hold)
----------------------------------
  1. sensors.find_objects() returns at least one detection whose name contains
     "cabinet" and whose position has three finite coordinates.
  2. A valid interior placement XYZ is derived from that detection.
  3. arm.move_to_pose raises no exception on the approach move.
  4. arm.move_to_pose raises no exception on the lower-into-cabinet move.
  5. gripper.open() succeeds and gripper width after open ≥ open_width_threshold
     (confirms the object was released, not still gripped).
  6. arm.move_to_pose raises no exception on the retract move.
  7. Skill prints "Result: SUCCESS" to stdout.

Failure modes tracked
----------------------
  cabinet_not_found    — find_objects() returned no detection named "cabinet".
  no_interior_position — cabinet detected but extracted position is invalid/missing.
  approach_failed      — move_to_pose raised an error on the pre-place approach.
  lower_failed         — move_to_pose raised an error lowering into the cabinet.
  release_failed       — gripper width after open ≤ threshold (object not released).
  retract_failed       — move_to_pose raised an error during retract.
  setup_failed         — pre-condition grasp could not be completed (trial skipped).
  timeout              — execution exceeded EXECUTION_TIMEOUT seconds.
  crash                — unexpected exception / non-zero exit code.
"""

import sys
import json
import math
import time
import requests
from pathlib import Path

# Unbuffered output so trial progress streams in real time
sys.stdout.reconfigure(line_buffering=True)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API = "http://localhost:8080"
HEADERS = {}

RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

NUM_TRIALS = 3            # override with: python run_trials.py <N>
EXECUTION_TIMEOUT = 150   # seconds — grasp + sense + place + retract

# Each scenario specifies where the object starts (for the pre-condition grasp)
# and the cabinet region the skill is expected to find with find_objects().
# object_*   : world-frame XYZ of the graspable object before the trial
# trial_label: human-readable description for logs
TEST_SCENARIOS = [
    {
        "label": "centre-approach",
        "object_x": 0.40, "object_y":  0.00, "object_z": -0.42,
    },
    {
        "label": "right-offset",
        "object_x": 0.42, "object_y":  0.12, "object_z": -0.42,
    },
    {
        "label": "left-offset",
        "object_x": 0.38, "object_y": -0.12, "object_z": -0.42,
    },
]

# ---------------------------------------------------------------------------
# Inline skill-invocation code sent to the robot executor.
#
# The script is self-contained and runs entirely inside the robot's Python
# execution sandbox (access to robot_sdk).  It:
#   A. Executes a fast top-down grasp at object_* to satisfy the pre-condition.
#   B. Runs the full place-in-cabinet logic as defined in SKILL.md.
#
# Explicit "Result: FAILED – <tag>: …" lines are printed on every failure so
# classify_failure() can map them to the canonical failure-mode names.
# ---------------------------------------------------------------------------

_INVOKE_TEMPLATE = """\
import sys, math
from robot_sdk import arm, gripper, sensors

# ── Constants ─────────────────────────────────────────────────────────────────
APPROACH_CLEARANCE  = 0.15    # m — above target height for both grasp & place
LIFT_HEIGHT         = 0.18    # m — raise after grasping before navigating
OPEN_WIDTH_MIN      = 0.030   # m — gripper must be at least this open after release
GRASP_WIDTH_MIN     = 0.010   # m — gripper must be at least this wide to confirm grasp
MOVE_TIMEOUT        = 10      # s — per arm.move_to_pose call
STRAIGHT_DOWN_ROLL  = math.pi # roll=π → EE points straight down

# Pre-condition object position (injected by trial runner)
OBJ_X = {object_x}
OBJ_Y = {object_y}
OBJ_Z = {object_z}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _move(x, y, z, label):
    print(f"  move_to_pose → {{label}} ({{x:.3f}}, {{y:.3f}}, {{z:.3f}})", flush=True)
    arm.move_to_pose(
        x=x, y=y, z=z,
        roll=STRAIGHT_DOWN_ROLL, pitch=0.0, yaw=0.0,
        timeout=MOVE_TIMEOUT,
    )


def _gripper_width():
    \"\"\"Return current gripper opening in metres (best-effort).\"\"\"
    try:
        w = sensors.get_gripper_width()
        if w is not None:
            return float(w)
    except Exception:
        pass
    try:
        state = gripper.get_state()
        mm = state.get("position_mm")
        if mm is not None:
            return mm / 1000.0
    except Exception:
        pass
    try:
        state = gripper.get_state()
        pos = state.get("position", 255)   # 0 = open, 255 = closed
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


# ── Phase A: pre-condition — grasp object at known position ───────────────────

print("=== Phase A: pre-condition grasp ===", flush=True)
try:
    print("A1: opening gripper", flush=True)
    gripper.open()

    print("A2: approaching object from above", flush=True)
    try:
        _move(OBJ_X, OBJ_Y, OBJ_Z + APPROACH_CLEARANCE, "pre-grasp approach")
    except Exception as e:
        print(f"Result: FAILED – setup_failed: approach to object failed: {{e}}", flush=True)
        sys.exit(1)

    print("A3: lowering to grasp height", flush=True)
    try:
        _move(OBJ_X, OBJ_Y, OBJ_Z, "grasp")
    except Exception as e:
        print(f"Result: FAILED – setup_failed: lower to object failed: {{e}}", flush=True)
        sys.exit(1)

    print("A4: closing gripper", flush=True)
    gripper.close()
    pre_width = _gripper_width()
    print(f"  gripper width after close: {{pre_width:.4f}} m", flush=True)
    if pre_width <= GRASP_WIDTH_MIN:
        print(f"Result: FAILED – setup_failed: grasp check failed (width={{pre_width:.4f}})", flush=True)
        sys.exit(1)

    print("A5: lifting object", flush=True)
    try:
        _move(OBJ_X, OBJ_Y, OBJ_Z + LIFT_HEIGHT, "lift")
    except Exception as e:
        print(f"Result: FAILED – setup_failed: lift failed: {{e}}", flush=True)
        sys.exit(1)

    print("Pre-condition satisfied: object grasped and lifted.\\n", flush=True)

except Exception as e:
    print(f"Result: FAILED – setup_failed: unexpected error during pre-condition: {{e}}", flush=True)
    sys.exit(1)


# ── Phase B: place-in-cabinet skill ──────────────────────────────────────────

print("=== Phase B: place-in-cabinet ===", flush=True)

# Step 1: Detect cabinet and derive interior placement position ────────────────
print("Step 1: detecting cabinet with sensors.find_objects()", flush=True)
try:
    detections = sensors.find_objects()
    print(f"  find_objects returned {{len(detections)}} detection(s)", flush=True)
    for d in detections:
        print(f"    • {{d.get('name','?')}} @ {{d.get('position','?')}}", flush=True)
except Exception as e:
    print(f"Result: FAILED – crash: find_objects() raised: {{e}}", flush=True)
    sys.exit(1)

cabinet = None
for d in detections:
    if "cabinet" in d.get("name", "").lower():
        cabinet = d
        break

if cabinet is None:
    names = [d.get("name", "?") for d in detections]
    print(f"Result: FAILED – cabinet_not_found: no cabinet in detections {{names}}", flush=True)
    sys.exit(1)

# Extract and validate interior position
raw_pos = cabinet.get("position", [])
if not raw_pos or len(raw_pos) < 3:
    print(f"Result: FAILED – no_interior_position: cabinet position missing or malformed: {{raw_pos}}", flush=True)
    sys.exit(1)

try:
    place_x = float(raw_pos[0])
    place_y = float(raw_pos[1])
    place_z = float(raw_pos[2])
    if not all(math.isfinite(v) for v in (place_x, place_y, place_z)):
        raise ValueError("non-finite coordinate")
except (TypeError, ValueError) as e:
    print(f"Result: FAILED – no_interior_position: invalid cabinet position {{raw_pos}}: {{e}}", flush=True)
    sys.exit(1)

print(f"  cabinet interior target: ({place_x:.3f}, {place_y:.3f}, {place_z:.3f})", flush=True)

# Step 2: Approach — move above cabinet interior ───────────────────────────────
print("Step 2: approaching above cabinet interior", flush=True)
try:
    _move(place_x, place_y, place_z + APPROACH_CLEARANCE, "place-approach")
except Exception as e:
    print(f"Result: FAILED – approach_failed: {{e}}", flush=True)
    sys.exit(1)

# Step 3: Lower into cabinet ──────────────────────────────────────────────────
print("Step 3: lowering into cabinet", flush=True)
try:
    _move(place_x, place_y, place_z, "place-lower")
except Exception as e:
    print(f"Result: FAILED – lower_failed: {{e}}", flush=True)
    sys.exit(1)

# Step 4: Open gripper to release object ──────────────────────────────────────
print("Step 4: opening gripper to release object", flush=True)
try:
    gripper.open()
except Exception as e:
    print(f"Result: FAILED – release_failed: gripper.open() raised: {{e}}", flush=True)
    sys.exit(1)

post_width = _gripper_width()
print(f"  gripper width after open: {{post_width:.4f}} m", flush=True)
if post_width < OPEN_WIDTH_MIN:
    print(
        f"Result: FAILED – release_failed: "
        f"gripper width {{post_width:.4f}} < threshold {{OPEN_WIDTH_MIN}} "
        f"(object may still be gripped)",
        flush=True,
    )
    sys.exit(1)

# Step 5: Retract arm clear of cabinet ────────────────────────────────────────
print("Step 5: retracting arm", flush=True)
try:
    _move(place_x, place_y, place_z + APPROACH_CLEARANCE, "retract")
except Exception as e:
    print(f"Result: FAILED – retract_failed: {{e}}", flush=True)
    sys.exit(1)

print("Result: SUCCESS", flush=True)
"""

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def acquire_lease() -> str:
    r = requests.post(
        f"{API}/lease/acquire",
        headers=HEADERS,
        json={"holder": "place-in-cabinet-trials", "rewind_on_release": True},
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
    """Poll /code/status until done; stream stdout live. Return final result dict."""
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
            final = result_r.json().get("result", {})
            # Merge any stdout we collected ourselves in case result omits it
            if "stdout" not in final:
                final["stdout"] = all_stdout
            return final

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
    """Block until no code execution is in flight (up to 30 s)."""
    for _ in range(30):
        data = requests.get(f"{API}/code/status", headers=HEADERS).json()
        if not data.get("is_running", data.get("running", False)):
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Failure-mode classification
# ---------------------------------------------------------------------------

# Canonical failure tags embedded in "Result: FAILED – <tag>: …" lines
_FAILURE_TAGS = (
    "cabinet_not_found",
    "no_interior_position",
    "approach_failed",
    "lower_failed",
    "release_failed",
    "retract_failed",
    "setup_failed",
    "crash",
)


def classify_failure(stdout: str, result: dict) -> str:
    """Return the most specific failure-mode string for a failed trial."""
    if result.get("timeout"):
        return "timeout"

    # Scan stdout for explicit failure tags printed by the inline script
    for tag in _FAILURE_TAGS:
        if tag in stdout:
            return tag

    # Non-zero exit code with no tagged message → unhandled crash
    exit_code = result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return "crash"

    return "unknown"


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------

def run_trial(trial_num: int, lease_id: str, num_trials: int) -> dict:
    print(f"\n{'=' * 60}", flush=True)
    print(f"TRIAL {trial_num} / {num_trials}", flush=True)

    scenario = TEST_SCENARIOS[(trial_num - 1) % len(TEST_SCENARIOS)]
    print(
        f"Scenario: {scenario['label']}  "
        f"object=({scenario['object_x']:.2f}, {scenario['object_y']:.2f}, {scenario['object_z']:.2f})",
        flush=True,
    )
    print(f"{'=' * 60}\n", flush=True)

    wait_for_idle()

    code = _INVOKE_TEMPLATE.format(
        object_x=scenario["object_x"],
        object_y=scenario["object_y"],
        object_z=scenario["object_z"],
    )

    try:
        exec_id = run_skill_code(lease_id, code)
    except requests.exceptions.HTTPError as e:
        print(f"Failed to start execution: {e}", flush=True)
        return {
            "trial": trial_num,
            "scenario": scenario,
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
        "scenario": scenario,
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

    print("place-in-cabinet — trial runner", flush=True)
    print(f"Trials: {num_trials}  |  Timeout per trial: {EXECUTION_TIMEOUT}s", flush=True)
    print(flush=True)

    print("Checking for running code...", flush=True)
    wait_for_idle()

    print("Acquiring lease...", flush=True)
    lease_id = acquire_lease()
    print(f"Lease acquired: {lease_id}\n", flush=True)

    try:
        for i in range(1, num_trials + 1):
            # Rewind restores the scene: object returns to its original position,
            # gripper opens, and arm returns to home pose.
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
            sc = r["scenario"]
            print(
                f"  Trial {r['trial']}: {mark}{extra}"
                f"  scenario={sc['label']}"
                f"  exec_id={r.get('execution_id', 'N/A')}"
            )

        summary = {
            "skill": "place-in-cabinet",
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
                            "scenario": r["scenario"],
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
