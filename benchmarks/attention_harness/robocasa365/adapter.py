"""Shared TidyBotSDK arm/gripper adapter plus explicit mobile composite extensions."""

import hashlib, json, time, uuid
import numpy as np
from tidybot_sdk import TidyBotSDK
from tidybot_sdk.contracts import CapabilityNotAvailableError
from .service import OutcomeUnknown


class Adapter:
    control_frame = "robot_base"

    def __init__(self, client, reset):
        self.client = client
        self.episode = reset["episode_id"]
        self.cap = reset["capabilities"]
        self.arm = self.cap.get("execution_arm", "right")
        self.part = self.cap.get("execution_part", self.arm)
        self.sequence = 0
        self.ledger = []
        self.grip = 0.0
        self.failed = False
        self.observation_sink = None

    def public_observation(self):
        if self.failed:
            raise RuntimeError("Adapter stopped after unknown result")
        obs = self.client.call("/observation")
        if obs["episode_id"] != self.episode or obs["sequence"] != self.sequence:
            self.failed = True
            raise OutcomeUnknown("observation identity/sequence mismatch")
        if self.ledger:
            actual = hashlib.sha256(
                json.dumps(
                    obs, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            ).hexdigest()
            if actual != self.ledger[-1].get("observation_sha256"):
                self.failed = True
                raise OutcomeUnknown("observation differs from durable action receipt")
        if self.observation_sink is not None:
            self.observation_sink(obs)
        return obs

    def observe(self):
        obs = self.public_observation()
        out = {k: np.asarray(v) for k, v in obs["robot"].items()}
        for src, dst in [
            (f"robot0_{self.arm}_eef_pos", "robot0_eef_pos"),
            (f"robot0_{self.arm}_eef_quat", "robot0_eef_quat"),
        ]:
            if src in out:
                out[dst] = out[src].copy()
        # Raw sim object values are confined to an explicit perception capability.
        return out

    def find_objects(self, target_names=None, camera_names=None):
        if camera_names is not None and camera_names != self.cap.get("camera_names", [
            "robot0_agentview_left",
            "robot0_eye_in_hand",
        ]):
            raise ValueError("camera request differs from frozen RoboCasa365 cameras")
        obs = self.public_observation()
        values = obs["perception"]["values"]
        items = []
        for k, v in values.items():
            if k.endswith("_pos") and (not target_names or k[:-4] in target_names):
                items.append(
                    {
                        "name": k[:-4],
                        "position": v,
                        "frame": "world",
                        "units": "metres",
                        "perception_mode": "sim_gt",
                        "source": "official_simulator_object_observation",
                    }
                )
        return items

    def step_parts(self, parts, steps=1, request_id=None):
        if self.failed:
            raise RuntimeError("Adapter stopped")
        grip_part = self.part + "_gripper"
        parts = {**parts, grip_part: parts.get(grip_part, [self.grip])}
        body = {
            "episode_id": self.episode,
            "request_id": request_id or uuid.uuid4().hex,
            "parts": parts,
            "steps": steps,
        }
        sha = hashlib.sha256(
            json.dumps(
                {k: body[k] for k in ("episode_id", "parts", "steps")},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        try:
            receipt = self.client.call("/step", body)
            if (
                receipt.get("status") != "completed"
                or receipt.get("request_id") != body["request_id"]
                or receipt.get("episode_id") != self.episode
                or receipt.get("request_sha256") != sha
                or receipt.get("sequence_before") != self.sequence
                or receipt.get("sequence_after") != self.sequence + steps
            ):
                raise OutcomeUnknown("unverifiable action receipt")
            self.sequence = receipt["sequence_after"]
            self.grip = parts[grip_part][0]
            self.ledger.append(receipt)
            return receipt
        except BaseException:
            self.failed = True
            raise

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        physical = np.array([dx, dy, dz, *rotation_delta], dtype=float)
        spec = self.cap["controllers"][self.part]
        lo = np.asarray(spec["output_min"])
        hi = np.asarray(spec["output_max"])
        if (
            physical.shape != (6,)
            or not np.isfinite(physical).all()
            or np.any(physical < lo)
            or np.any(physical > hi)
        ):
            raise ValueError("arm delta exceeds official m/rad envelope")
        normalized = 2 * (physical - lo) / (hi - lo) - 1
        return self.step_parts({self.part: normalized.tolist(), "base_mode": -1})

    def move_arm_to_position(self, *args, **kwargs):
        raise CapabilityNotAvailableError(
            "RoboCasa365 absolute Cartesian/IK/planning not supported"
        )

    def set_gripper(self, command, *, settle_steps):
        return self.step_parts(
            {self.part + "_gripper": [command], "base_mode": -1}, settle_steps
        )

    def normalized(self, part, values):
        return self.step_parts(
            {part: list(values), "base_mode": 1 if part == "base" else -1}
        )


class NormalizedPart:
    def __init__(self, part, backend, event_sink):
        self.part = part
        self.backend = backend
        self.sink = event_sink
        self.started = time.monotonic()

    def move_normalized(self, values):
        started = time.monotonic()
        try:
            r = self.backend.normalized(self.part, values)
            status = "completed"
            error = None
        except Exception as ex:
            self.sink(
                {
                    "timestamp": time.monotonic() - self.started,
                    "timestamp_basis": "relative",
                    "source": "robot_sdk." + self.part,
                    "event_type": "sdk.composite_command",
                    "operation": "move_normalized",
                    "arguments": {"values": values},
                    "status": "failed",
                    "error": {"type": type(ex).__name__, "message": str(ex)},
                }
            )
            raise
        self.sink(
            {
                "timestamp": time.monotonic() - self.started,
                "timestamp_basis": "relative",
                "source": "robot_sdk." + self.part,
                "event_type": "sdk.composite_command",
                "operation": "move_normalized",
                "arguments": {"values": values},
                "status": status,
                "result": {
                    "receipt_id": r["request_id"],
                    "sequence_after": r["sequence_after"],
                },
                "duration_ms": (time.monotonic() - started) * 1000,
            }
        )
        return None


def build_sdk(backend, event_sink):
    sdk = TidyBotSDK(backend, event_sink=event_sink)
    sdk.base = NormalizedPart("base", backend, event_sink)
    sdk.torso = NormalizedPart("torso", backend, event_sink)
    return sdk
