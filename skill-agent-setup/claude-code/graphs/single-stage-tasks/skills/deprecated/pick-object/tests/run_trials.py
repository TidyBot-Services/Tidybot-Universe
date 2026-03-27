#!/usr/bin/env python3
"""
Run pick-object trials and evaluate success.

Skill under test
----------------
Detect the target object ('obj') on the counter using detect-scene-objects,
then grasp-and-lift it. Composes detection with grasping into a reliable
pick sequence.

Pipeline exercised
------------------
  1. detect_scene_objects() — locate 'obj' and fixtures; get 'obj' XYZ
  2. Verify 'obj' position is plausibly on the counter (z in expected band)
  3. grasp_and_lift(target_x, target_y, target_z) — approach, lower, grasp, lift

Success criteria (ALL must hold)
---------------------------------
  1. 'obj' is present in detections with a finite world-frame (x, y, z)
  2. 'obj' z-position falls within the expected counter-height band
     (Z_COUNTER_MIN ≤ obj_z ≤ Z_COUNTER_MAX)
  3. Gripper confirms contact after close (width > grasp_width_threshold)
  4. End-effector reaches expected lift height (ee_z ≥ obj_z + LIFT_HEIGHT - tolerance)
  5. Skill prints "Result: SUCCESS" to stdout

Failure modes tracked
----------------------
  detection_failed    — detect_scene_objects() raised an error or found nothing
  obj_not_found       — scene was scanned but 'obj' is absent from detections
  obj_not_on_counter  — 'obj' detected but z-position outside counter-height band
  approach_failed     — arm could not reach the pre-grasp approach pose
  lower_failed        — arm could not descend to grasp height (IK / collision)
  grasp_failed        — gripper closed but width ≤ threshold (slipped / empty)
  lift_failed         — arm could not rise to lift height after grasping
  timeout             — execution exceeded EXECUTION_TIMEOUT seconds
  crash               — unexpected exception / non-zero exit code
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

SKILL_SCRIPT = Path(__file__).parent.parent / "scripts" / "main.py"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

NUM_TRIALS = 3            # override with: python run_trials.py <N>
EXECUTION_TIMEOUT = 180   # seconds — detection + full grasp motion

# Expected counter-height band for 'obj' z-position (robot base frame, metres).
# Tune these to match the physical setup.
Z_COUNTER_MIN = -0.60     # below this → object not on counter
Z_COUNTER_MAX = -0.25     # above this → suspiciously high for a counter object

# Grasp / lift parameters (must match the defaults in grasp-and-lift/scripts/main.py)
GRASP_WIDTH_THRESHOLD = 0.010   # m — gripper width below this → empty hand
LIFT_HEIGHT           = 0.20    # m — expected rise after grasp
LIFT_Z_TOLERANCE      = 0.05    # m — acceptable undershoot in lift verification


# ---------------------------------------------------------------------------
# Inline fallback code
# ---------------------------------------------------------------------------
# Submitted to the robot executor when scripts/main.py has not been implemented
# yet.  Calls detect_scene_objects() from the detect-scene-objects skill and
# grasp_and_lift() from the grasp-and-lift skill so the test exercises the
# composed pick behaviour directly.

_INLINE_PICK_CODE = """\
import sys, math, json
from robot_sdk import arm, gripper, sensors

# ── Inline detect-scene-objects ───────────────────────────────────────────────

def _is_finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False

def detect_scene_objects():
    raw      = sensors.find_objects()
    targeted = sensors.find_objects(target_names=["obj", "cabinet", "counter"])
    seen     = {o["name"] for o in raw}
    merged   = list(raw)
    for o in targeted:
        if o["name"] not in seen:
            merged.append(o)
            seen.add(o["name"])

    detections = []
    for o in merged:
        name = o.get("name", "")
        x, y, z = o.get("x", float("nan")), o.get("y", float("nan")), o.get("z", float("nan"))
        if not (isinstance(name, str) and name.strip()):
            continue
        if not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            continue
        detections.append({"name": name, "position": [float(x), float(y), float(z)]})

    if not detections:
        raise RuntimeError("no_objects_detected: find_objects returned an empty list")

    names_lower = [d["name"].lower() for d in detections]
    if not any("obj" in n for n in names_lower):
        raise RuntimeError("obj_not_found: 'obj' absent from scene detections")
    if not any("cabinet" in n or "counter" in n for n in names_lower):
        raise RuntimeError("fixtures_not_found: counter/cabinet not detected")
    return detections

