"""Offline, synthetic checks for the static evidence exporter."""

import hashlib
import html as html_module
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from scripts.attentionbench.export_evidence_bundle import BANNER, export, translate


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value)


def fixture(tmp_path):
    run = tmp_path / "devexp-abcdef1234"
    cid = "cell1"
    base = run / "artifacts" / run.name / "cells" / cid
    write(run / "selection.json", {"budget_tier": "dev", "uri": "/home/truares/demo"})
    write(run / "created.json", {"experiment_id": run.name})
    ledger = sqlite3.connect(run / "ledger.sqlite3")
    ledger.executescript("CREATE TABLE v2_experiments(id TEXT,payload TEXT); CREATE TABLE v2_cells(id TEXT,payload TEXT); CREATE TABLE v2_versions(cell TEXT,version INT,payload TEXT);")
    ledger.execute("INSERT INTO v2_experiments VALUES (?,?)", (run.name, json.dumps({"experiment_id": run.name})))
    ledger.execute("INSERT INTO v2_cells VALUES (?,?)", (cid, json.dumps({"id": cid})))
    ledger.execute("INSERT INTO v2_versions VALUES (?,?,?)", (cid, 1, json.dumps({"agent": {"eval": {"passed": False, "reason": "test"}}})))
    ledger.commit(); ledger.close()
    write(base / "config.json", {})
    for name in ("version.json", "public-feedback.json", "bundle.json"):
        write(base / "v1" / name, {})
    write(base / "v1/policy.py", "def run(): pass\n")
    agent = base / "agent/ws/versions/v0001"
    write(agent / "eval.json", {"passed": False, "reason": "test", "evidence": []})
    write(agent / "sources/main.py", "def main(): pass\n")
    execution = base / "agent/ws/executions/e000001"
    for name in ("execution.json", "result.json", "evidence.json", "trace.json", "initial_observation.json",
                 "final_observation.json", "state_log.json"):
        write(execution / name, {})
    write(execution / "stdout.txt", "ok\n")
    write(execution / "stderr.txt", "")
    write(execution / "frames/000001.jpg", "excluded")
    native = base / "agent/private/executions/e000001/openarm365-dev-123"
    write(native / "native_result.json", {})
    write(native / "scene-condition.json", {})
    (native / "replay.mp4").write_bytes(b"fake-mp4")
    write(native / "numba-cache/hidden.npy", "excluded")
    store = tmp_path / "store.sqlite3"
    memory = sqlite3.connect(store)
    memory.executescript("CREATE TABLE memories(id TEXT,payload TEXT); CREATE TABLE memory_v2_provenance(memory_id TEXT,payload TEXT); CREATE TABLE events(sequence INTEGER,event_key TEXT,entity_type TEXT,entity_id TEXT,event_type TEXT,payload TEXT);")
    memory.execute("INSERT INTO memories VALUES (?,?)", ("note1", json.dumps({"status": "provisional", "applicability": {"task_id": "task", "seed": 1}})))
    memory.execute("INSERT INTO memory_v2_provenance VALUES (?,?)", ("note1", json.dumps({"artifact_sha256": "abc", "request_sha256": "def"})))
    memory.execute("INSERT INTO events VALUES (?,?,?,?,?,?)", (1, "event1", "memory", "note1", "created", "{}"))
    memory.commit(); memory.close()
    return run, store, execution


def test_export_synthetic_snapshot(tmp_path):
    run, store, execution = fixture(tmp_path)
    sources = [run / "selection.json", run / "ledger.sqlite3", store, execution / "trace.json"]
    before = {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sources}
    out = tmp_path / "out"
    result = export(run, store, out, load_local_secrets=False)
    with tarfile.open(result["asset"]) as archive:
        names = archive.getnames()
        assert "run/selection.json" in names
        assert "memory/notes.json" in names
        assert any(name.endswith("replay.mp4") for name in names)
        assert not any(name.endswith((".png", ".jpg", ".jpeg", ".zst", ".npz", ".npy")) for name in names)
        assert not any("numba-cache" in name for name in names)
        manifest = json.load(archive.extractfile("MANIFEST.json"))
        for entry in manifest["files"]:
            assert hashlib.sha256(archive.extractfile(entry["path"]).read()).hexdigest() == entry["sha256"]
        html = archive.extractfile("index.html").read().decode()
        assert BANNER in html_module.unescape(html)
        assert "http://" not in html and "https://" not in html
        assert "@AUTHOR_WS@/demo" in archive.extractfile("run/selection.json").read().decode()
    after = {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sources}
    assert before == after
    assert result["scan"]["hits"] == []


def test_planted_credential_blocks_asset(tmp_path):
    run, store, execution = fixture(tmp_path)
    write(execution / "stdout.txt", "Authorization: fake-credential\n")
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="credential scan blocked"):
        export(run, store, out, load_local_secrets=False)
    assert not list(out.glob("*.tar.gz"))
    report = json.loads((out / "scan-report.json").read_text())
    assert report["result"] == "blocked" and report["hits"]


def test_path_translation_reversible():
    original = b"first /home/truares/a\nsecond /home/truares/b\n"
    translated, changed = translate(original)
    assert changed
    assert translated.replace(b"@AUTHOR_WS@/", b"/home/truares/") == original


def test_reviewed_false_positive_is_bound_to_bytes_and_offset(tmp_path):
    run, store, execution = fixture(tmp_path)
    mp4 = run / "artifacts" / run.name / "cells/cell1/agent/private/executions/e000001/openarm365-dev-123/replay.mp4"
    mp4.write_bytes(b"\x00\x01Sk-\x9c\x00\x13")  # codec bytes that look like the generic sk- prefix
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="credential scan blocked"):
        export(run, store, out, load_local_secrets=False)
    [hit] = json.loads((out / "scan-report.json").read_text())["hits"]
    digest = hashlib.sha256(mp4.read_bytes()).hexdigest()
    waiver = tmp_path / "reviewed.json"
    row = {"path": hit["file"], "sha256": digest, "offset": hit["offset"], "pattern": hit["pattern"],
           "note": "codec bytes, no key characters follow"}
    write(waiver, [dict(row, sha256="0" * 64)])
    with pytest.raises(RuntimeError, match="credential scan blocked"):
        export(run, store, tmp_path / "out-wrong-sha", load_local_secrets=False, reviewed_false_positives=waiver)
    write(waiver, [row])
    result = export(run, store, tmp_path / "out-ok", load_local_secrets=False, reviewed_false_positives=waiver)
    assert result["scan"]["hits"] == []
    assert [r["offset"] for r in result["scan"]["reviewed_false_positives"]] == [hit["offset"]]
    write(waiver, [dict(row, pattern="local#0")])
    with pytest.raises(ValueError, match="generic pattern"):
        export(run, store, tmp_path / "out-local", load_local_secrets=False, reviewed_false_positives=waiver)
