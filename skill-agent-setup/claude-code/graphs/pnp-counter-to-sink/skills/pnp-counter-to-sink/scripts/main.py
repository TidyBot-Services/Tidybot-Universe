#!/usr/bin/env python3
"""Auto-generated code execution wrapper."""

import sys
import os

# Add parent directory to path so robot_sdk can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Initialize robot_sdk with backend connections
from backends.franka import FrankaBackend
from backends.base import BaseBackend
from backends.gripper import GripperBackend
from backends.mocap import MocapBackend
from config import FrankaBackendConfig, BaseBackendConfig, GripperBackendConfig, MocapBackendConfig, TimingConfig
from robot_sdk import ArmAPI, BaseAPI, GripperAPI, SensorAPI, YoloAPI, WholeBodyAPI

timing = TimingConfig()
import robot_sdk

# Create backend configurations (use environment variables or defaults)
import asyncio

dry_run = os.getenv("ROBOT_DRY_RUN", "false").lower() == "true"

franka_config = FrankaBackendConfig(
    host=os.getenv("FRANKA_IP", "localhost"),
    cmd_port=int(os.getenv("FRANKA_CMD_PORT", "5555")),
    state_port=int(os.getenv("FRANKA_STATE_PORT", "5556")),
    stream_port=int(os.getenv("FRANKA_STREAM_PORT", "5557")),
)

base_config = BaseBackendConfig(
    host=os.getenv("BASE_IP", "localhost"),
    port=int(os.getenv("BASE_PORT", "50000")),
    authkey=b"secret password",
)

gripper_config = GripperBackendConfig(
    host=os.getenv("GRIPPER_IP", "localhost"),
    cmd_port=int(os.getenv("GRIPPER_CMD_PORT", "5570")),
    state_port=int(os.getenv("GRIPPER_STATE_PORT", "5571")),
)

mocap_config = MocapBackendConfig(
    host=os.getenv("MOCAP_IP", "localhost"),
    pub_port=int(os.getenv("MOCAP_PUB_PORT", "5590")),
)

# Create backends
franka_backend = FrankaBackend(franka_config, dry_run=dry_run)
base_backend = BaseBackend(base_config, dry_run=dry_run)
gripper_backend = GripperBackend(gripper_config, dry_run=dry_run)
mocap_backend = MocapBackend(mocap_config, dry_run=dry_run)

# Connect to backends (gracefully handle unavailable ones)
async def init_backends():
    # Franka (arm) - required
    try:
        await franka_backend.connect()
        print("[SDK] Franka backend connected")
    except Exception as e:
        print(f"[SDK] WARNING: Franka backend unavailable: {e}")

    # Base - optional
    try:
        await base_backend.connect()
        print("[SDK] Base backend connected")
    except Exception as e:
        print(f"[SDK] WARNING: Base backend unavailable: {e}")

    # Gripper - optional
    try:
        await gripper_backend.connect()
        print("[SDK] Gripper backend connected")
    except Exception as e:
        print(f"[SDK] WARNING: Gripper backend unavailable: {e}")

    # Mocap - optional
    try:
        await mocap_backend.connect()
        print("[SDK] Mocap backend connected")
    except Exception as e:
        print(f"[SDK] WARNING: Mocap backend unavailable: {e}")

asyncio.run(init_backends())

# Initialize SDK global instances
robot_sdk.arm = ArmAPI(
    franka_backend,
    motion_timeout=timing.motion_timeout_s,
    settle_timeout=timing.settle_timeout_s,
    command_rate_hz=timing.arm_command_rate_hz,
    converge_pos_m=timing.arm_converge_pos_m,
    converge_joint_rad=timing.arm_converge_joint_rad,
    converge_vel=timing.arm_converge_vel,
)
robot_sdk.base = BaseAPI(
    base_backend,
    mocap_backend=mocap_backend,
    timeout=timing.base_timeout_s,
    position_tolerance_m=timing.base_position_tolerance_m,
    angle_tolerance_rad=timing.base_angle_tolerance_rad,
)
robot_sdk.gripper = GripperAPI(gripper_backend)

# Initialize rewind API (uses HTTP calls to agent server)
from robot_sdk.rewind import RewindAPI
server_url = os.getenv("ROBOT_SERVER_URL", "http://localhost:8080")

robot_sdk.sensors = SensorAPI(franka_backend, base_backend, gripper_backend, agent_server_url=server_url, mocap_backend=mocap_backend)
lease_id = os.getenv("ROBOT_LEASE_ID")
robot_sdk.rewind = RewindAPI(server_url=server_url, lease_id=lease_id)
print(f"[SDK] Rewind API initialized (server: {server_url})")

