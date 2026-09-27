"""Bounded, evidence-linked diagnostic Eval for a persisted AttentionBench run.

The model receives a compact projection, never a filesystem path or tools.  The
simulator's native verdict remains authoritative and cannot be changed here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.attention_harness.parcc_client import ParccClient


def _inside(run_dir: Path, name: str) -> Path:
    path = Path(name).resolve()
    if not path.is_relative_to(run_dir.resolve()):
        raise ValueError("Eval evidence path escapes run artifact")
    return path


def build_eval_packet(artifact: Path) -> dict[str, Any]:
    run_dir = artifact.resolve().parent
    run = json.loads(artifact.read_text(encoding="utf-8"))
    if run.get("schema_version") != "attentionbench.sim-gt-attention-run.v1":
        raise ValueError("Eval requires a persisted v2 Attention run")
    attempts = []
    for attempt in run.get("attempts", [])[:3]:
        if (run.get("runner_boundary") or {}).get("mode") == "formal":
            attempts.append(_formal_attempt_packet(run_dir, run, attempt))
            continue
        bundle_name = attempt.get("attention_trace", {}).get("bundle")
        if not isinstance(bundle_name, str):
            raise ValueError("attempt has no trace bundle")
        bundle = json.loads(_inside(run_dir, bundle_name).read_text(encoding="utf-8"))
        traces = [event.get("payload", {}) for event in bundle.get("events", [])
                  if event.get("event_type") == "raw_trace.created"]
        if len(traces) != 1:
            raise ValueError("attempt does not have one raw trace")
        trace = traces[0]
        events = []
        for event in trace.get("events", [])[:50]:
            item = {key: event.get(key) for key in
                    ("event_id", "event_type", "operation", "status")}
            if event.get("error"):
                item["error"] = str(event["error"])[:350]
            if event.get("event_id") == "action-log":
                item["result"] = {"action_count": event.get("result", {}).get("action_count")}
            events.append(item)
        attempts.append({
            "attempt_id": attempt.get("attention_trace", {}).get("attempt_id"),
            "status": attempt.get("status"),
            "native_success": attempt.get("native_success"),
            "outcome": trace.get("outcome"),
            "events": events,
        })
    packet = {
        "schema_version": "attentionbench.diagnostic-eval-input.v1",
        "suite": run.get("suite"), "task_id": run.get("task_id"),
        "seed": run.get("seed"), "native_success": run.get("native_success"),
        "stopped_reason": run.get("stopped_reason"),
        "generated_policy_sha256": (run.get("generated_policy") or {}).get("sha256"),
        "attempts": attempts,
    }
    if len(json.dumps(packet, ensure_ascii=False)) > 12_000:
        raise ValueError("Eval evidence packet exceeds size limit")
    return packet


def _formal_attempt_packet(run_dir: Path, run: dict[str, Any],
                           attempt: dict[str, Any]) -> dict[str, Any]:
    formal = attempt.get("formal_runner_result")
    link = attempt.get("attention_trace") or {}
    if (not isinstance(formal, dict) or formal.get("boundary_checked") is not True
            or formal.get("run_id") != link.get("run_id")
            or formal.get("attempt_id") != link.get("attempt_id")
            or formal.get("policy_sha256") != run.get("approved_policy_sha256")
            or formal.get("config_sha256") != run.get("approved_config_sha256")):
        raise ValueError("Eval formal attempt identity or approval mismatch")
    artifacts = formal.get("artifacts") or {}
    values = {}
    for name in ("trace", "safety", "sandbox_receipt", "native_result"):
        ref = artifacts.get(name) or {}
        uri = ref.get("uri")
        if not isinstance(uri, str):
            raise ValueError(f"Eval formal {name} reference missing")
        path = _inside(run_dir, uri)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref.get("sha256"):
            raise ValueError(f"Eval formal {name} digest mismatch")
        values[name] = json.loads(data)
    trace, native = values["trace"], values["native_result"]
    if any(values[name].get(key) != link.get(key)
           for name in values for key in ("run_id", "attempt_id")):
        raise ValueError("Eval formal artifact identity mismatch")
    if (trace.get("run_id") != link["run_id"]
            or trace.get("attempt_id") != link["attempt_id"]
            or trace.get("policy_sha256") != run["approved_policy_sha256"]
            or trace.get("config_sha256") != run["approved_config_sha256"]
            or native.get("run_id") != link["run_id"]
            or native.get("attempt_id") != link["attempt_id"]
            or native.get("native_success") is not attempt.get("native_success")
            or formal.get("native_success") is not attempt.get("native_success")):
        raise ValueError("Eval formal trace or native result mismatch")
    if (trace.get("status") != attempt.get("status")
            or native.get("status") != attempt.get("status")
            or values["safety"].get("source") != "independent_safety_monitor"
            or not isinstance(values["safety"].get("unsafe_attempts"), int)):
        raise ValueError("Eval formal outcome or safety evidence mismatch")
    sdk_events = trace.get("sdk_events")
    if not isinstance(sdk_events, list):
        raise ValueError("Eval formal trace lacks SDK events")
    events = []
    for index, event in enumerate(sdk_events[:50]):
        if not isinstance(event, dict):
            raise ValueError("Eval formal SDK event is invalid")
        events.append({
            "event_id": str(event.get("event_id") or f"formal-sdk-{index}"),
            "event_type": event.get("event_type"),
            "operation": event.get("operation"),
            "status": event.get("status"),
            **({"error": str(event["error"])[:350]} if event.get("error") else {}),
        })
    events.append({"event_id": "formal-native-result", "event_type": "evaluator.result",
                   "operation": "native_success", "status": trace.get("status")})
    return {
        "attempt_id": link["attempt_id"], "status": attempt.get("status"),
        "native_success": attempt.get("native_success"),
        "outcome": {"status": trace.get("status"), "native_success": native["native_success"],
                    "evaluated": native.get("evaluated")},
        "events": events,
        "formal_artifact_refs": artifacts,
        "safety_unsafe_attempts": values["safety"].get("unsafe_attempts"),
        "sandbox_service_stop": values["sandbox_receipt"].get("service_stop"),
        "native_evaluator_source": native.get("source"),
    }


def diagnose_attention(artifact: Path, *, client: ParccClient | None = None,
                       model: str = "parcc/GLM") -> dict[str, Any]:
    packet = build_eval_packet(artifact)
    prompt = (
        "You are the AttentionBench diagnostic Eval Agent. The native_success "
        "field is authoritative; do not change it. Based only on this packet, "
        "give the earliest observed problem, one actionable next repair, and "
        "cite an attempt_id plus an event_id. Be concise. If evidence is "
        "insufficient, say so. Do not call tools or request more files.\n"
        + json.dumps(packet, ensure_ascii=False, sort_keys=True)
    )
    response = (client or ParccClient(timeout_seconds=75, max_attempts=1)).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048, temperature=0.0, reasoning_effort="low",
    )
    diagnosis = response.content.strip()
    if not diagnosis:
        raise ValueError("diagnostic Eval returned no final text")
    if not any(str(attempt["attempt_id"]) in diagnosis for attempt in packet["attempts"]):
        raise ValueError("diagnostic Eval did not cite an attempt ID")
    event_ids = {str(event["event_id"]) for attempt in packet["attempts"]
                 for event in attempt["events"]}
    if not any(event_id in diagnosis for event_id in event_ids):
        raise ValueError("diagnostic Eval did not cite a trace event ID")
    result = {
        "schema_version": "attentionbench.diagnostic-eval.v1",
        "model": model,
        "native_success": packet["native_success"],
        "input_sha256": hashlib.sha256(json.dumps(
            packet, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "diagnosis": diagnosis[:4000],
        "usage": response.usage,
        "attempts": response.attempts,
    }
    output = artifact.resolve().parent / "eval_diagnosis.json"
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                      encoding="utf-8")
    result["artifact"] = str(output)
    return result
