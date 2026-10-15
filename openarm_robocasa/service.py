"""Single-owner OpenArm kitchen HTTP service, inheriting accepted stop semantics."""
from pathlib import Path
import argparse
import io
import json
import signal
import sys

import numpy as np
import sapien

from .kitchen import OpenArmKitchen, accepted_module, PARAMETERS
from .planning import OpenArmPlanner

foundation = accepted_module()


class Runtime(foundation.Runtime):
    def __init__(self, evidence, *, task_id, seed, execution_arm, fault_after_steps=None, parameters_path=PARAMETERS):
        super().__init__(evidence, fault_after_steps)
        self.task_id = task_id; self.seed = seed; self.execution_arm = execution_arm
        self.kitchen = None; self.planner = None; self.object_log = []
        self.parameters_path = parameters_path

    def init_rig(self):
        from curobo_v2.deployment import retired_entry
        retired_entry('OpenArm legacy fixed runtime')
        if self.kitchen is not None:
            from .contact_controller import apply_contact_controller
            control=foundation.sim.CFG["controller"]
            for side in ["left", "right"]:
                apply_contact_controller(self.rig,side,stiffness=control["finger_stiffness"],
                    driver_damping=control["finger_damping"],follower_damping=0,mode=control["mode"])
            self.kitchen.reset_same_scene()
            self.reset_steps = 0
            check = self.planner.check_path(np.array(self.rig.robot.qpos))
            if not check["collision_free"]:
                raise RuntimeError("OpenArm native reset installation collides: " + json.dumps(check))
            return
        if self.planner is not None:
            self.planner.close()
        if self.kitchen is not None:
            self.kitchen.close()
        self.kitchen = OpenArmKitchen(self.task_id, self.seed, self.execution_arm, parameters_path=self.parameters_path)
        self.rig = self.kitchen.rig
        self.reset_steps = 0
        self.planner = OpenArmPlanner(self.kitchen)
        check = self.planner.check_path(np.array(self.rig.robot.qpos))
        if not check["collision_free"]:
            raise RuntimeError("OpenArm reset installation collides: " + json.dumps(check))
        self.log("installation", robot=self.kitchen.selection.artifact(), mount=self.kitchen.mount,
                 collision_check=check, objects=self.kitchen.object_state())
        self.setup_camera()

    def setup_camera(self):
        from mani_skill.utils.sapien_utils import look_at
        from mani_skill.render.shaders import set_shader_pack, PREBUILT_SHADER_CONFIGS
        scene = self.rig.scene
        set_shader_pack(PREBUILT_SHADER_CONFIGS["default"])
        self.camera = scene.add_camera("base_camera", 640, 480, np.pi / 3, .05, 8.)
        destination = getattr(self.kitchen.native, self.kitchen.spec.destination_debug_key)
        center = np.array(destination.pos) + [0, 0, .5]
        pose = look_at(center + [-1.8, -1.3, 1.], center)
        raw = sapien.Pose(pose.p[0].numpy(), pose.q[0].numpy())
        self.camera.entity.pose = raw
        self.camera_pose = raw.to_transformation_matrix()

    def health(self):
        result = super().health()
        result.update(api_version="openarm-robocasa/1.0", task_id=self.task_id,
                      robot={"model": "OpenArm", "execution_arm": self.execution_arm},
                      parameters_sha256=self.kitchen.parameters_sha256 if self.kitchen else None,
                      capabilities=["gripper_contact_hold"])
        return result

    def state(self):
        state = super().state()
        state.update(robot=self.kitchen.selection.artifact(), control_frame="world",
                     mount_world_xyz_wxyz=self.kitchen.mount,
                     tcp_world={side: self.rig.robot.find_link_by_name(f"openarm_{side}_hand_tcp").entity_pose.to_transformation_matrix()
                                for side in ["left", "right"]})
        return state

    def observation(self):
        # Called on the single simulation owner: rendering and state capture
        # cannot be interleaved with another physics step.
        raw = super().observation()
        with np.load(io.BytesIO(raw), allow_pickle=False) as captured:
            arrays = {name: captured[name].copy() for name in captured.files}
        arrays["state_json"] = foundation.dump(self.state()).decode()
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()

    def camera_snapshot(self):
        from .camera import pack_camera
        self.rig.scene.update_render();self.camera.take_picture()
        state=json.loads(foundation.dump(self.state()))
        return pack_camera(np.array(self.camera.get_picture("Color"))[...,:3],state)

    def validate_action(self, body, ingress_epoch):
        if isinstance(body, dict):
            inactive = "right" if self.execution_arm == "left" else "left"
            if {inactive, inactive + "_gripper"} & set(body):
                raise foundation.APIError(400, "inactive_arm_locked", "This run executes only " + self.execution_arm)
        goal = super().validate_action(body, ingress_epoch)
        check = self.planner.check_path(goal, allow_target_contact=True)
        if not check["collision_free"]:
            self.log("action_rejected_collision", collision_check=check)
            raise foundation.APIError(422, "collision_rejected", "OpenArm action intersects kitchen or either arm")
        gripper = self.execution_arm + "_gripper"
        if gripper in body and body[gripper] > self.rig.robot.qpos[self.rig.idx[f"openarm_{self.execution_arm}_finger_joint1"]]:
            from .contact_controller import apply_contact_controller
            control = foundation.sim.CFG["controller"]
            apply_contact_controller(self.rig, self.execution_arm, stiffness=control["finger_stiffness"],
                driver_damping=control["finger_damping"], follower_damping=0, mode=control["mode"])
        return goal

    def physics_step(self):
        super().physics_step()
        self.kitchen.steps = self.reset_steps
        self.object_log.append({"epoch": self.epoch, "step": self.total_steps,
                                "objects": self.kitchen.object_state()})
        if self.active:
            action = self.actions[self.active]
            if action.get("grasp_mode") == "contact_hold" and not action.get("contact_hold_committed"):
                from .contact_policy import bilateral_target_contacts
                proof = bilateral_target_contacts(self.rig.scene, side=self.execution_arm,
                                                   target_id=self.kitchen.target_entity.per_scene_id)
                action["contact_steps"] = action.get("contact_steps", 0) + 1 if proof else 0
                if action["contact_steps"] >= self.kitchen.parameters["contact_hold_steps"]:
                    driver = self.rig.idx[f"openarm_{self.execution_arm}_finger_joint1"]
                    held = max(self.kitchen.parameters["gripper_free_close_m"],
                               float(self.rig.robot.qpos[driver]) - self.kitchen.parameters["contact_hold_inset_m"])
                    self.rig.goal = self.rig.make_q({self.execution_arm + "_gripper": held}, self.rig.goal)
                    action.update(contact_hold_committed=True, contact_step=self.total_steps,
                                  contact_proof=proof, contact_goal_m=held, goal=self.rig.goal.tolist())
                    from .contact_controller import apply_contact_controller
                    action["contact_controller"] = apply_contact_controller(self.rig, self.execution_arm,
                        stiffness=self.kitchen.parameters.get("contact_hold_finger_stiffness", foundation.sim.CFG["controller"]["finger_stiffness"]),
                        driver_damping=self.kitchen.parameters["contact_hold_finger_damping"],
                        follower_damping=self.kitchen.parameters.get("contact_hold_follower_damping", 0),
                        mode=foundation.sim.CFG["controller"]["mode"])
                    self.log("grasp_contact_hold", action=action.copy())
            from .contact_policy import unexpected_contacts
            contacts = unexpected_contacts(self.rig.scene, side=self.execution_arm,
                                            target_id=self.kitchen.target_entity.per_scene_id)
            if contacts:
                action = self.actions[self.active]
                action.update(status="failed", finished_step=self.total_steps,
                              error={"code": "collision_stop", "message": "Unexpected physical contact; committed hold"},
                              collision_contacts=contacts)
                self.log("collision_stop", action=action.copy())
                self.active = None; self.epoch += 1
                q = np.clip(np.array(self.rig.robot.qpos), self.rig.qlimits[:, 0], self.rig.qlimits[:, 1])
                for mimic in self.rig.mimics:
                    q[self.rig.idx[mimic.joint]] = mimic.multiplier * q[self.rig.idx[mimic.mimic]] + mimic.offset
                self.rig.goal = q.copy(); self.rig.command = q.copy(); self.rig.apply_drive()

    def execute(self, request):
        method, path, body = request.method, request.path, request.body
        if path == "/grasp":
            if method != "POST" or not isinstance(body, dict) or set(body) != {"epoch"}:
                request.response = (400, "application/json", foundation.APIError(400, "invalid_grasp", "Expected locked epoch"))
                request.event.set(); return
            request.path = "/actions"
            request.body = {self.execution_arm + "_gripper": self.kitchen.parameters["gripper_free_close_m"], "epoch": body["epoch"]}
            super().execute(request)
            if request.response[0] == 202:
                self.actions[self.active].update(grasp_mode="contact_hold", contact_hold_committed=False,
                    requested_operation="close_until_bilateral_contact_or_free_limit")
            return
        endpoints = {"/task/info", "/task/success", "/perceive", "/plan", "/camera"}
        if path not in endpoints:
            return super().execute(request)
        try:
            if request.canceled:
                raise foundation.APIError(503, "request_timeout", "Canceled query was not executed")
            if method == "GET" and path == "/camera":
                request.response=(200,"application/x-npz",self.camera_snapshot())
                return
            if method == "GET" and path == "/task/info":
                result = {"task": self.kitchen.spec.environment_id,
                          "lang": self.kitchen.native.get_ep_meta().get("lang", ""),
                          "variation": self.kitchen.variation,
                          "robot": self.kitchen.selection.artifact(), "cameras": ["base_camera"]}
            elif method == "GET" and path == "/task/success":
                result = self.kitchen.native_evaluate()
                result["success"] = result["native_success"]
                self.log("native_evaluation", **result)
            elif method == "POST" and path == "/perceive" and body == {}:
                objects = []
                for name, pose in self.kitchen.object_state().items():
                    objects.append({"name": name, "position": pose["position"],
                                    "quaternion_wxyz": pose["quaternion_wxyz"],
                                    "source": "sim_gt", "frame": "world", "confidence": 1.})
                destination = getattr(self.kitchen.native, self.kitchen.spec.destination_debug_key)
                objects.append({"name": self.kitchen.spec.destination_debug_key,
                                "position": np.asarray(destination.pos).tolist(),
                                "source": "sim_gt", "frame": "world", "confidence": 1.})
                result = {"objects": objects, "cameras": ["base_camera"],
                          "perception_mode": "sim_gt", "robot": self.kitchen.selection.artifact()}
            elif method == "POST" and path == "/plan":
                if not isinstance(body, dict) or set(body) != {"position"}:
                    raise foundation.APIError(400, "invalid_plan", "Expected position[3] in world meters")
                orientation = self.rig.robot.root_pose.to_transformation_matrix()[:3, :3] @ np.asarray(
                    self.kitchen.parameters["tcp_orientation_mount"][self.execution_arm])
                from .planning import PlanningError
                try:
                    result = self.planner.plan(body["position"], orientation)
                    result.update(reachable=True, planning_required=True, status="planned")
                except PlanningError as exc:
                    result = {"robot": self.kitchen.selection.artifact(), "reachable": False,
                              "planning_required": True, "status": "rejected", "reason": str(exc),
                              "action_dispatched": False}
                result.update(epoch=self.epoch, physical_step=self.total_steps)
                self.log("plan", result=result)
            else:
                raise foundation.APIError(400, "invalid_request", "Unsupported endpoint arguments")
            request.response = (200, "application/json", result)
        except foundation.APIError as exc:
            request.response = (exc.status, "application/json", exc)
        except Exception as exc:
            self.log("query_rejected", path=path, error=f"{type(exc).__name__}: {exc}")
            request.response = (422, "application/json", foundation.APIError(422, "query_rejected", str(exc)))
        finally:
            request.event.set()

    def run(self):
        try:
            super().run()
        finally:
            if self.planner is not None:
                self.planner.close()
            if self.kitchen is not None:
                (self.evidence / "object_trajectory.jsonl").write_text(
                    "".join(json.dumps(row, allow_nan=False) + "\n" for row in self.object_log))
                self.kitchen.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8872)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--task", choices=["counter_to_sink", "counter_to_cab"], default="counter_to_sink")
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--arm", choices=["left", "right"], default="left")
    parser.add_argument("--fault-after-steps", type=int)
    parser.add_argument("--parameters", type=Path, default=PARAMETERS)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    runtime = Runtime(args.evidence, task_id=args.task, seed=args.seed,
                      execution_arm=args.arm, fault_after_steps=args.fault_after_steps, parameters_path=args.parameters)
    server = foundation.Server(("127.0.0.1", args.port), foundation.Handler)
    server.runtime = runtime; server.timeout = .1
    signal.signal(signal.SIGTERM, lambda *_: runtime.closing.set())
    signal.signal(signal.SIGINT, lambda *_: runtime.closing.set())
    runtime.thread.start()
    try:
        while not runtime.closing.is_set():
            server.handle_request()
    finally:
        runtime.closing.set(); server.server_close(); runtime.thread.join(10)
        if runtime.thread.is_alive():
            raise RuntimeError("simulation owner did not terminate")
    return int(runtime.error is not None)


if __name__ == "__main__":
    sys.exit(main())