# Initialize YOLO API (uses HTTP calls to remote YOLO server + agent server cameras)
from robot_sdk.yolo import YoloAPI
robot_sdk.yolo = YoloAPI(
    yolo_server_url=os.getenv("YOLO_SERVER_URL", ""),
    agent_server_url=server_url,
)
print("[SDK] YOLO API initialized")

# Initialize display API (uses HTTP calls to agent server)
from robot_sdk.display import DisplayAPI
robot_sdk.display = DisplayAPI(server_url=server_url)
print("[SDK] Display API initialized")

# Initialize whole-body motion API (uses planning server)
from robot_sdk.wb import WholeBodyAPI
planner_url = os.getenv("PLANNER_URL", "http://localhost:5500")
robot_sdk.wb = WholeBodyAPI(
    arm_backend=franka_backend,
    base_backend=base_backend,
    planner_url=planner_url,
)
print(f"[SDK] Whole-body API initialized (planner: {planner_url})")

# Initialize generic HTTP client (for calling any external service)
from robot_sdk import http as _http_module
robot_sdk.http = _http_module
print("[SDK] HTTP client initialized")

# Make them available for import
arm = robot_sdk.arm
base = robot_sdk.base
gripper = robot_sdk.gripper
sensors = robot_sdk.sensors
rewind = robot_sdk.rewind
yolo = robot_sdk.yolo
display = robot_sdk.display
wb = robot_sdk.wb
http = robot_sdk.http

# Also expose backends directly for advanced usage
# (same pattern as rewind orchestrator uses)

# ============================================================================
# USER CODE STARTS HERE
# ============================================================================

"""
PnP Counter-to-Sink: Pick object from counter, place inside sink.

Pipeline:
1. Detect target + sink position (save sink world coords)
2. base.forward() to reach object, pick with arm_only
3. Lift, base.forward(-dist) back toward sink
4. wb.move_to_pose above saved sink, lower arm_only, release, retreat
5. Verify with /task/success

Success criteria (RoboCasa _check_success):
  - obj_inside_of(self, "obj", self.sink, partial_check=True)
  - gripper_obj_far(self)  (default threshold 0.25m)
"""
import numpy as np
import time
import re
from transforms3d.euler import euler2quat
from robot_sdk import sensors, arm, base, wb, gripper, http


def is_held(threshold=230):
    gs = gripper.get_state()
    pos = gs.get("position", 0)
    return pos is not None and 15 < pos < threshold, pos


def task_success():
    try:
        return http.get("http://localhost:5500/task/success").json().get("success", False)
    except:
        return False


def firm_grip():
    gripper.close(force=255)
    time.sleep(0.5)
    gripper.grasp(force=255)
    time.sleep(0.5)


