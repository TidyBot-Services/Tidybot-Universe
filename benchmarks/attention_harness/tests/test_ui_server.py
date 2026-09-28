from __future__ import annotations

import json
import base64
import hashlib
import shutil
import struct
import re
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import numpy as np
import pytest

from benchmarks.attention_harness.core.models import AttemptStatus, RunStatus
from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.tests.test_attention_store import populated_store, records
from benchmarks.attention_harness.ui_server import (
    create_server,
    dashboard_snapshot,
    list_runs,
)
from benchmarks.attention_harness.ui_media import (AdvisorCameraRecorder, rgb_png,
                                                    robocasa_camera_frame)
from benchmarks.attention_harness.public_station import (publish_public_station,
                                                         read_public_station, read_public_frame)


def test_defer_and_cancel_survive_ui_restart(tmp_path) -> None:
    store = AttentionStore(tmp_path / "attention.sqlite3")
    run, attempt, trace, request = records()
    store.create_run(replace(run, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.create_attempt(attempt)
    store.put_trace(trace)
    live = replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST,
                   deadline_at=time.time() + 60)
    AttentionRuntime(store, None, clock=time.time).open_request(live)

    def operate(action):
        server = create_server(store=AttentionStore(store.path), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/ui/") as response:
                token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
            with urlopen(Request(base + f"/api/requests/{live.request_id}/{action}",
                                 data=b"", method="POST",
                                 headers={"X-Attention-Token": token})) as response:
                result = json.load(response)
            with urlopen(base + f"/api/runs/{run.run_id}") as response:
                snapshot = json.load(response)
            return result, snapshot
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    deferred, first = operate("defer")
    assert deferred["deadline_at"] == live.deadline_at
    assert first["request_lifecycle"][live.request_id][0]["event_type"] == "request.deferred"
    cancelled, second = operate("cancel")
    assert cancelled["state"] == "cancelled"
    assert second["requests"][0]["state"] == "cancelled"
    assert second["request_lifecycle"][live.request_id][-1]["event_type"] == "request.cancelled"


def test_ui_run_options_reject_heldout_seed_before_launch(tmp_path) -> None:
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"suite": "robosuite", "task_id": "cube_lift", "seed": 101}))
    service = tmp_path / "service"
    service.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"profiles": [{
        "id": "lift", "suite": "robosuite", "task": "cube_lift",
        "execution_target": "robosuite_sim", "code": str(code),
        "approved_policy_sha256": hashlib.sha256(code.read_bytes()).hexdigest(),
        "config": str(config),
        "approved_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "service_source_root": str(service),
    }]}))
    store = AttentionStore(tmp_path / "attention.sqlite3")
    server = create_server(store=store, port=0, artifact_root=tmp_path,
                           run_catalog=catalog)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/ui/") as response:
            token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
        with urlopen(base + "/api/run-options") as response:
            options = json.load(response)
        assert options["profiles"][0]["seed"] == 101
        payload = {"profile_id": "lift", "seed": 1001, "attention_policy": "autonomous",
                   "execution_target": "robosuite_sim", "assistance_mode": "benchmark_proxy",
                   "max_attempts": 2, "assistance_credits": 1, "token_limit": 100,
                   "human_deadline_seconds": 30, "overall_deadline_seconds": 90}
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base + "/api/runs/start", method="POST",
                            data=json.dumps(payload).encode(),
                            headers={"X-Attention-Token": token,
                                     "Content-Type": "application/json"}))
        assert caught.value.code == 400
        assert not store.list_launches()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_snapshot_uses_persisted_requests_without_oracle(tmp_path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    store.complete_attempt(
        request.attempt_id,
        AttemptStatus.FAILED,
        ended_at=5.0,
        native_success=False,
        artifact_uri="internal://oracle/result",
        event_key="attempt-ended",
    )
    snapshot = dashboard_snapshot(store, request.run_id)
    assert snapshot["requests"][0]["request_id"] == request.request_id
    assert snapshot["attempts"][0]["status"] == "failed"
    assert snapshot["work_source"] == "attempts_and_persisted_work_events"
    assert snapshot["resources"]["tokens"]["reserved"] is None
    encoded = json.dumps(snapshot)
    assert "native_success" not in encoded
    assert "internal://oracle" not in encoded
    assert "raw_trace" not in encoded
    assert list_runs(store)[0]["run_id"] == request.run_id


def test_dashboard_comparison_aggregates_only_matched_terminal_runs(tmp_path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    run, attempt, _, _ = records()
    second_run = replace(run, run_id="run-2", policy_id="autonomous")
    store.create_run(second_run)
    store.create_attempt(replace(attempt, attempt_id="attempt-2", run_id="run-2"))
    for run_id, attempt_id, success in (("run-1", "attempt-1", True),
                                        ("run-2", "attempt-2", False)):
        store.transition_run(run_id, RunStatus.RUNNING, event_key=f"start:{run_id}")
        store.complete_attempt(attempt_id, AttemptStatus.SUCCEEDED if success else AttemptStatus.FAILED,
                               ended_at=5.0, native_success=success, artifact_uri="artifact://result",
                               event_key=f"finish:{attempt_id}")
        store.transition_run(run_id, RunStatus.COMPLETED if success else RunStatus.FAILED,
                             event_key=f"finish:{run_id}")
    summary = dashboard_snapshot(store, request.run_id)["comparison"]
    assert summary["comparable"] is True
    assert [(item["policy_id"], item["successes"], item["runs"])
            for item in summary["policies"]] == [("autonomous", 0, 1), ("reactive_help", 1, 1)]


def test_ui_server_serves_live_projection_and_rejects_unrelated_writes(tmp_path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    server = create_server(store=store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/ui/") as response:
            page = response.read().decode()
        assert 'data-mode="live"' in page
        with urlopen(base + "/api/runs") as response:
            runs = json.load(response)["runs"]
        assert [item["run_id"] for item in runs] == [request.run_id]
        with urlopen(base + "/api/runs/" + request.run_id) as response:
            snapshot = json.load(response)
        assert snapshot["requests"][0]["request_id"] == request.request_id
        try:
            urlopen(Request(base + "/api/runs/" + request.run_id, method="POST", data=b"{}"))
        except HTTPError as error:
            assert error.code == 405
        else:
            raise AssertionError("unrelated write was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

def test_unified_ui_reads_dag_and_session_history_without_adding_inbox_requests(tmp_path) -> None:
    class OrchestratorStub(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/ui-snapshot":
                payload = json.dumps({
                    "graph": "test-graph", "run_id": "old-run", "entries": [
                        {"name": "detect", "status": "done", "dependencies": []},
                        {"name": "grasp", "status": "writing", "dependencies": ["detect"]},
                    ], "agents": [{"agent_id": "dev-1", "agent_type": "dev", "skill": "grasp",
                                  "target": "sim-1", "status": "running"}],
                    "live_sessions": [{"session_id": "live-1", "agent_id": "dev-1",
                                       "skill": "grasp", "agent_type": "dev", "target": "sim-1",
                                       "in_progress": True, "log": [{"role": "agent", "text": "working"}]}],
                }).encode()
            elif self.path == "/sessions/test-graph":
                payload = (json.dumps({"session_id": "past-1", "agent_id": "eval-1",
                                       "skill": "detect", "agent_type": "evaluator", "target": "sim-1",
                                       "log": [{"role": "user", "text": "hint"}]}) + "\n").encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), OrchestratorStub)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    store, request = populated_store(tmp_path / "attention.sqlite3")
    server = create_server(store=store, port=0,
                           orchestrator_url=f"http://127.0.0.1:{upstream.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/orchestrator/snapshot") as response:
            dag = json.load(response)
        assert dag["entries"][1]["dependencies"] == ["detect"]
        assert dag["live_sessions"][0]["in_progress"] is True
        with urlopen(base + "/api/orchestrator/sessions?graph=test-graph") as response:
            history = json.load(response)
        assert history["sessions"][0]["log"][0]["role"] == "user"
        with urlopen(base + "/api/runs/" + request.run_id) as response:
            attention = json.load(response)
        assert len(attention["requests"]) == 1
        assert "past-1" not in json.dumps(attention["requests"])
        try:
            urlopen(base + "/api/orchestrator/sessions?graph=..%2Fsecret")
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("graph traversal was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)


def test_ui_starts_only_active_orchestrator_graph_with_control_token(tmp_path) -> None:
    class OrchestratorStub(BaseHTTPRequestHandler):
        started = []

        def do_GET(self) -> None:
            if self.path != "/ui-snapshot":
                self.send_error(404)
                return
            self.reply({"graph": "active-graph", "entries": [
                {"name": "leaf", "status": "planned", "dependencies": []},
            ], "agents": [], "live_sessions": [], "autonomous_mode": False})

        def do_POST(self) -> None:
            if self.path != "/attention/auto-start":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.started.append(body)
            self.reply({"ok": True, "graph": body["graph"], "autonomous_mode": True,
                        "spawned": ["leaf"]})

        def reply(self, value) -> None:
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), OrchestratorStub)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    server = create_server(store=AttentionStore(tmp_path / "attention.sqlite3"), port=0,
                           orchestrator_url=f"http://127.0.0.1:{upstream.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(graph: str, token: str):
        return urlopen(Request(base + "/api/orchestrator/dispatch", method="POST",
                               data=json.dumps({"graph": graph}).encode(), headers={
                                   "Content-Type": "application/json", "X-Attention-Token": token,
                               }))

    try:
        with urlopen(base + "/ui/") as response:
            token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
        with pytest.raises(HTTPError) as unauthorized:
            post("active-graph", "wrong")
        assert unauthorized.value.code == 403
        with pytest.raises(HTTPError) as wrong_graph:
            post("old-graph", token)
        assert wrong_graph.value.code == 409
        assert not OrchestratorStub.started
        with post("active-graph", token) as response:
            result = json.load(response)
        assert result["spawned"] == ["leaf"]
        assert OrchestratorStub.started == [{"graph": "active-graph"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)


def test_live_human_hint_post_is_persisted_but_not_marked_adopted(tmp_path) -> None:
    store = AttentionStore(tmp_path / "attention.sqlite3")
    run, attempt, trace, request = records()
    store.create_run(replace(run, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.create_attempt(attempt)
    store.put_trace(trace)
    live = replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST,
                   deadline_at=time.time() + 60)
    AttentionRuntime(store, None, clock=time.time).open_request(live)
    server = create_server(store=store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/ui/") as response:
            page = response.read().decode()
        token = re.search(r'data-control-token="([^"]+)"', page).group(1)
        body = json.dumps({"content": "look at the wrist camera"}).encode()
        url = base + "/api/requests/" + live.request_id + "/respond"
        def post(value: bytes, auth: str):
            return urlopen(Request(url, method="POST", data=value, headers={
                "Content-Type": "application/json", "X-Attention-Token": auth,
            }))
        try:
            post(body, "wrong")
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("write without control token was accepted")
        with post(body, token) as response:
            result = json.load(response)
        assert result["state"] == "answered"
        snapshot = dashboard_snapshot(store, live.run_id)
        assert snapshot["responses"][result["response_id"]]["content"] == "look at the wrist camera"
        assert snapshot["response_uses"] == {}
        try:
            post(body, token)
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("second human answer was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    restarted = AttentionStore(tmp_path / "attention.sqlite3")
    restored = dashboard_snapshot(restarted, live.run_id)
    assert restored["requests"][0]["state"] == "answered"
    assert restored["responses"][result["response_id"]]["content"] == "look at the wrist camera"


def test_advisor_camera_evidence_and_live_public_frame_are_served(tmp_path) -> None:
    episode = tmp_path / "attention-run" / "attempts" / "episode-1"
    episode.mkdir(parents=True)
    rgb = np.zeros((3, 4, 3), dtype=np.uint8)
    rgb[:, :, 0] = 220
    evidence_path = episode / "initial_observation.npz"
    np.savez_compressed(evidence_path, agentview_image=rgb, oracle_state=np.array([42]))
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    video_path = episode / "advisor_replay.mp4"
    video_path.write_bytes(b"0123456789")
    run, attempt, trace, request = records()
    trace = replace(trace, evidence=({
        "evidence_id": "evidence:initial_observation.npz",
        "kind": "initial_observation", "uri": "artifact://episode-1/initial_observation.npz",
        "sha256": digest, "mime_type": "application/x-npz", "visibility": ["advisor"],
    }, {
        "evidence_id": "evidence:advisor_replay.mp4", "kind": "advisor_replay",
        "uri": "artifact://episode-1/advisor_replay.mp4",
        "sha256": hashlib.sha256(video_path.read_bytes()).hexdigest(),
        "mime_type": "video/mp4", "visibility": ["advisor"],
    }))
    store = AttentionStore(tmp_path / "attention.sqlite3")
    store.create_run(run)
    store.create_attempt(attempt)
    store.put_trace(trace)
    store.create_request(request)

    class Camera(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"observation": {"agentview_image": {
                "dtype": "uint8", "shape": list(rgb.shape),
                "data": base64.b64encode(rgb.tobytes()).decode(),
            }, "oracle_state": {"dtype": "int64", "shape": [1], "data": "AAAA"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    camera = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    server = create_server(store=store, port=0, artifact_root=tmp_path,
                           robosuite_url=f"http://127.0.0.1:{camera.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/runs/run-1") as response:
            snapshot = json.load(response)
        media_url = snapshot["traces"]["trace-1"]["evidence"][0]["media_url"]
        assert snapshot["station"]["camera_url"] == "/api/station/frame?run=run-1"
        with urlopen(base + media_url) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.read().startswith(b"\x89PNG")
        video_url = snapshot["traces"]["trace-1"]["evidence"][1]["media_url"]
        with urlopen(Request(base + video_url, headers={"Range": "bytes=2-5"})) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == "bytes 2-5/10"
            assert response.read() == b"2345"
        with urlopen(base + "/api/station/frame?run=run-1") as response:
            assert response.read().startswith(b"\x89PNG")
        try:
            urlopen(base + "/api/traces/trace-1/evidence/evidence:oracle_state")
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("non-advisor evidence was served")
        evidence_path.write_bytes(b"tampered")
        try:
            urlopen(base + media_url)
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("tampered evidence was served")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
        camera.shutdown(); camera.server_close(); camera_thread.join(timeout=2)


def test_run_bound_public_camera_survives_ui_restart_and_rejects_tampering(tmp_path) -> None:
    run_id, attempt_id = "run:attention-public-test", "attempt:attention-public-test:0"
    run, attempt, _, _ = records()
    store = AttentionStore(tmp_path / "attention.sqlite3")
    store.create_run(replace(run, run_id=run_id))
    store.create_attempt(replace(attempt, run_id=run_id, attempt_id=attempt_id))
    store.transition_run(run_id, RunStatus.RUNNING, event_key="public-start")
    rgb = np.zeros((3, 4, 3), dtype=np.uint8)
    rgb[:, :, 1] = 210

    class Camera(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps({"observation": {"agentview_image": {
                "dtype": "uint8", "shape": list(rgb.shape),
                "data": base64.b64encode(rgb.tobytes()).decode(),
            }, "oracle_state": {"dtype": "int64", "shape": [1], "data": "AAAA"}}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    camera = ThreadingHTTPServer(("127.0.0.1", 0), Camera)
    camera_thread = threading.Thread(target=camera.serve_forever, daemon=True)
    camera_thread.start()
    run_dir = tmp_path / "attention-public-test"
    publish_public_station(run_dir=run_dir, run_id=run_id, attempt_id=attempt_id,
                           suite="robosuite", origin=f"http://127.0.0.1:{camera.server_port}",
                           camera_name="agentview")

    def read_ui():
        server = create_server(store=AttentionStore(store.path), port=0, artifact_root=tmp_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/runs/" + run_id) as response:
                snapshot = json.load(response)
            assert snapshot["station"]["camera_url"]
            with urlopen(base + snapshot["station"]["camera_url"]) as response:
                return response.read()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    try:
        first = read_ui()
        assert first.startswith(b"\x89PNG")
        camera.shutdown(); camera.server_close(); camera_thread.join(timeout=2)
        store.transition_run(run_id, RunStatus.FAILED, event_key="public-finish")
        assert read_ui() == first
        (run_dir / "public_frame.png").write_bytes(b"tampered")
        with pytest.raises(HTTPError) as failure:
            read_ui()
        assert failure.value.code == 502
        station = run_dir / "public_station.json"
        station_copy = tmp_path / "station-copy.json"
        station_copy.write_bytes(station.read_bytes())
        station.unlink()
        station.symlink_to(station_copy)
        assert read_public_station(tmp_path, run_id, "robosuite", {attempt_id}) is None
        receipt = run_dir / "public_frame_receipt.json"
        receipt_copy = tmp_path / "receipt-copy.json"
        receipt_copy.write_bytes(receipt.read_bytes())
        receipt.unlink()
        receipt.symlink_to(receipt_copy)
        assert read_public_frame(run_dir, "robosuite") is None
    finally:
        if camera_thread.is_alive():
            camera.shutdown(); camera.server_close(); camera_thread.join(timeout=2)


def test_public_camera_recorder_produces_playable_attempt_video(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")
    frame = rgb_png(np.full((16, 16, 3), 120, dtype=np.uint8))
    recorder = AdvisorCameraRecorder(tmp_path, lambda: frame, image_format="png", fps=5)
    recorder.start()
    time.sleep(0.25)
    video = recorder.stop()
    assert video is not None
    assert video.is_file() and video.stat().st_size > 100
    assert video.read_bytes()[4:8] == b"ftyp"


def test_robocasa_camera_bridge_exposes_only_selected_color_stream(monkeypatch) -> None:
    jpeg = b"\xff\xd8public-color\xff\xd9"
    def packet(device, stream, data):
        header = json.dumps({"type": 11, "data": {"device_id": device,
                            "stream_type": stream, "format": "jpeg"}}).encode()
        return struct.pack(">I", len(header)) + header + data

    class Socket:
        def __init__(self):
            self.frames = iter([
                packet("maniskill_wrist", "color", jpeg),
                packet("maniskill_base", "depth", jpeg),
                packet("maniskill_base", "color", jpeg),
            ])

        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def send(self, message): assert json.loads(message)["action"] == "subscribe"
        def recv(self, timeout): return next(self.frames)

    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: Socket())
    assert robocasa_camera_frame("ws://127.0.0.1:5580") == jpeg
    with pytest.raises(ValueError, match="unsupported public camera"):
        robocasa_camera_frame("ws://127.0.0.1:5580", device_id="oracle")
