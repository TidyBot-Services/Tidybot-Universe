"""RoboCasa action adapter backed by the existing TidyBot agent_server.

The simulator and agent_server are separate services. This adapter submits
one bounded, SDK-only action at a time; it never submits evaluator calls.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable
from ..core.control import EmergencyInterrupt, raise_if_interrupted

import numpy as np


JsonTransport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]
_ARM_NONCONVERGENCE = re.compile(
    r"robot_sdk\.arm\.ArmError: Timeout: arm did not converge "
    r"\(error=([0-9]+(?:\.[0-9]+)?) m\)"
)
_WB_FAILURE = re.compile(r"robot_sdk\.wb\.WholeBodyError: ([^\r\n]{1,160})")
_NO_MOTION_PLAN_REJECTION = re.compile(
    r"robot_sdk\.wb\.PlanningRejectedWithoutMotion: Planning failed: curobo_no_trajectory(?:\r?\n|$)"
)


class AgentServerActionError(RuntimeError):
    pass


class ReadOnlyArmPlanRejected(RuntimeError):
    """A one-call SDK job rejected planning before trajectory execution."""

    def __init__(self):
        super().__init__("read_only_arm_plan_rejected")


class AgentServerActionBackend:
    control_frame = "arm_base"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        simulator_attested: bool,
        holder: str = "attentionbench-sim-gt",
        timeout_seconds: float = 90.0,
        poll_seconds: float = 0.25,
        sim_url: str | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        # The current agent_server /health endpoint does not attest whether
        # it is connected to a simulator or physical hardware. Fail closed
        # unless the caller explicitly confirms this endpoint is simulator-only.
        if simulator_attested is not True:
            raise ValueError("sim_gt actions require simulator-only agent attestation")
        self.simulator_attested = True
        if timeout_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("action timeout and poll interval must be positive")
        self.base_url = base_url.rstrip("/")
        if sim_url is not None and not sim_url.startswith("http://127.0.0.1:"):
            raise ValueError("planned arm execution requires a loopback simulator URL")
        self.sim_url = None if sim_url is None else sim_url.rstrip("/")
        self.holder = holder
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._transport = transport or self._http_json
        self._cancellation_checked = False
        self._episode_deadline: float | None = None
        self.cancellation_receipts: list[dict[str, str]] = []
        self.planned_motion_receipts: list[dict[str, Any]] = []
        self.plan_query_receipts: list[dict[str, Any]] = []
        self._interrupt_check: Callable[[], bool] | None = None

    def set_interrupt_check(self, check: Callable[[], bool]) -> None:
        self._interrupt_check = check

    def set_episode_deadline(self, deadline: float) -> None:
        """Bound each submitted action by the remaining episode wall time."""
        if not math.isfinite(deadline):
            raise ValueError("episode deadline must be finite")
        self._episode_deadline = deadline

    def assert_cancellation_available(self) -> None:
        """Fail before action submission when the service is too old to stop jobs."""
        if self._cancellation_checked:
            return
        capabilities = self._call("GET", "/code/capabilities")
        if (capabilities.get("job_cancellation") is not True
                or capabilities.get("cancel_auth") != "per_job_token"):
            raise AgentServerActionError("agent_server lacks per-job cancellation capability")
        self._cancellation_checked = True

    def observe(self) -> dict[str, np.ndarray]:
        state = self._call("GET", "/state")
        arm = state.get("arm")
        gripper = state.get("gripper")
        if not isinstance(arm, dict) or not isinstance(gripper, dict):
            raise AgentServerActionError("/state lacks arm or gripper state")
        joints = np.asarray(arm.get("q"), dtype=np.float64)
        ee_pose = np.asarray(arm.get("ee_pose"), dtype=np.float64)
        if joints.shape != (7,) or ee_pose.shape != (16,):
            raise AgentServerActionError("/state has invalid arm pose")
        eef_pos = ee_pose.reshape((4, 4), order="F")[:3, 3]
        width_mm = gripper.get("position_mm")
        if not np.isfinite(joints).all() or not np.isfinite(eef_pos).all():
            raise AgentServerActionError("/state contains non-finite arm data")
        result = {
            "robot0_joint_pos": joints,
            "robot0_eef_pos": eef_pos,
        }
        base = state.get("base")
        if isinstance(base, dict):
            base_pose = np.asarray(base.get("pose", []), dtype=np.float64)
            if base_pose.shape == (3,) and np.isfinite(base_pose).all():
                result["robot0_base_pose"] = base_pose
        if isinstance(width_mm, (int, float)) and math.isfinite(width_mm):
            result["robot0_gripper_width"] = np.asarray(
                [float(width_mm) / 1000.0], dtype=np.float64
            )
        return result

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        values = [_finite(value) for value in (dx, dy, dz, *rotation_delta)]
        self._submit(
            "from robot_sdk import arm\n"
            + "arm.move_delta(dx={!r}, dy={!r}, dz={!r}, droll={!r}, dpitch={!r}, dyaw={!r}, frame='base')\n".format(*values)
        )

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        del max_steps  # The existing Agent Server owns its trajectory cadence.
        values = [_finite(value) for value in (x, y, z)]
        if self.sim_url is not None:
            current = self.observe()["robot0_eef_pos"]
            if self._requires_planned_move(values, current):
                self._move_arm_to_position_planned(values, tolerance=_finite(tolerance))
                return
        self._submit(
            "from robot_sdk import arm\n"
            + "arm.move_to_pose(x={!r}, y={!r}, z={!r})\n".format(*values)
        )

    @staticmethod
    def _requires_planned_move(target: list[float], current: np.ndarray) -> bool:
        # Both counter descent and long lateral approach can stall the
        # Cartesian controller. Seeds 110 and 111 hit the latter path.
        planar_distance = float(np.linalg.norm(
            np.asarray(target[:2]) - current[:2]))
        return target[2] < float(current[2]) - 0.04 or planar_distance > 0.10

    def plan_arm_to_position(self, x, y, z) -> dict[str, Any]:
        """Query the simulator planner without submitting an Agent motion job."""
        if self.sim_url is None:
            raise AgentServerActionError("simulator-only arm planning is unavailable")
        target = [_finite(value) for value in (x, y, z)]
        current = self.observe()["robot0_eef_pos"]
        if not self._requires_planned_move(target, current):
            result = {"planning_required": False, "reachable": True,
                      "status": "cartesian_controller"}
            self.plan_query_receipts.append({"target_arm_base_m": target, **result})
            return result
        _, ee_quat, requested_world, offset, planner_target = self._planner_pose(target)
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise TimeoutError("RoboCasa episode deadline reached before arm plan query")
        plan = self._transport(
            "POST", self.sim_url + "/plan",
            {"target_pose": planner_target, "target_quat": ee_quat,
             "mask": "arm_only"}, min(60.0, remaining),
        )
        if not isinstance(plan, dict) or not isinstance(plan.get("status"), str):
            raise AgentServerActionError("simulator returned an invalid arm plan result")
        status = plan["status"]
        count = plan.get("waypoint_count")
        if status == "success":
            if not isinstance(count, int) or count < 1 or not isinstance(
                plan.get("trajectory"), list
            ) or len(plan["trajectory"]) != count:
                raise AgentServerActionError("simulator returned an incomplete arm plan")
        elif status != "curobo_no_trajectory" or count != 0:
            raise AgentServerActionError(f"arm plan query failed: {status}")
        result = {"planning_required": True, "reachable": status == "success",
                  "status": status}
        self.plan_query_receipts.append({
            "target_arm_base_m": target,
            "requested_eef_world_m": requested_world,
            "planner_ee_link_world_m": planner_target,
            "tool_offset_world_m": offset,
            **result,
        })
        return result

    def _planner_pose(
        self, target: list[float],
    ) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
        assert self.sim_url is not None
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise TimeoutError("RoboCasa episode deadline reached before arm planning")
        frame = self._transport("GET", self.sim_url + "/robot/frame", None,
                                min(10.0, remaining))
        if not isinstance(frame, dict):
            raise AgentServerActionError("simulator returned an invalid robot frame")
        arm_base = _finite_vector(frame.get("arm_base_world_pos"), 3)
        arm_quat = _finite_vector(frame.get("arm_base_world_quat"), 4)
        ee_quat = _finite_vector(frame.get("ee_world_quat"), 4)
        if abs(sum(value * value for value in arm_quat) - 1.0) > 0.02 or abs(
            sum(value * value for value in ee_quat) - 1.0
        ) > 0.02:
            raise AgentServerActionError("simulator returned a non-unit robot quaternion")
        requested_world = [a + b for a, b in zip(
            arm_base, _rotate_vector(arm_quat, target))]
        # cuRobo's ee_link is 0.100 m from panda_hand; public eef is 0.145 m.
        offset = _rotate_vector(ee_quat, [0.0, 0.0, 0.045])
        planner_target = [a - b for a, b in zip(requested_world, offset)]
        # The cuRobo URDF rotates panda_hand / ee_link -45 degrees around Z
        # from panda_link8. The simulator's public eef has no such rotation.
        # Without this conversion each planned action rotates the public eef
        # about +45 degrees and changes the next tool-offset direction.
        half_angle = math.pi / 8.0
        planner_quat = _quat_multiply(
            ee_quat, [math.cos(half_angle), 0.0, 0.0, -math.sin(half_angle)]
        )
        return arm_quat, planner_quat, requested_world, offset, planner_target

    def _move_arm_to_position_planned(self, target: list[float], *, tolerance: float) -> None:
        if tolerance <= 0:
            raise ValueError("arm target tolerance must be positive")
        assert self.sim_url is not None
        _, ee_quat, requested_world, offset, planner_target = self._planner_pose(target)
        receipt = {"target_arm_base_m": target, "requested_eef_world_m": requested_world,
                   "planner_ee_link_world_m": planner_target,
                   "tool_offset_world_m": offset, "tolerance_m": tolerance}
        self.planned_motion_receipts.append(receipt)
        self._submit(
            "from robot_sdk import wb\n"
            + ("wb.move_to_pose(x={!r}, y={!r}, z={!r}, quat={!r}, "
               "mask='arm_only', timeout=30.0)\n").format(*planner_target, ee_quat),
            allow_pre_execution_rejection=True,
        )
        actual = self.observe()["robot0_eef_pos"]
        error = float(np.linalg.norm(actual - np.asarray(target, dtype=np.float64)))
        receipt["initial_residual_m"] = error
        receipt["correction_attempted"] = False
        receipt["corrections"] = []
        for _ in range(3):
            if not tolerance < error <= 0.02:
                break
            # A successful joint plan can stop short in public EEF space.
            # Replan by the measured small endpoint gap. Stop if a correction
            # stops improving; keep a hard cap on extra arm jobs.
            receipt["correction_attempted"] = True
            previous_error = error
            # The public target is in the arm-base frame. The base can move
            # during execution, so the old world target is no longer valid.
            # Re-read the frame before planning the small residual move.
            corrected_arm_quat, corrected_ee_quat, corrected_requested_world, corrected_offset, refreshed_planner_target = (
                self._planner_pose(target)
            )
            residual_world = _rotate_vector(
                corrected_arm_quat,
                (np.asarray(target, dtype=np.float64) - actual).tolist(),
            )
            # The fresh arm-base pose already maps the requested local target
            # into the current world frame. Adding the entire measured gap a
            # second time sent the archived correction past that target.
            corrected_planner_target = refreshed_planner_target
            receipt["correction_world_m"] = [
                b - a for a, b in zip(planner_target, corrected_planner_target)
            ]
            receipt["residual_world_m"] = residual_world
            receipt["corrected_requested_eef_world_m"] = corrected_requested_world
            receipt["corrected_tool_offset_world_m"] = corrected_offset
            receipt["corrected_planner_ee_link_world_m"] = corrected_planner_target
            self._submit(
                "from robot_sdk import wb\n"
                + ("wb.move_to_pose(x={!r}, y={!r}, z={!r}, quat={!r}, "
                   "mask='arm_only', timeout=30.0)\n").format(
                       *corrected_planner_target, corrected_ee_quat
                   )
            )
            actual = self.observe()["robot0_eef_pos"]
            error = float(np.linalg.norm(actual - np.asarray(target, dtype=np.float64)))
            receipt["corrections"].append({
                "target_ee_link_world_m": corrected_planner_target,
                "residual_m": error,
            })
            if error >= previous_error - 0.0005:
                break
        receipt["actual_eef_arm_base_m"] = actual.tolist()
        receipt["residual_m"] = error
        if error > tolerance:
            raise AgentServerActionError(
                f"planned arm residual {error:.6f} m exceeds tolerance {tolerance:.6f} m"
            )

    def set_gripper(self, command, *, settle_steps):
        if command not in (-1.0, 1.0) or settle_steps < 1:
            raise ValueError("invalid gripper command")
        operation = "open" if command < 0 else "close"
        # Low-speed close is confined to the simulator-backed benchmark path.
        # The Robotiq bridge translates it into settled incremental targets,
        # avoiding a single full-aperture impulse on small RoboCasa objects.
        call = "gripper.open()" if command < 0 else "gripper.close(speed=64)"
        self._submit(f"from robot_sdk import gripper\n{call}\n")

    def move_base_delta(self, dx, dy, dtheta, *, frame):
        if frame not in {"local", "global"}:
            raise ValueError("base frame must be local or global")
        values = [_finite(value) for value in (dx, dy, dtheta)]
        if max(abs(values[0]), abs(values[1])) > 0.3 or abs(values[2]) > 0.5:
            raise ValueError("base step exceeds 0.3 m / 0.5 rad safety bounds")
        self._submit(
            "from robot_sdk import base\n"
            + "base.move_delta(dx={!r}, dy={!r}, dtheta={!r}, frame={!r})\n".format(
                *values, frame)
        )

    def _submit(self, code: str, *, allow_pre_execution_rejection: bool = False) -> None:
        raise_if_interrupted(self._interrupt_check)
        self.assert_cancellation_available()
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise TimeoutError("RoboCasa episode deadline reached before action submission")
        job_timeout = min(self.timeout_seconds, remaining)
        submitted = self._call(
            "POST", "/code/submit",
            {"code": code, "holder": self.holder,
             "timeout": job_timeout, "reset_env": False},
            timeout=min(20.0, remaining),
        )
        job_id = submitted.get("job_id")
        cancel_token = submitted.get("cancel_token")
        if not isinstance(job_id, str) or not job_id or not isinstance(cancel_token, str) or not cancel_token:
            raise AgentServerActionError("agent_server did not return a cancellable job identity")
        deadline = min(time.monotonic() + self.timeout_seconds,
                       self._episode_deadline if self._episode_deadline is not None else math.inf)
        try:
            while True:
                if self._interrupt_check is not None and self._interrupt_check():
                    self._cancel_and_confirm(job_id, cancel_token)
                    raise EmergencyInterrupt("operator interrupted agent_server action job")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"SDK action job {job_id} reached its deadline")
                try:
                    job = self._call("GET", f"/code/jobs/{job_id}", timeout=min(20.0, remaining))
                except AgentServerActionError:
                    self._cancel_and_confirm(job_id, cancel_token)
                    raise
                if job.get("status") not in {"completed", "failed", "cancelled"}:
                    time.sleep(min(self.poll_seconds, remaining))
                    continue
                result = job.get("result") or {}
                if job["status"] != "completed" or result.get("exit_code") != 0:
                    # This exception is only used for the first planned arm
                    # job, whose submitted code contains a single SDK call.
                    # The SDK raises it before _execute_trajectory. Require
                    # both the typed traceback and absence of its execution
                    # marker; all ambiguous failures stay unknown to Safety.
                    if (allow_pre_execution_rejection and job["status"] == "failed"
                            and isinstance(result, dict) and result.get("exit_code") == 1):
                        stderr, stdout = result.get("stderr"), result.get("stdout")
                        if (isinstance(stderr, str) and isinstance(stdout, str)
                                and _NO_MOTION_PLAN_REJECTION.search(stderr)
                                and "[wb] Executing trajectory" not in stdout):
                            raise ReadOnlyArmPlanRejected()
                    raise AgentServerActionError(_job_failure_detail(job_id, job, result))
                return
        except TimeoutError:
            self._cancel_and_confirm(job_id, cancel_token)
            raise

    def _cancel_and_confirm(self, job_id: str, cancel_token: str) -> None:
        """Never report a clean timeout while an action may still be executing."""
        try:
            self._call("POST", f"/code/jobs/{job_id}/cancel",
                       {"cancel_token": cancel_token}, timeout=5.0,
                       ignore_episode_deadline=True)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                job = self._call("GET", f"/code/jobs/{job_id}", timeout=2.0,
                                 ignore_episode_deadline=True)
                status = job.get("status")
                if status == "cancelled":
                    result = job.get("result") or {}
                    self.cancellation_receipts.append({
                        "job_id": job_id,
                        "job_status": "cancelled",
                        "execution_status": str(result.get("status", "unknown")),
                    })
                    return
                if status in {"completed", "failed"}:
                    raise AgentServerActionError(
                        f"SDK action job {job_id} ended as {status} after its deadline"
                    )
                time.sleep(max(0.0, min(self.poll_seconds, deadline - time.monotonic())))
        except AgentServerActionError:
            raise
        raise AgentServerActionError(
            f"could not confirm cancellation of SDK action job {job_id}"
        )

    def _remaining_seconds(self) -> float:
        if self._episode_deadline is None:
            return self.timeout_seconds
        return self._episode_deadline - time.monotonic()

    def _call(self, method: str, path: str, payload=None, *, timeout: float = 20.0,
              ignore_episode_deadline: bool = False) -> dict[str, Any]:
        if not ignore_episode_deadline and self._episode_deadline is not None:
            remaining = self._remaining_seconds()
            if remaining <= 0:
                raise TimeoutError("RoboCasa episode deadline reached")
            timeout = min(timeout, remaining)
        response = self._transport(method, self.base_url + path, payload, timeout)
        if not isinstance(response, dict):
            raise AgentServerActionError(f"{path} returned non-object JSON")
        return response

    @staticmethod
    def _http_json(method, url, payload, timeout):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise AgentServerActionError(f"{method} {url} failed: {exc}") from exc


def _job_failure_detail(job_id: str, job: dict[str, Any], result: dict[str, Any]) -> str:
    """Surface a bounded SDK reason without treating a failed motion as undone.

    The job's generic error (often "Process exited with code 1") masks the
    subprocess traceback. Only the known arm non-convergence signature is
    projected; arbitrary stderr is not copied into policy or Eval traces.
    """
    reason = str(job.get("error") or result.get("error") or f"SDK action job {job_id} failed")
    stderr = result.get("stderr")
    if isinstance(stderr, str):
        match = _ARM_NONCONVERGENCE.search(stderr)
        if match:
            return (
                f"{reason}; sdk_arm_nonconvergence_error_m={match.group(1)}; "
                "action outcome remains uncertain (partial motion possible)"
            )
        match = _WB_FAILURE.search(stderr)
        if match:
            return (
                f"{reason}; sdk_whole_body_error={match.group(1)}; "
                "action outcome remains uncertain (partial motion possible)"
            )
    return reason


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("action coordinates must be finite numbers")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("action coordinates must be finite numbers")
    return converted


def _finite_vector(value: Any, size: int) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise AgentServerActionError("simulator returned an invalid robot frame vector")
    try:
        return [_finite(item) for item in value]
    except ValueError as exc:
        raise AgentServerActionError("simulator returned a non-finite robot frame") from exc


def _rotate_vector(quat: list[float], vector: list[float]) -> list[float]:
    """Rotate a 3-vector by a unit wxyz quaternion without an extra dependency."""
    w, x, y, z = quat
    vx, vy, vz = vector
    cross = (y * vz - z * vy, z * vx - x * vz, x * vy - y * vx)
    cross2 = (y * cross[2] - z * cross[1],
              z * cross[0] - x * cross[2],
              x * cross[1] - y * cross[0])
    return [value + 2.0 * (w * first + second)
            for value, first, second in zip(vector, cross, cross2)]


def _quat_multiply(a: list[float], b: list[float]) -> list[float]:
    """Multiply two wxyz quaternions."""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return [w1*w2 - x1*x2 - y1*y2 - z1*z2,
            w1*x2 + x1*w2 + y1*z2 - z1*y2,
            w1*y2 - x1*z2 + y1*w2 + z1*x2,
            w1*z2 + x1*y2 - y1*x2 + z1*w2]
