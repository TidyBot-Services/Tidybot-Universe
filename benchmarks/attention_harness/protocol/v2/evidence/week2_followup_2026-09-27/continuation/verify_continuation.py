"""Verify the relocated continuation evidence without original absolute paths."""
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


freeze = load(ROOT / "robocasa_validation_freeze.json")
audit = load(ROOT / "robocasa_pair_audit.json")
assert digest(ROOT / "robocasa_validation_freeze.json") == audit["freeze_sha256"]
assert digest(ROOT / freeze["policy"]["path"]) == freeze["policy"]["sha256"]
assert audit["candidate_status"] == "candidate"
assert len(audit["rows"]) == 10
for seed in range(101, 106):
    assert [r["arm"] for r in audit["rows"] if r["seed"] == seed] == ["control", "treatment"]
    config = freeze["case_configs"][str(seed)]
    assert digest(ROOT / config["path"]) == config["sha256"]
for row in audit["rows"]:
    attempt = ROOT / row["relative_dir"]
    for field, filename in (
        ("result_sha256", "result.json"),
        ("trace_sha256", "trace.jsonl"),
        ("safety_sha256", "safety_monitor.json"),
    ):
        assert digest(attempt / filename) == row[field], (row["seed"], row["arm"], field)
    result = load(attempt / "result.json")
    safety = load(attempt / "safety_monitor.json")
    assert result["policy_sha256"] == freeze["policy"]["sha256"]
    assert result["native_success"] == row["native_success"]
    assert safety["unsafe_attempts"] == row["unsafe_attempts"]
    assert row["service_stop"]["reason"] == "normal_cleanup"
    assert all(s["leader_reaped"] and s["process_group_gone"]
               for s in row["service_stop"]["services"].values())

with sqlite3.connect(ROOT / "authority" / "attention_memory.sqlite3") as db:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

depth = load(ROOT / "depth_stress_progress.json")
assert len(depth) == 10 and sum(len(case["steps"]) for case in depth) == 200
assert {case["task"] for case in depth} == {"cube_lift", "cube_stack"}
assert {case["seed"] for case in depth} == set(range(101, 106))
assert all(case["status"] == "completed" and len(case["steps"]) == 20 for case in depth)
assert all(step["depth_finite"] and step["depth_sha256"]
           for case in depth for step in case["steps"])
depth_stop = load(ROOT / "depth_stress_service_stop.json")
assert depth_stop["leader_reaped"] and depth_stop["process_group_gone"]

print(json.dumps({"verified": True, "paired_arms": 10, "depth_steps": 200,
                  "candidate_status": "candidate", "formal_eligible": False}))
