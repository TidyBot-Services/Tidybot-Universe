"""Verify an approved, public, fixed demo before any simulator attempt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "attentionbench.public-demo.v1"


def verify_demo_prior(raw: str, *, suite: str, task_id: str,
                      approved_sha256: str, snapshot_dir: Path) -> tuple[str, dict[str, Any]]:
    """Return only approved public SDK material and its immutable receipt."""
    if hashlib.sha256(raw.encode()).hexdigest() != approved_sha256:
        raise ValueError("demo manifest differs from approved SHA-256")
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("demo prior must be an approved JSON manifest") from exc
    if (not isinstance(manifest, dict) or set(manifest) !=
            {"schema_version", "suite", "task_id", "approval", "assets"}
            or manifest["schema_version"] != SCHEMA or manifest["suite"] != suite
            or manifest["task_id"] != task_id):
        raise ValueError("demo manifest schema or task mismatch")
    approval = manifest["approval"]
    if (not isinstance(approval, dict) or set(approval) != {"id", "approved_by"}
            or any(not isinstance(approval[key], str) or not approval[key].strip()
                   for key in approval)):
        raise ValueError("demo requires a recorded pre-run approval")
    assets = manifest["assets"]
    if not isinstance(assets, list) or not 1 <= len(assets) <= 2:
        raise ValueError("demo requires public video or action trajectory")
    projected = []
    kinds = set()
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    (snapshot_dir / "approved_manifest.json").write_text(raw, encoding="utf-8")
    for asset in assets:
        if (not isinstance(asset, dict) or set(asset) != {"kind", "path", "sha256", "source"}
                or asset["kind"] not in {"public_video", "action_trajectory"}
                or asset["kind"] in kinds or asset["source"] != "public_sdk"
                or not isinstance(asset["path"], str)
                or not isinstance(asset["sha256"], str)
                or len(asset["sha256"]) != 64):
            raise ValueError("demo asset must identify unique public SDK material")
        kinds.add(asset["kind"])
        path = Path(asset["path"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("demo asset must be a fixed local file")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("demo asset differs from approved SHA-256")
        fixed_path = snapshot_dir / ("video" + path.suffix if asset["kind"] == "public_video"
                                     else "actions.json")
        fixed_path.write_bytes(data)
        if hashlib.sha256(fixed_path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("demo snapshot SHA-256 mismatch")
        item = {"kind": asset["kind"], "path": str(fixed_path.resolve()),
                "sha256": asset["sha256"]}
        if asset["kind"] == "action_trajectory":
            trajectory = json.loads(data)
            if (not isinstance(trajectory, dict) or set(trajectory) != {"source", "steps"}
                    or trajectory["source"] != "public_sdk"
                    or not isinstance(trajectory["steps"], list)
                    or len(trajectory["steps"]) > 32):
                raise ValueError("trajectory must contain bounded public SDK steps")
            for step in trajectory["steps"]:
                if (not isinstance(step, dict) or set(step) != {"operation", "arguments"}
                        or not isinstance(step["operation"], str)
                        or not step["operation"].startswith("sdk.")
                        or not isinstance(step["arguments"], dict)
                        or _contains_private(step)):
                    raise ValueError("trajectory contains non-public SDK fields")
            item["steps"] = trajectory["steps"]
        projected.append(item)
    public = json.dumps({"schema_version": SCHEMA, "assets": projected},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(public) > 4000:
        raise ValueError("public demo projection exceeds input bound")
    return public, {"manifest_sha256": approved_sha256,
                    "approval": approval, "assets": [{key: item[key] for key in
                    ("kind", "path", "sha256")} for item in projected]}


def _contains_private(value: Any) -> bool:
    forbidden = ("oracle", "ground_truth", "privileged", "evaluator", "native_success",
                 "sim_state", "object_pose", "hidden")
    if isinstance(value, dict):
        return any(any(word in str(key).lower() for word in forbidden)
                   or _contains_private(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_private(item) for item in value)
    if isinstance(value, str):
        return any(word in value.lower() for word in forbidden)
    return False
