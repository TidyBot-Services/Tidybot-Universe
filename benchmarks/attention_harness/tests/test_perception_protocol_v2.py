from __future__ import annotations

import json
import hashlib
from pathlib import Path


PROTOCOL_ROOT = Path(__file__).resolve().parents[1] / "protocol"


def test_v2_tracks_are_disjoint_and_v1_freeze_is_referenced():
    v2 = json.loads((PROTOCOL_ROOT / "v2/perception.json").read_text(encoding="utf-8"))
    v1 = json.loads((PROTOCOL_ROOT / "v1/freeze_manifest.json").read_text(encoding="utf-8"))
    assert v2["v1_freeze_digest_sha256"] == v1["freeze_digest_sha256"]
    assert v2["supersedes_v1"] is False
    assert v2["legacy_v1"]["mix_into_v2_score"] is False
    tracks = v2["tracks"]
    assert tracks["gt_perception_attentionbench"]["score_namespace"] != (
        tracks["vision_perception_deployment"]["score_namespace"]
    )
    assert v2["mode_selection"]["real_robot_sim_gt_allowed"] is False
    assert v2["mode_selection"]["fallback_across_modes"] is False
    manifest = json.loads(
        (PROTOCOL_ROOT / "v2/contract_manifest.json").read_text(encoding="utf-8")
    )
    repo_root = PROTOCOL_ROOT.parents[2]
    assert manifest["v1_freeze_digest_sha256"] == v1["freeze_digest_sha256"]
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((repo_root / relative).read_bytes()).hexdigest() == expected


def test_live_gt_smoke_is_explicitly_partial():
    report = json.loads(
        (PROTOCOL_ROOT / "v2/evidence/robocasa_gt_perception_smoke.json")
        .read_text(encoding="utf-8")
    )
    assert report["formal_gate_passed"] is False
    assert "policy action" in report["not_checked"]
    assert set(report["tasks"]) == {"counter_to_cab", "counter_to_sink"}
    for rows in report["tasks"].values():
        assert [row["seed"] for row in rows] == [101, 102, 103, 104, 105]
        assert all(row["objects"] > 0 and row["label_and_frame_valid"] for row in rows)
        assert all(row["no_op_success"] is False for row in rows)
