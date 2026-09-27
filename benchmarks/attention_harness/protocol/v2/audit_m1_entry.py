"""Audit persisted M1 lock, formal boundary, and raw artifact identities."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(path: Path) -> dict:
    summary = json.loads(path.read_text(encoding="utf-8"))
    lock = summary["entry_lock"]
    canonical = dict(lock)
    approved = canonical.pop("sha256")
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert actual == approved
    assert summary["formal_eligible"] is False
    assert summary["perception_mode"] == lock["perception_mode"] == "sim_gt"
    assert summary["suite"] == lock["suite"]
    assert summary["task_id"] == lock["task_id"]
    assert summary["seed"] == lock["seed"]
    assert summary["policy_id"] == lock["attention_policy"]
    assert summary["approved_config_sha256"] == lock["approved_config_sha256"]
    assert summary["approved_policy_sha256"] == lock["approved_policy_sha256"]
    assert digest(Path(summary["approved_config_path"])) == lock["approved_config_sha256"]
    assert digest(Path(summary["approved_policy_path"])) == lock["approved_policy_sha256"]
    persisted_lock = Path(summary["approved_config_path"]).parent / "entry_lock.json"
    assert json.loads(persisted_lock.read_text(encoding="utf-8")) == lock
    scheduler = summary["scheduler_config"]
    assert scheduler["entry_sha256"] == approved
    for key in ("max_attempts", "assistance_credits", "token_limit",
                "assistance_mode", "overall_deadline_seconds"):
        assert scheduler[key] == lock[key]
    attempts = []
    for attempt in summary["attempts"]:
        formal = attempt["formal_runner_result"]
        assert formal["boundary_checked"] is True
        assert formal["entry_lock"] == lock
        for key in ("suite", "task_id", "seed"):
            assert formal[key] == lock[key]
        assert formal["config_sha256"] == lock["approved_config_sha256"]
        assert formal["policy_sha256"] == lock["approved_policy_sha256"]
        refs = formal["artifacts"]
        for name in ("trace", "safety", "sandbox_receipt", "native_result"):
            assert digest(Path(refs[name]["uri"])) == refs[name]["sha256"]
        trace = json.loads(Path(refs["trace"]["uri"]).read_text(encoding="utf-8"))
        assert trace["entry_lock"] == lock
        attempts.append({"status": formal["status"], "native_success": formal["native_success"],
                         "service_revision": formal["service_revision"],
                         "artifacts": refs})
    return {"run": str(path.resolve()), "entry_sha256": approved,
            "entry_lock_record": str(persisted_lock.resolve()),
            "suite": lock["suite"], "task_id": lock["task_id"], "seed": lock["seed"],
            "attention_policy": lock["attention_policy"],
            "config_sha256": lock["approved_config_sha256"],
            "attempts": attempts, "formal_eligible": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps([audit(path) for path in args.runs], ensure_ascii=False,
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
