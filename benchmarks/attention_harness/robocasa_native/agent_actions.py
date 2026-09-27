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


class AgentServerActionError(RuntimeError):
    pass


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
        self.holder = holder
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._transport = transport or self._http_json
        self._cancellation_checked = False
        self._episode_deadline: float | None = None
        self.cancellation_receipts: list[dict[str, str]] = []
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
        del tolerance, max_steps  # The existing ArmAPI owns convergence.
        values = [_finite(value) for value in (x, y, z)]
        self._submit(
            "from robot_sdk import arm\n"
            + "arm.move_to_pose(x={!r}, y={!r}, z={!r})\n".format(*values)
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

    def _submit(self, code: str) -> None:
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
    return reason


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("action coordinates must be finite numbers")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("action coordinates must be finite numbers")
    return converted
