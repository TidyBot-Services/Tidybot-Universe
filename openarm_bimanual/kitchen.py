"""Native kitchen and OpenArm share one CPU PhysX scene.

Dependencies are imported only when a real environment is requested. The
original task reset owns initial object placement; execution only steps physics.
"""
from pathlib import Path
import importlib.util
import json
import hashlib
from functools import lru_cache

ROOT = Path(__file__).resolve().parent.parent
from .model import build_model
MODEL = Path(build_model()["path"])
PARAMETERS = ROOT / "openarm_bimanual/configs/robot_parameters.json"


def load_parameters(path=PARAMETERS):
    path = Path(path).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != path.with_suffix(".sha256").read_text().strip():
        raise ValueError("OpenArm robot parameters changed from pinned hash")
    return json.loads(path.read_text()), digest


@lru_cache(maxsize=1)
def accepted_module():
    spec = importlib.util.spec_from_file_location(
        "openarm_accepted_service", ROOT / "openarm_phase1/service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify_freeze()
    return module


def kitchen_rig(scene, mount_pose, *, tendon_damping=0):
    """Reuse accepted stepping/limits, loading into the kitchen's own scene."""
    import numpy as np
    import xml.etree.ElementTree as ET
    accepted = accepted_module()
    sim = accepted.sim

    class KitchenRig(sim.Rig):
        def __init__(self):
            self.urdf = MODEL
            self.xml = ET.parse(MODEL).getroot()
            self.scene = scene
            p = sim.CFG["physics"]
            scene.set_timestep(p["dt"])
            loader = scene.create_urdf_loader()
            loader.fix_root_link = True
            self.builder = loader.load_file_as_articulation_builder(str(MODEL))
            self.extra_pairs = [sorted(pair) for pair in loader.ignore_pairs]
            lock = json.loads((ROOT / "openarm_phase0_recheck/model/compat_v6/implementation_lock.json").read_text())
            expected = sorted(sorted(x["links"]) for x in lock["extra_disabled_pairs"])
            if sorted(self.extra_pairs) != expected:
                raise ValueError("OpenArm SRDF exceptions differ from accepted model")
            self.mimics = self.builder.mimic_joint_records.copy()
            self.robot = self.builder.build(fix_root_link=True, build_mimic_joints=tendon_damping == 0)
            if tendon_damping != 0:
                from openarm_robocasa.mimic import create_native_mimics
                create_native_mimics(self.robot, self.mimics, damping=tendon_damping)
            self.robot.set_solver_position_iterations(p["solver_position_iterations"])
            self.robot.set_solver_velocity_iterations(p["solver_velocity_iterations"])
            self.joints = self.robot.get_active_joints()
            self.names = [j.name for j in self.joints]
            self.idx = {name: i for i, name in enumerate(self.names)}
            self.arm = np.array([i for i, n in enumerate(self.names) if "finger" not in n and not n.startswith("base_")], int)
            self.finger = np.array([i for i, n in enumerate(self.names) if "finger" in n], int)
            self.qlimits = np.array(self.robot.get_qlimits(), float)
            self.jxml = {j.get("name"): j for j in self.xml.findall("joint")}
            c = sim.CFG["controller"]
            for joint in self.joints:
                node = self.jxml[joint.name]
                follower = node.find("mimic") is not None
                finger = "finger" in joint.name
                joint.max_velocity = float(node.find("limit").get("velocity"))
                joint.set_drive_properties(
                    0 if follower else c["finger_stiffness" if finger else "arm_stiffness"],
                    0 if follower else c["finger_damping" if finger else "arm_damping"],
                    float(node.find("limit").get("effort")), mode=c["mode"])
            self.goal = self.make_q(sim.CFG["initial"])
            self.command = self.goal.copy()
            self.robot.set_root_pose(mount_pose)
            self.base = np.array([self.idx[n] for n in ["base_x_joint", "base_y_joint", "base_yaw_joint"]])
            for i in self.base:
                self.joints[i].set_drive_properties(100000, 5000, 600, mode=c["mode"])
            self.robot.set_qpos(self.goal)
            self.robot.set_qvel(np.zeros(self.robot.dof))
            self.apply_drive()

        def close(self):
            # The environment owns the shared scene and releases it once.
            pass

    return KitchenRig()


class OpenArmKitchen:
    robot_model = "OpenArm"

    def __init__(self, task_id="counter_to_sink", seed=101, execution_arm="both", mount=None,
                 parameters_path=PARAMETERS):
        from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
        from benchmarks.attention_harness.robocasa_native.tasks import get_robocasa_task
        import numpy as np
        import sapien
        import mani_skill.envs  # noqa: F401
        import robocasa_tasks  # noqa: F401
        from mani_skill.utils.registration import REGISTERED_ENVS
        self.selection = RobotSelection("OpenArm", execution_arm)
        self.spec = get_robocasa_task(task_id)
        self.seed = seed
        parameters, self.parameters_sha256 = load_parameters(parameters_path)
        self.parameters = parameters
        accepted_module()
        task_class = REGISTERED_ENVS[self.spec.environment_id].cls

        class KitchenScene(task_class):
            def get_state_dict(self):
                # ManiSkill b22's robot_uids=none path still asks a missing
                # agent for controller state. Only fix that preload path.
                if self.agent is None:
                    return self.scene.get_sim_state()
                return super().get_state_dict()

        self.env = KitchenScene(num_envs=1, robot_uids="none",
                            obs_mode="none", sim_backend="cpu", render_backend="cpu",
                            sim_config={"sim_freq": 240, "control_freq": 20, "scene_config": {
                                "solver_position_iterations": 32, "solver_velocity_iterations": 8,
                                "enable_enhanced_determinism": True, "cpu_workers": 1}})
        self.env.reset(seed=seed, options={"reconfigure": True})
        self.native = self.env.unwrapped
        self.native_task_class = task_class
        self.scene = self.native.scene.sub_scenes[0]
        destination = getattr(self.native, self.spec.destination_debug_key)
        if mount is None:
            # Fixed mount relative to the task fixture; audited before promotion.
            from transforms3d.euler import euler2quat
            offset = np.array(parameters["mount_offset_fixture_m"])
            from scipy.spatial.transform import Rotation
            xyz = np.array(destination.pos) + Rotation.from_euler("z", destination.rot).apply(offset)
            xyz[2] = 0.0
            mount = [*xyz, *euler2quat(0, 0, destination.rot + parameters["mount_yaw_offset_rad"])]
        if len(mount) != 7 or not np.isfinite(mount).all():
            raise ValueError("mount requires finite world xyz and wxyz quaternion")
        self.mount = list(mount)
        self.rig = kitchen_rig(self.scene, sapien.Pose(mount[:3], mount[3:]),
                               tendon_damping=parameters.get("mimic_tendon_damping", 0))
        position_iterations = parameters.get("solver_position_iterations", 32)
        velocity_iterations = parameters.get("solver_velocity_iterations", 8)
        if type(position_iterations) is not int or position_iterations < 32 or type(velocity_iterations) is not int or velocity_iterations < 8:
            raise ValueError("OpenArm kitchen solver precision cannot be below accepted baseline")
        self.rig.robot.set_solver_position_iterations(position_iterations)
        self.rig.robot.set_solver_velocity_iterations(velocity_iterations)
        self.rig.goal = self.rig.make_q(parameters["initial"])
        self.rig.command = self.rig.goal.copy()
        self.rig.robot.set_qpos(self.rig.goal)
        self.rig.robot.set_qvel(np.zeros(self.rig.robot.dof))
        self.rig.apply_drive()
        self.target_entity = self.native.object_actors[0]["obj"]["actor"]._objs[0]
        self.steps = 0
        from openarm_robocasa.variation import variation_identity
        objects = [{"name": name, **dict(zip(["x", "y", "z"], pose["position"]))}
                   for name, pose in self.object_state().items()]
        scene_index = getattr(self.native, "_scene_idx_to_be_loaded", 0)
        self.variation = variation_identity(self.native.get_ep_meta(), objects,
            fixture_configs=self.native.scene_builder.scene_data[scene_index].get("fixture_cfgs"))

    def object_state(self):
        return {name: {"position": row["actor"].pose.p[0].cpu().numpy().tolist(),
                       "quaternion_wxyz": row["actor"].pose.q[0].cpu().numpy().tolist()}
                for name, row in self.native.object_actors[0].items()}

    def native_evaluate(self):
        result = self.native.evaluate()
        if "error" in result:
            raise RuntimeError("native evaluator failed: " + str(result["error"]))
        return {"native_success": bool(result["success"][0].item()),
                "source": self.native_task_class.__module__ + "." + self.native_task_class.__name__ + ".evaluate",
                "physical_steps": self.steps}

    def step(self):
        self.rig.step()
        self.steps += 1

    def reset_same_scene(self):
        import numpy as np
        self.rig.goal = self.rig.make_q(self.parameters["initial"])
        self.rig.command = self.rig.goal.copy()
        self.rig.robot.set_qpos(self.rig.goal)
        self.rig.robot.set_qvel(np.zeros(self.rig.robot.dof))
        self.rig.apply_drive()
        # Native reset owns initial kitchen/object placement. Reuse its fixed
        # scene instead of rebuilding render meshes inside an HTTP request.
        self.env.reset(seed=self.seed, options={"reconfigure": False})
        self.steps = 0

    def close(self):
        self.env.close()
