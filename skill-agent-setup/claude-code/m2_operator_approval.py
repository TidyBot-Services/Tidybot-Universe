"""Record an explicit operator M2 decision after reviewing exact candidate hashes.

Run only after the human has approved the hashes. Restart the Graph process after
this command, then call /attention/auto-start; the running process does not reload
graph.json from disk.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from m2_gate import candidate


def record_approval(*, graph_dir: Path, repo_root: Path, approval_file: Path,
                    operator: str, approval_reference: str, source_sha256: str,
                    config_sha256: str, entry_sha256: str) -> dict:
    graph_dir = graph_dir.resolve()
    repo_root = repo_root.resolve()
    graph_file = graph_dir / "graph.json"
    graph = json.loads(graph_file.read_text(encoding="utf-8"))
    entries = graph["entries"] if isinstance(graph, dict) else graph
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("M2 operator command requires one graph entry")
    entry = entries[0]
    config = entry.get("attentionbench", {})
    if (config.get("m2_gate") is not True or entry.get("m2_stage") != "awaiting_approval"
            or entry.get("status") != "review" or entry.get("m2_dispatch") is not None):
        raise ValueError("M2 graph is not awaiting a first human approval")
    current = candidate(config, graph_dir=graph_dir, repo_root=repo_root)
    if entry.get("m2_candidate") != current:
        raise ValueError("M2 candidate changed since Graph paused")
    if (source_sha256 != current["source_sha256"]
            or config_sha256 != current["config_sha256"]
            or entry_sha256 != current["entry_sha256"]):
        raise ValueError("human-approved SHA values do not match M2 candidate")
    if not operator.strip() or not approval_reference.strip():
        raise ValueError("operator identity and approval reference are required")
    approval_file = approval_file.resolve()
    if approval_file.is_relative_to(repo_root) or approval_file.exists():
        raise ValueError("operator approval record must be new and outside repository")
    approval = {
        "schema_version": "attentionbench.m2-human-approval.v1",
        "decision": "approve", "operator": operator, "approval_reference": approval_reference,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        **{key: current[key] for key in ("suite", "task", "seed", "source_sha256",
                                           "config_sha256", "generation_sha256", "entry_sha256")},
    }
    approval_file.parent.mkdir(parents=True, exist_ok=True)
    with approval_file.open("x", encoding="utf-8") as stream:
        json.dump(approval, stream, indent=2, ensure_ascii=False, sort_keys=True)
        stream.write("\n")
    config.update(m2_approval_file=str(approval_file), approved_generated_policy=True,
                  approved_policy_sha256=source_sha256,
                  approved_config_sha256=config_sha256)
    entry.update(m2_stage="approved", status="planned")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=graph_dir,
                                     prefix=".graph-m2-", delete=False) as stream:
        json.dump(graph, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        temp = Path(stream.name)
    os.replace(temp, graph_file)
    return approval


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--approval-file", type=Path, required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--entry-sha256", required=True)
    args = parser.parse_args()
    result = record_approval(
        graph_dir=args.graph, repo_root=args.repo_root,
        approval_file=args.approval_file, operator=args.operator,
        approval_reference=args.approval_reference,
        source_sha256=args.source_sha256, config_sha256=args.config_sha256,
        entry_sha256=args.entry_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
