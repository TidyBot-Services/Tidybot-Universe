"""RoboCasa action adapter backed by the existing TidyBot agent_server.

The simulator and agent_server are separate services. This adapter submits
one bounded, SDK-only action at a time; it never submits evaluator calls.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import Any, Callable

import numpy as np


JsonTransport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


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
        self._submit(f"from robot_sdk import gripper\ngripper.{operation}()\n")

    def _submit(self, code: str) -> None:
        submitted = self._call(
            "POST", "/code/submit",
            {"code": code, "holder": self.holder,
             "timeout": self.timeout_seconds, "reset_env": False},
        )
        job_id = submitted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise AgentServerActionError(f"agent_server rejected SDK action: {submitted}")
        deadline = time.monotonic() + self.timeout_seconds + 30.0
        while time.monotonic() < deadline:
            job = self._call("GET", f"/code/jobs/{job_id}")
            if job.get("status") in {"completed", "failed"}:
                result = job.get("result") or {}
                if job["status"] != "completed" or result.get("exit_code") != 0:
                    raise AgentServerActionError(
                        str(job.get("error") or result.get("error") or result.get("stderr")
                            or f"SDK action job {job_id} failed")
                    )
                return
            time.sleep(self.poll_seconds)
        raise AgentServerActionError(f"SDK action job {job_id} timed out")

    def _call(self, method: str, path: str, payload=None) -> dict[str, Any]:
        response = self._transport(method, self.base_url + path, payload, 20.0)
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


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("action coordinates must be finite numbers")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("action coordinates must be finite numbers")
    return converted
