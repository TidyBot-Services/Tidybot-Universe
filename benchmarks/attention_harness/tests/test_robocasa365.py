"""Targeted engineering fixtures. Real Service evidence lives separately in delivery."""

import hashlib, json, threading, time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
import numpy as np
from benchmarks.attention_harness.robocasa365.config import (
    validate_config,
    memory_context,
)
from benchmarks.attention_harness.robocasa365.adapter import Adapter, build_sdk
from benchmarks.attention_harness.robocasa365.safety import Safety
from benchmarks.attention_harness.robocasa365.memory import ScopedMemoryGateway, SCOPE
from benchmarks.attention_harness.robocasa365.sandbox import (
    execute_formal_policy,
    probe_sandbox,
)
from benchmarks.attention_harness.robocasa365.policy_validation import (
    validate_generated_policy,
)
from benchmarks.attention_harness.robocasa365.service import OutcomeUnknown
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
from benchmarks.attention_harness.core.policies import (
    POLICY_IDS,
    build_policy,
    PolicyContext,
    DecisionAction,
)
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
from benchmarks.attention_harness.core.models import MemoryStatus
from test_formal_attention_chain import FakeFormalRunner

DELIVERY = Path("/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def config(task="pick_place_counter_to_sink"):
    return json.loads((DELIVERY / "configs" / f"{task}_seed101.json").read_bytes())


@pytest.mark.parametrize("task", ["pick_place_counter_to_sink", "open_cabinet"])
def test_two_frozen_tasks_official_identity(task):
    c = validate_config(config(task))
    assert c["formal_eligible"] is False and c["seed"] == 101 and c["layout_id"] == 11
    freeze = DELIVERY / "ACCEPTANCE_FROZEN.json"
    assert sha(freeze) == (DELIVERY / "ACCEPTANCE_FROZEN.sha256").read_text().split()[0]
    assert (
        c["robocasa_commit"]
        == json.loads(freeze.read_bytes())["official"]["robocasa_commit"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", 1001),
        ("robot", "Panda"),
        ("controller", "BASIC"),
        ("split", "target"),
        ("layout_id", 1),
        ("perception_mode", "vision"),
        ("formal_eligible", True),
        ("horizon", 101),
        ("task_prompt", "Changed goal"),
        ("asset_manifest_sha256", "0" * 64),
    ],
)
def test_no_config_relaxation(field, value):
    c = config()
    c[field] = value
    with pytest.raises(ValueError):
        validate_config(c)


class PublicClient:
    def __init__(self, bad=False):
        self.bad = bad
        self.calls = []
        self.sequence = 0

    def call(self, path, body=None):
        self.calls.append((path, body))
        if path == "/observation":
            return {
                "episode_id": "ep",
                "sequence": self.sequence,
                "robot": {
                    "robot0_right_eef_pos": [0, 0, 1],
                    "robot0_joint_pos": [0] * 7,
                    "robot0_base_pos": [0, 0, 0],
                },
                "images": {},
                "perception": {"mode": "sim_gt", "values": {"cup_pos": [1, 2, 3]}},
            }
        if path == "/step":
            before = self.sequence
            self.sequence += body["steps"]
            h = hashlib.sha256(
                json.dumps(
                    {k: body[k] for k in ("episode_id", "parts", "steps")},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            return {
                "status": "completed",
                "episode_id": "wrong" if self.bad else "ep",
                "request_id": body["request_id"],
                "request_sha256": h,
                "sequence_before": before,
                "sequence_after": self.sequence,
                "steps": body["steps"],
                "observation_sha256": hashlib.sha256(
                    json.dumps(
                        self.call("/observation"), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
            }
        raise AssertionError(path)


def adapter(client=None):
    # Deliberately permuted indexes catch accidental old Panda [0:7] assumptions.
    reset = {
        "episode_id": "ep",
        "capabilities": {
            "part_indexes": {
                "base": [0, 3],
                "torso": [3, 4],
                "right_gripper": [4, 5],
                "right": [5, 11],
            },
            "controllers": {
                "right": {
                    "output_min": [-0.05] * 3 + [-0.5] * 3,
                    "output_max": [0.05] * 3 + [0.5] * 3,
                }
            },
        },
    }
    return Adapter(client or PublicClient(), reset)


def test_sdk_maps_physical_arm_and_mobile_parts_without_old_slices():
    a = adapter()
    s = Safety(a, config()["safety_limits"])
    events = []
    sdk = build_sdk(s, events.append)
    sdk.arm.move_delta(0.01, 0, 0, (0, 0, 0.1))
    sdk.gripper.close(settle_steps=2)
    sdk.torso.move_normalized([0.01])
    sdk.base.move_normalized([0.01, 0, 0])
    bodies = [v for path, v in a.client.calls if path == "/step"]
    assert np.allclose(bodies[0]["parts"]["right"], [0.2, 0, 0, 0, 0, 0.2])
    assert bodies[-1]["parts"]["base_mode"] == 1 and bodies[-1]["parts"][
        "right_gripper"
    ] == [1]
    assert len(a.ledger) == 4 and len(events) == 4 and not s.violations
    with pytest.raises(RuntimeError):
        sdk.arm.move_to_position(1, 2, 3)


def test_unknown_receipt_stops_adapter_and_no_retry():
    a = adapter(PublicClient(bad=True))
    with pytest.raises(OutcomeUnknown):
        a.move_arm_delta(0, 0, 0.001, (0, 0, 0))
    with pytest.raises(RuntimeError):
        a.set_gripper(-1, settle_steps=1)
    assert len([path for path, body in a.client.calls if path == "/step"]) == 1


@pytest.mark.parametrize(
    "part,values",
    [
        ("base", [float("nan"), 0, 0]),
        ("torso", [2]),
        ("base", [0, 0]),
        ("torso", [True]),
    ],
)
def test_independent_safety_rejects_before_action(part, values):
    a = adapter()
    s = Safety(a, config()["safety_limits"])
    with pytest.raises(RuntimeError):
        s.normalized(part, values)
    assert not a.client.calls and len(s.violations) == 1


def test_real_os_policy_boundary_timeout_cancel_and_unknown_action(tmp_path):
    evidence = probe_sandbox()
    assert (
        evidence["host_home_visible"] is False
        and evidence["network_reachable"] is False
    )
    sdk = build_sdk(Safety(adapter(), config()["safety_limits"]), lambda e: None)
    out = execute_formal_policy(
        code="from robot_sdk import sensors\nwhile True: pass\n",
        sdk=sdk,
        context={},
        deadline=time.monotonic() + 0.15,
        stderr_path=tmp_path / "timeout.log",
    )
    assert out.status == "timeout"
    out = execute_formal_policy(
        code="from robot_sdk import sensors\nwhile True: pass\n",
        sdk=sdk,
        context={},
        deadline=time.monotonic() + 2,
        stderr_path=tmp_path / "cancel.log",
        cancel_check=lambda: True,
    )
    assert out.status == "cancelled"
    for code in [
        "import os\n",
        "from robot_sdk import sensors\nsensors.native_success()\n",
    ]:
        with pytest.raises(Exception):
            validate_generated_policy(code)
    calls = []

    class Unknown:
        def move_delta(self, *args):
            calls.append("arm")
            raise OutcomeUnknown("test-only unknown response")

    class Grip:
        def close(self):
            calls.append("grip")

    out = execute_formal_policy(
        code="from robot_sdk import arm, gripper\ntry:\n arm.move_delta(0,0,.001)\nexcept Exception:\n gripper.close()\n",
        sdk=SimpleNamespace(arm=Unknown(), gripper=Grip()),
        context={},
        deadline=time.monotonic() + 2,
        stderr_path=tmp_path / "unknown.log",
    )
    assert out.status == "failed" and calls == ["arm"]


def test_nonempty_fixed_demo_is_ingested_and_executed_by_policy_worker(tmp_path):
    from benchmarks.attention_harness.demo_prior import verify_demo_prior

    p = DELIVERY / "configs/pick_place_counter_to_sink_demo.json"
    public, _ = verify_demo_prior(
        p.read_text(),
        suite="robocasa365",
        task_id="pick_place_counter_to_sink",
        approved_sha256=sha(p),
    )
    assert len(json.loads(public)["assets"][0]["steps"]) == 3
    # Parse the verified projection in the parent; worker consumes its actual operations and argument values.
    a = adapter()
    events = []
    sdk = build_sdk(Safety(a, config()["safety_limits"]), events.append)
    from benchmarks.attention_harness.robocasa365.formal_runner import public_demo_steps

    steps = public_demo_steps({"demo_prior": public})
    code = 'from robot_sdk import sensors, arm, gripper\nfor step in context["demo_steps"]:\n args=step["arguments"]\n if step["operation"]=="sdk.sensors.get_observation": sensors.get_observation()\n elif step["operation"]=="sdk.arm.move_delta": arm.move_delta(args["dx"],args["dy"],args["dz"])\n elif step["operation"]=="sdk.gripper.open": gripper.open(settle_steps=args["settle_steps"])\n'
    out = execute_formal_policy(
        code=code,
        sdk=sdk,
        context={"attention_input": {"demo_prior": public}, "demo_steps": steps},
        deadline=time.monotonic() + 2,
        stderr_path=tmp_path / "demo.log",
    )
    assert out.status == "completed" and out.call_count == 3 and len(a.ledger) == 2
    arm_body = next(
        body
        for path, body in a.client.calls
        if path == "/step" and "right" in body["parts"]
    )
    assert arm_body["parts"]["right"][2] == pytest.approx(
        steps[1]["arguments"]["dz"] / 0.05
    )
    assert a.ledger[1]["steps"] == steps[2]["arguments"]["settle_steps"]
    manifest = json.loads(p.read_text())
    manifest["assets"][0]["sha256"] = "0" * 64
    raw = json.dumps(manifest)
    with pytest.raises(ValueError):
        verify_demo_prior(
            raw,
            suite="robocasa365",
            task_id="pick_place_counter_to_sink",
            approved_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        )


class FixtureFormalRunner(FakeFormalRunner):
    def __init__(self):
        super().__init__("robocasa365")

    def execute(self, request):
        result = super().execute(request)
        ref = result["artifacts"]["sandbox_receipt"]
        p = Path(ref["uri"])
        v = json.loads(p.read_text())
        v["service_stop"] = {
            "reason": "normal_cleanup",
            "leader_reaped": True,
            "process_group_gone": True,
        }
        p.write_text(json.dumps(v))
        ref["sha256"] = sha(p)
        return result


@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_seven_methods_shared_formal_chain_and_visible_reply_consumption(
    tmp_path, policy_id
):
    code = DELIVERY / "configs/policy_smoke.py"
    conf = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    runner = FixtureFormalRunner()
    packets = []

    def test_transport(packet):
        packets.append(packet)
        assert "native_success" not in json.dumps(packet)
        assert 0 < packet["provider_timeout_seconds"] <= 90
        return AdvisorTransportReply(
            content=json.dumps(
                {
                    "schema_version": "attentionbench.advisor-advice.v1",
                    "request_type": "hint",
                    "diagnosis": "TEST FIXTURE ONLY: completed public action sequence; next tiny bounded probe",
                    "guidance": "TEST FIXTURE ONLY: increase tiny upward control step",
                    "caution": "Keep original frozen command limits",
                    "confidence": 0.5,
                }
            ),
            model="parcc/GLM",
            latency_seconds=0,
            attempts=1,
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            request_id="test-fixture",
        )

    demo = DELIVERY / "configs/pick_place_counter_to_sink_demo.json"
    summary = run_formal_attention(
        suite="robocasa365",
        task_id="pick_place_counter_to_sink",
        seed=101,
        artifact_root=tmp_path / "runs",
        policy_id=policy_id,
        policy_code_path=code,
        approved_policy_sha256=sha(code),
        config_path=conf,
        approved_config_sha256=sha(conf),
        runner=runner,
        max_attempts=2,
        assistance_credits=1,
        token_limit=8000,
        overall_deadline_seconds=300,
        formal_attempt_deadline_seconds=120,
        formal_run_wall_seconds=300,
        advisor_transport=test_transport,
        sleeper=lambda _: None,
        demo_prior=demo.read_text() if policy_id == "demo_first" else None,
        approved_demo_sha256=sha(demo) if policy_id == "demo_first" else None,
        policy_config=(
            {"target_request_count": 1, "total_failure_slots": 1, "seed": 101}
            if policy_id == "budget_matched_random_escalation"
            else {"k": 1} if policy_id == "retry_k_then_ask" else None
        ),
    )
    assert len(runner.requests) == 2 and summary["formal_eligible"] is False
    assert summary["memory_context"]["robot"] == "PandaOmron"
    assert summary["scheduler_config"]["robot_configuration"]["robot"] == {"model":"PandaOmron","execution_arm":"right"}
    if policy_id in [
        "reactive_help",
        "retry_k_then_ask",
        "budget_matched_random_escalation",
    ]:
        assert (
            len(packets) == 1 and runner.requests[1].attention_input["advisor_guidance"]
        )
    if policy_id in ["autonomous", "demo_first"]:
        assert not packets
    if policy_id == "demo_first":
        assert runner.requests[0].attention_input["demo_prior"]
    if policy_id == "budget_matched_random_escalation":
        pre = json.loads(
            (Path(summary["artifact_dir"]) / "random_preregistration.json").read_bytes()
        )
        assert (
            pre["selected_slots"] == [0]
            and pre["source"]
            == "operator_pre_registered_target_no_full_trace_or_result"
        )
    store = AttentionStore(Path(summary["store"]))
    raw = store.get_raw_trace(summary["attempts"][0]["attention_trace"]["raw_trace_id"])
    assert raw["code"]["sha256"] == sha(code)
    for row in summary["requests"]:
        if row.get("candidate_memory_id"):
            mem = store.get_memory(row["candidate_memory_id"])
            assert all(
                mem.applicability[k] == summary["memory_context"][k] for k in SCOPE
            )


def test_retry_k_and_full_planner_keep_original_history_and_safety_rules():
    ctx = PolicyContext("run", 1, 0, 1, 1, 1, True)
    assert (
        build_policy("retry_k_then_ask", k=2).decide(ctx).action == DecisionAction.RETRY
    )
    assert (
        build_policy("retry_k_then_ask", k=1).decide(ctx).action
        == DecisionAction.REQUEST
    )
    assert (
        build_policy("trace_aware_hint_only")
        .decide(PolicyContext("run", 2, 1, 2, 1, 1, True, matching_memory_count=1))
        .action
        != DecisionAction.RETRIEVE_MEMORY
    )
    assert (
        build_policy("full_trace_aware_attention_planner")
        .decide(PolicyContext("run", 2, 1, 2, 1, 1, True, unsafe=True))
        .action
        == DecisionAction.STOP
    )


class MemoryFixture:
    def __init__(self, records):
        self.records = records
        self.created = None
        self.authorization = 0

    def retrieve(self, context, now):
        return self.records

    def authorize_use(self, context, *, memory_id, attempt_id, now):
        self.authorization += 1
        return {"version": 2, "attempt_id": attempt_id, "context": context}

    def create_candidate(self, **payload):
        self.created = payload
        return payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("suite", "robocasa"),
        ("benchmark", "legacy"),
        ("robot", "Panda"),
        ("controller", "BASIC"),
        ("task_id", "open_cabinet"),
    ],
)
def test_memory_cross_identity_and_unvalidated_scope_rejected(field, value):
    ctx = memory_context(config())
    scope = {k: ctx[k] for k in SCOPE}
    scope[field] = value
    record = SimpleNamespace(
        memory_id="legacy", version=2, applicability=scope, status=MemoryStatus.TRUSTED
    )
    fixture = MemoryFixture([record])
    gate = ScopedMemoryGateway(fixture, ctx)
    assert gate.retrieve(ctx, now=1) == []
    with pytest.raises(PermissionError):
        gate.authorize_use(ctx, memory_id="legacy", attempt_id="attempt:x", now=1)
    assert fixture.authorization == 0


def test_memory_exact_version_grant_and_candidate_scope(tmp_path):
    ctx = memory_context(config())
    scope = {k: ctx[k] for k in SCOPE}
    record = SimpleNamespace(
        memory_id="scope-valid-test-only",
        version=2,
        applicability=scope,
        status=MemoryStatus.TRUSTED,
    )
    fixture = MemoryFixture([record])
    gate = ScopedMemoryGateway(fixture, ctx)
    grant = gate.authorize_use(
        ctx, memory_id=record.memory_id, attempt_id="attempt:one", now=1
    )
    assert grant["version"] == 2 and grant["attempt_id"] == "attempt:one"
    gate.create_candidate(
        applicability={
            "suite": "robocasa365",
            "task_id": ctx["task_id"],
            "perception_mode": "sim_gt",
        },
        guidance="test-only",
    )
    assert fixture.created["applicability"] == scope
    with pytest.raises(PermissionError):
        gate.retrieve({**ctx, "robot": "Panda"}, now=1)


def test_service_http_idempotence_native_access_and_unknown_stop(tmp_path):
    from robocasa365_sim.backend import OfficialBackend
    from robocasa365_sim.server import serve

    class Composite:
        def create_action_vector(self, parts):
            vector = np.zeros(12)
            for name, (start, end) in backend.parts.items():
                vector[start:end] = parts.get(name, np.zeros(end - start))
            vector[-1] = parts.get("base_mode", -1)
            return vector

    class Environment:
        action_spec = (-np.ones(12), np.ones(12))

        def __init__(self):
            self.executed = 0
            self.closed = False
            self.fail = False

        def step(self, action):
            self.executed += 1
            if self.fail:
                raise RuntimeError("TEST ONLY: fault after actual side effect")
            return (
                {"robot0_eef_pos": np.array([0, 0, 1])},
                999,
                True,
                {"success": True, "debug": "private"},
            )

        def close(self):
            self.closed = True

        def _check_success(self):
            return False

    backend = OfficialBackend.__new__(OfficialBackend)
    backend.env = Environment()
    env = backend.env
    backend.episode = "ep"
    backend.count = 0
    backend.receipts = {}
    backend.faulted = False
    backend.config = {"horizon": 100}
    backend.obs = {"robot0_eef_pos": np.array([0, 0, 1])}
    backend.parts = {
        "base": (0, 3),
        "torso": (3, 4),
        "right_gripper": (4, 5),
        "right": (5, 11),
    }
    backend.comp = Composite()
    backend.journal = tmp_path / "service_journal.jsonl"
    backend.identity = {}
    backend.recorder = None  # Match constructor state for this isolated HTTP fixture.
    server = serve(backend, "owner", "public", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(server.server_port)

    def call(path, value=None, owner=False):
        with urlopen(
            Request(
                url + path,
                data=None if value is None else json.dumps(value).encode(),
                headers={
                    "Authorization": "Bearer " + ("owner" if owner else "public"),
                    "Content-Type": "application/json",
                },
            ),
            timeout=2,
        ) as r:
            return json.load(r)

    try:
        payload = {
            "request_id": "same-id",
            "episode_id": "ep",
            "parts": {"right": [0.1, 0, 0, 0, 0, 0], "base_mode": -1},
            "steps": 1,
        }
        first = call("/step", payload)
        again = call("/step", payload)
        assert first == again and env.executed == 1
        assert first["action"][5] == 0.1 and first["sequence_after"] == 1
        with pytest.raises(HTTPError):
            call("/step", {**payload, "parts": {"right": [0.2, 0, 0, 0, 0, 0]}})
        assert env.executed == 1
        with pytest.raises(HTTPError) as caught:
            call("/native_success")
        assert caught.value.code == 403
        public = call("/observation")
        assert "reward" not in json.dumps(public) and "success" not in json.dumps(
            public
        )
        assert call("/native_success", owner=True)["native_success"] is False
        env.fail = True
        with pytest.raises(HTTPError):
            call("/step", {**payload, "request_id": "fault-id"})
        assert env.executed == 2 and env.closed and backend.faulted
        with pytest.raises(HTTPError):
            call("/step", {**payload, "request_id": "fault-id"})
        assert env.executed == 2
        journal = [
            json.loads(line) for line in backend.journal.read_text().splitlines()
        ]
        assert [j["event"] for j in journal].count("action_receipt") == 1 and any(
            j["event"] == "action_unknown" for j in journal
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_memory_candidate_and_wrong_version_grant_denied():
    ctx = memory_context(config())
    scope = {k: ctx[k] for k in SCOPE}
    record = SimpleNamespace(
        memory_id="candidate",
        version=2,
        applicability=scope,
        status=MemoryStatus.CANDIDATE,
    )
    gate = ScopedMemoryGateway(MemoryFixture([record]), ctx)
    assert gate.retrieve(ctx, now=1) == []
    record.status = MemoryStatus.TRUSTED
    record.version = 3
    with pytest.raises(PermissionError):
        gate.authorize_use(
            ctx, memory_id="candidate", attempt_id="attempt:wrong-version", now=1
        )


@pytest.mark.parametrize(
    "budget,value",
    [("max_attempts", 3), ("assistance_credits", 2), ("token_limit", 8001)],
)
def test_formal_entry_rejects_frozen_budget_excess(budget, value):
    p = DELIVERY / "configs/policy_smoke.py"
    c = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    options = dict(
        suite="robocasa365",
        task_id="pick_place_counter_to_sink",
        seed=101,
        policy_id="autonomous",
        code=p,
        approved_policy_sha256=sha(p),
        config=c,
        approved_config_sha256=sha(c),
        max_attempts=2,
        assistance_credits=1,
        token_limit=8000,
        assistance_mode="benchmark_proxy",
        human_deadline_seconds=30,
        overall_deadline_seconds=300,
    )
    options[budget] = value
    with pytest.raises(ValueError):
        inspect_formal_entry(**options)


def test_random_prereg_invalid_count_and_seed_fail_before_runner():
    # Verify the real CLI entry contract, not only deterministic random policy construction.
    import tempfile

    p = DELIVERY / "configs/policy_smoke.py"
    c = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    with tempfile.TemporaryDirectory() as root:
        random = Path(root) / "random.json"
        random.write_text(
            json.dumps(
                {"target_request_count": 2, "total_failure_slots": 1, "seed": 101}
            )
        )
        with pytest.raises(ValueError):
            inspect_formal_entry(
                suite="robocasa365",
                task_id="pick_place_counter_to_sink",
                seed=101,
                policy_id="budget_matched_random_escalation",
                code=p,
                approved_policy_sha256=sha(p),
                config=c,
                approved_config_sha256=sha(c),
                max_attempts=2,
                assistance_credits=1,
                token_limit=8000,
                assistance_mode="benchmark_proxy",
                human_deadline_seconds=30,
                overall_deadline_seconds=300,
                policy_config=random,
                approved_policy_config_sha256=sha(random),
            )


def test_observation_receipt_tamper_stops_adapter():
    client = PublicClient()
    a = adapter(client)
    a.move_arm_delta(0, 0, 0.001, (0, 0, 0))
    a.ledger[-1]["observation_sha256"] = "0" * 64
    with pytest.raises(OutcomeUnknown):
        a.public_observation()
    with pytest.raises(RuntimeError):
        a.move_arm_delta(0, 0, 0.001, (0, 0, 0))
    assert len([path for path, body in client.calls if path == "/step"]) == 1


def test_sdk_budget_stops_before_second_dispatch(tmp_path):
    calls = []

    class Sensors:
        def get_observation(self):
            calls.append(1)
            return {}

    out = execute_formal_policy(
        code="from robot_sdk import sensors\nsensors.get_observation()\nsensors.get_observation()\n",
        sdk=SimpleNamespace(sensors=Sensors()),
        context={},
        deadline=time.monotonic() + 2,
        stderr_path=tmp_path / "budget.log",
        max_sdk_calls=1,
    )
    assert out.status == "failed" and calls == [1]


def test_hint_only_gateway_never_read_and_zero_help_not_called(tmp_path):
    from attention_memory_service import MemoryService as InProcessMemoryGateway

    class ReadForbidden(InProcessMemoryGateway):
        def retrieve(self, *args, **kwargs):
            raise AssertionError("hint-only tried to read Memory")

    code = DELIVERY / "configs/policy_smoke.py"
    conf = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    store = AttentionStore(tmp_path / "store.sqlite3")
    runner = FixtureFormalRunner()
    summary = run_formal_attention(
        suite="robocasa365",
        task_id="pick_place_counter_to_sink",
        seed=101,
        artifact_root=tmp_path / "runs",
        policy_id="trace_aware_hint_only",
        policy_code_path=code,
        approved_policy_sha256=sha(code),
        config_path=conf,
        approved_config_sha256=sha(conf),
        runner=runner,
        max_attempts=2,
        assistance_credits=0,
        token_limit=0,
        overall_deadline_seconds=300,
        formal_attempt_deadline_seconds=120,
        formal_run_wall_seconds=300,
        store_path=store.path,
        memory_gateway=ReadForbidden(store, artifact_root=tmp_path),
        advisor_transport=lambda _: (_ for _ in ()).throw(
            AssertionError("zero budget called Advisor")
        ),
        sleeper=lambda _: None,
    )
    assert not summary["requests"] and summary["resource_usage"]["tokens"] == 0


def test_reviewed_ui_third_route_retry_k_and_budget_lock(tmp_path):
    from benchmarks.attention_harness.ui_launch import (
        load_catalog,
        public_options,
        launch_formal_run,
    )

    profiles = load_catalog(DELIVERY / "ui_catalog_with_retry_k.json")
    options = public_options(profiles)
    assert len(options["attention_policies"]) == 7 and {
        p["suite"] for p in options["profiles"]
    } == {"robocasa365"}
    selection = dict(
        profile_id=profiles[0]["id"],
        seed=101,
        attention_policy="retry_k_then_ask",
        execution_target="robocasa365_sim",
        assistance_mode="benchmark_proxy",
        max_attempts=2,
        assistance_credits=1,
        token_limit=8000,
        human_deadline_seconds=30,
        overall_deadline_seconds=300,
    )
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=99999999)

    receipt = launch_formal_run(
        selection,
        profiles=profiles,
        store=AttentionStore(tmp_path / "ui.sqlite3"),
        artifact_root=tmp_path / "runs",
        popen=fake_popen,
    )
    cmd = calls[0]
    assert "--policy-config" in cmd and cmd[cmd.index("--policy-config") + 1].endswith(
        "retry_k.json"
    )
    assert (
        cmd[cmd.index("--attempt-deadline-seconds") + 1] == "120"
        and receipt["locked"]["formal_eligible"] is False
    )
    with pytest.raises(ValueError):
        launch_formal_run(
            {**selection, "assistance_credits": 2},
            profiles=profiles,
            store=AttentionStore(tmp_path / "ui.sqlite3"),
            artifact_root=tmp_path / "runs",
            popen=fake_popen,
        )
    assert len(calls) == 1


def test_scoped_remote_memory_service_cannot_bypass_identity_health(tmp_path):
    from attention_memory_service import MemoryServiceClient

    class WrongSchema(MemoryServiceClient):
        def __init__(self):
            pass

        def health(self):
            return {"schema_version": "legacy.v1", "capabilities": []}

    code = DELIVERY / "configs/policy_smoke.py"
    conf = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    runner = FixtureFormalRunner()
    with pytest.raises(RuntimeError, match="Memory Service schema or store identity"):
        run_formal_attention(
            suite="robocasa365",
            task_id="pick_place_counter_to_sink",
            seed=101,
            artifact_root=tmp_path / "runs",
            policy_id="autonomous",
            policy_code_path=code,
            approved_policy_sha256=sha(code),
            config_path=conf,
            approved_config_sha256=sha(conf),
            runner=runner,
            max_attempts=1,
            assistance_credits=0,
            token_limit=0,
            overall_deadline_seconds=300,
            formal_attempt_deadline_seconds=120,
            formal_run_wall_seconds=300,
            memory_gateway=WrongSchema(),
            sleeper=lambda _: None,
        )
    assert not runner.requests
