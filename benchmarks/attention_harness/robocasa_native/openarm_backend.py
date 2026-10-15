"""SDK actions route exclusively to the attested OpenArm kitchen service."""
from __future__ import annotations

import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from tidybot_sdk import CapabilityNotAvailableError
from ..core.control import EmergencyInterrupt
from .robot_config import RobotSelection
from .agent_actions import ReadOnlyArmPlanRejected


class OpenArmActionBackend:
    control_frame = "world"

    def __init__(self, base_url, *, execution_arm="left", transport=None,
                 timeout_seconds=90., interrupt_check=None):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise ValueError("OpenArm SDK requires an isolated loopback service URL")
        if execution_arm == "both" and not getattr(self,"_supports_bimanual",False):
            raise ValueError("Legacy OpenArm backend requires one execution arm")
        self.selection = RobotSelection("OpenArm", execution_arm)
        self.base_url = base_url
        self._transport = transport or self._http
        self.timeout_seconds = timeout_seconds
        self.interrupt_check = interrupt_check
        self.deadline = None
        self.action_receipts = []
        self.cancellation_receipts = []
        self.planned_motion_receipts = []
        self.plan_query_receipts = []
        health = self._call("GET", "/health")
        if health.get("ready") is not True or health.get("robot") != self.selection.artifact():
            raise RuntimeError("OpenArm SDK service identity does not match locked selection")
        self.contact_grasp = "gripper_contact_hold" in health.get("capabilities", [])

    def _http(self, method, path, body, timeout):
        request = urllib.request.Request(self.base_url + path, method=method,
            data=None if body is None else json.dumps(body, allow_nan=False).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read() if path == "/observation" else json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenArm HTTP {exc.code}: {detail}") from exc

    def _call(self, method, path, body=None, timeout=None):
        if method=='POST' and (path.startswith(('/plan','/trajectory')) or path in {'/actions','/base/actions','/grasp'}):
            from curobo_v2.native_session import NativeV2Service
            from curobo_v2.deployment import retired_entry
            owner=getattr(self,'owner',None)
            if (not isinstance(owner,NativeV2Service) or owner.url!=self.base_url
                    or owner.process is None or owner.process.poll() is not None):
                retired_entry('unmanaged OpenArm SDK planning/control transport')
        if self.interrupt_check is not None and self.interrupt_check():
            self.stop()
            raise EmergencyInterrupt("operator cancelled OpenArm query")
        remaining = self.timeout_seconds if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            self.stop()
            raise TimeoutError("OpenArm episode deadline elapsed; stop barrier committed")
        return self._transport(method, path, body, min(remaining, timeout or self.timeout_seconds))

    def set_episode_deadline(self, deadline):
        if not math.isfinite(deadline):
            raise ValueError("episode deadline must be finite")
        self.deadline = deadline

    def set_interrupt_check(self, check):
        self.interrupt_check = check

    def _state(self):
        state = self._call("GET", "/state")
        if state.get("robot") != self.selection.artifact():
            raise RuntimeError("OpenArm identity changed during run")
        return state

    def observe(self):
        data = self._call("GET", "/observation")
        with np.load(io.BytesIO(data), allow_pickle=False) as observation:
            state = json.loads(str(observation["state_json"]))
            if (state.get("robot") != self.selection.artifact() or
                    state["total_steps"] != int(observation["total_steps"]) or
                    state["epoch"] != int(observation["epoch"])):
                raise RuntimeError("OpenArm observation identity or physical step mismatch")
            pose = np.asarray(state["tcp_world"][self.selection.execution_arm], float)
            return {"robot0_eef_pos": pose[:3, 3], "robot0_eef_rot_mat": pose[:3, :3],
                    "robot0_joint_pos": np.asarray(state[self.selection.execution_arm]["qpos"]),
                    "left_joint_pos": np.asarray(state["left"]["qpos"]),
                    "right_joint_pos": np.asarray(state["right"]["qpos"]),
                    "robot0_gripper_qpos": np.array([state[self.selection.execution_arm + "_gripper"]["driver_position_m"]]),
                    "base_camera_image": observation["rgb"].copy(),
                    "base_camera_depth": observation["depth_m"].copy(),
                    "base_camera_intrinsics": observation["K"].copy(),
                    "base_camera_pose_mat": observation["T_world_optical"].copy(),
                    "physical_step": np.array(int(observation["total_steps"])),
                    "epoch": np.array(int(observation["epoch"]))}

    def find_objects(self, target_names=None, camera_names=None):
        if camera_names is not None and set(camera_names) - {"base_camera"}:
            raise CapabilityNotAvailableError("OpenArm camera is base_camera")
        result = self._call("POST", "/perceive", {})
        if result.get("robot") != self.selection.artifact() or result.get("perception_mode") != "sim_gt":
            raise RuntimeError("OpenArm perception identity or frame attestation failed")
        return [item for item in result["objects"] if target_names is None or item["name"] in target_names]

    def plan_arm_to_position(self, x, y, z):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in [x, y, z]):
            raise ValueError("OpenArm position requires three finite world coordinates")
        plan = self._call("POST", "/plan", {"position": [x, y, z]})
        if plan.get("robot") != self.selection.artifact():
            raise RuntimeError("OpenArm plan lacks locked robot identity")
        if plan.get("reachable") is not False and not plan.get("collision_check", {}).get("collision_free"):
            raise RuntimeError("OpenArm plan lacks robot identity or dual-arm collision attestation")
        self.plan_query_receipts.append(plan)
        return plan

    def stop(self):
        # Stop is a priority barrier and remains available after the episode deadline.
        result = self._transport("POST", "/stop", {}, 10.)
        if result.get("robot") != self.selection.artifact():
            raise RuntimeError("OpenArm stop identity mismatch")
        self.cancellation_receipts.append(result)
        return result

    def _wait_action(self, receipt):
        action_id = receipt["id"]
        deadline = min(time.monotonic() + self.timeout_seconds, self.deadline or float("inf"))
        while time.monotonic() < deadline:
            if self.interrupt_check is not None and self.interrupt_check():
                self.stop()
                raise EmergencyInterrupt("operator cancelled OpenArm action")
            state = self._call("GET", "/actions/" + urllib.parse.quote(action_id, safe=""))
            if state["status"] not in {"running", "queued"}:
                self.action_receipts.append(state)
                if state["status"] != "completed":
                    raise RuntimeError("OpenArm action failed: " + json.dumps(state))
                return state
            time.sleep(.05)
        self.stop()
        raise TimeoutError("OpenArm action timed out and stop barrier was committed")

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                or not math.isfinite(tolerance) or tolerance <= 0
                or isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1):
            raise ValueError("invalid OpenArm motion tolerance or step budget")
        plan = self.plan_arm_to_position(x, y, z)
        if plan.get("reachable") is False:
            raise ReadOnlyArmPlanRejected()
        state = self._state()
        if state["epoch"] != plan["epoch"]:
            raise RuntimeError("OpenArm plan belongs to an earlier reset/stop epoch")
        target = np.asarray(plan["target_qpos"])
        names = state["joint_order"]
        side = self.selection.execution_arm
        selected = [names.index(f"openarm_{side}_joint{i}") for i in range(1, 8)]
        start = np.asarray(state[side]["qpos"])
        points = []
        for waypoint in plan.get("joint_waypoints", [target]):
            goal = np.asarray(waypoint)[selected]
            segments = max(1, math.ceil(float(np.max(abs(goal - start))) / 1.0))
            points.extend(start + (goal - start) * i / segments for i in range(1, segments + 1))
            start = goal
        if len(points) > max_steps:
            raise RuntimeError("OpenArm motion exceeds caller step budget before execution")
        for point in points:
            receipt = self._call("POST", "/actions", {side: point.tolist(), "epoch": state["epoch"]})
            self._wait_action(receipt)
        actual = np.asarray(self._state()["tcp_world"][side])[:3, 3]
        error = float(np.linalg.norm(actual - [x, y, z]))
        self.planned_motion_receipts.append({"plan": plan, "final_tcp_error_m": error})
        if error > tolerance:
            raise RuntimeError(f"OpenArm TCP convergence error {error}m exceeds {tolerance}m")

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        if len(rotation_delta) != 3 or any(float(v) != 0 for v in rotation_delta):
            raise CapabilityNotAvailableError("OpenArm rotation-delta control is not implemented")
        pose = np.asarray(self._state()["tcp_world"][self.selection.execution_arm])
        target = pose[:3, 3] + np.asarray([dx, dy, dz])
        self.move_arm_to_position(*target.tolist(), tolerance=.004, max_steps=100)

    def set_gripper(self, value, *, settle_steps):
        if value not in {-1., 1.} or isinstance(value, bool):
            raise ValueError("OpenArm gripper SDK requires open(-1) or close(1)")
        if isinstance(settle_steps, bool) or not isinstance(settle_steps, int) or settle_steps < 0:
            raise ValueError("invalid gripper settle steps")
        state = self._state()
        side = self.selection.execution_arm
        # The accepted model uses per-finger travel, not Panda jaw-width parameters.
        limits = state[side + "_gripper"]["limits_m"]
        goal = float(limits[1] if value < 0 else limits[0])
        receipt = (self._call("POST", "/grasp", {"epoch": state["epoch"]}) if value > 0 and self.contact_grasp
                   else self._call("POST", "/actions", {side + "_gripper": goal, "epoch": state["epoch"]}))
        return self._wait_action(receipt)

    def move_base_delta(self, *args, **kwargs):
        raise CapabilityNotAvailableError("OpenArm has a fixed mount; base motion is unavailable")
