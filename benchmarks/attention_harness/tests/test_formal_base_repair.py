"""Offline controller-semantics and fail-closed checks for the new candidates."""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest

from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy


POLICIES = Path(__file__).resolve().parents[1] / "protocol/v2/review_packages/formal_repair_v3_2026-09-30"


def _run(monkeypatch, suite: str, sdk: object, context: dict) -> None:
    module = types.ModuleType("robot_sdk")
    for name in ("sensors", "base", "arm", "gripper"):
        if hasattr(sdk, name):
            setattr(module, name, getattr(sdk, name))
    monkeypatch.setitem(sys.modules, "robot_sdk", module)
    path = POLICIES / suite / "policy.py"
    validate_generated_policy(path.read_text())
    runpy.run_path(str(path), init_globals={"context": context})


def test_robosuite_slow_osc_progress_uses_local_controller(monkeypatch):
    class SDK:
        def __init__(self):
            self.position = [-0.087, -0.001, 1.027]
            self.targets = []
            self.sensors = self
            self.arm = self
            self.gripper = self

        def find_objects(self, names):
            assert names == ["cube"]
            return [{"source": "sim_gt", "position": [-0.009, -0.019, 0.830]}]

        def get_observation(self):
            return {"robot0_eef_pos": list(self.position)}

        def move_delta(self, *args, **kwargs):
            raise AssertionError("single-tick move_delta must not be used")

        def move_to_position(self, x, y, z, *, tolerance, max_steps):
            goal = (x, y, z)
            distance = sum((a - b) ** 2 for a, b in zip(goal, self.position)) ** 0.5
            assert distance <= 0.181 and max_steps <= 80
            self.targets.append(goal)
            # Slow but convergent synthetic OSC, with less than 15 mm / tick.
            for _ in range(max_steps):
                error = [goal[i] - self.position[i] for i in range(3)]
                norm = sum(x * x for x in error) ** 0.5
                if norm < tolerance:
                    return
                for i in range(3):
                    self.position[i] += error[i] * min(1.0, 0.012 / norm)
            raise TimeoutError("synthetic controller failed to converge")

        def open(self):
            pass

        def close(self):
            pass

    sdk = SDK()
    _run(monkeypatch, "robosuite_cube_lift", sdk, {"task_id": "cube_lift"})
    assert len(sdk.targets) >= 3
    assert sdk.position[2] > 1.0


def test_robocasa_aligns_base_before_arm_and_stops_on_unknown(monkeypatch):
    class UnknownMotion(RuntimeError):
        pass

    class SDK:
        def __init__(self):
            self.base_steps = []
            self.arm_calls = 0
            self.find_queries = []
            self.sensors = self
            self.base = self
            self.arm = self
            self.gripper = self

        def find_objects(self, names=None):
            self.find_queries.append(names)
            if names is None:
                return [{"name": "boxed_drink_0", "source": "sim_gt", "position": [0.844, -0.597, 0.500]},
                        {"name": "boxed_drink_1", "source": "sim_gt", "position": [0.7, 0.2, 0.5]}]
            assert names == ["boxed_drink_0"]
            return [{"name": "boxed_drink_0", "source": "sim_gt", "position": [0.844, -0.597, 0.500]}]

        def plan_to_position(self, *args, **kwargs):
            return {"reachable": True, "status": "success"}

        def move_delta(self, *args, **kwargs):
            if "frame" in kwargs:
                assert not self.arm_calls
                assert max(abs(args[0]), abs(args[1])) <= 0.15
                self.base_steps.append(args)
            else:
                self.arm_calls += 1

        def move_to_position(self, *args, **kwargs):
            self.arm_calls += 1
            raise UnknownMotion("injected partially executed arm action")

        def open(self):
            pass

        def close(self):
            raise AssertionError("policy continued after unknown arm outcome")

    sdk = SDK()
    with pytest.raises(UnknownMotion, match="partially executed"):
        _run(monkeypatch, "robocasa_counter_to_sink", sdk, {
            "task_id": "counter_to_sink",
            "language": "pick the boxed drink from the counter and place it in the sink",
        })
    assert 4 <= len(sdk.base_steps) <= 10
    assert sdk.arm_calls == 3
    assert ["boxed_drink_0"] in sdk.find_queries


def test_robocasa_missed_grasp_ends_as_native_failure(monkeypatch):
    class SDK:
        def __init__(self):
            self.sensors = self
            self.base = self
            self.arm = self
            self.gripper = self
            self.base_steps = 0
            self.open_count = 0
            self.position_targets = []
            self.arm_deltas = 0

        def find_objects(self, names=None):
            if names is None:
                return [{"name": "garlic", "source": "sim_gt", "position": [0.5, -0.2, 0.5]}]
            if names == ["spout"]:
                raise AssertionError("no sink transport after a missed grasp")
            assert names == ["garlic"]
            return [{"source": "sim_gt", "position": [0.5, -0.2, 0.5]}]

        def plan_to_position(self, *args, **kwargs):
            return {"reachable": True, "status": "success"}

        def move_delta(self, *args, **kwargs):
            if "frame" in kwargs:
                self.base_steps += 1
            else:
                self.arm_deltas += 1

        def move_to_position(self, *args, **kwargs):
            self.position_targets.append(args)

        def open(self):
            self.open_count += 1

        def close(self):
            pass

    sdk = SDK()
    _run(monkeypatch, "robocasa_counter_to_sink", sdk, {
        "task_id": "counter_to_sink",
        "language": "pick the garlic from the counter and place it in the sink",
    })
    assert sdk.base_steps == 4
    assert sdk.open_count == 2
    assert sdk.arm_deltas == 2  # wrist orientation only; no repeated lift deltas
    assert [round(value[2], 3) for value in sdk.position_targets] == [0.66, 0.54, 0.65]
