"""Frozen-state, visibility, version and entry-binding rejection checks."""
import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_memory_contract import (
    ACCOUNTING, FrozenMemoryGateway, initialize_frozen_memory, validate_memory_contract,
)
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.tests.test_m1_entry import entry, digest
from benchmarks.attention_harness.tests.test_formal_attention_chain import FakeFormalRunner


def contract(tmp_path, kwargs, *, full=False):
    c = json.loads(kwargs["config"].read_bytes())
    value = {"schema_version": "attentionbench.formal-memory-contract.v1",
             "suite": kwargs["suite"], "task_id": kwargs["task_id"],
             "condition": kwargs["policy_id"], "visibility": "none",
             "initial_state": {"kind": "empty", "path": None, "sha256": None, "versions": []},
             "context": None, "reset_each_run": True, "automatic_promotion": False,
             "accounting": ACCOUNTING.copy(), "evidence_root": None}
    if full:
        source = tmp_path / "snapshot.sqlite3"
        AttentionStore(source)
        with sqlite3.connect(source) as connection:
            connection.execute("INSERT INTO memories(id,payload) VALUES(?,?)",
                               ("frozen-v1", json.dumps({"status": "trusted", "version": 1})))
        evidence = tmp_path / "source-evidence"
        evidence.mkdir()
        (evidence / "source.txt").write_text("source bytes")
        value.update(visibility="trusted_exact_versions", evidence_root=str(evidence),
                     context={"suite": c["suite"], "task_id": c["task_id"],
                              "perception_mode": c["perception_mode"], "scene_id": c["scene_id"],
                              "object_set_id": c["object_set_id"],
                              "camera_config_id": "camera-fixed", "task_variant_id": "variant-fixed",
                              "camera_names": c.get("camera_names", [c.get("camera_name")]),
                              "task_prompt": c.get("task_prompt", "Lift the cube clear of the table.")},
                     initial_state={"kind": "sqlite_snapshot", "path": str(source),
                                    "sha256": digest(source),
                                    "versions": [{"memory_id": "frozen-v1", "version": 1}]})
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(value))
    kwargs.update(memory_contract=path, approved_memory_contract_sha256=digest(path))
    return value


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
def test_m1_binds_memory_bytes_and_canonical_content(tmp_path, suite):
    args = entry(tmp_path, suite)
    value = contract(tmp_path, args)
    lock, _ = inspect_formal_entry(**args)
    assert lock["approved_memory_contract_sha256"] == digest(args["memory_contract"])
    args["memory_contract"].write_text(json.dumps({**value, "automatic_promotion": True}))
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_formal_entry(**args)
    args["approved_memory_contract_sha256"] = digest(args["memory_contract"])
    with pytest.raises(ValueError, match="reset or accounting"):
        inspect_formal_entry(**args)


@pytest.mark.parametrize("suite", ["robosuite", "robocasa"])
@pytest.mark.parametrize("change", ["scope", "version", "snapshot", "cache"])
def test_full_memory_rejects_changed_initial_state_and_scope(tmp_path, suite, change):
    args = entry(tmp_path, suite, "full_trace_aware_attention_planner")
    value = contract(tmp_path, args, full=True)
    inspect_formal_entry(**args)
    if change == "scope":
        value["context"]["scene_id"] = "wrong-scene"
    elif change == "version":
        value["initial_state"]["versions"][0]["version"] = 2
    elif change == "snapshot":
        value["initial_state"]["sha256"] = "0" * 64
    else:
        source = value["initial_state"]["path"]
        AttentionStore(args["memory_contract"].parent / "snapshot.sqlite3").cache_put("old", {"reply": "old"})
        value["initial_state"]["sha256"] = hashlib.sha256(open(source, "rb").read()).hexdigest()
    with pytest.raises(ValueError):
        validate_memory_contract(value, suite=suite, task_id=args["task_id"],
                                 policy_id=args["policy_id"], config=json.loads(args["config"].read_bytes()))


