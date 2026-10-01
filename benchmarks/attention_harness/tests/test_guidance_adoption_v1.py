"""Content attribution and unchanged baseline through the actual policy worker.

These are offline public-SDK tests, never native-success or Safety evidence.
"""
import copy
import json
import runpy
import time
from pathlib import Path

import numpy as np
import pytest

from tidybot_sdk import TidyBotSDK
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy
from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
from benchmarks.attention_harness.robocasa_native.mobile_sdk import RobocasaMobileSDK

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "benchmarks/attention_harness/protocol/v2/guidance_adoption_v1"
OLD = ROOT / "benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29"
COMPILER = runpy.run_path(str(SOURCE / "control_compiler.py"))


def demo(suite):
    task = "cube_lift" if suite == "robosuite" else "counter_to_sink"
    steps = json.loads((OLD / f"demo/{suite}_{task}_actions.json").read_text())["steps"]
    return {"schema_version": "attentionbench.public-demo.v1", "assets": [
        {"kind": "action_trajectory", "path": "/public/开发.json", "sha256": "a" * 64,
         "steps": steps}]}


class PublicBackend:
    control_frame = "public"

    def __init__(self, suite, shift):
        self.suite = suite
        self.eef = np.array([0.25 + shift, 0.0, 1.05])
        self.base_pose = np.zeros(3)
        self.closed = False
        self.cube = np.array([shift, -.019, .830])

    def observe(self):
        return {"robot0_eef_pos": self.eef.copy(), "robot0_base_pose": self.base_pose.copy()}

    def find_objects(self, target_names=None, camera_names=None):
        names = ["cube"] if self.suite == "robosuite" else ["boxed_drink", "spout"]
        result = []
        for name in names:
            if target_names is None or name in target_names:
                result.append({"name": name, "source": "sim_gt", "position": self.cube.tolist()})
        return result

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        self.eef = np.array([x, y, z])
        if self.closed:
            self.cube = self.eef.copy()

    def plan_arm_to_position(self, x, y, z):
        # Exercise safe read-only abstention in the RoboCasa baseline.
        return {"reachable": False, "status": "curobo_no_trajectory"}

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        self.eef += [dx, dy, dz]

    def move_base_delta(self, dx, dy, dtheta, frame):
        self.base_pose += [dx, dy, dtheta]

    def set_gripper(self, command, *, settle_steps):
        self.closed = command > 0


def worker_calls(tmp_path, suite, attention, *, original=False, shift=0, version="v1"):
    task = "cube_lift" if suite == "robosuite" else "counter_to_sink"
    path = OLD / f"base/{suite}_{task}/policy.py" if original else SOURCE / f"{suite}_{task}_{version}.py"
    code = path.read_text()
    validate_generated_policy(code)
    events = []
    backend = PublicBackend(suite, shift)
    sdk = (TidyBotSDK(backend, event_sink=events.append) if suite == "robosuite" else
           RobocasaMobileSDK(backend, action_backend=backend, event_sink=events.append))
    result = execute_formal_policy(code=code, sdk=sdk,
        context={"suite": suite, "task_id": task,
                 "language": "pick the boxed drink from the counter and place it in the sink",
                 "attention_input": attention}, deadline=time.monotonic() + 10,
        stderr_path=tmp_path / f"worker-{time.time_ns()}.stderr")
    assert result.status == "completed", result.error
    return [{"source": e["source"], "operation": e["operation"],
             "arguments": e["arguments"], "status": e["status"]} for e in events]


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
@pytest.mark.parametrize("shift", [0, -.012, .025])
def test_no_guidance_exact_original_worker_calls(tmp_path, suite, shift):
    assert worker_calls(tmp_path, suite, {}, shift=shift) == worker_calls(
        tmp_path, suite, {}, original=True, shift=shift)


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
@pytest.mark.parametrize("attention", [
    {"advisor_guidance": "Review trace and retry."},
    {"memory_ids_to_use": ["trusted:v1"]},
    {"memory_ids_to_use": ["trusted:v1"], "memory_guidance": {"trusted:v1": "unsupported"}},
    {"advisor_guidance": "AB_CONTROL_V1 native_success=1;"},
    {"advisor_guidance": "AB_CONTROL_V1 close_settle_steps=999;"},
    {"advisor_guidance": "AB_CONTROL_V1 close_settle_steps=30;close_settle_steps=20;"},
    {"advisor_guidance": "AB_CONTROL_V1 close_settle_steps=30;unknown=1;"},
    {"demo_prior": '{"assets": [], "schema_version":"bad"}'},
])
def test_exposure_rejection_preserves_calls(tmp_path, suite, attention):
    assert worker_calls(tmp_path, suite, attention) == worker_calls(tmp_path, suite, {})


