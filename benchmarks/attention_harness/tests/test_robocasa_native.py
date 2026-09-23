from __future__ import annotations

import pytest

from benchmarks.attention_harness.robocasa_native.client import (
    RobocasaServiceError,
    RobocasaSimClient,
)
from benchmarks.attention_harness.robocasa_native.probe import PrivilegedRobocasaProbe


class FakeTransport:
    def __init__(self) -> None:
        self.success = False
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        path = "/" + url.split("/", 3)[-1]
        self.calls.append((method, path, payload))
        if path == "/task/info":
            return {
                "task": "RoboCasa-Pn-P-Counter-To-Sink-v0",
                "lang": "pick the mug from the counter and place it in the sink",
            }
        if path == "/reset":
            self.success = False
            return {"status": "ok"}
        if path == "/perceive":
            return {
                "objects": [{"name": "mug", "x": 1.0, "y": 2.0, "z": 3.0}],
                "cameras": ["base_camera"],
                "oracle_state": "must not be copied",
            }
        if path == "/task/success":
            return {
                "success": self.success,
                "debug": {
                    "obj_pos": [0.0, 0.0, 0.0],
                    "sink": {"shifted": [1.0, 2.0, 3.0], "pos": [1.0, 2.0, 2.5]},
                },
            }
        if path == "/teleport":
            self.success = True
            return {"status": "ok"}
        raise AssertionError(path)


def test_public_client_strips_evaluator_debug_and_perception_envelope() -> None:
    transport = FakeTransport()
    client = RobocasaSimClient("counter_to_sink", transport=transport)
    observation = client.reset(101)
    artifact = observation.artifact()
    assert artifact["objects"] == [{"name": "mug", "x": 1.0, "y": 2.0, "z": 3.0}]
    assert "oracle_state" not in artifact
    assert client.native_success() is False
    assert not hasattr(client, "teleport")


def test_privileged_probe_is_separate_from_public_client() -> None:
    transport = FakeTransport()
    client = RobocasaSimClient("counter_to_sink", transport=transport)
    client.reset(101)
    probe = PrivilegedRobocasaProbe(
        "counter_to_sink", base_url=client.base_url, transport=transport
    )
    probe.force_reference_success()
    assert client.native_success() is True
    teleport = next(call for call in transport.calls if call[1] == "/teleport")
    assert teleport[2]["position"] == [1.0, 2.0, 2.5]


def test_wrong_service_task_is_rejected() -> None:
    transport = FakeTransport()
    client = RobocasaSimClient("counter_to_cab", transport=transport)
    with pytest.raises(RobocasaServiceError, match="does not match"):
        client.assert_task()