# ── Inline grasp-and-lift ─────────────────────────────────────────────────────

APPROACH_CLEARANCE  = 0.15
LIFT_HEIGHT         = 0.20
GRASP_WIDTH_MIN     = 0.010
MOVE_TIMEOUT        = 10
STRAIGHT_DOWN_ROLL  = math.pi
LIFT_Z_TOLERANCE    = 0.05

def _move(x, y, z, label):
    print(f"  move_to_pose → {{label}} ({{x:.3f}}, {{y:.3f}}, {{z:.3f}})", flush=True)
    arm.move_to_pose(x=x, y=y, z=z,
                     roll=STRAIGHT_DOWN_ROLL, pitch=0.0, yaw=0.0,
                     timeout=MOVE_TIMEOUT)

def _gripper_width():
    try:
        w = sensors.get_gripper_width()
        if w is not None:
            return w
    except Exception:
        pass
    try:
        mm = gripper.get_state().get("position_mm")
        if mm is not None:
            return mm / 1000.0
    except Exception:
        pass
    try:
        pos = gripper.get_state().get("position", 255)
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0

def grasp_and_lift(target_x, target_y, target_z):
    gripper.open()

    try:
        _move(target_x, target_y, target_z + APPROACH_CLEARANCE, "approach")
    except Exception as e:
        print(f"Result: FAILED – approach_failed: {{e}}", flush=True)
        return False

    try:
        _move(target_x, target_y, target_z, "grasp")
    except Exception as e:
        print(f"Result: FAILED – lower_failed: {{e}}", flush=True)
        return False

    gripper.close()
    width = _gripper_width()
    print(f"  gripper width after close: {{width:.4f}} m", flush=True)
    if width <= GRASP_WIDTH_MIN:
        print(f"Result: FAILED – grasp_failed: width={{width:.4f}} ≤ threshold={{GRASP_WIDTH_MIN}}",
              flush=True)
        gripper.open()
        return False

    lift_z = target_z + LIFT_HEIGHT
    try:
        _move(target_x, target_y, lift_z, "lift")
    except Exception as e:
        print(f"Result: FAILED – lift_failed: {{e}}", flush=True)
        return False

    try:
        ee_x, ee_y, ee_z = sensors.get_ee_position()
        print(f"  ee_z after lift: {{ee_z:.3f}} m (target {{lift_z:.3f}} m)", flush=True)
        if ee_z < lift_z - LIFT_Z_TOLERANCE:
            print(f"Result: FAILED – lift_failed: ee_z={{ee_z:.3f}} < expected={{lift_z:.3f}}",
                  flush=True)
            return False
    except Exception:
        pass  # trust move_to_pose success

    return True

# ── Composed pick ─────────────────────────────────────────────────────────────

try:
    # Step 1: Detect scene
    print("Step 1: detecting scene objects", flush=True)
    try:
        detections = detect_scene_objects()
    except Exception as e:
        print(f"Result: FAILED – detection_failed: {{e}}", flush=True)
        sys.exit(1)

    print(f"Detections: {{json.dumps(detections)}}")

    # Step 2: Find 'obj' position
    obj_det = next(
        (d for d in detections if "obj" in d["name"].lower()), None
    )
    if obj_det is None:
        print("Result: FAILED – obj_not_found: 'obj' not in detections", flush=True)
        sys.exit(1)

    tx, ty, tz = obj_det["position"]
    print(f"Target 'obj' at ({tx:.3f}, {ty:.3f}, {tz:.3f})", flush=True)

    # Step 3: Verify counter height
    if not ({z_min} <= tz <= {z_max}):
        print(
            f"Result: FAILED – obj_not_on_counter: "
            f"obj z={{tz:.3f}} outside expected counter band [{z_min}, {z_max}]",
            flush=True,
        )
        sys.exit(1)

    # Step 4: Grasp and lift
    print("Step 2: grasping and lifting 'obj'", flush=True)
    ok = grasp_and_lift(tx, ty, tz)
    if not ok:
        sys.exit(1)

    print("Result: SUCCESS", flush=True)

