"""Local AttentionBench operator UI backed by the persisted AttentionStore.

Run with ``python -m benchmarks.attention_harness.ui_server --store PATH``.
``--demo`` serves the approved interactive sample without opening a store.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
from uuid import uuid4
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen

from .core.projection import AttentionProjection
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore, StateConflictError
from .core.models import RequestType
from .attention_modes import AssistanceMode, RequestState
from .ui_summary import comparison_summary
from .ui_media import (authorized_evidence_path, encoded_observation_frame,
                       render_evidence, robocasa_camera_frame)
from .ui_launch import load_catalog, public_options, launch_formal_run
from .ui_eval import eval_snapshot
from .public_station import read_public_station, read_public_frame, save_public_frame


UI_DIR = Path(__file__).with_name("ui")
GRAPH_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def read_orchestrator_json(base_url: str, path: str) -> Any:
    with urlopen(base_url.rstrip("/") + path, timeout=3) as response:
        return json.load(response)


def start_orchestrator_dag(base_url: str, graph: str) -> dict[str, Any]:
    """Start the connected Orchestrator's autonomous DAG, for its active graph only."""
    if not GRAPH_NAME.fullmatch(graph):
        raise ValueError("invalid graph name")
    snapshot = read_orchestrator_json(base_url, "/ui-snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("invalid Orchestrator snapshot")
    if snapshot.get("graph") != graph:
        raise StateConflictError("selected graph is not the active Orchestrator graph")
    request = Request(
        base_url.rstrip("/") + "/attention/auto-start",
        data=json.dumps({"graph": graph}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=10) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("graph") != graph or result.get("ok") is not True:
        raise ValueError("invalid Orchestrator dispatch response")
    return result


def read_orchestrator_sessions(base_url: str, graph: str) -> list[dict[str, Any]]:
    if not GRAPH_NAME.fullmatch(graph):
        raise ValueError("invalid graph name")
    with urlopen(base_url.rstrip("/") + "/sessions/" + quote(graph), timeout=10) as response:
        # Orchestrator serves persisted JSONL. Ignore a partial last line while
        # an agent is appending; the next refresh will pick it up.
        sessions = []
        for line in response:
            if not line.strip():
                continue
            try:
                sessions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return sessions


def list_runs(store: AttentionStore) -> list[dict[str, Any]]:
    ids = {
        event["entity_id"]
        for event in store.events()
        if event["entity_type"] in {"run", "runs"}
    }
    runs = [store.get_run(run_id) for run_id in ids]
    return sorted(
        (
            {
                "run_id": run["run_id"],
                "suite": run["suite"],
                "task_id": run["task_id"],
                "status": run["status"],
                "created_at": run["created_at"],
            }
            for run in runs
            if run is not None
        ),
        key=lambda item: (item["created_at"], item["run_id"]),
        reverse=True,
    )


def dashboard_snapshot(store: AttentionStore, run_id: str, *, artifact_root: Path | None = None,
                       robosuite_url: str | None = None,
                       robocasa_camera_ws: str | None = None) -> dict[str, Any]:
    """Expose only UI fields; never send native outcome or raw trace to the browser."""
    projection = AttentionProjection(store).snapshot(run_id)
    run = store.get_run(run_id)
    assert run is not None

    traces = {}
    for trace in projection["request_detail"]["traces"]:
        traces[trace["trace_id"]] = {
            "trace_id": trace["trace_id"],
            "attempt_id": trace["attempt_id"],
            "execution_id": trace.get("execution_id"),
            "agent_state": trace.get("agent_state"),
            "hypothesis": trace.get("hypothesis"),
            "hypothesis_status": trace.get("hypothesis_status", "unknown"),
            "failure": {
                key: trace.get("failure", {}).get(key)
                for key in ("stage", "error_type", "message", "observed_symptom")
            },
            "events": [
                {
                    key: event.get(key)
                    for key in ("timestamp", "event_type", "operation", "status")
                }
                for event in trace.get("events", [])
            ],
            "evidence": [
                {"evidence_id": evidence.get("evidence_id"), "kind": evidence.get("kind"),
                 "sha256": evidence.get("sha256"), "mime_type": evidence.get("mime_type"),
                 "media_url": (f"/api/traces/{quote(trace['trace_id'], safe='')}/evidence/"
                               f"{quote(evidence['evidence_id'], safe='')}"
                               if artifact_root is not None and evidence.get("mime_type") in
                               {"application/x-npz", "image/png", "image/jpeg", "video/mp4", "video/webm"}
                               else None)}
                for evidence in trace.get("evidence", [])
            ],
            "memory_refs": list(trace.get("memory_refs", [])),
        }

    requests = [
        {
            key: request.get(key)
            for key in (
                "request_id", "attempt_id", "trace_id", "request_type", "reason",
                "priority", "created_at", "deadline_at", "mode", "state", "response_id",
            )
        }
        for request in projection["attention_inbox"]
    ]
    responses = {}
    uses = {}
    lifecycle = {}
    visible_request_ids = {request["request_id"] for request in requests}
    for event in store.events():
        if (event["event_type"] in {"response.used", "response.execution_linked"}
                and event["entity_id"] in visible_request_ids):
            uses[event["entity_id"]] = event["payload"]
        if (event["event_type"] in {"request.deferred", "request.timeout_observed",
                                    "request.fallback", "request.cancelled"}
                and event["entity_id"] in visible_request_ids):
            lifecycle.setdefault(event["entity_id"], []).append({
                "event_type": event["event_type"], "payload": event["payload"]})
    for request in requests:
        response_id = request.get("response_id")
        if response_id:
            response = store.get_response(response_id)
            if response is not None:
                responses[response_id] = {
                    key: response.get(key)
                    for key in ("response_id", "responder", "content", "created_at")
                }

    budgets = projection["resource_budget"]
    resources = {
        key: {
            "limit": value["limit"],
            "used": value["used"],
            "reserved": value.get("reserved") if key == "assistance" else None,
            "remaining": value["remaining"],
        }
        for key, value in budgets.items()
    }
    public_station = read_public_station(
        artifact_root, run_id, run["suite"],
        {item["attempt_id"] for item in projection["autonomous_work"]["attempts"]},
        allow_pending=run["status"] == "running",
    ) if artifact_root is not None else None
    return {
        "schema_version": "attentionbench.dashboard.v1",
        "run": {
            "run_id": run_id,
            "suite": run["suite"],
            "task_id": run["task_id"],
            "seed": run["seed"],
            "status": run["status"],
            "policy_id": run["policy_id"],
            "execution_target": run["execution_target"],
            "assistance_mode": run["assistance_mode"],
            "developer_model": run.get("developer_model"),
            "evaluator_model": run.get("evaluator_model"),
            "locked": projection["run_context"]["locked"],
        },
        "resources": resources,
        "attempts": [
            {
                key: attempt.get(key)
                for key in ("attempt_id", "index", "started_at", "ended_at", "status")
            }
            for attempt in projection["autonomous_work"]["attempts"]
        ],
        "requests": requests,
        "traces": traces,
        "responses": responses,
        "response_uses": uses,
        "request_lifecycle": lifecycle,
        "station": {**projection["live_station"],
                    "camera_url": f"/api/station/frame?run={quote(run_id, safe='')}" if (
                        public_station is not None or
                        (robosuite_url and run["suite"] == "robosuite") or
                        (robocasa_camera_ws and run["suite"] == "robocasa")
                    ) else None,
                    "interrupt": store.interrupt_status(run_id)},
        "work_items": store.list_work_items(run_id),
        "work_source": "attempts_and_persisted_work_events",
        "media_source": "advisor_artifacts" if artifact_root is not None else None,
        "comparison": comparison_summary(store, run_id),
        "eval": eval_snapshot(artifact_root, run_id, run, store.path)
                if artifact_root is not None else {"status": "unavailable"},
    }


def create_server(
    *, store: AttentionStore | None = None, host: str = "127.0.0.1", port: int = 8769,
    orchestrator_url: str | None = None, artifact_root: Path | None = None,
    robosuite_url: str | None = None, camera_name: str | None = None,
    robocasa_camera_ws: str | None = None, robocasa_camera_device: str = "maniskill_base",
    run_catalog: Path | None = None,
) -> ThreadingHTTPServer:
    control_token = secrets.token_urlsafe(32)
    profiles = load_catalog(run_catalog) if run_catalog is not None else []

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK,
                  extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in {"/", "/ui"}:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/ui/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/ui/":
                page = (UI_DIR / "index.html").read_text(encoding="utf-8")
                if store is not None:
                    page = page.replace('data-mode="demo"', 'data-mode="live"', 1)
                    page = page.replace('data-mode="live"',
                                        f'data-mode="live" data-control-token="{control_token}"', 1)
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/ui/live.js":
                self._send((UI_DIR / "live.js").read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/api/orchestrator/snapshot":
                if orchestrator_url is None:
                    self._json({"error": "Orchestrator not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                try:
                    self._json(read_orchestrator_json(orchestrator_url, "/ui-snapshot"))
                except (HTTPError, URLError, TimeoutError, ValueError) as error:
                    self._json({"error": f"Orchestrator unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
            elif path == "/api/orchestrator/sessions":
                if orchestrator_url is None:
                    self._json({"error": "Orchestrator not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                graph = parse_qs(parsed.query).get("graph", [""])[0]
                if not GRAPH_NAME.fullmatch(graph):
                    self._json({"error": "invalid graph name"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._json({"graph": graph, "sessions": read_orchestrator_sessions(orchestrator_url, graph)})
                except HTTPError as error:
                    self._json({"error": "graph not found" if error.code == 404 else str(error)},
                               HTTPStatus.NOT_FOUND if error.code == 404 else HTTPStatus.BAD_GATEWAY)
                except (URLError, TimeoutError, ValueError) as error:
                    self._json({"error": f"Orchestrator unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
            elif path == "/api/runs" and store is not None:
                self._json({"runs": list_runs(store), "launches": store.list_launches()})
            elif path == "/api/run-options" and store is not None:
                self._json(public_options(profiles))
            elif path == "/api/station/frame" and store is not None:
                try:
                    run_id = parse_qs(urlsplit(self.path).query).get("run", [""])[0]
                    run = store.get_run(run_id)
                    if run is None:
                        self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                        return
                    attempts = AttentionProjection(store).snapshot(run_id)["autonomous_work"]["attempts"]
                    public_station = read_public_station(
                        artifact_root, run_id, run["suite"],
                        {item["attempt_id"] for item in attempts},
                        allow_pending=run["status"] == "running",
                    ) if artifact_root is not None else None
                    if public_station is not None and artifact_root is not None:
                        run_dir = artifact_root / run_id.removeprefix("run:")
                        cached = read_public_frame(run_dir, run["suite"])
                        if run["status"] != "running":
                            if cached is None:
                                raise ValueError("no archived public RGB frame")
                            self._send(*cached)
                        else:
                            try:
                                if run["suite"] == "robosuite":
                                    with urlopen(public_station["origin"] + "/v1/observation", timeout=3) as response:
                                        frame = encoded_observation_frame(
                                            json.load(response), public_station.get("camera_name"))
                                    mime = "image/png"
                                else:
                                    frame = robocasa_camera_frame(
                                        public_station["origin"],
                                        device_id=public_station["device_id"])
                                    mime = "image/jpeg"
                                save_public_frame(run_dir, suite=run["suite"], frame=frame)
                                self._send(frame, mime)
                            except Exception:
                                if cached is None:
                                    raise
                                self._send(*cached)
                    elif run["suite"] == "robosuite" and robosuite_url:
                        with urlopen(robosuite_url.rstrip("/") + "/v1/observation", timeout=3) as response:
                            frame = encoded_observation_frame(json.load(response), camera_name)
                        self._send(frame, "image/png")
                    elif run["suite"] == "robocasa" and robocasa_camera_ws:
                        frame = robocasa_camera_frame(robocasa_camera_ws,
                                                     device_id=robocasa_camera_device)
                        self._send(frame, "image/jpeg")
                    else:
                        self._json({"error": "camera not configured for this suite"}, HTTPStatus.SERVICE_UNAVAILABLE)
                except (HTTPError, URLError, TimeoutError, ValueError, KeyError, OSError) as error:
                    self._json({"error": f"camera unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
            elif path.startswith("/api/traces/") and store is not None and artifact_root is not None:
                parts = path.removeprefix("/api/traces/").split("/evidence/")
                if len(parts) != 2:
                    self._json({"error": "invalid evidence path"}, HTTPStatus.BAD_REQUEST)
                    return
                trace_id, evidence_id = (unquote(part) for part in parts)
                if any("/" in part or "\\" in part for part in (trace_id, evidence_id)):
                    self._json({"error": "invalid evidence id"}, HTTPStatus.BAD_REQUEST)
                    return
                trace = store.get_trace(trace_id)
                if trace is None:
                    self._json({"error": "trace not found"}, HTTPStatus.NOT_FOUND)
                    return
                try:
                    media_path, mime_type = authorized_evidence_path(artifact_root, trace, evidence_id)
                    payload, served_type = render_evidence(media_path, mime_type)
                    if served_type.startswith("video/"):
                        byte_range = self.headers.get("Range")
                        if byte_range:
                            match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                            if match is None or int(match[1]) >= len(payload):
                                self._send(b"", served_type, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                                           {"Content-Range": f"bytes */{len(payload)}"})
                                return
                            start = int(match[1])
                            end = min(int(match[2]) if match[2] else len(payload) - 1,
                                      len(payload) - 1)
                            if end < start:
                                self._send(b"", served_type, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                                           {"Content-Range": f"bytes */{len(payload)}"})
                                return
                            self._send(payload[start:end + 1], served_type, HTTPStatus.PARTIAL_CONTENT,
                                       {"Accept-Ranges": "bytes",
                                        "Content-Range": f"bytes {start}-{end}/{len(payload)}"})
                            return
                        self._send(payload, served_type, extra_headers={"Accept-Ranges": "bytes"})
                    else:
                        self._send(payload, served_type)
                except FileNotFoundError:
                    self._json({"error": "evidence not found"}, HTTPStatus.NOT_FOUND)
                except ValueError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
            elif path.startswith("/api/runs/") and store is not None:
                run_id = unquote(path.removeprefix("/api/runs/"))
                if not run_id or "/" in run_id or "\\" in run_id:
                    self._json({"error": "invalid run id"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._json(dashboard_snapshot(store, run_id, artifact_root=artifact_root,
                                                  robosuite_url=robosuite_url,
                                                  robocasa_camera_ws=robocasa_camera_ws))
                except StateConflictError:
                    self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            is_response = path.startswith("/api/requests/") and path.endswith("/respond")
            is_defer = path.startswith("/api/requests/") and path.endswith("/defer")
            is_cancel = path.startswith("/api/requests/") and path.endswith("/cancel")
            is_interrupt = path.startswith("/api/runs/") and path.endswith("/interrupt")
            is_dag_start = path == "/api/orchestrator/dispatch"
            is_run_start = path == "/api/runs/start"
            if store is None or not (is_response or is_defer or is_cancel or is_interrupt or is_dag_start or is_run_start):
                self._json({"error": "unsupported write"}, HTTPStatus.METHOD_NOT_ALLOWED)
                return
            if not secrets.compare_digest(self.headers.get("X-Attention-Token", ""), control_token):
                self._json({"error": "control token required"}, HTTPStatus.FORBIDDEN)
                return
            if is_run_start:
                if not profiles or artifact_root is None:
                    self._json({"error": "run catalog and artifact root are required"},
                               HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                try:
                    if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                        raise ValueError("JSON body required")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 4096:
                        raise ValueError("run selection must be 1–4096 bytes")
                    selection = json.loads(self.rfile.read(size))
                    if not isinstance(selection, dict):
                        raise ValueError("run selection must be an object")
                    self._json(launch_formal_run(selection, profiles=profiles, store=store,
                                                 artifact_root=artifact_root))
                except (ValueError, TypeError, KeyError, PermissionError, json.JSONDecodeError) as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                except OSError as error:
                    self._json({"error": f"launch failed: {error}"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if is_dag_start:
                if orchestrator_url is None:
                    self._json({"error": "Orchestrator not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    self._json({"error": "JSON body required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 512:
                        raise ValueError("dispatch body must be 1–512 bytes")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict) or not isinstance(body.get("graph"), str):
                        raise ValueError("graph is required")
                    self._json(start_orchestrator_dag(orchestrator_url, body["graph"]))
                except (ValueError, json.JSONDecodeError) as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                except StateConflictError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                except (HTTPError, URLError, TimeoutError) as error:
                    self._json({"error": f"Orchestrator unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
                return
            if is_interrupt:
                run_id = unquote(path[len("/api/runs/"):-len("/interrupt")]).strip("/")
                if not run_id or "/" in run_id or "\\" in run_id:
                    self._json({"error": "invalid run id"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    run = store.get_run(run_id)
                    if run is None:
                        self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                        return
                    if run["execution_target"] not in {"robocasa_sim", "robosuite_sim"}:
                        raise StateConflictError("only connected simulator runs accept this interrupt")
                    result = store.request_interrupt(run_id, event_key=f"interrupt:{uuid4().hex}",
                                                     requested_at=time.time())
                    self._json(result)
                except StateConflictError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            suffix = "/respond" if is_response else "/defer" if is_defer else "/cancel"
            request_id = unquote(path[len("/api/requests/"):-len(suffix)]).strip("/")
            if not request_id or "/" in request_id or "\\" in request_id:
                self._json({"error": "invalid request id"}, HTTPStatus.BAD_REQUEST)
                return
            if is_defer or is_cancel:
                try:
                    if is_defer:
                        self._json(store.defer_request(request_id, deferred_at=time.time()))
                    else:
                        request = store.get_request(request_id)
                        if request is None or request.mode is not AssistanceMode.LIVE_HUMAN_FIRST:
                            raise StateConflictError("only human requests can be cancelled here")
                        runtime = AttentionRuntime(store, None, clock=time.time)
                        updated = runtime.cancel(request_id)
                        self._json({"request_id": request_id, "state": updated.state.value})
                except StateConflictError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._json({"error": "JSON body required"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError("request body must be 1–8192 bytes")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("request body must be an object")
                request = store.get_request(request_id)
                if request is None:
                    self._json({"error": "request not found"}, HTTPStatus.NOT_FOUND)
                    return
                if request.mode is not AssistanceMode.LIVE_HUMAN_FIRST:
                    raise StateConflictError("only live-human-first requests accept human responses")
                if request.state is not RequestState.PENDING:
                    raise StateConflictError("request is no longer pending")
                if request.request_type is RequestType.HINT:
                    content = body.get("content")
                    if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                        raise ValueError("hint content must contain 1–4000 characters")
                    content = content.strip()
                elif request.request_type is RequestType.APPROVAL:
                    if body.get("decision") not in {"approve", "deny"}:
                        raise ValueError("approval decision must be approve or deny")
                    content = body["decision"]
                else:
                    if body.get("decision") != "interrupt":
                        raise ValueError("interrupt response must be interrupt")
                    content = "interrupt"
                runtime = AttentionRuntime(store, None, clock=time.time)
                updated = runtime.submit_human_response(
                    request_id, response_id=f"human-response:{uuid4().hex}", content=content,
                )
                self._json({"request_id": request_id, "state": updated.state.value,
                            "response_id": updated.response_id})
            except (ValueError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except StateConflictError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--store", type=Path, help="existing AttentionStore SQLite path")
    source.add_argument("--demo", action="store_true", help="interactive sample data only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--orchestrator-url", help="read-only Orchestrator HTTP API, e.g. http://127.0.0.1:8766")
    parser.add_argument("--artifact-root", type=Path, help="parent of episode artifact directories")
    parser.add_argument("--run-catalog", type=Path,
                        help="operator-approved formal simulator profiles for UI launch")
    parser.add_argument("--robosuite-url", help="Robosuite simulator service origin for public RGB camera")
    parser.add_argument("--camera-name", help="camera name, e.g. agentview")
    parser.add_argument("--robocasa-camera-ws", help="ManiSkill camera bridge URL, e.g. ws://127.0.0.1:5580")
    parser.add_argument("--robocasa-camera-device", default="maniskill_base",
                        help="camera bridge device ID: maniskill_base or maniskill_wrist")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("this local operator console binds only to loopback")
    if args.store is not None and not args.store.is_file():
        parser.error(f"AttentionStore does not exist: {args.store}")
    if args.artifact_root is not None and not args.artifact_root.is_dir():
        parser.error(f"artifact root does not exist: {args.artifact_root}")
    if args.run_catalog is not None and (args.store is None or args.artifact_root is None):
        parser.error("--run-catalog requires --store and --artifact-root")
    store = AttentionStore(args.store) if args.store is not None else None
    if args.orchestrator_url:
        url = urlsplit(args.orchestrator_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.path not in {"", "/"}:
            parser.error("--orchestrator-url must be an http(s) origin without a path")
    if args.robosuite_url:
        url = urlsplit(args.robosuite_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.path not in {"", "/"}:
            parser.error("--robosuite-url must be an http(s) origin without a path")
    if args.robocasa_camera_ws:
        url = urlsplit(args.robocasa_camera_ws)
        if url.scheme not in {"ws", "wss"} or not url.netloc or url.path not in {"", "/"}:
            parser.error("--robocasa-camera-ws must be a ws(s) origin without a path")
    if args.robocasa_camera_device not in {"maniskill_base", "maniskill_wrist"}:
        parser.error("--robocasa-camera-device must be maniskill_base or maniskill_wrist")
    server = create_server(store=store, host=args.host, port=args.port,
                           orchestrator_url=args.orchestrator_url,
                           artifact_root=args.artifact_root, robosuite_url=args.robosuite_url,
                           camera_name=args.camera_name,
                           robocasa_camera_ws=args.robocasa_camera_ws,
                           robocasa_camera_device=args.robocasa_camera_device,
                           run_catalog=args.run_catalog)
    print(f"AttentionBench UI: http://{args.host}:{server.server_port}/ui/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
