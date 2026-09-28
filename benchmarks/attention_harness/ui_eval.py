"""Allowlisted, revalidated terminal Eval projection for the operator UI."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def eval_snapshot(root: Path, run_id: str, run: dict[str, Any], store_path: Path) -> dict[str, Any]:
    if not run_id.startswith("run:attention-"):
        # Only harness-generated run directory names are eligible.
        return {"status": "unavailable"}
    name = run_id.removeprefix("run:")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        return {"status": "unavailable"}
    run_dir = (root / name).resolve()
    if not run_dir.is_relative_to(root.resolve()):
        return {"status": "unavailable"}
    artifact = run_dir / "attention_run.json"
    if not artifact.is_file():
        return {"status": "pending" if run.get("status") == "running" else "unavailable"}
    eval_dir = Path(__file__).resolve().parents[2] / "skill-agent-setup" / "claude-code"
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    from attention_eval import build_eval_packet

    try:
        summary = json.loads(artifact.read_text(encoding="utf-8"))
        if (Path(summary.get("store", "")).resolve() != store_path.resolve()
                or summary.get("suite") != run["suite"]
                or summary.get("task_id") != run["task_id"]
                or summary.get("seed") != run["seed"]):
            raise ValueError("UI run identity differs from Runner")
        packet = build_eval_packet(artifact)
        result = {"status": "pending", "run_id": run_id,
                  "native_success": summary["native_success"],
                  "stopped_reason": summary.get("stopped_reason"),
                  "approved_config_sha256": summary.get("approved_config_sha256"),
                  "attempt_ids": [item["attempt_id"] for item in packet["attempts"]]}
        diagnosis_file = run_dir / "eval_diagnosis.json"
        if diagnosis_file.is_file():
            diagnosis = json.loads(diagnosis_file.read_text(encoding="utf-8"))
            digest = hashlib.sha256(json.dumps(
                packet, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            if (diagnosis.get("schema_version") != "attentionbench.diagnostic-eval.v1"
                    or diagnosis.get("run_id") != run_id
                    or diagnosis.get("input_sha256") != digest
                    or diagnosis.get("native_success") is not summary["native_success"]
                    or diagnosis.get("status") not in {"completed", "failed"}):
                raise ValueError("UI diagnostic receipt differs from validated Runner evidence")
            result.update(status=diagnosis["status"],
                          diagnosis=diagnosis.get("diagnosis") if diagnosis["status"] == "completed" else None,
                          error=diagnosis.get("error") if diagnosis["status"] == "failed" else None,
                          model=diagnosis.get("model"))
        return result
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {"status": "evidence_invalid", "error": str(error)[:300]}