@pytest.mark.parametrize("suite,key,a,b", [
    ("robosuite", "grasp_offset_m", .005, .012),
    ("robocasa", "base_forward_m", .11, .12),
])
def test_hint_numeric_content_changes_robot_commands(tmp_path, suite, key, a, b):
    first = worker_calls(tmp_path, suite, {"advisor_guidance": f"AB_CONTROL_V1 {key}={a};"})
    second = worker_calls(tmp_path, suite, {"advisor_guidance": f"AB_CONTROL_V1 {key}={b};"})
    baseline = worker_calls(tmp_path, suite, {})
    assert first != second and first != baseline and second != baseline


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
def test_selected_memory_content_not_id_drives_actions(tmp_path, suite):
    text = "AB_CONTROL_V1 close_settle_steps=30;" if suite == "robosuite" else "AB_CONTROL_V1 base_forward_m=.11;"
    selected = {"memory_ids_to_use": ["m"], "memory_guidance": {"m": text}}
    assert worker_calls(tmp_path, suite, selected) != worker_calls(tmp_path, suite, {})
    # This unselected input is an internal compiler negative, not valid gateway input.
    assert COMPILER["compile_attention"]({"memory_guidance": {"m": text}}, suite) == {}


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
def test_demo_numeric_trajectory_content_changes_commands(tmp_path, suite):
    first = demo(suite)
    second = copy.deepcopy(first)
    args = second["assets"][0]["steps"][1]["arguments"]
    if suite == "robosuite":
        args["tolerance"] = .004
        second["assets"][0]["steps"][2]["arguments"]["z"] -= .015
    else:
        args["dx"] = .11
    first_calls = worker_calls(tmp_path, suite, {"demo_prior": json.dumps(first, ensure_ascii=False)})
    second_calls = worker_calls(tmp_path, suite, {"demo_prior": json.dumps(second, ensure_ascii=False)})
    assert first_calls != second_calls


@pytest.mark.parametrize("text", ["{} {}", '{"assets":[],"assets":[]}', "[" * 14 + "]" * 14,
                                  '{"x":"\\u0041"}', '{"x":1e999}', '{"x": true,}'])
def test_demo_parser_rejects_unsupported_or_ambiguous_text(text):
    with pytest.raises(Exception):
        COMPILER["read_public_json"](text)


def test_precedence_is_parameter_specific_and_memory_order_stable():
    value = {"memory_ids_to_use": ["z", "a"], "memory_guidance": {
        "z": "AB_CONTROL_V1 close_settle_steps=30;grasp_offset_m=.012;",
        "a": "AB_CONTROL_V1 grasp_offset_m=.010;"},
        "advisor_guidance": "AB_CONTROL_V1 grasp_offset_m=.005;"}
    assert COMPILER["compile_attention"](value, "robosuite") == {
        "grasp_offset_m": .005, "close_settle_steps": 30}


def test_exact_natural_language_recipes_are_explicit():
    compile_text = COMPILER["text_parameters"]
    assert compile_text("lower it to the grasp height; close gripper", "robosuite")["grasp_offset_m"] == .005
    assert compile_text("get_observation to verify approach and grasp success", "robocasa")["grasp_offset_m"] == .045
    assert compile_text("lower it to the height; close gripper", "robosuite") == {}


def test_robocasa_v1_1_baseline_and_content_recipe_reaches_real_base(tmp_path):
    baseline = worker_calls(tmp_path, "robocasa", {}, version="v1_1")
    assert baseline == worker_calls(tmp_path, "robocasa", {}, original=True)
    text = "Use get_observation to verify approach and grasp success."
    guidance = {"memory_ids_to_use": ["trusted"], "memory_guidance": {"trusted": text}}
    changed = worker_calls(tmp_path, "robocasa", guidance, version="v1_1")
    first = next(e for e in changed if e["source"] == "robot_sdk.base")
    control = next(e for e in baseline if e["source"] == "robot_sdk.base")
    assert first["arguments"]["dx"] == .11
    assert control["arguments"]["dx"] == .125
    # The observed difference is a base command, not ignored gripper cadence.
    assert changed != baseline
