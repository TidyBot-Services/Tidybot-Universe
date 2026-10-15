"""Prevent V1 and unrelated services from crossing the V2 planning boundary."""
import copy
import importlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from curobo_v2 import identity as auth
from curobo_v2.client import V2Client, PlanningRejected
from curobo_v2.session import V2Service


@pytest.fixture
def lock(tmp_path):
    source = tmp_path / "pinned.py"
    source.write_text("# pinned\n")
    value = {"schema": "attentionbench.curobo-v2.runtime.v1", "generation": 2,
             "prefix": "/isolated", "python": "/isolated/bin/python", "library_root": "/v2",
             "library_revision": "commit", "library_base": "base", "module_version": "0.8.0.post1.dev44",
             "distribution_version": "0.8.0.post1.dev44", "service_root": "/service", "project_root": "/project",
             "files": {str(source): auth.digest(source)}, "dependencies": {"numpy": {"path": "/isolated/numpy/__init__.py"}},
             "distributions": {"nvidia-curobo": "0.8.0.post1.dev44"}, "robot_configs": {"/robot.yml": "cfg"}}
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps(value))
    return value, p


def service_identity(lock):
    return {"runtime": {"generation": 2, "prefix": "/isolated", "python": "/isolated/bin/python",
            "library_path": "/v2/curobo/__init__.py", "module_version": "0.8.0.post1.dev44", "library_revision": "commit",
            "dependencies": {"numpy": "/isolated/numpy/__init__.py"}}, "lock_sha256": "sha", "nonce": "nonce", "pid": 123,
            "service_root": "/service", "robot_config": "/robot.yml", "robot_config_sha256": "cfg", "kernel_backend":"cuda_core",
            "test_response_fault":"none", "test_gpu_activity":False}


def verify_payload(lock, value, *, token="secret", challenge="challenge"):
    return auth.verify_service({"identity": value, "signature": auth.sign(token, value, challenge)},
        lock=lock, lock_sha="sha", nonce="nonce", pid=123, token="secret", challenge="challenge", robot_config="/robot.yml")


def test_real_v2_08_version_identity_is_accepted(lock):
    value, _ = lock
    assert verify_payload(value, service_identity(value))["runtime"]["generation"] == 2


@pytest.mark.parametrize("field,bad", [("nonce", "old-instance"), ("pid", 999),
    ("lock_sha256", "different"), ("service_root", "/legacy"), ("robot_config", "/other.yml"),
    ("robot_config_sha256", "changed"), ("kernel_backend","auto")])
def test_even_signed_wrong_service_is_rejected(lock, field, bad):
    value, _ = lock
    current = service_identity(value)
    current[field] = bad
    with pytest.raises(auth.IdentityError, match="identity mismatch"):
        verify_payload(value, current)


@pytest.mark.parametrize("field,bad", [("generation", 1), ("prefix", "/shared"),
    ("python", "/v1/bin/python"), ("module_version", "0.7.8"),
    ("library_revision", "unapproved"), ("library_path", "/v1/curobo/__init__.py"),
    ("dependencies", {"numpy": "/external/numpy/__init__.py"})])
def test_v1_or_shadowed_runtime_rejected(lock, field, bad):
    value, _ = lock
    current = service_identity(value)
    current["runtime"][field] = bad
    with pytest.raises(auth.IdentityError):
        verify_payload(value, current)


@pytest.mark.parametrize("token,challenge", [("wrong", "challenge"), ("secret", "replayed")])
def test_signature_and_fresh_challenge_required(lock, token, challenge):
    value, _ = lock
    with pytest.raises(auth.IdentityError):
        verify_payload(value, service_identity(value), token=token, challenge=challenge)


def test_lock_cannot_be_implicit_or_relative(monkeypatch):
    monkeypatch.delenv("CUROBO_V2_LOCK", raising=False)
    with pytest.raises(auth.IdentityError):
        auth.load_lock()
    with pytest.raises(auth.IdentityError):
        auth.load_lock("runtime.json")


@pytest.mark.parametrize("mutation", ["generation", "dependencies", "prefix", "files"])
def test_incomplete_lock_refused(lock, mutation):
    value, path = lock
    value.pop(mutation)
    path.write_text(json.dumps(value))
    with pytest.raises(auth.IdentityError):
        auth.load_lock(path)


