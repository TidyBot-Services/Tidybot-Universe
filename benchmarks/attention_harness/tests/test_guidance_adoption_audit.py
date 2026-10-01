"""Meaningful audit negatives: snapshot relocation versus content tampering."""
import copy
import hashlib
import json
from benchmarks.attention_harness.protocol.v2.guidance_adoption_v1.evidence_audit import same_demo_content
from benchmarks.attention_harness.protocol.v2.guidance_adoption_v1.verify_frozen_package import verify


def test_demo_snapshot_relocation_requires_identical_content_and_bytes(tmp_path):
    asset = tmp_path / "original.json"
    snapshot = tmp_path / "snapshot.json"
    asset.write_text('{"source":"public_sdk","steps":[]}')
    snapshot.write_bytes(asset.read_bytes())
    item = {"kind": "action_trajectory", "path": str(asset),
            "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(), "steps": []}
    approved = {"schema_version": "attentionbench.public-demo.v1", "assets": [item]}
    actual = copy.deepcopy(approved)
    actual["assets"][0]["path"] = str(snapshot)
    assert same_demo_content(json.dumps(actual), json.dumps(approved))
    altered = copy.deepcopy(actual)
    altered["assets"][0]["steps"] = [{"operation": "sdk.gripper.close", "arguments": {}}]
    assert not same_demo_content(json.dumps(altered), json.dumps(approved))
    snapshot.write_text("changed")
    assert not same_demo_content(json.dumps(actual), json.dumps(approved))


def test_candidate_approval_identity_detects_file_and_manifest_tampering(tmp_path):
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    code = frozen / "policy.py"
    code.write_text("reviewed byte stream")
    manifest = tmp_path / "sha256_manifest.json"
    manifest.write_text(json.dumps({"files": {"frozen/policy.py": hashlib.sha256(code.read_bytes()).hexdigest()}}))
    request = tmp_path / "APPROVAL_REQUEST.json"
    request.write_text(json.dumps({"candidate_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    assert verify(tmp_path)["pass"]
    code.write_text("changed byte stream")
    assert not verify(tmp_path)["pass"]
    manifest.write_text(json.dumps({"files": {"frozen/policy.py": hashlib.sha256(code.read_bytes()).hexdigest()}}))
    result = verify(tmp_path)
    assert not result["pass"] and any(i["kind"] == "approval_candidate_identity" for i in result["issues"])
