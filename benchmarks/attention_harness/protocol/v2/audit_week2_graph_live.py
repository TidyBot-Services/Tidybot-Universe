"""Audit the bounded real graph and Service engineering receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skill-agent-setup/claude-code"))

from attention_eval import build_eval_packet
from benchmarks.attention_harness.memory_service import MemoryService


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_run(path: Path, suite: str) -> tuple[dict, list[dict]]:
    run = read(path)
    assert run["suite"] == suite and run["formal_eligible"] is False
    assert run["runner_boundary"]["mode"] == "formal"
    assert len(run["attempts"]) == 2
    build_eval_packet(path)
    attempts = []
    for item in run["attempts"]:
        formal = item["formal_runner_result"]
        assert formal["boundary_checked"] is True
        assert formal["native_evaluator"]["evaluated"] is True
        assert formal["native_evaluator"]["native_success"] is item["native_success"]
        for kind in ("trace", "safety", "sandbox_receipt", "native_result"):
            ref = formal["artifacts"][kind]
            assert sha(Path(ref["uri"])) == ref["sha256"]
        safety = read(Path(formal["artifacts"]["safety"]["uri"]))
        native = read(Path(formal["artifacts"]["native_result"]["uri"]))
        stop = read(Path(formal["artifacts"]["sandbox_receipt"]["uri"]))["service_stop"]
        assert safety["source"] == "independent_safety_monitor"
        assert safety["unsafe_attempts"] == 0
        assert native["native_success"] is item["native_success"]
        services = stop["services"].values() if suite == "robocasa" else (stop,)
        assert all(s["leader_reaped"] and s["process_group_gone"] for s in services)
        attempts.append({"attempt_id": formal["attempt_id"], "native_success":
                         item["native_success"], "safety_unsafe_attempts": 0,
                         "artifact_sha256": {k: v["sha256"] for k, v in formal["artifacts"].items()},
                         "service_reaped": True})
    return run, attempts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", required=True, type=Path)
    parser.add_argument("--depth-safety", required=True, type=Path)
    args = parser.parse_args()
    root = args.graph_root.resolve()
    rs = read(root / "graph-robosuite-restart/graph.json")["entries"][0]
    rc = read(root / "graph-robocasa/graph.json")["entries"][0]
    assert rs["status"] == rc["status"] == "review"
    assert rs["attentionbench_eval_status"] == rc["attentionbench_eval_status"] == "completed"
    source, source_attempts = audit_run(Path(rs["attentionbench_last_run"]["artifact"]), "robosuite")
    independent, independent_attempts = audit_run(
        Path(rs["attentionbench_independent_use"]["artifact"]), "robosuite")
    casa, casa_attempts = audit_run(Path(rc["attentionbench_last_run"]["artifact"]), "robocasa")
    assert not source["native_success"] and independent["native_success"]
    assert not casa["native_success"]
    assert len(source["requests"]) == len(casa["requests"]) == 1
    assert source["requests"][0]["responder"] == casa["requests"][0]["responder"] == "advisor_proxy"
    assert source["requests"][0]["token_usage"]["total_tokens"] <= 3000
    assert casa["requests"][0]["token_usage"]["total_tokens"] <= 3000
    mid = source["requests"][0]["candidate_memory_id"]
    task_link = rs["attentionbench_last_run"]["memory_validation_tasks"]
    assert len(task_link) == 1 and task_link[0]["memory_id"] == mid
    task = read(Path(task_link[0]["state_path"]))
    assert task["status"] == "trusted" and len(task["pairs"]) == 5
    assert sorted(map(int, task["pairs"])) == [101, 102, 103, 104, 105]
    assert all(set(arms) == {"control", "treatment"} for arms in task["arms"].values())
    for arms in task["arms"].values():
        for arm in arms.values():
            assert sha(Path(arm["safety_artifact"])) == arm["safety_sha256"]
    service = MemoryService(Path(source["store"]), artifact_root=Path(source["artifact_dir"]) / "attempts")
    impact = service.impact_report(mid)
    assert impact["control_successes"] == 0 and impact["treatment_successes"] == 5
    assert len(service.list_pairs(mid)) == 5
    assert [item["native_success"] for item in independent["attempts"]] == [False, True]
    assert independent["attempts"][1]["memory_ids"] == [mid]
    assert service.retrieve(independent["memory_context"], now=time.time())
    outside = {**independent["memory_context"], "scene_id": "scene:outside-validated-scope"}
    assert service.retrieve(outside, now=time.time()) == []
    checkpoint = read(root / "restart_checkpoint.json")
    assert checkpoint["status"] == "pair_registered" and checkpoint["pairs_before_restart"] == ["101"]
    assert read(root / "restart_service_cleanup.json")["process_group_gone"] is True
    assert len(task["arms"]["101"]) == 2 and len(task["pairs"]) == 5
    casa_mid = casa["requests"][0]["candidate_memory_id"]
    casa_task = rc["attentionbench_last_run"]["memory_validation_tasks"]
    assert len(casa_task) == 1 and casa_task[0]["status"] == "awaiting_approved_repair"
    casa_state = read(Path(casa_task[0]["state_path"]))
    assert casa_state["status"] == "awaiting_approved_repair"
    casa_service = MemoryService(Path(casa["store"]), artifact_root=Path(casa["artifact_dir"]) / "attempts")
    assert casa_service.get_memory(casa_mid).status.value == "candidate"
    assert casa_service.list_pairs(casa_mid) == []
    repeated = read(root / "repeated-action-clean/smoke_receipt.json")
    assert len(repeated["repetitions"]) == 3
    repeat_rows = []
    for row in repeated["repetitions"]:
        assert row["status"] == "completed" and row["native_success"] is False
        refs = row["artifacts"]
        assert all(sha(Path(refs[k]["uri"])) == refs[k]["sha256"]
                   for k in ("trace", "safety", "sandbox_receipt", "native_result"))
        trace = read(Path(refs["trace"]["uri"]))
        safety = read(Path(refs["safety"]["uri"]))
        stop = read(Path(refs["sandbox_receipt"]["uri"]))["service_stop"]
        commands = [event for event in trace["sdk_events"]
                    if event.get("event_type") == "sdk.gripper_command"]
        assert len(commands) == 3 and safety["unsafe_attempts"] == 0
        assert stop["leader_reaped"] and stop["process_group_gone"]
        repeat_rows.append({"index": row["index"], "actions": 3, "native_success": False,
                            "safety_unsafe_attempts": 0, "service_reaped": True})
    depth_safety = read(args.depth_safety)
    assert any("normalized depth must lie in [0, 1]" in row.get("error", "")
               for row in depth_safety["violations"])
    output = {"schema_version": "attentionbench.week2-graph-live-audit.v1",
              "formal_eligible": False,
              "robosuite": {"source_attempts": source_attempts, "candidate_memory_id": mid,
                             "pairs": 5, "impact": impact, "restart_checkpoint": checkpoint,
                             "independent_attempts": independent_attempts,
                             "independent_native_success": True},
              "robocasa": {"attempts": casa_attempts, "candidate_memory_id": casa_mid,
                           "task_status": "awaiting_approved_repair", "pair_count": 0},
              "repeated_action": repeat_rows,
              "depth_500": {"classification": "Robosuite Service depth conversion rejected an out-of-range normalized depth after a gripper step; raw extrema were not logged, so numerical overshoot versus an invalid frame remains unresolved",
                            "independent_safety_stop": "action_outcome_unknown"}}
    destination = root / "graph_live_audit.json"
    destination.write_text(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(destination)


if __name__ == "__main__":
    main()
