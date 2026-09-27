#!/usr/bin/env python3
"""Exercise the real Orchestrator and Attention UI HTTP surfaces with fake agents.

No model or simulator is called. The temporary graph is removed on completion;
the JSON evidence is retained under artifacts/attentionbench-dag-smoke/.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO = Path(__file__).resolve().parents[3]
ORCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ORCH_DIR))

from benchmarks.attention_harness.core.store import AttentionStore  # noqa: E402
from benchmarks.attention_harness.ui_server import create_server  # noqa: E402


class SmokeAgentServer(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/code/jobs":
            value = {"jobs": list(self.server.jobs.values())}
            content_type = "application/json"
        elif self.path.startswith("/code/jobs/"):
            value = self.server.jobs.get(self.path.rsplit("/", 1)[-1])
            if value is None:
                self.send_error(404)
                return
            content_type = "application/json"
        elif self.path == "/code/recordings":
            value = {"recordings": list(reversed(self.server.recordings))}
            content_type = "application/json"
        elif self.path.startswith("/code/recordings/"):
            execution_id = self.path.rsplit("/", 1)[-1]
            if execution_id not in self.server.recordings:
                self.send_error(404)
                return
            value = {"execution_id": execution_id, "timeline": [], "frames": [],
                     "cameras": [], "frame_count": 0, "duration": 0.01}
            content_type = "application/json"
        elif self.path == "/code/sdk/markdown":
            value = "Transport smoke: no robot SDK calls. Execute the prewritten print-only script once."
            content_type = "text/plain"
        elif self.path == "/docs/guide/html":
            value = "Transport smoke only. No simulator or robot movement."
            content_type = "text/plain"
        else:
            self.send_error(404)
            return
        body = (json.dumps(value) if content_type == "application/json" else value).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/code/submit":
            self.send_error(404)
            return
        value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        holder = value.get("holder", "")
        skill = holder.split(":", 1)[-1]
        expected = {"skill-a": "SMOKE_A_PASS", "skill-b": "SMOKE_B_PASS"}.get(skill)
        code = value.get("code", "")
        passed = bool(expected and expected in code)
        stdout = expected + "\n" if passed else ""
        stderr = "" if passed else f"Expected marker {expected!r} missing\n"
        execution_id = uuid4().hex
        job_id = uuid4().hex
        job = {"job_id": job_id, "execution_id": execution_id,
               "holder": holder, "status": "completed",
               "result": {"exit_code": 0 if passed else 1, "stdout": stdout, "stderr": stderr}}
        self.server.jobs[job_id] = job
        self.server.recordings.append(execution_id)
        recording = self.server.recording_root / execution_id
        recording.mkdir(parents=True, exist_ok=True)
        (recording / "metadata.json").write_text(json.dumps({"execution_id": execution_id,
                                                               "holder": holder}))
        (recording / "stdout.log").write_text(stdout)
        (recording / "stderr.log").write_text(stderr)
        body = json.dumps({"job_id": job_id}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def request_json(url: str, *, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"X-Attention-Token": token} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                  headers=headers, method="POST" if body is not None else "GET")
    with urlopen(req, timeout=5) as response:
        return json.load(response)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parcc", action="store_true", help="run real OpenClaw Dev and Eval agents")
    parser.add_argument("--scenario", choices=("success", "eval-fail"), default="success")
    parser.add_argument("--restart-ui-inflight", action="store_true",
                        help="restart the UI server while the fake evaluator is running")
    args = parser.parse_args()
    if args.parcc and os.environ.get("HARNESS") != "openclaw":
        parser.error("--parcc requires HARNESS=openclaw and the PARCC key wrapper")
    if args.parcc and args.scenario != "success":
        parser.error("PARCC canary only supports the success scenario")
    if args.restart_ui_inflight and (args.parcc or args.scenario != "success"):
        parser.error("--restart-ui-inflight requires the fake success scenario")
    graphs = ORCH_DIR / "graphs"
    graphs.mkdir(exist_ok=True)
    evidence_dir = REPO / "artifacts" / "attentionbench-dag-smoke"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if args.parcc:
        # A public unauthenticated probe is enough to detect a down gateway;
        # any HTTP response means the network path is alive. No key is logged.
        probe_url = "https://litellm.parcc.upenn.edu/v1/models"
        try:
            await asyncio.to_thread(lambda: urlopen(probe_url, timeout=8).close())
            reachable = True
            error_type = None
        except HTTPError as error:
            reachable = True
            error_type = f"HTTP {error.code} (reachable)"
        except (URLError, TimeoutError, OSError) as error:
            reachable = False
            error_type = f"{type(error).__name__}: {error.reason if isinstance(error, URLError) else error}"
        (evidence_dir / "parcc-preflight.json").write_text(json.dumps({
            "reachable": reachable, "endpoint": probe_url, "error": error_type,
            "checked_at": time.time(),
        }, indent=2))
        if not reachable:
            raise RuntimeError(f"PARCC gateway unavailable: {error_type}")
    with tempfile.TemporaryDirectory(prefix="attention-smoke-", dir=graphs) as graph_root:
        graph_dir = Path(graph_root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), SmokeAgentServer)
        server.jobs = {}
        server.recordings = []
        agent_thread = threading.Thread(target=server.serve_forever, daemon=True)
        agent_thread.start()
        target_url = f"http://127.0.0.1:{server.server_port}"
        (graph_dir / "graph.json").write_text(json.dumps({
            "targets": [{"name": "smoke", "agent_server": target_url,
                         "sim_api": target_url, "primary": True}],
            "entries": [
                {"name": "skill-a", "description": "smoke leaf", "dependencies": [],
                 "status": "planned"},
                {"name": "skill-b", "description": "smoke dependent", "dependencies": ["skill-a"],
                 "status": "planned"},
            ],
        }))
        if args.parcc:
            for skill, marker in (("skill-a", "SMOKE_A_PASS"), ("skill-b", "SMOKE_B_PASS")):
                skill_dir = graph_dir / "skills" / skill
                (skill_dir / "scripts").mkdir(parents=True)
                (skill_dir / "scripts" / "main.py").write_text(f'print("{marker}")\n')
                (skill_dir / "SKILL.md").write_text(
                    f"# {skill} — transport smoke\n\n"
                    "This is an orchestration transport test, with no simulator or robot movement. "
                    f"The existing scripts/main.py prints {marker}. Do not rewrite it. "
                    "Read the local SDK reference as required, then submit the existing script exactly once "
                    "using submit_and_wait.py with --no-eval and --holder dev:"
                    f"{skill}. The fake Agent Server records stdout for the evaluator. "
                    "After the submission returns exit_code 0, finish your turn.\n"
                )

        sys.argv = ["agent_orchestrator.py", "--graph", str(graph_dir)]
        import agent_orchestrator as orch
        orch._load_entries()
        server.recording_root = orch.PROJECT_DIR / "logs" / "code_executions"
        transitions: list[dict] = []
        dispatches: list[dict] = []
        evaluations: list[str] = []
        original_update = orch._update_entry

        def update(skill: str, fields: dict) -> dict | None:
            result = original_update(skill, fields)
            if "status" in fields:
                transitions.append({"skill": skill, "status": fields["status"],
                                    "time": time.time()})
            return result

        async def evaluator(skill: str, execution_id: str | None = None) -> dict:
            evaluations.append(skill)
            await asyncio.sleep(0.35)
            passed = args.scenario == "success" or skill != "skill-a"
            return {"passed": passed, "feedback": "deterministic smoke verdict"}

        async def spawn(skill: str, prompt: str, agent_type: str = "dev",
                        target: dict | None = None) -> str:
            assert agent_type == "dev" and target is not None
            agent_id = f"fake-{skill}"
            state = orch.AgentState(agent_id=agent_id, skill=skill, agent_type="dev",
                                    status="running", target_name=target["name"])
            orch.agents[agent_id] = state
            dispatches.append({"skill": skill, "target": target["name"], "time": time.time()})

            async def finish() -> None:
                await asyncio.sleep(0.3)
                state.status = "done"
                await orch._handle_agent_done(state)

            asyncio.create_task(finish())
            return agent_id

        orch._update_entry = update
        if args.parcc:
            original_spawn = orch.spawn_agent
            original_evaluator = orch.run_evaluator

            async def record_spawn(skill: str, prompt: str, agent_type: str = "dev",
                                   target: dict | None = None) -> str:
                result = await original_spawn(skill, prompt, agent_type=agent_type, target=target)
                if result:
                    dispatches.append({"skill": skill, "target": target["name"] if target else "",
                                       "time": time.time()})
                return result

            async def record_evaluator(skill: str, execution_id: str | None = None) -> dict:
                result = await original_evaluator(skill, execution_id=execution_id)
                evaluations.append({"skill": skill, "passed": result.get("passed"),
                                    "feedback": result.get("feedback", "")[:200]})
                return result

            orch.spawn_agent = record_spawn
            orch.run_evaluator = record_evaluator
        else:
            orch.run_evaluator = evaluator
            orch.spawn_agent = spawn
        orch_http = await asyncio.start_server(orch.handle_http, "127.0.0.1", 0)
        orch_url = f"http://127.0.0.1:{orch_http.sockets[0].getsockname()[1]}"
        store_path = graph_dir / "attention.sqlite3"
        ui = create_server(store=AttentionStore(store_path), port=0, orchestrator_url=orch_url)
        ui_thread = threading.Thread(target=ui.serve_forever, daemon=True)
        ui_thread.start()
        ui_url = f"http://127.0.0.1:{ui.server_port}"

        try:
            def read_token() -> str:
                with urlopen(ui_url + "/ui/", timeout=5) as response:
                    page = response.read().decode()
                return re.search(r'data-control-token="([^"]+)"', page).group(1)

            token = await asyncio.to_thread(read_token)
            before = await asyncio.to_thread(request_json, ui_url + "/api/orchestrator/snapshot")
            assert [e["status"] for e in before["entries"]] == ["planned", "planned"]
            try:
                await asyncio.to_thread(request_json, ui_url + "/api/orchestrator/dispatch",
                                        token="invalid", body={"graph": graph_dir.name})
            except HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError("missing UI authorization was accepted")

            try:
                started = await asyncio.to_thread(request_json, ui_url + "/api/orchestrator/dispatch",
                                                  token=token, body={"graph": graph_dir.name})
            except HTTPError as error:
                raise AssertionError(
                    f"UI dispatch returned HTTP {error.code}: {error.read().decode()}"
                ) from error
            assert started["spawned"] == ["skill-a"]
            assert dispatches[0]["skill"] == "skill-a"
            deadline = time.monotonic() + (720 if args.parcc else 12)
            snapshots = []
            inflight_restart_verified = False
            while time.monotonic() < deadline:
                snap = await asyncio.to_thread(request_json, ui_url + "/api/orchestrator/snapshot")
                snapshots.append({e["name"]: e["status"] for e in snap["entries"]})
                if (args.restart_ui_inflight and not inflight_restart_verified
                        and snapshots[-1]["skill-a"] == "evaluating"):
                    ui.shutdown()
                    ui.server_close()
                    ui_thread.join(timeout=3)
                    ui = create_server(store=AttentionStore(store_path), port=0,
                                       orchestrator_url=orch_url)
                    ui_thread = threading.Thread(target=ui.serve_forever, daemon=True)
                    ui_thread.start()
                    ui_url = f"http://127.0.0.1:{ui.server_port}"
                    recovered = await asyncio.to_thread(
                        request_json, ui_url + "/api/orchestrator/snapshot")
                    assert {entry["name"] for entry in recovered["entries"]} == {
                        "skill-a", "skill-b"}
                    inflight_restart_verified = True
                finished = (all(status == "done" for status in snapshots[-1].values())
                            if args.scenario == "success" else
                            snapshots[-1] == {"skill-a": "review", "skill-b": "planned"})
                if finished:
                    break
                await asyncio.sleep(2 if args.parcc else 0.1)
            else:
                raise AssertionError(f"DAG did not complete: {snapshots[-1]}")
            if args.restart_ui_inflight:
                assert inflight_restart_verified, "UI never restarted during Eval"
            observed_transitions = [(t["skill"], t["status"]) for t in transitions]
            if args.scenario == "eval-fail":
                assert [d["skill"] for d in dispatches] == ["skill-a", "skill-a"]
                assert evaluations == ["skill-a", "skill-a"]
                assert observed_transitions == [
                    ("skill-a", "writing"), ("skill-a", "evaluating"),
                    ("skill-a", "writing"), ("skill-a", "failed"),
                    ("skill-a", "writing"), ("skill-a", "evaluating"),
                    ("skill-a", "review"),
                ]
            else:
                assert [d["skill"] for d in dispatches] == ["skill-a", "skill-b"]
                if args.parcc:
                    assert all(any(e["skill"] == skill and e["passed"] for e in evaluations)
                               for skill in ("skill-a", "skill-b"))
                else:
                    assert evaluations == ["skill-a", "skill-b"]
                expected_transitions = [
                    ("skill-a", "writing"), ("skill-a", "evaluating"), ("skill-a", "done"),
                    ("skill-b", "writing"), ("skill-b", "evaluating"), ("skill-b", "done"),
                ]
                if not args.parcc:
                    assert observed_transitions == expected_transitions
                else:
                    assert all(item in observed_transitions for item in expected_transitions)
                assert snapshots[-1] == {"skill-a": "done", "skill-b": "done"}

            ui.shutdown()
            ui.server_close()
            ui_thread.join(timeout=3)
            ui = create_server(store=AttentionStore(store_path), port=0,
                               orchestrator_url=orch_url)
            ui_thread = threading.Thread(target=ui.serve_forever, daemon=True)
            ui_thread.start()
            ui_url = f"http://127.0.0.1:{ui.server_port}"
            restored = await asyncio.to_thread(request_json, ui_url + "/api/orchestrator/snapshot")
            assert {e["name"]: e["status"] for e in restored["entries"]} == snapshots[-1]
            assert restored["autonomous_mode"] is True
            orch._load_entries()
            reload_expected = snapshots[-1]
            assert {e["name"]: e["status"] for e in orch.skill_entries} == reload_expected

            evidence = {"result": "passed", "mode": "parcc_dev_eval" if args.parcc else "fake_dev_eval_real_http",
                        "scenario": args.scenario,
                        "graph": graph_dir.name, "dispatches": dispatches,
                        "evaluations": evaluations, "transitions": transitions,
                        "snapshot_samples": snapshots, "ui_restart_verified": True,
                        "ui_inflight_restart_verified": inflight_restart_verified,
                        "orchestrator_reload_verified": True,
                        "status_after_orchestrator_reload": reload_expected}
            output_name = ("parcc-smoke.json" if args.parcc else
                           "process-eval-fail.json" if args.scenario == "eval-fail" else
                           "process-ui-inflight-restart.json" if args.restart_ui_inflight else
                           "process-smoke.json")
            output = evidence_dir / output_name
            output.write_text(json.dumps(evidence, indent=2))
            print(f"PASS: {output}")
        finally:
            if args.parcc:
                saved_graph = evidence_dir / "parcc-graph"
                if saved_graph.exists():
                    shutil.rmtree(saved_graph)
                shutil.copytree(graph_dir, saved_graph)
            ui.shutdown()
            ui.server_close()
            ui_thread.join(timeout=3)
            orch_http.close()
            await orch_http.wait_closed()
            server.shutdown()
            server.server_close()
            agent_thread.join(timeout=3)


if __name__ == "__main__":
    asyncio.run(main())
