import json
from .tasks import get_task

EXPECTED = {
    "schema_version": "attentionbench.robocasa365-config.v1",
    "suite": "robocasa365",
    "seed": 101,
    "formal_eligible": False,
    "robot": "PandaOmron",
    "controller": "HYBRID_MOBILE_BASE",
    "perception_mode": "sim_gt",
    "layout_id": 11,
    "style_id": 34,
    "split": None,
    "obj_instance_split": "pretrain",
    "camera_names": ["robot0_agentview_left", "robot0_eye_in_hand"],
    "camera_width": 128,
    "camera_height": 128,
    "randomize_cameras": False,
    "generative_textures": None,
    "control_freq": 20,
    "horizon": 100,
    "sdk_call_limit": 40,
    "attempt_wall_seconds": 120,
    "whole_case_wall_seconds": 300,
    "scene_id": "robocasa365:layout11:style34",
    "robocasa_commit": "456174f62b89b8fca99eaaf33949c29fec9cfc2a",
    "robosuite_commit": "5ce6643f3092639d08f7b0f90ed1c6a84f50552c",
    "safety_limits": {
        "max_arm_delta_m": 0.05,
        "max_arm_rotation_rad": 0.5,
        "max_normalized_command": 1.0,
        "max_observed_eef_step_m": 0.25,
        "max_observed_base_step_m": 0.1,
    },
}


def validate_config(value):
    if isinstance(value, dict) and value.get("schema_version") == "attentionbench.openarm365-config.v1":
        from openarm365.config import validate_config as validate_openarm
        return validate_openarm(value)
    if isinstance(value, dict) and value.get("schema_version") == "attentionbench.openarm365-formal-config.v2":
        from openarm365_dev_v2.formal_config import validate_formal_config
        return validate_formal_config(value)
    if not isinstance(value, dict) or set(value) != set(EXPECTED) | {
        "task_id",
        "official_env",
        "task_prompt",
        "object_set_id",
        "asset_manifest_sha256",
    }:
        raise ValueError("RoboCasa365 config fields differ from frozen contract")
    for k, v in EXPECTED.items():
        if value[k] != v or type(value[k]) is not type(v):
            raise ValueError("RoboCasa365 frozen condition mismatch: " + k)
    if get_task(value["task_id"]).official_env != value["official_env"]:
        raise ValueError("official task mismatch")
    if (
        value["task_prompt"]
        != {
            "pick_place_counter_to_sink": "Pick the object from the counter and place it in the sink.",
            "open_cabinet": "Open the cabinet door.",
        }[value["task_id"]]
    ):
        raise ValueError("frozen task prompt mismatch")
    sha = value["asset_manifest_sha256"]
    if sha != "0385a2992da285a440f45f3f9c9ec918f081357cbe8a4b32caec66cb952da330":
        raise ValueError("frozen asset manifest mismatch")
    if (
        not isinstance(sha, str)
        or len(sha) != 64
        or any(c not in "0123456789abcdef" for c in sha)
    ):
        raise ValueError("asset digest required")
    if value["object_set_id"] != "robocasa365:pretrain:" + sha:
        raise ValueError("object set asset identity mismatch")
    return value


def _config(request):
    request.validate()
    if request.config_path is None:
        raise ValueError("RoboCasa365 requires frozen config")
    c = validate_config(json.loads(request.config_path.read_bytes()))
    if (c["task_id"], c["seed"]) != (request.task_id, request.seed):
        raise ValueError("RoboCasa365 request/config mismatch")
    if request.overall_deadline_seconds > c["whole_case_wall_seconds"] or (
        request.attempt_deadline_seconds is not None
        and request.effective_attempt_deadline_seconds > c["attempt_wall_seconds"]
    ):
        raise ValueError(
            "RoboCasa365 deadline exceeds freeze; explicit attempt deadline required"
        )
    return c


def memory_context(c):
    return {
        "benchmark": "RoboCasa365@" + c["robocasa_commit"],
        "suite": "robocasa365",
        "robot": c["robot"],
        "controller": c["controller"],
        "task_id": c["task_id"],
        "perception_mode": c["perception_mode"],
        "scene_id": c["scene_id"],
        "object_set_id": c["object_set_id"],
        "camera_config_id": "robocasa365:128x128:left+wrist",
        "task_variant_id": c["official_env"] + ":dev-layout11-style34",
        "camera_names": c["camera_names"],
        "task_prompt": c["task_prompt"],
    }
