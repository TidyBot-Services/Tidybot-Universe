from __future__ import annotations

from benchmarks.attention_harness import external_robosuite_service as module


def test_external_launcher_excludes_universe_source_from_child_imports(tmp_path, monkeypatch):
    launched = []

    class Process:
        returncode = None

        def __init__(self, argv, **kwargs):
            launched.append((argv, kwargs))

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return 0

    class Client:
        def __init__(self, url, *, timeout):
            assert url.startswith("http://127.0.0.1:")
            assert timeout == 1.0

        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(module.importlib.metadata, "version", lambda name: "1.0.0")
    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module, "RobosuiteSimClient", Client)
    with module.ManagedExternalRobosuiteService(log_path=tmp_path / "service.log") as url:
        assert url.startswith("http://127.0.0.1:")
    assert launched[0][0][1:4] == ["-I", "-m", "robosuite_sim"]
