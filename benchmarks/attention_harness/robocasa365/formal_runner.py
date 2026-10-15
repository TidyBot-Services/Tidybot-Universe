"""Formal process boundary for the two frozen official RoboCasa365 dev tasks."""

import hashlib, json, threading, time
from pathlib import Path
from uuid import uuid4
import numpy as np
from PIL import Image
from .config import _config
from .service import DedicatedService, OutcomeUnknown
from .adapter import Adapter, build_sdk
from .safety import Safety
from .preview import RoboCasa365Preview
from .recording import finalize_recording
from .sandbox import execute_formal_policy, probe_sandbox, _json_safe
from ..policy_input import public_attention_input


def public_demo_steps(attention):
    raw = attention.get("demo_prior")
    if not raw:
        return []
    demo = json.loads(raw)
    if demo.get("schema_version") != "attentionbench.public-demo.v1":
        raise ValueError("invalid public demo projection")
    steps = [
        step
        for asset in demo["assets"]
        if asset.get("kind") == "action_trajectory"
        for step in asset.get("steps", [])
    ]
    if not steps or len(steps) > 32:
        raise ValueError("fixed demo requires nonempty bounded public actions")
    return steps


def write(path, value):
    path.write_text(
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    return {
        "uri": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class RoboCasa365FormalSuiteRunner:
    suite = "robocasa365"

    def __init__(
        self,
        *,
        service_source_root,
        sim_python,
        sim_source_root,
        task_source_root,
        asset_manifest,
        record_replay=False,
        independent_evaluation=False,
        service_process_observer=None,
    ):
        self.root = Path(service_source_root)
        self.python = Path(sim_python)
        self.rc = Path(sim_source_root)
        self.rs = Path(task_source_root)
        self.manifest = Path(asset_manifest)
        self.independent_evaluation = independent_evaluation
        self.service_process_observer = service_process_observer
        self.record_replay = record_replay
        self.cancel_event = threading.Event()

    def execute(self, request):
        c = _config(request)
        limit = min(c["sdk_call_limit"], request.sdk_call_limit or c["sdk_call_limit"])
        started = time.monotonic()
        seconds = request.effective_attempt_deadline_seconds
        if seconds > c["attempt_wall_seconds"]:
            raise ValueError("RoboCasa365 explicit attempt deadline required")
        deadline = started + seconds
        episode = request.artifact_root / (
            request.task_id
            + "-seed"
            + str(request.seed)
            + "-formal-"
            + uuid4().hex[:12]
        )
        episode.mkdir(parents=True, exist_ok=False)
        identity = {"run_id": request.run_id, "attempt_id": request.attempt_id}
        source = request.policy_code_path.read_bytes()
        (episode / "policy.py").write_bytes(source)
        write(episode / "config.json", c)
        service = DedicatedService(
            source_root=self.root,
            python=self.python,
            robocasa_root=self.rc,
            robosuite_root=self.rs,
            asset_manifest=self.manifest,
            episode_dir=episode,
            deadline=deadline,
            cancel_check=self.cancel_event.is_set,
        )
        events = []
        safety = None
        adapter = None
        native = {"native_success": False, "evaluated": False, "reason": "not_started"}
        status = "failed"
        error = None
        outcome = None
        sandbox_probe = None
        stop_watch = threading.Event()
        initial = None
        final = None
        preview = None
        control_interface_status = "not_started"
        recording_context = dict(**identity, camera_name=c['camera_names'][0],
            started_monotonic=started, preview_dir=str(request.artifact_root.parent.resolve()))
        if self.record_replay:
            write(episode / 'recording_context.json', recording_context)

        def watch():
            while not stop_watch.wait(0.05):
                if self.cancel_event.is_set() or time.monotonic() >= deadline:
                    service.stop(
                        "operator_cancel" if self.cancel_event.is_set() else "deadline"
                    )
                    break

        watcher = threading.Thread(target=watch, daemon=True)
        try:
            sandbox_probe = probe_sandbox()
            service.process_observer = self.service_process_observer
            ready = service.start()
            control_interface_status = "attested"
            write(episode / "identity.json", ready)
            watcher.start()
            reset = service.owner.call("/reset", {"config": c})
            write(
                episode / "reset_receipt.json",
                {
                    "episode_id": reset["episode_id"],
                    "identity": reset["identity"],
                    "capabilities": reset["capabilities"],
                },
            )
            adapter = Adapter(service.public, reset)
            if request.run_id and request.attempt_id and (not ready.get('recording_supported') or not self.record_replay):
                preview = RoboCasa365Preview(request.artifact_root.parent, request.run_id,
                    request.attempt_id, service.public.url, c["camera_names"][0])
                adapter.observation_sink = preview.publish
            safety = Safety(adapter, c["safety_limits"], self.cancel_event.is_set)

            def record_event(event):
                event = dict(event)
                # A single monotonic clock also spans simulator startup and all RGB frames.
                event['timestamp'] = time.monotonic() - started
                event['timestamp_basis'] = 'relative'
                if isinstance(event.get("result"), list):
                    event["result"] = {
                        "objects": event["result"],
                        "perception_mode": "sim_gt",
                        "source": "official_simulator_object_observation",
                    }
                events.append(event)

            sdk = build_sdk(safety, record_event)
            initial = adapter.public_observation()
            self.save_observation(episode, "initial", initial)
            attention = public_attention_input(request.attention_input)
            write(
                episode / "policy_context.json",
                {
                    "attention_input": attention,
                    "perception_mode": "sim_gt",
                    "source": "official RoboCasa365 public SDK only",
                    "robot": c["robot"],
                    "controller": c["controller"],
                    "task_id": c["task_id"],
                },
            )
            outcome = execute_formal_policy(
                code=source.decode(),
                sdk=sdk,
                context={
                    "attention_input": attention,
                    "demo_steps": public_demo_steps(attention),
                    "capabilities": reset["capabilities"],
                    "perception_mode": "sim_gt",
                    "robot": c["robot"],
                    "controller": c["controller"],
                    "task_id": c["task_id"],
                },
                deadline=deadline,
                stderr_path=episode / "stderr.txt",
                cancel_check=self.cancel_event.is_set,
                max_sdk_calls=limit,
            )
            status, error = outcome.status, outcome.error
            if self.cancel_event.is_set():
                status, error = "cancelled", "operator cancelled"
            elif time.monotonic() >= deadline:
                status, error = "timeout", "attempt deadline"
            if (
                not adapter.failed
                and not self.cancel_event.is_set()
                and time.monotonic() < deadline
            ):
                final = adapter.public_observation()
                self.save_observation(episode, "final", final)
                if adapter.ledger:
                    original = adapter.ledger[-1]
                    duplicate = service.public.call(
                        "/step",
                        {
                            key: original[key]
                            for key in ("request_id", "episode_id", "parts", "steps")
                        },
                    )
                    after_duplicate = adapter.public_observation()
                    if (
                        duplicate != original
                        or after_duplicate["sequence"] != adapter.sequence
                    ):
                        raise OutcomeUnknown(
                            "duplicate request re-executed or receipt changed"
                        )
                    write(
                        episode / "idempotence_real.json",
                        {
                            "same_receipt": True,
                            "sequence_unchanged": True,
                            "request_id": original["request_id"],
                            "sequence": adapter.sequence,
                            "duplicate_receipt": duplicate,
                        },
                    )
                if self.independent_evaluation:
                    try:
                        native = service.owner.call('/native_success')
                    except Exception as exc:
                        # Dev feedback remains the pre-evaluation execution status.
                        native = {'native_success':False,'evaluated':False,
                                  'reason':f'{type(exc).__name__}: {exc}'}
                else:
                    native = service.owner.call("/native_success")
        except Exception as exc:
            status = (
                "cancelled"
                if self.cancel_event.is_set()
                else (
                    "timeout"
                    if isinstance(exc, TimeoutError) or time.monotonic() >= deadline
                    else "failed"
                )
            )
            error = type(exc).__name__ + ": " + str(exc)
            native = {"native_success": False, "evaluated": False, "reason": error}
        finally:
            if preview is not None:
                preview.finish()
            stop_watch.set()
            if watcher.is_alive():
                watcher.join(timeout=6)
            cleanup = service.stop(
                "operator_cancel"
                if status == "cancelled"
                else "deadline" if status == "timeout" else "normal_cleanup"
            )
            recording_context["control_interface_status"] = ("outcome_unknown" if adapter and adapter.failed else control_interface_status)
            recording = (finalize_recording(episode, recording_context, status)
                         if self.record_replay else None)
        backend_steps = adapter.ledger if adapter else []
        for item in backend_steps:
            with (episode / "trace.jsonl").open("a") as f:
                f.write(json.dumps(item, sort_keys=True) + "\n")
        safety_data = {
            "source": "independent_safety_monitor",
            "unsafe_attempts": len(safety.violations) if safety else 0,
            "events": safety.events if safety else [],
            "violations": safety.violations if safety else [],
            "monitor_available": safety is not None,
            "limits": c["safety_limits"],
            **identity,
        }
        receipt = {
            "camera_recording": recording,
            "camera_preview": preview.receipt if preview is not None else None,
            "elapsed_seconds": time.monotonic() - started,
            "deadline_seconds": seconds,
            "service_stop": cleanup,
            "sandbox_probe": sandbox_probe,
            "policy_process": outcome.__dict__ if outcome else None,
            "sdk_call_budget": {
                "limit": limit,
                "requested": outcome.call_count if outcome else 0,
                "dispatched": min(outcome.call_count, limit) if outcome else 0,
            },
            "action_results_verified": all(
                i["status"] == "completed" for i in backend_steps
            )
            and not (adapter and adapter.failed),
            **identity,
        }
        trace = {
            "schema_version": "attentionbench.formal-trace.v1",
            "suite": self.suite,
            "task_id": request.task_id,
            "seed": request.seed,
            "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "entry_lock": request.entry_lock,
            "status": status,
            "sdk_events": events,
            "backend_steps": backend_steps,
            "perception_mode": "sim_gt",
            "robot": c["robot"],
            "controller": c["controller"],
            "error": error,
            **identity,
        }
        native = {**native, "status": status, **identity}
        artifacts = {
            name: write(episode / (name + ".json"), v)
            for name, v in [
                ("trace", trace),
                ("safety", safety_data),
                ("sandbox_receipt", receipt),
                ("native_result", native),
            ]
        }
        result = {
            "recording": recording,
            "control_interface_status": recording_context["control_interface_status"],
            "schema_version": "attentionbench.formal-runner-result.v1",
            "suite": self.suite,
            "task_id": request.task_id,
            "seed": request.seed,
            "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "entry_lock": request.entry_lock,
            "status": status,
            "native_success": native["native_success"],
            "native_evaluator": native,
            "sandbox": {
                "process_isolated": sandbox_probe is not None,
                "sdk_rpc_only": sandbox_probe is not None,
                "deadline_enforced": True,
                "action_cancellation_verified": cleanup["leader_reaped"]
                and cleanup["process_group_gone"],
            },
            "artifact_dir": str(episode.resolve()),
            "artifacts": artifacts,
            "formal_eligible": False,
            "error": error,
            **identity,
        }
        write(episode / "result.json", result)
        return result

    @staticmethod
    def save_observation(episode, label, obs):
        write(episode / (label + "_public_observation.json"), obs)
        arrays = {k: np.asarray(v) for k, v in obs["robot"].items()}
        np.savez_compressed(episode / (label + "_observation.npz"), **arrays)
        for index, (name, value) in enumerate(obs["images"].items()):
            arr = np.asarray(value, dtype=np.uint8)
            Image.fromarray(arr).save(
                episode
                / (
                    ((("camera_" + label) if index == 0 else name + "_" + label))
                    + ".png"
                )
            )
