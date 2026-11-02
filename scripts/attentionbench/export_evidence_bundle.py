#!/usr/bin/env python3
"""Export a read-only, offline evidence snapshot of one dev experiment."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import quote

try:
    from .secret_scan import extract, source_paths
except ImportError:  # Direct script execution.
    from secret_scan import extract, source_paths

VERSION = "1.0.0"
AUTHOR_PREFIX = (str(Path.home()).rstrip("/") + "/").encode()
# Extra identity patterns (e-mail domain, tailnet prefix, ...) stay on the author's machine.
IDENTITY_CONFIG = Path.home() / ".config/attentionbench/export-identity.json"
PLACEHOLDER = b"@AUTHOR_WS@/"
BANNER = ("Exported snapshot of the author's run devexp-02cdcb7a… and Memory notes. "
          "Not trusted records. Cannot be imported into the Memory service or the UI. "
          "View only; dev tier, not formal.")
GENERIC = {
    "sk-": rb"(?<![A-Za-z0-9])sk-",
    "ghp_": rb"ghp_",
    "github_pat_": rb"github_pat_",
    "private-key header": rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "AKIA": rb"AKIA",
    "xox[bp]-": rb"xox[bp]-",
    "Bearer": rb"Bearer\s+",
    "Authorization": rb"Authorization\s*:",
}
EXCLUDED_SUFFIXES = {".png", ".jpg", ".jpeg", ".zst", ".npz", ".npy"}
EXCLUDED_PARTS = {"numba-cache", "probe-owner", "snapshots", "objects", "registration"}
MAX_ASSET = 2_000_000_000


def dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def translate(data: bytes) -> tuple[bytes, bool]:
    if PLACEHOLDER in data:
        raise ValueError("source already contains path placeholder; round-trip is ambiguous")
    result = data.replace(AUTHOR_PREFIX, PLACEHOLDER)
    if result.replace(PLACEHOLDER, AUTHOR_PREFIX) != data:
        raise ValueError("path translation round-trip failed")
    return result, result != data


def db(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    return conn


def load_patterns(extra: list[Path], local: bool) -> tuple[list[tuple[bytes, str]], int]:
    patterns = []
    if local:
        with contextlib.redirect_stdout(io.StringIO()):
            files = list(source_paths([str(p) for p in extra]))
        for path in files:
            for value in extract(path):
                if len(value) >= 12 and "\n" not in value and "\r" not in value:
                    patterns.append((value.encode(), str(path)))
        if not patterns:
            raise RuntimeError("zero local credential patterns loaded")
    return list(dict.fromkeys(patterns)), len(files) if local else 0


def scan_credentials(data: bytes, patterns: list[tuple[bytes, str]]) -> list[dict]:
    hits = []
    for name, expression in GENERIC.items():
        for match in re.finditer(expression, data, re.I):
            hits.append({"pattern": name, "offset": match.start()})
    for number, (secret, source) in enumerate(patterns, 1):
        start = data.find(secret)
        if start >= 0:
            hits.append({"pattern": f"local#{number}", "source": source, "offset": start})
    return hits


def local_identity_patterns(path: Path | None = None) -> list[tuple[str, str]]:
    """{"domain": "<regex>", "tailnet": "<regex>", ...} from a file outside the repository."""
    path = path or IDENTITY_CONFIG
    if not path.is_file():
        print(f"note: {path} not found; identity scan covers user name and host name only", file=sys.stderr)
        return []
    rows = json.loads(path.read_text())
    if not isinstance(rows, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in rows.items()):
        raise ValueError(f"{path} must map kind names to non-empty regular expressions")
    return sorted(rows.items())


def identity_hits(name: str, data: bytes, hostname: str) -> list[dict]:
    text = data.decode("utf-8", errors="replace")
    kinds = [("username", re.escape(getpass.getuser())), ("hostname", re.escape(hostname)),
             *local_identity_patterns()]
    hits = []
    for kind, pattern in kinds:
        if not pattern:
            continue
        for match in re.finditer(pattern, text, re.I):
            if kind == "username" and text[max(0, match.start()-6):match.start()] == "/home/":  # translated home prefix
                continue
            start = max(0, match.start()-40)
            end = min(len(text), match.end()+40)
            hits.append({"file": name, "kind": kind, "offset": len(text[:match.start()].encode()),
                         "line": text.count("\n", 0, match.start())+1,
                         "match": match.group(), "context": text[start:end].replace("\n", "\\n")})
    return hits


def safe_file(path: Path) -> bool:
    parts = path.parts
    return (path.suffix.lower() not in EXCLUDED_SUFFIXES
            and not any(part in EXCLUDED_PARTS or part.startswith("probe-owner-") for part in parts)
            and path.name != "keys.env" and not path.is_symlink())


def load_reviewed(path: Path | None) -> list[dict]:
    """Human-reviewed false positives: generic patterns only, bound to file bytes and offset."""
    if path is None:
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if (not isinstance(row, dict) or row.get("pattern") not in GENERIC
                or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))
                or not isinstance(row.get("offset"), int) or not row.get("path") or not row.get("note")):
            raise ValueError("reviewed false positive needs path, sha256, offset, a generic pattern and a note")
    return rows


def export(run_root: Path, memory_store: Path, out: Path, *, extra_secret_files: list[Path] | None = None,
           load_local_secrets: bool = True, reviewed_false_positives: Path | None = None) -> dict:
    run_root = run_root.resolve(strict=True)
    memory_store = memory_store.resolve(strict=True)
    run_id = run_root.name
    if not re.fullmatch(r"devexp-[a-f0-9]+", run_id):
        raise ValueError("run directory must be a devexp ID")
    out.mkdir(parents=True, exist_ok=True)
    patterns, source_count = load_patterns(extra_secret_files or [], load_local_secrets)
    scan_report = {"run_id": run_id, "tool_version": VERSION,
                   "patterns": {"local_count": len(patterns), "local_source_files": source_count,
                                "generic": list(GENERIC)}, "files_scanned": 0, "hits": [],
                   "reviewed_false_positives": []}
    reviewed = load_reviewed(reviewed_false_positives)
    hostname = socket.gethostname()
    entries: list[dict] = []
    identities: list[dict] = []
    seen_state_logs: dict[str, str] = {}
    omitted = ["raw frames (.png/.jpg/.jpeg/.zst/.npz/.npy), numba caches, probe-owner live data,",
               "snapshots/objects, registration roots, keys.env and all unselected private artifacts"]
    with tempfile.TemporaryDirectory(prefix="evidence-bundle-", dir=out) as tmp:
        stage = Path(tmp) / "bundle"
        stage.mkdir()

        def add(dest: str, data: bytes, source: str, binary: bool = False) -> None:
            if dest.startswith("/") or ".." in Path(dest).parts or not safe_file(Path(dest)):
                raise ValueError(f"unsafe bundle path: {dest}")
            if not binary:
                data, translated = translate(data)
            else:
                translated = False
            scan_report["files_scanned"] += 1
            digest = hashlib.sha256(data).hexdigest()
            for hit in scan_credentials(data, patterns):
                # A waiver matches only the exact file bytes, offset and generic pattern.
                waiver = next((row for row in reviewed if row["path"] == dest and row["sha256"] == digest
                               and row["offset"] == hit["offset"] and row["pattern"] == hit["pattern"]), None)
                if waiver is None:
                    scan_report["hits"].append({"file": dest, **hit})
                else:
                    scan_report["reviewed_false_positives"].append({"file": dest, **hit, "sha256": digest,
                                                                    "note": waiver["note"]})
            if not binary:
                identities.extend(identity_hits(dest, data, hostname))
            target = stage / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            entries.append({"path": dest, "sha256": hashlib.sha256(data).hexdigest(),
                            "size_bytes": len(data), "source": source, "path_translated": translated})

        def copy(source: Path, dest: str) -> None:
            if not safe_file(source):
                raise ValueError(f"excluded source: {source}")
            add(dest, source.read_bytes(), str(source), binary=source.suffix.lower() == ".mp4")

        def jrows(conn: sqlite3.Connection, table: str) -> list[dict]:
            rows = []
            for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                item = dict(row)
                for key, value in item.items():
                    if key == "payload" and isinstance(value, str):
                        item[key] = json.loads(value)
                rows.append(item)
            return rows

        for name in ("selection.json", "created.json"):
            copy(run_root / name, f"run/{name}")
        ledger = db(run_root / "ledger.sqlite3")
        try:
            tables = [row[0] for row in ledger.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v2_%' ORDER BY name")]
            ledger_data = {table: jrows(ledger, table) for table in tables}
            add("run/ledger.json", dump(ledger_data), str(run_root / "ledger.sqlite3") + "#v2_*")
            experiments = ledger_data.get("v2_experiments", [])
            if len(experiments) != 1 or experiments[0]["id"] != run_id:
                raise ValueError("ledger experiment ID mismatch")
            add("run/experiment.json", dump(experiments[0]["payload"]), str(run_root / "ledger.sqlite3") + "#v2_experiments")
            cells = ledger_data.get("v2_cells", [])
            versions = ledger_data.get("v2_versions", [])
            for cell in cells:
                add(f"run/cells/{cell['id']}/cell.json", dump(cell["payload"]), str(run_root / "ledger.sqlite3") + "#v2_cells:" + cell["id"])
            for version in versions:
                add(f"run/cells/{version['cell']}/v{version['version']}/ledger-version.json", dump(version["payload"]),
                    str(run_root / "ledger.sqlite3") + f"#v2_versions:{version['cell']}:{version['version']}")
                add(f"run/cells/{version['cell']}/v{version['version']}/summary.json",
                    dump({"summary": version["payload"].get("summary"),
                          "agent_summary": version["payload"].get("agent", {}).get("summary")}),
                    str(run_root / "ledger.sqlite3") + f"#v2_versions:{version['cell']}:{version['version']}:summary")
        finally:
            ledger.close()

        artifact = run_root / "artifacts" / run_id
        if not artifact.is_dir():
            raise FileNotFoundError(artifact)
        for cell in cells:
            cid = cell["id"]
            base = artifact / "cells" / cid
            copy(base / "config.json", f"run/cells/{cid}/config.json")
            for version in versions:
                if version["cell"] != cid:
                    continue
                v = version["version"]
                src = base / f"v{v}"
                dst = f"run/cells/{cid}/v{v}"
                for name in ("version.json", "policy.py", "public-feedback.json", "bundle.json"):
                    copy(src / name, f"{dst}/{name}")
                agent_version = base / "agent/ws/versions" / f"v{v:04d}"
                for name in ("eval.json", "evidence.json", "submission.json", "decision.json"):
                    if (agent_version / name).is_file():
                        copy(agent_version / name, f"{dst}/{name}")
                for source in sorted((agent_version / "sources").rglob("*")):
                    if source.is_file() and safe_file(source):
                        copy(source, f"{dst}/sources/{source.relative_to(agent_version / 'sources')}")

            ws_executions = base / "agent/ws/executions"
            private_executions = base / "agent/private/executions"
            for execution in sorted(ws_executions.glob("e[0-9]*")):
                if not execution.is_dir():
                    continue
                eid = execution.name
                dest = f"run/cells/{cid}/executions/{eid}"
                for name in ("execution.json", "result.json", "evidence.json", "trace.json", "initial_observation.json",
                             "final_observation.json", "stdout.txt", "stderr.txt"):
                    if (execution / name).is_file():
                        copy(execution / name, f"{dest}/{name}")
                copy(execution / "result.json", f"{dest}/public-feedback.json")
                state_log = execution / "state_log.json"
                if state_log.is_file():
                    digest = hashlib.sha256(state_log.read_bytes()).hexdigest()
                    if digest in seen_state_logs:
                        omitted.append(f"{dest}/state_log.json duplicates {seen_state_logs[digest]} (sha256 {digest})")
                    else:
                        copy(state_log, f"{dest}/state_log.json")
                        seen_state_logs[digest] = f"{dest}/state_log.json"
                matching_versions = [version for version in versions if version["cell"] == cid
                                     and version["payload"].get("agent", {}).get("execution_id") == eid]
                if matching_versions:
                    verdict = matching_versions[0]["payload"].get("agent", {}).get("eval", {})
                    verdict_record = {"passed": verdict.get("passed"), "reason": verdict.get("reason"),
                                      "evidence": verdict.get("evidence", []), "version": matching_versions[0]["version"]}
                    verdict_source = str(run_root / "ledger.sqlite3") + f"#v2_versions:{cid}:{matching_versions[0]['version']}:agent.eval"
                else:
                    verdict_record = {"passed": None, "reason": "No Eval verdict recorded for this probe execution",
                                      "evidence": [], "version": None}
                    verdict_source = "generated:export_evidence_bundle.py (absence checked in v2_versions)"
                add(f"{dest}/eval-verdict.json", dump(verdict_record), verdict_source)
                for name in ("native_result.json", "scene-condition.json"):
                    matches = list((private_executions / eid).glob(f"openarm365-dev-*/{name}"))
                    if len(matches) != 1:
                        raise ValueError(f"expected one {name} for {eid}; got {len(matches)}")
                    copy(matches[0], f"{dest}/{name}")
                for video in sorted((private_executions / eid).glob("openarm365-dev-*/*.mp4")):
                    copy(video, f"{dest}/{video.name}")

        memory = db(memory_store)
        try:
            notes = jrows(memory, "memories")
            provenance = {row["memory_id"]: row["payload"] for row in jrows(memory, "memory_v2_provenance")}
            events = jrows(memory, "events")
            all_ids = {row["id"] for row in notes}
            if set(provenance) != all_ids:
                raise ValueError("memory provenance does not match note IDs")
            selected_events = {mid: [] for mid in all_ids}
            for event in events:
                if event["entity_id"] in all_ids and event["entity_type"] in ("memory", "memories"):
                    selected_events[event["entity_id"]].append(event)
            memory_data = {"source": "read-only SQLite export", "note_count": len(notes),
                           "event_count": sum(map(len, selected_events.values())),
                           "notes": [{"id": row["id"], "status": row["payload"].get("status"),
                                      "scope": row["payload"].get("applicability"), "record": row["payload"],
                                      "provenance": provenance[row["id"]], "events": selected_events[row["id"]]}
                                     for row in notes]}
            add("memory/notes.json", dump(memory_data), str(memory_store) + "#memories,memory_v2_provenance,events")
        finally:
            memory.close()

        make_report(stage, entries, run_id, cells, versions, memory_data, add)
        manifest = {"schema": "attentionbench.static-evidence-bundle.v1", "tool_version": VERSION,
                    "source_run_id": run_id, "source_memory_store": str(memory_store),
                    "files": sorted(entries, key=lambda item: item["path"]), "omitted": omitted,
                    "manifest_self_hash": "MANIFEST.json is excluded from its own file list to avoid a recursive digest"}
        manifest_bytes, _ = translate(dump(manifest))
        scan_report["files_scanned"] += 1
        for hit in scan_credentials(manifest_bytes, patterns):
            scan_report["hits"].append({"file": "MANIFEST.json", **hit})
        identities.extend(identity_hits("MANIFEST.json", manifest_bytes, hostname))
        (stage / "MANIFEST.json").write_bytes(manifest_bytes)

        counts = Counter(); sizes = Counter()
        for entry in entries:
            directory = entry["path"].split("/", 1)[0] if "/" in entry["path"] else "."
            counts[directory] += 1
            sizes[directory] += entry["size_bytes"]
        counts["."] += 1
        sizes["."] += len(manifest_bytes)
        scan_report["candidate_directories"] = {
            key: {"files": counts[key], "size_bytes": sizes[key]} for key in sorted(counts)}
        scan_report["identity_hits_by_kind"] = dict(Counter(hit["kind"] for hit in identities))
        scan_report["omitted"] = omitted
        scan_report["result"] = "blocked" if scan_report["hits"] else "clear"
        (out / "scan-report.json").write_bytes(dump(scan_report))
        with (out / "identity-hits.txt").open("w", encoding="utf-8") as handle:
            for hit in identities:
                handle.write(json.dumps(hit, ensure_ascii=False) + "\n")
        if scan_report["hits"]:
            raise RuntimeError(f"credential scan blocked export: {len(scan_report['hits'])} hit(s); see scan-report.json")

        asset = out / f"{run_id}-evidence.tar.gz"
        temp_asset = Path(tmp) / asset.name
        with temp_asset.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped:
                with tarfile.open(fileobj=zipped, mode="w") as archive:
                    for file in sorted(stage.rglob("*")):
                        if file.is_file():
                            info = tarfile.TarInfo(file.relative_to(stage).as_posix())
                            info.size = file.stat().st_size
                            info.mode = 0o644
                            info.mtime = 0
                            with file.open("rb") as reader:
                                archive.addfile(info, reader)
        size = temp_asset.stat().st_size
        if size > MAX_ASSET:
            raise RuntimeError(f"asset exceeds hard 2 GB limit: {size} bytes")
        shutil.copyfile(temp_asset, asset)
        shutil.copyfile(stage / "MANIFEST.json", out / "MANIFEST.json")
        return {"asset": str(asset), "size_bytes": size, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "directories": {key: {"files": counts[key], "size_bytes": sizes[key]} for key in sorted(counts)},
                "notes": len(notes), "events": memory_data["event_count"],
                "identity_hits": dict(Counter(hit["kind"] for hit in identities)), "scan": scan_report}


def make_report(stage: Path, entries: list[dict], run_id: str, cells: list[dict], versions: list[dict],
                memory_data: dict, add) -> None:
    esc = html.escape
    rows = []
    for version in versions:
        cid = version["cell"]; v = version["version"]
        root = f"run/cells/{cid}/v{v}"
        payload = version["payload"]
        agent = payload.get("agent", {})
        verdict = agent.get("eval", {}) if isinstance(agent, dict) else {}
        passed = verdict.get("passed", payload.get("passed", "unknown"))
        reason = verdict.get("reason", payload.get("reason", ""))
        sources = [e["path"] for e in entries if e["path"].startswith(root + "/sources/")]
        links = [f'<a href="{esc(root + "/" + name, quote=True)}">{esc(name)}</a>' for name in ("summary.json", "eval.json", "public-feedback.json")]
        links += [f'<a href="{esc(path, quote=True)}">Dev source</a>' for path in sources]
        rows.append(f"<tr><td>{v}</td><td>{esc(str(passed))}</td><td>{esc(str(reason))}</td><td>{' · '.join(links)}</td></tr>")
    executions = []
    for entry in entries:
        path = entry["path"]
        if re.search(r"/executions/e\d+/trace\.json$", path):
            root = path.rsplit("/", 1)[0]
            videos = [e["path"] for e in entries if e["path"].startswith(root + "/") and e["path"].endswith(".mp4")]
            links = [f'<a href="{esc(path, quote=True)}">trace</a>',
                     f'<a href="{esc(root + "/native_result.json", quote=True)}">native result</a>',
                     f'<a href="{esc(root + "/scene-condition.json", quote=True)}">scene condition</a>',
                     f'<a href="{esc(root + "/public-feedback.json", quote=True)}">public feedback</a>',
                     f'<a href="{esc(root + "/eval-verdict.json", quote=True)}">Eval verdict</a>']
            links += [f'<a href="{esc(video, quote=True)}">{esc(Path(video).name)}</a>' for video in videos]
            executions.append(f"<tr><td>{esc(Path(root).name)}</td><td>{' · '.join(links)}</td></tr>")
    note_rows = []
    for note in memory_data["notes"]:
        prov = note["provenance"]
        scope = note["scope"] or {}
        note_rows.append("<tr><td>" + esc(str(note["id"])) + "</td><td>" + esc(str(note["status"])) +
                         "</td><td>" + esc(str(scope.get("task_id", ""))) + " / seed " + esc(str(scope.get("seed", ""))) +
                         "</td><td><code>" + esc(str(prov.get("artifact_sha256", ""))) + "</code><br><code>" +
                         esc(str(prov.get("request_sha256", ""))) + "</code></td><td>" + str(len(note["events"])) + "</td></tr>")
    style = "body{font:15px system-ui,sans-serif;max-width:1200px;margin:auto;padding:2rem;color:#192331}banner{display:block;background:#fff0c2;padding:1rem;border:2px solid #9d6400}table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #bbb;padding:.5rem;text-align:left;vertical-align:top}code{overflow-wrap:anywhere}a{color:#164c9b}"
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AttentionBench evidence {esc(run_id)}</title><style>{style}</style><body><banner>{esc(BANNER)}</banner><h1>Author run {esc(run_id)}</h1><p>Static offline snapshot. {len(cells)} cell(s), {len(versions)} version(s), {len(executions)} execution(s), {len(note_rows)} Memory note(s). <a href="run/selection.json">Selection</a> · <a href="run/created.json">Created</a> · <a href="run/experiment.json">Experiment snapshot</a> · <a href="run/ledger.json">Ledger JSON</a> · <a href="memory/notes.json">Memory notes JSON</a></p><h2>Versions and Eval verdicts</h2><table><thead><tr><th>Version</th><th>Passed</th><th>Reason</th><th>Records</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Executions</h2><table><thead><tr><th>ID</th><th>Evidence and video</th></tr></thead><tbody>{''.join(executions)}</tbody></table><h2>Memory notes</h2><table><thead><tr><th>ID</th><th>Status</th><th>Scope</th><th>Provenance SHA-256: artifact / request</th><th>Events</th></tr></thead><tbody>{''.join(note_rows)}</tbody></table></body></html>'''
    add("index.html", page.encode(), "generated:export_evidence_bundle.py")
    readme = f"# Static evidence bundle\n\n{BANNER}\n\nRun: `{run_id}`. Open `index.html` from the unpacked directory. All links are local. `run/ledger.json` is a read-only JSON export; `memory/notes.json` contains note records, provenance and their events. SHA-256 fingerprints are source data, not proof of trust. `MANIFEST.json` lists each other file and its digest; it excludes itself because a self-digest is recursive. Per-execution `eval-verdict.json` marks probe executions with no recorded Eval as unevaluated.\n\nOmitted: raw frames, numba caches, probe-owner live data, snapshots/objects, registration roots, keys or tokens, and unrelated private artifacts. Distinct state logs are included once per SHA-256 because Eval evidence cites them.\n"
    add("README.md", readme.encode(), "generated:export_evidence_bundle.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--memory-store", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, action="append", default=[])
    parser.add_argument("--reviewed-false-positives", type=Path,
                        help="JSON list of human-reviewed generic-pattern hits (path, sha256, offset, pattern, note)")
    args = parser.parse_args()
    try:
        result = export(args.run_root, args.memory_store, args.out, extra_secret_files=args.secret_file,
                        reviewed_false_positives=args.reviewed_false_positives)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
