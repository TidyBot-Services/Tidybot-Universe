"""Real scene compatibility evidence; this probe is never a success fixture."""
from pathlib import Path
import argparse
import json
import traceback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task", default="counter_to_sink")
    parser.add_argument("--arm", default="left")
    parser.add_argument("--stow", action="store_true")
    parser.add_argument("--mount", type=float, nargs=7)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    kitchen = None
    try:
        from openarm_robocasa.kitchen import OpenArmKitchen
        kitchen = OpenArmKitchen(args.task, execution_arm=args.arm, mount=args.mount)
        rig = kitchen.rig
        if args.stow:
            import numpy as np
            from openarm_robocasa.planning import OpenArmPlanner
            for side in ["left", "right"]:
                planner = OpenArmPlanner(kitchen, bootstrap_side=side)
                root = np.array(kitchen.mount[:3])
                target = root + [0.0, .2 if side == "left" else -.2, 1.0]
                _, q0, error, angular = planner.inverse(target)[0]
                rig.goal = q0.copy(); rig.command = q0.copy()
                rig.robot.set_qpos(q0); rig.robot.set_qvel(np.zeros(rig.robot.dof)); rig.apply_drive()
            (args.out / "bootstrap.json").write_text(json.dumps({
                "initial_qpos": rig.goal.tolist(), "joint_order": rig.names,
                "mount": kitchen.mount, "reason": "reset-only collision-free stow candidate"}, indent=2))
        before = kitchen.object_state()
        q = []; objects = []
        for _ in range(480):
            kitchen.step()
            q.append(rig.robot.qpos.copy())
            objects.append(kitchen.object_state())
        import numpy as np
        import sapien
        from PIL import Image
        from mani_skill.utils.sapien_utils import look_at
        from openarm_robocasa.planning import OpenArmPlanner
        planner = OpenArmPlanner(kitchen)
        current_check = planner.check_path(np.array(rig.robot.qpos))
        (args.out / "planner-current-check.json").write_text(json.dumps(current_check, indent=2))
        try:
            target = np.array(kitchen.object_state()["obj"]["position"]) + [0, 0, .12]
            plan = planner.plan(target)
            (args.out / "pregrasp-plan.json").write_text(json.dumps(plan, indent=2))
        except Exception:
            (args.out / "pregrasp-plan-failure.txt").write_text(traceback.format_exc())
        finally:
            planner.close()
        (args.out / "physics-result.json").write_text(json.dumps({
            "mount": kitchen.mount, "physical_steps": kitchen.steps,
            "before": before, "after": kitchen.object_state(),
            "native": kitchen.native_evaluate(),
            "tcp": {side: rig.robot.find_link_by_name("openarm_" + side + "_hand_tcp").entity_pose.to_transformation_matrix().tolist()
                    for side in ["left", "right"]},
            "qpos": rig.robot.qpos.tolist(), "contacts": rig.contacts()}, indent=2))
        scene = kitchen.scene
        from mani_skill.render.shaders import set_shader_pack, PREBUILT_SHADER_CONFIGS
        set_shader_pack(PREBUILT_SHADER_CONFIGS["default"])
        camera = scene.add_camera("openarm_kitchen", 640, 480, 1.0471975511965976, .05, 8)
        destination = getattr(kitchen.native, kitchen.spec.destination_debug_key)
        center = np.array(destination.pos) + [0, 0, .5]
        pose = look_at(center + [-1.8, -1.3, 1.0], center)
        camera.entity.pose = sapien.Pose(pose.p[0].numpy(), pose.q[0].numpy())
        scene.update_render(); camera.take_picture()
        rgb = np.array(camera.get_picture("Color"))[..., :3]
        Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255)).save(args.out / "kitchen.png")
        position = np.array(camera.get_picture("Position"))
        Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255)).save(args.out / "kitchen.png")
        np.savez_compressed(args.out / "physical.npz", qpos=np.array(q),
                            rgb=rgb, position_opengl_m=position,
                            K=camera.get_intrinsic_matrix(),
                            T_world_opengl=camera.get_model_matrix())
        result = {"robot": kitchen.selection.artifact(), "mount": kitchen.mount,
                  "joint_order": rig.names, "loaded_dof": rig.robot.dof,
                  "model_urdf": str(rig.urdf), "objects_before": before,
                  "objects_after": kitchen.object_state(), "object_trajectory": objects,
                  "tcp_poses": {side: rig.robot.find_link_by_name("openarm_" + side + "_hand_tcp").entity_pose.to_transformation_matrix().tolist()
                                for side in ["left", "right"]},
                  "native_evaluation": kitchen.native_evaluate(),
                  "fixtures": {name: {"position": np.array(fx.pos).tolist()}
                               for name, fx in kitchen.native.fixtures.items()},
                  "contacts": rig.contacts(), "physical_steps": kitchen.steps}
        (args.out / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False))
        print(json.dumps({"physical_steps": kitchen.steps, "native": kitchen.native_evaluate(),
                          "mount": kitchen.mount, "objects": kitchen.object_state()}), flush=True)
    except Exception:
        (args.out / "failure.txt").write_text(traceback.format_exc())
        raise
    finally:
        if kitchen is not None:
            kitchen.close()


if __name__ == "__main__":
    main()
