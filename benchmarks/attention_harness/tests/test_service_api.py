from __future__ import annotations

import threading

import numpy as np

from robosuite_sim.backend import BackendConfig, RobosuiteBackend
from robosuite_sim.client import RobosuiteSimClient
from robosuite_sim.server import SimulatorState, create_server
from robosuite_sim.tasks import get_task


class FakeBackend:
    def __init__(self, config: BackendConfig) -> None:
        self.config = config
        self.spec = get_task(config.task_id)
        self.action_spec = (np.full(7, -1.0), np.full(7, 1.0))
        self.position = np.zeros(3)
        self.trace = []

    @property
    def metadata(self):
        return {"service": "robosuite_sim", "task_id": self.spec.task_id}

    def _observation(self):
        return {"robot0_eef_pos": self.position.copy()}

    def reset(self, _seed):
        self.position[:] = 0
        self.trace.clear()
        return self._observation()

    def step(self, action):
        self.position += np.asarray(action[:3]) * 0.05
        self.trace.append({"step": len(self.trace), "action": np.asarray(action).tolist()})
        return self._observation(), 0.0, False, {}

    def observe(self):
        return self._observation()

    def native_success(self):
        return bool(self.position[2] > 0)

    def run_reference(self, _timeout):
        self.step(np.array([0, 0, 1, 0, 0, 0, -1], dtype=float))
        return self._observation()

    def close(self):
        pass


def test_public_filter_removes_object_oracle() -> None:
    public = RobosuiteBackend._public_observation(
        {
            "agentview_image": np.zeros((2, 2, 3)),
            "robot0_joint_pos": np.zeros(7),
            "cube_pos": np.ones(3),
            "object-state": np.ones(4),
        }
    )
    assert set(public) == {"agentview_image", "robot0_joint_pos"}


def test_http_service_contract_round_trip() -> None:
    state = SimulatorState(backend_factory=FakeBackend)  # type: ignore[arg-type]
    server = create_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = RobosuiteSimClient(f"http://{host}:{port}")
    try:
        assert client.health()["status"] == "ok"
        observation, low, high, metadata = client.reset(
            task_id="cube_lift", seed=101, camera=False
        )
        assert observation["robot0_eef_pos"].tolist() == [0.0, 0.0, 0.0]
        assert low.shape == high.shape == (7,)
        assert metadata["service"] == "robosuite_sim"
        attached, attached_low, attached_high, attached_metadata = client.attach()
        assert attached["robot0_eef_pos"].tolist() == [0.0, 0.0, 0.0]
        assert attached_low.shape == attached_high.shape == (7,)
        assert attached_metadata["task_id"] == "cube_lift"
        client.step(np.array([0, 0, 1, 0, 0, 0, -1], dtype=float))
        assert client.native_success() is True
        reference = client.run_reference(1.0)
        assert reference.trace
        client.close_environment()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
