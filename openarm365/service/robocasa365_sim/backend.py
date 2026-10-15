"""Official RoboCasa365 owner. Task implementations and evaluators stay upstream."""

from __future__ import annotations
import hashlib, importlib.metadata, inspect, json, os, subprocess, time, uuid
from pathlib import Path
import numpy as np
from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view

TASKS = {
    "pick_place_counter_to_sink": "PickPlaceCounterToSink",
    "open_cabinet": "OpenCabinet",
}
FROZEN_CONFIG_DIGESTS = {
    "open_cabinet": "4eb9a00075b2f27e508b53fa4dafc7c2f6ce326baca94f312e55af13222c0c5c",
    "pick_place_counter_to_sink": "66b0d01dd1a0f338c0746aa2277f3bd319428d937e7087543ef3f1274f81914c",
}
VERSIONS = {"robocasa": "1.0.1", "robosuite": "1.5.2"}
INSTALLED_ASSET_MANIFEST_SHA = (
    "6ba5a9d08f036b3a729eb3bc769f7b8ab2714ec1de94c5a9b72dbc1915199a01"
)
COMMITS = {
    "robocasa": "456174f62b89b8fca99eaaf33949c29fec9cfc2a",
    "robosuite": "5ce6643f3092639d08f7b0f90ed1c6a84f50552c",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe(v) for v in value]
    return value


from openarm365.config import validate_config


