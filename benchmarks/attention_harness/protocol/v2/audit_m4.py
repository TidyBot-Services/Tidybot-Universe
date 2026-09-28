"""Recompute M4 engineering-run evidence without changing formal eligibility."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmarks.attention_harness.core.store import AttentionStore


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(path: Path) -> dict:
    run = json.loads(path.read_text())
    store = AttentionStore(Path(run["store"]))
    events = store.events()
    checked = []
    failures = []
    for index, item in enumerate(run["attempts"]):
        formal = item["formal_runner_result"]
        files = {}
        for name in ("trace", "safety", "sandbox_receipt", "native_result"):
            ref = formal["artifacts"][name]
            p = Path(ref["uri"])
            valid = p.is_file() and sha(p) == ref["sha256"]
            files[name] = {"uri": str(p), "sha256": ref["sha256"], "valid": valid}
            if not valid:
                failures.append(f"attempt {index} {name} digest mismatch")
        safety = json.loads(Path(files["safety"]["uri"]).read_text())
        receipt = json.loads(Path(files["sandbox_receipt"]["uri"]).read_text())
        trace = json.loads(Path(files["trace"]["uri"]).read_text())
        raw = store.get_raw_trace(item["attention_trace"]["raw_trace_id"])
        if not raw:
            failures.append(f"attempt {index} raw trace missing")
        if safety.get("source") != "independent_safety_monitor":
            failures.append(f"attempt {index} independent Safety missing")
        stops = receipt.get("service_stop")
        if isinstance(stops, dict):
            stops = list(stops["services"].values()) if isinstance(stops.get("services"), dict) else [stops]
        if not stops or any(stop.get("process_group_gone") is not True for stop in stops):
            failures.append(f"attempt {index} service process not verified gone")
        checked.append({
            "attempt_id": item["attention_trace"]["attempt_id"],
            "status": item["status"], "native_success": item["native_success"],
            "advisor_trace_id": item["attention_trace"].get("advisor_trace_id"),
            "raw_trace_id": item["attention_trace"]["raw_trace_id"],
            "files": files, "safety": safety, "service_stop": stops,
            "attention_input": trace.get("attention_input"),
            "raw_trace_present": bool(raw),
        })
    requests = []
    for row in run["requests"]:
        request = store.get_request(row["request_id"])
        response = store.get_response(row["response_id"])
        uses = [event for event in events if event["entity_id"] == row["request_id"]
                and event["event_type"] in {"response.used", "response.execution_linked"}]
        slot = row["failure_slot"]
        next_input = checked[slot + 1]["attention_input"] if slot + 1 < len(checked) else None
        guidance = (row.get("advice") or {}).get("guidance")
        guidance_applied = bool(guidance and next_input and next_input.get("advisor_guidance") == guidance)
        if request is None or request.state.value != "answered" or response is None:
            failures.append(f"request {row['request_id']} not answered")
        if not guidance_applied and row.get("advice", {}).get("request_type") == "hint":
            failures.append(f"request {row['request_id']} guidance absent from next attempt")
        if not any(event["event_type"] == "response.execution_linked" for event in uses):
            failures.append(f"request {row['request_id']} not linked to next execution")
        requests.append({"request_id": row["request_id"],
                         "state": request.state.value if request else None,
                         "response": response, "uses": uses,
                         "guidance_applied": guidance_applied})
    all_requests = []
    for event in events:
        if event["event_type"] != "request.created":
            continue
        request = store.get_request(event["entity_id"])
        response = store.get_response(request.response_id) if request and request.response_id else None
        all_requests.append({"request_id": event["entity_id"],
                             "state": request.state.value if request else None,
                             "response_id": request.response_id if request else None,
                             "response": response})
    if len([row for row in all_requests if row["state"] == "answered"]) != len(requests):
        failures.append("answered request not represented in run summary")
    if run.get("formal_eligible") is not False:
        failures.append("run incorrectly formal eligible")
    return {"summary": str(path), "summary_sha256": sha(path),
            "suite": run["suite"], "policy_id": run["policy_id"],
            "formal_eligible": run["formal_eligible"],
            "attempts": checked, "decisions": run["decisions"],
            "requests": requests, "resource_usage": run["resource_usage"],
            "all_request_states": all_requests,
            "resource_status": store.resource_status(f"run:{Path(run['artifact_dir']).name}"),
            "stopped_reason": run["stopped_reason"], "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"schema_version": "attentionbench.m4-evidence-audit.v1",
              "formal_eligible": False, "runs": [audit(path) for path in args.run]}
    result["passed"] = all(not row["failures"] for row in result["runs"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
