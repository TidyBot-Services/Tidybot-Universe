"""Named-joint cuRoboV2 planning with immutable worlds and dense validation."""
from __future__ import annotations

from curobo_v2.identity import IdentityError, require_service_context
_service_lock, _service_robot_config = require_service_context()

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET

import numpy as np
import torch
import yaml


class CuroboPlanner:
    """Return validated trajectories; this class never drives a simulator."""

    def __init__(self, device: str, robot_cfg: str,
                 assets_dir: str, use_cuda_graph: bool):
        if (device != "cuda:0" or robot_cfg != _service_robot_config
                or not Path(robot_cfg).is_absolute()
                or Path(assets_dir).resolve() != Path(_service_lock["assets_root"]).resolve()):
            raise IdentityError("Planner parameters differ from the authenticated service configuration")
        self._device = torch.device(device)
        self.robot_cfg = robot_cfg
        self.assets_dir = Path(assets_dir)
        self.use_cuda_graph = use_cuda_graph
        self._models = {}
        self._measured_inputs = None
        self._warmed_up = False
        self._world_cuboids = []
        self.attachment = None
        self._attachment_context = None
        self._attachment_key = None
        self.last_validation = None
        self.last_failure = None
        self.last_solver_diagnostics = []
        self.interpolation_dt = .025
        self.max_joint_step = .005
        self.last_timing = None
        self.last_derivatives = None
        self._load_robot()
        self.geometry = None
        if self.settings.get("exact_geometry"):
            from .geometry import ExactGeometryValidator
            self.geometry = ExactGeometryValidator(self.settings["exact_geometry"],self.joint_names)

    def _load_robot(self) -> None:
        from curobo._src.util_file import get_robot_configs_path, get_assets_path
        path = Path(self.robot_cfg).resolve()
        if path is None:
            raise ValueError(f"Robot configuration not found: {self.robot_cfg}")
        document = yaml.safe_load(path.read_text())
        self._config = document.get("robot_cfg", document)
        self.settings = document.get("service", {})
        if 'use_cuda_graph' in self.settings:
            if type(self.settings['use_cuda_graph']) is not bool:raise IdentityError('CUDA graph policy must be explicit bool')
            self.use_cuda_graph=self.settings['use_cuda_graph']
        kin = self._config["kinematics"]
        for key in ("urdf_path", "asset_root_path"):
            if not kin.get(key):
                continue
            options = [path.parent / kin[key], self.assets_dir / kin[key],
                       Path(get_assets_path()) / kin[key]]
            resolved = next((p.resolve() for p in options if p.exists()), None)
            if resolved is None:
                raise ValueError(f"Robot asset not found: {kin[key]}")
            if resolved.is_file() and str(resolved) not in _service_lock['files']:
                raise IdentityError("Robot asset is outside the runtime lock: " + str(resolved))
            kin[key] = str(resolved)
        if isinstance(kin.get("collision_spheres"), str):
            options = [path.parent / kin["collision_spheres"],
                       self.assets_dir / kin["collision_spheres"],
                       Path(get_robot_configs_path()) / kin["collision_spheres"]]
            spheres = next((p for p in options if p.is_file()), None)
            if spheres is None:
                raise ValueError("Collision sphere file missing")
            if str(spheres.resolve()) not in _service_lock['files']:
                raise IdentityError("Collision spheres are outside the runtime lock")
            kin["collision_spheres"] = yaml.safe_load(spheres.read_text())["collision_spheres"]
        xml = ET.parse(kin["urdf_path"]).getroot()
        self._joints = {j.get("name"): j for j in xml.findall("joint")}
        self._locks = dict(kin.get("lock_joints", {}))
        self.joint_names = self.settings.get("joint_names") or [
            name for name in kin["cspace"]["joint_names"]
            if name not in self._locks and self._joints[name].find("mimic") is None]
        if len(set(self.joint_names)) != len(self.joint_names):
            raise ValueError("Duplicate robot joint names")
        self.limits = np.array([[float(self._joints[n].find("limit").get(k))
                                for k in ("lower", "upper")] for n in self.joint_names])
        self.default_q = np.array([dict(zip(kin["cspace"]["joint_names"],
                                            kin["cspace"]["default_joint_position"]))[n]
                                   for n in self.joint_names])
        self.tool_frames = list(kin["tool_frames"])
        self.default_quaternion = self.settings.get("default_quaternion_wxyz", [0., 1., 0., 0.])
        self.identity = {"robot_config": str(path), "robot_config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "urdf_sha256": hashlib.sha256(Path(kin["urdf_path"]).read_bytes()).hexdigest(),
                         "joint_names": self.joint_names, "tool_frames": self.tool_frames,
                         "cuda_graph": self.use_cuda_graph,
                         "pose_solver": {"implementation":"unmodified NVIDIA public MotionPlanner API", "max_attempts":1,"public_strategy":self.settings.get("pose_search","official_defaults")},
                         "contract": {"frame":kin["base_link"], "quaternion":"wxyz", "length_unit":"m",
                             "joint_units":{n:"m" if self._joints[n].get("type")=="prismatic" else "rad" for n in self.joint_names},
                             "limits":self.limits.tolist(),"attachments":"explicit measured single-hand shapes or cooperative cuboid; full geometry and closure veto required",
                             "self_collision":True,"clearance_m":.005,"position_tolerance_m":.004,"orientation_tolerance_rad":.03}}
        self.identity['contract'].update(command_curve='C2 quintic Hermite through public q/v/a at unchanged public timestamps',
            continuous_geometry='required conservative full-URDF/world/rigid-attachment interval certificate; otherwise no trajectory',
            continuous_certificate_budget_seconds=2.)


    def warmup(self) -> None:
        if not self._warmed_up:
            self._select(self.default_q)
            self._warmed_up = True

    def named(self, q: list | np.ndarray, names: list[str] | None = None) -> np.ndarray:
        names = self.joint_names if names is None else names
        if any(isinstance(v,(bool,np.bool_)) for v in q):
            raise ValueError("Boolean joint positions are invalid")
        values = np.asarray(q, dtype=np.float64)
        if (len(names) != len(set(names)) or set(names) != set(self.joint_names)
                or values.shape != (len(names),) or not np.isfinite(values).all()):
            raise ValueError("Exactly one finite value for every configured joint is required")
        result = values[[names.index(n) for n in self.joint_names]]
        if np.any(result < self.limits[:, 0]) or np.any(result > self.limits[:, 1]):
            raise ValueError("Joint position outside URDF limits")
        return result

    def reorder(self, values: np.ndarray, names: list[str] | None = None) -> np.ndarray:
        names = names or self.joint_names
        return values[..., [self.joint_names.index(n) for n in names]]

    def _scene(self):
        from curobo.scene import Scene, Cuboid
        attached=set() if self.attachment is None else self.attachment.names
        return Scene(cuboid=[Cuboid(name=c["name"], pose=[*c["center"], *c.get("quat_wxyz", [1, 0, 0, 0])],
                                   dims=(2 * np.array(c["half_size"])).tolist()) for c in self._world_cuboids if c['name']not in attached])

    def set_collision_world(self, cuboids: list[dict], robot_pos=None) -> None:
        # No target, overlap, distance, or failure-dependent obstacle filtering.
        if len(cuboids) > 4096:
            raise ValueError("Complete world exceeds declared 4096-shape capacity; no geometry may be dropped")
        for c in cuboids:
            for key in ("center", "half_size"):
                a = np.asarray(c[key], float)
                if a.shape != (3,) or not np.isfinite(a).all() or (key == "half_size" and np.any(a <= 0)):
                    raise ValueError("Invalid cuboid geometry")
            quat = np.asarray(c.get("quat_wxyz", [1, 0, 0, 0]), float)
            if quat.shape != (4,) or not np.isfinite(quat).all() or abs(np.linalg.norm(quat) - 1) > 1e-5:
                raise ValueError("Cuboid requires a unit wxyz quaternion")
        if len({c["name"] for c in cuboids}) != len(cuboids):
            raise ValueError("Duplicate obstacle names")
        if robot_pos is not None:
            raise ValueError("robot_pos is ambiguous: all world geometry and targets must use the declared robot base frame")
        self._world_cuboids = deepcopy(cuboids)
        if self.geometry is not None:
            self.geometry.update_world(cuboids)
        for key in list(self._models):
            if key[2] < len(cuboids):
                del self._models[key]
        # The request's freshly validated attachment is selected after world
        # upload under PlannerAdapter's lock. Update the selected model there;
        # a temporary no-attachment upload must not evict carried topology.

    def set_attachment(self, context):
        attachment=None
        if context is not None:
            if not isinstance(context,dict)or self.geometry is None:
                raise ValueError('Measured attachment requires full exact-geometry validation')
            if 'holder'in context:
                from curobo_v2.attachment import SingleArmAttachment
                attachment=SingleArmAttachment(context,self._world_cuboids,self.tool_frames)
            elif 'object_name'in context:
                from .cooperative import CarriedCuboid
                attachment=CarriedCuboid(context,self._world_cuboids,self.tool_frames)
            else:raise ValueError('Complete explicit measured attachment context required')
        self.attachment=attachment
        self._attachment_context=deepcopy(context)
        from .attachment_config import carried_model_topology
        self._attachment_key=carried_model_topology(attachment)
        if self.geometry is not None:
            self.geometry.attachment=attachment
            self.geometry.update_world(self._world_cuboids)

    def _select(self, q: np.ndarray, active_joints: list[str] | None = None,
                tool_frames: list[str] | None = None, locked_joints: dict | None = None):
        active, tools, locks = self.check_selection(q, active_joints, tool_frames, locked_joints)
        from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
        from curobo.collision_checking import RobotCollisionChecker, RobotCollisionCheckerCfg
        from curobo.types import DeviceCfg
        capacity = max(256, 1 << max(0, len(self._world_cuboids) - 1).bit_length())
        search=self.settings.get('pose_search')
        # Official Kinematics.update_kinematics_config explicitly supports
        # measured locked-joint updates when tensor dimensions stay unchanged.
        # Keep topology changes separate; never round measured lock values.
        key = (tuple(sorted(locks)) if search is not None else tuple(sorted(locks.items())),
               tools, capacity, self._attachment_key)
        carried_supports = self.attachment is not None and bool(self.attachment.supports)
        if carried_supports:
            declared_names = {c['name'] for c in self._world_cuboids}
            if (not self.attachment.names <= declared_names
                    or not self.attachment.supports <= declared_names - self.attachment.names):
                raise ValueError('Carried object/support names absent from complete world')
            key += (True,)
        from .attachment_config import configure_carried_body
        robot = configure_carried_body(self._config,self.attachment)
        robot['kinematics']['lock_joints']=locks
        robot['kinematics']['tool_frames']=list(tools)
        if search is not None:
            if search!='measured_reference_high_penalty_v1':raise IdentityError('Undeclared V2 public optimization strategy')
            measured_reference={**dict(zip(self.joint_names,q)),**locks}
            reference=robot['kinematics']['cspace']['joint_names']
            robot['kinematics']['cspace']['default_joint_position']=[measured_reference[n] for n in reference]
        if key not in self._models:
            logging.warning("V2 adapter model cache miss: active=%s tools=%s",active,tools)
            # Calls are serialized; keep immutable model instances bounded.
            while len(self._models)>=2:self._models.pop(next(iter(self._models)))
            optimizer_options={}
            if search is not None:
                if search!='measured_reference_high_penalty_v1':
                    raise IdentityError('Undeclared V2 public optimization strategy')
                # Public task configuration only; the official implementation,
                # hard URDF limits, goal and acceptance tolerances are unchanged.
                task=Path(_service_lock['library_root'])/'curobo/content/configs/task/ik/lbfgs_ik.yml'
                ik=yaml.safe_load(task.read_text())
                ik['rollout']['cost_cfg']['cspace_cfg']['activation_distance'][0]=.00001
                ik['rollout']['cost_cfg']['cspace_cfg']['weight'][0]=100000000.
                optimizer_options['ik_optimizer_configs']=[ik]
                task=Path(_service_lock['library_root'])/'curobo/content/configs/task/trajopt/lbfgs_bspline_trajopt.yml'
                traj=yaml.safe_load(task.read_text())
                traj['rollout']['cost_cfg']['cspace_cfg']['activation_distance'][0]=.00001
                traj['rollout']['cost_cfg']['cspace_cfg']['weight'][0]=100000000.
                optimizer_options['trajopt_optimizer_configs']=[traj]
            device = DeviceCfg(device=self._device)
            cfg = MotionPlannerCfg.create(robot=robot, scene_model=self._scene(), device_cfg=device,
                collision_cache={"cuboid": capacity, "mesh": 32}, use_cuda_graph=self.use_cuda_graph,
                self_collision_check=True, position_tolerance=.004, orientation_tolerance=.03,
                num_ik_seeds=64 if search is not None else 16, num_trajopt_seeds=1,
                interpolation_dt=self.interpolation_dt, **optimizer_options, optimizer_collision_activation_distance=float(self.settings.get("search_activation_distance_m",.02)))
            if search is not None:cfg.ik_solver_config.use_lm_seed=False
            if carried_supports:
                from .contact_scene import CarriedMotionPlanner
                planner = CarriedMotionPlanner(cfg, self.attachment, self._scene())
            else:
                planner = MotionPlanner(cfg)
            actual_limits = planner.kinematics.get_joint_limits().position.detach().cpu().numpy().T
            expected_limits = self.limits[[self.joint_names.index(n) for n in planner.joint_names]]
            if not np.allclose(actual_limits,expected_limits,rtol=0.,atol=1e-6):
                raise IdentityError('V2 active joint bounds differ from the original URDF')
            planner.warmup(num_warmup_iterations=1,enable_graph=search is None)
            checker_options = {}
            if carried_supports:
                from curobo._src.geom.collision.collision_scene import SceneCollisionCfg
                from .contact_scene import CarriedSceneCollision, configure_scene
                checker_scene = CarriedSceneCollision.from_config(SceneCollisionCfg(
                    device_cfg=device, scene_model=self._scene(), max_distance=1.,
                    cache={'cuboid': capacity, 'mesh': 50}))
                configure_scene(checker_scene, planner.kinematics, self.attachment,
                                {'cuboid': capacity, 'mesh': 50}, self._scene())
                checker_options['scene_collision_checker'] = checker_scene
            checker = RobotCollisionChecker(RobotCollisionCheckerCfg.load_from_config(
                robot_config=cfg.ik_solver_config.robot_config, scene_model=self._scene(), device_cfg=device,
                collision_activation_distance=.005, self_collision_activation_distance=0.,
                n_cuboids=capacity, **checker_options))
            if carried_supports:
                configure_scene(checker.scene_model, checker.kinematics, self.attachment,
                                {'cuboid': capacity, 'mesh': 50}, self._scene())
            self._models[key] = (planner, checker, robot, locks)
        planner, checker, previous_robot, old_locks = self._models[key]
        if robot!=previous_robot:
            self._refresh_measured_model(planner,checker,robot)
            self._models[key]=(planner,checker,robot,dict(locks))
        if carried_supports:
            from .contact_scene import configure_scene
            configure_scene(planner.scene_collision_checker, planner.kinematics,
                            self.attachment, planner.config.scene_collision_cfg.cache, self._scene())
            configure_scene(checker.scene_model, checker.kinematics,
                            self.attachment, {'cuboid': capacity, 'mesh': 50}, self._scene())
        # Always bind the complete current scene after the current measured
        # payload has replaced its world shapes. No cached pose/world is used.
        planner.update_world(self._scene())
        checker.update_world(self._scene())
        return planner, checker

    def check_selection(self, q: np.ndarray, active_joints: list[str] | None = None,
                        tool_frames: list[str] | None = None, locked_joints: dict | None = None):
        """The same selection checks as _select, before GPU model construction."""
        active = self.joint_names if active_joints is None else active_joints
        if not active or len(set(active)) != len(active) or not set(active) <= set(self.joint_names):
            raise ValueError("Invalid active joint selection")
        locks = dict(self._locks)
        if locked_joints:
            if not set(locked_joints) <= set(self._locks):
                raise ValueError("Only configured gripper/base locks may be updated")
            for name, value in locked_joints.items():
                node = self._joints[name].find("limit")
                if not np.isfinite(value) or not float(node.get("lower")) <= value <= float(node.get("upper")):
                    raise ValueError("Lock value outside URDF limits")
            locks.update(locked_joints)
        locks.update({n: float(q[i]) for i, n in enumerate(self.joint_names) if n not in active})
        tools = tuple(self.tool_frames if tool_frames is None else tool_frames)
        if not tools or len(set(tools)) != len(tools) or not set(tools) <= set(self.tool_frames):
            raise ValueError("Exactly one entry for each selected configured tool frame is required")
        return active, tools, locks

    def _refresh_measured_model(self,planner,checker,robot):
        from curobo.types import DeviceCfg
        current=planner.kinematics.config.kinematics_config
        from curobo_v2.measured_kinematics import MeasuredKinematicsInputs
        if self._measured_inputs is None:
            self._measured_inputs=MeasuredKinematicsInputs(self._config,DeviceCfg(device=self._device))
        new=self._measured_inputs.prepare(current,robot,
            payload_link=None if self.attachment is None else self.attachment.link)
        if (new.joint_names!=current.joint_names or new.tool_frames!=current.tool_frames
                or new.total_spheres!=current.total_spheres):
            raise IdentityError('Measured model update changes topology')
        expected_limits=self.limits[[self.joint_names.index(n)for n in new.joint_names]]
        if not np.allclose(new.joint_limits.position.detach().cpu().numpy().T,expected_limits,rtol=0.,atol=1e-6):
            raise IdentityError('Measured model update changes original joint limits')
        # The official shared-kinematics invariant must hold at runtime, not
        # merely in an upstream unit test. A mismatch rejects this request.
        for solver in [planner.ik_solver,planner.trajopt_solver]:
            for rollout in solver.get_all_rollout_instances():
                if rollout.transition_model.robot_model.config.kinematics_config is not current:
                    raise IdentityError('Official solver rollout has unshared kinematics')
        if planner.graph_planner is not None and planner.graph_planner.kinematics.config.kinematics_config is not current:
            raise IdentityError('Official graph planner has unshared kinematics')
        planner.kinematics.update_kinematics_config(new)
        if checker.kinematics.config.kinematics_config is not current:
            checker.kinematics.update_kinematics_config(new)
        planner.reset_seed()
        expected=robot['kinematics']['lock_joints']
        for model in [planner.kinematics,checker.kinematics]:
            actual=model.lock_jointstate
            if (actual is None or set(actual.joint_names)!=set(expected)
                    or not np.allclose(actual.position.detach().cpu().numpy().reshape(-1),
                        [expected[n]for n in actual.joint_names],rtol=0.,atol=1e-7)):
                raise IdentityError('Official measured lock update did not propagate')
        logging.warning('V2 public measured kinematics update: %d locked joints',len(expected))

    def _state(self, q: np.ndarray, planner):
        from curobo.types import JointState
        values = np.ascontiguousarray(q[..., [self.joint_names.index(n) for n in planner.joint_names]])
        return JointState.from_position(torch.as_tensor(values, device=self._device, dtype=torch.float32),
                                        joint_names=planner.joint_names)

    def _goal(self, targets: dict):
        from curobo.types import Pose, GoalToolPose
        poses = {}
        for name, (p, quat) in self.check_targets(targets).items():
            poses[name] = Pose(position=torch.as_tensor(p[None], device=self._device),
                               quaternion=torch.as_tensor(quat[None], device=self._device))
        return GoalToolPose.from_poses(poses, num_goalset=1)

    def check_targets(self, targets: dict):
        """Validate _goal inputs without creating cuRobo tensors."""
        if not targets or not set(targets) <= set(self.tool_frames):
            raise ValueError("Unknown target tool frame")
        checked = {}
        for name, target in targets.items():
            if any(isinstance(v,(bool,np.bool_)) for v in target["position"]):
                raise ValueError("Boolean target positions are invalid")
            p = np.asarray(target["position"], np.float32)
            value = target.get("quaternion_wxyz")
            if value is not None and any(isinstance(v,(bool,np.bool_)) for v in value):
                raise ValueError("Boolean target quaternion is invalid")
            quat = np.asarray(self.default_quaternion if value is None else value, np.float32)
            if p.shape != (3,) or quat.shape != (4,) or not np.isfinite(p).all() or not np.isfinite(quat).all() or abs(np.linalg.norm(quat)-1) > 1e-5:
                raise ValueError("Target requires finite xyz and a unit wxyz quaternion")
            checked[name] = p, quat
        return checked

    def validate(self, trajectory: np.ndarray, planner, checker, *, measured_start=True) -> bool:
        samples = [trajectory[0]]
        for a, b in zip(trajectory[:-1], trajectory[1:]):
            count = max(1, int(np.ceil(np.max(abs(b-a)) / self.max_joint_step)))
            samples.extend(a + (b-a) * t/count for t in range(1, count+1))
        dense = np.asarray(samples, np.float32)
        active = self._state(dense, planner).position
        valid = []
        for batch in active.split(128):
            valid.extend(checker.validate(batch[:, None, :].contiguous()).flatten().cpu().tolist())
        within = bool(np.isfinite(dense).all() and np.all(dense >= self.limits[:,0])
                      and np.all(dense <= self.limits[:,1]))
        self.last_validation = {"samples": len(samples), "max_joint_step_rad": self.max_joint_step,
                                "joint_limits": within, "collision_free": all(valid),
                                "obstacle_count": len(self._world_cuboids),
                                "world_sha256": hashlib.sha256(json.dumps(self._world_cuboids,sort_keys=True).encode()).hexdigest()}
        if self.attachment is not None and self.attachment.supports:
            from .contact_scene import CarriedSceneCollision
            scene = checker.scene_model
            if (not isinstance(scene, CarriedSceneCollision)
                    or not isinstance(planner.scene_collision_checker, CarriedSceneCollision)
                    or scene._support_names != self.attachment.supports
                    or scene._object_names != self.attachment.names):
                raise ValueError('Carried proxy support exemption is not wired')
            self.last_validation['proxy_support_exemption'] = {
                'enabled': True, 'support_names': sorted(scene._support_names),
                'carried_sphere_count': self.attachment.sphere_count,
                'object_names': sorted(scene._object_names)}
        if self.attachment is not None and hasattr(self.attachment,'artifact'):
            self.last_validation['attachment']=self.attachment.artifact()
        declared={c['name']for c in self._world_cuboids}
        carried=set()if self.attachment is None else self.attachment.names
        static=declared-carried
        if not carried<=declared or static&carried or static|carried!=declared:
            raise IdentityError('Static/carried collision geometry accounting mismatch')
        self.last_validation['geometry_accounting']={'declared_names':sorted(declared),'static_names':sorted(static),
            'carried_names':sorted(carried),'bijective':True,'no_shape_dropped':True,
            'carried_representation':'public robot extra_links/collision_spheres and complete exact PhysX shapes'if carried else None}
        exact = None
        if self.geometry is not None:
            locks = next(model[3] for model in self._models.values() if model[0] is planner)
            exact = self.geometry.validate(dense,locks,measured_start=measured_start)
            self.last_validation["exact_geometry"] = exact
            self.last_validation["proxy_collision_free"] = self.last_validation["collision_free"]
            self.last_validation["collision_free"] = bool(all(valid) and exact["collision_free"])
        return within and all(valid) and (exact is None or exact["collision_free"])

    def _extract(self, result, current: np.ndarray, planner, checker) -> np.ndarray | None:
        from .solver_evidence import summarize
        self.last_solver_diagnostics.append(dict(stage='public_api_result',**summarize(result)))
        if result is None or not bool(result.success.all()):
            from .solver_evidence import summarize
            self.last_solver_diagnostics.append(dict(stage='motion_result',**summarize(result)))
            self.last_failure = "official_motion_planner_non_success"
            return None
        state = result.get_interpolated_plan()
        positions = state.position
        while positions.ndim > 2:
            positions = positions[0]
        locks = next(model[3] for model in self._models.values() if model[0] is planner)
        if (positions.ndim != 2 or len(positions) < 2 or not torch.isfinite(positions).all()
                or state.joint_names is None or len(state.joint_names) != positions.shape[-1]
                or len(set(state.joint_names)) != len(state.joint_names)
                or set(state.joint_names) != set(planner.joint_names) | set(locks)):
            raise RuntimeError(f"Invalid official trajectory envelope: shape={tuple(positions.shape)}, returned_names={state.joint_names}, planner_names={planner.joint_names}")
        for name, value in locks.items():
            column = positions[:,state.joint_names.index(name)].detach().cpu().numpy()
            if np.max(abs(column - value)) > 1e-7:
                raise RuntimeError("Official trajectory changes a locked joint: " + name)
        trajectory = np.repeat(current[None], len(positions), axis=0)
        for i, name in enumerate(self.joint_names):
            if name in state.joint_names:
                trajectory[:, i] = positions[:, state.joint_names.index(name)].detach().cpu().numpy()
        if np.max(abs(trajectory[0]-current)) > 1e-4 or not self.validate(trajectory, planner, checker):
            self.last_failure = "trajectory_validation_failed"
            return None
        dt = state.dt
        if dt is None:
            raise RuntimeError("Official interpolated trajectory is missing time data")
        dt_values = dt.detach().cpu().numpy().reshape(-1)
        if not np.isfinite(dt_values).all() or np.any(dt_values <= 0) or not np.allclose(dt_values,self.interpolation_dt,rtol=0,atol=1e-7):
            raise RuntimeError("Official interpolated time step differs from configured seconds")
        self.last_timing = {"dt_seconds":float(dt_values[0]),"time_from_start_seconds":(np.arange(len(trajectory))*float(dt_values[0])).tolist()}
        derivatives={}
        for name in ('velocity','acceleration','jerk'):
            values=getattr(state,name,None)
            if values is None:
                derivatives[name]=None
                continue
            while values.ndim>2:values=values[0]
            if values.shape!=positions.shape or not torch.isfinite(values).all():
                raise RuntimeError('Invalid official interpolated derivative envelope: '+name)
            ordered=np.zeros_like(trajectory)
            for i,joint in enumerate(self.joint_names):
                ordered[:,i]=values[:,state.joint_names.index(joint)].detach().cpu().numpy()
            derivatives[name]=ordered
        self.last_derivatives=derivatives
        bounds={'velocity':np.array([float(self._joints[n].find('limit').get('velocity'))for n in self.joint_names]),
            'acceleration':np.full(len(self.joint_names),float(self._config['kinematics']['cspace']['max_acceleration'])),
            'jerk':np.full(len(self.joint_names),float(self._config['kinematics']['cspace']['max_jerk']))}
        evidence={}
        for name,values in derivatives.items():
            if values is None:
                evidence[name]={'result':'未验证','reason':'Optional official JointState field is None'}
            else:
                maximum=np.max(abs(values),axis=0)
                evidence[name]={'result':'PASS'if np.all(maximum<=bounds[name])else'FAIL',
                    'maximum_absolute':maximum.tolist(),'limits':bounds[name].tolist()}
        self.last_validation['official_derivative_limits']=evidence
        if any(row['result']=='FAIL'for row in evidence.values()):
            self.last_failure='official_derivative_limit_violation'
            return None
        from curobo_v2.sweep import certify_trajectory
        candidate=dict(status='success',joint_names=self.joint_names,trajectory=trajectory.tolist(),
            time_from_start_seconds=self.last_timing['time_from_start_seconds'],
            trajectory_derivatives={k:None if v is None else v.tolist()for k,v in derivatives.items()})
        receipt,motion=certify_trajectory(candidate,urdf=self._config['kinematics']['urdf_path'],
            world=self._world_cuboids,locks=locks,attachment=self._attachment_context,
            position_limits=self.limits,maximum_speed=dict(zip(self.joint_names,bounds['velocity'])),
            max_acceleration=float(bounds['acceleration'][0]),max_jerk=float(bounds['jerk'][0]))
        self.last_validation['continuous_geometry']=receipt
        self.last_validation['continuous_motion']=motion
        if receipt['result']!='PASS' or motion['result']!='PASS':
            self.last_validation['collision_free']=False
            self.last_failure='service_continuous_command_validation_rejected'
            return None
        return trajectory

    def _validate_tool_endpoints(self, values, targets, planner):
        poses = planner.compute_kinematics(self._state(values[None],planner)).tool_poses
        errors = {}
        for name,target in targets.items():
            pose = poses.get_link_pose(name)
            position = pose.position.detach().cpu().numpy().reshape(3)
            quaternion = pose.quaternion.detach().cpu().numpy().reshape(4)
            goal_quaternion = np.asarray(target.get('quaternion_wxyz') or self.default_quaternion)
            dot = abs(float(quaternion @ goal_quaternion))/(np.linalg.norm(quaternion)*np.linalg.norm(goal_quaternion))
            errors[name] = {'position_error_m':float(np.linalg.norm(position-target['position'])),
                            'orientation_error_rad':float(2*np.arccos(np.clip(dot,0.,1.)))}
        self.last_validation['tool_errors'] = errors
        return all(e['position_error_m']<=.004 and e['orientation_error_rad']<=.03 for e in errors.values())

    def plan_joints(self, current_q: np.ndarray, target_q: np.ndarray,
                    active_joints=None, locked_joints=None) -> np.ndarray | None:
        started = time.monotonic()
        def stage(name):
            logging.warning("V2 joint planning stage=%s elapsed=%.6f", name, time.monotonic()-started)
        self.last_timing = None; self.last_derivatives = None; self.last_failure = None; self.last_validation = None; self.last_solver_diagnostics = []
        current_q, target_q = self.named(current_q), self.named(target_q)
        planner, checker = self._select(current_q, active_joints, locked_joints=locked_joints)
        stage("model_selected")
        if not self.validate(current_q[None], planner, checker):
            self.last_failure = "start_state_collision_or_constraint_violation"
            return None
        stage("start_validated")
        if not self.validate(target_q[None], planner, checker,measured_start=False):
            self.last_failure = "goal_state_collision_or_constraint_violation"
            return None
        stage("goal_validated")
        if active_joints is not None:
            inactive = [i for i,n in enumerate(self.joint_names) if n not in active_joints]
            if inactive and np.max(abs(current_q[inactive]-target_q[inactive])) > 1e-6:
                raise ValueError("Target changes an inactive joint")
        target_state=self._state(target_q[None],planner)
        current_state=self._state(current_q[None],planner)
        from curobo_v2.gpu_activity import public_call
        with public_call():
            result = planner.plan_cspace(target_state,current_state,max_attempts=1,enable_graph_attempt=1 if self.settings.get('pose_search')=='measured_reference_high_penalty_v1' else 0)
        stage("cspace_solved")
        trajectory = self._extract(result,current_q,planner,checker)
        if trajectory is not None and np.max(abs(trajectory[-1]-target_q)) > 1e-4:
            self.last_failure = "trajectory_endpoint_error"
            trajectory = None
        stage("trajectory_validated")
        return trajectory

    def plan_pose(self, current_q: np.ndarray, target_pos=None, target_quat=None,
                  lock_base: bool = False, targets=None, active_joints=None, locked_joints=None):
        self.last_timing = None; self.last_derivatives = None; self.last_failure = None; self.last_validation = None; self.last_solver_diagnostics = []
        current_q = self.named(current_q)
        if lock_base and active_joints is None:
            bases = self.settings.get("base_joints", [n for n in self.joint_names if n.startswith("base_")])
            active_joints = [n for n in self.joint_names if n not in bases]
        targets = targets or {self.tool_frames[0]: {"position":target_pos,"quaternion_wxyz":target_quat}}
        if not set(targets) <= set(self.tool_frames):
            raise ValueError("Unknown target tool frame")
        # Locked inactive joints already fix their TCP. Only requested tools
        # belong in the pose objective; all robot links remain collision bodies.
        planner, checker = self._select(current_q,active_joints,tool_frames=list(targets),locked_joints=locked_joints)
        if not self.validate(current_q[None], planner, checker):
            self.last_failure = "start_state_collision_or_constraint_violation"
            return None
        result = planner.plan_pose(self._goal(targets),self._state(current_q[None],planner),max_attempts=1,enable_graph_attempt=1 if self.settings.get('pose_search')=='measured_reference_high_penalty_v1' else 0)
        from .solver_evidence import summarize
        self.last_solver_diagnostics.append(summarize(result))
        trajectory = self._extract(result,current_q,planner,checker)
        if trajectory is not None and not self._validate_tool_endpoints(trajectory[-1],targets,planner):
            self.last_failure = 'trajectory_tool_endpoint_error'
            return None
        return trajectory

    def plan_cooperative(self,current_q,object_pose,context,locked_joints=None):
        self.set_attachment(context)
        from .cooperative import CarriedCuboid
        if not isinstance(self.attachment,CarriedCuboid):
            raise ValueError('Cooperative endpoint requires a measured two-hand attachment')
        return self.plan_pose(current_q,targets=self.attachment.targets(object_pose),locked_joints=locked_joints)

    def solve_ik(self, target_pos=None, target_quat=None, current_q=None,
                 lock_base=False, targets=None, active_joints=None, locked_joints=None):
        self.last_timing = None; self.last_derivatives = None; self.last_failure = None; self.last_validation = None; self.last_solver_diagnostics = []
        current = self.default_q if current_q is None else self.named(current_q)
        if lock_base and active_joints is None:
            active_joints = [n for n in self.joint_names if not n.startswith("base_")]
        targets = targets or {self.tool_frames[0]: {"position":target_pos,"quaternion_wxyz":target_quat}}
        if not set(targets) <= set(self.tool_frames):
            raise ValueError("Unknown target tool frame")
        planner, checker = self._select(current,active_joints,tool_frames=list(targets),locked_joints=locked_joints)
        if set(targets) != set(planner.tool_frames):
            poses = planner.compute_kinematics(self._state(current[None],planner)).tool_poses
            targets = deepcopy(targets)
            for name in planner.tool_frames:
                if name not in targets:
                    pose = poses.get_link_pose(name)
                    targets[name] = {"position":pose.position.flatten().cpu().tolist(),
                                     "quaternion_wxyz":pose.quaternion.flatten().cpu().tolist()}
        logging.warning("V2 public IK API call started")
        result = planner.ik_solver.solve_pose(self._goal(targets),current_state=self._state(current[None],planner),return_seeds=1)
        from .solver_evidence import summarize
        self.last_solver_diagnostics.append(dict(stage='ik_public_api_result',**summarize(result)))
        if not bool(result.success.all()):
            self.last_failure = "official_ik_non_success"
            return None
        solution = current.copy()
        values = result.solution.reshape(-1,planner.action_dim)[0].detach().cpu().numpy()
        for name,value in zip(planner.joint_names,values):
            solution[self.joint_names.index(name)] = value
        if not self.validate(solution[None],planner,checker):
            self.last_failure = "ik_geometry_validation_failed"
            return None
        if not self._validate_tool_endpoints(solution,targets,planner):
            self.last_failure = "ik_tool_endpoint_error"
            return None
        return solution

    def plan_ik_path(self, current_q, *, targets, active_joints, locked_joints=None,
                     max_joint_jump=.35, minimum_duration=.5, ik_candidates=1):
        """One public IK solve followed by a locally generated certified path."""
        from curobo_v2.ik_path import quintic_path, ranked_ik_candidates
        from curobo_v2.sweep import certify_trajectory
        from .solver_evidence import summarize

        self.last_timing = None; self.last_derivatives = None; self.last_failure = None
        self.last_validation = None; self.last_solver_diagnostics = []
        self.last_ik_path_metrics = {}
        tool = CuroboPlanner.check_ik_path_request(self, targets, active_joints,
                                                   max_joint_jump, minimum_duration,
                                                   ik_candidates=ik_candidates)
        current = self.named(current_q)
        started = time.monotonic()
        planner, checker = self._select(current, active_joints, tool_frames=[tool], locked_joints=locked_joints)
        if not self.validate(current[None], planner, checker):
            self.last_failure = 'start_state_collision_or_constraint_violation'
            return None
        solve_started = time.monotonic()
        result = planner.ik_solver.solve_pose(
            self._goal(targets), current_state=self._state(current[None], planner), return_seeds=ik_candidates)
        self.last_ik_path_metrics['ik_seconds'] = time.monotonic() - solve_started
        self.last_solver_diagnostics.append(dict(stage='ik_public_api_result', **summarize(result)))
        selection_started = time.monotonic()
        if result is None or not bool(result.success.any()):
            self.last_failure = 'official_ik_non_success'
            if result is not None and result.solution is not None:
                unchosen = result.solution[0].detach().cpu().numpy()
                if unchosen.shape == (ik_candidates, planner.action_dim):
                    current_active = current[[self.joint_names.index(n) for n in planner.joint_names]]
                    _, distances, _ = ranked_ik_candidates(current_active, planner.joint_names,
                        active_joints, unchosen, np.zeros(ik_candidates, dtype=bool))
                    self.last_ik_path_metrics['candidate_distances'] = distances
            self.last_ik_path_metrics.setdefault('candidate_distances', [])
            self.last_ik_path_metrics['candidate_reasons'] = ([] if result is None else
                ['official_ik_non_success'] * ik_candidates)
            self.last_ik_path_metrics['selection_seconds'] = time.monotonic() - selection_started
            return None
        values = result.solution[0].detach().cpu().numpy()
        successes = result.success[0].detach().cpu().numpy()
        if values.shape != (ik_candidates, planner.action_dim):
            self.last_failure = 'ik_invalid_solution'
            self.last_ik_path_metrics['selection_seconds'] = time.monotonic() - selection_started
            return None
        current_active = current[[self.joint_names.index(n) for n in planner.joint_names]]
        order, distances, changes = ranked_ik_candidates(
            current_active, planner.joint_names, active_joints, values, successes)
        self.last_ik_path_metrics['candidate_distances'] = distances
        candidate_reasons = ['official_ik_non_success' if not ok else
                             'ik_invalid_solution' if distance is None else None
                             for ok, distance in zip(successes, distances)]
        self.last_ik_path_metrics['candidate_reasons'] = candidate_reasons
        failures = []
        solution = None
        jump = None
        for index in order:
            candidate = current.copy()
            for name, value in zip(planner.joint_names, values[index]):
                candidate[self.joint_names.index(name)] = value
            if np.any(candidate < self.limits[:, 0]) or np.any(candidate > self.limits[:, 1]):
                failures.append('ik_joint_limits')
                candidate_reasons[index] = failures[-1]
                continue
            # For multiple candidates, reject large jumps before costly geometry checks.
            # k=1 retains the original check order and failure reason.
            if ik_candidates > 1 and changes[index] > max_joint_jump:
                failures.append('ik_path_joint_jump')
                candidate_reasons[index] = failures[-1]
                continue
            if ik_candidates > 1 and not self._validate_tool_endpoints(candidate, targets, planner):
                failures.append('ik_tool_endpoint_error')
                candidate_reasons[index] = failures[-1]
                continue
            if not self.validate(candidate[None], planner, checker, measured_start=False):
                failures.append('ik_geometry_validation_failed')
                candidate_reasons[index] = failures[-1]
                continue
            if ik_candidates == 1 and not self._validate_tool_endpoints(candidate, targets, planner):
                failures.append('ik_tool_endpoint_error')
                candidate_reasons[index] = failures[-1]
                continue
            if ik_candidates == 1 and changes[index] > max_joint_jump:
                if self.last_validation is not None:
                    self.last_validation['ik_path_max_joint_change'] = changes[index]
                failures.append('ik_path_joint_jump')
                candidate_reasons[index] = failures[-1]
                continue
            solution, jump = candidate, changes[index]
            candidate_reasons[index] = 'selected'
            self.last_ik_path_metrics.update(selected_joint_distance_rad=distances[index],
                                             selected_max_joint_change_rad=jump,
                                             selected_candidate_index=index)
            break
        self.last_ik_path_metrics['selection_seconds'] = time.monotonic() - selection_started
        if solution is None:
            if not order:
                self.last_failure = 'ik_invalid_solution'
            elif len(set(failures)) == 1:
                self.last_failure = failures[0]
            else:
                self.last_failure = 'ik_path_no_feasible_candidate'
            return None
        self.last_validation['ik_path_max_joint_change'] = jump
        bounds = {'velocity': np.array([float(self._joints[n].find('limit').get('velocity'))
                                        for n in self.joint_names]),
                  'acceleration': np.full(len(self.joint_names), float(self._config['kinematics']['cspace']['max_acceleration'])),
                  'jerk': np.full(len(self.joint_names), float(self._config['kinematics']['cspace']['max_jerk']))}
        path_started = time.monotonic()
        trajectory, times, derivatives = quintic_path(
            current, solution, dt=self.interpolation_dt, minimum_duration=minimum_duration,
            speed=bounds['velocity'], acceleration=float(bounds['acceleration'][0]),
            jerk=float(bounds['jerk'][0]))
        self.last_ik_path_metrics['trajectory_generation_seconds'] = time.monotonic() - path_started
        if not self.validate(trajectory, planner, checker):
            self.last_failure = 'trajectory_validation_failed'
            return None
        self.last_validation['ik_path_max_joint_change'] = jump
        evidence = {}
        for name, values in derivatives.items():
            maximum = np.max(np.abs(values), axis=0)
            evidence[name] = {'result': 'PASS' if np.all(maximum <= bounds[name]) else 'FAIL',
                              'maximum_absolute': maximum.tolist(), 'limits': bounds[name].tolist()}
        self.last_validation['official_derivative_limits'] = evidence
        if any(row['result'] == 'FAIL' for row in evidence.values()):
            self.last_failure = 'official_derivative_limit_violation'
            return None
        self.last_timing = {'dt_seconds': self.interpolation_dt,
                            'time_from_start_seconds': times.tolist()}
        self.last_derivatives = derivatives
        candidate = dict(status='success', joint_names=self.joint_names,
                         trajectory=trajectory.tolist(), time_from_start_seconds=times.tolist(),
                         trajectory_derivatives={k: v.tolist() for k, v in derivatives.items()})
        locks = next(model[3] for model in self._models.values() if model[0] is planner)
        certificate_started = time.monotonic()
        receipt, motion = certify_trajectory(
            candidate, urdf=self._config['kinematics']['urdf_path'], world=self._world_cuboids,
            locks=locks, attachment=self._attachment_context, position_limits=self.limits,
            maximum_speed=dict(zip(self.joint_names, bounds['velocity'])),
            max_acceleration=float(bounds['acceleration'][0]), max_jerk=float(bounds['jerk'][0]))
        self.last_ik_path_metrics.update(certification_seconds=time.monotonic() - certificate_started,
                                         total_seconds=time.monotonic() - started,
                                         certificate_checks=receipt.get('queries'))
        self.last_validation['continuous_geometry'] = receipt
        self.last_validation['continuous_motion'] = motion
        if receipt['result'] != 'PASS' or motion['result'] != 'PASS':
            self.last_validation['collision_free'] = False
            self.last_failure = 'service_continuous_command_validation_rejected'
            return None
        return trajectory

    def check_ik_path_request(self, targets, active_joints, max_joint_jump=.35,
                              minimum_duration=.5, ik_candidates=1):
        """Validate the IK path route's arm and scalar bounds before GPU use."""
        if (not np.isfinite(max_joint_jump) or max_joint_jump <= 0
                or not np.isfinite(minimum_duration) or minimum_duration <= 0):
            raise ValueError('IK path bounds must be positive and finite')
        maximum = 64 if getattr(self, 'settings', {}).get('pose_search') is not None else 16
        if type(ik_candidates) is not int or not 1 <= ik_candidates <= maximum:
            raise ValueError(f'ik_candidates must be an integer between 1 and {maximum}')
        if (len(targets) != 1 or active_joints is None or len(active_joints) != 7
                or len(set(active_joints)) != 7):
            raise ValueError('IK path requires one target and exactly seven active arm joints')
        tool = next(iter(targets))
        side = tool.removeprefix('openarm_').removesuffix('_hand_tcp')
        if (tool != f'openarm_{side}_hand_tcp' or side not in ('left', 'right')
                or set(active_joints) != {f'openarm_{side}_joint{i}' for i in range(1, 8)}):
            raise ValueError('IK path target and active joints must belong to the same arm')
        return tool

    def fk(self, q, locked_joints=None, active_joints=None) -> dict:
        current = self.named(q)
        planner, _ = self._select(current,active_joints,locked_joints=locked_joints)
        state = planner.compute_kinematics(self._state(current[None],planner))
        return {name:{"position":state.tool_poses.get_link_pose(name).position.flatten().cpu().tolist(),
                      "quaternion_wxyz":state.tool_poses.get_link_pose(name).quaternion.flatten().cpu().tolist()}
                for name in self.tool_frames}

    def validate_base_path(self, *args, **kwargs):
        raise ValueError('Base motion requires a complete V2 native-joint trajectory; legacy proxy path validation is retired')