class OfficialBackend:
    def __init__(self, *, roots, asset_manifest, evidence_dir):
        from curobo_v2.deployment import retired_entry
        retired_entry('RoboCasa365 OpenArm direct backend')
        self.roots = {k: Path(v).resolve() for k, v in roots.items()}
        self.manifest = Path(asset_manifest).resolve()
        self.evidence = Path(evidence_dir)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.env = None
        self.episode = None
        self.receipts = {}
        self.faulted = False
        self.count = 0
        self.obs = {}
        self.identity = self.verify_identity()
        self.journal = self.evidence / "service_journal.jsonl"
        self.recorder = None
        context_path = self.evidence / 'recording_context.json'
        if context_path.is_file():
            from benchmarks.attention_harness.robocasa365.recording import StepRecorder
            self.recorder = StepRecorder(self.evidence, json.loads(context_path.read_text()))

    def log(self, v):
        with self.journal.open("a") as f:
            f.write(
                json.dumps(
                    safe({"timestamp": time.time(), **v}),
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )
            f.flush()
            os.fsync(f.fileno())

    def verify_identity(self):
        identity = {}
        for name, expected in COMMITS.items():
            root = self.roots[name]
            actual = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", str(root), "diff", "--name-only", "HEAD"], text=True
            ).strip()
            if actual != expected or dirty:
                raise RuntimeError(
                    "official source identity/cleanliness mismatch: " + name
                )
            untracked = subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                text=True,
            ).splitlines()
            if any(
                not filename.startswith("robocasa/models/assets/")
                for filename in untracked
            ):
                raise RuntimeError("unexpected untracked official source")
            version = importlib.metadata.version(name)
            if version != VERSIONS[name]:
                raise RuntimeError("official package version mismatch: " + name)
            identity[name] = {"commit": actual, "root": str(root), "version": version}
        import robocasa, robosuite

        for name, module in [("robocasa", robocasa), ("robosuite", robosuite)]:
            if not Path(module.__file__).resolve().is_relative_to(self.roots[name]):
                raise RuntimeError("imported wrong official checkout")
        assets = self.roots["robocasa"] / "robocasa/models/assets"
        manifest = json.loads(self.manifest.read_bytes())
        lock_path = self.manifest.with_name("assets-installation-lock.json")
        if not lock_path.is_file():
            raise RuntimeError("official missing-dependency installation lock required")
        lock = json.loads(lock_path.read_bytes())
        registry = assets / "box_links/box_links_assets.json"
        installed_path = self.manifest.with_name("assets-installed-manifest.json")
        if (
            lock["official_commit"] != COMMITS["robocasa"]
            or lock["base_manifest_sha256"]
            != hashlib.sha256(self.manifest.read_bytes()).hexdigest()
            or lock["asset_registry_sha256"]
            != hashlib.sha256(registry.read_bytes()).hexdigest()
            or lock["installed_manifest_sha256"]
            != hashlib.sha256(installed_path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("official asset installation identity mismatch")
        if lock["installed_manifest_sha256"] != INSTALLED_ASSET_MANIFEST_SHA:
            raise RuntimeError("unreviewed installed asset manifest")
        installed = json.loads(installed_path.read_bytes())
        if any(installed.get(name) != sha for name, sha in manifest.items()):
            raise RuntimeError("asset installation changed frozen baseline")
        manifest = installed
        actual_files = {
            str(p.relative_to(assets))
            for p in assets.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
        if actual_files != set(manifest):
            raise RuntimeError("official asset file set differs from installed lock")
        for name, sha in manifest.items():
            p = assets / name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise RuntimeError("asset mismatch: " + name)
        identity["assets"] = {
            "manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            "files_verified": len(manifest),
            "root": str(assets),
            "installed_manifest_sha256": lock["installed_manifest_sha256"],
            "installation_lock_sha256": hashlib.sha256(
                lock_path.read_bytes()
            ).hexdigest(),
            "packs": lock["packs"],
        }
        identity["runtime"] = {
            k: importlib.metadata.version(k)
            for k in ("numpy", "numba", "scipy", "mujoco")
        }
        expected_runtime = {
            "numpy": "2.2.5",
            "numba": "0.61.2",
            "scipy": "1.15.3",
            "mujoco": "3.3.1",
        }
        if identity["runtime"] != expected_runtime:
            raise RuntimeError("frozen runtime version mismatch")
        identity["service"] = {
            "version": importlib.metadata.version("robocasa365_sim"),
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(__file__).parent.glob("*.py"))
            },
        }
        return identity

    def reset(self, c):
        validate_config(c)
        if os.environ.get('PYTHONHASHSEED')!=str(c['python_hash_seed']):
            raise ValueError('OpenArm365 requires its pinned deterministic Python launcher')
        if c["asset_manifest_sha256"] != self.identity["assets"]["manifest_sha256"]:
            raise ValueError("asset freeze mismatch")
        self.stop()
        from openarm365.environment import create_env

        self.config = c
        self.env = create_env(
            c["official_env"],
            robots=c["robot"],
            seed=c["seed"],
            split=None,
            obj_instance_split=c["obj_instance_split"],
            layout_and_style_ids=[(c["layout_id"], c["style_id"])],
            camera_names=c["camera_names"],
            camera_widths=c["camera_width"],
            camera_heights=c["camera_height"],
            randomize_cameras=False,
            generative_textures=None,
            control_freq=c["control_freq"],
            horizon=c["horizon"],
        )
        self.obs = self.env.reset()
        from openarm365.environment import validate_physical_model
        validate_physical_model(self.env)
        self.episode = uuid.uuid4().hex
        self.count = 0
        self.receipts = {}
        self.faulted = False
        self.comp = self.env.robots[0].composite_controller
        self.parts = {k: tuple(v) for k, v in self.comp._action_split_indexes.items()}
        if (
            set(self.parts) != {"right", "right_gripper", "left", "left_gripper", "base"}
            or self.parts["right"][1] - self.parts["right"][0] != 6
        ):
            raise RuntimeError("unexpected official composite action layout")
        from openarm365.controller import install_pose_hold
        if c['task_id']=='open_cabinet': install_pose_hold(self.comp.part_controllers['right'])
        from openarm365.environment import configure_display_camera
        configure_display_camera(self.env)
        self.obs = self.env._get_observations(force_update=True)
        if self.recorder is not None:
            capture_human_view(self.recorder, self.env, self.episode, self.count)
        evaluator = inspect.getsourcefile(self.env._check_success)
        self.log(
            {
                "event": "reset",
                "episode_id": self.episode,
                "task_id": c["task_id"],
                "config_sha256": digest(c),
                "capabilities": self.capabilities(),
                "evaluator_file": evaluator,
                "evaluator_sha256": hashlib.sha256(
                    Path(evaluator).read_bytes()
                ).hexdigest(),
            }
        )
        return {
            "episode_id": self.episode,
            "identity": self.identity,
            "capabilities": self.capabilities(),
            "observation": self.observation(),
        }

    def capabilities(self):
        result = {
            "schema_version": "robocasa365.capabilities.v1",
            "robot": "OpenArm365",
            "execution_arm": "left",
            "execution_part": "right",
            "camera_names": self.config["camera_names"],
            "native_arm_mapping": {"right":"physical OpenArm left", "left":"physical OpenArm right (held)"},
            "controller": "BASIC",
            "neutral_arm_target": "last desired base-relative pose for cabinet; achieved pose for counter task",
            "supported_tasks": TASKS,
            "unsupported": [
                "absolute_cartesian",
                "IK",
                "planning",
                "dual_arm",
                "navigation",
                "real_robot",
            ],
            "identity": self.identity,
            "model_identity": __import__("openarm365.environment",fromlist=["model_identity"]).model_identity(),
            "public_observation_source": "sim_gt / official observations; RGB and robot proprioception",
            "arm_frame": "robot base frame",
            "arm_units": ["metres", "axis-angle radians"],
            "base_native_units": ["metres/second", "metres/second", "radians/second"],
            
            "base_input": "normalized current-base-frame planar velocity command; official JOINT_VELOCITY uses action directly after yaw transformation",
            "torso_input": "normalized JOINT_POSITION delta",
            "mode": {"arm": -1, "base_tracking": 1},
            "formal_eligible": False,
        }
        if self.env is not None:
            ctrls = {}
            for name, ctrl in self.comp.part_controllers.items():
                ctrls[name] = {
                    key: safe(getattr(ctrl, key, None))
                    for key in [
                        "input_min",
                        "input_max",
                        "output_min",
                        "output_max",
                        "input_ref_frame",
                        "input_type",
                        "control_dim",
                    ]
                }
                ctrls[name]["type"] = type(ctrl).__name__
                ctrls[name]["command_dim"] = self.parts[name][1] - self.parts[name][0]
            result.update(
                part_indexes=safe(self.parts),
                controllers=ctrls,
                action_dim=len(self.env.action_spec[0]),
                action_low=safe(self.env.action_spec[0]),
                action_high=safe(self.env.action_spec[1]),
            )
        return result

    def observation(self):
        if self.env is None:
            raise RuntimeError("no live episode")
        robot = {
            k: safe(v)
            for k, v in self.obs.items()
            if k.startswith("robot0_") and not k.endswith("_image")
        }
        robot={k.replace("_right_","_physical_execution_").replace("_left_","_right_").replace("_physical_execution_","_left_"):v for k,v in robot.items()}
        images = {k: safe(v) for k, v in self.obs.items() if k.endswith("_image")}
        object_keys = {
            name + suffix
            for name in getattr(self.env, "obj_body_id", {})
            for suffix in ("_pos", "_quat")
        }
        objects = {k: safe(v) for k, v in self.obs.items() if k in object_keys}
        from openarm365.environment import task_geometry
        objects.update(task_geometry(self.env,self.config))
        return {
            "schema_version": "robocasa365.public-observation.v1",
            "episode_id": self.episode,
            "sequence": self.count,
            "robot": robot,
            "images": images,
            "perception": {
                "mode": "sim_gt",
                "source": "official simulator object observation",
                "values": objects,
            },
            "units": {"position": "metres", "rotation": "quaternion xyzw"},
            "frame": "world for eef/base/object poses; base_to_eef keys are robot-base-relative; joints in their native coordinates",
            "frames": {
                "robot0_eef_pos": "world",
                "robot0_base_pos": "world",
                "robot0_base_to_eef_pos": "current_robot_base",
                "perception.values": "world",
            },
            "proprioception_units": {
                "robot0_joint_pos": "radians",
                "robot0_gripper_qpos": "metres",
                "robot0_base_pos": "metres",
                "robot0_base_quat": "quaternion xyzw",
            },
        }

    def step(self, request_id, episode_id, parts, steps=1):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("request_id required")
        if self.env is None or episode_id != self.episode:
            raise ValueError("episode identity mismatch")
        if type(steps) is not int or not 1 <= steps <= 10:
            raise ValueError("steps outside bounded call")
        if not isinstance(parts, dict) or set(parts) - set(self.parts) - {"base_mode"}:
            raise ValueError("unknown action part")
        if type(parts.get("base_mode", -1)) not in (int, float) or parts.get(
            "base_mode", -1
        ) not in (-1, 1):
            raise ValueError("invalid base_mode")
        data = {"episode_id": episode_id, "parts": parts, "steps": steps}
        sha = digest(data)
        if request_id in self.receipts:
            old = self.receipts[request_id]
            if old["request_sha256"] != sha:
                raise ValueError("conflicting duplicate request")
            if old["status"] != "completed":
                raise RuntimeError("action outcome unknown; stopped; do not retry")
            return old
        if self.faulted:
            raise RuntimeError("episode faulted; stopped")
        for name, value in parts.items():
            if name == "base_mode":
                continue
            dim = self.parts[name][1] - self.parts[name][0]
            if not isinstance(value, (list, tuple)) or any(
                type(x) not in (int, float) for x in value
            ):
                raise ValueError("action requires numeric values: " + name)
            a = np.asarray(value, dtype=float)
            if a.shape != (dim,) or not np.isfinite(a).all() or np.any(np.abs(a) > 1):
                raise ValueError("invalid normalized part: " + name)
        if self.count + steps > self.config["horizon"]:
            raise RuntimeError("control step budget exhausted")
        action = self.comp.create_action_vector(
            {
                k: np.asarray(v, dtype=float) if k != "base_mode" else v
                for k, v in parts.items()
            }
        )
        if (
            not np.isfinite(action).all()
            or np.any(action < self.env.action_spec[0])
            or np.any(action > self.env.action_spec[1])
        ):
            raise ValueError("action_spec violation")
        pending = {
            "request_id": request_id,
            "episode_id": episode_id,
            "request_sha256": sha,
            "status": "pending",
            "sequence_before": self.count,
            "action": safe(action),
            "parts": parts,
            "steps": steps,
        }
        self.receipts[request_id] = pending
        self.log({"event": "action_intent", **pending})
        try:
            for _ in range(steps):
                obs, _reward, _done, _info = self.env.step(action.copy())
                self.obs = obs
                self.count += 1
                from openarm365.environment import physics_sample
                self.log({"event":"physics_sample", "sequence":self.count, **physics_sample(self.env)})
                if self.recorder is not None:
                    capture_human_view(self.recorder, self.env, self.episode, self.count)
            receipt = {
                **pending,
                "status": "completed",
                "sequence_after": self.count,
                "observation_sha256": digest(self.observation()),
            }
            self.log({"event": "action_receipt", **receipt})
            self.receipts[request_id] = receipt
            return receipt
        except BaseException as exc:
            self.faulted = True
            self.receipts[request_id] = {
                **pending,
                "status": "unknown",
                "sequence_after": self.count,
            }
            self.log(
                {
                    "event": "action_unknown",
                    "request_id": request_id,
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
            self.stop()
            raise

    def native_success(self):
        if self.env is None or self.faulted:
            raise RuntimeError("native evaluator unavailable")
        result = {
            "native_success": bool(self.env._check_success()),
            "evaluated": True,
            "source": "official env._check_success",
            "episode_id": self.episode,
            "sequence": self.count,
        }
        self.log({"event": "native_evaluator", **result})
        return result

    def stop(self):
        if self.env is not None:
            try:
                self.env.close()
            finally:
                self.env = None
        self.obs = {}
        if hasattr(self, "journal"):
            self.log({"event": "stop", "episode_id": self.episode, "closed": True})
        return {"closed": True, "episode_id": self.episode, "sequence": self.count}
