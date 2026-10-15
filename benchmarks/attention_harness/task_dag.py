"""Explicit, frozen Skill DAG bindings for operator progress (never dispatch)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "attentionbench.task-skill-dag.v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def validate_dag(dag: dict[str, Any], *, suite: str, task_id: str) -> dict[str, Any]:
    if (not isinstance(dag, dict) or dag.get("schema_version") != SCHEMA
            or dag.get("suite") != suite or dag.get("task_id") != task_id
            or not isinstance(dag.get("graph_id"), str) or not dag["graph_id"]
            or not isinstance(dag.get("revision"), str) or len(dag["revision"]) != 64):
        raise ValueError("Skill DAG task/source identity mismatch")
    nodes = dag.get("nodes")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
        raise ValueError("Skill DAG requires 1–100 nodes")
    names = [node.get("node_id") for node in nodes if isinstance(node, dict)]
    if (len(names) != len(nodes) or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names) or dag.get("root_skill") not in names):
        raise ValueError("Skill DAG node/root identity is invalid")
    by_id = {node["node_id"]: node for node in nodes}
    for node in nodes:
        deps = node.get("dependencies")
        if (not isinstance(deps, list) or any(not isinstance(d, str) or d not in by_id for d in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError("Skill DAG has missing/duplicate dependencies")
        for key in ("name", "description", "development_status"):
            if not isinstance(node.get(key), str):
                raise ValueError("Skill DAG has invalid display fields")
        if node.get("label") is not None and not isinstance(node["label"], str):
            raise ValueError("Skill DAG label must be text")
    visiting, visited = set(), set()
    def walk(name):
        if name in visiting: raise ValueError("Skill DAG contains a cycle")
        if name in visited: return
        visiting.add(name)
        for dep in by_id[name]["dependencies"]: walk(dep)
        visiting.remove(name); visited.add(name)
    for name in names: walk(name)
    checked = copy.deepcopy(dag)
    content = {key: value for key, value in checked.items() if key != "definition_sha256"}
    if checked.get("definition_sha256") != _digest(content):
        raise ValueError("Skill DAG definition digest mismatch")
    return checked


def freeze_graph(path: Path, *, suite: str, task_id: str, root_skill: str,
                 expected_sha256: str, policy_sha256: str | None = None) -> dict[str, Any]:
    """Freeze the real selected node and its dependency closure; no guessed steps."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError("Skill DAG source is unavailable") from error
    if len(data) > 2_000_000 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Skill DAG source differs from locked digest")
    raw = json.loads(data)
    entries = raw if isinstance(raw, list) else raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("Skill DAG source requires graph entries")
    by_id = {}
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name or name in by_id:
            raise ValueError("Skill DAG source has duplicate/invalid node names")
        by_id[name] = entry
    root = by_id.get(root_skill)
    contract = root.get("attentionbench") if root else None
    if (not isinstance(contract, dict) or contract.get("suite") != suite
            or contract.get("task") != task_id
            or (policy_sha256 is not None and contract.get("approved_policy_sha256") != policy_sha256)):
        raise ValueError("Skill DAG root is not bound to the selected task/policy")
    selected = set()
    def include(name):
        if name in selected: return
        if name not in by_id: raise ValueError("Skill DAG dependency is missing")
        selected.add(name)
        deps = by_id[name].get("dependencies", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            raise ValueError("Skill DAG dependencies must be names")
        for dep in deps: include(dep)
    include(root_skill)
    nodes = [{"node_id": name, "name": name,
              "label": entry.get("display_name"),
              "description": entry.get("description", ""),
              "development_status": entry.get("status", "planned"),
              "dependencies": entry.get("dependencies", [])}
             for name, entry in by_id.items() if name in selected]
    dag = {"schema_version": SCHEMA,
           "graph_id": raw.get("graph_id", path.parent.name) if isinstance(raw, dict) else path.parent.name,
           "revision": expected_sha256, "root_skill": root_skill,
           "suite": suite, "task_id": task_id, "nodes": nodes,
           "policy_sha256": policy_sha256}
    dag["definition_sha256"] = _digest(dag)
    return validate_dag(dag, suite=suite, task_id=task_id)


def write_manifest(dag: dict[str, Any], directory: Path) -> tuple[Path, str]:
    """Write a content-addressed observation snapshot, outside policy inputs."""
    checked = validate_dag(dag, suite=dag['suite'], task_id=dag['task_id'])
    raw = (json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(raw).hexdigest() + '.json')
    # Publish only complete bytes and never replace a content-addressed version.
    # Another launcher may freeze the same graph concurrently.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix='.dag-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != raw: raise ValueError('Skill DAG snapshot changed')
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path, hashlib.sha256(raw).hexdigest()


def load_manifest(path: Path | None, digest: str | None, *, suite: str, task_id: str,
                  policy_sha256: str | None = None) -> dict[str, Any] | None:
    if path is None and digest is None: return None
    if path is None or digest is None: raise ValueError('Skill DAG snapshot and digest must be supplied together')
    raw = path.read_bytes()
    if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Skill DAG snapshot differs from locked digest')
    dag = validate_dag(json.loads(raw), suite=suite, task_id=task_id)
    if policy_sha256 is not None and dag.get('policy_sha256') != policy_sha256:
        raise ValueError('Skill DAG snapshot policy identity mismatch')
    return dag


def graph_command_args(config: dict[str, Any], *, repo_root: Path, artifact_root: Path) -> list[str]:
    keys = ('skill_graph_file', 'skill_graph_sha256', 'skill_root')
    if not config.get('require_skill_graph') and not any(config.get(key) is not None for key in keys): return []
    if any(not isinstance(config.get(key), str) or not config[key] for key in keys):
        raise ValueError('Skill DAG requires an explicit graph file, digest and root')
    dag = freeze_graph((repo_root / config['skill_graph_file']).resolve(), suite=config['suite'],
                       task_id=config['task'], root_skill=config['skill_root'],
                       expected_sha256=config['skill_graph_sha256'],
                       policy_sha256=config.get('approved_policy_sha256'))
    path, digest = write_manifest(dag, artifact_root / 'skill-dags')
    return ['--skill-dag', str(path.resolve()), '--skill-dag-sha256', digest]


def dag_projection(store, run_id, attempts, traces, native_outcomes):
    """Project each frozen DAG and only evidence belonging to that attempt."""
    run = store.get_run(run_id)
    snapshots = store.attempt_dag_snapshots(run_id)
    result = {}
    for attempt in attempts:
        attempt_id = attempt["attempt_id"]
        frozen = snapshots.get(attempt_id)
        if frozen is None:
            result[attempt_id] = {"status": "unavailable", "reason": "本次未记录 Skill DAG"}
            continue
        try:
            if frozen.get("run_id") != run_id or frozen.get("index") != attempt["index"]:
                raise ValueError("snapshot attempt mismatch")
            dag = validate_dag(frozen["dag"], suite=run["suite"], task_id=run["task_id"])
        except (KeyError, ValueError, TypeError):
            result[attempt_id] = {"status": "unavailable", "reason": "Skill DAG 绑定或校验失败"}
            continue
        candidates = [t for t in traces.values() if t.get("attempt_id") == attempt_id]
        trace = candidates[0] if len(candidates) == 1 else None
        finished_result = None
        if trace:
            # The compact UI trace omits result payloads. Read the already
            # authorized persisted trace for the worker's execution verdict.
            persisted = store.get_trace(trace.get("trace_id"))
            source_trace = persisted if persisted and persisted.get("attempt_id") == attempt_id else trace
            finished = [e for e in source_trace.get("events", []) if isinstance(e, dict)
                        and e.get("event_type") == "execution.finished"]
            if len(finished) == 1 and isinstance(finished[0].get("result"), dict):
                finished_result = finished[0]["result"].get("execution_status")
        nodes = []
        for definition in dag["nodes"]:
            node = {**definition, "execution_status": "unknown", "goal_status": "unconfirmed",
                    "event_ids": [], "evidence_source": None}
            # The scheduler runs the root task; development status is not used.
            if definition["node_id"] == dag["root_skill"]:
                node["execution_status"] = {"running": "running", "failed": "failed", "timed_out": "failed",
                                            "cancelled": "stopped", "succeeded": "executed"}.get(attempt.get("status"), "unknown")
                node["evidence_source"] = attempt.get("status_source", "persisted_attempt")
                if finished_result in {"completed", "failed", "cancelled", "timed_out"}:
                    node["execution_status"] = {"completed": "executed", "failed": "failed",
                                                "cancelled": "stopped", "timed_out": "failed"}[finished_result]
                    node["execution_evidence_source"] = "persisted_execution_result"
                native = native_outcomes.get(attempt_id, {})
                if native.get("evaluated") is True and type(native.get("native_success")) is bool:
                    node["goal_status"] = "succeeded" if native["native_success"] else "failed"
                    node["evidence_source"] = "verified_native_result"
            if trace:
                # Only explicit, public skill markers can identify inner skills.
                for event in trace.get("events", []):
                    if not isinstance(event, dict):
                        continue
                    arguments = event.get("arguments")
                    if (not isinstance(arguments, dict) or arguments.get("skill_id") != definition["node_id"]
                            or event.get("event_type") not in {"skill.started", "skill.finished", "skill.blocked"}
                            or not isinstance(event.get("event_id"), str) or not event["event_id"]):
                        continue
                    node["event_ids"].append(event["event_id"])
                    node["evidence_source"] = "advisor_visible_skill_event"
                    node["execution_status"] = ("running" if event["event_type"] == "skill.started" else
                        "blocked" if event["event_type"] == "skill.blocked" else
                        {"completed": "executed", "failed": "failed", "cancelled": "stopped"}.get(event.get("status"), "unknown"))
                if node["execution_status"] == "running" and attempt.get("status") != "running":
                    node["execution_status"] = "unknown"  # missing finish marker
                node["trace_id"] = trace.get("trace_id")
            nodes.append(node)
        result[attempt_id] = {"status": "bound", "run_id": run_id, "attempt_id": attempt_id,
                              "captured_at": frozen["captured_at"], "dag": {**dag, "nodes": nodes}}
    return result
