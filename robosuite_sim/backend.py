"""Official-Robosuite environment lifecycle owned by the simulator service."""

from __future__ import annotations

import importlib.metadata
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .tasks import TaskSpec, get_task


EXPECTED_VERSIONS = {
    "robosuite": "1.5.1",
    "mujoco": "3.3.0",
    "numpy": "1.26.4",
}


class DependencyVersionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendConfig:
    task_id: str
    horizon: int = 500
    camera: bool = True
    camera_name: str = "agentview"
    camera_height: int = 256
    camera_width: int = 256
    gl_backend: str = "egl"


def verify_runtime_versions() -> dict[str, str]:
    actual = {name: importlib.metadata.version(name) for name in EXPECTED_VERSIONS}
    mismatches = [
        f"{name}=={actual[name]} (expected {expected})"
        for name, expected in EXPECTED_VERSIONS.items()
        if actual[name] != expected
    ]
    if mismatches:
        raise DependencyVersionError("incompatible simulator versions: " + ", ".join(mismatches))
    return actual


class RobosuiteBackend:
    """One active Robosuite environment; never imported by AttentionHarness."""

    def __init__(self, config: BackendConfig) -> None:
        os.environ.setdefault("MUJOCO_GL", config.gl_backend)
        versions = verify_runtime_versions()

        import robosuite as suite
        from robosuite.controllers import load_composite_controller_config

        module_path = Path(suite.__file__).resolve()
        if "ASPIRE" in module_path.parts or "aspire" in module_path.parts:
            raise RuntimeError(f"refusing non-independent Robosuite import: {module_path}")

        self.config = config
        self.spec: TaskSpec = get_task(config.task_id)
        self._versions = versions
        self._module_path = module_path
        self._seed: int | None = None
        self._last_raw: Mapping[str, Any] | None = None
        self.trace: list[dict[str, Any]] = []
        self._env = suite.make(
            env_name=self.spec.robosuite_env,
            robots="Panda",
            controller_configs=load_composite_controller_config(robot="Panda"),
            has_renderer=False,
            has_offscreen_renderer=config.camera,
            use_camera_obs=config.camera,
            use_object_obs=True,
            camera_names=config.camera_name,
            camera_heights=config.camera_height,
            camera_widths=config.camera_width,
            camera_depths=config.camera,
            horizon=config.horizon,
            ignore_done=True,
            hard_reset=True,
            reward_shaping=False,
        )

    @property
    def action_spec(self) -> tuple[np.ndarray, np.ndarray]:
        low, high = self._env.action_spec
        return np.asarray(low, dtype=np.float64), np.asarray(high, dtype=np.float64)

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "service": "robosuite_sim",
            "service_api_version": "v1",
            "backend_class": f"{type(self._env).__module__}.{type(self._env).__name__}",
            "task_id": self.spec.task_id,
            "robosuite_env": self.spec.robosuite_env,
            "seed": self._seed,
            "control_frame": "robosuite_world",
            "observation_schema": "tidybot.robot-observation.v1",
            "robosuite_origin": str(self._module_path),
            "versions": self._versions,
        }

    def reset(self, seed: int) -> dict[str, np.ndarray]:
        caller_rng_state = np.random.get_state()
        try:
            np.random.seed(seed)
            raw = self._env.reset()
        finally:
            np.random.set_state(caller_rng_state)
        self._seed = seed
        self._last_raw = raw
        self.trace.clear()
        return self._public_observation(raw)

    def step(self, action: np.ndarray) -> tuple[dict[str, np.ndarray], float, bool, dict[str, Any]]:
        low, high = self.action_spec
        array = np.asarray(action, dtype=np.float64)
        if array.shape != low.shape:
            raise ValueError(f"action shape {array.shape} does not match {low.shape}")
        if not np.isfinite(array).all():
            raise ValueError("action must contain only finite values")
        raw, reward, done, info = self._env.step(np.clip(array, low, high))
        self._last_raw = raw
        public = self._public_observation(raw)
        self.trace.append(
            {
                "step": len(self.trace),
                "action": np.clip(array, low, high).tolist(),
                "reward": float(reward),
                "done": bool(done),
            }
        )
        return public, float(reward), bool(done), dict(info)

    def observe(self) -> dict[str, np.ndarray]:
        if self._last_raw is None:
            raise RuntimeError("reset must be called before observe")
        return self._public_observation(self._last_raw)

    def native_success(self) -> bool:
        return bool(self._env._check_success())

    def run_reference(self, timeout_seconds: float) -> dict[str, np.ndarray]:
        from .reference_policy import run_reference_policy

        run_reference_policy(self, timeout_seconds)
        return self.observe()

    def reference_observation(self) -> Mapping[str, Any]:
        return self._env._get_observations()

    def close(self) -> None:
        self._env.close()

    def _public_observation(self, raw: Mapping[str, Any]) -> dict[str, np.ndarray]:
        public = self._filter_observation(raw)
        if not self.config.camera:
            return public

        camera = self.config.camera_name
        depth_key = f"{camera}_depth"
        if depth_key in public:
            public[depth_key] = np.asarray(
                self._metric_depth(self._env.sim, public[depth_key]),
                dtype=np.float32,
            )
        public[f"{camera}_intrinsics"] = np.asarray(
            self._camera_intrinsics(
                self._env.sim,
                camera,
                self.config.camera_height,
                self.config.camera_width,
            ),
            dtype=np.float64,
        )
        public[f"{camera}_pose_mat"] = np.asarray(
            self._camera_pose(self._env.sim, camera),
            dtype=np.float64,
        )
        return public

    @staticmethod
    def _metric_depth(sim: Any, depth: np.ndarray) -> np.ndarray:
        """Convert MuJoCo's normalized depth buffer to meters."""

        normalized = np.asarray(depth, dtype=np.float64)
        if np.any(normalized < 0.0) or np.any(normalized > 1.0):
            raise ValueError("normalized depth must lie in [0, 1]")
        extent = float(sim.model.stat.extent)
        far = float(sim.model.vis.map.zfar) * extent
        near = float(sim.model.vis.map.znear) * extent
        return near / (1.0 - normalized * (1.0 - near / far))

    @staticmethod
    def _camera_intrinsics(
        sim: Any,
        camera_name: str,
        camera_height: int,
        camera_width: int,
    ) -> np.ndarray:
        camera_id = sim.model.camera_name2id(camera_name)
        fovy = float(sim.model.cam_fovy[camera_id])
        focal = 0.5 * camera_height / np.tan(fovy * np.pi / 360.0)
        return np.array(
            [
                [focal, 0.0, camera_width / 2.0],
                [0.0, focal, camera_height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    @staticmethod
    def _camera_pose(sim: Any, camera_name: str) -> np.ndarray:
        """Return OpenCV camera coordinates transformed into simulator world."""

        camera_id = sim.model.camera_name2id(camera_name)
        camera_to_world = np.eye(4, dtype=np.float64)
        camera_to_world[:3, :3] = np.asarray(
            sim.data.cam_xmat[camera_id], dtype=np.float64
        ).reshape(3, 3)
        camera_to_world[:3, 3] = np.asarray(
            sim.data.cam_xpos[camera_id], dtype=np.float64
        )
        axis_correction = np.diag([1.0, -1.0, -1.0, 1.0])
        return camera_to_world @ axis_correction

    @staticmethod
    def _filter_observation(raw: Mapping[str, Any]) -> dict[str, np.ndarray]:
        """Strip privileged task/object state before adding public calibration."""

        public: dict[str, np.ndarray] = {}
        for key, value in raw.items():
            if key.endswith("_image") or key.endswith("_depth") or key.startswith("robot0_"):
                public[key] = np.array(value, copy=True)
        return public