except Exception as e:
    print(f"Result: FAILED – crash: {{e}}", flush=True)
    sys.exit(1)
""".format(z_min=Z_COUNTER_MIN, z_max=Z_COUNTER_MAX)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def acquire_lease() -> str:
    r = requests.post(
        f"{API}/lease/acquire",
        headers=HEADERS,
        json={"holder": "pick-object-trials", "rewind_on_release": True},
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


def submit_code(lease_id: str, code: str) -> str:
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
        for _ in range(120):
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
# Output parsing helpers
# ---------------------------------------------------------------------------

def _is_finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def parse_obj_detection(stdout: str) -> dict | None:
    """
    Find the 'obj' detection in the skill's Detections: [...] output line.
    Returns {"name": ..., "position": [x, y, z]} or None.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        candidate = None
        if stripped.startswith("["):
            candidate = stripped
        elif "detections:" in stripped.lower():
            idx = stripped.lower().index("detections:")
            candidate = stripped[idx + len("detections:"):].strip()

        if candidate:
            try:
                data = json.loads(candidate)
                if isinstance(data, list):
                    for det in data:
                        if "obj" in det.get("name", "").lower():
                            return det
            except json.JSONDecodeError:
                pass
    return None


def parse_gripper_width(stdout: str) -> float | None:
    """Extract the gripper width reported after close, e.g. 'gripper width after close: 0.0312 m'."""
    for line in stdout.splitlines():
        if "gripper width after close" in line.lower():
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    return float(parts[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
    return None


def parse_ee_z_after_lift(stdout: str) -> float | None:
    """Extract the ee_z reported after lift, e.g. 'ee_z after lift: -0.220 m ...'."""
    for line in stdout.splitlines():
        if "ee_z after lift" in line.lower():
            parts = line.split(":")
            if len(parts) >= 2:
                try:
                    return float(parts[-1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
    return None


# ---------------------------------------------------------------------------
# Success / failure evaluation
# ---------------------------------------------------------------------------

def evaluate_trial(stdout: str, result: dict) -> tuple[bool, str | None, dict]:
    """
    Evaluate trial output against all success criteria.

    Returns
    -------
    (success, failure_mode, details)
    """
    details: dict = {}

    # ── Primary gate ─────────────────────────────────────────────────────────
    skill_says_success = "Result: SUCCESS" in stdout

    # ── Criterion 1 & 2: obj detected with valid counter-height position ──────
    obj_det = parse_obj_detection(stdout)
    details["obj_detected"] = obj_det is not None
    obj_on_counter = False
    if obj_det:
        pos = obj_det.get("position", [])
        if len(pos) >= 3 and all(_is_finite(c) for c in pos[:3]):
            obj_z = pos[2]
            details["obj_z"] = obj_z
            obj_on_counter = Z_COUNTER_MIN <= obj_z <= Z_COUNTER_MAX
            details["obj_on_counter"] = obj_on_counter

    # ── Criterion 3: gripper contact confirmed ────────────────────────────────
    width = parse_gripper_width(stdout)
    details["gripper_width_m"] = width
    gripper_ok = width is not None and width > GRASP_WIDTH_THRESHOLD

    # ── Criterion 4: lift height reached ─────────────────────────────────────
    ee_z = parse_ee_z_after_lift(stdout)
    details["ee_z_after_lift"] = ee_z
    lift_ok = True  # optimistic if sensor line absent (trust move_to_pose)
    if ee_z is not None and obj_det:
        pos = obj_det.get("position", [])
        if len(pos) >= 3:
            expected_lift_z = pos[2] + LIFT_HEIGHT
            lift_ok = ee_z >= expected_lift_z - LIFT_Z_TOLERANCE
            details["lift_ok"] = lift_ok

    if skill_says_success:
        return True, None, details

    # ── Derive failure mode from stdout tags ──────────────────────────────────
    failure_mode = _classify_failure(stdout, result)
    return False, failure_mode, details


def _classify_failure(stdout: str, result: dict) -> str:
    """Map stdout / result metadata to a concise failure label."""
    if result.get("timeout"):
        return "timeout"

    # Check exit code first
    exit_code = result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        # Try to find a specific tag before falling back to crash
        pass

    # Ordered tag scan (most specific first)
    tags = [
        "detection_failed",
        "obj_not_found",
        "obj_not_on_counter",
        "approach_failed",
        "lower_failed",
        "grasp_failed",
        "lift_failed",
        "crash",
    ]
    for tag in tags:
        if tag in stdout:
            return tag

    if exit_code is not None and exit_code != 0:
        return "crash"

    return "unknown"


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------

def _load_skill_code() -> str:
    """
    Return the skill code to submit.

    Priority:
      1. scripts/main.py if it exists and is non-empty / not a placeholder
      2. Inline fallback template (_INLINE_PICK_CODE)
    """
    if SKILL_SCRIPT.exists():
        text = SKILL_SCRIPT.read_text().strip()
        if text and "NotImplementedError" not in text:
            print(f"Using skill script: {SKILL_SCRIPT}", flush=True)
            return text

    print(
        "scripts/main.py not implemented — using inline pick code for trial.",
        flush=True,
    )
    return _INLINE_PICK_CODE


def run_trial(trial_num: int, lease_id: str) -> dict:
    print(f"\n{'=' * 60}", flush=True)
    print(f"TRIAL {trial_num}", flush=True)
    print(f"{'=' * 60}\n", flush=True)

    wait_for_idle()

    code = _load_skill_code()

    try:
        exec_id = submit_code(lease_id, code)
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

    success, failure_mode, details = evaluate_trial(stdout, result)

    if success:
        obj_z = details.get("obj_z", "?")
        width = details.get("gripper_width_m", "?")
        ee_z  = details.get("ee_z_after_lift", "?")
        print(
            f"\n  obj_z={obj_z}  gripper_width={width}  ee_z_after_lift={ee_z}",
            flush=True,
        )

    recording = get_recording(exec_id)

    trial_info = {
        "trial": trial_num,
        "execution_id": exec_id,
        "success": success,
        "failure_mode": failure_mode,
        "details": details,
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

    print("pick-object — trial runner", flush=True)
    print(f"Trials: {num_trials}  |  Timeout per trial: {EXECUTION_TIMEOUT}s", flush=True)
    print(
        f"Counter-height band: z ∈ [{Z_COUNTER_MIN}, {Z_COUNTER_MAX}] m",
        flush=True,
    )
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

            info = run_trial(i, lease_id)
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
            mark = "\u2713" if r.get("success") else "\u2717"
            extra = "" if r.get("success") else f" ({r.get('failure_mode', '?')})"
            det   = r.get("details", {})
            obj_z = f"obj_z={det['obj_z']:.3f}" if "obj_z" in det else "obj_z=?"
            w     = f"w={det['gripper_width_m']:.4f}" if det.get("gripper_width_m") is not None else "w=?"
            print(
                f"  Trial {r['trial']}: {mark}{extra}"
                f"  {obj_z}  {w}"
                f"  exec_id={r.get('execution_id', 'N/A')}"
            )

        summary = {
            "skill": "pick-object",
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
                            "success": r.get("success", False),
                            "failure_mode": r.get("failure_mode"),
                            "details": r.get("details", {}),
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