def main():
    info = sensors.get_task_info()
    print(f"Task: {info.get('lang', '')}")

    # === DETECT SCENE ===
    objs = sensors.find_objects()
    ab = sensors.get_arm_base_world()
    print(f"Arm base: ({ab[0]:.3f}, {ab[1]:.3f}, {ab[2]:.3f})")

    target = None
    sink_basin = None
    distr_sink = None
    spout = None

    for o in objs:
        if re.match(r"^obj_\d+$", o["name"]) and target is None:
            if o.get("fixture_context", "") == "counter":
                target = o
        if o["name"] == "object" and o.get("size_x", 0) > 0.25 and sink_basin is None:
            sink_basin = o
        if o["name"].startswith("distr_sink") and distr_sink is None:
            distr_sink = o
        if o["name"] == "spout" and spout is None:
            spout = o

    # Fallback target
    if target is None:
        for o in objs:
            if o.get("fixture_context") == "counter" and o["size_x"] < 0.15 and o["size_y"] < 0.15:
                if not o["name"].startswith("distr"):
                    target = o
                    break

    if target is None:
        print("FAILURE: No target found on counter")
        return

    # Sink position
    if sink_basin:
        SINK_WORLD = np.array([sink_basin["x"], sink_basin["y"], sink_basin["z"]])
        print(f"Sink basin: ({SINK_WORLD[0]:.3f}, {SINK_WORLD[1]:.3f}, {SINK_WORLD[2]:.3f}) "
              f"size=({sink_basin['size_x']:.3f}, {sink_basin['size_y']:.3f}, {sink_basin['size_z']:.3f})")
    elif spout:
        SINK_WORLD = np.array([spout["x"], spout["y"], spout["z"] - 0.28])
        print(f"Sink from spout: ({SINK_WORLD[0]:.3f}, {SINK_WORLD[1]:.3f}, {SINK_WORLD[2]:.3f})")
    else:
        print("FAILURE: Cannot locate sink")
        return

    if distr_sink:
        print(f"Distr sink: ({distr_sink['x']:.3f}, {distr_sink['y']:.3f}, {distr_sink['z']:.3f})")

    print(f"Target: {target['name']} ({target['x']:.3f}, {target['y']:.3f}, {target['z']:.3f})")

    # === MOVE BASE TOWARD OBJECT ===
    # Robot starts at sink. Object is on counter, behind the arm.
    # base.forward() moves in -x direction (robot faces -x).
    base_state = base.get_state()
    heading = base_state["base_pose"][2]
    dx = target['x'] - ab[0]
    dy = target['y'] - ab[1]
    c, s = np.cos(heading), np.sin(heading)
    arm_x = c * dx + s * dy  # distance in arm forward direction

    fwd_dist = 0.0
    if arm_x < -0.1:
        fwd_dist = abs(arm_x) - 0.35
        if fwd_dist > 0.05:
            print(f"\nBase forward {fwd_dist:.3f}m toward object (arm_x={arm_x:.3f})")
            base.forward(fwd_dist)
            time.sleep(0.3)
            try:
                arm.go_home()
            except:
                pass

            # Re-detect target after base move
            objs2 = sensors.find_objects()
            ab = sensors.get_arm_base_world()
            for o in objs2:
                if re.match(r"^obj_\d+$", o["name"]) and o.get("fixture_context") == "counter":
                    target = o
                    break
            print(f"Post-fwd: target=({target['x']:.3f},{target['y']:.3f},{target['z']:.3f}) ab=({ab[0]:.3f},{ab[1]:.3f})")

    # === PICK ===
    print("\n=== PICK ===")
    tx, ty, tz = target['x'], target['y'], target['z']
    yaw_base = np.arctan2(ty - ab[1], tx - ab[0])

    picked = False
    for cycle in range(3):
        if picked:
            break

        if cycle > 0:
            print(f"\nPick retry cycle {cycle + 1}")
            gripper.open()
            try:
                arm.go_home()
            except:
                pass
            time.sleep(0.3)
            objs2 = sensors.find_objects()
            ab = sensors.get_arm_base_world()
            for o in objs2:
                if re.match(r"^obj_\d+$", o["name"]) and o.get("fixture_context") == "counter":
                    tx, ty, tz = o['x'], o['y'], o['z']
                    yaw_base = np.arctan2(ty - ab[1], tx - ab[0])
                    break

        for gz_off in [-0.02, -0.04, 0.0]:
            if picked:
                break
            for yaw_off in [0, np.radians(20), np.radians(-20)]:
                if picked:
                    break
                yaw = yaw_base + yaw_off
                q = list(euler2quat(0, 3 * np.pi / 4, yaw, 'sxyz'))

                gripper.open()
                time.sleep(0.4)

                try:
                    # arm_only for precise grasping (base already positioned)
                    wb.move_to_pose(tx, ty, tz + 0.10, quat=q, mask="arm_only", timeout=10.0)
                    wb.move_to_pose(tx, ty, tz + gz_off, quat=q, mask="arm_only", timeout=10.0)
                except Exception as e:
                    print(f"  [yaw={np.degrees(yaw):.0f} gz={gz_off}] move failed: {e}")
                    continue

                firm_grip()
                ok, pos = is_held()
                if not ok:
                    print(f"  [yaw={np.degrees(yaw):.0f} gz={gz_off}] miss (pos={pos})")
                    continue

                print(f"  [yaw={np.degrees(yaw):.0f} gz={gz_off}] CONTACT pos={pos}")

                # Lift to confirm hold
                firm_grip()
                try:
                    arm.move_delta(dz=0.05, duration=2.0)
                except:
                    pass
                time.sleep(0.3)
                firm_grip()

                ok2, pos2 = is_held()
                if ok2:
                    print(f"  PICKED! pos={pos2}")
                    picked = True
                else:
                    print(f"  Dropped during lift (pos={pos2})")

    if not picked:
        print("FAILURE: Could not pick object")
        return

    # === TRANSPORT TO SINK ===
    print("\n=== TRANSPORT ===")

    # Lift higher
    firm_grip()
    try:
        arm.move_delta(dz=0.12, duration=2.0)
    except:
        pass
    firm_grip()

    # Move base back toward sink
    if fwd_dist > 0.05:
        print(f"Base backward {fwd_dist:.3f}m toward sink")
        firm_grip()
        base.forward(-fwd_dist)
        time.sleep(0.3)

    ok, pos = is_held()
    print(f"After transport - held: {ok}, pos={pos}")
    if not ok:
        print("Lost object during transport")
        gripper.open()
        try:
            arm.go_home()
        except:
            pass
        result = task_success()
        if result:
            print("SUCCESS: Object landed in sink!")
        else:
            print("FAILURE: Lost object")
        return

    # === PLACE IN SINK ===
    print("\n=== PLACE ===")

    # Use saved SINK_WORLD coordinates (don't re-detect!)
    ab_now = sensors.get_arm_base_world() or ab
    yaw_sink = np.arctan2(SINK_WORLD[1] - ab_now[1], SINK_WORLD[0] - ab_now[0])
    q_td = list(euler2quat(0, 3 * np.pi / 4, yaw_sink, 'sxyz'))

    # Move above sink
    above_z = SINK_WORLD[2] + 0.22
    print(f"Above sink: ({SINK_WORLD[0]:.3f}, {SINK_WORLD[1]:.3f}, {above_z:.3f})")
    try:
        wb.move_to_pose(float(SINK_WORLD[0]), float(SINK_WORLD[1]), float(above_z),
                       quat=q_td, mask="arm_only", timeout=12.0)
    except Exception as e:
        print(f"arm_only above sink failed: {e}, trying wb")
        try:
            wb.move_to_pose(float(SINK_WORLD[0]), float(SINK_WORLD[1]), float(above_z),
                           quat=q_td, timeout=15.0)
        except Exception as e2:
            print(f"wb above sink also failed: {e2}")

    firm_grip()
    ok, pos = is_held()
    print(f"Above sink - held: {ok}, pos={pos}")

    if not ok:
        print("Lost object above sink")
        gripper.open()
        try:
            arm.go_home()
        except:
            pass
        result = task_success()
        print(f"task_success: {result}")
        if result:
            print("SUCCESS: Object landed in sink!")
        else:
            print("FAILURE: Object not in sink")
        return

    # Recompute yaw after any base movement from wb
    ab_now2 = sensors.get_arm_base_world() or ab_now
    yaw_sink2 = np.arctan2(SINK_WORLD[1] - ab_now2[1], SINK_WORLD[0] - ab_now2[0])
    q_td2 = list(euler2quat(0, 3 * np.pi / 4, yaw_sink2, 'sxyz'))

    # Lower into sink
    print(f"Lowering to sink z={SINK_WORLD[2]:.3f}")
    try:
        wb.move_to_pose(float(SINK_WORLD[0]), float(SINK_WORLD[1]), float(SINK_WORLD[2]),
                       quat=q_td2, mask="arm_only", timeout=10.0)
    except Exception as e:
        print(f"Lower failed: {e}, trying delta")
        try:
            arm.move_delta(dz=-0.18, duration=2.5)
        except:
            pass

    # Check EE position
    ee = arm.get_state()["ee_pose"]
    print(f"EE arm: ({ee[12]:.3f}, {ee[13]:.3f}, {ee[14]:.3f}), world_z ~ {ee[14]+ab_now2[2]:.3f}")

    # Release
    print("Releasing object")
    q_joints = arm.get_state().get("q", [])
    try:
        arm.move_joints(q_joints, duration=0.5)
        time.sleep(0.3)
    except:
        pass

    gripper.open()
    time.sleep(2.0)

    # Check success IMMEDIATELY after release, before any arm movement
    result = task_success()
    print(f"task_success (immediate): {result}")

    if not result:
        # Retreat arm and check again
        try:
            arm.move_delta(dz=0.25, duration=2.0)
            time.sleep(0.5)
        except:
            pass
        result = task_success()
        print(f"task_success (after retreat): {result}")

    if not result:
        try:
            arm.go_home()
            time.sleep(1.0)
        except:
            pass
        result = task_success()
        print(f"task_success (after home): {result}")

    # Check object position for debug
    try:
        objs_after = sensors.find_objects()
        for o in objs_after:
            if re.match(r"^obj_\d+$", o["name"]):
                print(f"Object pos: ({o['x']:.3f}, {o['y']:.3f}, {o['z']:.3f}) ctx={o.get('fixture_context')}")
                break
    except:
        pass

    if result:
        print("\nSUCCESS: Object placed in sink!")
    else:
        print("\nFAILURE: Object not in sink")


main()


# ============================================================================
# USER CODE ENDS HERE
# ============================================================================

# Cleanup (disconnect backends)
async def cleanup():
    await franka_backend.disconnect()
    await base_backend.disconnect()
    await gripper_backend.disconnect()
    await mocap_backend.disconnect()

asyncio.run(cleanup())
