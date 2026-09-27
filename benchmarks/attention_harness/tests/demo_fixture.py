"""Tiny approved public demo fixture, with no simulator state."""

import hashlib
import json


def approved_demo(tmp_path, suite, task):
    video = tmp_path / f"{suite}-public-video.mp4"
    video.write_bytes(b"public camera frames fixture")
    trajectory = tmp_path / f"{suite}-public-actions.json"
    trajectory.write_text(json.dumps({"source": "public_sdk", "steps": [
        {"operation": "sdk.gripper.close", "arguments": {}}
    ]}))
    return json.dumps({
        "schema_version": "attentionbench.public-demo.v1", "suite": suite,
        "task_id": task, "approval": {"id": "fixture-approval", "approved_by": "test-operator"},
        "assets": [{"kind": kind, "path": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "source": "public_sdk"}
                   for kind, path in (("public_video", video), ("action_trajectory", trajectory))],
    })
