"""Audit a bounded Week 2 engineering smoke from preserved raw artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_service import MemoryService


def ref(path: Path) -> dict[str, str]:
    path = path.resolve()
    return {"uri": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def audit_run(path: Path, *, suite: str, expected_success: bool) -> tuple[dict, dict]:
    run = json.loads(path.read_text(encoding="utf-8"))
    saved = Path(run["artifact_dir"]) / "attention_run.json"
    assert run == json.loads(saved.read_text(encoding="utf-8"))
    assert run["suite"] == suite and run["formal_eligible"] is False
    assert run["runner_boundary"]["mode"] == "formal"
    assert run["native_success"] is expected_success
    assert len(run["attempts"]) == 2
    store = AttentionStore(Path(run["store"]))
    audited = []
    for attempt in run["attempts"]:
        formal = attempt["formal_runner_result"]
        assert formal["boundary_checked"] is True
        assert formal["run_id"] == attempt["attention_trace"]["run_id"]
        assert formal["attempt_id"] == attempt["attention_trace"]["attempt_id"]
        assert formal["native_evaluator"]["evaluated"] is True
        assert formal["native_evaluator"]["native_success"] is attempt["native_success"]
        artifacts = formal["artifacts"]
        for name in ("trace", "safety", "sandbox_receipt", "native_result"):
            assert ref(Path(artifacts[name]["uri"])) == artifacts[name]
        native = json.loads(Path(artifacts["native_result"]["uri"]).read_text())
        safety = json.loads(Path(artifacts["safety"]["uri"]).read_text())
        sandbox = json.loads(Path(artifacts["sandbox_receipt"]["uri"]).read_text())
        assert native["source"].startswith(suite)
        assert native["evaluated"] is True and native["native_success"] is attempt["native_success"]
        assert safety["source"] == "independent_safety_monitor" and safety["unsafe_attempts"] == 0
        stop = sandbox["service_stop"]
        if suite == "robocasa":
            assert all(item["leader_reaped"] and item["process_group_gone"]
                       for item in stop["services"].values())
        else:
            assert stop["leader_reaped"] and stop["process_group_gone"]
        raw = store.get_raw_trace(attempt["attention_trace"]["raw_trace_id"])
        assert raw["outcome"]["native_success"] is attempt["native_success"]
        audited.append({"attempt_id": formal["attempt_id"], "native_success": attempt["native_success"],
                        "native_source": native["source"], "safety_unsafe_attempts": 0,
                        "artifacts": artifacts, "service_stop": stop})
    return run, {"summary": ref(saved), "attempts": audited}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("robosuite", "independent", "robocasa", "dispatch"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    suite, suite_audit = audit_run(args.robosuite, suite="robosuite", expected_success=False)
    independent, independent_audit = audit_run(args.independent, suite="robosuite", expected_success=True)
    casa, casa_audit = audit_run(args.robocasa, suite="robocasa", expected_success=False)
    for run in (suite, casa):
        assert len(run["requests"]) == 1
        request = run["requests"][0]
        assert request["responder"] == "advisor_proxy"
        assert request["candidate_memory_id"]
        assert request["token_usage"]["total_tokens"] <= 3000
        store = AttentionStore(Path(run["store"]))
        links = [event["payload"] for event in store.events()
                 if event["event_type"] == "response.execution_linked"
                 and event["entity_id"] == request["request_id"]]
        assert len(links) == 1
        assert links[0]["execution_id"] == run["attempts"][1]["attention_trace"]["execution_id"]
        trace = json.loads(Path(run["attempts"][1]["formal_runner_result"]["artifacts"]["trace"]["uri"]).read_text())
        assert trace["attention_input"]["advisor_guidance"] == request["advice"]["guidance"]
    dispatch = json.loads(args.dispatch.read_text(encoding="utf-8"))
    assert len(dispatch) == 1 and dispatch[0]["status"] == "trusted"
    state_path = Path(dispatch[0]["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "trusted" and len(state["pairs"]) == 5
    assert state["reused_existing_pairs"] == []
    assert len(state["arms"]) == 5
    service = MemoryService(Path(suite["store"]), artifact_root=Path(suite["artifact_dir"]) / "attempts")
    memory_id = suite["requests"][0]["candidate_memory_id"]
    assert service.get_memory(memory_id).status.value == "trusted"
    assert len(service.list_pairs(memory_id)) == 5
    impact = service.impact_report(memory_id)
    assert impact["control_successes"] == 0 and impact["treatment_successes"] == 5
    assert not impact["safety_regressions"]
    for arms in state["arms"].values():
        for receipt in arms.values():
            assert ref(Path(receipt["safety_artifact"]))["sha256"] == receipt["safety_sha256"]
    stop_ref = ref(Path(dispatch[0]["service_stop_receipt"]))
    assert stop_ref["sha256"] == dispatch[0]["service_stop_sha256"]
    stop = json.loads(Path(stop_ref["uri"]).read_text())
    assert stop["leader_reaped"] and stop["process_group_gone"]
    assert independent["store"] == suite["store"]
    assert independent["attempts"][0]["native_success"] is False
    used = independent["attempts"][1]
    assert used["memory_ids"] == [memory_id]
    independent_store = AttentionStore(Path(independent["store"]))
    raw = independent_store.get_raw_trace(used["attention_trace"]["raw_trace_id"])
    retrieval = [event for event in raw["events"] if event["event_type"] == "attention.memory_retrieval"]
    assert len(retrieval) == 1 and retrieval[0]["status"] == "completed"
    assert retrieval[0]["arguments"]["memory_id"] == memory_id
    assert retrieval[0]["result"]["memory_version"] == 1 and retrieval[0]["result"]["grant_id"]
    uses = [item for item in independent_store.list_memory_uses(memory_id)
            if item["attempt_id"] == used["attention_trace"]["attempt_id"]]
    assert len(uses) == 1 and uses[0]["outcome"] == "native_success" and uses[0]["memory_version"] == 1
    assert len(service.retrieve(independent["memory_context"], now=time.time())) == 1
    outside = {**independent["memory_context"], "scene_id": "scene:outside-validated-scope"}
    assert service.retrieve(outside, now=time.time()) == []
    casa_service = MemoryService(Path(casa["store"]), artifact_root=Path(casa["artifact_dir"]) / "attempts")
    casa_id = casa["requests"][0]["candidate_memory_id"]
    assert casa_service.check_candidate(casa_id)["status"] == "candidate"
    task_files = list((Path(casa["artifact_dir"]) / "memory_validation").glob("*.json"))
    assert len(task_files) == 1
    casa_state = json.loads(task_files[0].read_text())
    assert casa_state["status"] == "awaiting_approved_repair"
    output = {
        "schema_version": "attentionbench.week2-auto-closure-evidence.v1",
        "formal_eligible": False,
        "new_execution": {"robosuite_source": suite_audit,
                          "robosuite_pairs": {"state": ref(state_path), "impact": impact,
                                               "service_stop": stop_ref},
                          "robosuite_independent": independent_audit,
                          "robocasa_source": casa_audit,
                          "robocasa_candidate_task": ref(task_files[0])},
        "historical_evidence_reused_for_pairs": False,
        "robosuite_memory_id": memory_id,
        "robosuite_independent_use": {"attempt_id": used["attention_trace"]["attempt_id"],
                                      "memory_version": 1,
                                      "grant_id": retrieval[0]["result"]["grant_id"],
                                      "outcome": uses[0]["outcome"],
                                      "scope_outside_match": False},
        "robocasa_memory_id": casa_id,
        "robocasa_blocker": casa_state["blocker"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