def test_source_mutation_and_missing_files_refused(lock):
    value, _ = lock
    auth.verify_files(value)
    source = Path(next(iter(value["files"])))
    source.write_text("# altered\n")
    with pytest.raises(auth.IdentityError, match="changed"):
        auth.verify_files(value)
    source.unlink()
    with pytest.raises(auth.IdentityError):
        auth.verify_files(value)


def test_wrong_interpreter_fails_before_loading_curobo(lock, monkeypatch):
    value, _ = lock
    monkeypatch.setattr(auth.importlib.util, "find_spec", lambda *a: pytest.fail("must not load planner"))
    with pytest.raises(auth.IdentityError, match="prefix"):
        auth.verify_runtime(value)


def test_shadow_v1_module_fails_before_import(lock, monkeypatch):
    value, _ = lock
    monkeypatch.setattr(sys, "prefix", "/isolated")
    monkeypatch.setattr(sys, "base_prefix", "/isolated")
    monkeypatch.setattr(sys, "executable", "/isolated/bin/python")
    monkeypatch.setattr(auth.importlib.util, "find_spec", lambda *a: SimpleNamespace(origin="/v1/curobo/__init__.py"))
    with pytest.raises(auth.IdentityError, match="import path"):
        auth.verify_runtime(value)


def test_v1_import_guard_rejects_without_fallback():
    guard = auth._RejectLegacy()
    for name in ["curobo.wrap.reacher.motion_gen", "curobo.cuda_robot_model.cuda_robot_model"]:
        with pytest.raises(auth.IdentityError, match="V1 import"):
            guard.find_spec(name)
    assert guard.find_spec("curobo.motion_planner") is None


def test_direct_service_import_forbidden():
    with pytest.raises(auth.IdentityError, match="Direct planner startup"):
        auth.require_service_context()


def test_direct_planning_core_refused_before_loading_gpu_modules(monkeypatch):
    monkeypatch.setattr(auth,'_service_context',None)
    path = Path(auth.__file__).parent/'service/curobo_service/planner_core.py'
    spec = importlib.util.spec_from_file_location('unowned_v2_core',path)
    with pytest.raises(auth.IdentityError,match='Direct planner startup'):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_service_context_cannot_authorize_foreign_interpreter(lock,monkeypatch):
    value,_ = lock
    monkeypatch.setattr(auth,'_service_context',None)
    with pytest.raises(auth.IdentityError,match='independent'):
        auth.authorize_service_import(value,'/robot.yml')
    with pytest.raises(auth.IdentityError):
        auth.require_service_context()


@pytest.mark.parametrize('url,config,deadline',[
    ('http://127.0.0.1:8080#foreign','/robot.yml',1.),
    ('http://:secret@127.0.0.1:8080','/robot.yml',1.),
    ('http://127.0.0.1:8080','relative.yml',1.),
    ('http://127.0.0.1:8080','/robot.yml',float('nan')),
    ('http://127.0.0.1:8080','/robot.yml',float('inf')),
])
def test_invalid_client_configuration_never_contacts_service(lock,tmp_path,monkeypatch,url,config,deadline):
    _,path=lock
    monkeypatch.setattr('curobo_v2.client.verify_client_imports',lambda *a:None)
    monkeypatch.setattr(V2Client,'attest',lambda *a:pytest.fail('must refuse before contacting service'))
    with pytest.raises(auth.IdentityError):
        V2Client(lock_path=path,url=url,nonce='nonce',pid=123,token='secret',
                 robot_config=config,deadline=deadline,evidence=tmp_path)


def test_failed_planning_latches_client_and_records_error(tmp_path):
    client = object.__new__(V2Client)
    client.failed, client.deadline = False, time.monotonic() + 5
    client.cancel_check = lambda: False
    client.nonce, client.lock_sha, client.evidence = "nonce", "sha", tmp_path
    client.worlds = {"default": (1, "world")}
    calls = []
    client.attest = lambda: calls.append("attest")
    client._request = lambda *a: {"status": "failed", "trajectory": None, "reason": "collision"}
    with pytest.raises(PlanningRejected, match="collision"):
        client.call("/plan", {})
    assert client.failed and calls == ["attest"]
    assert "collision" in (tmp_path / "v2_client.jsonl").read_text()


