"""Local AttentionBench operator UI backed by the persisted AttentionStore.

Run with ``python -m benchmarks.attention_harness.ui_server --store PATH``.
``--demo`` serves the approved interactive sample without opening a store.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import io
import json
import math
import secrets
import threading
import time
from datetime import datetime, timezone
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
from .gateway_auth import OwnerError, control_token_for, owner_of, read_gateway_secret
from .ui_summary import comparison_summary
from .ui_media import (authorized_evidence_path, encoded_observation_frame,
                       render_evidence, robocasa_camera_frame, robocasa_human_rgb, rgb_jpeg, rgb_png)
from .ui_launch import load_catalog, public_options, launch_formal_run
from .ui_eval import eval_snapshot
from .public_station import read_public_station, read_public_frame, save_public_frame
from .live_preview import read_preview
from .hint_compilation import prepare_human_guidance
from .attention_control import AttentionControl, run_identity
from .ui_history import UIHistory, name_suffix
from .dev_attention import DevAttentionControl
from .fixed_ui import FixedUI
from .memory_view import ACTIONS as MEMORY_ACTIONS, MemoryView
from .gpu_meter import COVERAGE as GPU_COVERAGE
from .run_recovery import recover_lost_runs
from .request_lifecycle import request_timeline
from .codex_quota import read_quota
from .notices import NoticeReader


UI_DIR = Path(__file__).with_name("ui")
FREE_VIEW_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                 "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
TASK_PICTURE_CACHE = "private, max-age=300"  # task picker JPEGs only; everything else stays no-store
TASK_PICTURE = re.compile(r"/ui/tasks/(?:[a-z_]{1,64}-\d{3,4}-(?:start|done)\.jpg|manifest\.json)")
GRAPH_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def openarm_camera_frame(station):
    import numpy as np
    with urlopen(station["origin"] + "/camera", timeout=3) as response:
        raw = response.read()
    with np.load(io.BytesIO(raw), allow_pickle=False) as observation:
        state = json.loads(str(observation["state_json"]))
        if state.get("robot") != station["robot"]:
            raise ValueError("OpenArm camera identity differs from locked run")
        return rgb_png(observation["rgb_u8"])


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
    recover_lost_runs(store)
    # Sidebar metadata comes from current materialized rows. Reading all event
    # payloads also parses every physical trace and delays the entire UI.
    with store._connect() as db:
        ids=[row[0] for row in db.execute('SELECT id FROM runs')]
        requests=[json.loads(row[0]) for row in db.execute('SELECT payload FROM requests')]
    pending={}
    for request in requests:
        if request['state'] in {'pending','fallback'}:
            owner=request['run_id'];pending[owner]=pending.get(owner,0)+1
    runs = [store.get_run(run_id) for run_id in ids]
    return sorted(
        (
            {
                "run_id": run["run_id"],
                "suite": run["suite"],
                "task_id": run["task_id"],
                "status": run["status"],
                "created_at": run["created_at"],
                **run_identity(store,run['run_id']),
                'attention_control': AttentionControl(store).snapshot(run['run_id']),
                'pending_hints': pending.get(run['run_id'], 0),
                'interrupt': store.interrupt_status(run['run_id']),
            }
            for run in runs
            if run is not None
        ),
        key=lambda item: (item["created_at"], item["run_id"]),
        reverse=True,
    )


def _recording_summary(root: Path | None, trace: dict[str, Any]) -> dict[str, Any] | None:
    if root is None:
        return None
    evidence = next((e for e in trace.get("evidence", []) if e.get("kind") == "camera_recording"), None)
    if evidence is None:
        return None
    try:
        path, mime = authorized_evidence_path(root, trace, evidence["evidence_id"])
        if mime != "application/json" or path.stat().st_size > 524_288:
            raise ValueError("invalid recording receipt")
        value = json.loads(path.read_text())
        if value.get("schema_version") != "attentionbench.camera-recording.v1":
            raise ValueError("invalid recording schema")
        summary = {key: value.get(key) for key in ("source", "source_width", "source_height",
            "status", "reason", "execution_status", "control_interface_status", "duration_seconds", "run_id", "attempt_id",
            "requested_fps", "frame_count", "sampled_seconds", "initial_frame_saved",
            "final_frame_saved", "video_saved", "truncated", "error", "views", "actual_capture_fps", "max_gap_seconds")}
        timing = value.get('replay_timing')
        if timing is not None:
            import math
            if (not isinstance(timing, dict) or timing.get('basis') != 'relative'
                    or timing.get('event_clock') != 'sdk_completion'):
                raise ValueError('invalid replay clock')
            traces, videos = timing.get('frame_trace_seconds'), timing.get('frame_video_seconds')
            if (not isinstance(traces, list) or not isinstance(videos, list)
                    or not 1 <= len(traces) <= 5000 or len(traces) != len(videos)
                    or any(type(t) not in (int, float) or not math.isfinite(t) for t in traces + videos)
                    or videos[0] != 0 or any(t < 0 for t in videos)
                    or any(b <= a for a, b in zip(traces, traces[1:]))
                    or any(b <= a for a, b in zip(videos, videos[1:]))):
                raise ValueError('invalid replay frame times')
            summary['replay_timing'] = {'basis': 'relative',
                'event_clock': 'sdk_completion', 'frame_trace_seconds': traces,
                'frame_video_seconds': videos}
        binding = value.get('control_recording')
        if binding is not None:
            control = next((e for e in trace.get('evidence', []) if e.get('kind') == 'bimanual_control'), None)
            if not isinstance(binding, dict) or control is None or binding.get('sha256') != control.get('sha256'):
                raise ValueError('Invalid hand replay binding')
            summary['control_recording'] = {'url': f"/api/traces/{quote(trace['trace_id'],safe='')}/evidence/{quote(control['evidence_id'],safe='')}",
                'sha256':control['sha256'],'schema':binding['schema'],'sample_count':binding['sample_count']}
        return summary
    except (OSError, ValueError, KeyError, TypeError):
        return {"error": "Recording receipt missing or failed validation"}


def saved_execution_camera(root, traces, attempt):
    """Resolve existing Advisor-visible camera evidence without backfilling a run."""
    if root is None or attempt is None:
        return None
    trace = next((t for t in traces if t.get("attempt_id") == attempt["attempt_id"]), None)
    if trace is None:
        return None
    for kind in ("camera_final", "camera_initial"):
        for evidence in trace.get("evidence", []):
            if evidence.get("kind") != kind:
                continue
            try:
                _, mime = authorized_evidence_path(root, trace, evidence["evidence_id"])
                if mime not in {"image/png", "image/jpeg"}:
                    continue
                return {"camera_url": f"/api/traces/{quote(trace['trace_id'], safe='')}/evidence/{quote(evidence['evidence_id'], safe='')}",
                        "camera_attempt_id": attempt["attempt_id"], "camera_source": "saved_evidence",
                        "camera_image_transform": None if (_recording_summary(root, trace) or {}).get("source") ==
                            "official_simulator_display_rgb_after_each_control_step" else "flip_y"}
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return None


def arm_control_options(root,run_id,station):
    if root is None or station is None or station.get('robot',{}).get('execution_arm')!='both':return None
    try:
        index=station['attempt_id'].split(':')[-1]
        if not index.isdecimal() or not 0<=int(index)<10:return None
        path=root/run_id.removeprefix('run:')/f'public_control-{index}.json'
        if path.is_symlink() or path.stat().st_size>16384:return None
        value=json.loads(path.read_text())
        if value.get('run_id')!=run_id or value.get('attempt_id')!=station['attempt_id']:return None
        return value['control']
    except (OSError,ValueError,KeyError,TypeError):return None


def camera_view_options(root,run_id,station):
    if root is None or station is None:return None
    try:
        index=station['attempt_id'].split(':')[-1]
        if not index.isdecimal() or not 0<=int(index)<10:return None
        path=root/run_id.removeprefix('run:')/f'public_views-{index}.json'
        if path.is_symlink() or path.stat().st_size>8192:return None
        value=json.loads(path.read_text())
        if value.get('run_id')!=run_id or value.get('attempt_id')!=station['attempt_id']:return None
        views=value.get('views')
        if not isinstance(views,dict) or set(views) not in ({'chest','wrist','external'},{'chest','external','left_wrist','right_wrist'}):return None
        prefix=f"/api/station/preview-frame?run={quote(run_id,safe='')}&attempt={quote(station['attempt_id'],safe='')}"
        return {key:{'name':str(row['name']),'width':row['width'],'height':row['height'],
            'preview_url':prefix+'&view='+key} for key,row in views.items()}
    except (OSError,ValueError,KeyError,TypeError):return None


def memory_snapshot(store: AttentionStore, run_id: str, projection: dict[str, Any],
                    memory_view: MemoryView | None) -> dict[str, Any]:
    """Memory of one run as the agent saw it: candidates made, versions retrieved, records.

    Records come from the Memory Service itself; this only adds the run's links
    (which request produced a candidate, which attempt retrieved which version).
    """
    traces = projection["request_detail"]["traces"]
    uses = []
    for trace in traces:
        for event in trace.get("events", []):
            if event.get("event_type") == "attention.memory_retrieval":
                uses.append({"attempt_id": trace["attempt_id"],
                             "memory_id": (event.get("arguments") or {}).get("memory_id"),
                             "memory_version": (event.get("result") or {}).get("memory_version"),
                             "grant_id": (event.get("result") or {}).get("grant_id")})
    ingestion = [dict(event["payload"]) for event in store.events_for_run(run_id)
                 if event["entity_type"] == "memory_ingestion"]
    ids = list(dict.fromkeys(
        [item["memory_id"] for item in ingestion if item.get("memory_id")]
        + [item["memory_id"] for item in uses if item["memory_id"]]
        + [ref for trace in traces for ref in trace.get("memory_refs", [])]))
    return {
        "control": None if memory_view is None else memory_view.public(),
        "ingestion": ingestion, "uses": uses,
        "records": {} if memory_view is None else {memory_id: memory_view.detail(memory_id)
                                                   for memory_id in ids},
    }


def permitted_actions(request: dict[str, Any], deferred: bool, now: float) -> list[str]:
    """What an operator may still do with a request, decided here rather than in the page."""
    expired = request.get("deadline_at") is not None and now >= request["deadline_at"]
    if request["mode"] != "live_human_first" or request["state"] != "pending" or expired:
        return []
    reply = {"hint": "respond_hint", "approval": "approve_or_deny", "interrupt": "confirm_interrupt"}
    return [reply[request["request_type"]], *([] if deferred else ["defer"]), "ignore", "cancel"]


def run_lock(store: AttentionStore, run: dict[str, Any], run_id: str) -> dict[str, Any]:
    """The conditions fixed when the run started, and whether they can still change."""
    options = store.scheduler_options(run_id) or {}
    switchable = False
    try:
        control = AttentionControl(store).snapshot(run_id)
        switchable = bool(control.get("binding")) and not control.get("read_only")
    except StateConflictError:
        pass
    mode = AssistanceMode(run["assistance_mode"])
    from .attention_modes import MODE_SPECS
    return {
        "locked": run["status"] != "created",
        "conditions": {
            "suite": run["suite"], "task_id": run["task_id"], "seed": run["seed"],
            "policy_id": run["policy_id"], "assistance_mode": run["assistance_mode"],
            "execution_target": run["execution_target"],
            "request_mode": options.get("request_mode"),
            "approval_rules": options.get("approval_rules"),
            "budget": run["budget"],
        },
        # An engineering binding lets an operator switch policy mid-run; such a run is not comparable.
        "policy_switchable": switchable,
        "ranking_eligible": MODE_SPECS[mode].primary_ranking_eligible and not switchable,
    }


def autonomous_work(attempts: list[dict[str, Any]], requests: list[dict[str, Any]],
                    work_items: list[dict[str, Any]]) -> dict[str, Any]:
    """What the agent is doing on its own, and what holds it back (resources, failures, replies)."""
    open_requests = [request for request in requests if request["state"] in {"pending", "fallback"}]
    blocked = [{"kind": "resource", **item} for item in work_items if item.get("state") == "blocked"]
    blocked += [{"kind": "failure", **item} for item in work_items if item.get("state") == "failed"]
    continuing = []
    for request in open_requests:
        waits = str((request.get("explanation") or {}).get("while_waiting", "")).startswith("The agent waits")
        entry = {"request_id": request["request_id"], "request_type": request["request_type"],
                 "state": request["state"]}
        # A gated request holds work back; an asynchronous one lets work continue.
        (blocked.append({"kind": "dependency", "depends_on": request["request_id"],
                         "waiting_for": request["request_type"]})
         if waits else continuing.append(entry))
    return {
        "running": [a for a in attempts if a.get("status") == "running"],
        "completed": sum(1 for a in attempts if a.get("status") not in {None, "running"}),
        "continuing_while_waiting": continuing,
        "blocked": blocked,
    }


def dashboard_snapshot(store: AttentionStore, run_id: str, *, artifact_root: Path | None = None,
                       robosuite_url: str | None = None,
                       robocasa_camera_ws: str | None = None,
                       memory_view: MemoryView | None = None) -> dict[str, Any]:
    """Expose Advisor projections and separately revalidated Eval outcomes."""
    run_events = store.events_for_run(run_id)
    projection = AttentionProjection(store).snapshot(run_id, events=run_events)
    run = store.get_run(run_id)
    assert run is not None

    traces = {}
    # The projection contains all Advisor-visible attempts, not only requests.
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
                    for key in ("event_id", "sequence", "timestamp", "timestamp_basis", "event_type", "operation", "status", "arguments")
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
            "recording": _recording_summary(artifact_root, trace),
            "projection": {key: trace.get('projection', {}).get(key) for key in ('source_sha256', 'projection_sha256', 'source_complete', 'included_event_count', 'omitted_event_count')},
        }

    requests = [
        {
            key: request.get(key)
            for key in (
                "request_id", "attempt_id", "trace_id", "request_type", "reason",
                "priority", "created_at", "deadline_at", "mode", "state", "response_id",
                "explanation", "selected_moments", "localization",
            )
        }
        for request in projection["attention_inbox"]
    ]
    responses = {}
    uses = {}
    lifecycle = {}
    budget_events = []
    visible_request_ids = {request["request_id"] for request in requests}
    for event in run_events:
        if event["event_type"] == "budget.released" and event["entity_id"] == run_id:
            budget_events.append({"sequence": event["sequence"], "event_type": event["event_type"], "payload": event["payload"]})
        if (event["event_type"] in {"response.used", "response.execution_linked"}
                and event["entity_id"] in visible_request_ids):
            uses[event["entity_id"]] = event["payload"]
        if (event["event_type"] in {"request.deferred", "request.timeout_observed",
                                    "request.fallback", "request.cancelled", "request.ignored"}
                and event["entity_id"] in visible_request_ids):
            lifecycle.setdefault(event["entity_id"], []).append({
                "event_type": event["event_type"], "payload": event["payload"]})
    now = time.time()
    for request in requests:
        request["permitted_actions"] = permitted_actions(
            request, any(item["event_type"] == "request.deferred"
                         for item in lifecycle.get(request["request_id"], [])), now)
        response_id = request.get("response_id")
        if response_id:
            response = store.get_response(response_id)
            if response is not None:
                responses[response_id] = {
                    key: response.get(key)
                    for key in ("response_id", "responder", "content", "created_at")
                }

    budgets = projection["resource_budget"]
    held = store.resource_reservations(run_id)
    resources = {
        key: {
            "limit": value["limit"],
            "used": value["used"],
            "reserved": value.get("reserved") if key == "assistance" else held.get(key, 0.0),
            "remaining": value["remaining"],
            **({"budgeted": value["budgeted"], "coverage": GPU_COVERAGE}
               if key == "gpu_seconds" else {}),
        }
        for key, value in budgets.items()
    }
    public_station = read_public_station(
        artifact_root, run_id, run["suite"],
        {item["attempt_id"] for item in projection["autonomous_work"]["attempts"]},
        allow_pending=run["status"] == "running",
    ) if artifact_root is not None else None
    latest_attempt = max(projection['autonomous_work']['attempts'], key=lambda item: item['index'], default=None)
    latest_trace = next((item for item in traces.values() if latest_attempt and item['attempt_id'] == latest_attempt['attempt_id']), None)
    latest_execution = ({'attempt_id': latest_attempt['attempt_id'], 'execution_id': latest_trace.get('execution_id') if latest_trace else None}
                        if latest_attempt else None)
    evaluation = eval_snapshot(artifact_root, run_id, run, store.path) if artifact_root is not None else {'status': 'unavailable'}
    attempts_by_index = {a['index']: {**a, 'status_source': 'persisted_attempt'}
                         for a in projection['autonomous_work']['attempts']}
    for dispatched in store.executor_attempts(run_id):
        attempts_by_index.setdefault(dispatched['index'], dispatched)
    attempts = [attempts_by_index[index] for index in sorted(attempts_by_index)]
    saved_camera = (saved_execution_camera(artifact_root, projection["request_detail"]["traces"],
                    attempts[-1] if attempts else None)
                    if run["suite"] == "robocasa365" and public_station is None else None)
    preview_url = (f"/api/station/preview-frame?run={quote(run_id, safe='')}&attempt={quote(public_station['attempt_id'], safe='')}"
                   if public_station and (run['suite'] in {'robosuite', 'robocasa365'} or
                       public_station.get('camera_transport') in {'openarm_http', 'robocasa_human_http'}) else None)
    from .task_dag import dag_projection
    attempt_dags = dag_projection(store, run_id, attempts, traces, evaluation.get('native_outcomes', {}))
    from .ui_observability import response_evidence
    adoption = response_evidence(requests, responses, uses, traces, projection['autonomous_work']['attempts'], evaluation.get('native_outcomes', {}))
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
            "robot": (run.get("robot_configuration") or {}).get("robot"),
            "robot_configuration": run.get("robot_configuration"),
            **run_identity(store,run_id),
        },
        'attention_control': AttentionControl(store).snapshot(run_id),
        "resources": resources,
        "attempts": [
            {
                key: attempt.get(key)
                for key in ("attempt_id", "index", "started_at", "ended_at", "status", "status_source")
            }
            for attempt in attempts
        ],
        "task_plan": store.task_plan(run_id),
        "attempt_dags": attempt_dags,
        "requests": requests,
        "traces": traces,
        "responses": responses,
        "response_uses": uses,
        "response_evidence": adoption,
        "waiting_windows": store.waiting_windows(run_id),
        "request_lifecycle": lifecycle,
        "request_timeline": request_timeline(store, run_id),
        "memory": memory_snapshot(store, run_id, projection, memory_view),
        "lock": run_lock(store, run, run_id),
        "autonomous_work": autonomous_work(attempts, requests, store.list_work_items(run_id)),
        "request_dispositions": {request["request_id"]: store.request_disposition(request["request_id"])
                                 for request in requests if request["state"] == "answered"},
        "waiting_activity": store.waiting_activities(run_id),
        "scheduler_options": store.scheduler_options(run_id),
        "budget_events": budget_events,
        "station": {**projection["live_station"],
                    "latest_execution": latest_execution,
                    "camera_attempt_id": public_station.get('attempt_id') if public_station else None,
                    "camera_views": camera_view_options(artifact_root,run_id,public_station),
                    "arm_control": arm_control_options(artifact_root,run_id,public_station),
                    "camera_preview_url": preview_url,
                    "camera_state_url": preview_url.replace('/preview-frame?', '/preview-state?') if preview_url else None,
                    "camera_url": preview_url if public_station and run['suite'] == 'robocasa365' else f"/api/station/frame?run={quote(run_id, safe='')}" if (
                        public_station is not None or
                        (robosuite_url and run["suite"] == "robosuite") or
                        (robocasa_camera_ws and run["suite"] == "robocasa")
                    ) else None,
                    "interrupt": store.interrupt_status(run_id), **(saved_camera or {})},
        "work_items": store.list_work_items(run_id),
        "work_source": "attempts_and_persisted_work_events",
        "media_source": "advisor_artifacts" if artifact_root is not None else None,
        "comparison": comparison_summary(store, run_id),
        "eval": evaluation,
    }


def create_server(
    *, store: AttentionStore | None = None, host: str = "127.0.0.1", port: int = 8769,
    orchestrator_url: str | None = None, artifact_root: Path | None = None,
    robosuite_url: str | None = None, camera_name: str | None = None,
    robocasa_camera_ws: str | None = None, robocasa_camera_device: str = "maniskill_base",
    run_catalog: Path | None = None, dev_v2_bridge=None,
    dev_v2_root: Path | None = None, dev_v2_authorization: Path | None = None,
    dev_v2_per_run_budget: bool = False, dev_v2_memory: dict | None = None,
    memory_view: MemoryView | None = None,
    vault=None, allow_custom: bool | None = None, allow_private_hosts: tuple = (), lobby=None,
    gateway_secret=None, gateway_clock=time.time, sim_lock: Path | None = None, run_slots: int | None = None,
) -> ThreadingHTTPServer:
    # These adapters own different ledgers. Never silently replace an explicitly
    # injected shared-goal bridge with an independent daily experiment budget.
    if dev_v2_bridge is not None and dev_v2_root is not None:
        raise ValueError('choose an existing goal bridge or --dev-v2-root')
    if dev_v2_root is None and (dev_v2_authorization is not None or dev_v2_per_run_budget or dev_v2_memory is not None):
        raise ValueError('Dev v2 authorization/budget options require --dev-v2-root')
    # Remote time-share is for the public gateway's harness only; a local console keeps one run at a time.
    if (sim_lock is None) != (run_slots is None) or (sim_lock is not None and (gateway_secret is None or dev_v2_root is None)):
        raise ValueError('--sim-lock and --run-slots go together, on a --public-gateway-mode instance with --dev-v2-root')
    if store is not None:
        recover_lost_runs(store)
        # Live snapshots must not block executor evidence or stop acknowledgements.
        # This explicit opt-in leaves immutable memory SQLite snapshots untouched.
        store.enable_concurrent_writes()
        # Only the mutable UI store receives this index. Frozen snapshots keep
        # their schema untouched; event payloads/history are never rewritten.
        with store._connect() as db:
            db.execute('CREATE INDEX IF NOT EXISTS ui_event_entity_sequence ON events(entity_type,entity_id,sequence)')
            db.execute('CREATE INDEX IF NOT EXISTS ui_event_id_sequence ON events(entity_id,sequence)')
            db.execute("CREATE INDEX IF NOT EXISTS ui_attempt_run ON attempts(json_extract(payload,'$.run_id'))")
            db.execute("CREATE INDEX IF NOT EXISTS ui_trace_attempt ON traces(json_extract(payload,'$.attempt_id'))")
            db.execute("CREATE INDEX IF NOT EXISTS ui_request_run ON requests(json_extract(payload,'$.run_id'))")
    history = UIHistory(store) if store is not None else None
    fixed_ui = FixedUI(artifact_root) if artifact_root is not None else None
    # Behind the public gateway every request must carry its signature, and the owner in it decides whose keys are used.
    public_gateway = gateway_secret is not None
    control_token = control_token_for(gateway_secret) if public_gateway else secrets.token_urlsafe(32)
    if public_gateway:os.environ.pop('PARCC_URL', None)  # A preset URL cannot be redirected from this process's environment.
    # Session provider keys (BYOK): memory only, owner-scoped, served by provider_http.
    from . import provider_http
    from .dev_agent_key_vault import ProviderKeyVault
    from .dev_agent_run_launch import RunCredentials,daily_error
    from .dev_agent_provider import ProviderError
    vault = vault if vault is not None else ProviderKeyVault()
    if allow_custom is None:
        allow_custom = os.environ.get('ATTENTION_ALLOW_CUSTOM_PROVIDER_URL', '').lower() in ('1', 'true', 'yes')
    # A public host can offer custom endpoints, but never one on its own network: they stay HTTPS on public addresses.
    if public_gateway:allow_private_hosts = ()
    quota_cache = {'until': 0.0, 'value': {'available': False}}
    quota_lock = threading.Lock()
    quota_first = threading.Lock()

    def refresh_codex_quota():
        try:
            raw = read_quota()
            used = raw.get('used_percent')
            window = raw.get('window_minutes')
            reset = raw.get('resets_at')
            plan = raw.get('plan_type')
            value = ({'used_percent': used,
                      'window_minutes': window if type(window) is int and window > 0 else None,
                      'resets_at': reset if type(reset) in (int, float) and math.isfinite(reset) else None,
                      'plan_type': plan if isinstance(plan, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,31}', plan) else None,
                      'read_at': datetime.now(timezone.utc).isoformat()}
                     if isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used) and 0 <= used <= 100
                     else {'available': False})
        except Exception:
            value = {'available': False}
        with quota_lock:
            quota_cache.update(until=time.monotonic() + 10, value=value, loaded=True, refreshing=False)

    def public_codex_quota():
        """Return the last reading at once; an expired reading is refreshed in the background."""
        with quota_first:
            if not quota_cache.get('loaded'):refresh_codex_quota()
        with quota_lock:
            start = time.monotonic() >= quota_cache['until'] and not quota_cache.get('refreshing')
            if start:quota_cache['refreshing'] = True
        if start:threading.Thread(target=refresh_codex_quota, name='codex-quota', daemon=True).start()
        with quota_lock:
            return quota_cache['value']
    human_token = os.environ.get('ATTENTION_HUMAN_APPROVAL_TOKEN','')
    if human_token and len(human_token)<24:raise ValueError('separate human capability must have at least 24 characters')
    profiles = load_catalog(run_catalog) if run_catalog is not None else []

    if dev_v2_root is not None:
        from .daily_dev_v2 import DailyDevV2
        authorization = json.loads(dev_v2_authorization.read_bytes()) if dev_v2_authorization else None
        dev_v2_bridge = DailyDevV2(dev_v2_root, profiles, authorization,
                                  per_run_budget=dev_v2_per_run_budget,memory=dev_v2_memory,sim_lock=sim_lock,run_slots=run_slots)
    if lobby is not None:
        from . import lobby_http
        lobby = lobby_http.prepare(lobby, dev_v2_bridge)
    notices = NoticeReader(dev_v2_bridge, memory_view, public_codex_quota, store=store)
    from . import home_status
    home_probe = home_status.ProviderProbe()  # Idle home page readiness; cached background checks.

    from . import dev_loop
    from .core.dev_store import DevConflict
    if store is not None:
        dev_loop.recover_sessions(store)
        for session in store.list_dev_sessions():
            if session['state']=='ready' and not session['stop_requested'] and time.time()<session['deadline_at']:
                dev_loop.spawn_worker(store,session['session_id'])

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK,
                  extra_headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            extra_headers = dict(extra_headers or {})
            self.send_header("Cache-Control", extra_headers.pop("Cache-Control", "no-store"))
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in extra_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _preview(self, query):
            run_id = query.get('run', [''])[0]
            attempt_id = query.get('attempt', [''])[0]
            run = store.get_run(run_id) if store else None
            if not run or artifact_root is None or run['suite'] not in {'robosuite', 'robocasa', 'robocasa365'}:
                raise FileNotFoundError('run-bound public preview unavailable')
            bound = store.get_attempt(attempt_id)
            station = read_public_station(artifact_root, run_id, run['suite'],
                {attempt_id} if bound else set(), allow_pending=run['status'] == 'running')
            if not station or station['attempt_id'] != attempt_id:
                raise ValueError('preview no longer belongs to this run/attempt')
            if run['suite'] == 'robocasa' and station.get('camera_transport') not in {'openarm_http', 'robocasa_human_http'}:
                raise FileNotFoundError('run-bound public preview unavailable')
            # Attempt completion/producer receipt ends the stream before human
            # waiting. No repeated full observation or event history is read.
            active = run['status'] == 'running' and (bound is None or bound['status'] == 'running')
            waiting = False
            camera=query.get('view',[None])[0]
            if camera is not None and camera not in {'chest','wrist','external','left_wrist','right_wrist'}:
                raise ValueError('unsupported camera view')
            packet = read_preview(artifact_root / run_id.removeprefix('run:'), run_id, attempt_id, camera)
            if packet is None:
                return {'state': 'waiting' if waiting else 'ended' if not active else 'unavailable',
                        'run_id': run_id, 'attempt_id': attempt_id, 'age_seconds': None}, None
            header, frame = packet
            age = max(0., time.time() - header['captured_at'])
            state = ('waiting' if waiting else 'ended' if not active or header.get('ended_at')
                     else 'offline' if header.get('producer_error') else 'stale' if age > 2 else 'live')
            return {**header, 'state': state, 'age_seconds': round(age, 3)}, frame

        def _fixed_get(self, path):
            if path in {'/ui/fixed', '/ui/fixed/'}:
                self._json({'error':'Independent fixed scene entry retired; use the main task UI',
                            'main_ui':'/ui','history':'/api/fixed'},HTTPStatus.GONE);return True
            if path=='/ui/fixed.js':
                self._json({'error':'Independent fixed scene entry retired'},HTTPStatus.GONE);return True
            if not path.startswith('/api/fixed'):return False
            if fixed_ui is None:
                self._json({'error':'Fixed scene requires configured artifact root'},HTTPStatus.SERVICE_UNAVAILABLE);return True
            try:
                if path=='/api/fixed':self._json(fixed_ui.snapshot());return True
                match=re.fullmatch(r'/api/fixed/runs/(fixed-[0-9a-f]{24})/(frame|video|evidence|files/.*)',path)
                if not match:raise FileNotFoundError('Unknown fixed resource')
                run_id,kind=match.groups();directory=fixed_ui.directory(run_id)
                if kind=='evidence':
                    self._json({'run':fixed_ui.read(run_id),'files':[{'path':str(p.relative_to(directory)),
                        'url':f'/api/fixed/runs/{run_id}/files/'+quote(str(p.relative_to(directory))) } for p in sorted(directory.rglob('*')) if p.is_file() and not p.name.endswith('.tmp')]});return True
                relative='preview.jpg' if kind=='frame' else 'physics/simulation.mp4' if kind=='video' else unquote(kind[6:])
                file=(directory/relative).resolve()
                if not file.is_relative_to(directory.resolve()) or not file.is_file():raise FileNotFoundError('Fixed artifact unavailable')
                size=file.stat().st_size;start,end=0,size-1;status=HTTPStatus.OK
                if kind=='video' and self.headers.get('Range'):
                    match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
                    if not match or int(match[1])>=size:
                        self._send(b'','video/mp4',HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,{'Content-Range':f'bytes */{size}'});return True
                    start=int(match[1]);end=min(int(match[2]) if match[2] else size-1,size-1)
                    if end<start:raise ValueError('Invalid media range')
                    status=HTTPStatus.PARTIAL_CONTENT
                content='image/jpeg' if kind=='frame' else 'video/mp4' if file.suffix=='.mp4' else 'application/json' if file.suffix=='.json' else 'text/plain; charset=utf-8'
                self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Accept-Ranges','bytes')
                if status==HTTPStatus.PARTIAL_CONTENT:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                with file.open('rb') as f:
                    f.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=f.read(min(262144,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
            except FileNotFoundError as error:self._json({'error':str(error)},HTTPStatus.NOT_FOUND)
            except (ValueError,KeyError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
            except (BrokenPipeError,ConnectionResetError):pass
            return True

        def _dev_get(self,path):
            if not path.startswith('/api/dev/'):return False
            try:
                if store is None:raise ValueError('Dev store unavailable')
                match=re.fullmatch(r'/api/dev/sessions/(dev-[0-9a-f]{24})(?:/files/(.+))?',path)
                if not match:raise FileNotFoundError('Unknown Dev resource')
                session=store.get_dev_session(match[1])
                if not match[2]:self._json(history.project('dev',session));return True
                directory=Path(session['artifact_dir']).resolve()
                file=(directory/unquote(match[2])).resolve()
                if not file.is_relative_to(directory) or any(x.startswith('.') for x in file.relative_to(directory).parts) or not file.is_file():raise FileNotFoundError('Dev artifact unavailable')
                allowed={'session-lock.json','config.json','model-input.json','model-output.json','provider-response.json','public-feedback.json','bundle.json','version.json','policy.py','diff.patch','trace.json','reset_receipt.json','identity.json','native_result.json','sandbox_receipt.json','result.json','recording.json','simulation.mp4','camera_initial.png','camera_final.png'}
                if file.name not in allowed and file.suffix!='.mp4':raise FileNotFoundError('Artifact is not public UI evidence')
                size=file.stat().st_size;start,end,status=0,size-1,HTTPStatus.OK
                if self.headers.get('Range'):
                    rng=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
                    if not rng or int(rng[1])>=size:
                        self._send(b'', 'application/octet-stream',HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,{'Content-Range':f'bytes */{size}'});return True
                    start=int(rng[1]);end=min(int(rng[2]) if rng[2] else end,end)
                    if end<start:raise ValueError('Invalid range')
                    status=HTTPStatus.PARTIAL_CONTENT
                content='video/mp4' if file.suffix=='.mp4' else 'image/png' if file.suffix=='.png' else 'application/json' if file.suffix=='.json' else 'text/plain; charset=utf-8'
                self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(max(0,end-start+1)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Accept-Ranges','bytes')
                if status==HTTPStatus.PARTIAL_CONTENT:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                with file.open('rb') as f:
                    f.seek(start);remaining=end-start+1
                    while remaining>0:
                        chunk=f.read(min(262144,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
            except (DevConflict,FileNotFoundError) as error:self._json({'error':str(error)},HTTPStatus.NOT_FOUND)
            except (ValueError,KeyError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
            except (BrokenPipeError,ConnectionResetError):pass
            return True

        def _v2_get(self):
            if not urlsplit(self.path).path.startswith('/api/dev-v2/'):return False
            try:
                if dev_v2_bridge is None:raise FileNotFoundError('isolated Dev v2 bridge not configured')
                if urlsplit(self.path).path == '/api/dev-v2/memory-monitor':
                    from .memory_monitor import project, CatalogueUnavailable, EXPERIMENT, _log_failure
                    experiment_id = parse_qs(urlsplit(self.path).query).get('exp', [''])[0]
                    if not EXPERIMENT.fullmatch(experiment_id):
                        self._json({'error': 'invalid experiment id'}, HTTPStatus.BAD_REQUEST)
                        return True
                    try:
                        self._json(project(dev_v2_bridge, memory_view, experiment_id))
                    except CatalogueUnavailable as error:
                        _log_failure(error.__cause__ or error, 'online catalogue')
                        self._json({'error': 'Memory monitor unavailable'}, HTTPStatus.BAD_GATEWAY)
                    except FileNotFoundError:
                        self._json({'error': 'run or Memory service unavailable'}, HTTPStatus.NOT_FOUND)
                    except Exception as error:
                        _log_failure(error, 'route')
                        self._json({'error': 'Memory monitor unavailable'}, HTTPStatus.BAD_GATEWAY)
                    return True
                result=dev_v2_bridge.get(self.path)
                value,content=result[:2];status=result[2] if len(result)>2 else 200;headers=result[3] if len(result)>3 else {}
                if isinstance(value,Path):
                    # Reuse the existing range-aware evidence transport below.
                    size=value.stat().st_size;start=0;end=size-1
                    if self.headers.get('Range'):
                        match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
                        if not match or int(match[1])>=size:
                            self._send(b'',content,416,{'Content-Range':f'bytes */{size}'});return True
                        start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
                        if end<start:raise ValueError('invalid range')
                        status=206;headers['Content-Range']=f'bytes {start}-{end}/{size}'
                    headers['Accept-Ranges']='bytes'
                    with value.open('rb') as f:f.seek(start);raw=f.read(end-start+1)
                    self._send(raw,content,status,headers)
                elif content.startswith('application/json'):self._send(json.dumps(value,ensure_ascii=False).encode(),content,status,headers)
                else:self._send(value.encode() if isinstance(value,str) else value,content,status,headers)
            except FileNotFoundError as error:self._json({'error':str(error)},404)
            except (ValueError,KeyError,TypeError,DevConflict) as error:self._json({'error':str(error)},409)
            return True

        def reply(self, value: Any, status: int = 200) -> None:
            self._json(value, HTTPStatus(status))
        def read_body(self, limit: int) -> bytes:
            size = int(self.headers.get('Content-Length', '0') or 0)
            if not 0 <= size <= limit:raise OwnerError('gateway_body_too_large', 'Request body too large', 413)
            raw = self.rfile.read(size)
            self.rfile = io.BytesIO(raw)  # The route that follows reads the same bytes.
            return raw
        def _provider(self) -> None:
            provider_http.handle(self, vault=vault, token=control_token, allow_custom=allow_custom, allow_private_hosts=allow_private_hosts, owner_of=owner_of)
        def _lobby(self) -> bool:
            return lobby is not None and lobby_http.handle(self, lobby,
                authorized=secrets.compare_digest(self.headers.get('X-Attention-Token',''),control_token),
                viewer=lobby_http.viewer_of(self,lobby))
        def _other_method(self) -> None:
            if self._lobby():return
            self.send_error(HTTPStatus.NOT_IMPLEMENTED, f"Unsupported method ({self.command!r})")
        do_HEAD = do_OPTIONS = do_PATCH = _other_method
        def do_PUT(self) -> None:
            if self._lobby():return
            self._provider()
        def do_DELETE(self) -> None:
            if self._lobby():return
            self._provider()
        def do_GET(self) -> None:
            if self._lobby():return
            if provider_http.is_provider_path(urlsplit(self.path).path):
                self._provider()
                return
            if urlsplit(self.path).path == '/api/dev-v2/automatic-checks':
                from .memory_monitor import automatic_checks
                self._json(automatic_checks(gateway=memory_view.gateway if memory_view is not None else None))
                return
            if urlsplit(self.path).path == '/api/notices':
                params = parse_qs(urlsplit(self.path).query)
                exp = params.get('exp', [None])[0]
                run = params.get('run', [None])[0]
                try:self._json(notices.read(exp, run))
                except Exception:
                    self._json({'generated_at': datetime.now(timezone.utc).isoformat(), 'notices': [
                        NoticeReader._notice('notices-unavailable', 'error', 'system', 'Some status details are unavailable',
                                             'The page could not read current status.', 'Try refreshing the page.', time.time())]})
                return
            if self._v2_get():return
            if self._dev_get(urlsplit(self.path).path):return
            if self._fixed_get(urlsplit(self.path).path):return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in {"/", "/ui"}:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/ui/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path == "/ui/remote" and lobby is not None and store is not None:
                page = (UI_DIR / "index.html").read_text(encoding="utf-8").replace('data-mode="demo"', 'data-mode="live"', 1)
                page = page.replace('data-mode="live"', f'data-mode="live" data-control-token="{control_token}" data-remote="1" data-remote-tab="1"'
                                    , 1)
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/ui/":
                page = (UI_DIR / "index.html").read_text(encoding="utf-8")
                if store is not None:
                    page = page.replace('data-mode="demo"', 'data-mode="live"', 1)
                    page = page.replace('data-mode="live"',
                                        f'data-mode="live" data-control-token="{control_token}"' + (' data-remote-tab="1"' if lobby is not None else ''), 1)
                self._send(page.encode("utf-8"), "text/html; charset=utf-8")
            elif path in {"/ui/live.js", "/ui/notices.js", "/ui/shell.js", "/ui/human_reply.js", "/ui/lobby.js"}:
                self._send((UI_DIR / path.rsplit('/', 1)[-1]).read_bytes(), "text/javascript; charset=utf-8")
            elif path == "/ui/shell.css":
                self._send((UI_DIR / "shell.css").read_bytes(), "text/css; charset=utf-8")
            elif path == "/ui/free_view.html":
                # Look-only 3D replay page; it only ever sends GETs for one attempt's three files.
                self._send((UI_DIR / "free_view.html").read_bytes(), "text/html; charset=utf-8", extra_headers={"Content-Security-Policy": FREE_VIEW_CSP})
            elif TASK_PICTURE.fullmatch(path) and (UI_DIR / path[4:]).is_file():
                # Task picker pictures + manifest (scripts/attentionbench/task_picker_pictures.py).
                # Pictures may be cached briefly so switching tasks does not re-download them (11-10 flicker fix);
                # the manifest stays no-store so regenerated text shows at once.
                self._send((UI_DIR / path[4:]).read_bytes(), "image/jpeg" if path.endswith(".jpg") else "application/json; charset=utf-8",
                           extra_headers={"Cache-Control": TASK_PICTURE_CACHE} if path.endswith(".jpg") else None)
            elif path == "/api/codex-quota":
                self._json(public_codex_quota())
            elif path in {"/ui/home.html", "/ui/home.js", "/ui/home.css"}:
                kind = "text/html; charset=utf-8" if path.endswith('.html') else "text/javascript; charset=utf-8" if path.endswith('.js') else "text/css; charset=utf-8"
                body = (UI_DIR / path.rsplit('/', 1)[-1]).read_bytes()
                if path.endswith('.html'):
                    body = body.replace(b'__CONTROL_TOKEN__', control_token.encode())
                    if lobby is not None:
                        body = body.replace(b'<body', b'<body data-remote-tab="1"', 1)
                self._send(body, kind)
            elif path == "/api/home/status":
                if not hasattr(dev_v2_bridge, 'root'):
                    self._json({"error": "Home needs the daily Dev v2 bridge (--dev-v2-root)"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                if parse_qs(parsed.query).get('refresh') == ['1']:
                    home_probe.snapshot(force=True)
                self._json(home_status.status(dev_v2_bridge, home_probe, public_codex_quota()))
            elif path == "/api/home/scene":
                rows = sorted(dev_v2_bridge.list(), key=lambda r: r['created_at'], reverse=True) if hasattr(dev_v2_bridge, 'root') else []
                query = parse_qs(parsed.query)
                wanted, view = query.get('run', [''])[0], query.get('view', ['overview'])[0]
                frame = home_status.scene_for(rows, dev_v2_bridge.root, wanted, view) if wanted else home_status.latest_scene(rows, dev_v2_bridge.root)
                if frame is None:
                    self._json({"error": "No recorded scene yet"}, HTTPStatus.NOT_FOUND)
                    return
                self._send(frame.read_bytes(), "image/png" if frame.suffix == '.png' else "image/jpeg")
            elif path in {"/ui/memory.html", "/ui/memory.js", "/ui/memory.css"}:
                kind = "text/html; charset=utf-8" if path.endswith('.html') else "text/javascript; charset=utf-8" if path.endswith('.js') else "text/css; charset=utf-8"
                body = (UI_DIR / path.rsplit('/', 1)[-1]).read_bytes()
                if path.endswith('.html'):
                    body = body.replace(b'__CONTROL_TOKEN__', control_token.encode())
                    if lobby is not None:
                        body = body.replace(b'<body', b'<body data-remote-tab="1"', 1)
                self._send(body, kind)
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
            elif path == "/api/runs" and store is None and lobby is not None:
                self._json(dict(runs=[], launches=[], dev_sessions=lobby.sessions(lobby_http.viewer_of(self,lobby))))
            elif path == "/api/runs" and store is not None:
                dev_loop.recover_sessions(store)
                listing = {"runs": history.project_many('run',list_runs(store)), "launches": store.list_launches(), "dev_sessions": history.project_many('dev',store.list_dev_sessions()) + (dev_v2_bridge.list() if dev_v2_bridge else [])}
                if lobby is not None:listing['dev_sessions'] += lobby.sessions(lobby_http.viewer_of(self,lobby))
                self._json(listing)
            elif path == "/api/run-options" and store is not None:
                options=public_options(profiles)
                options['dev_loop']=dev_loop.dev_options(profiles)
                if dev_v2_bridge:
                    options['dev_v2']=dev_v2_bridge.options()
                    options['dev_loop']['profiles']=options['dev_v2']['profiles']+options['dev_loop'].get('profiles',[])
                    options['dev_loop']['enabled']=True
                self._json(options)
            elif path == "/api/station/preview-state" and store is not None:
                try:
                    state, _ = self._preview(parse_qs(parsed.query))
                    self._json(state)
                except FileNotFoundError as error:
                    self._json({'error': str(error)}, HTTPStatus.NOT_FOUND)
                except ValueError as error:
                    self._json({'error': str(error)}, HTTPStatus.CONFLICT)
            elif path == "/api/station/preview-frame" and store is not None:
                try:
                    state, frame = self._preview(parse_qs(parsed.query))
                    if frame is None:
                        raise FileNotFoundError('no saved public preview frame')
                    query = parse_qs(parsed.query)
                    unchanged = query.get('after', [None])[0] == str(state['sequence'])
                    self._send(b'' if unchanged else frame, 'image/jpeg',
                        HTTPStatus.NO_CONTENT if unchanged else HTTPStatus.OK, extra_headers={
                            'X-Frame-Sequence': str(state['sequence']),
                            'X-Frame-Age': str(state['age_seconds']),
                            'X-Captured-At': str(state['captured_at']),
                            'X-Preview-State': state['state']})
                except FileNotFoundError as error:
                    self._json({'error': str(error)}, HTTPStatus.NOT_FOUND)
                except ValueError as error:
                    self._json({'error': str(error)}, HTTPStatus.CONFLICT)
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
                                elif public_station.get("camera_transport") == "robocasa_human_http":
                                    frame = rgb_jpeg(robocasa_human_rgb(public_station["origin"])[0])
                                    mime = "image/jpeg"
                                elif public_station.get("camera_transport") == "openarm_http":
                                    recorded = (run.get("robot_configuration") or {}).get("robot")
                                    if recorded != public_station["robot"]:
                                        raise ValueError("OpenArm camera identity differs from recorded run")
                                    frame = openarm_camera_frame(public_station)
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
                    evidence = next(e for e in trace['evidence'] if e['evidence_id'] == evidence_id)
                    if evidence.get('kind') == 'bimanual_control' and mime_type == 'application/json':
                        if media_path.stat().st_size > 8_388_608: raise ValueError('Control replay too large')
                        from openarm_bimanual.replay import validate_control_recording
                        payload = media_path.read_bytes()
                        validate_control_recording(json.loads(payload),run_id=trace['run_id'],attempt_id=trace['attempt_id'])
                        served_type = 'application/json'
                    else:
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
            elif path == "/api/dev-memories" and memory_view is not None:
                try:
                    self._json({"control": memory_view.dev_control(),
                                "memories": memory_view.dev_catalogue()})
                except Exception as error:
                    self._json({"error": f"Dev v2 Memory unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
            elif path.startswith("/api/dev-memories/") and memory_view is not None:
                memory_id = unquote(path.removeprefix("/api/dev-memories/"))
                if not memory_id or "/" in memory_id or "\\" in memory_id:
                    self._json({"error": "invalid memory id"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    self._json(memory_view.dev_public(memory_id))
                except FileNotFoundError as error:
                    self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            elif path == "/api/memories" and memory_view is not None:
                try:
                    self._json({"control": memory_view.public(),
                                "memories": memory_view.catalogue(
                                    parse_qs(parsed.query).get("status", [None])[0])})
                except Exception as error:
                    self._json({"error": f"Memory unavailable: {error}"}, HTTPStatus.BAD_GATEWAY)
            elif path.startswith("/api/memories/") and memory_view is not None:
                memory_id = unquote(path.removeprefix("/api/memories/"))
                if not memory_id or "/" in memory_id or "\\" in memory_id:
                    self._json({"error": "invalid memory id"}, HTTPStatus.BAD_REQUEST)
                    return
                detail = memory_view.detail(memory_id)
                self._json(detail, HTTPStatus.NOT_FOUND if "error" in detail else HTTPStatus.OK)
            elif path.startswith("/api/runs/") and store is not None:
                run_id = unquote(path.removeprefix("/api/runs/"))
                if not run_id or "/" in run_id or "\\" in run_id:
                    self._json({"error": "invalid run id"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    snapshot=dashboard_snapshot(store, run_id, artifact_root=artifact_root,
                                                robosuite_url=robosuite_url,
                                                robocasa_camera_ws=robocasa_camera_ws,
                                                memory_view=memory_view)
                    snapshot['run']=history.project('run',snapshot['run'])
                    self._json(snapshot)
                except StateConflictError:
                    self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self._lobby():return
            if provider_http.is_provider_path(urlsplit(self.path).path):
                self._provider()
                return
            # Includes run-note-off: the same token and bounded JSON as run/cancel/switch/pause.
            if urlsplit(self.path).path.startswith('/api/dev-v2/'):
                if not secrets.compare_digest(self.headers.get('X-Attention-Token',''),control_token):
                    self._json({'error':'control token required'},403);return
                try:
                    if dev_v2_bridge is None:raise FileNotFoundError('isolated bridge not configured')
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<size<=32768:raise ValueError('bounded JSON object required')
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict):raise ValueError('JSON object required')
                    if urlsplit(self.path).path=='/api/dev-v2/experiments':
                        # The browser sends credential IDs, never custom URLs.
                        selection=body.get('selection')
                        if isinstance(selection,dict) and isinstance(selection.get('agent_provider'),dict):
                            roles=selection['agent_provider'].get('roles',{})
                            if isinstance(roles,dict) and any(isinstance(d,dict) and d.get('provider')=='openai-compatible' for d in roles.values()):
                                known={r['credential_id']:r for r in vault.list(owner_of(self))}
                                for desc in roles.values():
                                    if isinstance(desc,dict) and desc.get('provider')=='openai-compatible' and desc.get('credential') in known:
                                        desc.setdefault('base_url_host',known[desc['credential']]['base_url_host'])
                    human=bool(human_token and secrets.compare_digest(self.headers.get('X-Attention-Human-Token',''),human_token))
                    self._json(dev_v2_bridge.post(urlsplit(self.path).path,body,human_authorized=human,
                        keys=RunCredentials(vault,owner_of(self),allow_custom,allow_private_hosts)))
                except PermissionError as error:self._json({'error':str(error)},403)
                except FileNotFoundError as error:self._json({'error':str(error)},404)
                except ProviderError as error:self._json(daily_error(error),409)
                except (ValueError,KeyError,TypeError,DevConflict) as error:self._json({'error':str(error)},409)
                return
            path = urlsplit(self.path).path
            if path in {'/api/history/delete','/api/history/restore'}:
                if not secrets.compare_digest(self.headers.get('X-Attention-Token',''),control_token):
                    self._json({'error':'control token required'},HTTPStatus.FORBIDDEN);return
                try:
                    if history is None:raise ValueError('History unavailable')
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<size<=4096:
                        raise ValueError('bounded JSON body required')
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict) or set(body)!={'kind','record_id'}:raise ValueError('record kind and ID required')
                    self._json(history.change(body['kind'],body['record_id'],deleted=path.endswith('/delete')))
                except StateConflictError as error:self._json({'error':str(error)},HTTPStatus.CONFLICT)
                except (ValueError,KeyError,TypeError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
                return
            if path.startswith('/api/fixed'):
                if not secrets.compare_digest(self.headers.get('X-Attention-Token',''),control_token):
                    self._json({'error':'control token required'},HTTPStatus.FORBIDDEN);return
                if path=='/api/fixed/start':
                    self._json({'error':'Independent fixed scene launch retired; use the main task chain'},HTTPStatus.GONE);return
                try:
                    if fixed_ui is None:raise ValueError('Fixed scene artifact root is not configured')
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<size<=4096:raise ValueError('JSON body1-4096bytes required')
                    body=json.loads(self.rfile.read(size))
                    match=re.fullmatch(r'/api/fixed/runs/(fixed-[0-9a-f]{24})/command',path)
                    if not match:raise ValueError('Unknown fixed command')
                    if body.get('action') not in {'stop','cancel'}:
                        self._json({'error':'Independent fixed scene execution retired'},HTTPStatus.GONE);return
                    result=fixed_ui.command(match[1],body)
                    self._json(result,HTTPStatus.ACCEPTED)
                except RuntimeError as error:self._json({'error':str(error)},HTTPStatus.CONFLICT)
                except (ValueError,KeyError,TypeError,FileNotFoundError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
                except Exception as error:self._json({'error':f'Fixed launch failed: {error}'},HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if path.startswith('/api/dev/'):
                if not secrets.compare_digest(self.headers.get('X-Attention-Token',''),control_token):
                    self._json({'error':'control token required'},HTTPStatus.FORBIDDEN);return
                try:
                    if store is None or artifact_root is None:raise ValueError('Dev store/artifacts unavailable')
                    size=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<size<=4096:raise ValueError('bounded JSON body required')
                    body=json.loads(self.rfile.read(size))
                    attention_match=re.fullmatch(r'/api/dev/sessions/(dev-[0-9a-f]{24})/(attention-policy(?:/cancel)?|attention-guidance)',path)
                    if attention_match:
                        sid,command=attention_match.groups()
                        if history.project('dev',store.get_dev_session(sid)).get('history_deleted_at'):
                            raise DevConflict('Restore this Dev record first')
                        control=DevAttentionControl(store)
                        required={'policy_id','request_id','expected_generation'} if command=='attention-policy' else {'request_id'} if command.endswith('/cancel') else {'request_id','content','skip'}
                        if not isinstance(body,dict) or set(body)!=required:raise ValueError('invalid Dev Attention fields')
                        if command=='attention-policy':result=control.request(sid,**body)
                        elif command.endswith('/cancel'):result=control.cancel(sid,body['request_id'])
                        else:
                            if type(body['skip']) is not bool:raise ValueError('skip must be boolean')
                            result=control.guidance(sid,**body)
                        self._json(result);return
                    if path=='/api/dev/start':
                        if not isinstance(body,dict):raise ValueError('Dev selection must be an object')
                        suffix=name_suffix(body.pop('name_suffix',''))
                        session=dev_loop.create_session(store,profiles,artifact_root,body)
                        history.assign_suffix('dev',session['session_id'],suffix)
                        if history.project('dev',session).get('history_deleted_at'):
                            raise DevConflict('Restore the deleted record before starting it again')
                        if session['state']=='ready':dev_loop.spawn_worker(store,session['session_id'])
                        session=history.project('dev',session)
                    else:
                        match=re.fullmatch(r'/api/dev/sessions/(dev-[0-9a-f]{24})/(stop|resume)',path)
                        if not match or body:raise ValueError('unknown Dev command or extra fields')
                        if match[2]=='stop':session=store.stop_dev_session(match[1])
                        else:
                            if history.project('dev',store.get_dev_session(match[1])).get('history_deleted_at'):
                                raise DevConflict('Restore the deleted record before resuming it')
                            dev_loop.recover_sessions(store);store.resume_dev_session(match[1]);session=dev_loop.spawn_worker(store,match[1])
                    self._json(session,HTTPStatus.ACCEPTED)
                except (DevConflict,StateConflictError) as error:self._json({'error':str(error)},HTTPStatus.CONFLICT)
                except (ValueError,TypeError,KeyError,FileNotFoundError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
                return
            is_response = path.startswith("/api/requests/") and path.endswith("/respond")
            is_preview = path.startswith("/api/requests/") and path.endswith("/guidance-preview")
            is_defer = path.startswith("/api/requests/") and path.endswith("/defer")
            is_cancel = path.startswith("/api/requests/") and path.endswith("/cancel")
            is_ignore = path.startswith("/api/requests/") and path.endswith("/ignore")
            dev_memory_action = path.startswith("/api/dev-memories/")
            memory_action = next((a for a in MEMORY_ACTIONS if path.startswith(
                                  "/api/dev-memories/" if dev_memory_action else "/api/memories/")
                                  and path.endswith("/" + a)), None)
            is_interrupt = path.startswith("/api/runs/") and path.endswith("/interrupt")
            is_dag_start = path == "/api/orchestrator/dispatch"
            is_run_start = path == "/api/runs/start"
            control_suffix = next((s for s in ('/attention-policy','/attention-policy/cancel','/display-name') if path.startswith('/api/runs/') and path.endswith(s)),None)
            if store is None or not (is_response or is_preview or is_defer or is_cancel or is_ignore or is_interrupt or is_dag_start or is_run_start or control_suffix or memory_action):
                self._json({"error": "unsupported write"}, HTTPStatus.METHOD_NOT_ALLOWED)
                return
            if not secrets.compare_digest(self.headers.get("X-Attention-Token", ""), control_token):
                self._json({"error": "control token required"}, HTTPStatus.FORBIDDEN)
                return
            if control_suffix:
                try:
                    run_id=unquote(path[len('/api/runs/'):-len(control_suffix)]).strip('/')
                    if not run_id or '/' in run_id or '\\' in run_id:raise ValueError('invalid run ID')
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON body required')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096:raise ValueError('invalid control body size')
                    body=json.loads(self.rfile.read(size));control=AttentionControl(store)
                    required={'display_name'} if control_suffix=='/display-name' else {'request_id'} if control_suffix.endswith('/cancel') else {'policy_id','request_id','expected_generation'}
                    if not isinstance(body,dict) or set(body)!=required:raise ValueError('invalid control fields')
                    if control_suffix=='/display-name':result=control.rename(run_id,body['display_name'],now=time.time())
                    elif control_suffix.endswith('/cancel'):result=control.cancel(run_id,body['request_id'],now=time.time())
                    else:result=control.request(run_id,body['policy_id'],request_id=body['request_id'],expected_generation=body['expected_generation'],now=time.time())
                    self._json(result)
                except StateConflictError as error:self._json({'error':str(error)},HTTPStatus.CONFLICT)
                except (ValueError,KeyError,TypeError) as error:self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
                return
            if is_preview:
                # A format check has no lifecycle, budget, response or robot side
                # effects. Infer the task from the stored request, never the caller.
                request_id = unquote(path[len("/api/requests/"):-len("/guidance-preview")]).strip("/")
                try:
                    if not request_id or "/" in request_id or "\\" in request_id:
                        raise ValueError("invalid request id")
                    if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                        raise ValueError("JSON body required")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 8192:
                        raise ValueError("request body must be 1–8192 bytes")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict) or set(body) not in ({"content"},{"content","guidance_scope"}):
                        raise ValueError("only guidance content is accepted")
                    content = body["content"]
                    if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                        raise ValueError("hint content must contain 1–4000 characters")
                    request = store.get_request(request_id)
                    if request is None:
                        self._json({"error": "request not found"}, HTTPStatus.NOT_FOUND)
                        return
                    if request.mode is not AssistanceMode.LIVE_HUMAN_FIRST or request.request_type is not RequestType.HINT:
                        raise StateConflictError("only human hint requests support guidance previews")
                    run = store.get_run(request.run_id)
                    robot=(run.get('robot_configuration') or {}).get('robot')
                    scope=body.get('guidance_scope')
                    if robot and robot.get('execution_arm')=='both':
                        from openarm_bimanual.hint import validate_scope
                        scope=validate_scope(scope,attempt_id=request.attempt_id)
                    elif scope is not None:raise ValueError('This execution mode does not accept hand scope')
                    receipt = prepare_human_guidance(content.strip(), suite=run["suite"], task_id=run["task_id"],
                        robot=robot,guidance_scope=scope)
                    self._json({"run_id": request.run_id, "request_id": request_id,
                                **{key: receipt[key] for key in ("status", "parameters", "rule")}})
                except StateConflictError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
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
                    suffix=name_suffix(selection.pop('name_suffix',''))
                    launch=launch_formal_run(selection, profiles=profiles, store=store,
                                             artifact_root=artifact_root)
                    history.assign_suffix('launch',launch['launch_id'],suffix)
                    self._json(launch)
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
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=512:raise ValueError('bounded interrupt request required')
                    body=json.loads(self.rfile.read(size)) if size else {}
                    if not isinstance(body,dict)or set(body)-{'action'}or body.get('action','stop')not in {'stop','cancel'}:raise ValueError('invalid interrupt action')
                    run = store.get_run(run_id)
                    if run is None:
                        self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                        return
                    if run["execution_target"] not in {"robocasa_sim", "robosuite_sim", "robocasa365_sim"}:
                        raise StateConflictError("only connected simulator runs accept this interrupt")
                    result = store.request_interrupt(run_id, event_key=f"interrupt:{uuid4().hex}",
                                                     requested_at=time.time(),operator_action=body.get('action','stop'))
                    self._json(result)
                except (ValueError,TypeError,json.JSONDecodeError) as error:
                    self._json({'error':str(error)},HTTPStatus.BAD_REQUEST)
                except StateConflictError as error:
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            if memory_action:
                prefix = "/api/dev-memories/" if dev_memory_action else "/api/memories/"
                memory_id = unquote(path[len(prefix):-len("/" + memory_action)]).strip("/")
                if not memory_id or "/" in memory_id or "\\" in memory_id:
                    self._json({"error": "invalid memory id"}, HTTPStatus.BAD_REQUEST)
                    return
                if memory_view is None:
                    self._json({"error": "Memory is not connected to this server"},
                               HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if (self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json"
                            or not 0 < size <= 2048):
                        raise ValueError("bounded JSON body required")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict) or set(body) - {"actor", "reason", "expires_at"}:
                        raise ValueError("body needs actor and reason (and expires_at for expiry)")
                    record = memory_view.operate(memory_id, memory_action, actor=body.get("actor"),
                                                 reason=body.get("reason"),
                                                 expires_at=body.get("expires_at"), dev_v2=dev_memory_action)
                    self._json({"memory_id": memory_id, "action": memory_action, "memory": record})
                except FileNotFoundError as error:
                    self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
                except PermissionError as error:
                    self._json({"error": str(error)}, HTTPStatus.FORBIDDEN)
                except (ValueError, TypeError, json.JSONDecodeError) as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                except TimeoutError as error:
                    self._json({"error": "Memory Service request timed out (180 second limit): "
                                + (str(error) or type(error).__name__)}, HTTPStatus.GATEWAY_TIMEOUT)
                except URLError as error:
                    timed_out = isinstance(error.reason, TimeoutError)
                    self._json({"error": ("Memory Service request timed out (180 second limit): " if timed_out
                                          else "Memory Service request failed: ") + str(error)},
                               HTTPStatus.GATEWAY_TIMEOUT if timed_out else HTTPStatus.BAD_GATEWAY)
                except Exception as error:
                    # The service refuses illegal transitions (for example re-enabling a rollback).
                    conflict = "StateConflict" in type(error).__name__
                    self._json({"error": str(error)}, HTTPStatus.CONFLICT if conflict else HTTPStatus.BAD_GATEWAY)
                return
            suffix = ("/respond" if is_response else "/defer" if is_defer
                      else "/ignore" if is_ignore else "/cancel")
            request_id = unquote(path[len("/api/requests/"):-len(suffix)]).strip("/")
            if not request_id or "/" in request_id or "\\" in request_id:
                self._json({"error": "invalid request id"}, HTTPStatus.BAD_REQUEST)
                return
            if is_defer or is_cancel or is_ignore:
                try:
                    if is_defer:
                        self._json(store.defer_request(request_id, deferred_at=time.time()))
                    elif is_ignore:
                        # The operator declines to answer: the AdvisorProxy fallback takes over
                        # at once instead of waiting for the deadline.
                        updated = AttentionRuntime(store, None, clock=time.time).decline(request_id)
                        self._json({"request_id": request_id, "state": updated.state.value})
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
                    request_id, response_id=f"human-response:{uuid4().hex}", content=content, guidance_scope=body.get("guidance_scope"),
                )
                self._json({"request_id": request_id, "state": updated.state.value,
                            "response_id": updated.response_id})
            except (ValueError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except StateConflictError as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)

    def signed(method):
        def run(self):
            try:self._request_owner = owner_of(self)
            except OwnerError as error:
                self._json({'error': {'code': error.code, 'message': str(error)}}, HTTPStatus(error.status))
                return
            method(self)
        return run
    for name in ('do_GET', 'do_POST', 'do_PUT', 'do_DELETE'):setattr(Handler, name, signed(getattr(Handler, name)))
    server = ThreadingHTTPServer((host, port), Handler)
    server.public_gateway_mode, server.gateway_secret, server.gateway_clock = public_gateway, gateway_secret, gateway_clock
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--store", type=Path, help="existing AttentionStore SQLite path")
    source.add_argument("--demo", action="store_true", help="interactive sample data only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--orchestrator-url", help="read-only Orchestrator HTTP API, e.g. http://127.0.0.1:8766")
    parser.add_argument("--artifact-root", type=Path, help="parent of episode artifact directories")
    parser.add_argument("--lobby-config", type=Path, help="local demo lobby JSON with an explicit state_dir")
    parser.add_argument("--run-catalog", type=Path,
                        help="operator-approved formal simulator profiles for UI launch")
    parser.add_argument("--dev-v2-root", type=Path,
                        help="dedicated daily experiment ledgers and artifacts")
    parser.add_argument("--public-gateway-mode", action="store_true",
                        help="serve only requests signed by the public gateway; needs --gateway-secret-fd")
    parser.add_argument("--gateway-secret-fd", type=int, help="file descriptor holding the gateway secret")
    parser.add_argument("--sim-lock", type=Path,
                        help="public gateway only: this host's simulator lock. Several runs stay open; each execution takes it in turn, and runs have no time limit")
    parser.add_argument("--run-slots", type=int, help="with --sim-lock: how many runs may be open at once")
    parser.add_argument("--dev-v2-authorization", type=Path,
                        help="explicit time-bounded daily run authorization")
    parser.add_argument("--dev-v2-per-run-budget", action="store_true",
                        help="each daily run retains its own finite cumulative budget")
    parser.add_argument("--robosuite-url", help="Robosuite simulator service origin for public RGB camera")
    parser.add_argument("--camera-name", help="camera name, e.g. agentview")
    parser.add_argument("--robocasa-camera-ws", help="ManiSkill camera bridge URL, e.g. ws://127.0.0.1:5580")
    parser.add_argument("--robocasa-camera-device", default="maniskill_base",
                        help="camera bridge device ID: maniskill_base or maniskill_wrist")
    parser.add_argument("--memory-service-url",
                        help="Memory Service origin; keys come from ATTENTION_MEMORY_API_KEY and "
                             "ATTENTION_MEMORY_OPERATOR_KEY (operator key enables lifecycle actions)")
    parser.add_argument("--dev-v2-memory-store-id", default=os.environ.get("ATTENTION_MEMORY_STORE_ID"),
                        help="opt-in daily Memory authority store ID")
    parser.add_argument("--dev-v2-memory-namespace", default=os.environ.get("ATTENTION_MEMORY_NAMESPACE"),
                        help="isolated daily Memory namespace")
    parser.add_argument("--memory-local", action="store_true",
                        help="show the Memory kept in this run store (read-only unless "
                             "--memory-local-operator)")
    parser.add_argument("--memory-local-operator", action="store_true",
                        help="allow disable/enable/rollback/expiry on the local Memory (development)")
    args = parser.parse_args()
    if args.public_gateway_mode != (args.gateway_secret_fd is not None):
        parser.error("--public-gateway-mode requires --gateway-secret-fd and vice versa")
    if args.public_gateway_mode and (args.lobby_config or args.memory_service_url or args.memory_local):
        parser.error("a public gateway instance has no lobby and no Memory")
    if args.dev_v2_root is None and (args.dev_v2_authorization is not None or args.dev_v2_per_run_budget):
        parser.error("Dev v2 authorization/budget options require --dev-v2-root")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("this local operator console binds only to loopback")
    if args.store is not None and not args.store.is_file():
        parser.error(f"AttentionStore does not exist: {args.store}")
    if args.artifact_root is not None and not args.artifact_root.is_dir():
        parser.error(f"artifact root does not exist: {args.artifact_root}")
    if args.run_catalog is not None and (args.store is None or args.artifact_root is None):
        parser.error("--run-catalog requires --store and --artifact-root")
    store = AttentionStore(args.store) if args.store is not None else None
    if store is not None:
        recover_lost_runs(store)
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
    memory_view = None
    if args.memory_service_url and args.memory_local:
        parser.error("choose --memory-service-url or --memory-local, not both")
    if args.memory_service_url:
        api_key = os.environ.get("ATTENTION_MEMORY_API_KEY", "")
        if not api_key:
            parser.error("--memory-service-url requires ATTENTION_MEMORY_API_KEY")
        memory_view = MemoryView.service(
            args.memory_service_url, api_key=api_key,
            operator_key=os.environ.get("ATTENTION_MEMORY_OPERATOR_KEY") or None)
    elif args.memory_local:
        if store is None:
            parser.error("--memory-local requires --store")
        memory_view = MemoryView.local(args.store, operate=args.memory_local_operator)
    daily_memory = None
    if args.dev_v2_memory_store_id or args.dev_v2_memory_namespace:
        if not (args.dev_v2_root and args.memory_service_url and args.dev_v2_memory_store_id and args.dev_v2_memory_namespace and os.environ.get("ATTENTION_MEMORY_API_KEY")):
            parser.error("daily Memory requires --dev-v2-root, --memory-service-url, store ID, namespace and ATTENTION_MEMORY_API_KEY")
        from .dev_planner import configuration as planner_configuration
        daily_memory=dict(base_url=args.memory_service_url,store_id=args.dev_v2_memory_store_id,
                          namespace=args.dev_v2_memory_namespace,
                          url_sha256=hashlib.sha256(args.memory_service_url.encode()).hexdigest())
        planner_configuration({'memory':daily_memory,'human_wait_seconds':0,'background_seconds':0,'approval_rule':'none'})
    server = create_server(store=store, host=args.host, port=args.port, memory_view=memory_view,
                           orchestrator_url=args.orchestrator_url,
                           artifact_root=args.artifact_root, robosuite_url=args.robosuite_url,
                           camera_name=args.camera_name,
                           robocasa_camera_ws=args.robocasa_camera_ws,
                           robocasa_camera_device=args.robocasa_camera_device,
                           run_catalog=args.run_catalog, dev_v2_root=args.dev_v2_root,
                           dev_v2_authorization=args.dev_v2_authorization,
                           dev_v2_per_run_budget=args.dev_v2_per_run_budget,dev_v2_memory=daily_memory,
                           gateway_secret=read_gateway_secret(args.gateway_secret_fd) if args.public_gateway_mode else None,
                           sim_lock=args.sim_lock, run_slots=args.run_slots,
                           lobby=json.loads(args.lobby_config.read_text()) if args.lobby_config else None)
    print(f"AttentionBench UI: http://{args.host}:{server.server_port}/ui/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
