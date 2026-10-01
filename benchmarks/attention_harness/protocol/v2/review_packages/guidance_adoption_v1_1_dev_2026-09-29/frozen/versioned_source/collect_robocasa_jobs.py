"""Retain only logged jobs for these attempts, without executing log content."""
import hashlib
import json
import re
import shutil
from pathlib import Path

OUT = Path("/home/truares/桌面/attentionbench-guidance-adoption-v1-20260929")
LOG_ROOT = Path("/home/truares/文档/Tidybot-Universe/logs/code_executions").resolve()


def collect():
    ledger = json.loads((OUT / "ledger.json").read_text())
    for row in ledger["rows"]:
        if row["suite"] != "robocasa" or row["status"] == "reserved":
            continue
        episode = Path(row["result_path"]).parent
        log = (episode / "agent.log").read_text()
        completed = set(re.findall(r"Execution ([0-9a-f-]+) finished: ExecutionStatus.COMPLETED", log))
        entries = re.findall(r"Executing code \(ID: ([0-9a-f-]+)\): ([^\n]+)", log)
        destination = episode / "executed_jobs"
        destination.mkdir(exist_ok=True)
        jobs = []
        for execution_id, filename in entries:
            path = Path(filename).resolve()
            if not path.is_relative_to(LOG_ROOT) or path.suffix != ".py":
                raise RuntimeError("unexpected Agent code artifact path")
            raw = path.read_bytes() if path.is_file() else None
            copied = destination / f"{execution_id}.py"
            if raw is not None:
                copied.write_bytes(raw)
            text = raw.decode() if raw is not None else ""
            # This is data slicing, not code evaluation.
            user = None
            if text:
                user = text.split("# USER CODE STARTS HERE", 1)[1].split("# USER CODE ENDS HERE", 1)[0]
                user = user.split("\n\n", 1)[1].rsplit("\n\n#", 1)[0].strip()
            state = LOG_ROOT / execution_id / "state_log.jsonl"
            states = None
            if state.is_file():
                target = destination / f"{execution_id}.state.jsonl"
                shutil.copyfile(state, target)
                states = {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                          "samples": len(target.read_text().splitlines())}
            jobs.append({"execution_id": execution_id, "completed": execution_id in completed,
                         "original_wrapper_path": str(path), "path": str(copied) if raw is not None else None,
                         "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                         "original_wrapper_retained": raw is not None,
                         "missing_reason": None if raw is not None else "existing Agent recorder cleanup removed temporary wrapper",
                         "submitted_user_code": user,
                         "state_samples": states})
        receipt = {"schema": "attentionbench.executed-robocasa-jobs.v1", "row_id": row["id"],
                   "agent_log_sha256": hashlib.sha256((episode / "agent.log").read_bytes()).hexdigest(),
                   "jobs": jobs}
        (destination / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False,
                                                             indent=2, sort_keys=True) + "\n")
        print(row["id"], len(jobs), sum(j["completed"] for j in jobs),
              sum(j["state_samples"]["samples"] for j in jobs if j["state_samples"]))


if __name__ == "__main__":
    collect()