def test_expired_client_never_contacts_service():
    client = object.__new__(V2Client)
    client.failed, client.deadline = False, time.monotonic() - 1
    client.cancel_check = lambda: False
    with pytest.raises(PlanningRejected):
        client._request("/plan", {})


def test_missing_robot_config_never_spawns(lock, tmp_path, monkeypatch):
    value, path = lock
    monkeypatch.setattr('curobo_v2.session.load_lock',lambda path:(value,'fixture'))
    monkeypatch.setattr("curobo_v2.session.subprocess.Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    with pytest.raises(auth.IdentityError, match="robot configuration"):
        with V2Service(lock_path=path, robot_config="/unlocked.yml", evidence=tmp_path, deadline=time.monotonic()+5):
            pass


def test_source_contains_no_legacy_planning_imports():
    root = Path(auth.__file__).parent / "service/curobo_service"
    for path in root.rglob("*.py"):
        source = path.read_text()
        assert "from curobo.wrap" not in source
        assert "from maniskill_tidyverse.curobo_planner" not in source


def fake_client(tmp_path, response):
    client = object.__new__(V2Client)
    client.failed, client.deadline = False, time.monotonic() + 5
    client.cancel_check = lambda: False
    client.identity = {}
    client.nonce, client.lock_sha, client.evidence = "nonce", "sha", tmp_path
    client.worlds = {"default": (4, "current-world")}
    client.attest = lambda: None
    client._request = lambda *a: response
    return client


@pytest.mark.parametrize('revision,world', [(3,'current-world'), (4,'different-world'), (None,None)])
def test_stale_world_trajectory_rejected(tmp_path, revision, world):
    client = fake_client(tmp_path, {'status':'success','trajectory':[[0]],
                                   'world_revision':revision,'world_sha256':world})
    with pytest.raises(auth.IdentityError, match='stale collision world'):
        client.call('/plan/joint', {})
    assert client.failed


def test_missing_world_never_plans(tmp_path):
    client = fake_client(tmp_path, {})
    client.worlds = {}
    client._request = lambda *a: pytest.fail('must not send planning request')
    with pytest.raises(PlanningRejected, match='explicit complete world'):
        client.call('/plan', {})


def test_geometry_omission_rejected(tmp_path):
    client = fake_client(tmp_path, {'env_id':'default','cuboid_count':0})
    with pytest.raises(auth.IdentityError, match='omitted geometry'):
        client.call('/world/cuboids', {'cuboids':[{'name':'cabinet'}]})


def test_current_world_trajectory_accepted(tmp_path):
    result = json.loads((Path(__file__).parent/'fixtures/recorded_v2_micro_joint_plan.json').read_text())
    client = fake_client(tmp_path,result)
    client.worlds = {'default':(result['world_revision'],result['world_sha256'])}
    assert client.call('/plan/joint', {}) == result


def test_remote_client_refuses_local_legacy_import(lock, monkeypatch):
    value, _ = lock
    monkeypatch.setattr(auth.importlib.util,'find_spec',lambda *a:SimpleNamespace(origin='/v1/curobo/__init__.py'))
    with pytest.raises(auth.IdentityError, match='client exposes'):
        auth.verify_client_imports(value)


def test_remote_client_without_local_planner_is_allowed(lock, monkeypatch):
    value, _ = lock
    monkeypatch.setattr(auth.importlib.util,'find_spec',lambda *a:None)
    auth.verify_client_imports(value)


def test_installed_v1_with_missing_import_path_is_still_refused(lock, monkeypatch):
    value, _ = lock
    monkeypatch.setattr(auth.importlib.util, 'find_spec', lambda *a: None)
    def version(name):
        if name == 'nvidia-curobo': return '0.7.6'
        raise auth.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(auth.importlib.metadata, 'version', version)
    with pytest.raises(auth.IdentityError, match='installed legacy'):
        auth.verify_client_imports(value)
