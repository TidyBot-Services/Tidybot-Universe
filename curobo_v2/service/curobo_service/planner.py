"""Serialize planning and world updates behind the same lock."""
from __future__ import annotations
from threading import RLock
import numpy as np
from .config import Config
from .planner_core import CuroboPlanner
from .world import WorldStore


class PlannerAdapter:
    def __init__(self, device: str, world_store: WorldStore):
        self._planner = CuroboPlanner(device, Config.ROBOT_CFG, str(Config.ASSETS_DIR),
                                      Config.USE_CUDA_GRAPH)
        self._world = world_store
        self._lock = RLock()
        self._warmed_up = False

    def warmup(self, current_q=None, joint_names=None, active_joints=None,
               locked_joints=None, tool_frames=None, env_id=None, attachment=None) -> dict:
        with self._lock:
            if current_q is None and any(value is not None for value in
                    (joint_names,active_joints,locked_joints,tool_frames,attachment,env_id)):
                raise ValueError('Warmup selectors require the actual current_q; no selector may be ignored')
            if attachment is not None and env_id is None:
                raise ValueError('Attachment warmup requires an explicitly uploaded env_id')
            if env_id is not None:self._ensure_world(env_id)
            self._planner.set_attachment(attachment)
            if current_q is not None:
                q = self._planner.named(current_q, joint_names)
                model, checker = self._planner._select(q, active_joints, tool_frames, locked_joints)
                self._planner._warmed_up = True
                self._warmed_up = True
                return {"status": "warmed_up", **self._planner.identity}
            if self._warmed_up:
                return {"status": "already_warmed_up"}
            self._planner.warmup(); self._warmed_up = True
            return {"status": "warmed_up", **self._planner.identity}

    def is_warmed_up(self) -> bool:
        return self._warmed_up

    def _ensure_world(self, env_id: str) -> None:
        if self._world.active() == env_id:
            return
        world = self._world.get(env_id)
        if world is None:
            raise ValueError("Unknown environment; explicitly upload its complete collision world")
        self._planner.set_attachment(None)
        self._planner.set_collision_world(world.cuboids, robot_pos=world.robot_pos)
        self._world.mark_active(env_id)

    def _result(self, result, names, *, ik=False) -> dict:
        world = self._world.get(self._world.active())
        response = {"status": "failed" if result is None else "success",
                    "world_revision": world.revision, "world_sha256": world.sha256,
                    "qpos" if ik else "trajectory": None if result is None else self._planner.reorder(result,names).tolist(),
                    "joint_names": names or self._planner.joint_names,
                    "validation": self._planner.last_validation,
                    "reason": self._planner.last_failure,
                    "solver_diagnostics": self._planner.last_solver_diagnostics,
                    "robot": self._planner.identity}
        if not ik:
            response["interpolation_dt"] = None if result is None else self._planner.last_timing["dt_seconds"]
            response["time_from_start_seconds"] = None if result is None else self._planner.last_timing["time_from_start_seconds"]
            response['trajectory_derivatives']=None if result is None else {
                key:None if value is None else self._planner.reorder(value,names).tolist()
                for key,value in self._planner.last_derivatives.items()}
            response['trajectory_derivatives_source']='unmodified official public get_interpolated_plan JointState optional fields'
        return response

    def plan_pose(self, env_id, current_q, target_pos=None, target_quat=None,
                  lock_base=False, joint_names=None, targets=None, active_joints=None, locked_joints=None, attachment=None):
        with self._lock:
            current = self._planner.named(current_q,joint_names)
            self._ensure_world(env_id)
            self._planner.set_attachment(attachment)
            result = self._planner.plan_pose(current,target_pos,target_quat,lock_base,
                targets=targets,active_joints=active_joints,locked_joints=locked_joints)
            return self._result(result,joint_names)

    def plan_ik_path(self, env_id, current_q, *, targets, joint_names=None,
                     active_joints=None, locked_joints=None, attachment=None,
                     max_joint_jump=.35, minimum_duration=.5, ik_candidates=1):
        with self._lock:
            current = self._planner.named(current_q, joint_names)
            self._ensure_world(env_id)
            self._planner.set_attachment(attachment)
            result = self._planner.plan_ik_path(
                current, targets=targets, active_joints=active_joints,
                locked_joints=locked_joints, max_joint_jump=max_joint_jump,
                minimum_duration=minimum_duration, ik_candidates=ik_candidates)
            response = self._result(result, joint_names)
            response.update(ik_candidates=ik_candidates,
                            ik_candidate_distances=self._planner.last_ik_path_metrics.get('candidate_distances', []),
                            ik_candidate_reasons=self._planner.last_ik_path_metrics.get('candidate_reasons', []),
                            ik_selected_joint_distance_rad=self._planner.last_ik_path_metrics.get('selected_joint_distance_rad'),
                            ik_selected_max_joint_change_rad=self._planner.last_ik_path_metrics.get('selected_max_joint_change_rad'),
                            ik_selection_seconds=self._planner.last_ik_path_metrics.get('selection_seconds'),
                            ik_seconds=self._planner.last_ik_path_metrics.get('ik_seconds'))
            response['source'] = 'ik_path'
            response['trajectory_derivatives_source'] = 'analytic stationary-endpoint quintic joint path'
            return response

    def plan_joints(self, env_id, current_q, target_q, joint_names=None, active_joints=None, locked_joints=None, attachment=None):
        with self._lock:
            current = self._planner.named(current_q,joint_names)
            target = self._planner.named(target_q,joint_names)
            self._ensure_world(env_id)
            self._planner.set_attachment(attachment)
            result = self._planner.plan_joints(current,target,active_joints,locked_joints)
            return self._result(result,joint_names)

    def plan_cooperative(self,env_id,current_q,object_pose,attachment,joint_names=None,locked_joints=None):
        with self._lock:
            self._ensure_world(env_id)
            result=self._planner.plan_cooperative(self._planner.named(current_q,joint_names),object_pose,attachment,locked_joints)
            return self._result(result,joint_names)

    def solve_ik(self, env_id, target_pos=None, target_quat=None, lock_base=False,
                 seed_q=None, joint_names=None, targets=None, active_joints=None, locked_joints=None):
        with self._lock:
            self._ensure_world(env_id)
            self._planner.set_attachment(None)
            if seed_q is None:
                raise ValueError("V2 IK requires the actual current named joint state")
            current = self._planner.named(seed_q,joint_names)
            result = self._planner.solve_ik(target_pos,target_quat,current_q=current,lock_base=lock_base,
                targets=targets,active_joints=active_joints,locked_joints=locked_joints)
            return self._result(result,joint_names,ik=True)

    def fk(self, env_id, q, joint_names=None, locked_joints=None, active_joints=None):
        with self._lock:
            self._ensure_world(env_id)
            self._planner.set_attachment(None)
            return {"status":"success","poses":self._planner.fk(self._planner.named(q,joint_names),locked_joints,active_joints),
                    "robot":self._planner.identity}

    def validate_base_path(self, env_id, base_positions, target_pos, base_box):
        with self._lock:
            self._ensure_world(env_id)
            collision, index, name = self._planner.validate_base_path(np.asarray(base_positions),
                                        target_pos=np.asarray(target_pos),base_box=base_box)
            return {"collision":bool(collision),"waypoint_idx":int(index),"fixture_name":name}

    def push_world(self, env_id, cuboids, robot_pos=None) -> dict:
        with self._lock:
            self._world.check_capacity(env_id)
            self._planner.set_attachment(None)
            self._planner.set_collision_world(cuboids,robot_pos)
            self._world.set(env_id,cuboids,robot_pos)
            self._world.mark_active(env_id)
            world = self._world.get(env_id)
            return {"status":"ok","env_id":env_id,"cuboid_count":len(cuboids),
                    "world_revision": world.revision, "world_sha256": world.sha256}