def test_each_run_starts_at_same_version_with_empty_cache_and_protected_source(tmp_path):
    args = entry(tmp_path, "robosuite", "full_trace_aware_attention_planner")
    value = contract(tmp_path, args, full=True)
    initial_sha = value["initial_state"]["sha256"]
    first, evidence1, _ = initialize_frozen_memory(value, artifact_root=tmp_path / "runs")
    AttentionStore(first).cache_put("new-answer", {"reply": "cached"})
    (evidence1 / "source.txt").write_text("run-local modification")
    second, evidence2, _ = initialize_frozen_memory(value, artifact_root=tmp_path / "runs")
    assert first != second and AttentionStore(second).cache_get("new-answer") is None
    assert digest(tmp_path / "snapshot.sqlite3") == initial_sha
    assert (evidence2 / "source.txt").read_text() == "source bytes"
    with sqlite3.connect(second) as connection:
        assert json.loads(connection.execute("SELECT payload FROM memories WHERE id='frozen-v1'").fetchone()[0])["version"] == 1


def test_hidden_conditions_have_no_memory_probe_or_grant():
    class NoCalls:
        def retrieve(self, *_args, **_kwargs):
            pytest.fail("hidden Memory was probed")
    value = {"visibility": "none", "context": None, "initial_state": {"versions": []}}
    gateway = FrozenMemoryGateway(NoCalls(), value)
    assert gateway.retrieve({}, now=1) == []
    with pytest.raises(PermissionError):
        gateway.authorize_use({}, memory_id="x", attempt_id="a", now=1)


@pytest.mark.parametrize("action", ["promote", "disable", "rollback", "set_expiry", "authorize_dev_use"])
def test_matrix_cannot_promote_or_change_lifecycle(action):
    gateway = FrozenMemoryGateway(object(), {"initial_state": {"versions": []}})
    with pytest.raises(PermissionError):
        getattr(gateway, action)


def test_gateway_requires_exact_service_version_and_attempt():
    value = {"visibility": "trusted_exact_versions", "context": {"scene": "fixed"},
             "initial_state": {"versions": [{"memory_id": "m", "version": 1}]}}
    class Service:
        version = 1
        attempt = "a"
        def retrieve(self, *_args, **_kwargs):
            return [SimpleNamespace(memory_id="m", version=self.version),
                    SimpleNamespace(memory_id="unlisted", version=1)]
        def authorize_use(self, *_args, **_kwargs):
            return {"memory_id": "m", "version": self.version, "attempt_id": self.attempt}
    service = Service()
    gateway = FrozenMemoryGateway(service, value)
    assert len(gateway.retrieve(value["context"], now=1)) == 1
    assert gateway.authorize_use(value["context"], memory_id="m", attempt_id="a", now=1)["version"] == 1
    service.version = 2
    with pytest.raises(ValueError, match="version drift"):
        gateway.retrieve(value["context"], now=1)
    with pytest.raises(ValueError, match="exact version/attempt"):
        gateway.authorize_use(value["context"], memory_id="m", attempt_id="a", now=1)
    with pytest.raises(ValueError, match="outside frozen"):
        gateway.retrieve({"scene": "other"}, now=1)


def test_direct_runtime_cannot_pair_altered_contract_with_approved_lock(tmp_path):
    args = entry(tmp_path, "robosuite")
    value = contract(tmp_path, args)
    lock, _ = inspect_formal_entry(**args)
    changed = copy.deepcopy(value)
    changed["reset_each_run"] = False
    with pytest.raises(ValueError, match="entry lock identity"):
        run_formal_attention(suite=args["suite"], task_id=args["task_id"], seed=101,
            artifact_root=tmp_path / "run", policy_id=args["policy_id"],
            policy_code_path=args["code"], approved_policy_sha256=args["approved_policy_sha256"],
            config_path=args["config"], approved_config_sha256=args["approved_config_sha256"],
            runner=FakeFormalRunner("robosuite"), max_attempts=2, assistance_credits=1,
            token_limit=100, assistance_mode="benchmark_proxy", human_deadline_seconds=30,
            overall_deadline_seconds=90, entry_lock=lock, memory_contract=changed,
            approved_memory_contract_sha256=args["approved_memory_contract_sha256"])
    assert not (tmp_path / "run").exists()


def test_frozen_retry_requires_explicit_k(tmp_path):
    args = entry(tmp_path, "robosuite", "retry_k_then_ask")
    contract(tmp_path, args)
    with pytest.raises(ValueError, match="explicit approved k"):
        inspect_formal_entry(**args)
